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


def test_relative_time_cannot_dodge_by_literal_or_other_span():
    """同一个错误换身衣服：把相对时间词留在上一段，日期记到这一段的 literal 上。

    真实跑批里见过这样的产物——span 写"先别出海"（里面没有"明天"），
    from 写 literal（不走文字推导那支），value 直接给一个 ISO 日期。
    旧判据只看 span，于是这条一路绿灯，数字对账还显示"没丢数"。
    所以条件要抬到整句：**整句有相对时间词、且整句没有绝对日期**，落出日期就算猜。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "明天风大", "kind": "action"},
                   {"id": 2, "text": "先别出海", "kind": "action"}],
        "fills": [
            {"claim": 2, "row": "D04", "span": "先别出海", "from": "literal",
             "value": {"kind": "weather", "day": "2026-10-31", "state": "不可作业"}},
        ],
        "unassigned": [],
        "dsl": {"data": [{"kind": "weather", "day": "2026-10-31", "state": "不可作业"}]},
    }
    assert "A4" in _codes(verify.verify("明天风大，先别出海", product))


def test_relative_token_smuggled_into_value_is_rejected():
    """第三种绕法，也是实测撞到的：不编日期，直接把"明天"写进日期字段。

    产物长这样：`from=default` + `why="原文未给具体日期"` + `day="明天"`。
    A4 的 default 分支只看 why 写没写，于是放行；A9 只看形状和枚举，也放行；
    最后 `{"day": "明天"}` 一路进编译器。**没编造不等于能用。**
    两头都要堵：value 里不许留相对时间词（A4），日期字段必须是日期（A9）。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "明天风大", "kind": "action"},
                   {"id": 2, "text": "先别出海", "kind": "action"}],
        "fills": [
            {"claim": 1, "row": "D04", "span": "明天风大", "from": "default",
             "why": "原文只说风大，未给具体风速，按不可作业处理",
             "value": {"day": "明天", "state": "不可作业"}},
            {"claim": 2, "row": "C09", "span": "先别出海", "from": "default",
             "why": "原文未给具体日期，明天按不可作业日处理",
             "value": {"dates": ["明天"]}},
        ],
        "unassigned": [],
        "dsl": {"data": [{"kind": "weather", "day": "明天", "state": "不可作业"}],
                "constraints": [{"type": "forbidden_dates", "dates": ["明天"]}]},
    }
    codes = _codes(verify.verify("明天风大，先别出海", product))
    assert "A4" in codes, "value 里的相对时间词必须判死"
    assert "A9" in codes, "日期字段里是「明天」也必须判死"


def test_date_fields_accept_iso_and_month_day():
    """反过来：正常的日期写法不许被这条新判据误伤（ISO 与"11月9日"都算）。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "任务003最晚11月9日修完", "kind": "action"}],
        "fills": [
            {"claim": 1, "row": "C07", "span": "11月9日", "from": "literal",
             "value": "2026-11-09T08:00:00+08:00"},
        ],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "deadline", "task": "任务003",
                                 "finish": "2026-11-09T08:00:00+08:00"}]},
    }
    codes = _codes(verify.verify("任务003最晚11月9日修完", product))
    assert "A9" not in codes


# ── 8. 数字对账的适用性 ─────────────────────────────────────────────────────

def test_number_audit_is_not_applicable_without_dsl():
    """正确拒绝的一句话，不许被记成"丢数"。

    跑批跑出来的：`把风险降低50%` 判成 reject 是对的，但数字对账照跑，
    报"丢数 50"——把"正确地拒绝"显示成"丢了用户的话"，这是最伤的一种假警报：
    它看起来和真丢话一模一样，跑几轮人就不看这一列了。
    """
    audit = verify.audit_numbers("把风险降低50%", {}, route="reject")
    assert audit["applicable"] is False
    assert audit["missing"] == [] and audit["dropped"] == 0


def test_number_audit_still_works_for_dsl():
    audit = verify.audit_numbers("钱不超过300万",
                                 {"constraints": [{"type": "budget", "op": "<=", "value": 3000000}]},
                                 route="dsl")
    assert audit["applicable"] is not False
    assert audit["missing"] == []


def test_percent_is_not_a_dropped_number():
    """「上涨20%」落到 DSL 是 `*1.2`，20 本来就不该出现。"""
    audit = verify.audit_numbers("船租上涨20%",
                                 {"parameters": [{"field": "船舶日租金", "op": "*", "value": 1.2}]})
    assert audit["missing"] == []

    # 但它也不能变成"随便写个数就算对"：写成绝对值 12000（等于没改）仍然判丢
    wrong = verify.audit_numbers("船租上涨20%",
                                 {"parameters": [{"field": "船舶日租金", "op": "=", "value": 12000}]})
    assert wrong["missing"] == [20.0]


# ── 9. 时刻归一 ────────────────────────────────────────────────────────────

def test_time_normalisation_is_accepted():
    """`8点` 落成 `"08:00"` 是完全正确的写法，不该因为字面不同被判错。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "每天8点开工", "kind": "action"}],
        "fills": [{"claim": 1, "row": "P02", "span": "8点", "from": "literal", "value": "08:00"}],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "每天开始维修", "op": "=", "value": "08:00"}]},
    }
    report = verify.verify("每天8点开工", product)
    assert "A4" not in _codes(report)


def test_afternoon_hour_must_shift():
    """反过来：「下午6点」写成 `06:00` 是错的，A4 要判死。

    这条同时说明为什么时刻比对值得做——只按字符串比，
    `06:00` 和 `18:00` 都会被判"推不出"，等于两种写法都不对。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "下午6点收工", "kind": "action"}],
        "fills": [{"claim": 1, "row": "P03", "span": "下午6点", "from": "literal", "value": "06:00"}],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "每天结束维修", "op": "=", "value": "06:00"}]},
    }
    assert "A4" in _codes(verify.verify("下午6点收工", product))


# ── 10. A1 与切段器：连接词不该造成假警报 ───────────────────────────────────

def test_claim_keeping_connective_still_covers():
    """「同时」在切段时被当连接词剥掉，claim 照抄原文却还带着它。

    这不是模型漏了一段，是两边形态不同：判据要把连接词从两边一起去掉再比。
    修之前这条会 fail，整句被降级成 clarify——一句完全正确的话被退回。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "T01和T02不能同时修", "kind": "action"}],
        "fills": [{"claim": 1, "row": "C04", "span": "T01和T02不能同时修", "from": "literal",
                   "value": {"mode": "diff_day", "tasks": ["任务001", "任务002"]}}],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "co_occur", "mode": "diff_day",
                                 "tasks": ["任务001", "任务002"]}]},
    }
    assert "A1" not in _codes(verify.verify("T01和T02不能同时修", product))


def test_pure_connective_segment_is_not_a_gap():
    """只剩连接词的段没有内容可覆盖，不能因为它没有 claim 就报漏。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "船租涨到12万", "kind": "action"}],
        "fills": [{"claim": 1, "row": "P13", "span": "12万", "from": "literal", "value": 120000}],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "船舶日租金", "op": "=", "value": 120000}]},
    }
    assert "A1" not in _codes(verify.verify("船租涨到12万，另外", product))


# ── 11. A6 单值：lexmin 天生一串，不是两个值打架 ───────────────────────────

def test_lexmin_stages_are_not_a_clash():
    """「先最小化A再最小化B」会在 O04 上填两条，那是两个阶段。

    修之前 A6 把它当"同一行两个值"判死，一句完全正确的话被降级成 clarify。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "先最小化最后完工时间，再最小化总经济成本", "kind": "action"}],
        "fills": [
            {"claim": 1, "row": "O04", "span": "先最小化最后完工时间", "from": "literal",
             "value": {"dir": "minimize", "q": "最后完工时间"}},
            {"claim": 1, "row": "O04", "span": "再最小化总经济成本", "from": "literal",
             "value": {"dir": "minimize", "q": "总经济成本"}},
        ],
        "unassigned": [],
        "dsl": {"objectives": [{"type": "lexmin", "stages": [
            {"dir": "minimize", "q": "最后完工时间"},
            {"dir": "minimize", "q": "总经济成本"}]}]},
    }
    assert "A6" not in _codes(verify.verify("先最小化最后完工时间，再最小化总经济成本", product))


def test_multi_value_kinds_come_from_registry():
    """名单从注册表算，不手抄——不然加了同样形状的原语，判据会静默不认。"""
    kinds = verify._multi_value_kinds()
    assert "lexmin" in kinds


# ── 12. A7 退回消息要说清楚缺哪个键 ───────────────────────────────────────

def test_a7_names_the_missing_key():
    """「严重故障11月8日前必须修完」被写成 `deadline{severity:...}`——少了个 `task`。

    退回消息只写"1 项缺必填字段"，模型下一轮还是不知道改哪儿；重试机制就等于没用。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "严重故障11月8日前必须修完", "kind": "action"}],
        "fills": [{"claim": 1, "row": "C07", "span": "严重故障11月8日前必须修完",
                   "from": "convert", "op": "<=",
                   "value": {"severity": "高", "time": "2026-11-08T08:00:00+08:00"},
                   "calc": "严重故障=高"}],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "deadline", "severity": "高",
                                 "time": "2026-11-08T08:00:00+08:00"}]},
    }
    check = verify.verify("严重故障11月8日前必须修完", product).get("A7")
    assert check.verdict == "fail"
    assert "task" in check.detail, "要说清缺的是哪个键，不然重试拿不到能改的信息"
    assert "deadline" in check.detail


# ── 13. A6 合并：同一条数据补丁的两半不是两个值 ───────────────────────────

def _part_arrival_product():
    return {
        "route": "dsl",
        "claims": [{"id": 1, "text": "叶片11月5日到2套", "kind": "action"},
                   {"id": 2, "text": "到货前不能开工", "kind": "action"}],
        "fills": [
            {"claim": 1, "row": "D02", "span": "叶片11月5日到2套", "from": "literal",
             "value": {"kind": "part_arrival", "part": "叶片",
                       "at": "2026-11-05T08:00:00+08:00", "quantity": 2}},
            {"claim": 2, "row": "D02", "span": "到货前不能开工", "from": "literal",
             "value": {"kind": "part_arrival", "part": "叶片",
                       "at": "2026-11-05T08:00:00+08:00", "blockBefore": True}},
        ],
        "unassigned": [],
        "dsl": {"data": [{"kind": "part_arrival", "part": "叶片",
                          "at": "2026-11-05T08:00:00+08:00",
                          "quantity": 2, "blockBefore": True}]},
    }


def test_two_halves_of_one_patch_are_merged():
    """一句话切成两条 claim，各填一半字段——合起来才是完整的一条补丁。"""
    check = verify.verify("叶片11月5日到2套，到货前不能开工", _part_arrival_product()).get("A6")
    assert check.verdict == "pass", check.detail


def test_same_key_different_value_is_still_a_clash():
    """放宽的是"补不同键"，不是"同键填两个值"。真冲突还是必须拦。"""
    product = _part_arrival_product()
    product["fills"][1]["value"]["quantity"] = 5        # 同一个键，两个不同的数
    check = verify.verify("叶片11月5日到2套，到货前不能开工", product).get("A6")
    assert check.verdict == "fail"


# ── 14. A4 认中文数字：用户说了数，就不能算"推不出" ────────────────────────

def test_chinese_number_in_span_is_read():
    """「只剩两条作业线」里的"两条"就是 2。不认这个，等于把明确的话判成猜的。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "船坏了，只剩两条作业线", "kind": "action"}],
        "fills": [{"claim": 1, "row": "P05", "span": "两条作业线",
                   "from": "literal", "op": "=", "value": 2}],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "最大作业线", "op": "=", "value": 2}]},
    }
    assert "A4" not in _codes(verify.verify("船坏了，只剩两条作业线", product))


def test_chinese_number_with_unit():
    """「预算三百万」→ 3000000，不是 3。带量词的中文数字要把量词算进去。"""
    assert verify._numbers("预算三百万") == [3000000.0]


def test_words_that_only_look_like_numbers():
    """「明天一定别开工」不能读出一个 1。所以只认"数字 + 量词"的组合。"""
    assert verify._numbers("明天一定别开工") == []
    assert verify._numbers("安全第一") == []


def test_value_written_in_chinese_is_still_rejected():
    """反过来：填进 dsl 的值不能是中文写法。「八小时」不是求解器能用的数。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "每天只能干8小时", "kind": "action"}],
        "fills": [{"claim": 1, "row": "P04", "span": "8小时",
                   "from": "literal", "op": "=", "value": "八小时"}],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "每日有效维修上限", "op": "=", "value": "八小时"}]},
    }
    assert "A4" in _codes(verify.verify("每天只能干8小时", product))
