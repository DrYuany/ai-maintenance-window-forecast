from __future__ import annotations

from types import SimpleNamespace

from app.core import conversation_agent as module
from app.core.conversation_agent import MATH_PASSTHROUGH
from app.core.agent_tools import AgentToolbox


def test_math_passthrough_triggers_on_formula_requests() -> None:
    assert MATH_PASSTHROUGH.search("给我方案01的目标函数公式")
    assert MATH_PASSTHROUGH.search("用 LaTeX 写出约束")
    assert MATH_PASSTHROUGH.search("把数学模型贴出来")


def test_math_passthrough_ignores_cost_questions() -> None:
    assert not MATH_PASSTHROUGH.search("你直接告诉我目标函数考虑了哪些成本")
    assert not MATH_PASSTHROUGH.search("为什么这版成本更低")


def test_tool_surface_is_the_whole_product_surface() -> None:
    """工具清单是产品面，不是实现细节：多一个就多一条模型能做的事。

    这条测试拦的是「不小心多出来一个工具」，所以改动它必须有理由：
    read_jobs 是 2026-10-31 有意加的——以前模型看不见自己派出去的求解活，
    用户问「算完了吗」它只能让人自己去方案页面看。
    """
    toolbox = object.__new__(AgentToolbox)
    specs = toolbox.tool_specs()

    assert [spec["name"] for spec in specs] == [
        "read_versions",
        "read_environment",
        "read_catalog",
        "read_alarms",
        "read_site",
        "read_jobs",
        "solve",
        "submit_change",
        "ledger",
    ]
    # 读类工具必须自报"只读"，写类工具（solve 预览、ledger 台账）不能这么标。
    for spec in specs:
        if spec["name"].startswith("read_"):
            assert "只读" in spec["description"]


def test_read_schema_accepts_multiple_versions() -> None:
    toolbox = object.__new__(AgentToolbox)
    specs = {spec["name"]: spec for spec in toolbox.tool_specs()}
    versions = specs["read_versions"]["input_schema"]["properties"]["versions"]

    assert versions["type"] == "array"
    assert "include" in specs["read_versions"]["input_schema"]["properties"]
    assert "baseline" in specs["read_versions"]["input_schema"]["properties"]


# --- 下面的假 Anthropic 客户端只为验证循环本身 -------------------------------
# 真 SDK 的 stream() 是个上下文管理器：text_stream 边吐字，get_final_message()
# 在结束后给出完整 content（text 块 + tool_use 块）。这里按这个形状做一个。


def _text(body: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=body)


def _tool_use(name: str, args: dict | None = None, tid: str = "t1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=name, input=args or {}, id=tid)


class _FakeStream:
    def __init__(self, final: SimpleNamespace) -> None:
        self._final = final

    def __enter__(self) -> "_FakeStream":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    @property
    def text_stream(self):
        return iter([b.text for b in self._final.content if b.type == "text"])

    def get_final_message(self) -> SimpleNamespace:
        return self._final


class _FakeClient:
    """按顺序吐出预设回复；calls 记下每一轮实际发给模型的 messages。"""

    def __init__(self, replies: list[SimpleNamespace]) -> None:
        self._replies = list(replies)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs: object) -> _FakeStream:
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        assert index < len(self._replies), (
            f"循环多跑了第 {index + 1} 轮，但只准备了 {len(self._replies)} 条回复"
        )
        return _FakeStream(self._replies[index])


class _FakeToolbox:
    ordered: list = []

    def __init__(self, results: dict[str, dict]) -> None:
        self._results = results
        self.executed: list[str] = []

    def tool_specs(self) -> list:
        return []

    def execute(self, name: str, args: dict) -> dict:
        self.executed.append(name)
        return self._results[name]


def _agent(monkeypatch, replies: list[SimpleNamespace]) -> tuple[object, _FakeClient]:
    fake = _FakeClient(replies)
    monkeypatch.setattr(module, "Anthropic", lambda **kwargs: fake)
    agent = module.ConversationAgent(api_key="test-key")
    # 提示词与历史不是这两个用例的观察对象，替换掉以免牵动真实上下文装配。
    agent._build_messages = lambda *a, **k: []
    agent._system_prompt = lambda *a, **k: "system"
    return agent, fake


def _ask(agent, message: str, toolbox: _FakeToolbox) -> str:
    return agent.answer(
        message=message,
        history=[],
        memory=None,
        toolbox=toolbox,
        selected_version=None,
    )


def test_verbatim_only_turn_answers_in_one_roundtrip(monkeypatch) -> None:
    """只要原样内容（公式、参数表）时走快路径，不再多烧一轮模型。"""
    reply = SimpleNamespace(
        content=[_text("这是本版模型："), _tool_use("read_versions", tid="t1")]
    )
    agent, fake = _agent(monkeypatch, [reply])
    toolbox = _FakeToolbox(
        {"read_versions": {"status": "ok", "data": {"markdown": "目标函数……", "verbatim": True}}}
    )

    out = _ask(agent, "把数学模型贴出来", toolbox)

    assert len(fake.calls) == 1
    assert out == "这是本版模型：\n\n目标函数……"


def test_verbatim_plus_other_request_does_not_drop_the_second_half(monkeypatch) -> None:
    """同一轮里既取公式又要求改参数时，后半句不能被快路径吃掉。

    这是真出过的 bug：只要本轮读到 verbatim 就直接 return，于是"贴出这版公式，
    再把船租压到8万"里的"压船租"当场消失，而界面没有任何提示。
    """
    first = SimpleNamespace(
        content=[
            _tool_use("read_versions", tid="t1"),
            _tool_use("solve", {"change": "船租8万"}, tid="t2"),
        ]
    )
    second = SimpleNamespace(content=[_text("船租已经压到8万，方案见上。")])
    agent, fake = _agent(monkeypatch, [first, second])
    toolbox = _FakeToolbox(
        {
            "read_versions": {
                "status": "ok",
                "data": {"markdown": "目标函数……", "verbatim": True},
            },
            "solve": {"status": "ok", "data": {"markdown": "已改"}},
        }
    )

    out = _ask(agent, "把数学模型贴出来，再把船租压到8万", toolbox)

    assert len(fake.calls) == 2, "夹着别的诉求就必须继续回喂，不能提前交卷"
    # 两个工具的结果都要回喂给模型，否则模型看不到"船租改了没有"
    fed = fake.calls[1]["messages"][-1]["content"]
    assert [item["tool_use_id"] for item in fed] == ["t1", "t2"]
    assert "船租已经压到8万" in out
    assert "目标函数……" in out, "原样内容仍然要出现在最终答复里"

