"""注册表 → LLM 槽位说明书（schema prompt）。

设计要点（见 docs/superpowers/specs/2026-10-29-registry-driven-intent-design.md）：

  系统提示词（静态）回答四件事：
    1. 有什么        —— 实体 + 6 张数据表（entity_block / data_block）
    2. 什么能改      —— 51 个可改参数（L0 目录）+ 16 原语 + 5 目标
    3. 改成什么格式  —— DSL 字段 / 单位 / 取值约束
  上下文（动态，每轮拼）回答：
    4. 现在是什么状态 —— 快照清单（snapshot_block）+ 当前方案（context_block）

分层注入（控制 prompt 体积）：
  L0 目录     所有可改参数一行一个（名称+单位+取值约束），约 2k 字符，永远在
  L1 详述     按本轮话术检索出的 top-k 槽位详解，约 1k 字符，每轮变
  L2 原语/数据 固定块，约 1.5k 字符，永远在

改注册表（parameters.py / primitives.py / data_fields.py / entities.py），
本模块输出自动跟着变——不需要再手写提示词。
"""
from __future__ import annotations

import math
from typing import Any

from . import data_fields as data_reg
from . import entities as entity_reg
from . import primitives as prim_reg
from . import parameters as param_reg

# 高频槽位：用户最常改；检索未命中时也保底带上详解，避免"明明很常见却没细节"。
CORE_PARAMS: tuple[str, ...] = (
    "每日有效维修上限",
    "每天开始维修",
    "每天结束维修",
    "最大作业线",
    "可调动人员总数",
    "船舶日租金",
    "维修人工单价",
    "电价整体倍率",
    "检修期间停机比例",
    "候选开工粒度",
)

# 易错的列表/边界槽位给一条写法示例（只影响 L1 详解，不额外膨胀目录）。
_PARAM_EXAMPLES: dict[str, str] = {
    "候选风机": '只看风机001和风机002 → {"field":"候选风机","op":"=","value":["风机001","风机002"]}',
    "候选部件": '这次只排部件01和部件02 → {"field":"候选部件","op":"=","value":["部件01","部件02"]}',
    "强制维修任务": '任务001和任务003必须修 → {"field":"强制维修任务","op":"=","value":["任务001","任务003"]}',
    "禁止新开工风机": '风机005这几天不许开工 → {"field":"禁止新开工风机","op":"=","value":["风机005"]}',
    "费用预算上限": '总预算控制在300万 → {"field":"费用预算上限","op":"=","value":3000000}',
    "安全风速上限": '风速上限放宽到14米每秒 → {"field":"安全风速上限","op":"=","value":14}',
    "补库延期量": '备件到货统一延后24小时 → {"field":"补库延期量","op":"=","value":24}',
}


# 参数名 → 口语线索（只用于检索打分，不参与生成；注册表新增参数无需改这里，
# 名称本身和 CSV 的取值约束/建模影响也会参与匹配）。
_PARAM_HINTS: dict[str, tuple[str, ...]] = {
    "每日有效维修上限": ("每天", "工时", "小时", "干多久", "加班", "维修时长", "工作小时"),
    "每天开始维修": ("几点开工", "开始维修", "上班时间", "开工时间"),
    "每天结束维修": ("几点收工", "结束维修", "下班", "收工时间"),
    "最大作业线": ("作业线", "几条船", "几条线", "船舶数量", "上几条"),
    "可调动人员总数": ("人员", "人手", "工人", "人力", "几个人", "人数"),
    "风机间转移": ("转移", "换风机", "转场", "路上时间"),
    "允许夜间转移": ("夜间", "晚上转移", "夜航"),
    "允许跨天暂停": ("跨天", "分两天", "隔天"),
    "常态风速上限": ("风速", "常态风速", "风大"),
    "常态浪高上限": ("浪高", "波高", "海浪"),
    "安全风速上限": ("安全风速", "风太大", "风速阈值"),
    "安全浪高上限": ("安全浪高", "浪太大"),
    "候选开工粒度": ("粒度", "几点一档", "分钟粒度"),
    "船舶日租金": ("船租", "租金", "租船", "船费"),
    "维修人工单价": ("人工", "工时费", "工资", "人工费"),
    "每任务耗材附加费": ("耗材", "材料费", "附加费"),
    "故障等待损失比例": ("等待损失", "故障损失"),
    "检修期间停机比例": ("停机", "停机比例", "停发"),
    "潜在功率整体倍率": ("功率", "发电能力", "出力"),
    "电价整体倍率": ("电价", "电费", "卖电"),
    "费用预算上限": ("预算", "上限", "花钱", "成本上限", "别超过"),
    "预算限制启用": ("预算限制", "开启预算"),
    "补库延期量": ("补库", "到货延期", "备件延期"),
    "总求解预算": ("求解时间", "限时", "算多久"),
    "标准目标策略": ("策略", "目标", "优先", "轻重缓急", "风险", "按什么排", "排序"),
    "成本敏感性倍率列表": ("敏感性", "涨跌", "敏感性分析"),
    "候选风机": ("哪些风机", "只看", "指定风机"),
    "候选部件": ("哪些部件", "只看部件"),
    "强制维修任务": ("必须修", "一定修", "必修"),
    "禁止新开工风机": ("不许开工", "禁止开工风机"),
    "指定任务截止时间": ("截止", "最晚", "期限"),
    "固定使用的批准窗口": ("固定窗口", "只用窗口"),
    "禁止新增开工日期": ("这几天别开工", "禁开工日"),
}


def _cn_len(text: str) -> int:
    return len(text)


def _compact_value(value: str, limit: int = 24) -> str:
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def param_names() -> list[str]:
    """注册表里全部可改参数名（顺序稳定）。"""
    return [row["参数名称"] for row in param_reg._rows() if param_reg.is_changeable(row["参数名称"])]


def param_catalog() -> str:
    """L0：每个可改参数一行（名称 + 单位 + 取值约束）。

    有些名字虽然在注册表里，却不是"系统参数"，写进 parameters 会被拒。
    这里当场标注该往哪写，比让模型去猜再报错便宜得多。
    """
    lines = ["可改参数目录（名称｜单位｜取值约束；需要细节时会自动展开）："]
    for row in param_reg._rows():
        name = row["参数名称"]
        if not param_reg.is_changeable(name):
            continue
        unit = row.get("单位", "") or "-"
        rule = _compact_value(row.get("取值约束", ""), 28) or "-"
        note = _NOT_A_PARAM.get(name)
        lines.append(f"- {name}｜{unit}｜{rule}" + (f"（{note}）" if note else ""))
    return "\n".join(lines)


# 写在 parameters 里会被拒的名字 → 正确写法。
# 只登记"确实有别的出口"的那几个；其余不支持的不要在这里编造出口。
_NOT_A_PARAM = {
    "标准目标策略": "不是 parameters 项，写进 objectives：{type:strategy,name:四选一}",
    "共同最低完成任务数": "不是 parameters 项，写进 constraints：cardinality",
    "费用预算上限": "不是 parameters 项，写进 constraints：budget/总经济成本",
    "强制维修任务": "不是 parameters 项，写进 constraints：must",
}


def param_detail(name: str) -> str:
    row = param_reg.lookup(name)
    if row is None:
        return ""
    parts = [f"### {name}"]
    if row.get("单位"):
        parts.append(f"单位：{row['单位']}")
    if row.get("取值约束"):
        parts.append(f"取值约束：{row['取值约束']}")
    if row.get("默认值"):
        parts.append(f"默认值：{_compact_value(row['默认值'], 40)}")
    if row.get("建模影响"):
        parts.append(f"影响：{row['建模影响']}")
    example = _PARAM_EXAMPLES.get(name)
    if example:
        parts.append(f"示例：{example}")
    return "｜".join(parts)


def _embed(text: str) -> dict[str, float]:
    vec: dict[str, int] = {}
    for n in (2, 3):
        for i in range(len(text) - n + 1):
            gram = text[i:i + n]
            vec[gram] = vec.get(gram, 0) + 1
    norm = math.sqrt(sum(v * v for v in vec.values()))
    return {g: c / norm for g, c in vec.items()} if norm else {}


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    return sum(w * b.get(g, 0.0) for g, w in a.items())


def _param_text(name: str) -> str:
    row = param_reg.lookup(name) or {}
    hints = " ".join(_PARAM_HINTS.get(name, ()))
    return " ".join([name, row.get("参数类别", ""), row.get("单位", ""),
                     row.get("取值约束", ""), hints])


def retrieve_params(message: str, top_k: int = 6) -> list[str]:
    """按本轮话术检索最相关的可改参数（含口语线索）。"""
    query = _embed(message)
    if not query:
        return []
    scored = [(_cosine(query, _embed(_param_text(name))), name) for name in param_names()]
    scored.sort(reverse=True)
    return [name for score, name in scored[:top_k] if score > 0]


def primitive_block() -> str:
    """L2：约束/目标原语（字段来自 primitives.py）。"""
    lines = ["## 约束原语（constraints）"]
    for name, spec in prim_reg.CONSTRAINT_PRIMITIVES.items():
        required = "、".join(spec.get("required", []))
        optional = spec.get("optional") or []
        suffix = f"（可选 {('、'.join(optional))}）" if optional else ""
        mark = "" if name in prim_reg.COMPILED_CONSTRAINTS else "【未实现，勿用】"
        lines.append(f"- {name}：必填 {required}{suffix} {mark}".rstrip())
    modes = "/".join(prim_reg.CO_OCCUR_MODES)
    lines.append(f"co_occur 的 mode 取值：{modes}；当前实现 {sorted(prim_reg.COMPILED_CO_OCCUR_MODES)}")
    lines.append("## 目标原语（objectives）")
    for name, spec in prim_reg.OBJECTIVE_PRIMITIVES.items():
        required = "、".join(spec.get("required", []))
        lines.append(f"- {name}：必填 {required}")
    return "\n".join(lines)


def data_block() -> str:
    """L2：6 张数据表可改字段 + data 段写法示例。"""
    lines = ["## 输入数据字段（data 段）"]
    for table, fields in data_reg.DATA_FIELDS.items():
        changeable = [
            f"{name}（{spec.get('unit') or spec.get('type')}）"
            for name, spec in fields.items()
            if spec.get("changeable")
        ]
        if changeable:
            lines.append(f"- {table}：{'、'.join(changeable)}")
    lines.extend([
        "data 段写法示例（kind 是固定枚举，不要自创）：",
        '{"kind":"part_arrival","part":"叶片","at":"2026-11-05T08:00:00+08:00","quantity":2,"blockBefore":true}',
        '{"kind":"part_hours","part":"齿轮箱","factor":1.2}',
        '{"kind":"task_deadline","severity":"高","at":"2026-11-08T08:00:00+08:00"}',
        '{"kind":"weather","day":"2026-11-03","wind":15.0}',
        '{"kind":"weather","day":"2026-11-04","state":"不可作业"}',
        '{"kind":"hourly","time":"2026-11-03T12:00:00+08:00","price":400}',
        '{"kind":"window","window":"窗口02","start":"2026-11-01T00:00:00+08:00"}',
    ])
    return "\n".join(lines)


def entity_block() -> str:
    lines = ["## 可引用实体"]
    for name, desc in entity_reg.ENTITIES.items():
        lines.append(f"- {name}：{desc}")
    return "\n".join(lines)


def snapshot_block(snapshot: Any) -> str:
    """极简快照：只给"存在什么"，不给具体数值。"""
    if snapshot is None:
        return "## 当前数据\n（未提供快照；引用任务/风机时请让用户确认编号）"
    tasks = [t["任务编号"] for t in getattr(snapshot, "tasks", [])]
    turbines = sorted({t.get("风机编号") for t in getattr(snapshot, "tasks", []) if t.get("风机编号")})
    parts = sorted({p.get("部件名称") for p in getattr(snapshot, "parts", {}).values() if p.get("部件名称")})
    windows = [w["编号"] for w in getattr(snapshot, "observation_windows", [])]

    def span(items: list[str]) -> str:
        if not items:
            return "无"
        return f"{items[0]}~{items[-1]}（共{len(items)}个）" if len(items) > 1 else items[0]

    return "\n".join([
        "## 当前数据（只给清单，不给数值）",
        f"- 任务：{span(tasks)}",
        f"- 风机：{span(turbines)}",
        f"- 部件：{'/'.join(parts) or '无'}",
        f"- 海事窗口：{span(windows)}",
    ])


def context_block(context: dict[str, Any] | None) -> str:
    """当前方案上下文：上一版是什么、待确认变更、最近对话。"""
    if not context:
        return ""
    lines = ["## 当前上下文"]
    if context.get("plan_label"):
        lines.append(f"- 当前选中方案：{context['plan_label']}")
    schedule = context.get("schedule") or []
    if schedule:
        sample = "；".join(
            f"{item.get('task')} {item.get('start')}" for item in schedule[:8]
        )
        lines.append(f"- 各任务开工日（前 8 条）：{sample}")
    pending = context.get("pending")
    if pending:
        lines.append(f"- 未确认变更（新话术应在其基础上累加）：{pending}")
    history = context.get("history") or []
    if history:
        lines.append("- 最近对话：")
        for turn in history[-3:]:
            lines.append(f"  用户：{turn.get('user', '')} ／ 助手：{turn.get('assistant', '')}")
    return "\n".join(lines)


def _parse_contract() -> str:
    return """## 输出格式（必须是统一 JSON 包装）
{"route": "dsl" | "not_change" | "reject" | "clarify", "confidence": 0~1, "dsl": {...}}

- route=dsl：可编译的排程修改，dsl 里放 constraints / objectives / parameters / data 中任意几段。
- route=not_change：闲聊、查询、解释（"怎么算""是什么""为什么"）。
- route=reject：明确越界（非线性目标、改颜色外观、风险/可靠性/满意度等未建模的业务概念）。
- route=clarify：吃不准、缺少基准（如"顺延两天"没说从哪天算）、或引用的实体不存在。
- 时间限定的数据修改（如"未来三天电价上涨20%"）不能用全局倍率参数顶替：
  改 data 段的 hourly/weather 等时序字段，做不到就 clarify。
- confidence：0.9~1.0 很确定；0.5~0.7 吃不准；<0.6 会被系统降级为澄清。

硬性规范：
- 任务编号写全称"任务018"；风机"风机003"；窗口"窗口02"。
- 时间一律 ISO 带时区，形如 2026-11-05T08:00:00+08:00；只说到"某日"默认 08:00。
- 金额一律元（"500万"→5000000）。
- parameters 的 field 只能用目录里没标注"不是 parameters 项"的名字。
- parameters 每项必须写 op："="（绝对值）或 "*"（相对倍率，如"涨两成"→1.2）。
- 取值形态：开关类写 "是"/"否"；时刻类写 "07:00"；数值类写数字。
- 下面四类不写 parameters，各有专门位置：
  策略 → objectives：{"type":"strategy","name":"成本最低优先"}
  成本上限 → constraints：{"type":"budget","quantity":"总经济成本","op":"<=","value":5000000}
  必修任务 → constraints：{"type":"must","task":"任务018"}
  最低完成数 → constraints：{"type":"cardinality","op":">=","value":5}
- 标准目标策略四选一：高风险优先抢修 / 成本最低优先 / 最短完成优先 / 检修最多器件优先。
  用户说"按风险来排""重视安全"→高风险优先抢修；"省钱""成本优先"→成本最低优先。
- 一句话里有多条诉求时，全部输出（不要只取第一条）。"""


def build_system_prompt(message: str | None = None, top_k: int = 6) -> str:
    """组装完整系统提示词：L0 目录 + L1 检索详解 + 原语/数据/实体 + 输出契约。"""
    detail_names: list[str] = []
    if message:
        for name in retrieve_params(message, top_k=top_k) + list(CORE_PARAMS):
            if name not in detail_names:
                detail_names.append(name)

    blocks = [
        "你是检修排程 DSL 生成器。把用户的话翻译成 DSL，不要解释。",
        "## 你能改什么",
        param_catalog(),
    ]
    if detail_names:
        blocks.append("## 本轮相关槽位详解")
        blocks.extend(param_detail(name) for name in detail_names if name)
    blocks.extend([
        primitive_block(),
        data_block(),
        entity_block(),
        _parse_contract(),
    ])
    return "\n\n".join(blocks)
