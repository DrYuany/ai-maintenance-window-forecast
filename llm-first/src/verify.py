"""验收器：A1~A10。

判据全部只查"产物之间对不对得上"，不查"模型心里怎么想"。
四态而不是两态：`pass` / `fail` / `unclear` / `na`。
`unclear` 只记日志、不拦截——把"我判不了"说成"你错了"，比漏报更伤：
一条假警报就足以让人关掉整个验收器（上一版审计器就吃过这个亏）。

刻意不做的事：
  - 不判"填得对不对"（那要靠求解器与业务规则），只判"填的能不能对上原文"
  - 不判"答案好不好"，只判"答案有没有依据"
  - 不因为某个字段没见过就判错，先归一到已知键名再看
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Literal

import contract
import context as session
import rows as row_book
import segment

Verdict = Literal["pass", "fail", "unclear", "na"]

_CN_UNIT = {"万": 1e4, "亿": 1e8, "千": 1e3}
_NUM = re.compile(r"(-?\d+(?:\.\d+)?)\s*(亿|万|千)?")
_DATE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


@dataclass
class Check:
    code: str
    name: str
    verdict: Verdict
    detail: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name, "verdict": self.verdict,
                "detail": self.detail, "items": self.items}


@dataclass
class Report:
    checks: list[Check]
    segments: list[str]

    @property
    def blocked(self) -> bool:
        return any(c.verdict == "fail" for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.verdict == "fail"]

    def get(self, code: str) -> Check | None:
        for check in self.checks:
            if check.code == code:
                return check
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "segments": self.segments,
            "checks": [c.as_dict() for c in self.checks],
            "summary": f"{sum(1 for c in self.checks if c.verdict == 'pass')} 过 / "
                       f"{sum(1 for c in self.checks if c.verdict == 'fail')} 退 / "
                       f"{sum(1 for c in self.checks if c.verdict == 'unclear')} 存疑",
        }


# ── 基础工具 ────────────────────────────────────────────────────────────────

def _locate(needle: str, hay: str) -> int | None:
    """找 needle 在 hay 里的可证程度：0=逐字，1=去空白标点后，None=找不到。"""
    if not needle:
        return None
    if needle in hay:
        return 0
    if segment.norm(needle) and segment.norm(needle) in segment.norm(hay):
        return 1
    return None


def _numbers(text: str) -> list[float]:
    out: list[float] = []
    for raw, unit in _NUM.findall(text or ""):
        try:
            value = float(raw)
        except ValueError:
            continue
        if unit:
            value *= _CN_UNIT[unit]
        out.append(value)
    return out


def _dates(text: str) -> list[tuple[int, int]]:
    out = [(int(m), int(d)) for m, d in _DATE.findall(text or "")]
    out += [(int(m), int(d)) for _y, m, d in _ISO.findall(text or "")]
    return out


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-6, 1e-6 * abs(right))


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        found = _numbers(value)
        if len(found) == 1:
            return found[0]
    return None


def _safe_eval(expr: str) -> float | None:
    """只允许数字与四则运算的算式求值，不用 eval 执行任意代码。"""
    text = (expr or "").replace("×", "*").replace("÷", "/").replace("％", "%").strip()
    if not text or not re.fullmatch(r"[0-9+\-*/().\s]+", text):
        return None
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
                                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd,
                                ast.Load)):
            return None
    try:
        return float(eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {}))
    except Exception:
        return None


def _same_value(left: Any, right: Any) -> bool:
    lf, rf = _to_float(left), _to_float(right)
    if lf is not None and rf is not None:
        return _close(lf, rf)
    return str(left).strip() == str(right).strip()


# ── A1 覆盖 ─────────────────────────────────────────────────────────────────

def check_cover(utterance: str, product: dict[str, Any]) -> Check:
    pieces = segment.segments(utterance)
    claims = product.get("claims") or []
    norms = [segment.norm(c.get("text") or "") for c in claims]

    covered, missed = [], []
    for piece in pieces:
        target = segment.norm(piece)
        rest = target
        for claim_norm in norms:
            if claim_norm and claim_norm in rest:
                rest = rest.replace(claim_norm, "", 1)
        # 合并型 claim：整段被某一条 claim 包含（模型自己把两段并成一条也算覆盖）
        merged = any(target and target in claim_norm for claim_norm in norms)
        if not rest or merged:
            covered.append(piece)
        else:
            missed.append(piece)

    if not pieces:
        return Check("A1", "覆盖", "na", "原文切不出段落")
    if missed:
        return Check("A1", "覆盖", "fail",
                     f"{len(missed)}/{len(pieces)} 段没有被任何 claim 覆盖",
                     [{"segment": m} for m in missed])
    return Check("A1", "覆盖", "pass", f"{len(pieces)} 段全部有 claim 认领",
                 [{"segment": c} for c in covered])


# ── A2 闭合 ─────────────────────────────────────────────────────────────────

def check_closure(product: dict[str, Any]) -> Check:
    claims = product.get("claims") or []
    actions = [c for c in claims if c.get("kind") == "action"]
    if not claims:
        return Check("A2", "闭合", "na", "没有 claims")

    homed = {str(f.get("claim")) for f in product.get("fills") or []}
    homed |= {str(u.get("claim")) for u in product.get("unassigned") or []}
    missing = [c.get("id") for c in actions if str(c.get("id")) not in homed]

    if missing:
        return Check("A2", "闭合", "fail",
                     f"{len(missing)} 条 action 诉求没有归宿（既不在 fills 也不在 unassigned）",
                     [{"claim": m} for m in missing])
    return Check("A2", "闭合", "pass",
                 f"{len(actions)} 条 action 诉求都有归宿（其余 {len(claims) - len(actions)} 条不需要）")


# ── A3 出处 ─────────────────────────────────────────────────────────────────

def check_evidence(utterance: str, product: dict[str, Any]) -> Check:
    fills = product.get("fills") or []
    claims = {str(c.get("id")): (c.get("text") or "") for c in product.get("claims") or []}
    if not fills:
        return Check("A3", "出处", "na", "没有 fills")

    bad, soft, ok = [], [], 0
    for index, fill in enumerate(fills):
        span = str(fill.get("span") or "")
        label = f"fills[{index}] {fill.get('row')}"
        if not span:
            bad.append({"where": label, "why": "span 为空"})
            continue
        in_utterance = _locate(span, utterance)
        if in_utterance is None:
            bad.append({"where": label, "why": f"span {span!r} 在原文里找不到"})
            continue
        claim_text = claims.get(str(fill.get("claim")))
        if claim_text is None:
            bad.append({"where": label, "why": f"claim {fill.get('claim')} 不存在"})
            continue
        in_claim = _locate(span, claim_text)
        if in_claim is None:
            bad.append({"where": label, "why": f"span {span!r} 不在它指向的那条 claim 里"})
            continue
        if in_utterance == 0 and in_claim == 0:
            ok += 1
        else:
            soft.append({"where": label, "why": "不是逐字，去标点后能对上"})

    if bad:
        return Check("A3", "出处", "fail",
                     f"{len(bad)}/{len(fills)} 条 span 对不上原文", bad)
    if soft:
        return Check("A3", "出处", "unclear",
                     f"{len(soft)}/{len(fills)} 条 span 非逐字（去标点后一致）", soft)
    return Check("A3", "出处", "pass", f"{len(fills)} 条 span 全部逐字命中")


# ── A4 来源 ─────────────────────────────────────────────────────────────────

_PLAIN_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _enum_domain(row: row_book.Row) -> set[str] | None:
    """这一行的 value 允许出现哪些固定词。取不到就返回 None（判"存疑"，不判错）。"""
    from app.core.dsl.registry import parameters as param_reg
    from app.core.dsl.registry import quantities as qty_reg

    if row.kind in ("minimize", "maximize", "weighted", "budget"):
        return set(qty_reg.NAMED_QUANTITIES)
    if row.kind == "strategy":
        from app.core.dsl import llm_parser as lp

        return set(lp._STRATEGY_CANONICAL.values())
    if row.section == "parameters":
        meta = param_reg.lookup(row.kind) or {}
        domain = str(meta.get("取值约束") or "")
        if "是" in domain and "否" in domain:
            return {"是", "否"}
    return None


_RELATIVE = re.compile(r"(明天|后天|今天|昨天|大后天|次日|隔天|下周|本周|这周|上周|月底|月初|下个月|这个月)")

# 注册表里所有"值必须是日期"的键。写死在这里是有意的：
# 这几个键的名字是契约的一部分，改名字要连着提示词一起改，本来就该同步。
_DATE_KEYS = {"day", "date", "dates", "start", "finish", "deadline", "at"}


def _value_strings(value: Any) -> list[str]:
    """把 value 里的字符串掏出来（含嵌套 dict/list）。用于核对文字推导。

    跳过 `kind` / `type`：这两个是结构标签（"weather"、"budget"），
    不是从原文推出来的值。把它们也算进"出处"，会冤枉所有天气类补丁。
    """
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in ("kind", "type"):
                    continue
                walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)
        elif isinstance(node, str) and node.strip():
            out.append(node)

    walk(value)
    return out


def _echoed(token: str, text: str) -> bool:
    """这个值在推导式里交代过吗？

    日期按"月-日"比，不按字符串比：`2026-11-05` 和 `11月5日` 是同一件事，
    字符串比对会把它当成"没交代"。这类假警报最伤，一条就能让人不信整套判据。
    """
    if not token:
        return True
    if segment.norm(token) and segment.norm(token) in segment.norm(text):
        return True
    token_dates = _dates(token)
    text_dates = _dates(text)
    return bool(token_dates) and any(pair in text_dates for pair in token_dates)


def _is_arithmetic(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9+\-*/().\s]+", text or "")) and any(c.isdigit() for c in text or "")


_CALC_SEPS = ("=>", "→", "=", "：")

def _split_calc(calc: str) -> tuple[str, str] | None:
    """把推导拆成"因 → 果"。分隔符认 = / → / => / ：四种。

    这只是格式容错：模型常写"明天→2026-11-05"，硬要它写等号属于挑刺，
    而挑刺的代价是一整轮重试。语义判据一点没放宽——右边照样要能核。
    """
    for sep in _CALC_SEPS:
        if sep in calc:
            left, _, right = calc.partition(sep)
            return left.strip(), right.strip()
    return None


def _guessed_date(utterance: str, span: str, value: Any,
                  ctx: "session.Context | None" = None) -> str | None:
    """相对时间落成了日期——这个日期站得住吗？站不住就返回一条人能读的理由。

    三种情况：

    1. **原文本来就有绝对日期**（`11月5日风大，明天也一样`）→ 不判。
       机器分不清"哪个日期对应哪一段"，硬判必然假警报。
    2. **有基准**（`ctx`）→ 拿基准算出"明天"应该是哪天，落出来的日期必须对上。
       对不上就判死，并报出"基准算是 10-31，你写的是 10-31 之外的东西"。
       这比旧判据更严：旧的是"落了就算猜"，现在是"落错了才算错"。
    3. **没基准，或那个说法本身有歧义**（"下周""月底"）→ 落成具体日期就是猜的。
       机器不许替用户定"下周从哪天算"。
    """
    date_like = [t for t in _value_strings(value) if _ISO.search(t) or _dates(t)]
    if not date_like:
        return None
    if _dates(utterance) or _ISO.search(utterance):
        return None

    tokens = session.find_relative(utterance) or session.find_relative(span)
    if not tokens:
        return None

    if ctx is None:
        return (f"原文只有相对时间（{span!r}），没有基准日期，"
                f"却落成 {date_like[0]!r}——这是猜的，应走 clarify")

    expected = [ctx.resolve(token) for token in tokens]
    expected = [day for day in expected if day is not None]
    landed = set()
    for text in date_like:
        landed.update(_dates(text))

    if not expected:  # 说法有歧义，算不出来
        return (f"原文的相对时间 {tokens} 有歧义（基准 {ctx.iso} 也算不出唯一日期），"
                f"却落成 {date_like[0]!r}——应写 unassigned/need_info 让用户补日期")

    if landed & {(day.month, day.day) for day in expected}:
        return None

    want = "、".join(day.isoformat() for day in expected)
    return (f"按基准 {ctx.iso} 换算，{tokens} 应该是 {want}，"
            f"但落成了 {date_like[0]!r}——换算对不上")


def _unresolved_time(value: Any) -> str | None:
    """value 里还留着没落地的相对时间词吗（"明天"、"下周一"、"月底"）。

    这是实测补的第二道口子：模型发现"编个日期"会被 A4 判死，就换成不编——
    直接把 `"day": "明天"`、`"dates": ["明天"]` 原样写进 DSL，
    再用 `from=default` 附一句 why（"原文未给具体日期"）。
    于是 A4 走了 default 分支、只看 why 写没写，日期字段里就带着一句人话进了编译器。
    这类值后面没有任何一步会报错，求解器要么崩要么当成无效值——
    **"没编造"不等于"能用"**。
    """
    for token in _value_strings(value):
        found = _RELATIVE.search(token)
        if found:
            return found.group(0)
    return None


def _date_field_issues(dsl: dict[str, Any]) -> list[dict[str, Any]]:
    """DSL 里日期字段的值，必须是日期。

    上面那条管的是"相对时间词混进来"，这条管"混进来的是别的东西"——
    比如 `day: "TBD"`、`dates: ["待定"]`。日期字段是注册表里定死的几处，
    在这里逐个校验，代价很小。
    """
    issues: list[dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                here = f"{path}.{key}" if path else key
                if key in _DATE_KEYS:
                    if isinstance(item, (list, tuple)):
                        for index, single in enumerate(item):
                            _check(single, f"{here}[{index}]")
                    else:
                        _check(item, here)
                else:
                    walk(item, here)
        elif isinstance(node, (list, tuple)):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    def _check(value: Any, where: str) -> None:
        if not isinstance(value, str):
            issues.append({"where": where, "why": f"日期字段收到 {value!r}，不是字符串"})
            return
        if _RELATIVE.search(value):
            issues.append({"where": where,
                           "why": f"日期字段里是 {value!r}，相对时间没有落地"})
            return
        if not (_ISO.search(value) or _dates(value)):
            issues.append({"where": where, "why": f"日期字段里是 {value!r}，不是日期"})

    walk(dsl, "")
    return issues


def check_source(product: dict[str, Any], utterance: str = "",
                 ctx: "session.Context | None" = None) -> Check:
    fills = product.get("fills") or []
    if not fills:
        return Check("A4", "来源", "na", "没有 fills")

    bad, soft = [], []
    for index, fill in enumerate(fills):
        source = fill.get("from")
        label = f"fills[{index}] {fill.get('row')}"
        span = str(fill.get("span") or "")
        value = fill.get("value")

        # 先把"没落地的相对时间"挡住，default 也不例外——
        # `from=default` 只需要一句 why 就能过 A4，最容易被用来夹带 "明天"。
        stuck = _unresolved_time(value)
        if stuck is not None:
            bad.append({"where": label,
                        "why": f"value 里还是相对时间 {stuck!r}，没有落成具体日期；"
                               "要么走 clarify 要日期，要么进 unassigned"})
            continue

        # 相对时间：有基准就核换算，没基准就判猜。**必须放在 default 分支之前**——
        # 第一版放在后面，`from=default` 走完自己的分支就 continue 了，
        # 于是"自己补一个日期"这条最省事的路，正好绕开了相对时间判据。
        guess = _guessed_date(utterance, span, value, ctx) if utterance else None
        if guess is not None:
            bad.append({"where": label, "why": guess})
            continue

        if source == "default":
            if not str(fill.get("why") or "").strip():
                bad.append({"where": label, "why": "from=default 但没写 why"})
                continue
            if value is None:
                bad.append({"where": label, "why": "from=default 但没写 value"})
            continue

        if source not in ("literal", "convert", "enum"):
            bad.append({"where": label, "why": f"from={source!r} 不在四种之内"})
            continue

        if value is None:
            # 没有值就没法核对出处。模型偶尔只写 row+span、把值留在 dsl 里，
            # 那样 A4 会"因为没东西可查"而空过——这比判错更糟。
            bad.append({"where": label, "why": "这条 fill 没有 value，出处无从核对"})
            continue

        if source == "literal":
            numbers = _numbers(span)
            numeric = _to_float(value)
            if numeric is not None and numbers:
                if any(_close(n, numeric) for n in numbers):
                    continue
                bad.append({"where": label,
                            "why": f"span {span!r} 里没有 {value}；span 里的数是 {numbers}"})
                continue
            if numeric is None and isinstance(value, str) and value:
                dates = _dates(span)
                target = _dates(value)
                if dates and target and any(d == t for d in dates for t in target):
                    continue
                if _locate(value, span) is not None:
                    continue
                if dates == [] and target:
                    soft.append({"where": label,
                                 "why": f"span {span!r} 没有具体日期（相对时间需基准），无法核对"})
                    continue
                bad.append({"where": label, "why": f"value {value!r} 推不出自 span {span!r}"})
                continue
            if numeric is not None and not numbers:
                dates = _dates(span)
                if dates:
                    soft.append({"where": label, "why": f"span {span!r} 只有日期，值与数字对不上"})
                    continue
                bad.append({"where": label, "why": f"span {span!r} 里没有任何数字"})
            continue

        if source == "convert":
            calc = str(fill.get("calc") or "")
            halves = _split_calc(calc)
            if halves is None:
                bad.append({"where": label,
                            "why": "from=convert 但 calc 里没有推导（要写成 因 = 果 或 因 → 果）"})
                continue
            left, right = halves

            # 文字推导（"明天=2026-11-05"、"风大→不可作业"）不是算式，
            # 硬套数值核对只会报"算不出来"。分成两支各判各的。
            if not (_is_arithmetic(left) and _is_arithmetic(right)):
                tokens = _value_strings(value)
                date_like = [t for t in tokens if _ISO.search(t) or _dates(t)]
                if _RELATIVE.search(span) and date_like and not _dates(span):
                    # 这条最要紧：原文只说"明天"，任何一个具体日期都是猜的。
                    #
                    # 注意条件是"span 里有相对时间词"，不是"span 里没有日期"。
                    # 后者会冤枉"别出海"这种靠上一句带日期的分句——
                    # 假警报比漏报更伤，所以宁可窄一点。
                    bad.append({"where": label,
                                "why": f"原文 {span!r} 是相对时间，缺基准日期就落成 {date_like[0]!r}"
                                       "——这是猜的，应走 clarify 或让用户给日期"})
                    continue
                missing = [t for t in tokens if not _echoed(t, calc)]
                if missing:
                    bad.append({"where": label,
                                "why": f"calc 里没有交代 {missing} 是怎么来的"})
                    continue
                soft.append({"where": label,
                             "why": f"文字推导（{calc}），只能核到「引用一致」，核不了对错"})
                continue

            lhs = _safe_eval(left)
            rhs = _safe_eval(right)
            if lhs is None or rhs is None:
                bad.append({"where": label, "why": f"calc {calc!r} 算不出来"})
                continue
            if not _close(lhs, rhs):
                bad.append({"where": label, "why": f"calc {calc!r} 左右不相等（{lhs}≠{rhs}）"})
                continue
            numeric = _to_float(value)
            if numeric is not None and not _close(rhs, numeric):
                bad.append({"where": label, "why": f"calc 结果 {rhs} 与 value {value} 不一致"})
            continue

        # enum
        if not isinstance(value, str) or not value.strip():
            bad.append({"where": label,
                        "why": f"enum 的 value 必须是固定词，收到 {value!r}"})
            continue
        if _PLAIN_NUMBER.fullmatch(value.strip()):
            # 这一条是实测补的：`只排风机003的任务` 曾被写成
            # `from=enum, value=1`，而 enum 天然不可机器核对，于是就这么过了。
            bad.append({"where": label,
                        "why": f"enum 的 value 是数字 {value!r}；数字要用 literal 或 convert"})
            continue
        row = row_book.by_id(str(fill.get("row") or ""))
        domain = _enum_domain(row) if row else None
        if domain and value not in domain:
            bad.append({"where": label,
                        "why": f"{value!r} 不在该行的允许取值里：{sorted(domain)}"})
            continue
        if domain:
            continue
        soft.append({"where": label,
                     "why": f"enum 映射 {value!r} 与 span {span!r} 无法机器核对（保留）"})

    if bad:
        # 详情要能直接读出"哪一条、为什么"，只看"1 条推不出出处"等于没说。
        # 退回重试时模型拿到的也是这句，含糊的报错会让它瞎改一通。
        head = "；".join(str(item.get("why") or "") for item in bad[:2])
        return Check("A4", "来源", "fail", f"{len(bad)} 条推不出出处：{head}", bad)
    if soft:
        return Check("A4", "来源", "unclear", f"{len(soft)} 条无法机器核对", soft)
    return Check("A4", "来源", "pass", f"{len(fills)} 条来源可复算")


# ── A5 行号 ─────────────────────────────────────────────────────────────────

def check_rows(utterance: str, product: dict[str, Any]) -> Check:
    fills = product.get("fills") or []
    if not fills:
        return Check("A5", "行号", "na", "没有 fills")

    bad, seen = [], []
    for index, fill in enumerate(fills):
        row_id = str(fill.get("row") or "")
        row = row_book.by_id(row_id)
        if row is None:
            bad.append({"where": f"fills[{index}]", "why": f"行号 {row_id!r} 不存在",
                        "hint": f"合法范围：{row_book.summary()}"})
        else:
            seen.append({"row": row_id, "label": row.label})

    if bad:
        return Check("A5", "行号", "fail", f"{len(bad)} 条行号越界", bad)
    return Check("A5", "行号", "pass", f"{len(fills)} 条行号都在目录内", seen)


# ── A6 单值 ─────────────────────────────────────────────────────────────────

def check_single_value(product: dict[str, Any]) -> Check:
    fills = product.get("fills") or []
    if not fills:
        return Check("A6", "单值", "na", "没有 fills")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for fill in fills:
        row_id = str(fill.get("row") or "")
        grouped.setdefault(row_id, []).append(fill)

    clashes = []
    for row_id, items in grouped.items():
        distinct: list[Any] = []
        for item in items:
            value = item.get("value")
            if not any(_same_value(value, existing) for existing in distinct):
                distinct.append(value)
        if len(distinct) > 1:
            clashes.append({"row": row_id, "values": distinct,
                            "claims": [i.get("claim") for i in items]})

    if clashes:
        if product.get("route") == "clarify":
            return Check("A6", "单值", "pass", "同一行多个值，已按 clarify 处理", clashes)
        return Check("A6", "单值", "fail",
                     f"{len(clashes)} 个行号出现互相矛盾的值，却仍按 dsl 提交", clashes)
    return Check("A6", "单值", "pass", f"{len(grouped)} 个行号各只有一个值")


# ── A7 必填 ─────────────────────────────────────────────────────────────────

def _item_type(section: str, item: dict[str, Any]) -> Any:
    if section == "parameters":
        return item.get("field")
    if section == "data":
        return item.get("kind")
    return item.get("type")


def _match(row: row_book.Row, product: dict[str, Any]) -> list[dict[str, Any]]:
    items = product["dsl"].get(row.section) or []
    return [item for item in items if _item_type(row.section, item) == row.kind]


def check_required(utterance: str, product: dict[str, Any]) -> Check:
    fills = product.get("fills") or []
    if not fills:
        return Check("A7", "必填", "na", "没有 fills")

    bad, checked = [], 0
    for fill in fills:
        row = row_book.by_id(str(fill.get("row") or ""))
        if row is None:
            continue
        for item in _match(row, product):
            checked += 1
            missing = [name for name in row.required if name not in item]
            if missing:
                bad.append({"row": row.id, "kind": row.kind, "missing": missing,
                            "required": list(row.required)})

    if not checked:
        return Check("A7", "必填", "na", "fills 没有对应的 dsl 项，交给 A8 判")
    if bad:
        return Check("A7", "必填", "fail", f"{len(bad)} 项缺必填字段", bad)
    return Check("A7", "必填", "pass", f"{checked} 项必填齐全")


# ── A8 一致 ─────────────────────────────────────────────────────────────────

def check_backing(utterance: str, product: dict[str, Any]) -> Check:
    """dsl 每项都有 fill 背书；每个 fill 在 dsl 里都有对应项。"""
    route = product.get("route")
    if route in ("not_change", "reject"):
        extra = sum(len(v) for v in product["dsl"].values())
        if extra:
            return Check("A8", "一致", "fail",
                         f"route={route} 却交了 {extra} 项 dsl", [{"dsl_items": extra}])
        return Check("A8", "一致", "pass", f"route={route}，没有 dsl")

    fills = product.get("fills") or []
    dsl_items = [(section, item) for section in contract.SECTIONS
                 for item in (product["dsl"].get(section) or [])]

    if not dsl_items and not fills:
        return Check("A8", "一致", "na", "既没有 dsl 也没有 fills")

    unbacked = []
    for section, item in dsl_items:
        kind = _item_type(section, item)
        matched = False
        for fill in fills:
            row = row_book.by_id(str(fill.get("row") or ""))
            if row and row.section == section and row.kind == kind:
                matched = True
                break
        if not matched:
            unbacked.append({"section": section, "type": kind,
                             "why": "这一项没有任何 fill 指向它"})

    orphan = []
    for index, fill in enumerate(fills):
        row = row_book.by_id(str(fill.get("row") or ""))
        if row is None:
            orphan.append({"where": f"fills[{index}]", "why": f"行号 {fill.get('row')!r} 不存在"})
            continue
        if not _match(row, product):
            orphan.append({"where": f"fills[{index}]", "row": row.id,
                           "why": f"{row.section} 里没有 {row.kind}"})

    problems = unbacked + orphan
    if problems:
        return Check("A8", "一致", "fail",
                     f"dsl 多出 {len(unbacked)} 项、fill 落空 {len(orphan)} 条", problems)
    return Check("A8", "一致", "pass",
                 f"{len(dsl_items)} 项 dsl 与 {len(fills)} 条 fill 双向对上")


# ── A9 形状与枚举 ───────────────────────────────────────────────────────────

def check_shape(raw: dict[str, Any], product: dict[str, Any]) -> Check:
    issues = list(contract.shape_issues(raw))
    issues += _date_field_issues(product.get("dsl") or {})

    for claim in product.get("claims") or []:
        kind = claim.get("kind")
        if kind not in contract.KINDS:
            issues.append({"where": f"claim {claim.get('id')}", "why": f"kind={kind!r} 不在四值之内"})
        elif kind == "ask" and claim.get("about") not in contract.ASK_ABOUT:
            issues.append({"where": f"claim {claim.get('id')}",
                           "why": f"kind=ask 但 about={claim.get('about')!r}"})

    for index, fill in enumerate(product.get("fills") or []):
        if fill.get("from") not in contract.FROM:
            issues.append({"where": f"fills[{index}]", "why": f"from={fill.get('from')!r} 不在四种之内"})

    for index, item in enumerate(product.get("unassigned") or []):
        if item.get("reason") not in contract.REASONS:
            issues.append({"where": f"unassigned[{index}]",
                           "why": f"reason={item.get('reason')!r} 不在三种之内"})

    if issues:
        return Check("A9", "形状", "fail", f"{len(issues)} 处形状或枚举不合法", issues)
    return Check("A9", "形状", "pass", "形状与枚举合法")


# ── A10 不改 ────────────────────────────────────────────────────────────────

def check_route_consistency(product: dict[str, Any]) -> Check:
    route = product.get("route")
    claims = product.get("claims") or []
    actions = [c for c in claims if c.get("kind") == "action"]
    fills = product.get("fills") or []
    unassigned = product.get("unassigned") or []
    dsl_items = sum(len(v) for v in product["dsl"].values())

    if route is None:
        return Check("A10", "不改", "fail", "没有 route")

    if route == "not_change":
        if actions:
            return Check("A10", "不改", "fail",
                         f"route=not_change 却有 {len(actions)} 条 action 诉求，改动被吞了",
                         [{"claim": c.get("id"), "text": c.get("text")} for c in actions])
        if fills:
            return Check("A10", "不改", "fail", f"route=not_change 却交了 {len(fills)} 条 fills")
        return Check("A10", "不改", "pass", "not_change 且没有 action 诉求")

    if route == "reject":
        if fills:
            return Check("A10", "不改", "fail", f"route=reject 却交了 {len(fills)} 条 fills")
        homed = {str(u.get("claim")) for u in unassigned}
        loose = [c.get("id") for c in claims if str(c.get("id")) not in homed]
        if loose:
            return Check("A10", "不改", "fail",
                         f"{len(loose)} 条 claim 没有进 unassigned，拒绝理由不全",
                         [{"claim": x} for x in loose])
        return Check("A10", "不改", "pass", "整句都在表外，逐条给了理由")

    if route == "clarify":
        return Check("A10", "不改", "pass", "走澄清，不提交改动")

    if route == "dsl":
        if not fills:
            return Check("A10", "不改", "fail", "route=dsl 却没有任何 fills")
        if not dsl_items:
            return Check("A10", "不改", "fail", "route=dsl 却交了空 dsl")
        return Check("A10", "不改", "pass", f"dsl 与 {len(fills)} 条 fills 自洽")

    return Check("A10", "不改", "fail", f"route={route!r} 不在四种之内")


# ── A11 方向 ────────────────────────────────────────────────────────────────
# 「不超过」写成 `>=`，是错误分类学里的 E5.5。它比"值填错"更隐蔽：
# 值看上去对、能编译、求解器也接受，只是**结论反了**——原本要压预算，
# 结果变成"至少花这么多"。这是唯一一类"全过还更糟"的错。
#
# 判据只在能确定方向时才判死：
#   · 只对带 `op` 的约束项（budget / cardinality / linear 等）生效；
#   · 参数是赋值（"上限放宽到14"里的"上限"是栏目名，不是方向），一律不判；
#   · claim 里同时出现两个方向的说法（"不超过…不低于…"）就算不清，不判。
_DIRECTION_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("<=", ("不超过", "不多于", "不能超过", "不许超过", "不能晚于", "不晚于", "不许晚于",
            "最多", "至多", "不高于", "以内", "之内", "封顶", "别超", "最晚")),
    (">=", ("不低于", "不少于", "不能低于", "不能早于", "不早于", "不许早于",
            "至少", "最少", "下限", "以上", "起步")),
)
_OP_EQUALISH = ("==", "=")


def _direction_of(text: str) -> tuple[str | None, str | None]:
    """这句话说的是哪个方向。两个方向都出现就当说不清（返回 None）。"""
    hits = [(op, word) for op, words in _DIRECTION_WORDS
            for word in words if word in (text or "")]
    if not hits:
        return None, None
    kinds = {op for op, _w in hits}
    if len(kinds) > 1:
        return None, None
    return hits[0]


def check_direction(utterance: str, product: dict[str, Any]) -> Check:
    if product.get("route") != "dsl":
        return Check("A11", "方向", "na", f"route={product.get('route')}，没有方向可判")

    claims = {str(c.get("id")): c for c in product.get("claims") or []}
    fills = product.get("fills") or []
    constraints = product["dsl"].get("constraints") or []

    wrong, unsure, checked = [], [], 0
    for fill in fills:
        row = row_book.by_id(str(fill.get("row") or ""))
        if row is None or row.section != "constraints":
            continue
        claim = claims.get(str(fill.get("claim"))) or {}
        said_op, word = _direction_of(str(claim.get("text") or ""))
        if said_op is None:
            continue
        for item in constraints:
            if _item_type("constraints", item) != row.kind:
                continue
            wrote = item.get("op")
            if not isinstance(wrote, str):
                continue
            checked += 1
            label = f"{row.id} {row.kind}"
            if wrote in _OP_EQUALISH:
                unsure.append({"where": label, "claim": claim.get("text"),
                               "said": f"{word}（{said_op}）", "wrote": wrote,
                               "why": "原文说的是不等式，这里写成了等号——可能对，也可能是近似"})
                continue
            if wrote != said_op:
                wrong.append({"where": label, "claim": claim.get("text"),
                              "said": f"{word}（{said_op}）", "wrote": wrote,
                              "why": f"原文是「{word}」，op 却写成 {wrote}，结论反了"})

    if wrong:
        sample = "；".join(f"「{w['claim']}」说 {w['said']}，写成 {w['wrote']}" for w in wrong[:2])
        return Check("A11", "方向", "fail",
                     f"{len(wrong)} 处比较方向和原文相反：{sample}", wrong + unsure)
    if unsure:
        return Check("A11", "方向", "unclear",
                     f"{len(unsure)} 处把不等式写成等号，疑似近似", unsure)
    if not checked:
        return Check("A11", "方向", "na", "没有「文本说了方向、又写了 op」的项")
    return Check("A11", "方向", "pass", f"{checked} 处比较方向与原文一致")


# ── A12 reject 自查 ─────────────────────────────────────────────────────────
# 整句拒掉、但原话里明明有表内的东西——这是 reject 最常见的滥用。
# 工程上判不了"这段到底能不能做"（要判就得把 claim 文本对到 DSL 语义，
# 那是另一个量级的活，而且极容易假警报）。所以这里只给线索，不拦截：
# 命中就记一条 unclear，在页面上摆出来让人一眼看到"它拒了，但这句话里有 5 个表内词"。
#
# **不作为判据的一部分，是有意的。** 把判不了的东西说成"你错了"，
# 一条假警报就能让人关掉整套验收器。
_SCOPE_HINTS: tuple[str, ...] = (
    "船租", "人工", "单价", "工时", "人数", "作业线", "风速", "浪高",
    "预算", "成本", "任务0", "风机0", "窗口0", "同一天", "先修", "别排",
    "截止", "完工", "工期", "电价", "部件", "叶片", "齿轮箱", "必须修", "不许修",
)


def check_reject_scope(utterance: str, product: dict[str, Any]) -> Check:
    if product.get("route") != "reject":
        return Check("A12", "不轻拒", "na", f"route={product.get('route')}，不需要自查")

    hits = [word for word in _SCOPE_HINTS if word in (utterance or "")]
    claims = product.get("claims") or []
    actions = [c for c in claims if c.get("kind") == "action"]

    if hits and actions:
        return Check("A12", "不轻拒", "unclear",
                     f"整句拒了，但原文里有 {len(hits)} 个表内线索词：{'、'.join(hits)}；"
                     "请自查是不是有哪一段其实能落表",
                     [{"hint": word} for word in hits])
    return Check("A12", "不轻拒", "pass", "整句拒，原文没有明显的表内线索")


# ── 汇总 ────────────────────────────────────────────────────────────────────

def _dsl_numbers(dsl: dict[str, Any]) -> list[float]:
    """从 DSL 里取数字。ISO 时间只取月/日，不取时刻——
    否则每个 08:00 都会变成"多出来的数字"，把对账淹掉。"""
    out: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            out.append(float(node))
        elif isinstance(node, str):
            found = _ISO.search(node)
            if found:
                out.append(float(found.group(2)))
                out.append(float(found.group(3)))
                return
            out.extend(_numbers(node))

    walk(dsl)
    return out


def _dedupe(values: list[float]) -> list[float]:
    kept: list[float] = []
    for value in values:
        if value in (0.0, 2026.0):
            continue
        if not any(_close(value, existing) for existing in kept):
            kept.append(value)
    return sorted(kept)


def audit_numbers(utterance: str, dsl: dict[str, Any]) -> dict[str, Any]:
    """数字对账：原文里出现的数，DSL 里有没有；DSL 里的数，原文有没有。

    这是两边（规则侧 / LLM 侧）**唯一完全共用**的判据，因为它不需要 claims/fills。
    "原文有、DSL 没有"就是丢话；"DSL 有、原文没有"就是编造或默认值。
    ISO 时间里的 08:00 会算作"多出"，属于已知噪声——所以结果给人看，不自动定罪。
    """
    original = _dedupe(_numbers(utterance))
    produced = _dedupe(_dsl_numbers(dsl))

    missing = [n for n in original if not any(_close(n, p) for p in produced)]
    extra = [n for n in produced if not any(_close(n, o) for o in original)]
    return {"original": original, "produced": produced,
            "missing": missing, "extra": extra,
            "dropped": len(missing), "invented": len(extra)}


def verify(utterance: str, raw: dict[str, Any],
           ctx: "session.Context | None" = None) -> Report:
    """跑完 A1~A11，返回报告。raw 是模型原始产物（未归一化）。

    `ctx` 是会话基准（今天几号）。不给的话，相对时间一律按"没有基准"判——
    旧行为和旧测试都不变，这是有意的：判据不该偷偷读系统时钟。
    """
    product = contract.normalize(raw)
    pieces = segment.segments(utterance)
    return Report(
        checks=[
            check_cover(utterance, product),
            check_closure(product),
            check_evidence(utterance, product),
            check_source(product, utterance, ctx),
            check_rows(utterance, product),
            check_single_value(product),
            check_required(utterance, product),
            check_backing(utterance, product),
            check_shape(raw, product),
            check_route_consistency(product),
            check_direction(utterance, product),
            check_reject_scope(utterance, product),
        ],
        segments=pieces,
    )
