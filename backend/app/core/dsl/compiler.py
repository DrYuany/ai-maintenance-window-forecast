"""编译器：DSL → CP-SAT 约束/目标（物化层）。

通过 product_planner.build_model 的注入闭包工作：
  - constraints_builder：build_model 建完 choices 后回调，把 DSL 约束加进模型
  - stages_builder：返回自定义分层目标，绕过四模板

量纲缩放（CP-SAT 只吃整数系数）：
  - 时间量（finish/start/day）        → 分钟，整数，scale 1
  - 工时（work_hours）                → 分钟（作业段分钟和），scale 60
  - 成本量（labor/material/other_fee）→ 微元（×1e6，与生产 MONEY 一致）

原型限制（明确报错，不静默给错数）：
  - 同一 linear 表达式内不允许混合时间量与成本量（量纲不同）
  - max/derived 形式未实现（校验器已拦）
"""
from __future__ import annotations

from typing import Any, Callable

from . import generator
from .registry import primitives as prim_reg
from .registry import quantities as qty_reg
from .registry.entities import resolve_minute, resolve_task

MONEY = 1_000_000  # 微元，与 product_planner.MONEY 一致


def _scale_of_coeff(coeff: str) -> int:
    if coeff in ("finish", "start", "day"):
        return 1
    if coeff == "work_hours":
        return 60  # 小时 → 分钟
    if coeff in ("labor", "material", "other_fee"):
        return MONEY
    return 1


def _coeff_of_quantity(ref: object) -> str:
    resolved = qty_reg.resolve_quantity(ref)
    if isinstance(resolved, tuple):
        return resolved[1]
    if isinstance(resolved, dict):
        return resolved.get("coeff", "one")
    raise ValueError(f"量引用无法解析: {ref!r}")


# 由 build_model metrics 提供的具名量的量纲缩放（微元 vs 分钟）
_METRICS_SCALES = {
    "总经济成本": MONEY, "现金支出": MONEY,
    "最后完工时间": 1, "完工时间和": 1, "完成任务数": 1,
    "高严重完成数": 1, "中严重完成数": 1, "低严重完成数": 1,
    "高严重完工时间和": 1, "中严重完工时间和": 1, "低严重完工时间和": 1,
}


def _scale_of_quantity(ref: object) -> int:
    if isinstance(ref, str) and ref in _METRICS_SCALES:
        return _METRICS_SCALES[ref]
    return _scale_of_coeff(_coeff_of_quantity(ref))


def materialize(
    model,
    choices: list[tuple[dict, Any]],
    cands: list[dict],
    ref: object,
    data,
    metrics: dict | None = None,
):
    """量引用 → cp_model 线性表达式（带量纲缩放）。

    build_model 已算好的指标（总经济成本/现金支出/最后完工时间等）直接从
    metrics dict 引用——它们是 max/复合形式，生成器无法从候选字段重算。
    """
    if isinstance(ref, str) and metrics is not None and ref in metrics:
        return metrics[ref]
    terms = generator.resolve_quantity_terms(cands, ref, data)
    coeff = _coeff_of_quantity(ref)
    scale = _scale_of_coeff(coeff)
    return _weighted(model, choices, terms, scale)


def _weighted(model, choices: list[tuple[dict, Any]], terms: list[tuple[float, int]], scale: int):
    if not terms:
        return model.NewConstant(0)
    variables = [choices[index][1] for _coef, index in terms]
    coefficients = [int(round(coef * scale)) for coef, _index in terms]
    return sum(c * v for c, v in zip(coefficients, variables))


def _task_subset(cands: list[dict], task_ref: str, predicate) -> list[int]:
    """某任务中满足谓词的候选下标。predicate(c) -> bool。"""
    return [
        index for index, c in enumerate(cands)
        if c["任务编号"] == task_ref and predicate(c)
    ]


def _add_subset_zero(model, choices, indexes: list[int]) -> None:
    if not indexes:
        return
    variables = [choices[i][1] for i in indexes]
    model.Add(sum(variables) == 0)


# ── 约束编译 ──────────────────────────────────────────────────────────────────

def compile_constraint(
    model,
    choices: list[tuple[dict, Any]],
    cands: list[dict],
    data,
    spec: dict[str, Any],
    metrics: dict | None = None,
) -> list[str]:
    """单条约束 → 加进模型，返回错误列表（空 = 成功）。"""
    ctype = spec["type"]
    errors: list[str] = []
    if ctype == "must":
        task = resolve_task(data, spec["task"])
        terms = generator.per_task_terms(cands, task, "day", data)  # 系数无关，只取变量
        variables = [choices[i][1] for _c, i in terms]
        model.Add(sum(variables) == 1)
    elif ctype == "exclude":
        task = resolve_task(data, spec["task"])
        terms = generator.per_task_terms(cands, task, "day", data)
        variables = [choices[i][1] for _c, i in terms]
        model.Add(sum(variables) == 0)
    elif ctype == "precedence":
        before = resolve_task(data, spec["before"])
        after = resolve_task(data, spec["after"])
        gap = int(spec.get("gap") or 0)
        finish_b = _weighted(model, choices, generator.per_task_terms(cands, before, "finish", data), 1)
        start_a = _weighted(model, choices, generator.per_task_terms(cands, after, "start", data), 1)
        model.Add(finish_b + gap <= start_a)
    elif ctype == "co_occur":
        tasks = [resolve_task(data, ref) for ref in spec["tasks"]]
        day_terms = {t: generator.per_task_terms(cands, t, "day", data) for t in tasks}
        day_expr = {t: _weighted(model, choices, terms, 1) for t, terms in day_terms.items()}
        pairs = [(tasks[i], tasks[j]) for i in range(len(tasks)) for j in range(i + 1, len(tasks))]
        days_total = data.end // 1440
        for a, b in pairs:
            if spec["mode"] == "same_day":
                model.Add(day_expr[a] == day_expr[b])
            elif spec["mode"] == "diff_day":
                flag = model.NewBoolVar(f"dsl_diff_day_{a}_{b}")
                model.Add(day_expr[a] - day_expr[b] <= -1 + (days_total + 1) * (1 - flag))
                model.Add(day_expr[b] - day_expr[a] <= -1 + (days_total + 1) * flag)
            else:
                errors.append(f"co_occur 模式 {spec['mode']!r} 原型未实现")
    elif ctype == "fix":
        task = resolve_task(data, spec["task"])
        line = int(spec["line"])
        bad = [i for i, c in enumerate(cands) if c["任务编号"] == task and c.get("作业线") != line]
        _add_subset_zero(model, choices, bad)
    elif ctype == "cardinality":
        terms = generator.quantity_terms(cands, "count", "one", spec["filter"], data)
        expr = _weighted(model, choices, terms, 1)
        _add_op(model, expr, spec["op"], int(spec["value"]))
    elif ctype == "deadline":
        task = resolve_task(data, spec["task"])
        minute = resolve_minute(data, spec["time"])
        bad = _task_subset(cands, task, lambda c: c["完成"] > minute)
        _add_subset_zero(model, choices, bad)
    elif ctype == "earliest":
        task = resolve_task(data, spec["task"])
        minute = resolve_minute(data, spec["time"])
        bad = _task_subset(cands, task, lambda c: c["开始"] < minute)
        _add_subset_zero(model, choices, bad)
    elif ctype == "forbidden_dates":
        minutes = [resolve_minute(data, date) for date in spec["dates"]]
        bad = [i for i, c in enumerate(cands) if c["开始"] // 1440 in {m // 1440 for m in minutes}]
        _add_subset_zero(model, choices, bad)
    elif ctype == "budget":
        scale = _scale_of_quantity(spec["quantity"])
        expr = materialize(model, choices, cands, spec["quantity"], data, metrics)
        _add_op(model, expr, spec["op"], int(round(float(spec["value"]) * scale)))
    elif ctype == "linear":
        scales = {_scale_of_quantity(ref) for _c, ref in spec["terms"]}
        if len(scales) > 1:
            errors.append("linear 原语不允许混合时间量与成本量（量纲不同）")
            return errors
        scale = next(iter(scales))
        total = 0
        for coef, ref in spec["terms"]:
            for s, i in generator.resolve_quantity_terms(cands, ref, data):
                total = total + int(round(float(coef) * s * scale)) * choices[i][1]
        _add_op(model, total, spec["op"], int(round(float(spec["value"]) * scale)))
    else:
        errors.append(f"原语 {ctype} 已登记但原型未实现编译")
    return errors


def _add_op(model, expr, op: str, bound: int) -> None:
    if op == "<=":
        model.Add(expr <= bound)
    elif op == ">=":
        model.Add(expr >= bound)
    elif op == "==":
        model.Add(expr == bound)
    else:  # pragma: no cover - 校验器已拦
        raise ValueError(f"非法运算符 {op}")


# ── 注入闭包 ──────────────────────────────────────────────────────────────────

def constraints_builder(
    dsl_constraints: list[dict[str, Any]],
    data,
) -> Callable:
    """build_model 的 extra_constraints 闭包：(m, choices, task_vars, metrics) -> 错误列表。"""
    def _apply(m, choices, task_vars, metrics) -> list[str]:
        cands = generator.candidate_rows(choices)
        errors: list[str] = []
        for index, spec in enumerate(dsl_constraints):
            errors.extend(compile_constraint(m, choices, cands, data, spec, metrics))
            if errors:
                errors.append(f"（第 {index + 1} 条约束编译失败）")
                break
        return errors
    return _apply


def stages_builder(dsl_objectives: list[dict[str, Any]], data) -> Callable:
    """build_model 的 custom_stages 闭包：(m, choices, metrics) -> stages 列表。"""
    def _build(m, choices, metrics):
        cands = generator.candidate_rows(choices)
        stages: list[tuple[str, str, Any]] = []
        for index, spec in enumerate(dsl_objectives):
            otype = spec["type"]
            if otype in ("minimize", "maximize"):
                expr = materialize(m, choices, cands, spec["q"], data, metrics)
                stages.append((f"dsl_{index}_{otype}", "minimize" if otype == "minimize" else "maximize", expr))
            elif otype == "lexmin":
                for j, stage in enumerate(spec["stages"]):
                    expr = materialize(m, choices, cands, stage["q"], data, metrics)
                    stages.append((f"dsl_{index}_{j}_{stage['dir']}", stage["dir"], expr))
            else:
                raise ValueError(f"目标 {otype} 已登记但原型未实现编译")
        return stages
    return _build
