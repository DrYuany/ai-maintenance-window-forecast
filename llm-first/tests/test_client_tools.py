"""工具通道的客户端约定。

这里守的不是"能不能连上模型"（那是 scripts/probe_tools.py 的事），
而是三件容易在重构中悄悄改坏的事：

  1. 传了 tools 就绝不能再带 response_format —— json_object 会压空 content
  2. 工具轮 content 是空的也算成功 —— 否则每次工具调用都被当成失败重试
  3. 工具参数解析失败不许抛 —— 让工具那边按 invalid_arguments 报，模型能自己改

第 2 条最阴：content 为空 → 判失败 → 重试 → 又空 → 最终抛 LLMUnavailable，
而模型其实干得好好的。这种 bug 只在真接上工具那天才炸。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import client  # noqa: E402


TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_capabilities",
        "description": "查能力表",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]},
    },
}]


@pytest.fixture()
def capture(monkeypatch):
    """截住真正的 HTTP，返回一个可控响应，并把发出去的 payload 留下来。"""
    sent: list[dict] = []

    def fake_post(_url, payload, _key, _timeout):
        sent.append(payload)
        return fake_post.response

    fake_post.response = {
        "choices": [{"message": {"content": "好的"}, "finish_reason": "stop"}],
        "model": "fake",
        "usage": {},
    }
    monkeypatch.setattr(client, "_post", fake_post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")
    return sent, fake_post


def _tool_reply(content: str = "", name: str = "read_capabilities",
                arguments: str = '{"query": "船租"}'):
    return {
        "choices": [{
            "message": {
                "content": content,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "model": "fake",
        "usage": {},
    }


def test_tools_and_json_mode_never_go_together(capture):
    """传了 tools 还带 response_format，模型就不说话了。这条把它钉死。"""
    sent, _ = capture
    client.chat_messages([{"role": "user", "content": "船租多少"}],
                         tools=TOOLS, json_mode=True)
    assert "tools" in sent[0]
    assert "response_format" not in sent[0], (
        "tools 与 response_format 同时出现；实测这样会把 content 压成空")


def test_json_mode_still_works_without_tools(capture):
    """老路径不能被新逻辑带坏：不传 tools 时 json_mode 照旧生效。"""
    sent, _ = capture
    client.chat("sys", "user", json_mode=True)
    assert sent[0]["response_format"] == {"type": "json_object"}
    assert "tools" not in sent[0]


def test_plain_chat_has_no_tools_field(capture):
    """`chat()` 是单趟通路，不该凭空长出 tools 字段。"""
    sent, _ = capture
    client.chat("sys", "user")
    assert "tools" not in sent[0]
    assert "tool_choice" not in sent[0]


def test_empty_content_with_tool_calls_counts_as_success(capture):
    """工具轮 content 为空是常态，不是失败。

    改坏了的症状：每次工具调用都白跑一次重试，最后抛 LLMUnavailable，
    而日志里看起来像"模型挂了"。
    """
    sent, fake = capture
    fake.response = _tool_reply(content="")
    reply = client.chat_messages([{"role": "user", "content": "船租多少"}],
                                 tools=TOOLS)
    assert len(sent) == 1, "空 content 被当成失败重试了"
    assert reply.wants_tools
    assert reply.text == ""


def test_empty_everything_still_fails(capture):
    """真·什么都没有才该失败。这是上一条的对照组。"""
    sent, fake = capture
    fake.response = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}
    with pytest.raises(client.LLMUnavailable):
        client.chat_messages([{"role": "user", "content": "x"}], tools=TOOLS)
    assert len(sent) >= 2


def test_tool_choice_is_forwarded_only_with_tools(capture):
    """没工具时传 tool_choice 是无意义的参数，别发出去。"""
    sent, _ = capture
    client.chat_messages([{"role": "user", "content": "x"}], tool_choice="required")
    assert "tool_choice" not in sent[0]

    client.chat_messages([{"role": "user", "content": "x"}],
                         tools=TOOLS, tool_choice="required")
    assert sent[1]["tool_choice"] == "required"


def test_call_list_parses_arguments(capture):
    """正常参数解析成字典。"""
    _sent, fake = capture
    fake.response = _tool_reply()
    reply = client.chat_messages([{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply.call_list() == [("read_capabilities", {"query": "船租"})]


def test_broken_arguments_do_not_raise(capture):
    """模型吐半截 JSON 时，给空字典让它撞 invalid_arguments —— 别抛。

    抛的话整轮就废了，而且模型拿不到"你参数写坏了"这个反馈，没法自己改。
    """
    _sent, fake = capture
    fake.response = _tool_reply(arguments='{"query": "船')
    reply = client.chat_messages([{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply.call_list() == [("read_capabilities", {})]


def test_wants_tools_ignores_finish_reason(capture):
    """`finish_reason == "stop"` 与非空 tool_calls 同时出现是见过的。

    以 tool_calls 为准。反过来的写法（信 finish_reason）会在那一刻判定
    "模型说完了"，把要调的工具丢掉。
    """
    _sent, fake = capture
    payload = _tool_reply()
    payload["choices"][0]["finish_reason"] = "stop"
    fake.response = payload
    reply = client.chat_messages([{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply.wants_tools


def test_unnamed_tool_call_is_dropped(capture):
    """没有函数名的调用是废的，不能塞进执行队列。"""
    _sent, fake = capture
    fake.response = _tool_reply(name="")
    reply = client.chat_messages([{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply.call_list() == []


def test_arguments_as_dict_already(capture):
    """有的实现直接给字典不给字符串，别在上面崩。"""
    _sent, fake = capture
    payload = _tool_reply()
    payload["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = {
        "query": "船租"}
    fake.response = payload
    reply = client.chat_messages([{"role": "user", "content": "x"}], tools=TOOLS)
    assert reply.call_list() == [("read_capabilities", {"query": "船租"})]
