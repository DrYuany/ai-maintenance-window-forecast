"""确定性校验器：DSL JSON → 错误列表。

"受控"的命门。只做确定性检查，不调用 LLM：
  1. 结构合法（constraints/objectives 列表）
  2. 原语在注册表、required 字段齐全
  3. 实体引用可解析（传入 data 时）
  4. 可引用量合法（形式/系数/过滤都在注册表；max/derived 原型明确报错）
  5. 线性（系数必须有限常数）、op 合法、界合法
"""
from __future__ import annotations

import math
from typing import Any

from .registry import entities as entity_reg
from .registry import primitives as prim_reg
from .registry import quantities as qty_reg

# data 段各补丁类型的必需字段（第二期）
_DATA_PATCH_REQUIRED: dict[str, tuple[str, ...]] = {
    "part_hours": ("part", "factor"),
    "part_arrival": ("part", "at"),
    "task_deadline": ("at",),
    "weather": ("day",),
    "hourly": ("time",),
    "window": ("window",),
}


def validate_dsl(dsl: dict[str, Any], data=None) -> list[str]:
    errors: list[str] = []
    if not isinstance(dsl, dict):
        return ["DSL 必须是 JSON 对象"]
    for key, item in dsl.items():
        if key not in ("constraints", "objectives", "parameters", "data"):
            errors.append(f"未知字段: {key}")
    for index, spec in enumerate(dsl.get("constraints") or []):
        errors.extend(_validate_constraint(spec, data, f"constraints[{index}]"))
    for index, spec in enumerate(dsl.get("objectives") or []):
        errors.extend(_validate_objective(spec, data, f"objectives[{index}]"))
    for index, spec in enumerate(dsl.get("parameters") or []):
        errors.extend(_validate_parameter(spec, f"parameters[{index}]"))
    for index, spec in enumerate(dsl.get("data") or []):
        errors.extend(_validate_data_patch(spec, data, f"data[{index}]"))
    return errors


def _validate_parameter(spec: Any, where: str) -> list[str]:
    """parameters 段：全局参数覆盖，字段必须在 122 项参数注册表且可改。"""
    from .registry import parameters as param_reg

    errors: list[str] = []
    if not isinstance(spec, dict):
        return [f"{where}: 参数必须是 JSON 对象"]
    field = spec.get("field")
    if not isinstance(field, str) or param_reg.lookup(field) is None:
        errors.append(f"{where}: 参数 {field!r} 不在 122 项参数注册表")
        return errors
    if not param_reg.is_changeable(field):
        errors.append(f"{where}: 参数 {field!r} 登记为待扩展/固定，不可改")
    op = spec.get("op")
    if op not in ("=", "*"):
        errors.append(f"{where}: op 必须是 '='（绝对）或 '*'（相对倍率）")
    if "value" not in spec:
        errors.append(f"{where}: 缺少 value")
    else:
        errors.extend(_check_bound(spec["value"], where, "value", minimum=0))
    return errors


def _validate_data_patch(spec: Any, data, where: str) -> list[str]:
    """data 段：行级数据补丁 + 时序假设修改（第二期）。"""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return [f"{where}: 数据补丁必须是 JSON 对象"]
    kind = spec.get("kind")
    if kind not in _DATA_PATCH_REQUIRED:
        return [f"{where}: 未知数据补丁类型 {kind!r}，允许 {tuple(_DATA_PATCH_REQUIRED)}"]
    for field_name in _DATA_PATCH_REQUIRED[kind]:
        if field_name not in spec:
            errors.append(f"{where}: 补丁 {kind} 缺少必需字段 {field_name}")
    if kind == "part_hours":
        if "factor" in spec:
            errors.extend(_check_bound(spec["factor"], where, "factor", minimum=0))
    if kind == "part_arrival":
        if "quantity" in spec:
            errors.extend(_check_bound(spec["quantity"], where, "quantity", integer=True, minimum=1))
        if "at" in spec and data is not None:
            from .registry.entities import resolve_minute

            if resolve_minute(data, spec["at"]) is None:
                errors.append(f"{where}: 到货时间 {spec['at']!r} 不在规划范围内")
    if kind == "task_deadline":
        if "at" in spec and data is not None:
            from .registry.entities import resolve_minute

            if resolve_minute(data, spec["at"]) is None:
                errors.append(f"{where}: 期限 {spec['at']!r} 不在规划范围内")
    if kind == "weather":
        if not any(key in spec for key in ("wind", "wave", "hours", "state")):
            errors.append(f"{where}: weather 补丁至少要给 wind/wave/hours/state 之一")
        if "state" in spec and spec["state"] not in ("常态", "恶劣但安全", "不可作业"):
            errors.append(f"{where}: 海况类别 {spec['state']!r} 不合法")
    if kind == "hourly":
        if not any(key in spec for key in ("price", "wind", "power")):
            errors.append(f"{where}: hourly 补丁至少要给 price/wind/power 之一")
    if kind == "window":
        if not any(key in spec for key in ("start", "end")):
            errors.append(f"{where}: window 补丁至少要给 start/end 之一")
    return errors


def _validate_constraint(spec: Any, data, where: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return [f"{where}: 约束必须是 JSON 对象"]
    ctype = spec.get("type")
    if ctype not in prim_reg.CONSTRAINT_PRIMITIVES:
        return [f"{where}: 未知约束原语 {ctype!r}，注册表共 {len(prim_reg.CONSTRAINT_PRIMITIVES)} 种"]
    schema = prim_reg.CONSTRAINT_PRIMITIVES[ctype]
    for field in schema["required"]:
        if field not in spec:
            errors.append(f"{where}: 原语 {ctype} 缺少必需字段 {field}")
    allowed = set(schema["required"]) | set(schema.get("optional", [])) | {"type"}
    for key in spec:
        if key not in allowed:
            errors.append(f"{where}: 原语 {ctype} 不允许字段 {key!r}")
    if ctype not in prim_reg.COMPILED_CONSTRAINTS:
        errors.append(f"{where}: 原语 {ctype} 已登记但原型未实现编译")

    if "op" in spec:
        if spec["op"] not in prim_reg.OPS:
            errors.append(f"{where}: 非法运算符 {spec['op']!r}，允许 {prim_reg.OPS}")
    if "value" in spec:
        errors.extend(_check_bound(spec["value"], where, "value"))
    if "gap" in spec and spec["gap"] is not None:
        errors.extend(_check_bound(spec["gap"], where, "gap", minimum=0))
    if "line" in spec:
        errors.extend(_check_bound(spec["line"], where, "line", integer=True, minimum=1))
    if "limit" in spec:
        errors.extend(_check_bound(spec["limit"], where, "limit", minimum=0))

    if ctype == "co_occur":
        mode = spec.get("mode")
        if mode not in prim_reg.CO_OCCUR_MODES:
            errors.append(f"{where}: 非法 co_occur 模式 {mode!r}，允许 {prim_reg.CO_OCCUR_MODES}")
        elif mode not in prim_reg.COMPILED_CO_OCCUR_MODES:
            errors.append(f"{where}: co_occur 模式 {mode!r} 已登记但原型未实现")
        errors.extend(_check_task_list(spec.get("tasks"), data, where))
    if ctype in ("must", "exclude", "deadline", "earliest", "fix"):
        errors.extend(_check_task_ref(spec.get("task"), data, where))
    if ctype == "precedence":
        errors.extend(_check_task_ref(spec.get("before"), data, where))
        errors.extend(_check_task_ref(spec.get("after"), data, where))
    if ctype in ("at_most_one", "at_least_one"):
        errors.extend(_check_task_list(spec.get("tasks"), data, where))
    if ctype == "forbidden_dates":
        if not isinstance(spec.get("dates"), list) or not spec["dates"]:
            errors.append(f"{where}: forbidden_dates 需要非空日期列表")
        elif data is not None:
            for date in spec["dates"]:
                if entity_reg.resolve_minute(data, date) is None:
                    errors.append(f"{where}: 日期 {date!r} 不在规划范围内")
    if ctype == "cardinality":
        errors.extend(_check_filter(spec.get("filter"), where))
    if ctype == "budget":
        errors.extend(_check_quantity(spec.get("quantity"), where))
    if ctype == "linear":
        errors.extend(_check_linear_terms(spec.get("terms"), where))
    return errors


def _validate_objective(spec: Any, data, where: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(spec, dict):
        return [f"{where}: 目标必须是 JSON 对象"]
    otype = spec.get("type")
    if otype not in prim_reg.OBJECTIVE_PRIMITIVES:
        return [f"{where}: 未知目标原语 {otype!r}"]
    schema = prim_reg.OBJECTIVE_PRIMITIVES[otype]
    for field in schema["required"]:
        if field not in spec:
            errors.append(f"{where}: 目标 {otype} 缺少必需字段 {field}")
    if otype not in prim_reg.COMPILED_OBJECTIVES:
        errors.append(f"{where}: 目标 {otype} 已登记但原型未实现")
    if otype in ("minimize", "maximize"):
        errors.extend(_check_quantity(spec.get("q"), where))
    if otype == "lexmin":
        stages = spec.get("stages")
        if not isinstance(stages, list) or not stages:
            errors.append(f"{where}: lexmin 的 stages 需要非空列表")
        else:
            for i, stage in enumerate(stages):
                if not isinstance(stage, dict) or "q" not in stage:
                    errors.append(f"{where}.stages[{i}]: 缺少 q")
                    continue
                errors.extend(_check_quantity(stage["q"], f"{where}.stages[{i}]"))
                if stage.get("dir") not in ("minimize", "maximize"):
                    errors.append(f"{where}.stages[{i}]: dir 必须是 minimize/maximize")
    if otype == "weighted":
        weights = spec.get("weights")
        if not isinstance(weights, dict) or not weights:
            errors.append(f"{where}: weighted 需要非空权重字典")
        else:
            for name, w in weights.items():
                errors.extend(_check_quantity(name, where))
                errors.extend(_check_bound(w, where, f"权重 {name}", minimum=0))
    return errors


# ── 子检查 ───────────────────────────────────────────────────────────────────

def _check_bound(value: Any, where: str, label: str, minimum=None, integer=False) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return [f"{where}: {label} 必须是有限数值，收到 {value!r}"]
    if minimum is not None and value < minimum:
        return [f"{where}: {label} 必须 ≥ {minimum}，收到 {value!r}"]
    if integer and float(value) != int(value):
        return [f"{where}: {label} 必须是整数，收到 {value!r}"]
    return []


def _check_task_ref(ref: Any, data, where: str) -> list[str]:
    if not isinstance(ref, str) or not ref.strip():
        return [f"{where}: 任务引用必须是任务编号字符串"]
    if data is not None and entity_reg.resolve_task(data, ref) is None:
        return [f"{where}: 任务 {ref!r} 不在当前告警快照"]
    return []


def _check_task_list(refs: Any, data, where: str) -> list[str]:
    if not isinstance(refs, list) or len(refs) < 2:
        return [f"{where}: tasks 需要至少两个任务"]
    errors: list[str] = []
    for ref in refs:
        errors.extend(_check_task_ref(ref, data, where))
    return errors


def _check_filter(filter_spec: Any, where: str) -> list[str]:
    if not isinstance(filter_spec, dict):
        return [f"{where}: filter 必须是字典"]
    errors: list[str] = []
    for dim in filter_spec:
        if dim not in qty_reg.FILTERS:
            errors.append(f"{where}: 未知过滤维度 {dim!r}，允许 {qty_reg.FILTERS}")
    return errors


def _check_quantity(ref: Any, where: str) -> list[str]:
    """检查量引用：具名量 / 原始三元组 / 未注册名。"""
    resolved = qty_reg.resolve_quantity(ref)
    if resolved is None:
        return [f"{where}: 量 {ref!r} 未注册（具名量或三元组）"]
    if resolved == "UNIMPLEMENTED":
        return [f"{where}: 量 {ref!r} 依赖 max/derived 形式，原型未实现"]
    if isinstance(resolved, dict):
        errors: list[str] = []
        form = resolved.get("form")
        if form not in qty_reg.FORMS:
            errors.append(f"{where}: 未知数学形式 {form!r}，允许 {qty_reg.FORMS}")
        elif form not in qty_reg.IMPLEMENTED_FORMS:
            errors.append(f"{where}: 形式 {form!r} 已登记但原型未实现")
        coeff = resolved.get("coeff", "one")
        if coeff != "one" and coeff not in qty_reg.COEFFS:
            errors.append(f"{where}: 未知系数 {coeff!r}，允许 {qty_reg.COEFFS}")
        errors.extend(_check_filter(resolved.get("filter", {}), where))
        return errors
    return []


def _check_linear_terms(terms: Any, where: str) -> list[str]:
    """linear 原语的 terms：[(系数, 量引用), ...]，系数必须有限常数。"""
    if not isinstance(terms, list) or not terms:
        return [f"{where}: linear 的 terms 需要非空列表"]
    errors: list[str] = []
    for i, term in enumerate(terms):
        if not isinstance(term, (list, tuple)) or len(term) != 2:
            errors.append(f"{where}.terms[{i}]: 每项必须是 [系数, 量]")
            continue
        coef, ref = term
        if isinstance(coef, bool) or not isinstance(coef, (int, float)) or not math.isfinite(float(coef)):
            errors.append(f"{where}.terms[{i}]: 系数必须有限常数，收到 {coef!r}")
        errors.extend(_check_quantity(ref, f"{where}.terms[{i}]"))
    return errors
