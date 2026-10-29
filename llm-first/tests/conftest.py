"""让 tests/ 能直接 import src/ 下的模块（llm-first 是独立包，不装进 site-packages）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
