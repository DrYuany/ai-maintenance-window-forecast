"""规则主导版的"理解层"复刻：只读复用 backend，不改它一个字节。

为什么是复刻而不是调 HTTP 接口：
`/api/agent/message` 会建会话、跑求解、写版本库，一次几十秒，
而这里要比的是"同一句话，两边各自读成什么"，不是"谁算得快"。
所以按 `_parse_core` 的真实顺序走一遍，**到调用大模型之前就停**——
那一停的位置，正好就是"规则层能独自走到哪"的边界。

顺序必须与 `llm_parser._parse_core` 一致，否则比出来的东西不可信：
    批量 → 规则层认领 → 非改类守卫 → 旧编译器 → 关门 → （此处停）→ 向量 → 大模型

停在这里还有一个好处：规则侧的结果是**确定性的**，同样的输入永远同样的输出，
可以拿来当基线，不用担心某次网络抖动让对比失真。
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

_STAGE_LABEL = {
    "batch": "层2 批量正则",
    "rules": "层3 规则层直出（43 条正则）",
    "not_change_guard": "层4 关门（非改类守卫）",
    "legacy": "层5 旧规划编译器",
    "gate": "层6 关门（6 组短路）",
    "needs_llm": "前五层都没认领 → 本该交给大模型",
}


@dataclass
class RuleOutcome:
    utterance: str
    stage: str
    route: str
    dsl: dict[str, Any] = field(default_factory=dict)
    compile_errors: list[str] = field(default_factory=list)
    numbers: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        from app.core.dsl import planning_bridge  # 只在需要缩略时导入

        return {
            "side": "rule",
            "utterance": self.utterance,
            "route": self.route,
            "stage": self.stage,
            "stage_label": _STAGE_LABEL.get(self.stage, self.stage),
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


def understand_rule(utterance: str) -> RuleOutcome:
    from app.core.dsl import llm_parser as lp

    text = (utterance or "").strip()
    started = time.monotonic()
    outcome = RuleOutcome(utterance=text, stage="needs_llm", route="not_change")

    dsl: dict[str, Any] | None = None
    if lp._BATCH_RE.search(text):
        outcome.stage, outcome.route = "batch", "batch"
    else:
        ruled = lp.parse_by_rules(text)
        if ruled is not None:
            outcome.stage, outcome.route, dsl = "rules", "dsl", ruled
        elif lp._classify_not_change_only(text) is not None:
            outcome.stage, outcome.route = "not_change_guard", "not_change"
        else:
            bridged = lp.parse_by_legacy(text)
            if bridged is not None:
                outcome.stage, outcome.route, dsl = "legacy", "dsl", bridged
            else:
                classified = lp._classify_non_change(text)
                if classified in {"not_change", "reject", "clarify"}:
                    outcome.stage, outcome.route = "gate", classified
                else:
                    outcome.stage, outcome.route = "needs_llm", "unparsed"

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
