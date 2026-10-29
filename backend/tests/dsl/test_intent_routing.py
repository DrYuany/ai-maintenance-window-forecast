"""意图路由的现状与已知缺口。

这一组测试不是为了夸系统，而是把"规则层在前面、大模型在后面"这件事钉住，
并把已知的漏词场景标成 xfail——修好了它会自己变红（strict），提醒删掉标记。

实际链路（app/core/dsl/llm_parser.py::_parse_core）：
    1 批量句正则
    2 parse_by_rules（规则）
    3 _classify_not_change_only（关门）
    4 parse_by_legacy（旧规划语言正则编译）   ← 命中即返回，置信度 1.0
    5 _classify_non_change（关门）
    6 向量模板命中
    7 大模型

上面 agent_runtime 还有一道 is_planning_request 会把整句认领走，直接进旧编译器，
连 1~7 都不走。所以"大模型为主、规则兜底"是设计意图，不是当前实现。
"""
from __future__ import annotations

import json

import pytest

from app.core.dsl.llm_parser import parse_by_legacy
from app.core.planning_intent import PlanningIntentCompiler


def test_rule_gate_claims_strategy_sentences() -> None:
    """把现状写清楚：含策略词的句子由规则层认领，大模型不参与。"""
    compiler = PlanningIntentCompiler()
    assert compiler.is_planning_request("按成本最低优先排每天只能干8小时")


def test_pure_strategy_sentence_is_fully_covered_by_rules() -> None:
    """只有策略诉求时，规则层的结果是完整的——这种句子不需要大模型。"""
    dsl = parse_by_legacy("按成本最低优先排")
    assert dsl is not None
    assert dsl["objectives"][0]["name"] == "成本最低优先"
    assert "parameters" not in dsl and "constraints" not in dsl


@pytest.mark.xfail(strict=True, reason=(
    "已知缺口：策略词命中后规则层直接返回，同一句里的其它改动被丢掉，"
    "而大模型没机会看。修法：规则层只认领'完全消费'的句子，"
    "有未消费片段时交出控制权并把已解析部分合并进去。"
))
def test_strategy_plus_other_change_keeps_both() -> None:
    dsl = parse_by_legacy("按成本最低优先排每天只能干8小时")
    assert dsl is not None
    assert "parameters" in dsl, f"工时改动被丢掉了，实际只解析出 {json.dumps(dsl, ensure_ascii=False)}"
    assert any(p["field"] == "每日有效维修上限" for p in dsl["parameters"])
