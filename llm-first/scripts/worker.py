"""执行侧工人：在**子进程**里把 DSL 真跑一遍，结果以 JSON 打回 stdout。

为什么必须另起进程：

1. **超时**。CP-SAT 在 12 个任务上就要几秒，25 个任务能跑到几分钟。
   放在 Web 进程里，一次慢请求就能把整个对照台拖死。
2. **状态**。`product_planner` 里有一堆模块级常量（MONEY、STRATEGIES），
   求解还会往 `product-output/` 写中间模型文件。进程隔离最省事。
3. **依赖**。ortools 导入要一两秒。只有真的要跑求解时才付这个钱。

协议：stdin 一个 JSON，stdout 一个 JSON。所有库的打印都走 stderr，
stdout 只能有结果——否则调用方解析不了。

三种 mode：

| mode | 干什么 | 用在哪 |
|---|---|---|
| `solve` | 按 DSL 跑 CP-SAT | 执行侧（页面上点"跑一遍"） |
| `profile` | 跑两条启发式基线并独立核算成本 | 回答"为什么这么贵" |
| `diff` | 同一批任务换策略各解一次 | 回答"经济优先和风险优先差多少" |

`profile` 不调求解器，0.02 秒出结果；`diff` 要真解两次，慢，所以默认只取前几个任务。
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent.parent              # 仓库根：AI窗口期预测产品/
BACKEND = ROOT / "backend"
KERNEL = ROOT / "maintenance-milp-modeler" / "scripts"

for path in (str(BACKEND), str(KERNEL)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _load(dsl: dict | None = None, max_tasks: int | None = None, base_id: str = "基座01"):
    """按 DSL 重建数据快照。**这段和 `dsl/runner.run` 里那几行是同一套**——
    runner 不把 data 对象返出来，而我们做独立核算（account）必须拿到它。
    复制是有代价的，所以这里只做"读数据"这一步，求解仍然走 runner。
    """
    from product_data import load_project

    base = load_project(ROOT, base_id=base_id)
    overrides = data_patches = timeline = None
    if dsl:
        from app.core.dsl.compiler import compile_data, compile_parameters

        if dsl.get("parameters"):
            overrides = compile_parameters(dsl["parameters"], base)
        if dsl.get("data"):
            data_patches, timeline = compile_data(dsl["data"], base)
    data = load_project(ROOT, base_id=base_id, overrides=overrides,
                        data_patches=data_patches, timeline_patches=timeline)
    if max_tasks:
        data.tasks = data.tasks[:max_tasks]
    return data


def _breakdown(data, schedule) -> dict:
    """独立核算：船租 / 人工 / 备件 / 其他 / 发电损失。**不复用求解器的目标值**，
    因为它算的是模型里的表达式，这里算的是"把这份排程拿去执行要花多少钱"。
    两者对不上，正是需要知道的信号（求解器的 目标值 vs account 的 总成本）。
    """
    from product_planner import account

    try:
        bill = account(data, schedule)
    except Exception as exc:                      # 排程本身不合法时不要假装算得出来
        return {"error": f"{type(exc).__name__}: {exc}"}
    bill.pop("排程", None)
    return bill


def mode_solve(payload: dict) -> dict:
    from app.core.dsl.runner import DslError, run

    dsl = payload.get("dsl") or {}
    seconds = int(payload.get("seconds") or 10)
    max_tasks = payload.get("max_tasks")
    started = time.time()
    try:
        report = run(dsl, base_id=payload.get("base_id", "基座01"),
                     seconds=seconds, max_tasks=max_tasks,
                     partial=bool(payload.get("partial")))
    except DslError as exc:
        return {"ok": False, "status": "DSL_ERROR", "errors": exc.errors,
                "seconds": round(time.time() - started, 2)}
    except Exception as exc:
        return {"ok": False, "status": "ERROR",
                "errors": [f"{type(exc).__name__}: {exc}"],
                "seconds": round(time.time() - started, 2)}

    info = report.get("求解") or {}
    schedule = report.get("排程") or []
    data = _load(dsl, max_tasks=max_tasks, base_id=payload.get("base_id", "基座01"))
    return {
        "ok": bool(info.get("有解")),
        "status": info.get("解状态", "UNKNOWN"),
        "terminated": info.get("终止原因"),
        "stages": len(info.get("阶段") or []),
        "scheduled": len(schedule),
        "tasks": len(data.tasks),
        "model_cost": (info.get("阶段") or [{}])[0].get("目标值") if info.get("阶段") else None,
        "breakdown": _breakdown(data, schedule) if schedule else None,
        "seconds": round(time.time() - started, 2),
        "solver_seconds": info.get("总耗时秒"),
        "note": "；".join(
            part for part in (
                "分层全部最优" if info.get("分层全部最优") else "分层未证明全部最优",
                f"终止于 {info.get('终止原因')}" if info.get("终止原因") else "",
            ) if part),
    }


def _profile_one(data, kind: str) -> dict:
    from product_planner import account, candidates, heuristic

    options = {task["任务编号"]: candidates(data, task) for task in data.tasks}
    plan = heuristic(data, options, kind)
    bill = account(data, plan)
    return {
        "策略": kind,
        "完成任务数": bill["完成任务数"],
        "待修总数": bill["待修总数"],
        "未安排": [m["任务编号"] for m in bill["未安排"]],
        "最晚完工": bill["最晚完工"],
        "船舶艘天": bill["船舶艘天"],
        "船租": bill["船租"],
        "人工": bill["人工"],
        "备件": bill["备件"],
        "其他费用": bill["其他费用"],
        "发电损失": bill["发电损失"],
        "现金支出": bill["现金支出"],
        "总成本": bill["总成本"],
    }


def mode_profile(payload: dict) -> dict:
    data = _load(payload.get("dsl"), max_tasks=payload.get("max_tasks"))
    started = time.time()
    out = [_profile_one(data, kind) for kind in ("严重性优先", "低风速优先")]
    return {"ok": True, "profiles": out, "tasks": len(data.tasks),
            "seconds": round(time.time() - started, 2)}


def mode_diff(payload: dict) -> dict:
    """同一批任务，换目标策略各解一次。**这不是"哪个方案更好"的证明**，
    只是同一时限下两种目标各自能走到哪——时限短，结论要打折。"""
    from app.core.dsl.runner import run

    base_dsl = payload.get("dsl") or {}
    seconds = int(payload.get("seconds") or 5)
    max_tasks = payload.get("max_tasks") or 6
    rows = []
    for strategy in payload.get("strategies") or ["成本最低优先", "高风险优先抢修"]:
        started = time.time()
        try:
            report = run(base_dsl, seconds=seconds, max_tasks=max_tasks, strategy=strategy)
            info = report.get("求解") or {}
            data = _load(base_dsl, max_tasks=max_tasks)
            schedule = report.get("排程") or []
            bill = _breakdown(data, schedule) if schedule else {"error": "无排程"}
            rows.append({
                "策略": strategy,
                "状态": info.get("解状态"),
                "完成任务数": len(schedule),
                "总成本": (bill or {}).get("总成本"),
                "现金支出": (bill or {}).get("现金支出"),
                "最晚完工": (bill or {}).get("最晚完工"),
                "发电损失": (bill or {}).get("发电损失"),
                "耗时秒": round(time.time() - started, 2),
            })
        except Exception as exc:
            rows.append({"策略": strategy, "状态": "ERROR",
                         "错误": f"{type(exc).__name__}: {exc}",
                         "耗时秒": round(time.time() - started, 2)})
    return {"ok": True, "rows": rows, "tasks": max_tasks, "seconds": seconds}


MODES = {"solve": mode_solve, "profile": mode_profile, "diff": mode_diff}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError as exc:
        json.dump({"ok": False, "status": "BAD_INPUT", "errors": [str(exc)]}, sys.stdout,
                  ensure_ascii=False)
        return 1
    mode = payload.get("mode") or "solve"
    handler = MODES.get(mode)
    if handler is None:
        json.dump({"ok": False, "status": "BAD_MODE", "errors": [mode]}, sys.stdout,
                  ensure_ascii=False)
        return 1
    try:
        result = handler(payload)
    except Exception:
        result = {"ok": False, "status": "ERROR",
                  "errors": traceback.format_exc(limit=4).splitlines()[-6:]}
    json.dump(result, sys.stdout, ensure_ascii=False, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
