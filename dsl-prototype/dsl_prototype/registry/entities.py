"""实体注册表：DSL 里可引用的 7 个实体及引用解析规则。

实体 = 有身份、能被用户点名引用的东西。严重度、海况等分类标签不是实体，
它们是过滤维度的取值（见 quantities.py 的 FILTERS）。

解析依赖 Snapshot（product_data.load_project 的产物）：
  - 任务/风机/部件/窗口：直接查快照里的编号集合
  - 作业线：1..N 整数
  - 人员：当前模型只有总数（个体身份预留，原型不支持"某个人"引用）
  - 时间：带时区字符串，经 Snapshot.minute 换算
"""
from __future__ import annotations

from typing import Any

# 实体名 -> 中文说明
ENTITIES: dict[str, str] = {
    "task": "任务（任务编号，如 任务018）",
    "turbine": "风机（风机编号，如 风机001）",
    "part": "部件（部件编号/名称，如 齿轮箱、部件04）",
    "window": "海事窗口（窗口编号，如 窗口02）",
    "line": "作业线/船（线号 1..N）",
    "crew": "人员（当前仅总数，个体身份预留）",
    "time": "时间（日/小时，带时区）",
}


def resolve_task(data: Any, ref: str) -> str | None:
    """把用户引用解析成快照里的任务编号；不存在返回 None。"""
    ref = str(ref).strip()
    ids = {t["任务编号"] for t in data.tasks}
    if ref in ids:
        return ref
    # 容忍"任务18"/"T18"/"18"这类简写
    plain = ref.lstrip("T任务t").zfill(3)
    if plain.isdigit() and f"任务{plain}" in ids:
        return f"任务{plain}"
    return None


def resolve_turbine(data: Any, ref: str) -> str | None:
    ref = str(ref).strip()
    ids = {t["风机编号"] for t in data.tasks}
    if ref in ids:
        return ref
    plain = ref.lstrip("风机").zfill(3)
    if plain.isdigit() and f"风机{plain}" in ids:
        return f"风机{plain}"
    return None


def resolve_part(data: Any, ref: str) -> str | None:
    """部件可按编号（部件04）或名称（齿轮箱）引用。"""
    ref = str(ref).strip()
    for pid, part in data.parts.items():
        if ref in (pid, part["部件名称"]):
            return pid
    return None


def resolve_window(data: Any, ref: str) -> str | None:
    ref = str(ref).strip()
    ids = {w["编号"] for w in data.observation_windows}
    if ref in ids:
        return ref
    plain = ref.lstrip("窗口").zfill(2)
    if plain.isdigit() and f"窗口{plain}" in ids:
        return f"窗口{plain}"
    return None


def resolve_minute(data: Any, ref: str) -> int | None:
    """把时间引用解析成分钟偏移（相对快照 base）。"""
    try:
        minute = data.minute(str(ref).strip())
        if 0 <= minute <= data.end:
            return minute
    except (ValueError, AttributeError):
        return None
    return None
