"""把规则主导版的 backend 挂进 sys.path。

只读引用：注册表（表 A~F 的事实来源）与校验器。
本目录不改 backend 任何一行——要改执行侧，回 ../backend 改，两边一起受益。
这样两种产品共用同一个执行侧，对比才有意义。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
# 求解内核（product_data / product_planner）。只有读参数快照和跑求解时才用得上，
# 但路径要在这里统一挂——否则每个模块各写一份，迟早有一处漏了、报 ModuleNotFoundError。
KERNEL = ROOT / "maintenance-milp-modeler" / "scripts"


def ensure() -> None:
    for path in (str(BACKEND), str(KERNEL)):
        if path not in sys.path:
            sys.path.insert(0, path)


ensure()
