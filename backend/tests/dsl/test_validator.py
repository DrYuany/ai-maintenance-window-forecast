"""校验器单测（纯 Python，不依赖 OR-Tools/求解）。"""
from __future__ import annotations

from app.core.dsl.validator import validate_dsl


def test_valid_co_occur_dsl_passes_structure_check() -> None:
    dsl = {
        "constraints": [
            {"type": "co_occur", "mode": "diff_day", "tasks": ["任务018", "任务022"]},
            {"type": "precedence", "before": "任务018", "after": "任务022", "gap": 120},
        ],
        "objectives": [
            {"type": "lexmin", "stages": [
                {"q": "高严重完工时间和", "dir": "minimize"},
                {"q": {"form": "count", "filter": {"severity": "中"}}, "dir": "maximize"},
            ]},
        ],
    }
    assert validate_dsl(dsl) == []


def test_unknown_primitive_rejected() -> None:
    errors = validate_dsl({"constraints": [{"type": "magic_wand", "task": "任务018"}]})
    assert any("未知约束原语" in e for e in errors)


def test_missing_required_field_rejected() -> None:
    errors = validate_dsl({"constraints": [{"type": "precedence", "before": "任务018"}]})
    assert any("缺少必需字段 after" in e for e in errors)


def test_unknown_field_rejected() -> None:
    errors = validate_dsl({"constraints": [{"type": "must", "task": "任务018", "color": "red"}]})
    assert any("不允许字段" in e for e in errors)


def test_illegal_operator_rejected() -> None:
    errors = validate_dsl({"constraints": [{"type": "budget", "quantity": "完工时间和", "op": "≈", "value": 100}]})
    assert any("非法运算符" in e for e in errors)


def test_non_finite_bound_rejected() -> None:
    errors = validate_dsl({"constraints": [{"type": "cardinality", "filter": {}, "op": ">=", "value": "十"}]})
    assert any("有限数值" in e for e in errors)


def test_illegal_co_occur_mode_rejected() -> None:
    errors = validate_dsl({"constraints": [{"type": "co_occur", "mode": "same_universe", "tasks": ["任务001", "任务002"]}]})
    assert any("非法 co_occur 模式" in e for e in errors)


def test_co_occur_window_mode_registered_but_uncompiled() -> None:
    errors = validate_dsl({"constraints": [{"type": "co_occur", "mode": "same_window", "tasks": ["任务001", "任务002"]}]})
    assert any("原型未实现" in e for e in errors)


def test_unregistered_quantity_rejected() -> None:
    errors = validate_dsl({"objectives": [{"type": "minimize", "q": "碳排放"}]})
    assert any("未注册" in e for e in errors)


def test_metrics_form_quantities_now_valid() -> None:
    """最后完工时间/总经济成本/现金支出经 metrics 形式可引用（编译器物化）。"""
    for name in ("最后完工时间", "总经济成本", "现金支出"):
        errors = validate_dsl({"objectives": [{"type": "minimize", "q": name}]})
        assert errors == [], f"{name} 应可引用，错误: {errors}"


def test_raw_triple_with_illegal_filter_rejected() -> None:
    errors = validate_dsl({"constraints": [{
        "type": "cardinality", "filter": {"color": "红"}, "op": ">=", "value": 1,
    }]})
    assert any("未知过滤维度" in e for e in errors)


def test_raw_triple_with_illegal_coeff_rejected() -> None:
    errors = validate_dsl({"objectives": [{
        "type": "minimize", "q": {"form": "weighted_sum", "coeff": "magic", "filter": {}},
    }]})
    assert any("未知系数" in e for e in errors)


def test_linear_with_non_constant_coef_rejected() -> None:
    errors = validate_dsl({"constraints": [{
        "type": "linear", "op": "<=", "value": 100,
        "terms": [["x", "完成任务数"]],
    }]})
    assert any("系数必须有限常数" in e for e in errors)


def test_negative_weight_rejected() -> None:
    errors = validate_dsl({"objectives": [{"type": "weighted", "weights": {"完成任务数": -1.0}}]})
    assert any("权重" in e for e in errors)


def test_unknown_top_level_field_rejected() -> None:
    errors = validate_dsl({"wishes": [{"type": "must", "task": "任务001"}]})
    assert any("未知字段" in e for e in errors)


def test_tasks_list_needs_at_least_two() -> None:
    errors = validate_dsl({"constraints": [{"type": "co_occur", "mode": "diff_day", "tasks": ["任务001"]}]})
    assert any("至少两个" in e for e in errors)


# ── 第二期：parameters/data 段校验 ───────────────────────────────────────────

def test_parameter_section_validates_registry() -> None:
    errors = validate_dsl({"parameters": [{"field": "船舶日租金", "op": "*", "value": 1.2}]})
    assert errors == []


def test_parameter_unknown_field_rejected() -> None:
    errors = validate_dsl({"parameters": [{"field": "不存在的参数", "op": "=", "value": 1}]})
    assert any("不在 122 项" in e for e in errors)


def test_parameter_unchangeable_field_rejected() -> None:
    errors = validate_dsl({"parameters": [{"field": "人工审批", "op": "=", "value": "否"}]})
    assert any("不可改" in e for e in errors)


def test_parameter_bad_op_rejected() -> None:
    errors = validate_dsl({"parameters": [{"field": "船舶日租金", "op": "+", "value": 1}]})
    assert any("op 必须是" in e for e in errors)


def test_data_weather_requires_day() -> None:
    errors = validate_dsl({"data": [{"kind": "weather", "wind": 15}]})
    assert any("缺少必需字段 day" in e for e in errors)


def test_data_unknown_kind_rejected() -> None:
    errors = validate_dsl({"data": [{"kind": "magic_patch"}]})
    assert any("未知数据补丁类型" in e for e in errors)
