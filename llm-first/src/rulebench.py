"""规则主导版的"理解层"复刻：只读复用 backend，不改它一个字节。

为什么是复刻而不是调 HTTP 接口：
`/api/agent/message` 会建会话、跑求解、写版本库，一次几十秒，
而这里要比的是"同一句话，两边各自读成什么"，不是"谁算得快"。

**为什么不能用 `_parse_core` 一句话概括"规则层"**——这里踩过两次坑，都写下来：

1. 第一版直接拿整句喂 `_parse_core`，得到"只认出一半改动"。
   但产品的 `parse()` 第一步是 `_split_compounds` 按逗号拆段、逐段解析、全部命中才合并。
   跳过拆段，等于拿一个产品不会走的输入去论证它不是。
2. 第二版补上拆段，发现"人工单价涨到200"规则层认得出，
   "任务003最晚11月9日修完"规则层（批量/正则/守卫/桥接/门控）**一层都认不出**，
   于是整句退回大模型——而页面上那句话两边改动都做出来了，是大模型做的。
   结论：**规则主导版在这句话上同样是 LLM 主导，只是用旧提示词。**
   这条比任何"命中率"都重要，它说明两套产品的真实差别不在"用不用模型"，
   而在"模型交出来的东西有没有人验收"。

所以这里的分层是把产品真实入口摊开，逐段标注，到**调用大模型之前**就停：

    批量场景 → 拆复合句 → 逐段（规则 → 非改类守卫 → 旧桥接 → 门控）
             → 有一段没认下来 = 整句退给大模型（此处停）

停在这里还有两个好处：规则侧的结果是**确定性的**，可以当基线；
以及"规则层到哪一步为止"这个边界本身就是结论的一部分。
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import paths  # noqa: E402,F401

import contract  # noqa: E402
import verify  # noqa: E402

# 每一段/整句落在哪一层。"needs_llm" 不是失败，是出口——
# 产品的出口就是大模型，这里只是走到门口为止。
_STAGE_LABEL = {
    "batch": "批量场景正则（一句话多个场景）",
    "compound": "拆段后逐段命中规则，合并（纯规则能做到）",
    "rules": "规则层直出（43 条正则）",
    "not_change_guard": "非改类守卫（只问不改）",
    "legacy": "旧规划语言桥接",
    "gate": "意图门控（确定性判死）",
    "needs_llm": "规则层读不出来 → 产品在这里交给大模型",
}
_PART_LABEL = {
    "batch": "批量",
    "rules": "正则",
    "not_change_guard": "守卫",
    "legacy": "旧桥接",
    "gate": "门控",
    "needs_llm": "读不出",
}


@dataclass
class Part:
    """复合句拆开的一段，各自落在哪一层。"""

    text: str
    stage: str
    route: str

    @property
    def claimed(self) -> bool:
        return self.route == "dsl"

    @property
    def label(self) -> str:
        return _PART_LABEL.get(self.stage, self.stage)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "stage": self.stage,
            "label": self.label,
            "route": self.route,
            "claimed": self.claimed,
        }


@dataclass
class RuleOutcome:
    utterance: str
    stage: str = "needs_llm"
    route: str = "unparsed"
    dsl: dict[str, Any] = field(default_factory=dict)
    compile_errors: list[str] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    parts: list[Part] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fallback: dict[str, Any] | None = None

    @property
    def llm_needed(self) -> bool:
        return self.stage == "needs_llm"

    def as_dict(self) -> dict[str, Any]:
        from app.core.dsl import planning_bridge  # 只在需要缩略时导入

        return {
            "side": "rule",
            "utterance": self.utterance,
            "route": self.route,
            "stage": self.stage,
            "stage_label": _STAGE_LABEL.get(self.stage, self.stage),
            "parts": [p.as_dict() for p in self.parts],
            "notes": self.notes,
            "llm_needed": self.llm_needed,
            "fallback": self.fallback,
            "dsl": self.dsl,
            "summary": _summarize(self.dsl, planning_bridge),
            "numbers": self.numbers,
            "compile_errors": self.compile_errors,
            "latency_ms": self.latency_ms,
            "has_work_record": False,
        }


def _summarize(dsl: dict[str, Any], bridge) -> list[str]:
    """把 DSL 翻成几句人话，方便和右边对照着看。"""
    if not dsl:
        return []
    lines: list[str] = []
    for item in dsl.get("parameters") or []:
        lines.append(f"参数 {item.get('field')} {item.get('op', '=')} {item.get('value')}")
    for item in dsl.get("constraints") or []:
        kind = item.get("type")
        rest = {k: v for k, v in item.items() if k != "type"}
        body = "、".join(f"{k}={v}" for k, v in rest.items())
        lines.append(f"约束 {kind}（{body}）")
    for item in dsl.get("objectives") or []:
        kind = item.get("type")
        rest = {k: v for k, v in item.items() if k != "type"}
        body = "、".join(f"{k}={v}" for k, v in rest.items())
        lines.append(f"目标 {kind}（{body}）")
    for item in dsl.get("data") or []:
        kind = item.get("kind")
        rest = {k: v for k, v in item.items() if k != "kind"}
        body = "、".join(f"{k}={v}" for k, v in rest.items())
        lines.append(f"数据 {kind}（{body}）")
    return lines


def _rule_layer(text: str) -> tuple[str, str, dict[str, Any]]:
    """单段过一遍规则层，顺序与 `_parse_core` 完全一致，遇到大模型就返回空。"""
    from app.core.dsl import llm_parser as lp

    if lp._BATCH_RE.search(text):
        return "batch", "batch", {}
    ruled = lp.parse_by_rules(text)
    if ruled is not None:
        return "rules", "dsl", ruled
    if lp._classify_not_change_only(text) is not None:
        return "not_change_guard", "not_change", {}
    bridged = lp.parse_by_legacy(text)
    if bridged is not None:
        return "legacy", "dsl", bridged
    classified = lp._classify_non_change(text)
    if classified in {"not_change", "reject", "clarify"}:
        return "gate", classified, {}
    return "needs_llm", "unparsed", {}


def _planning_fallback(text: str) -> dict[str, Any] | None:
    """旧规划编译器（`is_planning_request` 判真时才轮得到它）。

    它在真实链路里排在大模型**之后**：DSL 解析失败才退回来。
    这里提前算一份，只为回答"退到最后一层还剩什么"，不代表产品的主路径。
    """
    from app.core.dsl.planning_bridge import request_to_dsl
    from app.core.planning_intent import PlanningIntentCompiler

    compiler = PlanningIntentCompiler()
    if not compiler.is_planning_request(text):
        return None
    intent = compiler.compile_detailed(text)
    dsl = contract.as_dsl(contract.normalize({"dsl": request_to_dsl(intent.request)}))
    from app.core.dsl import planning_bridge

    return {
        "summary": _summarize(dsl, planning_bridge),
        "numbers": verify.audit_numbers(text, dsl),
        "dsl": dsl,
        "errors": list(getattr(intent, "errors", []) or []),
    }


def understand_rule(utterance: str) -> RuleOutcome:
    """按 `agent_runtime` 真实入口走一遍，到调用大模型之前就停。"""
    from app.core.dsl import llm_parser as lp
    from app.core.model_change import is_scenario_batch

    text = (utterance or "").strip()
    started = time.monotonic()
    outcome = RuleOutcome(utterance=text)
    dsl: dict[str, Any] = {}

    if is_scenario_batch(text):
        outcome.stage, outcome.route = "batch", "batch"
        outcome.notes.append("产品会按场景拆成多份请求逐个预览（这一步是纯规则）。")
    else:
        segments = lp._split_compounds(text)
        if segments:
            outcome.parts = [Part(s, *_rule_layer(s)[:2]) for s in segments]
            hit = [p for p in outcome.parts if p.claimed]
            if len(hit) == len(outcome.parts):
                merged = lp._merge_dsls([_rule_layer(p.text)[2] for p in outcome.parts])
                outcome.stage, outcome.route, dsl = "compound", "dsl", merged
                outcome.notes.append(
                    f"按逗号拆成 {len(outcome.parts)} 段，每段规则层都认下，合并成一份 DSL——"
                    "这一句是纯规则做到的，没经过大模型。"
                )
            else:
                outcome.stage, outcome.route = "needs_llm", "unparsed"
                miss = [p for p in outcome.parts if not p.claimed]
                outcome.notes.append(
                    f"按逗号拆成 {len(outcome.parts)} 段，规则层只认下 {len(hit)} 段；"
                    f"『{miss[0].text}』规则层读不出来 → 整句退回大模型"
                    "（`parse()` 要求每段都命中才合并）。"
                )
        else:
            outcome.stage, outcome.route, dsl = _rule_layer(text)

    if outcome.llm_needed:
        fallback = _planning_fallback(text)
        outcome.fallback = fallback
        if fallback is not None:
            outcome.notes.append(
                "这句话 `is_planning_request` 判真，所以产品先让大模型出 DSL；"
                "大模型也给不出时才退回旧规划编译器——右下的「最后兜底」就是它读到的，"
                "不代表主路径。"
            )
        else:
            outcome.notes.append("这句话不进规划请求分支，产品会交给大模型对话/解析。")

    clean = contract.normalize({"dsl": dsl or {}})
    outcome.dsl = contract.as_dsl(clean)
    outcome.numbers = verify.audit_numbers(text, outcome.dsl)

    if outcome.dsl:
        try:
            from app.core.dsl.validator import validate_dsl

            outcome.compile_errors = list(validate_dsl(outcome.dsl))
        except Exception as exc:
            outcome.compile_errors = [f"校验器不可用：{type(exc).__name__}: {exc}"]

    outcome.latency_ms = int((time.monotonic() - started) * 1000)
    return outcome
