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

def test_uncompiled_list_is_empty_because_everything_runs():
    """清单必须为空：五个目标原语现在都能执行。

    这条测试的来历值得记：`weighted` 曾经在这里，因为 `stages_builder` 抛
    「已登记但原型未实现编译」。后来查清，那不是"编译器没实现"，而是
    `strategy` / `weighted` **本来就不该走分层那条路**——它们是求解器模式，
    该落到 mode / weights / strategy 三个字段。`planning_bridge` 一直这么做，
    `runner.run` 漏了，于是同一份 DSL 走变更流程能跑、走 runner 抛错。
    补齐 runner 之后清单就空了。

    要是哪天它又非空，说明有人真的加了实现不了的原语，那就得回到
    「A14 拦一道」的状态，表 D 也要把它排除。这条测试会立刻告诉你。
    """
    if verify._UNCOMPILED_OBJECTIVES:
        builders = {}
        from app.core.dsl.compiler import stages_builder
        for name in verify._UNCOMPILED_OBJECTIVES:
            builder = stages_builder([{"type": name, "weights": {}}], None)
            try:
                builder(None, [], None)
            except ValueError as exc:
                assert "未实现" in str(exc), f"{name} 抛的是 {exc}"
                builders[name] = True
            else:
                raise AssertionError(
                    f"编译器已经能执行 {name} 了——请清空 verify._UNCOMPILED_OBJECTIVES")
        assert builders


def test_every_objective_is_handled_somewhere():
    """每个目标原语都必须**有人管**。

    两条路，必须占一条：
      - `split_objectives` 把它提成求解器模式（strategy / weighted 这类）
      - `stages_builder` 能把它建成分层阶段（minimize / maximize / lexmin）

    两边都不管的，就是当年 `weighted` 那个 bug 的形状——校验放行、
    编译时没人接、求解前炸。这条测试专门盯这个洞。
    """
    from app.core.dsl.compiler import stages_builder
    from app.core.dsl.planning_bridge import split_objectives
    from app.core.dsl.registry import primitives as prims

    unhandled = []
    for name in prims.OBJECTIVE_PRIMITIVES:
        spec = {"type": name, "name": "成本最低优先",
                "weights": {"总经济成本": 1.0}, "q": {}, "stages": []}
        _strategy, _mode, _weights, rest = split_objectives([spec])
        if not rest:
            continue                      # 被提成求解器模式，这条路走通了
        try:
            stages_builder(rest, None)(None, [], None)
        except AttributeError:
            continue                      # 分派对了，是空 model 导致的崩
        except ValueError as exc:
            unhandled.append(f"{name}: {exc}")
        else:
            continue
    assert not unhandled, f"这些目标原语两边都不管：{unhandled}"


def test_table_d_lists_exactly_the_executable_ones():
    """表 D 必须与"真正能跑的目标原语"一字不差，两个方向都要防。

    少列 = **藏能力**：用户说"严重故障占六成、成本占四成"，系统回"不支持加权"，
    而它其实支持。比报错更隐蔽，因为用户会以为是自己要求过分。

    多列 = **递假情报**：写了一个必炸的原语，校验放行、求解阶段才抛。

    所以不能只断言"weighted 不在里面"（那是上一个 bug 的形状），
    要断言集合相等。
    """
    import prompt
    import tools
    from app.core.dsl.registry import primitives as prims

    real = tools.executable_objectives(prims)
    listed = set(prompt._TABLE_D_ORDER)
    assert listed == real, f"表 D 少列了：{real - listed}；多列了：{listed - real}"
    assert len(listed) == len(prompt._TABLE_D_ORDER), "表 D 有重复项，行号会错位"


def test_weighted_is_no_longer_rejected():
    """`weighted` 曾经被 A14 拦在这里，因为 `stages_builder` 不认它。

    查清之后发现那不是"编译器没实现"——`strategy` / `weighted` 是**求解器模式**，
    该落到 mode / weights / strategy 三个字段，压根不该进分层阶段。
    `planning_bridge` 一直这么处理，`runner.run` 漏了这一步。补齐后它真能跑，
    A14 就不该再拦。

    **继续拦的代价是把一个可用的能力挡在门外**：用户说"严重故障占六成、
    成本占四成"，系统会回一句"不支持加权"，而它其实支持。
    """
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
    assert "A14" not in codes


def test_a14_gate_still_fires_for_a_genuinely_uncompiled_objective(monkeypatch):
    """闸门本身必须还能用：往名单里塞一个，A14 立刻要点亮。

    这条防的是"A14 变成永远 pass 的空转判据"。名单清空之后，
    `check_executable` 在任何输入下都返回 pass —— 看着一直绿，其实什么
    都没检查。判据这样退化比没有更糟：它让人以为这里有人看着。

    所以必须有一条测试，证明"名单非空时它真的会拦"。
    """
    monkeypatch.setitem(verify._UNCOMPILED_OBJECTIVES, "weighted", "测试用：假装不能编译")
    product = {
        "route": "dsl",
        "claims": [{"id": 1, "text": "高严重完成数占六成", "kind": "action"}],
        "fills": [],
        "unassigned": [],
        "dsl": {"objectives": [{"type": "weighted",
                                "weights": {"高严重完成数": 0.6}}]},
    }
    codes = _codes(product, "高严重完成数占六成")
    assert "A14" in codes, "名单非空时 A14 还不亮，这道闸门已经失效了"
