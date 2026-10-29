"""把 `backend/eval_cases/error_taxonomy.py` 那 18 条历史案例接进本版回归。

那 18 条是**从填表自由度推导出来的 20 类错法**里的真实样本（不是随手举的例子）。
它们原本用来测规则主导版的事后审计器；这里换个问法：

> 同一份错产物，LLM 主导版的 A1~A13 抓得到吗？

这份文件的价值不在"全抓到了"，而在**把抓不到的三类标出来**。
一份只在成功的判据清单，用起来比没有更危险——因为它会让人以为已经全检过了。

已知抓不到的三类（下面有对应的 `test_known_gap_*`）：

| 类 | 是什么 | 为什么抓不到 |
|---|---|---|
| E4.3 范围顶替 | 「只看风机003」被写成 19 条 `exclude` | 每条 exclude 都能自证出处，组合起来才是错的；要判得先定义"范围限定"该长什么样 |
| E5.4 值域越界 | `安全风速上限=1400` | 值域表 15 项里只有 2 项是机器可解析的区间，其余是散文或"引用另一字段" |
| E4.2 张冠李戴 | 「风速上限」被归到海况之外 | 靠句式表判，误报率高于命中率；判据化会把假警报引进主链 |
"""
from __future__ import annotations

import verify

CTX = None            # 这批案例不涉及相对时间，不传基准


def _codes(product: dict, text: str) -> set[str]:
    report = verify.verify(text, product, CTX)
    return {c.code for c in report.checks if c.verdict == "fail"}


def _fill(claim: int, row: str, span: str, value, source: str = "literal", **extra) -> dict:
    return dict({"claim": claim, "row": row, "span": span, "from": source, "value": value}, **extra)


# ── 抓得到的 ────────────────────────────────────────────────────────────────

def test_e0_2_shape_error_is_caught():
    """参数写成字典而不是数组：下游全部读不出来，必须在形状这一层就停。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "每天只能干8小时", "kind": "action"}],
        "fills": [_fill(1, "P04", "8小时", 8)],
        "unassigned": [],
        "dsl": {"parameters": {"每日有效维修上限": 8}},
    }
    assert "A9" in _codes(product, "每天只能干8小时")


def test_e5_1_chinese_number_is_caught():
    """值写成"八小时"：literal 的出处对不上。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "每天只能干8小时", "kind": "action"}],
        "fills": [_fill(1, "P04", "8小时", "八小时")],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "每日有效维修上限", "op": "=", "value": "八小时"}]},
    }
    assert "A4" in _codes(product, "每天只能干8小时")


def test_e5_1_relative_day_in_date_field_is_caught():
    """`day: "明天"`——没编造，但也没落地。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "明天风大，先别出海", "kind": "action"}],
        "fills": [_fill(1, "D04", "明天风大", {"day": "明天", "state": "不可作业"}, "default",
                        why="原文未给具体日期")],
        "unassigned": [],
        "dsl": {"data": [{"kind": "weather", "day": "明天", "state": "不可作业"}]},
    }
    codes = _codes(product, "明天风大，先别出海")
    assert "A9" in codes and "A4" in codes


def test_e5_3_magnitude_error_is_caught():
    """原文 14，DSL 写 1400：span 里没有这个数。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "安全风速上限放宽到14米每秒", "kind": "action"}],
        "fills": [_fill(1, "P14", "14米每秒", 1400)],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "安全风速上限", "op": "=", "value": 1400}]},
    }
    assert "A4" in _codes(product, "安全风速上限放宽到14米每秒")


def test_e5_5_direction_reversal_is_caught():
    """「不超过」写成 `>=`——本轮新增的 A11 就是为这一类。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "钱不超过300万", "kind": "action"}],
        "fills": [_fill(1, "C10", "不超过300万", 3000000)],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "budget", "quantity": "现金支出",
                                 "op": ">=", "value": 3000000}]},
    }
    assert "A11" in _codes(product, "钱不超过300万")


def test_e4_1_invented_parameter_is_caught():
    """「候选部件」不在落点表里——填了个执行侧不认的栏目。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "只排部件01和部件02", "kind": "action"}],
        "fills": [_fill(1, "P04", "部件01和部件02", ["部件01", "部件02"])],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "候选部件", "op": "=", "value": ["部件01", "部件02"]}]},
    }
    assert "A8" in _codes(product, "只排部件01和部件02")


def test_e4_1_invented_primitive_is_caught():
    """编造原语 `range_limit`：行号对不上任何一块积木。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "把工期限制在某个范围", "kind": "action"}],
        "fills": [_fill(1, "C11", "工期限制在某个范围", 30, "default", why="原文没给具体范围")],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "range_limit", "value": 30}]},
    }
    assert "A8" in _codes(product, "把工期限制在某个范围")


def test_e2_2_two_values_for_one_field_is_caught():
    """同一行两个值：要么是模型没想清楚，要么是两个诉求被塞进一行。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "船租涨两成", "kind": "action"},
                   {"id": 2, "text": "人工单价也涨两成", "kind": "action"}],
        "fills": [_fill(1, "P13", "船租涨两成", 14400),
                  _fill(2, "P13", "人工单价也涨两成", 15000)],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "船舶日租金", "op": "=", "value": 14400},
                               {"field": "船舶日租金", "op": "=", "value": 15000}]},
    }
    assert "A6" in _codes(product, "船租涨两成，人工单价也涨两成")


def test_e4_3_all_quantifier_downgrade_is_caught():
    """「必须全修完」写成「至少 1 个」——本轮新增的 A13。"""
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "高风险的必须全修", "kind": "action"}],
        "fills": [_fill(1, "C06", "高风险的必须全修", "高", "enum")],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "cardinality", "filter": {"severity": "高"},
                                 "op": ">=", "value": 1}]},
    }
    assert "A13" in _codes(product, "成本压到最低，但高风险的必须全修")


def test_e1_2_question_treated_as_command_is_caught_indirectly():
    """问句被当成指令去改策略。

    没有一条判据专门判"该不该改"，但 route=dsl 却**没有任何 fills** 会被 A10 拦下——
    这是间接抓到的：模型改了一件事，却说不清依据在哪。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "经济优先和风险优先差多少", "kind": "ask", "about": "diff"}],
        "fills": [],
        "unassigned": [],
        "dsl": {"objectives": [{"type": "strategy", "name": "高风险优先抢修"}]},
    }
    codes = _codes(product, "经济优先和风险优先差多少")
    assert "A10" in codes or "A8" in codes


# ── 抓不到的：标出来，不假装有 ──────────────────────────────────────────────

def test_known_gap_scope_substitution_is_not_caught():
    """E4.3 范围顶替：`只看风机003` 被写成 19 条 exclude。

    每条 exclude 单看都能自证出处（span 抄的是任务编号），组合起来才是错的。
    要判它，得先把「范围限定」定义成原语——那是补能力，不是补判据。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "只看风机003和风机004", "kind": "action"}],
        "fills": [_fill(1, "C02", "只看风机003和风机004", f"任务{i:03d}", "enum")
                  for i in range(1, 20)[:1]],
        "unassigned": [],
        "dsl": {"constraints": [{"type": "exclude", "task": f"任务{i:03d}"}
                                for i in range(1, 20)]},
    }
    codes = _codes(product, "只看风机003和风机004")
    assert "A13" not in codes and "A11" not in codes, \
        "这条靠判据抓不到；如果哪天抓到了，是补了能力，回来把它改成正面断言"


def test_known_gap_value_domain_is_not_caught():
    """E5.4 值域越界：安全风速上限=1400。

    值域表 15 项里只有 2 项是机器可解析的区间，其余是散文或"引用另一字段"。
    所以这条只能靠 A4 的出处核对（写了 1400 但 span 里是 14），不是靠值域。
    如果哪天补了值域表，这个测试会失败，提醒把它改成正面断言。
    """
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "安全风速上限放宽到1400米每秒", "kind": "action"}],
        "fills": [_fill(1, "P14", "1400米每秒", 1400)],
        "unassigned": [],
        "dsl": {"parameters": [{"field": "安全风速上限", "op": "=", "value": 1400}]},
    }
    codes = _codes(product, "安全风速上限放宽到1400米每秒")
    assert "A4" not in codes, "出处能对上时，值域这一层现在没有判据"


# ── A14：登记了但执行不了的，要能被证伪 ────────────────────────────────────

def test_uncompiled_list_matches_compiler_reality():
    """A14 里列的"执行时必抛错"必须**真的**抛错。

    这条测试是给未来的自己留的：编译器哪天实现了 weighted，
    它会失败，提醒把 `_UNCOMPILED_OBJECTIVES` 清空、并把表 D 里的项加回去。
    不这么写，那张表就会变成一份靠记性维护的清单——而记性从来不可靠。
    """
    from app.core.dsl.compiler import stages_builder

    assert verify._UNCOMPILED_OBJECTIVES, "清单空了就把这条测试删掉"
    for name in verify._UNCOMPILED_OBJECTIVES:
        builder = stages_builder([{"type": name, "weights": {}}], None)
        try:
            # 传空 choices：内层先展开候选，再逐条建表达式；weighted 在展开之后、
            # 建表达式之前就抛，所以空列表足够走到那个分支。
            builder(None, [], None)
        except ValueError as exc:
            assert "未实现" in str(exc), f"{name} 抛的是 {exc}"
        else:                                    # 没抛 = 编译器实现了
            raise AssertionError(
                f"编译器已经能执行 {name} 了——请清空 verify._UNCOMPILED_OBJECTIVES，"
                f"并把它加回 prompt._TABLE_D_ORDER")


def test_table_d_only_lists_executable():
    """表 D 只列能执行的。列出来又写"别用"是嘱咐，不在表里才是定义。"""
    import prompt

    assert "weighted" not in prompt._TABLE_D_ORDER


def test_weighted_is_rejected_before_it_reaches_the_solver():
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "高严重完成数占六成，成本占四成", "kind": "action"}],
        "fills": [{"claim": 1, "row": "O02", "span": "高严重完成数占六成",
                   "from": "enum", "value": "高严重完成数"}],
        "unassigned": [],
        "dsl": {"objectives": [{"type": "weighted",
                                "weights": {"高严重完成数": 0.6, "总经济成本": 0.4}}]},
    }
    codes = _codes(product, "高严重完成数占六成，成本占四成")
    assert "A14" in codes
