from __future__ import annotations

from app.core.conversation_agent import MATH_PASSTHROUGH
from app.core.agent_tools import AgentToolbox


def test_math_passthrough_triggers_on_formula_requests() -> None:
    assert MATH_PASSTHROUGH.search("给我方案01的目标函数公式")
    assert MATH_PASSTHROUGH.search("用 LaTeX 写出约束")
    assert MATH_PASSTHROUGH.search("把数学模型贴出来")


def test_math_passthrough_ignores_cost_questions() -> None:
    assert not MATH_PASSTHROUGH.search("你直接告诉我目标函数考虑了哪些成本")
    assert not MATH_PASSTHROUGH.search("为什么这版成本更低")


def test_tool_surface_is_five_readers_plus_compare_plus_solve_and_ledger() -> None:
    toolbox = object.__new__(AgentToolbox)
    specs = toolbox.tool_specs()

    assert [spec["name"] for spec in specs] == [
        "read_versions",
        "read_environment",
        "read_catalog",
        "read_alarms",
        "read_site",
        "solve",
        "ledger",
        "compare_versions",
    ]
    # 读类工具必须自报"只读"，写类工具（solve 预览、ledger 台账）不能这么标。
    for spec in specs:
        if spec["name"].startswith("read_") or spec["name"] == "compare_versions":
            assert "只读" in spec["description"]


def test_read_schema_accepts_multiple_versions() -> None:
    toolbox = object.__new__(AgentToolbox)
    specs = {spec["name"]: spec for spec in toolbox.tool_specs()}
    versions = specs["read_versions"]["input_schema"]["properties"]["versions"]

    assert versions["type"] == "array"
    assert "include" in specs["read_versions"]["input_schema"]["properties"]
    assert "baseline" in specs["read_versions"]["input_schema"]["properties"]
