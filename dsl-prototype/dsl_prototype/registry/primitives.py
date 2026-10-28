"""原语注册表：约束（七维度 + linear 兜底）与目标（3 个）。

每个原语登记：required 字段、optional 字段、原型是否已实现编译。
编译器（compiler.py）按此表把 DSL 约束编译成 CP-SAT 线性约束。
"""
from __future__ import annotations

OPS: tuple[str, ...] = ("<=", ">=", "==")

# ── 约束原语（七维度）───────────────────────────────────────────────────────

CONSTRAINT_PRIMITIVES: dict[str, dict] = {
    # 选择维度
    "must":            {"required": ["task"]},
    "exclude":         {"required": ["task"]},
    "at_most_one":     {"required": ["tasks"]},
    "at_least_one":    {"required": ["tasks"]},
    # 关系维度
    "precedence":      {"required": ["before", "after"], "optional": ["gap"]},
    "co_occur":        {"required": ["mode", "tasks"]},
    # 资源维度
    "no_overlap":      {"required": ["resource", "tasks"]},
    "capacity":        {"required": ["resource", "time", "limit"]},
    "fix":             {"required": ["task", "line"]},
    # 计数维度
    "cardinality":     {"required": ["filter", "op", "value"]},
    # 时间维度
    "deadline":        {"required": ["task", "time"]},
    "earliest":        {"required": ["task", "time"]},
    "forbidden_dates": {"required": ["dates"]},
    # 成本维度
    "budget":          {"required": ["quantity", "op", "value"]},
    # 兜底
    "linear":          {"required": ["terms", "op", "value"]},
}

# 原型已实现编译的约束原语；其余登记在册但报"原型未实现"
COMPILED_CONSTRAINTS: frozenset[str] = frozenset({
    "must", "exclude", "precedence", "co_occur", "fix",
    "cardinality", "deadline", "earliest", "forbidden_dates", "budget", "linear",
})

# co_occur 模式：同/不同 × 日/窗口/线；原型实现 day 两种，其余登记在册
CO_OCCUR_MODES: tuple[str, ...] = (
    "same_day", "diff_day", "same_window", "diff_window", "same_line", "diff_line",
)
COMPILED_CO_OCCUR_MODES: frozenset[str] = frozenset({"same_day", "diff_day"})

# ── 目标原语（本质 2 个 + 加权糖）───────────────────────────────────────────

OBJECTIVE_PRIMITIVES: dict[str, dict] = {
    "minimize": {"required": ["q"]},
    "maximize": {"required": ["q"]},
    "lexmin":   {"required": ["stages"]},   # stages: [{"q":..., "dir":"minimize|maximize"}, ...]
    "weighted": {"required": ["weights"]},  # weights: {具名量: 权重}
}

# 原型已实现编译的目标原语：minimize/maximize/lexmin（q 限于已实现形式的量）
COMPILED_OBJECTIVES: frozenset[str] = frozenset({"minimize", "maximize", "lexmin"})
