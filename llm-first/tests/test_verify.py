"""验收器与切段的回归测试。

两类断言：
  1. 正确样本要全过 —— 否则判据太严，模型永远过不了
  2. 错误样本要判死 —— 否则判据太松，等于没有

第二类更重要。每一条"错样本"都对应一个真实发生过的错误，
不是编出来凑数的。
"""
from __future__ import annotations

import json
import re

import contract
import segment
import verify
from app.core.dsl.validator import validate_dsl


def _product(**over):
    base = {
        "route": "dsl",
        "claims": [
            {"id": 1, "text": "把船租降到8万", "kind": "action"},
            {"id": 2, "text": "每天最多干10小时", "kind": "action"},
        ],
        "fills": [
            {"claim": 1, "row": "P14", "span": "8万", "from": "literal", "op": "=", "value": 80000},
            {"claim": 2, "row": "P01", "span": "10小时", "from": "literal", "op": "=", "value": 10},
        ],
        "unassigned": [],
        "dsl": {"parameters": [
            {"field": "船舶日租金", "op": "=", "value": 80000},
            {"field": "每日有效维修上限", "op": "=", "value": 10},
        ]},
    }
    base.update(over)
    return base


GOOD_UTTERANCE = "把船租降到8万，每天最多干10小时"


def _codes(report):
    return {c.code for c in report.checks if c.verdict == "fail"}


# ── 1. 正确样本必须全过 ────────────────────────────────────────────────────

def test_good_sample_passes_everything():
    report = verify.verify(GOOD_UTTERANCE, _product())
    assert not report.blocked, [(c.code, c.detail) for c in report.failures]
    assert all(c.verdict in ("pass", "na") for c in report.checks)


def test_good_sample_also_compiles():
    """全过还不够——得真能编译。A1~A10 查的是产物自洽，编译器查的是执行侧能不能吃。"""
    assert validate_dsl(_product()["dsl"]) == []


def test_clarify_with_need_info_passes():
    report = verify.verify("顺延两天", {
        "route": "clarify",
        "claims": [{"id": 1, "text": "顺延两天", "kind": "action"}],
        "fills": [],
        "unassigned": [{"claim": 1, "reason": "need_info", "note": "从哪天算"}],
        "dsl": {},
    })
    assert not report.blocked


def test_convert_with_calc_passes():
    report = verify.verify("船租涨两成", {
        "route": "dsl",
        "claims": [{"id": 1, "text": "船租涨两成", "kind": "action"}],
        "fills": [{"claim": 1, "row": "P14", "span": "涨两成", "from": "convert",
                   "calc": "12000*1.2=14400", "value": 14400}],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "船舶日租金", "op": "=", "value": 14400}]},
    })
    assert not report.blocked


# ── 2. 错误样本必须判死 ────────────────────────────────────────────────────

def test_dropping_half_a_sentence_is_caught_by_A1():
    """真实错误：规则层直出时只覆盖一半。这里用"少切一条 claim"复现。"""
    product = _product(claims=[{"id": 1, "text": "把船租降到8万", "kind": "action"}],
                       fills=[{"claim": 1, "row": "P14", "span": "8万",
                               "from": "literal", "op": "=", "value": 80000}],
                       dsl={"parameters": [{"field": "船舶日租金", "op": "=", "value": 80000}]})
    assert "A1" in _codes(verify.verify(GOOD_UTTERANCE, product))


def test_fabricated_number_is_caught_by_A4():
    """真实错误：无出处编造。span 里根本没有这个数。"""
    product = _product(
        claims=[{"id": 1, "text": "把船租降一点", "kind": "action"}],
        fills=[{"claim": 1, "row": "P14", "span": "降一点", "from": "literal", "value": 80000}],
        dsl={"parameters": [{"field": "船舶日租金", "op": "=", "value": 80000}]})
    assert "A4" in _codes(verify.verify("把船租降一点", product))


def test_enum_cannot_carry_a_number():
    """真实错误：`只排风机003的任务` 被写成 from=enum, value=1。

    enum 天然不可机器核对，于是它成了万能出口——任何数字塞进去都能过。
    现在规定 enum 的 value 必须是固定词，数字只能走 literal/convert。
    """
    product = _product(
        claims=[{"id": 1, "text": "只排风机003的任务", "kind": "action"}],
        fills=[{"claim": 1, "row": "C11", "span": "只排风机003的任务",
                "from": "enum", "value": 1}],
        dsl={"constraints": [{"type": "linear", "terms": [[1, "完成任务数"]],
                              "op": "<=", "value": 1}]})
    assert "A4" in _codes(verify.verify("只排风机003的任务", product))


def test_unknown_row_is_caught_by_A5():
    product = _product(fills=[{"claim": 1, "row": "P99", "span": "8万",
                               "from": "literal", "value": 80000}])
    assert "A5" in _codes(verify.verify(GOOD_UTTERANCE, product))


def test_conflicting_values_must_go_clarify():
    product = _product(
        claims=[{"id": 1, "text": "船租8万", "kind": "action"},
                {"id": 2, "text": "船租7万", "kind": "action"}],
        fills=[{"claim": 1, "row": "P14", "span": "8万", "from": "literal", "value": 80000},
               {"claim": 2, "row": "P14", "span": "7万", "from": "literal", "value": 70000}],
        dsl={"parameters": [{"field": "船舶日租金", "op": "=", "value": 80000}]})
    report = verify.verify("船租8万，船租7万", product)
    assert "A6" in _codes(report)
    product["route"] = "clarify"
    assert "A6" not in _codes(verify.verify("船租8万，船租7万", product))


def test_extra_dsl_item_is_caught_by_A8():
    """真实错误：dsl 里凭空多一项，没有任何 fill 指向它。"""
    product = _product()
    product["dsl"]["parameters"].append({"field": "维修人工单价", "op": "=", "value": 300})
    assert "A8" in _codes(verify.verify(GOOD_UTTERANCE, product))


def test_orphan_fill_is_caught_by_A8():
    product = _product()
    product["fills"].append({"claim": 2, "row": "P15", "span": "10小时",
                             "from": "literal", "value": 10})
    assert "A8" in _codes(verify.verify(GOOD_UTTERANCE, product))


def test_not_change_swallowing_an_action_is_caught_by_A10():
    """真实错误：`把船租降到8万，为什么这么贵` 被判成 not_change，8 万那半句消失。"""
    product = _product(
        route="not_change",
        claims=[{"id": 1, "text": "把船租降到8万", "kind": "action"},
                {"id": 2, "text": "为什么这么贵", "kind": "ask", "about": "reason"}],
        fills=[], dsl={})
    assert "A10" in _codes(verify.verify("把船租降到8万，为什么这么贵", product))


def test_reject_must_explain_every_claim():
    product = _product(route="reject", claims=[{"id": 1, "text": "把界面改成蓝色", "kind": "action"}],
                       fills=[], unassigned=[], dsl={})
    assert "A10" in _codes(verify.verify("把界面改成蓝色", product))


def test_ask_without_about_is_caught_by_A9():
    product = _product(
        route="not_change",
        claims=[{"id": 1, "text": "经济优先和风险优先差多少", "kind": "ask"}],
        fills=[], dsl={})
    assert "A9" in _codes(verify.verify("经济优先和风险优先差多少", product))


def test_unknown_route_is_caught_by_A9():
    """route 不认识的，必须当场判死，不能悄悄改成 clarify。

    旧系统就是这么干的：6 个 route 名喂进去全部变成「请澄清」，
    模型判对了却被改成"我没听懂"。
    """
    product = _product(route="modify_plan")
    assert "A9" in _codes(verify.verify(GOOD_UTTERANCE, product))


# ── 3. 切段 ────────────────────────────────────────────────────────────────

def test_segments_split_on_punctuation_and_connectives():
    assert segment.segments("任务001和任务002不要同一天做，钱不超过300万") == [
        "任务001和任务002不要同一天做", "钱不超过300万"]


def test_segments_handle_turning_words():
    """真实错误：`但` 不在连接词表里，切出"但高风险的必须全修"，
    模型抄 claim 时去掉"但"，A1 就因为一个字判它漏话。"""
    assert segment.segments("成本压到最低，但高风险的必须全修") == [
        "成本压到最低", "高风险的必须全修"]


def test_prompt_connective_list_matches_segmenter():
    """提示词里教模型的那串连接词，必须就是切段器认的那串。
    两边不同源的话，A1 就成了随机判据。"""
    import prompt

    text = prompt.build()
    listed = re.search(r"连接词含：(.+?)。", text)
    assert listed, "提示词里没有连接词说明"
    taught = [w for w in listed.group(1).split("、") if w]
    assert taught == list(segment.CONNECTIVES), taught


# ── 4. 数字对账（两边共用）─────────────────────────────────────────────────

def test_number_audit_finds_dropped_budget():
    """真实错误：规则层直出 `任务001和002不同天，钱不超过300万` 时，
    只交了 co_occur，300 万静默消失。"""
    dsl = {"constraints": [{"type": "co_occur", "mode": "diff_day",
                            "tasks": ["任务001", "任务002"]}]}
    audit = verify.audit_numbers("任务001和任务002不要同一天做，钱不超过300万", dsl)
    assert 3000000 in [int(x) for x in audit["missing"]]


def test_number_audit_ignores_iso_time_of_day():
    dsl = {"constraints": [{"type": "earliest", "task": "任务018",
                            "time": "2026-11-05T08:00:00+08:00"}]}
    audit = verify.audit_numbers("任务018最早11月5日开工", dsl)
    assert audit["missing"] == []
    assert 8 not in [int(x) for x in audit["extra"]]


# ── 5. 提示词里的例子必须真能编译 ──────────────────────────────────────────

def test_linear_example_in_prompt_compiles():
    """提示词给了 `linear` 的写法示例。示例本身必须过编译器——
    否则就是在教模型踩坑（历史上 `op:"="` 与 terms 形状都这么错过）。"""
    import prompt

    section = prompt.syntax_block()
    blocks = re.findall(r"```json\n(.*?)\n```", section, re.S)
    assert blocks, "写法段里没有 JSON 示例"
    for block in blocks:
        dsl = {"constraints": [json.loads(block)]}
        assert validate_dsl(dsl) == [], f"提示词里的示例编译不过：{dsl}"


def test_output_example_in_prompt_passes_verifier():
    """交卷示例必须自己能过 A1~A10。

    示例是模型最信的东西：示例错了，模型会照着错。这条测试
    就是让"示例"和"验收"永远对得上。
    """
    import prompt

    text = prompt.output_block()
    block = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert block
    raw = json.loads(block.group(1))
    utterance = "".join(c["text"] for c in raw["claims"])
    report = verify.verify(utterance, raw)
    assert not report.blocked, [(c.code, c.detail) for c in report.failures]


# ── 6. 形状归一化 ──────────────────────────────────────────────────────────

def test_contract_normalizes_aliases():
    raw = {"route": "dsl",
           "claims": [{"claim_id": 1, "content": "船租8万", "kind": "action"}],
           "fills": [{"claimId": 1, "rowId": "P14", "quote": "8万", "source": "literal", "val": 80000}],
           "dsl": {"parameters": {"field": "船舶日租金", "op": "=", "value": 80000}}}
    product = contract.normalize(raw)
    assert product["claims"][0]["text"] == "船租8万"
    assert product["fills"][0]["row"] == "P14"
    # 单对象包成数组
    assert product["dsl"]["parameters"] == [{"field": "船舶日租金", "op": "=", "value": 80000}]


# ── 7. 相对时间：既不能放过，也不能冤枉 ────────────────────────────────────

def test_relative_time_without_base_date_is_rejected():
    """真实错误：`明天风大` 直接落成 2026-11-05。没有基准日期，这个日期是猜的。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "明天风大", "kind": "action"},
                   {"id": 2, "text": "先别出海", "kind": "action"}],
        "fills": [
            {"claim": 1, "row": "D04", "span": "明天风大", "from": "convert",
             "calc": "明天→2026-11-05", "value": {"kind": "weather", "day": "2026-11-05", "state": "不可作业"}},
            {"claim": 2, "row": "C09", "span": "先别出海", "from": "convert",
             "calc": "先别出海→明天不可作业", "value": {"type": "forbidden_dates", "dates": ["2026-11-05"]}},
        ],
        "unassigned": [],
        "dsl": {"data": [{"kind": "weather", "day": "2026-11-05", "state": "不可作业"}],
                "constraints": [{"type": "forbidden_dates", "dates": ["2026-11-05"]}]},
    }
    assert "A4" in _codes(verify.verify("明天风大，先别出海", product))


def test_date_with_source_is_accepted():
    """反过来也要对：原文有"11月5日"，写 2026-11-05 就不该被判错。

    这里最容易出假警报——"别出海"本身没有日期，日期来自同句的上一段。
    判据如果写成"span 里没日期就是猜的"，就会冤枉它。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "11月5日风大", "kind": "action"},
                   {"id": 2, "text": "别出海", "kind": "action"}],
        "fills": [
            {"claim": 1, "row": "D04", "span": "11月5日风大", "from": "convert",
             "calc": "11月5日→2026-11-05，风大→不可作业",
             "value": {"kind": "weather", "day": "2026-11-05", "state": "不可作业"}},
            {"claim": 2, "row": "C09", "span": "别出海", "from": "convert",
             "calc": "别出海→11月5日不可作业", "value": {"type": "forbidden_dates", "dates": ["2026-11-05"]}},
        ],
        "unassigned": [],
        "dsl": {"data": [{"kind": "weather", "day": "2026-11-05", "state": "不可作业"}],
                "constraints": [{"type": "forbidden_dates", "dates": ["2026-11-05"]}]},
    }
    report = verify.verify("11月5日风大，别出海", product)
    assert not report.blocked, [(c.code, c.detail) for c in report.failures]
