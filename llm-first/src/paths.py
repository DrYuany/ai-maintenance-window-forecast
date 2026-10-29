"""把规则主导版的 backend 挂进 sys.path。

只读引用：注册表（表 A~F 的事实来源）与校验器。
本目录不改 backend 任何一行——要改执行侧，回 ../backend 改，两边一起受益。
这样两种产品共用同一个执行侧，对比才有意义。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"


def ensure() -> None:
    path = str(BACKEND)
    if path not in sys.path:
        sys.path.insert(0, path)


ensure()
