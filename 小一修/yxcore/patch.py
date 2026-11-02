"""补丁：小一修里大模型产出的全部内容。

一个补丁 = 旋钮位 + 拧在哪一格 + 拧到几。运算符只有 5 个，没有表达式、没有嵌套、
没有原语组合——这是相对大一修的降级点，也是校验器只用查表就能闭合的原因。

    {"slot": "bucket_min",
     "index": {"桶": {"kind": "窗口", "value": "窗口03"},
               "组": {"kind": "部件", "value": "叶片"}},
     "op": "=", "value": 1, "strength": "hard", "utterance": "11月第二周必须修叶片"}

两个纪律：

* **同格不重复**（决策 D2）：同一槽位 + 同一索引只允许一个补丁，第二个直接报错让模型
  自己合并。否则「船租涨两成」来两次是 1.44 倍还是 1.2 倍说不清，指纹和复现性都会坏。
* **相对运算按基线换算**：`*` / `+` / `-` 一律折算成绝对值再落库，相对量不进模型。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

OPS = ("=", "*", "+", "-", "reset")
STRENGTHS = ("hard", "soft", "unsure")


@dataclass(frozen=True)
class Patch:
    slot: str
    index: dict[str, Any] = field(default_factory=dict)
    op: str = "="
    value: Any = None
    strength: str = "hard"        # hard|soft|unsure，不给数字（见 README 第六节）
    utterance: str = ""           # 用户原话，回显与对账用

    @property
    def cell(self) -> str:
        """格子标识：槽位 + 规范化的索引，用于「同格不重复」判定。"""
        return self.slot + "@" + json.dumps(self.index, sort_keys=True, ensure_ascii=False)

    def to_json(self) -> dict[str, Any]:
        return {
            "槽位": self.slot,
            "索引": self.index,
            "运算": self.op,
            "值": self.value,
            "强度": self.strength,
            "原话": self.utterance,
        }


def parse_patch(raw: dict[str, Any]) -> tuple[Patch | None, str | None]:
    """从大模型给的字典解析成补丁。形状不对就报出来，不猜。"""
    if not isinstance(raw, dict):
        return None, f"补丁必须是对象，收到 {type(raw).__name__}"
    slot = raw.get("slot") or raw.get("槽位")
    if not slot or not isinstance(slot, str):
        return None, "补丁缺槽位名"
    op = raw.get("op") or raw.get("运算") or "="
    if op not in OPS:
        return None, f"运算 {op!r} 不支持，只能是 {'、'.join(OPS)}"
    strength = raw.get("strength") or raw.get("强度") or "hard"
    if strength not in STRENGTHS:
        # 大模型给置信度数字是常见跑偏；明确挡回去，不静默当成 hard。
        return None, (f"强度只能是 hard/soft/unsure 三选一，收到 {strength!r}；"
                      "不要给置信度数字")
    index = raw.get("index") or raw.get("索引") or {}
    if not isinstance(index, dict):
        return None, f"索引必须是对象，收到 {type(index).__name__}"
    if op == "reset" and raw.get("value") is not None:
        return None, "reset 是拧回默认，不该带值"
    # 值可以在这里缺省（开关型旋钮不需要数值），但校验器会对需要数值的族报错——
    # 不在这一层静默补默认值，免得"漏给值"变成"当成 1"。
    value = raw.get("value") if raw.get("value") is not None else raw.get("值")
    return Patch(
        slot=slot, index=index, op=op, value=value,
        strength=strength, utterance=str(raw.get("utterance") or raw.get("原话") or ""),
    ), None


# 「2」「2号」「2号船」「作业线2」都是 2 号船——作业线编号只有一种读法
_LINE_RE = re.compile(r"^(?:作业线|船|线)?\s*(\d+)\s*(?:号(?:船|作业线|线)?)?$")


def _to_line_no(value: Any) -> Any:
    """把作业线编号的各种写法归一成整数。数字本身没有第二种解释，这不是猜。"""
    if isinstance(value, str):
        hit = _LINE_RE.match(value.strip())
        if hit:
            return int(hit.group(1))
    return value


_TRUE = ("是", "允许", "开", "启用", "true", "yes")
_FALSE = ("否", "不允许", "关", "停用", "false", "no")


def _unique_enum(value: Any, allowed: tuple[str, ...]) -> Any:
    """枚举归一：只有一个候选对得上才归一，对不上或撞车就原样返回（让校验器去报错）。

    模型会说「恶劣」「任务数」「总成本」这类口语简称。枚举是封闭的，简称到全称的映射
    只要唯一就是确定的——不唯一就不许归一，这正是「不猜」的边界。
    """
    if not isinstance(value, str) or value in allowed:
        return value
    text = value.strip()
    hits = [a for a in allowed if text == a or text in a or a in text]
    return hits[0] if len(hits) == 1 else value


_ENUM_KINDS = {
    "metric": None,          # 运行时取 METRIC_NAMES
    "strategy": None,        # 运行时取 STRATEGY_NAMES
    "severity": ("高", "中", "低"),
    "sea": ("常态", "恶劣但安全"),
    "项": ("风速", "浪高", "电价"),
    "端": ("开始", "结束"),
    "粒度": ("天", "窗口"),
    "cap_维度": None,        # 运行时取 CAP_DIMENSIONS
    "budget_口径": None,     # 运行时取 BUDGET_KINDS
}


# 口径的口语别名：模型常照 metrics 的名字写（船舶艘天），而槽位登记的是「租船天」。
# 显式登记别名是确定的映射，不是猜——猜是指「两个候选都对得上还硬选一个」。
_KIND_ALIASES = {
    "budget_口径": {"人工费": "人工", "备件费": "备件", "船舶艘天": "租船天",
                    "租船费用": "租船天", "总经济成本": "总成本", "总费用": "总成本",
                    "现金支出": "现金"},
    # 「起」不是「开始」的子串，是同义词——子串匹配救不了，只能显式登记
    "端": {"起": "开始", "起始": "开始", "开始时间": "开始",
           "止": "结束", "结束时间": "结束"},
}


def normalize_patch(patch: "Patch") -> "Patch":
    """把大模型给的口语化写法归一到注册值。只做唯一可判定的归一，其余原样留给校验器。

    放在翻译层而不是校验层：这是「大模型输出的自然波动」，不是「用户写错了」——
    两类问题要分开处理，混在一起会让校验器替模型背锅。
    """
    from . import slots as S  # 延迟导入，避免 slots↔patch 循环

    # 模型写的 kind 常常是口语（"口径"而非"budget_口径"）。枚举按注册表**声明**的
    # kind 去查——校验器本来也只用声明的 kind，这里跟它保持一致。
    # 单 kind 的维度（量/口径/维度/任务/部件/窗口/项/端…）只有一种可能，写好值就够了；
    # 多 kind 的维度（组/桶/范围：部件还是风机？窗口还是某日？）必须带 kind，否则就是猜。
    declared = dict(S.SLOTS.get(patch.slot).index) if patch.slot in S.SLOTS else {}
    multi_kind = {"group", "bucket", "group_or_bucket"}
    index = {}
    for key, spec in patch.index.items():
        if not isinstance(spec, dict):
            kind = declared.get(key)
            if kind and kind not in multi_kind:
                spec = {"kind": kind, "value": spec}
            else:
                index[key] = spec  # 多 kind 维度给了裸值：不猜，交给校验器报错
                continue
        spec = dict(spec)
        value = spec.get("value")
        if (spec.get("kind") or declared.get(key)) in ("作业线", "task", "task_set", "task_seq"):
            value = _to_line_no(value)  # 作业线/任务编号的各种写法归一
        # 「11月16号」被写成区间 [16,16] 或单元素清单，都归一成单值
        if isinstance(value, (list, tuple)):
            if len(value) == 1:
                value = value[0]
            elif len(value) == 2 and value[0] == value[1]:
                value = value[0]
        kind = declared.get(key) or spec.get("kind")
        if kind in _KIND_ALIASES and isinstance(value, str):
            value = _KIND_ALIASES[kind].get(value.strip(), value)
        if kind in _ENUM_KINDS:
            allowed = _ENUM_KINDS[kind]
            if allowed is None:
                allowed = {"metric": S.METRIC_NAMES, "strategy": S.STRATEGY_NAMES,
                           "cap_维度": S.CAP_DIMENSIONS, "budget_口径": S.BUDGET_KINDS}[kind]
            value = _unique_enum(value, allowed)
        spec["value"] = value
        index[key] = spec

    value = patch.value
    if isinstance(value, str):
        text = value.strip()
        if text in _TRUE:
            value = True
        elif text in _FALSE:
            value = False
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            # 只给日期不给时刻，按当天零点补——这是确定的，不是猜
            value = f"{text}T00:00:00+08:00"
        elif re.fullmatch(r"\d+\s*号(船|作业线|线)?", text):
            value = int(re.match(r"\d+", text).group())  # 「2号船」→ 2
    slot = S.SLOTS.get(patch.slot)
    if slot is not None and slot.value_type == "act":
        value = None  # 开关型：点亮即生效，带什么值都一样
    elif patch.slot == "line_fixed":
        value = _to_line_no(value)
    return Patch(slot=patch.slot, index=index, op=patch.op, value=value,
                 strength=patch.strength, utterance=patch.utterance)


def absolute_value(op: str, value: Any, baseline: Any) -> tuple[Any, str | None]:
    """把相对运算按基线折算成绝对值。基线取不到就报错，不按 1 或 0 硬算。"""
    if op == "reset":
        return None, None
    if op == "=":
        return value, None
    if baseline is None:
        return None, f"运算 {op} 需要基线值，但这一格的基线取不到"
    try:
        if op == "*":
            return float(baseline) * float(value), None
        if op == "+":
            return float(baseline) + float(value), None
        if op == "-":
            return float(baseline) - float(value), None
    except (TypeError, ValueError):
        return None, f"运算 {op} 的值 {value!r} 和基线 {baseline!r} 算不出结果"
    return None, f"未知运算 {op!r}"


def sort_patches(patches: list[Patch]) -> list[Patch]:
    """稳定排序：同一句话两种写法出来的补丁集必须落在同一顺序上，指纹才可复现。"""
    return sorted(patches, key=lambda p: (p.slot, json.dumps(p.index, sort_keys=True, ensure_ascii=False)))


def duplicate_cells(patches: list[Patch]) -> list[str]:
    """找出被写了两次的格子（决策 D2：直接报错，不累乘）。"""
    seen: set[str] = set()
    dups: list[str] = []
    for patch in patches:
        if patch.cell in seen:
            dups.append(patch.cell)
        seen.add(patch.cell)
    return dups


def fingerprint(patches: list[Patch], extra: dict[str, Any] | None = None) -> str:
    """补丁集的指纹（同一份输入两次跑必须字节一致，这是发布闸门之一）。"""
    payload = {
        "补丁": [p.to_json() for p in sort_patches(patches)],
        "附加": extra or {},
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class PatchError(RuntimeError):
    """补丁层面的拦截：形状不对、槽位不在册、同格重复等。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("；".join(errors))
        self.errors = errors
