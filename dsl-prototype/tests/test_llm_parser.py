"""三层解析器单测：规则层 + 向量层 + LLM 层（mock，无外部调用）。"""
from __future__ import annotations

import json

import pytest

from dsl_prototype import llm_parser
from dsl_prototype.llm_parser import (
    ParseUnavailable,
    load_library,
    parse,
    parse_by_rules,
    parse_with_llm,
    retrieve,
)


# ── 规则层 ────────────────────────────────────────────────────────────────────

def test_rule_co_occur_diff_day() -> None:
    dsl = parse_by_rules("任务001和任务002不能在同一天出海维修")
    assert dsl == {"constraints": [{"type": "co_occur", "mode": "diff_day", "tasks": ["任务001", "任务002"]}]}


def test_rule_precedence() -> None:
    dsl = parse_by_rules("先修任务001再修任务022")
    assert dsl["constraints"][0]["type"] == "precedence"
    assert dsl["constraints"][0]["before"] == "任务001"
    assert dsl["constraints"][0]["after"] == "任务022"


def test_rule_t_normalizes_task_ref() -> None:
    dsl = parse_by_rules("T01和T02不能同时修")
    assert dsl["constraints"][0]["tasks"] == ["任务001", "任务002"]


def test_rule_earliest_with_date() -> None:
    dsl = parse_by_rules("任务018最早11月5日开工")
    assert dsl["constraints"][0] == {"type": "earliest", "task": "任务018", "time": "2026-11-05T08:00:00+08:00"}


def test_rule_fix_line() -> None:
    dsl = parse_by_rules("任务018只能上2号船")
    assert dsl["constraints"][0] == {"type": "fix", "task": "任务018", "line": 2}


def test_rule_forbidden_dates() -> None:
    dsl = parse_by_rules("11月3号不要开工")
    assert dsl["constraints"][0] == {
        "type": "forbidden_dates", "dates": ["2026-11-03T08:00:00+08:00"],
    }


def test_rule_unmatched_returns_none() -> None:
    assert parse_by_rules("今天天气怎么样") is None


# ── 向量层 ────────────────────────────────────────────────────────────────────

def test_retrieve_ranks_similar_phrasing_first() -> None:
    library = load_library()
    top = retrieve("任务003和任务004不能同一天出海", library, top_k=1)
    assert top[0]["id"] == "tpl-001"


def test_retrieve_similarity_is_bounded() -> None:
    top = retrieve("任务001和任务002不能在同一天出海维修", load_library(), top_k=1)
    assert 0.0 < top[0]["similarity"] <= 1.0


# ── LLM 层（mock，不调真实 API）──────────────────────────────────────────────

class _FakeBlock:
    type = "text"
    text: str


class _FakeMessages:
    def __init__(self, dsl: dict) -> None:
        block = _FakeBlock()
        block.text = f"```json\n{json.dumps(dsl)}\n```"
        self.content = [block]


class _FakeClient:
    """模仿 anthropic 客户端：client.messages.create(...) 返回带 .content 的响应。"""

    def __init__(self, dsl: dict) -> None:
        self._dsl = dsl
        self.last_request = None
        self.messages = self

    def create(self, **kwargs):
        self.last_request = kwargs
        return _FakeMessages(self._dsl)


def test_llm_layer_sends_few_shot_and_validates() -> None:
    expected = {"constraints": [{"type": "co_occur", "mode": "diff_day", "tasks": ["任务003", "任务004"]}]}
    client = _FakeClient(expected)
    templates = retrieve("任务003和任务004不能同一天出海", load_library(), top_k=3)

    result = parse_with_llm("任务003和任务004不能同一天出海", templates, client=client)

    assert result.source == "llm"
    assert result.dsl == expected
    # few-shot 例子必须进了 prompt
    prompt = client.last_request["messages"][0]["content"]
    assert "tpl-001" in "".join(str(entry["id"]) for entry in templates) or "不能在同一天" in prompt


def test_llm_layer_rejects_invalid_output() -> None:
    client = _FakeClient({"constraints": [{"type": "magic_wand", "task": "任务001"}]})
    with pytest.raises(ValueError, match="未通过校验"):
        parse_with_llm("任意话", [], client=client)


def test_llm_layer_unavailable_without_key(monkeypatch) -> None:
    import os

    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    os.environ.pop("MINIMAX_API_KEY", None)
    with pytest.raises(ParseUnavailable):
        parse_with_llm("任意话", [])


# ── 三层入口 ──────────────────────────────────────────────────────────────────

def test_parse_prefers_rule_layer(monkeypatch) -> None:
    result = parse("任务001和任务002不能在同一天出海维修")
    assert result.source == "rule"
    assert result.confidence == 1.0
