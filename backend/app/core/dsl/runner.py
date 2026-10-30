"""端到端 runner：DSL JSON + 主项目求解内核 → 求解报告。

流程：load_project → candidates → build_model(注入 DSL 闭包) → solve → 报告。
已下发检修单的锁定/部分排程兜底在原型里未处理（正式合并时走 recommend 路径）。

用法：
    from app.core.dsl.runner import run
    report = run(dsl, base_id="基座01", seconds=30)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[4]

_SCRIPTS = PROJECT_ROOT / "maintenance-milp-modeler" / "scripts"


class DslError(RuntimeError):
    """校验或编译失败，携带全部错误信息。"""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("；".join(errors))
        self.errors = errors


def _solver_modules():
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    from product_data import load_project  # noqa: F401
    from product_planner import candidates, heuristic, solve  # noqa: F401
    return load_project, candidates, heuristic, solve


def run(
    dsl: dict[str, Any],
    *,
    base_id: str = "基座01",
    as_of: str | None = None,
    seconds: int = 60,
    output: Path | None = None,
    strategy: str = "成本最低优先",
    mode: str = "分层",
    weights: dict[str, float] | None = None,
    max_tasks: int | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    """校验 DSL → 注入求解 → 返回 {报告, dsl, 求解信息}。

    max_tasks：原型测试用的小规模任务子集（完整 25 任务模型单线程 CP-SAT 在
    短时限内证明不了可行/不可行，生产默认 300s；golden 用例用 8~10 个任务
    验证约束语义，规模性能是主项目已有能力，不属于本原型验证范围）。
    partial：部分排程模式（允许丢任务），exclude/must/cardinality 等
    "选择类"原语只在部分模式下有意义（完整模式所有任务强制排入）。
    """
    from .compiler import (
        compile_data,
        compile_parameters,
        constraints_builder,
        stages_builder,
    )
    from .validator import validate_dsl

    load_project, candidates, heuristic, solve = _solver_modules()

    # 基座先读一次：相对参数倍率（op="*"）要按基座值换算
    base_data = load_project(PROJECT_ROOT, base_id=base_id, as_of=as_of)
    overrides = None
    if dsl.get("parameters"):
        overrides = compile_parameters(dsl["parameters"], base_data)
    data_patches = None
    timeline_patches = None
    if dsl.get("data"):
        data_patches, timeline_patches = compile_data(dsl["data"], base_data)

    try:
        data = load_project(
            PROJECT_ROOT,
            base_id=base_id,
            as_of=as_of,
            overrides=overrides,
            data_patches=data_patches,
            timeline_patches=timeline_patches,
        )
    except ValueError as exc:
        # 补丁落地时的拒绝理由本来就是说给用户听的（"这次改不会影响结果"、
        # "期限没让任何任务提前"），但它抛的是裸 ValueError，走出去会被当成
        # 系统内部错误。转成 DslError，让调用方按"需求表达有误"处理。
        raise DslError([str(exc)]) from exc
    if max_tasks:
        data.tasks = data.tasks[:max_tasks]

    errors = validate_dsl(dsl, data)
    if errors:
        raise DslError(errors)

    options = {t["任务编号"]: candidates(data, t) for t in data.tasks}
    # 期限补丁会让候选按截止时间截断（product_data.candidates 里 hi=min(窗口, 截止)）。
    # 一个任务若干不完，候选数直接变成 0 —— 它于是从"必须排进去"的名单里消失，
    # 求解照常返回 FEASIBLE，那几条躺在"未安排"里。用户说的是"必须修完"。
    # 这里提前认出来：基座里排得进、加完期限却排不进，就是期限挤掉的，不是本来就没窗口。
    if data_patches and not partial:
        starved = []
        for task in data.tasks:
            tid = task["任务编号"]
            if options.get(tid) or task.get("截止分钟") is None:
                continue
            base_task = next(
                (t for t in base_data.tasks if t["任务编号"] == tid), None
            )
            if base_task is not None and candidates(base_data, base_task):
                starved.append(tid)
        if starved:
            raise DslError([
                "这些任务在期限之前排不进来，改期限等于把它们挪出计划："
                + "、".join(starved)
            ])
    # 与生产 recommend() 一致：启发式基座当热启动提示（不牺牲最优性证明）。
    baselines = {name: heuristic(data, options, name) for name in ("严重性优先", "低风速优先")}
    hint = max(baselines.values(), key=len)
    output = output or (PROJECT_ROOT / "product-output" / "dsl-run")
    output.mkdir(parents=True, exist_ok=True)

    constraints = dsl.get("constraints") or []
    objectives = dsl.get("objectives") or []

    # 策略 / 权重是**求解器模式**，不是分层目标。先挑出来，否则
    # stages_builder 会拿 strategy / weighted 去建分层阶段，直接抛
    # 「已登记但原型未实现编译」——而同一份 DSL 走变更流程
    # （planning_bridge.dsl_to_request）明明是能跑的。同一个输入两个答案，
    # 是最难查的一类缺陷：你会以为是模型写错了。
    # 拆分逻辑只有一份，在 bridge 里；这里只是共用它。
    from .planning_bridge import split_objectives

    dsl_strategy, dsl_mode, dsl_weights, objectives = split_objectives(objectives)
    strategy = dsl_strategy or strategy
    mode = dsl_mode or mode
    if dsl_weights is not None:
        weights = dsl_weights

    extra = constraints_builder(constraints, data) if constraints else None
    stages = stages_builder(objectives, data) if objectives else None

    selected, info = solve(
        data,
        options,
        output,
        seconds=seconds,
        strategy=strategy,
        mode=mode,
        weights=weights,
        hint=hint,
        partial=partial,
        extra_constraints=extra,
        custom_stages=stages,
    )
    # 兜底方案必须点破，不能当成解交出去。
    #
    # product_planner.solve 会先把启发式基座当 fallback（partial 模式下也用它），
    # 那个基座是在**没有** DSL 约束的模型上排出来的。一旦约束把模型逼到不可行
    # 或时限内没解，它就把这份旧方案原样返回，解状态还写 FEASIBLE，
    # 来源字段里才藏着 "HEURISTIC_FALLBACK"。实测：同时要求"禁止任务001"
    # 和"必须任务001"，返回的方案里任务001 排得好好的。
    # 这不是"暂时没证明"，是"根本没照约束做"——必须报错。
    source = str((info or {}).get("方案来源") or "")
    if constraints and source == "HEURISTIC_FALLBACK":
        raise DslError([
            "求解器没有在满足这些约束的前提下找到解，返回的是未考虑约束的基座方案；"
            "不把它当作结果。请放宽约束，或确认这几条约束是否互相冲突："
            + str([spec.get("type") for spec in constraints])
        ])
    return {
        "数据": data.meta,
        "求解": info,
        "排程": selected,
        "dsl": dsl,
    }


def assert_constraint_held(selected: list[dict] | None, checker, label: str) -> None:
    """golden 判分辅助：对求解结果做业务断言，违反抛 AssertionError。"""
    assert selected is not None, f"{label}: 求解未产生排程"
    assert checker(selected), f"{label}: 排程未满足约束"
