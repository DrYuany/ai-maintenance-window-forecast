"""规则主导版理解层的复刻必须与产品真实入口一致。

这个文件存在的理由，是踩过的两次坑：

  第一次：拿整句直接喂 `_parse_core`，得到"规则只认一半"，
          于是写了一整篇"规则侧丢 3、9、11"的对照——
          但产品第一步就按逗号拆段，测的根本不是它走的路。
  第二次：发现"任务003最晚11月9日修完"规则层一层都认不出，
          整句会退回大模型；而页面上那句话它两个改动都做了，
          说明**规则主导版在这句话上也是大模型做的**。

所以这里的断言盯的不是"命中率"，而是"边界在哪"：
  - 纯规则能做到的，必须报 compound，不能含糊成 needs_llm；
  - 做不到的，必须老实说 needs_llm，并且指出是哪一段没认下来；
  - 兜底（旧规划编译器）只作为参考挂出来，不许冒充主路径。
"""
from __future__ import annotations

import rulebench


def test_compound_all_claimed_by_rules():
    """两段都能被规则层认下 → 纯规则合并，不该惊动大模型。"""
    out = rulebench.understand_rule("任务001和任务002不要同一天做，钱不超过300万")
    assert out.stage == "compound"
    assert out.route == "dsl"
    assert out.llm_needed is False
    assert [p.claimed for p in out.parts] == [True, True]
    kinds = {item["type"] for item in out.dsl.get("constraints", [])}
    assert {"co_occur", "budget"} <= kinds


def test_compound_partial_falls_to_llm_and_says_which_part():
    """一段认不下 → 整句退模型；必须点名是哪一段，不能只说"没认出"。"""
    out = rulebench.understand_rule("人工单价涨到200，任务003最晚11月9日修完")
    assert out.stage == "needs_llm"
    assert out.llm_needed is True
    assert out.dsl == {}  # 没合并出半份 DSL，不许把一半当整份交上去
    claimed = [p.text for p in out.parts if p.claimed]
    missed = [p.text for p in out.parts if not p.claimed]
    assert claimed == ["人工单价涨到200"]
    assert missed == ["任务003最晚11月9日修完"]
    assert any("任务003最晚11月9日" in note for note in out.notes)


def test_single_paraphrase_not_claimed_by_rules():
    """"降到"这种口语改写规则层接不住——这是产品要靠模型的直接证据。

    如果哪天 backend 补上了这条正则，这个测试会红，那是好事：
    说明规则侧覆盖面真的变宽了，对照表该重跑。
    """
    out = rulebench.understand_rule("把船租降到8万")
    assert out.stage == "needs_llm"
    assert out.dsl == {}
    assert out.parts == []  # 单句不拆段


def test_fallback_is_marked_and_not_primary():
    """兜底必须带自己的丢数，且只在 needs_llm 时出现。"""
    out = rulebench.understand_rule("人工单价涨到200，任务003最晚11月9日修完")
    assert out.fallback is not None
    assert out.fallback["numbers"]["missing"], "兜底读不全，丢数不能是空"
    assert out.fallback["dsl"] != out.dsl

    clean = rulebench.understand_rule("任务001和任务002不要同一天做，钱不超过300万")
    assert clean.fallback is None, "纯规则认下的句子不该挂兜底"


def test_numbers_audit_only_when_dsl_exists():
    """规则层没交 DSL 时不要报"丢了全部数字"，那是噪声。"""
    out = rulebench.understand_rule("明天风大，先别出海")
    assert out.dsl == {}
    assert out.numbers["produced"] == []
