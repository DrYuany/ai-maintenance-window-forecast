"""执行侧客户端：把 DSL 交给子进程真跑一遍。

这里只做三件事：起进程、限时、解析。**不 import ortools，也不 import 求解内核**——
一旦在这里 import，Web 进程就会带上求解器的全部状态，慢请求能拖死整个对照台。

一条硬规矩：**超时就是超时，不许当成"不可行"**。
CP-SAT 跑到时限没证完，正确说法是"还没证完"，不是"做不到"。
这两句话在业务上的差别是：前者可以再等等，后者会让人去改需求。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "worker.py"

# 默认只跑前几个任务：完整 12 任务的模型在短时限内证不完，跑完也说明不了什么。
# 子集能验证"约束怎么写"这件事，而这是理解层的责任范围。
DEFAULT_MAX_TASKS = 6
DEFAULT_SECONDS = 6


@dataclass
class Execution:
    ok: bool = False
    status: str = "SKIPPED"
    payload: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""
    mode: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "status": self.status,
            "seconds": self.seconds,
            "error": self.error,
            "payload": self.payload,
        }


def _run(mode: str, payload: dict[str, Any], timeout: float) -> Execution:
    body = dict(payload, mode=mode)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, str(WORKER)],
            input=json.dumps(body, ensure_ascii=False),
            capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, env=env, cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return Execution(ok=False, status="TIMEOUT", mode=mode, seconds=timeout,
                         error=f"子进程 {timeout:.0f} 秒没跑完，已终止；"
                               "这只说明没跑完，不说明不可行")
    if proc.returncode != 0 and not proc.stdout.strip():
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        return Execution(ok=False, status="ERROR", mode=mode, error="；".join(tail) or "子进程无输出")
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        tail = (proc.stderr or "").strip().splitlines()[-3:]
        return Execution(ok=False, status="BAD_OUTPUT", mode=mode,
                         error="；".join(tail) or proc.stdout[:200])
    return Execution(
        ok=bool(data.get("ok")),
        status=str(data.get("status") or ("OK" if data.get("ok") else "ERROR")),
        payload=data,
        seconds=float(data.get("seconds") or 0.0),
        error="；".join(data.get("errors") or []),
        mode=mode,
    )


def solve(dsl: dict[str, Any], *, seconds: int = DEFAULT_SECONDS,
          max_tasks: int | None = DEFAULT_MAX_TASKS,
          partial: bool = False, timeout: float | None = None) -> Execution:
    """按 DSL 求解。`timeout` 是**进程级**兜底，比求解时限多给 30 秒。"""
    if not dsl:
        return Execution(ok=False, status="EMPTY", mode="solve", error="DSL 为空，没有可执行的东西")
    return _run("solve", {"dsl": dsl, "seconds": seconds, "max_tasks": max_tasks,
                          "partial": partial},
                timeout=timeout or seconds + 40)


def profile(*, max_tasks: int | None = None, timeout: float = 90) -> Execution:
    """两条启发式基线的成本构成。不调求解器，快。"""
    return _run("profile", {"max_tasks": max_tasks}, timeout=timeout)


def diff(strategies: list[str] | None = None, *, seconds: int = 4,
         max_tasks: int | None = 4, timeout: float = 180) -> Execution:
    return _run("diff", {"strategies": strategies or ["成本最低优先", "高风险优先抢修"],
                         "seconds": seconds, "max_tasks": max_tasks}, timeout=timeout)


if __name__ == "__main__":            # 手动冒烟：python src/execute.py
    print(json.dumps(profile().as_dict(), ensure_ascii=False, indent=1)[:1200])
