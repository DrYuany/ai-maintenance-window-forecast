"""三层解析器：自然语言 → DSL JSON。

    规则层（正则直出）      高频确定性话术，slot 抽取后直接产出 DSL，零成本零延迟
    向量层（模板检索）      本地字符 n-gram 检索"已验证 DSL 模板库"，作为 few-shot
                            喂给 LLM——降低误判、提高一致性（模板曾通过校验+求解）
    LLM 层（生成 + 校验）   新话术兜底，产物必须过确定性校验器（validator）

路由（ParseResult.route）：一句话要么进 DSL，要么不进：
  - dsl          修改类 → 产出 DSL JSON
  - batch        批量多场景（主产品 build_scenario_batch 处理，DSL 不认领）
  - data_patch   行级数据补丁（主产品 planning_intent 处理，DSL 不认领）
  - reject       越界诉求（非线性/无意义数据），明确拒绝，不静默编造
  - not_change   闲聊/查询/解释/对比 → 读工具或直接回答，不进 DSL

确定性边界：解析层可能非确定（LLM），产物必须落库进证据链；"可复现"承诺
从"NL→模型"降级为"DSL→模型"（见设计文档 §7.1）。
"""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .validator import validate_dsl

LIBRARY_PATH = Path(__file__).resolve().parents[1] / "eval" / "validated_library.jsonl"

_TASK = r"(任务\d{1,3}|T\d{1,3})"
# 年作为可选捕获组，保证 groups 索引对齐 (year, month, day)
_MONTH_DAY = r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]"


def _task_ref(value: str) -> str:
    plain = re.sub(r"^任务|^T", "", value)
    return f"任务{int(plain):03d}"


def _month_day(year: str | None, month: str, day: str) -> str:
    return f"2026-{int(month):02d}-{int(day):02d}T08:00:00+08:00"


_QUESTION_HINTS = re.compile(r"[吗么呢][?？]?$|是不是|能不能|行不行|什么时候|多久|怎么")


_METRIC = r"(高严重完成数|中严重完成数|低严重完成数|完工时间和|完工时间|总经济成本|经济成本|现金支出|完成任务数|完成数|成本)"
_METRIC_ALIASES = {
    "成本": "总经济成本", "经济成本": "总经济成本",
    "完工时间": "最后完工时间", "完成数": "完成任务数",
}
_DIR_ALIASES = {"最小化": "minimize", "降低": "minimize", "压缩": "minimize", "最大化": "maximize"}

# ── 规则层：修改类正则 + slot 抽取 ────────────────────────────────────────────

RULE_PATTERNS: list[tuple[str, str]] = [
    # 自定义分层目标："先最小化完工时间和，再最小化成本"（lexmin，不是批量）
    (rf"(?:先|首先|优先).*?(最小化|最大化|降低|压缩).*?{_METRIC}.*?(?:再|然后|接着).*?(最小化|最大化|降低|压缩).*?{_METRIC}", "lexmin"),
    # 互斥：不能/别/不要 同一天、同时
    (rf"{_TASK}[和与、,，]{_TASK}.*?(?:不能|不要|别).*?(?:同[一1]天|同时|一起)", "co_occur_diff_day"),
    # 先后：先修 A 再修 B；A 修完才能动 B；A 在 B 之前
    (rf"先修?{_TASK}.*?再修?{_TASK}", "precedence"),
    (rf"{_TASK}.*?(?:修完|完成).{{0,4}}(?:才能|再|之后).{{0,4}}(?:动|修|开工).{{0,4}}{_TASK}", "precedence"),
    (rf"{_TASK}.*?必须.*?在{_TASK}.*?之前", "precedence_after"),
    # 最早开工（有日期的才算修改；"最早什么时候"是问句，被问句守卫拦截）
    (rf"{_TASK}.*?最早.{{0,8}}?{_MONTH_DAY}", "earliest"),
    # 必修
    (rf"{_TASK}.*?(?:必须|一定|务必).*?(?:修|安排|完成|做)", "must"),
    # 排除
    (rf"(?:别排|不要排|排除|不排){_TASK}", "exclude"),
    # 固定作业线：只能上 N 号船/线；用 N 号线
    (rf"{_TASK}.*?只能.{{0,6}}(\d+)号(?:船|线)", "fix"),
    (rf"{_TASK}.*?(?:用|上)(\d+)号(?:船|线)", "fix"),
    # 期限：最晚 X 月 Y 日前修完；X 月 Y 日前修完
    (rf"{_TASK}.*?(?:最晚|最迟).*?{_MONTH_DAY}.*?(?:前|之前).*?(?:修完|完成|修好)", "deadline"),
    (rf"{_TASK}.*?{_MONTH_DAY}.*?(?:前|之前).*?(?:修完|完成|修好|搞定)", "deadline"),
    # 禁开工日期
    (rf"{_MONTH_DAY}.*?(?:别开工|不要开工|禁止开工)", "forbidden_dates"),
    # 预算（时间量）：完工时间和/完成数 + 不超过/最多/压到 + 数值
    (rf"(完工时间和|完成数|完成任务数).*?(?:不超过|最多|压到|别超过)\s*(\d+)", "budget"),
    # 计数下界：至少完成 N 项
    (rf"(?:至少|最少|不低于).{0,4}(?:完成|修|排).{0,6}(\d+)\s*项", "cardinality_min"),
]


def parse_by_rules(message: str) -> dict[str, Any] | None:
    """规则层：正则命中且不是问句则直出 DSL；未命中返回 None。"""
    text = message.strip()
    if _QUESTION_HINTS.search(text):
        return None
    for pattern, kind in RULE_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = list(match.groups())
        if kind == "lexmin":
            dir1, metric1, dir2, metric2 = groups
            stages = [
                {"q": _METRIC_ALIASES.get(metric1, metric1), "dir": _DIR_ALIASES[dir1]},
                {"q": _METRIC_ALIASES.get(metric2, metric2), "dir": _DIR_ALIASES[dir2]},
            ]
            return {"objectives": [{"type": "lexmin", "stages": stages}]}
        if kind == "co_occur_diff_day":
            return {"constraints": [{
                "type": "co_occur", "mode": "diff_day",
                "tasks": [_task_ref(groups[0]), _task_ref(groups[1])],
            }]}
        if kind == "precedence":
            return {"constraints": [{
                "type": "precedence",
                "before": _task_ref(groups[0]), "after": _task_ref(groups[1]),
            }]}
        if kind == "precedence_after":
            return {"constraints": [{
                "type": "precedence",
                "before": _task_ref(groups[1]), "after": _task_ref(groups[0]),
            }]}
        if kind == "earliest":
            task, year, month, day = groups[0], groups[1], groups[2], groups[3]
            return {"constraints": [{
                "type": "earliest", "task": _task_ref(task),
                "time": _month_day(year, month, day),
            }]}
        if kind == "must":
            return {"constraints": [{"type": "must", "task": _task_ref(groups[0])}]}
        if kind == "exclude":
            return {"constraints": [{"type": "exclude", "task": _task_ref(groups[0])}]}
        if kind == "fix":
            return {"constraints": [{
                "type": "fix", "task": _task_ref(groups[0]), "line": int(groups[1]),
            }]}
        if kind == "deadline":
            task, year, month, day = groups[0], groups[1], groups[2], groups[3]
            return {"constraints": [{
                "type": "deadline", "task": _task_ref(task),
                "time": _month_day(year, month, day),
            }]}
        if kind == "forbidden_dates":
            year, month, day = groups[0], groups[1], groups[2]
            return {"constraints": [{
                "type": "forbidden_dates",
                "dates": [_month_day(year, month, day)],
            }]}
        if kind == "budget":
            metric, value = groups
            metric = _METRIC_ALIASES.get(metric, metric)
            return {"constraints": [{
                "type": "budget", "quantity": metric, "op": "<=", "value": int(value),
            }]}
        if kind == "cardinality_min":
            return {"constraints": [{
                "type": "cardinality", "filter": {}, "op": ">=", "value": int(groups[0]),
            }]}
    return None


# ── 意图门控：批量 / 数据补丁 / 参数修改 / 越界 / 非修改 ─────────────────────

_BATCH_RE = re.compile(
    r"(?:先|首先).{0,24}(?:再|然后|接着).{0,24}(?:算|排|求解|对比|比较|看看)|两个.{0,6}场景|两种.{0,6}情况|敏感性"
)

_DATA_PATCH_RE = re.compile(
    r"到货|维修工时.{0,4}(?:上涨|翻倍|增加|提高)|(?:更费时|耗时变长|修得更久)"
    r"|(?:严重故障|严重告警|高严重|高危|中等故障|中等告警|低严重).{0,8}"
    r"(?:\d{1,2}月)?\d{1,2}[日号].{0,6}(?:前|之前).{0,4}(?:修完|完成|修好|搞定)"
    r"|补库延期"
)

# 参数类修改（主产品 planning_intent 处理，DSL 不认领但门控必须认）
_PARAMETER_RE = re.compile(
    r"船租.{0,6}(?:改成|改为|设为|上涨|上调|翻倍|翻一番|翻一倍|涨|增加)"
    r"|人工.{0,6}(?:改成|改为|设为|上涨|上调|涨|增加)"
    r"|(?:最多|不超过|限定|只上|只剩)\s*[\d一两二三]+\s*(?:条|人|小时)"
    r"|(?:少|减)\s*[\d一两二三]+\s*(?:条|人)"
    r"|每天.{0,6}(?:最多|只能|干|工作)\s*\d+.*小时|每天\s*\d+(?:\.\d+)?.*小时"
    r"|人员.{0,6}(?:上限|改成|设为|砍)"
    r"|人手.{0,6}(?:砍|减|少)"
    r"|(?:转移|挪动|换风机).{0,6}(?:改成|要|用)\s*\d+"
    r"|(?:早上|每天)\s*\d+点(?:开工|开始|干活)"
    r"|每天\s*\d+点(?:收工|结束)"
)

# 策略/加权目标（主产品四模板处理，DSL 不认领但门控必须认）
_STRATEGY_RE = re.compile(
    r"(?:按|改成|改为|用|采用).{0,4}(?:高风险优先|风险优先|严重性优先|成本最低|经济优先|成本优先"
    r"|最短完成|工期优先|检修最多|数量优先|省钱)"
    r"|先修严重的|严重的先|早点干完|尽快完成|改成加权|权重|加权模式"
    r"|省钱优先|最短完成优先|检修最多器件优先|数量优先"
)

_ILLEGAL_RE = re.compile(
    r"平方|相乘|除以|绝对值|非线性|方差|标准差"
    r"|碳排放|碳足迹|颜色|外观|声音|噪音|振动|温度"
    r"|天气.{0,4}(?:改|变好|变差|调)"
    r"|最大化.{0,6}距离|最小化.{0,6}距离"
    r"|删掉|删除|报废|卖掉"
)

# 需要澄清的诉求（模糊/涉及未实现能力）：预算引成本量（未实现）、安全应急
_CLARIFY_RE = re.compile(
    r"预算.{0,6}(?:不超过|控制在|别超过|压到)\s*\d"
    r"|台风|保安全|应急模式"
)

NON_CHANGE_PATTERNS: list[tuple[str, str]] = [
    (r"^(你好|您好|嗨|哈喽|hello|hi|在吗|在不在|谢谢|多谢|再见|拜拜)[!！。.～~ ]*$", "chitchat"),
    (r"你(?:叫什么|是谁|是啥|会什么|能干什么|能做什么)", "chitchat"),
    (r"(?:写首诗|讲个笑话|聊聊天|中午吃什么|今天天气|点外卖)", "chitchat"),
    (r"为什么|为啥|为何|怎么理解|如何理解|是什么|有哪些|能(?:不能|否)介绍|介绍一下|讲讲|解释一下|什么原理|什么算法|怎么建", "explain"),
    (r"对比|比较|差在哪|差多少|区别|哪个更|哪版更", "compare"),
    (r"多少|哪些|怎么样|如何|有没有|够不够|什么时候|哪天|还剩|当前|现在|最近", "query"),
]


def _classify_non_change(message: str) -> str | None:
    """非 DSL 修改类话术的路由分类；不匹配返回 None（交 LLM 层）。"""
    text = message.strip()
    if _BATCH_RE.search(text):
        return "batch"
    if _DATA_PATCH_RE.search(text):
        return "data_patch"
    if _STRATEGY_RE.search(text):
        return "strategy"
    if _PARAMETER_RE.search(text):
        return "parameter"
    if _ILLEGAL_RE.search(text):
        return "reject"
    if _CLARIFY_RE.search(text):
        return "clarify"
    for pattern, _kind in NON_CHANGE_PATTERNS:
        if re.search(pattern, text):
            return "not_change"
    return None


# ── 向量层：本地字符 n-gram 检索（无外部依赖）────────────────────────────────

def _embed(text: str) -> dict[str, float]:
    """字符 2/3-gram 加权向量（L2 归一），纯 Python，无外部依赖。"""
    vec: dict[str, int] = {}
    for n in (2, 3):
        for i in range(len(text) - n + 1):
            gram = text[i:i + n]
            vec[gram] = vec.get(gram, 0) + 1
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if not norm:
        return {}
    return {gram: count / norm for gram, count in vec.items()}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    return sum(w * b.get(gram, 0.0) for gram, w in a.items())


def load_library(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or LIBRARY_PATH
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def retrieve(message: str, library: list[dict], top_k: int = 3) -> list[dict[str, Any]]:
    """检索 top_k 条与当前话术最相似的已验证模板（附相似度）。"""
    query = _embed(message)
    scored = [
        {**entry, "similarity": _cosine(query, _embed(entry["utterance"]))}
        for entry in library
    ]
    scored.sort(key=lambda item: item["similarity"], reverse=True)
    return scored[:max(1, top_k)]


# ── LLM 层：few-shot 生成 + 确定性校验 ────────────────────────────────────────

_PARSE_SYSTEM = """你是检修排程 DSL 生成器。把用户的排程约束诉求翻译成 DSL JSON，
只允许使用以下约束原语与字段（不要编造原语、字段或指标名）：

原语（type 取值）与字段：
- must/exclude: {"task": "任务018"}
- precedence: {"before": "任务018", "after": "任务022", "gap": 分钟数(可选)}
- co_occur: {"mode": "diff_day"|"same_day", "tasks": ["任务018","任务022"]}
- fix: {"task": "任务018", "line": 1}
- cardinality: {"filter": {"severity": "高"}, "op": ">="|"<="|"==", "value": 3}
- deadline: {"task": "任务018", "time": "2026-11-08T08:00:00+08:00"}
- earliest: {"task": "任务018", "time": "2026-11-05T08:00:00+08:00"}
- forbidden_dates: {"dates": ["2026-11-03T08:00:00+08:00"]}
- budget: {"quantity": "完工时间和", "op": "<=", "value": 数值}
- linear: {"terms": [[系数, 量]], "op": "<=", "value": 数值}

任务编号写全称"任务018"（三位）。日期必须带时区。只输出 JSON，不要解释。

如果这句话不是排程修改诉求（闲聊、查询、解释、对比），输出 {"route": "not_change"}；
如果是非线性、无意义或越界诉求（如"最小化成本的平方""改颜色"），输出 {"route": "reject"}。"""


@dataclass
class ParseResult:
    dsl: dict[str, Any] | None
    source: str  # rule / llm
    route: str = "dsl"  # dsl / batch / data_patch / reject / not_change
    confidence: float = 1.0
    templates_used: list[str] = field(default_factory=list)


class ParseUnavailable(RuntimeError):
    """LLM 未配置（无 API key）时抛出，调用方应回退到澄清提问。"""


def _llm_config() -> tuple[str, str, str] | None:
    api_key = os.environ.get("MINIMAX_API_KEY")
    if not api_key:
        return None
    return (
        api_key,
        os.environ.get("MINIMAX_MODEL", "deepseek-flash"),
        os.environ.get("MINIMAX_BASE_URL", "https://api.deepseek.com/anthropic"),
    )


def parse_with_llm(
    message: str,
    templates: list[dict[str, Any]],
    client=None,
) -> ParseResult:
    """LLM 层：带 few-shot 的生成 + 校验。client 可注入（测试 mock）。"""
    config = _llm_config()
    if client is None and config is None:
        raise ParseUnavailable("未配置 MINIMAX_API_KEY，LLM 解析不可用")
    if client is None:
        from anthropic import Anthropic

        api_key, model, base_url = config
        # 本机 Claude Code 代理环境里有 ANTHROPIC_AUTH_TOKEN=PROXY_MANAGED：
        # SDK 在 auth_token=None 时读环境变量并优先于 api_key 发 Bearer 头，
        # 导致真实 key 被劫持（401 ****AGED）。构造时传空串跳过环境读取、
        # 构造后置回 None 让 SDK 走 x-api-key 头。
        client = Anthropic(api_key=api_key, auth_token="", base_url=base_url, timeout=60.0)
        client.auth_token = None
        model_name = model
    else:
        model_name = "mock-model"

    examples = "\n".join(
        f"用户：{entry['utterance']}\nDSL：{json.dumps(entry['dsl'], ensure_ascii=False)}"
        for entry in templates
    )
    user = (
        f"以下是已通过校验和求解验证的相似例子：\n\n{examples}\n\n"
        f"现在把这句话翻译成 DSL JSON：{message}"
    )
    response = client.messages.create(
        model=model_name,
        max_tokens=1000,
        temperature=0.0,
        system=_PARSE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    content = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()
    # 容忍 markdown 代码围栏
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    payload = json.loads(content)
    if payload.get("route") in ("not_change", "reject"):
        return ParseResult(dsl=None, source="llm", route=payload["route"], confidence=1.0)
    errors = validate_dsl(payload)
    if errors:
        # 产物过不了校验 → 不静默也不抛异常，路由为"澄清"（低置信），
        # 由上层决定重试、换说法或转人工。
        return ParseResult(dsl=None, source="llm", route="clarify", confidence=0.5)
    return ParseResult(
        dsl=payload,
        source="llm",
        route="dsl",
        confidence=1.0,
        templates_used=[entry.get("id", entry.get("utterance", "")) for entry in templates],
    )


def parse(message: str, library: list[dict] | None = None, client=None) -> ParseResult:
    """三层解析入口：规则 → 意图门控 → 向量(few-shot) → LLM。

    路由结果：dsl / batch / data_patch / reject / not_change。
    """
    ruled = parse_by_rules(message)
    if ruled is not None:
        return ParseResult(dsl=ruled, source="rule", route="dsl", confidence=1.0)
    classified = _classify_non_change(message)
    if classified is not None:
        return ParseResult(dsl=None, source="rule", route=classified, confidence=0.9)
    library = library if library is not None else load_library()
    templates = retrieve(message, library, top_k=3)
    return parse_with_llm(message, templates, client=client)
