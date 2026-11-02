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
    if op != "reset" and (raw.get("value") is None and (raw.get("值") is None)):
        return None, f"运算 {op} 需要一个值"
    value = raw.get("value") if raw.get("value") is not None else raw.get("值")
    return Patch(
        slot=slot, index=index, op=op, value=value,
        strength=strength, utterance=str(raw.get("utterance") or raw.get("原话") or ""),
    ), None


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
