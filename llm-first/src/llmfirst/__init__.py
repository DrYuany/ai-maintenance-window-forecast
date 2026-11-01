"""LLM 主导版理解层的命名空间包。

把仓库根（dsl_core）、backend（rulebench 对照用）、求解内核挂进 sys.path——
必须在本包其它模块 import 之前跑，否则 `from dsl_core import ...` 会 ModuleNotFoundError。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
for _path in (
    str(_ROOT),                                          # dsl_core
    str(_ROOT / "backend"),                              # rulebench 对照用 app.core
    str(_ROOT / "maintenance-milp-modeler" / "scripts"),  # 求解内核
):
    if _path not in sys.path:
        sys.path.insert(0, _path)
