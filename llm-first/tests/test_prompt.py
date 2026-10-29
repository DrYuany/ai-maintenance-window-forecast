"""提示词的结构约束：把设计决策钉成测试，而不是写在注释里靠自觉。

这里的断言分三类：
  1. 与注册表一致 —— 表 A~F 不许手抄漂移（历史上吃过 51/16/4 的亏）
  2. 行号引用正确 —— 跨字段引用、示例里的 row 必须指向对的栏目
  3. 结构不许退化 —— 不许退回"负面清单 + 嘱咐"的旧写法

第 3 类最容易被后来的改动悄悄破坏：加一句"请不要……"很自然，
但每加一句就多一段没人查的文字。测试在这里当刹车。
"""
from __future__ import annotations

import re

import prompt
from app.core.dsl.registry import parameters as param_reg


def test_self_check_clean():
    assert prompt.self_check() == []


def test_table_a_covers_every_writable_param():
    actual = {row["参数名称"] for row in param_reg._rows()
              if param_reg.route_of(row["参数名称"]) == "parameters"}
    listed = {name for _group, names in prompt._TABLE_A_GROUPS for name in names}
    assert listed == actual, "表 A 与注册表的可改集合不一致"


def test_table_a_row_ids_are_contiguous():
    rows = prompt._table_a_rows()
    assert [r[0] for r in rows] == [f"P{i:02d}" for i in range(1, len(rows) + 1)]


def test_cross_field_refs_point_to_the_right_row():
    """跨字段关系里的行号必须指向对的那个栏目。

    回归用例：第一版手写 "≤ P11" 时，P11 其实是"常态浪高上限"而不是"安全风速上限"，
    整组海况阈值的关系全错了一位。改成按名字解析后，这条测试兜住它。
    """
    row_of = {name: row_id for row_id, name, _u, _d in prompt._table_a_rows()}
    domain = {name: d for _r, name, _u, d in prompt._table_a_rows()}

    assert row_of["安全风速上限"] in domain["常态风速上限"]
    assert row_of["安全浪高上限"] in domain["常态浪高上限"]
    assert row_of["常态风速上限"] in domain["安全风速上限"]
    assert row_of["常态浪高上限"] in domain["安全浪高上限"]
    # 反向断言：别把风速和浪高串了
    assert row_of["安全浪高上限"] not in domain["常态风速上限"]
    assert row_of["安全风速上限"] not in domain["常态浪高上限"]


def test_example_rows_match_their_fields():
    """交卷示例里 row 和 field 必须指同一件事（第一版把船租写成了 P13）。"""
    row_of = {name: row_id for row_id, name, _u, _d in prompt._table_a_rows()}
    text = prompt.output_block()
    assert f'"row": "{row_of["船舶日租金"]}", "span": "8万"' in text
    assert f'"row": "{row_of["每日有效维修上限"]}", "span": "10小时"' in text


def test_every_table_has_a_header():
    for fn in (prompt.table_a, prompt.table_b, prompt.table_c,
               prompt.table_d, prompt.table_e, prompt.table_f):
        first = fn().splitlines()[0]
        assert first.startswith("| 行号"), f"{fn.__name__} 缺表头：{first!r}"
        assert fn().splitlines()[1].startswith("|---"), f"{fn.__name__} 缺分隔行"


def test_bricks_framing_present():
    """核心：原语必须被讲成"积木"，不是清单。"""
    text = prompt.build()
    assert "表 A 是拧好的旋钮" in text
    assert "表 C 是积木" in text
    assert "表 A 里没有，但积木能拼的" in text
    assert "这句话能不能被表达" in text


def test_bricks_section_has_enough_worked_examples():
    """至少 6 条"表 A 没有但能拼"的例子。少于这个数，模型学不会组合。"""
    section = prompt.capability_block()
    table_rows = [line for line in section.splitlines() if line.startswith("| ") and "C0" in line or
                  line.startswith("| ") and "O0" in line]
    assert len(table_rows) >= 6, f"只有 {len(table_rows)} 条例子"


def test_boundaries_are_stated():
    """边界必须写出来，否则模型会拿近似的东西顶。"""
    section = prompt.capability_block()
    assert "「全部」这种量词" in section
    assert "量目录里没有的单项" in section
    assert "非线性的东西" in section
    assert "不要拿近似的东西顶" in section


def test_no_negative_list_of_unimplemented():
    """不许再列"未实现，勿用"——那是嘱咐；不在表里才是定义。"""
    text = prompt.build()
    assert "未实现" not in text
    assert "勿用" not in text


def test_no_default_values():
    """不许再投喂默认值：用户没说数字时，模型会抄提示词里的默认值，
    而那个值"有出处"，无出处检查抓不到。"""
    assert "默认值" not in prompt.build()


def test_no_self_reported_confidence():
    """自评不可验（conf 1.0 也能静默丢预算），已删除。"""
    assert "confidence" not in prompt.build()


def test_audit_items_all_present():
    text = prompt.build()
    for i in range(1, 11):
        assert f"| A{i} " in text, f"验收单缺 A{i}"


def test_row_ranges_are_derived():
    """行号范围必须是算出来的，不是手抄的固定串。"""
    ranges = prompt._row_ranges()
    assert ranges.count("~") == 5
    # 表 A 的行数改了，范围要跟着变
    assert f"P{len(prompt._table_a_rows()):02d}" in ranges


def test_never_emits_placeholder_leftovers():
    text = prompt.build()
    assert "@RENT@" not in text and "@HOURS@" not in text
    assert not re.search(r"\{[^}]*\}不符合", text)


def test_kind_enum_is_consistent():
    """`kind` 的取值必须前后一致。

    回归用例：第一版"第一步"里教的是 `kind=ask`/`noise`/`unsure`，
    交卷示例里却写 `kind:"action"`——同一个字段两套枚举，
    模型只能猜，而且没有任何检查会红。
    """
    text = prompt.build()
    enum_rows = {re.match(r"\| `([a-z_]+)` \|", ln).group(1)
                 for ln in text.splitlines()
                 if re.match(r"\| `[a-z_]+` \|", ln)}
    kinds = enum_rows & {"action", "ask", "noise", "unsure"}
    assert kinds == {"action", "ask", "noise", "unsure"}, f"kind 枚举不完整：{kinds}"
    assert "kind=ask" not in text, "旧写法 kind=ask 残留，与表格里的 action 冲突"
    assert '"kind": "action"' in prompt.output_block()


def test_not_change_cannot_swallow_an_action():
    """改一半问一半的句子，必须能被判死。

    回归用例：线上 `把船租降到8万，为什么这么贵` 被判成 not_change，
    8 万那半句静默消失。判据必须写成"not_change 里不许有 action"，
    而不是"记得把两种都切出来"——后者没人查。
    """
    text = prompt.build()
    assert "| A10 不改 |" in text
    assert "`not_change` 时不许有 `action` claim" in text
    not_change_row = [ln for ln in text.splitlines() if ln.startswith("| `not_change`")]
    assert len(not_change_row) == 1, f"route 表里 not_change 行数异常：{not_change_row}"
    assert "不许有" in not_change_row[0] and "`action`" in not_change_row[0]


def test_every_action_claim_needs_a_home():
    """A2 只能对 action 提要求。

    回归用例：第一版写"claim 数 = fills + unassigned 条数"，
    于是任何 not_change 答案（没有 fills）都过不了 A2——
    契约自己把自己锁死了。
    """
    text = prompt.build()
    a2 = [ln for ln in text.splitlines() if ln.startswith("| A2 闭合 |")]
    assert len(a2) == 1
    assert "`action` claim 都有归宿" in a2[0]
    assert "claim 数 = fills" not in text
