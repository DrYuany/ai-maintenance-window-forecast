"""基准日期与 A11/A12 两条判据的回归。

这三个东西是同一轮补的，放在一个文件里：
  · 基准日期 —— 让"明天"有解；
  · A11 方向 —— 抓住"结论反了"；
  · A12 不轻拒 —— 给 reject 一个自查线索。

它们的共同点：**都在回答"机器到底能判到什么程度"**，
而不是"再写一条规则拦一下"。所以每条测试的断言都写清"为什么这样判"。
"""
from __future__ import annotations

import datetime

import context
import verify


def _codes(report: verify.Report) -> set[str]:
    return {c.code for c in report.checks if c.verdict == "fail"}


CTX = context.Context(today=datetime.date(2026, 10, 30))


# ── 1. 换算表：能算的算准，算不出的不猜 ────────────────────────────────────

def test_offsets_and_weekdays():
    assert CTX.resolve("今天") == datetime.date(2026, 10, 30)
    assert CTX.resolve("明天") == datetime.date(2026, 10, 31)
    assert CTX.resolve("后天") == datetime.date(2026, 11, 1)
    assert CTX.resolve("大后天") == datetime.date(2026, 11, 2)
    # 2026-10-30 是周五，所以"本周一"是 10-26，"下周一"是 11-02
    assert CTX.resolve("本周一") == datetime.date(2026, 10, 26)
    assert CTX.resolve("下周一") == datetime.date(2026, 11, 2)
    assert CTX.resolve("上周五") == datetime.date(2026, 10, 23)


def test_ambiguous_words_resolve_to_nothing():
    """有歧义的词必须算不出来。

    "下周"是从明天起算还是从周一起算？"月底"是这个月还是下个月？
    机器猜一个就是编造——所以宁可返回 None，让上层退回澄清。
    """
    for word in ("下周", "月底", "月初", "下个月", "这个月"):
        assert CTX.resolve(word) is None, word


def test_find_relative_does_not_split_weekday_into_ambiguous():
    """`下周` 是 `下周一` 的前缀，扫描顺序错了会把能算的当成不能算的。"""
    assert context.find_relative("下周一也别出海") == ["下周一"]


# ── 2. 有基准之后，判据从"不许落日期"翻成"落错了才判死" ────────────────────

def _weather_product(day: str) -> dict:
    return {
        "route": "dsl",
        "claims": [{"id": 1, "text": "明天风大", "kind": "action"},
                   {"id": 2, "text": "先别出海", "kind": "action"}],
        "fills": [
            {"claim": 1, "row": "D04", "span": "明天风大", "from": "default",
             "why": "原文未给风速，按不可作业处理",
             "value": {"kind": "weather", "day": day, "state": "不可作业"}},
        ],
        "unassigned": [{"claim": 2, "reason": "need_info", "note": "已由天气补丁覆盖"}],
        "dsl": {"data": [{"kind": "weather", "day": day, "state": "不可作业"}]},
    }


def test_base_date_makes_tomorrow_legal():
    """给了基准，2026-10-31 就是对的——旧判据会把它判死，那是误报。"""
    report = verify.verify("明天风大，先别出海", _weather_product("2026-10-31"), CTX)
    assert "A4" not in _codes(report), [c.detail for c in report.checks if c.code == "A4"]


def test_wrong_conversion_is_still_caught():
    """给了基准之后判据**更严**了：落错了要判死，并说清基准算是哪天。"""
    report = verify.verify("明天风大，先别出海", _weather_product("2026-11-05"), CTX)
    assert "A4" in _codes(report)
    detail = report.get("A4").detail
    assert "2026-10-31" in detail, detail


def test_without_base_date_any_date_is_a_guess():
    """不传基准时退回旧行为——判据不许偷偷读系统时钟。"""
    report = verify.verify("明天风大，先别出海", _weather_product("2026-10-31"))
    assert "A4" in _codes(report)


# ── 3. A11 方向：唯一一类"全过还更糟"的错 ──────────────────────────────────

def _budget_product(op: str) -> dict:
    return {
        "route": "dsl",
        "claims": [{"id": 1, "text": "钱不超过300万", "kind": "action"}],
        "fills": [{"claim": 1, "row": "C10", "span": "不超过300万",
                   "from": "literal", "value": 3000000}],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "budget", "quantity": "现金支出",
                                 "op": op, "value": 3000000}]},
    }


def test_direction_reversed_is_blocked():
    """「不超过」写成 `>=`：值对、能编译、能求解，只是结论反了。"""
    report = verify.verify("钱不超过300万", _budget_product(">="))
    assert "A11" in _codes(report)
    detail = report.get("A11").detail
    assert "不超过" in detail


def test_direction_correct_passes():
    report = verify.verify("钱不超过300万", _budget_product("<="))
    assert "A11" not in _codes(report)


def test_direction_equal_sign_is_unclear_not_fail():
    """把不等式写成等号可能是近似，也可能是错的——判不了就不判死。

    这条守的是"假警报比漏报更伤"：`==` 在这里只是一种收紧写法，
    判死会让所有"钱就压到300万"的说法都被退回。
    """
    report = verify.verify("钱不超过300万", _budget_product("=="))
    assert report.get("A11").verdict == "unclear"
    assert "A11" not in _codes(report)


def test_direction_ignores_parameters():
    """参数是赋值：「上限放宽到14」里的"上限"是栏目名，不是比较方向。

    第一版对所有项都判，于是每一句带"上限"的参数改动都被判死。
    参数段（P 组）没有比较语义，直接不进来。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "风速上限放宽到14米每秒", "kind": "action"}],
        "fills": [{"claim": 1, "row": "P11", "span": "14米每秒",
                   "from": "literal", "value": 14}],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "常态风速上限", "op": "=", "value": 14}]},
    }
    report = verify.verify("风速上限放宽到14米每秒", product)
    assert report.get("A11").verdict == "na", report.get("A11").detail


def test_direction_two_way_sentence_is_not_judged():
    """一句话里两个方向都有（"不超过300万，不低于200万"）→ 算不清，不判。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "成本不超过300万，不低于200万", "kind": "action"}],
        "fills": [{"claim": 1, "row": "C10", "span": "不超过300万",
                   "from": "literal", "value": 3000000}],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "budget", "quantity": "现金支出",
                                 "op": ">=", "value": 3000000}]},
    }
    report = verify.verify("成本不超过300万，不低于200万", product)
    assert report.get("A11").verdict == "na"


# ── 4. A12：reject 的自查线索（不拦截） ────────────────────────────────────

def _reject_product(text: str) -> dict:
    return {
        "route": "reject",
        "claims": [{"id": 1, "text": text, "kind": "action"}],
        "fills": [],
        "unassigned": [{"claim": 1, "reason": "row_not_wired", "note": "做不到"}],
        "dsl": {},
    }


def test_reject_with_in_table_words_is_flagged():
    """「只排风机003的任务」整句拒了，但这句话里有"风机0""任务0"——值得看一眼。"""
    report = verify.verify("只排风机003的任务", _reject_product("只排风机003的任务"))
    check = report.get("A12")
    assert check.verdict == "unclear"
    assert check.items, "线索要能列出来，不能只说一句'疑似'"


def test_reject_without_clue_passes():
    report = verify.verify("帮我订一杯咖啡", _reject_product("帮我订一杯咖啡"))
    assert report.get("A12").verdict == "pass"


def test_a12_never_blocks():
    """线索不拦截。判不了的事说成"你错了"，一条假警报就能让人关掉验收器。"""
    report = verify.verify("只排风机003的任务", _reject_product("只排风机003的任务"))
    assert not report.blocked


# ── 窗口起止也要进现值表 ──────────────────────────────────────────────────

def _params_ctx() -> context.Context:
    return context.with_params_for(CTX)


def test_window_dates_are_available_for_relative_change():
    """「窗口02提前两天」要知道窗口02原来哪天开始，否则只能反问。

    和「船少一条」是同一件事：现值系统自己知道，让用户重说一遍是产品的问题。
    """
    ctx = _params_ctx()
    if not ctx.windows:                 # 读不到数据就跳过，不算失败
        return
    first = ctx.windows[0]
    name, start, end = first
    assert name.startswith("窗口")
    assert len(start) >= 10 and len(end) >= 10
    body = ctx.param_block()
    assert "海事窗口" in body and name in body and start[:10] in body


def test_param_block_falls_back_when_no_data():
    """没有现值就不要编一段。宁可提示词短一点，也不要出现"现值未知"这种空话。"""
    assert context.Context(today=datetime.date(2026, 10, 30)).param_block() == ""
