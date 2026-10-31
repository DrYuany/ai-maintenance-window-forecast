"""read_capabilities 的行为约定。

这个工具的价值全押在一件事上：**它报的"能不能编译"必须是真的**。
报错了不是"答得不准"，是给模型递假情报 —— 它会照着写，
然后在求解阶段炸，而我们要花几个小时才能追回到这里。

所以第一组测试守"真话"，第二组守"中英/口语能不能对上"，
第三组守"查不到的时候不许装懂"。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import paths  # noqa: E402,F401
import tools  # noqa: E402


def _items(query: str, kind: str | None = None) -> list[dict]:
    args = {"query": query}
    if kind:
        args["kind"] = kind
    result = tools.execute("read_capabilities", args)
    assert result["status"] == "ok", result
    return result["data"]["items"]


def _find(items: list[dict], name: str) -> dict | None:
    return next((it for it in items if it.get("name") == name), None)


# ── 一、真话：能不能编译 ──────────────────────────────────────────────────────

def test_weighted_is_reported_as_executable():
    """`weighted` 现在能跑了，工具必须跟着改口。

    它曾经被报成"不能编译"，因为 `runner.run` 把整段 objectives 丢给了
    `stages_builder`，而 `strategy` / `weighted` 其实是**求解器模式**，
    该被 `split_objectives` 摘出来变成 mode / weights。补齐之后五个目标
    原语全部可执行。

    **报错方向反了同样是假情报。** 上一版把它藏起来，模型会以为系统不支持
    加权，于是拒绝一个完全能满足的要求——比报"能用"然后炸更隐蔽。
    """
    item = _find(_items("weighted", "objective"), "weighted")
    assert item is not None
    assert item.get("executable") is not False, item
    assert "why" not in item


def test_all_objectives_claimed_executable_really_run():
    """工具声称能跑的每个目标原语，必须真的有人接住。

    判据是"两条路占一条"：被 `split_objectives` 提成求解器模式，
    或者 `stages_builder` 能建出分层阶段。两边都不管的就是当年那个 bug。
    """
    from app.core.dsl.compiler import stages_builder
    from app.core.dsl.planning_bridge import split_objectives
    from app.core.dsl.registry import primitives as prims

    claimable = tools.executable_objectives(prims)
    assert claimable == set(prims.OBJECTIVE_PRIMITIVES), (
        f"还有目标原语被报成不可执行：{set(prims.OBJECTIVE_PRIMITIVES) - claimable}")

    for name in claimable:
        spec = {"type": name, "name": "成本最低优先",
                "weights": {"总经济成本": 1.0}, "q": {}, "stages": []}
        _s, _m, _w, rest = split_objectives([spec])
        if not rest:
            continue                       # 走模式那条路
        try:
            stages_builder(rest, None)(None, [], None)
        except AttributeError:
            continue                       # 分派对了，空 model 才崩
        except ValueError as exc:
            pytest.fail(f"工具说 {name} 能跑，但编译器说：{exc}")


def test_constraint_executability_is_honest():
    """约束原语的"能不能编译"必须与 `COMPILED_CONSTRAINTS` 完全一致。

    注册表里登记了 15 个约束原语，编译器只实现了 11 个：
    `at_most_one` / `at_least_one` / `no_overlap` / `capacity` 都在册，
    但 `compile_constraint` 里没有它们的分支。

    这不是工具的 bug，是工具**如实报告**了一个真实的能力缺口。
    这条测试守两件事：已实现的 11 个不许被误报成不能编译（藏能力），
    未实现的 4 个不许被误报成能编译（递假情报）。
    """
    from app.core.dsl.registry import primitives as prims

    compiled = set(prims.COMPILED_CONSTRAINTS)
    for name in prims.CONSTRAINT_PRIMITIVES:
        result = tools.execute("read_capabilities", {"query": name, "kind": "constraint"})
        item = next((i for i in result["data"]["items"]
                     if i["name"] == name and i["kind"] == "constraint"), None)
        assert item is not None, f"查不到 {name}"
        if name in compiled:
            assert item.get("executable") is not False, f"{name} 已实现却被报成不能编译"
        else:
            assert item.get("executable") is False, f"{name} 未实现却被报成能编译"


def test_all_constraint_branches_exist():
    """已实现的约束原语，编译器里必须真有分支。

    这条防的是另一头：某个原语被加进 COMPILED_CONSTRAINTS、却没写编译分支。
    那时工具会如实报 True（因为它在 COMPILED 里），而求解阶段才炸。
    """
    from app.core.dsl.registry import primitives as prims

    source = Path(paths.KERNEL).parent.parent / "backend/app/core/dsl/compiler.py"
    if not source.exists():        # 路径变了就跳过，不让它误报
        pytest.skip("找不到 compiler.py")
    text = source.read_text(encoding="utf-8")
    missing = [name for name in prims.COMPILED_CONSTRAINTS
               if f'ctype == "{name}"' not in text]
    assert not missing, f"这些原语声称能编译但编译器里找不到分支：{missing}"


# ── 二、中英鸿沟与口语 ────────────────────────────────────────────────────────

def test_chinese_query_reaches_chinese_parameter():
    """「船租」必须能摸到「船舶日租金」——字面上它们没有连续公共子串。

    这是工具存在的核心理由：模型说"船租"，表里写"船舶日租金"，
    纯字面匹配永远对不上。
    """
    items = _items("船租")
    names = [it["name"] for it in items]
    assert "船舶日租金" in names
    assert names.index("船舶日租金") <= 2, f"排太后面了，模型未必看到：{names[:5]}"


def test_chinese_query_reaches_english_primitive():
    """「加权」必须能摸到 `weighted`——这两个词字符零重叠，只能靠注解。"""
    items = _items("加权")
    names = [it["name"] for it in items]
    assert "weighted" in names, names
    assert names.index("weighted") <= 3, f"排太后面了：{names[:5]}"


def test_colloquial_headcount_reaches_personnel_params():
    """「人手」是口语，表里叫「可调动人员总数」。"""
    names = [it["name"] for it in _items("人手")]
    assert "可调动人员总数" in names, names
    assert "维修人工单价" in names or "每日有效维修上限" in names


def test_alias_rows_never_outrank_real_params():
    """alias 行字面特别容易命中（"船租计费单位"含"船租"），但不能抢头条。

    抢了的话，模型第一条看到的是"船租计费单位 maps_to unsupported"，
    很可能就判"不支持"了——而真正的参数就在下面第五行。
    """
    items = _items("船租")
    first_alias = next((i for i, it in enumerate(items) if it["kind"] == "alias"), None)
    first_param = next((i for i, it in enumerate(items) if it["kind"] == "parameter"), None)
    if first_alias is not None and first_param is not None:
        assert first_param < first_alias


# ── 三、查不到的时候不许装懂 ──────────────────────────────────────────────────

def test_no_match_still_hands_over_candidates():
    """查不到也要给候选 + 明说"只是没字面命中"。

    直接回 not_found 等于告诉模型"系统不支持"，而"船租"这种情况
    恰恰是**系统支持但字面对不上**。这两件事必须分开。
    """
    # 故意用一个业务上绝不存在的词，保证 matched=0
    result = tools.execute("read_capabilities", {"query": "zzzz"})
    assert result["status"] == "ok"
    data = result["data"]
    assert data["matched"] == 0
    assert data["items"], "没命中就什么都不给，模型没法自己判断"
    assert "没有字面命中" in (data.get("hint") or "")


def test_empty_query_returns_counts_only():
    """空查询只给计数。铺全表几千字，模型反而抓不住重点。"""
    result = tools.execute("read_capabilities", {"query": ""})
    assert result["status"] == "ok"
    summary = result["data"]["summary"]
    assert summary["parameter"] == 15
    assert summary["constraint"] == 15
    assert "items" not in result["data"]


# ── 四、坏输入不许炸 ──────────────────────────────────────────────────────────

def test_bad_query_type():
    assert tools.execute("read_capabilities", {"query": 123})["status"] == "invalid_arguments"
    assert tools.execute("read_capabilities", {})["status"] == "invalid_arguments"


def test_bad_kind():
    result = tools.execute("read_capabilities", {"query": "船租", "kind": "nonsense"})
    assert result["status"] == "invalid_arguments"


def test_unknown_tool_is_not_an_exception():
    """工具名写错要能自己学乖，而不是把整轮炸掉。"""
    result = tools.execute("read_everything", {})
    assert result["status"] == "not_found"
    assert "read_capabilities" in result["message"]


def test_handler_crash_becomes_internal(monkeypatch):
    """工具内部异常也要变成可读状态，不能冒泡。"""
    def boom(_args):
        raise RuntimeError("模拟崩了")

    monkeypatch.setitem(tools._HANDLERS, "read_capabilities", boom)
    result = tools.execute("read_capabilities", {"query": "x"})
    assert result["status"] == "internal"


def test_result_is_capped():
    """单次返回要有上限，防止把整张表倒给模型。"""
    result = tools.execute("read_capabilities", {"query": "a"})
    assert len(result["data"]["items"]) <= tools._MAX_ITEMS


# ── prepare_change：交卷前自己试一遍 ───────────────────────────────────────────
# 这个工具最危险的地方不是"报错"，是"报得和交卷时不一样"。
# 上一轮刚吃过「同一段 JSON 两个解析器两个结论」的亏：循环里说交卷了，
# 验收里说不是合法 JSON，白烧一整轮修复。所以这里第一条就守"同源"。

def test_prepare_change_shares_one_verdict_with_compile_check():
    """同一份 DSL，工具说的和交卷后判的必须是同一句话。

    两边都调 validate_dsl，所以断言是「逐字相等」而不是「都是错的」。
    一旦有人给其中一边加个"顺手也查一下"，这条会立刻红。
    """
    import pipeline

    bad = {"constraints": [{"type": "linear", "op": "<=", "value": 1,
                            "terms": [[1, "最后完工时间"]]}]}
    result = tools.execute("prepare_change", {"dsl": bad})

    assert result["status"] == "invalid"
    assert result["errors"] == pipeline._compile_check(bad)


def test_prepare_change_ok_does_not_mean_ready_to_submit():
    """绿灯要配一句话说清它只过了格式关。

    不说的话模型会把 ok 当成"验收通过"直接交卷，然后被 A1~A14 退回来——
    比不试还冤，还多烧一轮。
    """
    result = tools.execute("prepare_change", {"dsl": {"constraints": [], "objectives": [], "data": []}})

    assert result["status"] == "ok"
    assert result["errors"] == []
    assert "不等于" in result["hint"]


def test_prepare_change_rejects_empty_payload_with_a_usable_reason():
    """模型偶尔会把参数名写成 dsl_json / changes。回一句它能照着改的话。"""
    assert tools.execute("prepare_change", {})["status"] == "invalid_arguments"
    assert tools.execute("prepare_change", {"dsl": {}})["status"] == "invalid_arguments"
    assert tools.execute("prepare_change", {"dsl": "不是对象"})["status"] == "invalid_arguments"


def test_prepare_change_never_pretends_to_pass_when_validator_is_missing(monkeypatch):
    """校验器加载不了时不能返回 ok。假装通过比报错危险得多。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "app.core.dsl.validator" or name.endswith("dsl.validator"):
            raise ImportError("模拟校验器缺失")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    result = tools.execute("prepare_change", {"dsl": {"constraints": []}})

    assert result["status"] == "unavailable"
    assert any("校验器" in item for item in result["errors"])
