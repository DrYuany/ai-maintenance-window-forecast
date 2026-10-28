"""三层解析器：自然语言 → DSL JSON（第二阶段）。

    规则层（正则直出）    高频确定性话术，slot 抽取后直接产出 DSL，零成本零延迟
    向量层（模板检索）    本地字符 n-gram 检索"已验证 DSL 模板库"，作为 few-shot
                          喂给 LLM——降低误判、提高一致性（模板曾通过校验+求解）
    LLM 层（生成+校验）   新话术兜底，产物必须过确定性校验器（validator）

顺序上规则最优先，LLM 覆盖面最广——不是反的。向量层不做"分类"（我们是开集
结构化生成，不是闭集意图识别），而是做"检索"。

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


# ── 规则层：正则 + slot 抽取 ──────────────────────────────────────────────────

RULE_PATTERNS: list[tuple[str, Any]] = [
    # 不能同一天 / 不能同时 → 互斥
    (rf"{_TASK}[和与、,，]{_TASK}.*?不能.*?(同[一1]天|同时)", "co_occur_diff_day"),
    # 先修 A 再修 B → 先后
    (rf"先修?{_TASK}.*?再修?{_TASK}", "precedence"),
    # A 必须在 B 之前 → 先后（反向写法）
    (rf"{_TASK}.*?必须.*?在{_TASK}.*?之前", "precedence_after"),
    # 最早 X 月 Y 日开工 → 最早开工
    (rf"{_TASK}.*?最早.*?{_MONTH_DAY}.*?(?:开工|开始)", "earliest"),
    # 必须修 → 必修
    (rf"{_TASK}.*?(?:必须|一定|务必).*?(?:修|安排|完成|做)", "must"),
    # 别排 / 排除 → 排除
    (rf"(?:别排|不要排|排除|不排){_TASK}", "exclude"),
    # 只能上 N 号船/线 → 固定作业线
    (rf"{_TASK}.*?只能.*?(\d+)号(?:船|线)", "fix"),
    # 最晚 X 月 Y 日前修完 → 期限
    (rf"{_TASK}.*?(?:最晚|最迟).*?{_MONTH_DAY}.*?(?:前|之前).*?(?:修完|完成|修好)", "deadline"),
    # X 月 Y 日别开工 → 禁开工日期
    (rf"{_MONTH_DAY}.*?(?:别开工|不要开工|禁止开工)", "forbidden_dates"),
]


def _month_day(year: str | None, month: str, day: str) -> str:
    return f"2026-{int(month):02d}-{int(day):02d}T08:00:00+08:00"


def parse_by_rules(message: str) -> dict[str, Any] | None:
    """规则层：正则命中则直出 DSL，未命中返回 None。"""
    text = message.strip()
    for pattern, kind in RULE_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = list(match.groups())
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

任务编号写全称"任务018"（三位）。日期必须带时区。只输出 JSON，不要解释。"""


@dataclass
class ParseResult:
    dsl: dict[str, Any]
    source: str  # rule / llm
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
        client = Anthropic(api_key=api_key, base_url=base_url, timeout=60.0)
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
    dsl = json.loads(content)
    errors = validate_dsl(dsl)
    if errors:
        raise ValueError("LLM 产物未通过校验：" + "；".join(errors))
    return ParseResult(
        dsl=dsl,
        source="llm",
        confidence=1.0,
        templates_used=[entry.get("id", entry.get("utterance", "")) for entry in templates],
    )


def parse(message: str, library: list[dict] | None = None, client=None) -> ParseResult:
    """三层解析入口：规则 → 向量(few-shot) → LLM。"""
    ruled = parse_by_rules(message)
    if ruled is not None:
        return ParseResult(dsl=ruled, source="rule", confidence=1.0)
    library = library if library is not None else load_library()
    templates = retrieve(message, library, top_k=3)
    return parse_with_llm(message, templates, client=client)
