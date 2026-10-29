"""行号目录：P / X / C / O / D 五组行号的唯一来源。

提示词用它生成表格，verify 用它判 A5（行号在不在）、A7（必填有没有）、A8（dsl 与 fill 对不对得上）。

为什么要有这个模块：上一版手抄行号错过两次——`P11` 写成"安全风速上限"实际是"常态浪高上限"，
`P13` 想指船租实际是"安全浪高上限"。手抄必错，所以行号一律由注册表算，
表格和判据都从这里取，改注册表时两边一起动。

X 组是"常用组合"的快捷方式（X02 预算 ≡ C 组的 budget，X04 强制维修 ≡ must），
所以它们和对应的 C/O 行是别名关系，A8 匹配时按 `kind` 认，不按行号认。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ""):  # 允许 `python src/rows.py` 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402,F401  （副作用：把 ../backend 挂进 sys.path）

import prompt  # noqa: E402  （表序与注解的唯一来源，不重复抄一份）
from app.core.dsl import validator as dsl_validator  # noqa: E402
from app.core.dsl.registry import primitives as prim_reg  # noqa: E402

SECTIONS: tuple[str, ...] = ("parameters", "constraints", "objectives", "data")


@dataclass(frozen=True)
class Row:
    id: str                    # P01 / X02 / C07 / O02 / D03
    section: str               # 落到 DSL 的哪一段
    kind: str                  # 参数名 / 原语名 / data kind —— A8 按这个认
    label: str                 # 给人看的名字
    required: tuple[str, ...]  # 必填字段（A7）

    @property
    def is_param(self) -> bool:
        return self.section == "parameters"


# X 组：常用组合的快捷方式。required 里不含段名（段由 section 决定）。
_X_META: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "X01": ("objectives", "strategy", "标准目标策略", ("type", "name")),
    "X02": ("constraints", "budget", "费用预算上限", ("type", "quantity", "op", "value")),
    "X03": ("constraints", "cardinality", "共同最低完成任务数", ("type", "filter", "op", "value")),
    "X04": ("constraints", "must", "强制维修任务", ("type", "task")),
}


def _build() -> list[Row]:
    rows: list[Row] = []

    for row_id, name, _unit, _domain in prompt._table_a_rows():
        rows.append(Row(row_id, "parameters", name, name, ("field", "op", "value")))

    for index, _line in enumerate(prompt._TABLE_B_ROWS, start=1):
        row_id = f"X{index:02d}"
        section, kind, label, required = _X_META[row_id]
        rows.append(Row(row_id, section, kind, label, required))

    for index, (name, _note) in enumerate(prompt._TABLE_C_ORDER, start=1):
        required = ("type",) + tuple(prim_reg.CONSTRAINT_PRIMITIVES[name].get("required", []))
        rows.append(Row(f"C{index:02d}", "constraints", name, name, required))

    for index, name in enumerate(prompt._TABLE_D_ORDER, start=1):
        required = ("type",) + tuple(prim_reg.OBJECTIVE_PRIMITIVES[name].get("required", []))
        rows.append(Row(f"O{index:02d}", "objectives", name, name, required))

    for index, (kind, _fields, _said) in enumerate(prompt._TABLE_E_ORDER, start=1):
        required = ("kind",) + tuple(dsl_validator._DATA_PATCH_REQUIRED.get(kind, ()))
        rows.append(Row(f"D{index:02d}", "data", kind, kind, required))

    return rows


_ROWS: list[Row] = _build()
_BY_ID: dict[str, Row] = {row.id: row for row in _ROWS}


def all_rows() -> list[Row]:
    return list(_ROWS)


def by_id(row_id: str) -> Row | None:
    return _BY_ID.get(str(row_id or "").strip())


def ids() -> list[str]:
    return [row.id for row in _ROWS]


def row_of_param(name: str) -> str | None:
    for row in _ROWS:
        if row.section == "parameters" and row.kind == name:
            return row.id
    return None


def ids_by_section(section: str) -> list[str]:
    return [row.id for row in _ROWS if row.section == section]


def summary() -> str:
    return " / ".join(
        f"{prefix} 组 {sum(1 for r in _ROWS if r.id.startswith(prefix))} 项"
        for prefix in ("P", "X", "C", "O", "D")
    )


if __name__ == "__main__":
    for row in _ROWS:
        print(f"{row.id}\t{row.section:<12}\t{row.kind:<22}\t{','.join(row.required)}")
    print("---")
    print(summary())
