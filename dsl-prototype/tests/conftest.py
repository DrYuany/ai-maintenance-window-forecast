from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]           # dsl-prototype/
PROJECT = ROOT.parent                                  # AI窗口期预测产品/
SCRIPTS = PROJECT / "maintenance-milp-modeler" / "scripts"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# 钉住规划时点：2026-10-23 18:00 收到 25 项告警，目标窗口 窗口02。
os.environ["PLANNING_AS_OF"] = "2026-10-23T18:00:00+08:00"
os.environ["SKIP_LOCAL_ENV"] = "1"
