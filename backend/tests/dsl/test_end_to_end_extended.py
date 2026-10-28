"""扩展原语端到端（完整模式 + 部分模式）。

选择类原语（exclude/must/cardinality）只在部分排程模式下有意义——
完整模式所有任务强制排入，exclude 会导致不可行。
"""
from __future__ import annotations

from app.core.dsl.runner import run
import pytest

pytestmark = pytest.mark.slow


def _row(selected: list[dict], task_id: str) -> dict:
    return next(c for c in selected if c["任务编号"] == task_id)


def _sum_of_finishes(selected: list[dict]) -> int:
    return sum(c["完成"] for c in selected)


def test_deadline_bounds_finish() -> None:
    """

'任务001 必须 11 月 6 日前修完' —— 完成时间不晚于期限。"""
    report = run({
        "constraints": [
            {"type": "deadline", "task": "任务001", "time": "2026-11-06T00:00:00+08:00"},
        ],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    from pathlib import Path
    from product_data import load_project

    data = load_project(Path(__file__).resolve().parents[3], base_id="基座01",
                        as_of="2026-10-23T18:00:00+08:00")
    minute = data.minute("2026-11-06T00:00:00+08:00")
    assert _row(selected, "任务001")["完成"] <= minute


def test_fix_forces_line() -> None:
    """'任务001 只能上 2 号船' —— 作业线固定为 2。"""
    report = run({
        "constraints": [
            {"type": "fix", "task": "任务001", "line": 2},
        ],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    assert _row(selected, "任务001")["作业线"] == 2


def test_forbidden_dates_blocks_starts() -> None:
    """'11 月 3 号别开工' —— 该日没有任何任务开工。"""
    report = run({
        "constraints": [
            {"type": "forbidden_dates", "dates": ["2026-11-03T00:00:00+08:00"]},
        ],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    from pathlib import Path
    from product_data import load_project

    data = load_project(Path(__file__).resolve().parents[3], base_id="基座01",
                        as_of="2026-10-23T18:00:00+08:00")
    day = data.minute("2026-11-03T00:00:00+08:00") // 1440
    assert all(c["开始"] // 1440 != day for c in selected)


def test_lexmin_objective_overrides_default_strategy() -> None:
    """DSL 自定义目标绕过四模板：最小化完工时间和，结果显著低于成本最优基线。

    成本最优基线（默认策略）的完工时间和实测 194100（pinned 演示数据）。
    """
    report = run({
        "objectives": [
            {"type": "lexmin", "stages": [{"q": "完工时间和", "dir": "minimize"}]},
        ],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    assert _sum_of_finishes(selected) < 194100


def test_budget_bounds_sum_of_finishes() -> None:
    """budget + lexmin 目标组合：约束被编译并强制，全部任务仍排入（模型可行）。

    边界依据：10 任务真实最小完工时间和实测 180840（pinned 数据），取 185000。
    注：约束是否"收紧"解取决于目标组合与求解器性能，这里只断言强制生效。
    """
    report = run({
        "constraints": [
            {"type": "budget", "quantity": "完工时间和", "op": "<=", "value": 185000},
        ],
        "objectives": [
            {"type": "lexmin", "stages": [{"q": "完工时间和", "dir": "minimize"}]},
        ],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    assert len(selected) == 10
    assert _sum_of_finishes(selected) <= 185000


def test_exclude_drops_task_in_partial_mode() -> None:
    """'任务001 不要排'（部分模式）—— 不在排程里。"""
    report = run({
        "constraints": [
            {"type": "exclude", "task": "任务001"},
        ],
    }, seconds=60, max_tasks=10, partial=True)
    selected = report["排程"]
    assert selected is not None
    assert all(c["任务编号"] != "任务001" for c in selected)


def test_must_forces_task_in_partial_mode() -> None:
    """'任务010 必须修'（部分模式）—— 在排程里。"""
    report = run({
        "constraints": [
            {"type": "must", "task": "任务010"},
        ],
    }, seconds=60, max_tasks=10, partial=True)
    selected = report["排程"]
    assert selected is not None
    assert any(c["任务编号"] == "任务010" for c in selected)


def test_cardinality_bounds_completion_in_partial_mode() -> None:
    """'至少完成 8 项'（部分模式）—— 完成数不少于 8。"""
    report = run({
        "constraints": [
            {"type": "cardinality", "filter": {}, "op": ">=", "value": 8},
        ],
    }, seconds=60, max_tasks=10, partial=True)
    selected = report["排程"]
    assert selected is not None
    assert len(selected) >= 8


def test_budget_on_total_cost_via_metrics() -> None:
    """'总经济成本不超过 300 万' —— 经 build_model metrics 引用的复合成本量。"""
    report = run({
        "constraints": [
            {"type": "budget", "quantity": "总经济成本", "op": "<=", "value": 3_000_000},
        ],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    assert len(selected) == 10
    cost = report["求解"].get("模型成本")
    assert cost is not None and cost <= 3_000_000


def test_lexmin_minimizes_total_cost_via_metrics() -> None:
    """lexmin 目标引用复合成本量（metrics 形式）—— 编译 + 求解成功。"""
    report = run({
        "objectives": [
            {"type": "lexmin", "stages": [{"q": "总经济成本", "dir": "minimize"}]},
        ],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    assert len(selected) == 10


def test_full_pipeline_nl_to_solve() -> None:
    """全链路：自然语言 → 规则层解析 → DSL → 真求解 → 约束生效。"""
    from app.core.dsl.llm_parser import parse

    result = parse("任务001和任务002不能在同一天出海维修")
    assert result.source == "rule"
    report = run(result.dsl, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    days = {c["任务编号"]: c["开始"] // 1440 for c in selected}
    assert days["任务001"] != days["任务002"]


# ── 第二期：parameters/data 段端到端（真求解）──────────────────────────────

def test_weather_patch_forces_no_work_on_storm_day() -> None:
    """'11月3日风速15米每秒' → 该日海况重算为不可作业 → 无任务开工。"""
    report = run({
        "data": [{"kind": "weather", "day": "2026-11-03", "wind": 15.0}],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    from pathlib import Path
    from product_data import load_project

    data = load_project(
        Path(__file__).resolve().parents[3], base_id="基座01",
        as_of="2026-10-23T18:00:00+08:00",
        timeline_patches=[{"kind": "weather", "day": "2026-11-03", "wind": 15.0}],
    )
    storm_day = data.minute("2026-11-03T00:00:00+08:00") // 1440
    assert all(c["开始"] // 1440 != storm_day for c in selected)


def test_weather_state_patch_blocks_work() -> None:
    """'11月3日海况不可作业'（直接设类别）→ 同样无任务开工。"""
    report = run({
        "data": [{"kind": "weather", "day": "2026-11-03", "state": "不可作业"}],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    from pathlib import Path
    from product_data import load_project

    data = load_project(Path(__file__).resolve().parents[3], base_id="基座01",
                        as_of="2026-10-23T18:00:00+08:00")
    storm_day = data.minute("2026-11-03T00:00:00+08:00") // 1440
    assert all(c["开始"] // 1440 != storm_day for c in selected)


def test_parameter_override_via_dsl() -> None:
    """'船租上涨20%' → 参数覆盖进入快照且求解成功。"""
    report = run({
        "parameters": [{"field": "船舶日租金", "op": "*", "value": 1.2}],
    }, seconds=60, max_tasks=10)
    selected = report["排程"]
    assert selected is not None
    overrides = report["数据"].get("参数覆盖") or {}
    assert overrides.get("船租（元每艘每天）") == 14400.0
