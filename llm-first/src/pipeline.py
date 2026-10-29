"""编排：护栏 → 缓存 → 模型 → 验收 → 退回重试 → 澄清。

和上一版方案的五段式相比，这里只有四段，少的是"快路"：
实测向量层的 0.95 阈值只对逐字重复生效（221 条命中 14 条，全部逐字相同），
而逐字重复本来就有缓存。所以快路 = 带额外步骤的缓存，删掉。

也**没有规则兜底**。这是有意的：
  1. 兜底会让"LLM 主导版"变成混合体，两个产品就比不出理解层的差异；
  2. 上一版实测 33 条规则的真实覆盖就是 68 条直出句子，接不住复合句——
     拿它兜底等于把"静默丢一半"重新放回来；
  3. 验收不通过时，正确的动作是承认"我没读懂"，不是换一套更弱的办法硬答。

失败时交出的东西比成功时更有信息量：把 A1~A10 哪几条没过、原文哪几段没被覆盖、
哪个数字丢了，全部原样摆出来让人看。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import client
import context as session
import contract
import guardrail
import prompt
import segment
import verify


class _Cache:
    def __init__(self, size: int = 64) -> None:
        self.size = size
        self.data: dict[str, "Outcome"] = {}

    def get(self, key: str) -> "Outcome | None":
        return self.data.get(key)

    def put(self, key: str, value: "Outcome") -> None:
        if len(self.data) >= self.size:
            self.data.pop(next(iter(self.data)))
        self.data[key] = value


_CACHE = _Cache()


@dataclass
class Outcome:
    utterance: str
    route: str
    product: dict[str, Any] = field(default_factory=dict)
    report: verify.Report | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    numbers: dict[str, Any] = field(default_factory=dict)
    guardrail: guardrail.Hit | None = None
    compile_errors: list[str] = field(default_factory=list)
    latency_ms: int = 0
    model_ms: int = 0
    attempts: int = 0
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    cached: bool = False
    model_route: str | None = None
    today: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "side": "llm",
            "utterance": self.utterance,
            "route": self.route,
            "model_route": self.model_route,
            "segments": self.report.segments if self.report else segment.segments(self.utterance),
            "claims": (self.product or {}).get("claims", []),
            "fills": (self.product or {}).get("fills", []),
            "unassigned": (self.product or {}).get("unassigned", []),
            "dsl": contract.as_dsl(self.product) if self.product else {},
            "numbers": self.numbers,
            "compile_errors": self.compile_errors,
            "guardrail": self.guardrail.as_dict() if self.guardrail else None,
            "report": self.report.as_dict() if self.report else None,
            "latency_ms": self.latency_ms,
            "model_ms": self.model_ms,
            "attempts": self.attempts,
            "model": self.model,
            "usage": self.usage,
            "error": self.error,
            "cached": self.cached,
            "today": self.today,
        }


def _compile_check(dsl: dict[str, Any], data=None) -> list[str]:
    if not dsl:
        return []
    try:
        from app.core.dsl.validator import validate_dsl
    except Exception as exc:  # 执行侧没装起来时，不假装通过
        return [f"无法加载校验器：{type(exc).__name__}: {exc}"]
    try:
        return list(validate_dsl(dsl, data))
    except Exception as exc:
        return [f"校验器异常：{type(exc).__name__}: {exc}"]


def _repair_message(utterance: str, raw: dict[str, Any],
                    report: verify.Report,
                    compile_errors: list[str] | None = None) -> str:
    """退回重试。只说"哪条判据没过、为什么"，不替它改答案。

    这和编译器报错是同一件事：给位置和原因，不给结论。
    一旦把"正确答案"塞进去，验收就变成了抄写。

    编译器的报错也一并给它——否则 A1~A10 全过、编译器一句
    "非法运算符 '='" 就给退了，模型永远不知道自己错在哪。
    """
    lines = [
        f"原话：{utterance}",
        "",
        "你上一版交的产物没过验收，逐条原因如下：",
        "",
    ]
    for check in report.failures:
        lines.append(f"- {check.code} {check.name}：{check.detail}")
        for item in check.items[:5]:
            lines.append(f"    · {item}")
    for error in (compile_errors or [])[:6]:
        lines.append(f"- 编译不过：{error}")
    lines += [
        "",
        "请按这些原因重交一次。只修被指出的问题，其他不变。",
        "如果某条诉求确实做不到，就写进 unassigned 并说明理由——",
        "说做不到不算失败，编一个像的才算。",
        "",
        "**「能过验收」不等于「表达对了」。** 如果为了过 A1~A12 换成一个意思已经变了的写法"
        "（比如把「只排风机003」写成「完工任务数==1」），那还是错的，只是这次没人拦得住。",
        "拿不准就退回 clarify：问一句，比猜错一版便宜。",
    ]
    return "\n".join(lines)


def _finalize(outcome: Outcome, reason: str = "") -> Outcome:
    product = outcome.product or {}
    if outcome.report is None:
        return outcome

    dsl = contract.as_dsl(product)
    outcome.compile_errors = _compile_check(dsl)

    out = dict(outcome.product)
    blocked = outcome.report.blocked or bool(outcome.compile_errors)
    if blocked:
        out["route"] = "clarify"
        outcome.model_route = product.get("route")
    outcome.product = out
    outcome.route = out.get("route") or "clarify"
    outcome.numbers = verify.audit_numbers(outcome.utterance, dsl)
    if blocked:
        parts = [f"{c.code} {c.detail}" for c in outcome.report.failures]
        parts += [f"编译不过：{e}" for e in outcome.compile_errors[:2]]
        outcome.error = reason or f"验收未通过，已退回澄清：{'；'.join(parts)}"
    return outcome


def understand(utterance: str, *, use_cache: bool = True,
               allow_repair: bool = True,
               ctx: "session.Context | None" = None) -> Outcome:
    text = (utterance or "").strip()
    started = time.monotonic()
    ctx = ctx or session.default()

    hit = guardrail.check(text)
    if hit is not None:
        return Outcome(utterance=text, route="reject", guardrail=hit,
                       latency_ms=int((time.monotonic() - started) * 1000),
                       today=ctx.iso,
                       error=f"护栏拦下：{hit.category}（{hit.evidence}）")

    # 缓存键带日期：同一句"明天"在 10-30 和 11-01 该落成不同日期，
    # 只按文本做键会把昨天的答案今天照发。
    key = f"{ctx.iso}|{text}"
    if use_cache:
        cached = _CACHE.get(key)
        if cached is not None:
            echo = Outcome(**{**cached.__dict__, "cached": True})
            # 缓存命中的耗时是"这一次"的，不是上一轮模型跑的时间——
            # 否则页面上会显示 1.4 秒，让人以为又调了一次模型。
            echo.latency_ms = int((time.monotonic() - started) * 1000)
            return echo

    if not client.configured():
        return Outcome(utterance=text, route="clarify",
                       latency_ms=int((time.monotonic() - started) * 1000),
                       today=ctx.iso,
                       error="未配置 DEEPSEEK_API_KEY，模型层不可用")

    system = prompt.build(ctx)
    outcome = Outcome(utterance=text, route="clarify", today=ctx.iso)
    report: verify.Report | None = None
    raw: dict[str, Any] = {}

    rounds = 2 if allow_repair else 1
    message = text
    for attempt in range(1, rounds + 1):
        try:
            reply = client.chat(system, message)
        except client.LLMUnavailable as exc:
            outcome.error = str(exc)
            outcome.latency_ms = int((time.monotonic() - started) * 1000)
            outcome.attempts = attempt
            return outcome

        outcome.model = reply.model
        outcome.model_ms += reply.latency_ms
        outcome.attempts = attempt
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            outcome.usage[key] = outcome.usage.get(key, 0) + (reply.usage.get(key) or 0)

        try:
            raw = contract.load(reply.text)
        except ValueError as exc:
            outcome.error = f"产物不是合法 JSON：{exc}"
            if attempt == rounds:
                break
            message = f"你上一版返回的不是合法 JSON（{exc}）。请只返回一个 JSON 对象。原话：{text}"
            continue

        product = contract.normalize(raw)
        report = verify.verify(text, raw, ctx)
        outcome.raw, outcome.product, outcome.report = raw, product, report
        errors = _compile_check(contract.as_dsl(product))

        if not report.blocked and not errors:
            break
        if attempt < rounds:
            message = _repair_message(text, raw, report, errors)

    if report is None:
        outcome.latency_ms = int((time.monotonic() - started) * 1000)
        return outcome

    outcome = _finalize(outcome)
    outcome.latency_ms = int((time.monotonic() - started) * 1000)
    # 只缓存"真的能用"的结果。把澄清也缓存起来，会让改了 prompt 之后
    # 同一句话永远拿不到新结果，调试时会以为模型没变。
    if not outcome.error and outcome.route != "clarify":
        _CACHE.put(key, outcome)
    return outcome
