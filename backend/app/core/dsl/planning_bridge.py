"""旧解析语言 ↔ DSL 桥接（第三期：收拢解析管道）。

设计原则（见设计文档 §11.2-11.3）：
- 保留 planning_intent 的正则模式（它们是最成熟的规则层实现），不重写；
- 统一成 DSL schema：strategy/mode/weights → objectives；overrides → parameters；
  limits/必修 → constraints；data_patches → data；seconds → solver；
- PlanningRequest 不消失，它变成 DSL 编译后的产物。

两个方向：
  request_to_dsl(request)            旧编译器产物 → DSL（规则层输出统一化）
  dsl_to_request(dsl, base)          DSL → PlanningRequest（求解器输入）
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.core.planning_intent import PlanningRequest

from .registry.parameters import PARAM_NAME_TO_KEY

# 模型内部键 → 122 项注册表参数名称（PARAM_NAME_TO_KEY 的反向）
_KEY_TO_PARAM_NAME: dict[str, str] = {value: key for key, value in PARAM_NAME_TO_KEY.items()}


def to_registry_name(internal_key: str) -> str:
    return _KEY_TO_PARAM_NAME.get(internal_key, internal_key)


# limits 键 → DSL 约束规格
def _limits_to_constraints(limits: dict[str, Any]) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    if "最大总成本" in limits:
        constraints.append({"type": "budget", "quantity": "总经济成本", "op": "<=",
                            "value": float(limits["最大总成本"])})
    if "现金预算" in limits:
        constraints.append({"type": "budget", "quantity": "现金支出", "op": "<=",
                            "value": float(limits["现金预算"])})
    if "最低完成数" in limits:
        constraints.append({"type": "cardinality", "filter": {}, "op": ">=",
                            "value": int(limits["最低完成数"])})
    for tid in limits.get("必修任务", []) or []:
        constraints.append({"type": "must", "task": str(tid)})
    return constraints


def request_to_dsl(request: PlanningRequest) -> dict[str, Any]:
    """旧编译器产物 → 统一 DSL。"""
    dsl: dict[str, Any] = {}
    if request.mode == "加权" and request.weights:
        dsl["objectives"] = [{"type": "weighted", "weights": dict(request.weights)}]
    else:
        dsl["objectives"] = [{"type": "strategy", "name": request.strategy, "mode": request.mode}]

    parameters: list[dict[str, Any]] = []
    for key, value in sorted(request.overrides.items()):
        parameters.append({"field": to_registry_name(key), "op": "=", "value": value})
    for key, factor in sorted(request.relative_overrides.items()):
        parameters.append({"field": to_registry_name(key), "op": "*", "value": factor})
    if parameters:
        dsl["parameters"] = parameters

    constraints = _limits_to_constraints(request.limits or {})
    if constraints:
        dsl["constraints"] = constraints

    if request.data_patches:
        dsl["data"] = [dict(patch) for patch in request.data_patches]

    dsl["solver"] = {"seconds": request.seconds}
    return dsl


def dsl_to_request(
    dsl: dict[str, Any],
    base: PlanningRequest | None = None,
) -> tuple[PlanningRequest, list[dict[str, Any]], list[dict[str, Any]]]:
    """DSL → (PlanningRequest, 剩余约束, 时序补丁)。

    能映射进 PlanningRequest 的（参数/策略/权重/预算/必修/数据补丁/时限）
    直接映射；映射不了的（co_occur/precedence 等自定义约束、lexmin 目标）
    作为"剩余约束"交给编译器的注入闭包。
    """
    request = replace(base) if base else PlanningRequest()
    residual: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []

    objectives = dsl.get("objectives") or []
    custom_objectives: list[dict[str, Any]] = []
    for spec in objectives:
        otype = spec.get("type")
        if otype == "strategy":
            request = replace(
                request,
                strategy=str(spec.get("name") or request.strategy),
                mode=str(spec.get("mode") or request.mode),
            )
        elif otype == "weighted":
            request = replace(
                request,
                mode="加权",
                weights={str(k): float(v) for k, v in (spec.get("weights") or {}).items()},
            )
        else:
            custom_objectives.append(spec)

    overrides = dict(request.overrides)
    relative = dict(request.relative_overrides)
    for spec in dsl.get("parameters") or []:
        field = to_registry_name(spec["field"])  # 接受注册表名或内部键
        field = PARAM_NAME_TO_KEY.get(field, field)
        if spec.get("op") == "*":
            relative[field] = float(spec["value"])
        else:
            overrides[field] = spec["value"]
    request = replace(request, overrides=overrides, relative_overrides=relative)

    limits = dict(request.limits or {})
    data_patches = list(request.data_patches)
    for spec in dsl.get("constraints") or []:
        ctype = spec.get("type")
        if ctype == "budget" and spec.get("quantity") == "总经济成本" and spec.get("op") == "<=":
            limits["最大总成本"] = float(spec["value"])
        elif ctype == "budget" and spec.get("quantity") == "现金支出" and spec.get("op") == "<=":
            limits["现金预算"] = float(spec["value"])
        elif (ctype == "cardinality" and spec.get("op") == ">="
              and not spec.get("filter")):
            limits["最低完成数"] = int(spec["value"])
        elif ctype == "must":
            limits["必修任务"] = sorted({*limits.get("必修任务", []), str(spec["task"])})
        else:
            residual.append(spec)
    for spec in dsl.get("data") or []:
        if spec.get("kind") in ("part_hours", "part_arrival", "task_deadline"):
            data_patches.append(dict(spec))
        else:
            timeline.append(dict(spec))
    request = replace(request, limits=limits, data_patches=data_patches)

    solver = dsl.get("solver") or {}
    if "seconds" in solver:
        request = replace(request, seconds=int(solver["seconds"]))
    return request, residual, timeline


def custom_objectives(dsl: dict[str, Any]) -> list[dict[str, Any]]:
    """返回 DSL 里映射不进 PlanningRequest 的目标（lexmin/minimize 等）。"""
    return [
        spec for spec in (dsl.get("objectives") or [])
        if spec.get("type") not in ("strategy", "weighted")
    ]
