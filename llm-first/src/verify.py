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


# 中文数字。**用户说的数就是数**——"两条作业线"里的"两条"和"2条"是同一回事。
# 不认中文数字，就会把"用户原话里明明白白写了2"判成"推不出出处"。
_CN_DIGIT = {"零": 0, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
# 只认"数字 + 量词/单位"的组合。**为什么这么窄**：中文里到处是"一定""一次""一样"，
# 见到单个汉字就当数字，"明天一定别开工"都会被读出个 1 来。
# 加了量词才说明它在数东西。
_CN_CLASSIFIER = {"万": 1e4, "千": 1e3, "百": 1e2,
                  "条": 1, "个": 1, "人": 1, "天": 1, "艘": 1, "台": 1, "名": 1,
                  "小时": 1, "米": 1, "元": 1, "次": 1, "班": 1, "度": 1, "周": 1}
_CN_NUMBER_RE = re.compile(
    r"([零一二两三四五六七八九十]+)((?:[" + "".join(_CN_CLASSIFIER) + "])+)")


def _cn_digits(body: str) -> float | None:
    """读一到九十九的中文数字。

    **读不准就返回 None，不返回"看起来像"的值**：这里读出来的数会去和用户填的值比对，
    猜出来的数一旦凑巧对上，A4 就会放过一条本来错的东西。
    """
    if not body:
        return None
    if len(body) == 1:
        return float(_CN_DIGIT[body])
    if "十" not in body:
        return None
    head, _, tail = body.partition("十")
    if len(head) > 1 or len(tail) > 1:      # "一百二十" 这种读法这里不认
        return None
    tens = _CN_DIGIT.get(head, 1) if head else 1
    ones = _CN_DIGIT.get(tail, 0) if tail else 0
    return float(tens * 10 + ones)


def _cn_numbers(text: str) -> list[float]:
    """读中文数字。用户说"两条"，就是说了 2——不认这个会把明确的话判成推不出。

    量词可能连着写（"三百万" 是 3×100×10000），逐个乘上去。
    """
    out: list[float] = []
    for raw, units in _CN_NUMBER_RE.findall(text or ""):
        value = _cn_digits(raw)
        if value is None:
            continue
        step, rest = 1.0, units
        while rest:
            hit = next((u for u in _CN_CLASSIFIER if rest.startswith(u)), None)
            if hit is None:
                break
            rest = rest[len(hit):]
            step *= _CN_CLASSIFIER[hit]
        if rest:                             # 有没认出来的量词，宁可不要这个数
            continue
        out.append(value * step)
    return out


def _numbers(text: str, *, chinese: bool = True) -> list[float]:
    """读文本里的数。`chinese=False` 只认阿拉伯数字，用在**填进去的值**上。

    两边不一样是有意的：原文里"两条"就是 2，读不出来等于没听见用户说话；
    但填进 dsl 的 value 必须是求解器能用的数字，"八小时"这种写法本身就不合格
    （它会被 A9/校验器说"形状不对"，不该被 A4 当成一个对上了 8 的值放过去）。
    """
    out: list[float] = []
    for raw, unit in _NUM.findall(text or ""):
        try:
            value = float(raw)
        except ValueError:
            continue
        if unit:
            value *= _CN_UNIT[unit]
        out.append(value)
    if chinese:
        out += _cn_numbers(text)
    return out


def _dates(text: str) -> list[tuple[int, int]]:
    out = [(int(m), int(d)) for m, d in _DATE.findall(text or "")]
    out += [(int(m), int(d)) for _y, m, d in _ISO.findall(text or "")]
    return out


_CLOCK_HHMM = re.compile(r"(\d{1,2})[:：](\d{2})")
_CLOCK_POINT = re.compile(r"(上午|早上|早晨|下午|晚上|傍晚|中午|夜里)?\s*(\d{1,2})\s*[点時时]")
_PM_WORDS = ("下午", "晚上", "傍晚", "夜里")
_CN_HOUR = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _clock(text: str) -> list[int]:
    """把文本里的时刻读成小时数。`08:00`、`8点`、`下午6点` 都要能读。

    为什么要有这个：`每天8点开工` 落成 `"08:00"` 是完全正确的写法，
    但字面上"08:00"确实不是"8点"的子串。第一版按字符串比，
    于是每一条时刻改动都被 A4 判成"推不出出处"——这类假警报会让人
    连真的编造也一起放过。
    """
    body = text or ""
    out: list[int] = []
    for hour, _minute in _CLOCK_HHMM.findall(body):
        out.append(int(hour))
    for word, raw in _CLOCK_POINT.findall(body):
        hour = int(raw)
        if word in _PM_WORDS and hour < 12:
            hour += 12
        out.append(hour)
    for match in re.finditer(r"([零一二两三四五六七八九十])\s*[点時时]", body):
        out.append(_CN_HOUR.get(match.group(1), -1))
    return [h for h in out if 0 <= h <= 24]


def _close(left: float, right: float) -> bool:
    return abs(left - right) <= max(1e-6, 1e-6 * abs(right))


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        found = _numbers(value, chinese=False)   # 填的值必须是阿拉伯数字
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

def _content(text: str) -> str:
    """去掉连接词之后的内容形态，只用于 A1 的两边比对。

    切段器会把「同时/然后/另外」这类词换成逗号，段里就没有它们了；
    而 claim 是**照抄原文**的，这些词还在。直接比字符串，就会出现
    「T01和T02不能同时修」被判成"没被覆盖"——原文一字不差地抄了，却因为
    切段时剥了一个"同时"而对不上。连接词不承载内容，两边都去掉再比。
    """
    body = text or ""
    for word in segment.CONNECTIVES:
        body = body.replace(word, "")
    return segment.norm(body)


def check_cover(utterance: str, product: dict[str, Any]) -> Check:
    pieces = segment.segments(utterance)
    claims = product.get("claims") or []
    norms = [_content(c.get("text") or "") for c in claims]

    covered, missed = [], []
    for piece in pieces:
        target = _content(piece)
        if not target:          # 整段都是连接词，没有内容可覆盖
            covered.append(piece)
            continue
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
                span_hours, value_hours = _clock(span), _clock(value)
                if span_hours and value_hours and set(span_hours) & set(value_hours):
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

def _multi_value_kinds() -> set[str]:
    """本身就要装一串值的原语，同一行出现多个值不是打架。

    `lexmin` 的必填是 `stages`（一串阶段），"先最小化A再最小化B" 天然会在
    同一行上填两条——那是两个阶段，不是两个互相矛盾的值。从注册表算，
    不手抄名单：以后加了同样形状的原语（比如 `weights`），这里自动跟上。
    """
    try:
        from app.core.dsl.registry import primitives as prim_reg
    except Exception:                                    # 没挂上后端就退回保守名单
        return {"lexmin"}
    out = set()
    for name, spec in prim_reg.OBJECTIVE_PRIMITIVES.items():
        required = spec.get("required") or ()
        if any(f in ("stages", "weights") for f in required):
            out.add(name)
    return out or {"lexmin"}


_MULTI_VALUE = _multi_value_kinds()


def _mergeable(left: Any, right: Any) -> bool:
    """两个值能不能并成一条：**只要没有同一个键取值不同，就能并。**

    "叶片11月5日到2套，到货前不能开工" 会被切成两条 claim：
    一条填 `{part, at, quantity:2}`，另一条填 `{part, at, blockBefore:true}`。
    它们不是两个互相矛盾的值，是同一条数据补丁的两半——合并起来正好是完整的一条。
    真正该拦的是"同一个键填了两个不同的数"（船租今天说5万、明天说8万），
    那条路还在：`_clash_keys` 只放宽了"补不同键"，没放宽"同键不同值"。
    """
    if isinstance(left, dict) and isinstance(right, dict):
        return all(_same_value(left[key], right[key]) for key in left.keys() & right.keys())
    return _same_value(left, right)


def check_single_value(product: dict[str, Any]) -> Check:
    fills = product.get("fills") or []
    if not fills:
        return Check("A6", "单值", "na", "没有 fills")

    grouped: dict[str, list[dict[str, Any]]] = {}
    for fill in fills:
        row_id = str(fill.get("row") or "")
        grouped.setdefault(row_id, []).append(fill)

    clashes, skipped, merged_rows = [], [], []
    for row_id, items in grouped.items():
        row = row_book.by_id(row_id)
        if row is not None and row.kind in _MULTI_VALUE:
            skipped.append(row_id)      # 这一行本来就是一串（lexmin 的阶段）
            continue
        distinct: list[Any] = []
        merged_keys = False
        for item in items:
            value = item.get("value")
            hit = next((d for d in distinct if _mergeable(d, value)), None)
            if hit is not None:
                # 同一件事的两个方面（数量 / 到货前不许开工），合并进同一条
                if isinstance(hit, dict) and isinstance(value, dict):
                    hit.update(value)
                    merged_keys = True
                continue
            distinct.append(value)
        if len(distinct) > 1:
            clashes.append({"row": row_id, "values": distinct,
                            "claims": [i.get("claim") for i in items]})
        elif merged_keys:
            merged_rows.append(row_id)

    tail = f"；{len(skipped)} 行本来就要装一串（{'、'.join(skipped)}）" if skipped else ""
    if merged_rows:
        tail += f"；{len(merged_rows)} 行是两半合成一条（{'、'.join(merged_rows)}）"
    if clashes:
        if product.get("route") == "clarify":
            return Check("A6", "单值", "pass", "同一行多个值，已按 clarify 处理" + tail, clashes)
        return Check("A6", "单值", "fail",
                     f"{len(clashes)} 个行号出现互相矛盾的值，却仍按 dsl 提交" + tail, clashes)
    return Check("A6", "单值", "pass", f"{len(grouped) - len(skipped)} 个行号各只有一个值" + tail)


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
        # 说清"哪一项、缺哪个键"——只写"1 项缺必填"，模型下一轮还是不知道改哪儿。
        head = "；".join(
            f"{item['kind']} 缺 {'/'.join(item['missing'])}（必填是 {'/'.join(item['required'])}）"
            for item in bad[:3])
        return Check("A7", "必填", "fail", f"{len(bad)} 项缺必填字段：{head}", bad)
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


# ── A13 量词 ────────────────────────────────────────────────────────────────
# 「高风险的必须全修完」被写成 `cardinality{">=",1}`——"至少修一个"，不是"全部"。
# 这是错误分类学里的 E4.3（近似栏目顶替），也是本系统历史上真发生过的一次。
#
# 判据做得很窄，故意的：只认「claim 里出现『全部/所有/全修』」+「对应的 cardinality 值 <= 1」
# 这一种组合。不窄就会误伤——"至少修5个"和"全部修完"在字面上都可能带"修"字。
_ALL_WORDS = ("全部", "所有", "全修", "全都", "一个不落", "每一个")


def check_quantifier(utterance: str, product: dict[str, Any]) -> Check:
    if product.get("route") != "dsl":
        return Check("A13", "量词", "na", f"route={product.get('route')}，没有可比的量词")

    claims = {str(c.get("id")): c for c in product.get("claims") or []}
    constraints = product["dsl"].get("constraints") or []
    bad = []
    for fill in product.get("fills") or []:
        row = row_book.by_id(str(fill.get("row") or ""))
        if row is None or row.kind not in ("cardinality", "must", "linear"):
            continue
        claim = claims.get(str(fill.get("claim"))) or {}
        text = str(claim.get("text") or "")
        if not any(word in text for word in _ALL_WORDS):
            continue
        for item in constraints:
            if _item_type("constraints", item) != row.kind:
                continue
            value = item.get("value")
            if row.kind == "cardinality" and isinstance(value, (int, float)) and value <= 1:
                bad.append({"where": f"{row.id} {row.kind}", "claim": text,
                            "wrote": f'op={item.get("op")} value={value}',
                            "why": "原文说「全部」，却写成「至少 1 个」——这是拿近似的东西顶，"
                                   "不是表达不了；表达不了应该进 unassigned"})
    if bad:
        return Check("A13", "量词", "fail",
                     f"{len(bad)} 处把「全部」写成了「至少一个」", bad)
    return Check("A13", "量词", "pass", "没有「全部」被降级成「至少一个」")


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


# ── A14 可执行性 ────────────────────────────────────────────────────────────
# 登记了、能过校验、但执行时抛错的原语。这是"51 项宣称 / 16 项生效"的另一种形态：
# 校验器认识 `weighted`，编译器却不认，于是它一路绿灯走到求解，然后在
# `stages_builder` 里抛 "目标 weighted 已登记但原型未实现编译"。
#
# **这张表要能被证伪。** `tests/test_taxonomy.py` 里有一条测试会真去调编译器，
# 编译器哪天实现了 weighted，那条测试就会失败，提醒把这里清空——
# 而不是靠谁记得回来改。
_UNCOMPILED_OBJECTIVES: dict[str, str] = {
    "weighted": "原型未实现编译（compiler.stages_builder 会直接抛错）；"
                "要权重改用 lexmin 或多个 minimize",
}


def check_executable(product: dict[str, Any]) -> Check:
    objectives = product["dsl"].get("objectives") or []
    bad = [{"where": f"objectives[{index}]", "type": item.get("type"),
            "why": _UNCOMPILED_OBJECTIVES[item.get("type")]}
           for index, item in enumerate(objectives)
           if item.get("type") in _UNCOMPILED_OBJECTIVES]
    if bad:
        return Check("A14", "可执行", "fail",
                     f"{len(bad)} 个目标原语执行时必然抛错：{'、'.join(b['type'] for b in bad)}",
                     bad)
    return Check("A14", "可执行", "pass", "没有已知的不可执行原语")


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


# 「少一条」「提前两天」这类**增减量**：它会被算进现值里，DSL 里出现的是
# 算完之后的结果，增减量本身不出现是对的。中阿两种写法都要认——
# 原文写"少一条"，图里的数是 1，按字符串找就是找不到。
_DELTA_RE = re.compile(
    r"(少|多|减|降|涨|加|增|提前|推[后迟]|推迟|延[期后]|拖后)[了掉]*\s*"
    r"([零一二两三四五六七八九十]+|\d+(?:\.\d+)?)")


def _consumed_by_arithmetic(n: float, body: str) -> bool:
    for _word, raw in _DELTA_RE.findall(body or ""):
        value = _cn_digits(raw) if not raw[0].isdigit() else float(raw)
        if value is not None and _close(value, n):
            return True
    return False


def audit_numbers(utterance: str, dsl: dict[str, Any], route: str = "dsl") -> dict[str, Any]:
    """数字对账：原文里出现的数，DSL 里有没有；DSL 里的数，原文有没有。

    这是两边（规则侧 / LLM 侧）**唯一完全共用**的判据，因为它不需要 claims/fills。
    "原文有、DSL 没有"就是丢话；"DSL 有、原文没有"就是编造或默认值。
    ISO 时间里的 08:00 会算作"多出"，属于已知噪声——所以结果给人看，不自动定罪。

    **route 不是 dsl 时整个对账不适用。** 这一条是跑批跑出来的：
    "把风险降低50%" 正确判成 reject，"只有风机003" 正确判成 reject，
    结果两句话都被记了一笔"丢数 50 / 丢数 3"——把正确的拒绝报成了丢话。
    没有 DSL 就没有"丢"这回事，硬对账只会制造一堆看起来很严重的假警报。
    """
    # 对账只读阿拉伯数字。**和 A4 不一样是有意的**：A4 比的是"这一条 fill 的 span
    # 和它填的值"，范围窄，中文数字读对了收益很大（"两条作业线"→2）；
    # 对账拿整句话比整套 DSL，范围宽到连"同一天""一天""一小时"都会被读成数——
    # 上线当天就冒出三条假警报（Q302/Q309/Q340），而一次真丢话都没多抓到。
    # 收益撑不住误报，只认阿拉伯数字。
    original = _dedupe(_numbers(utterance, chinese=False))
    if route != "dsl":
        return {"applicable": False, "route": route, "original": original, "produced": [],
                "missing": [], "extra": [], "dropped": 0, "invented": 0,
                "why": f"route={route}，没有 DSL 可对账"}
    produced = _dedupe(_dsl_numbers(dsl))

    # 「上涨20%」落到 DSL 里是 `op:"*", value:1.2`——20 这个数**本来就不该出现**。
    # 不认这件事，每个百分比改动都会被记一笔"丢数"，而这是最像假警报的那种假警报：
    # 它天天出现、每次都对，看的人很快就学会无视整列。
    # 两类"原文的数和 DSL 的数本来就该不同"的情况：
    #   1. 百分比：上涨20% → `*1.2`，20 不该出现在 DSL 里；
    #   2. 12 小时制：下午6点 → `18:00`，6 也不该出现。
    # 不认这两件事，每一条都记一笔"丢数"——而这种假警报每次都对，
    # 看的人几天就学会无视整列，真丢话也就跟着被无视了。
    _PM = re.compile(r"(下午|晚上|傍晚|夜里|夜间|中午)\s*(\d{1,2})\s*[点時时]")
    alternatives: dict[float, tuple[float, ...]] = {}
    exempt: set[float] = set()
    body = utterance or ""
    for n in original:
        raw = str(int(n)) if float(n).is_integer() else str(n)
        alts: list[float] = []
        if re.search(rf"{re.escape(raw)}\s*[%％]", body):
            alts += [n / 100.0, 1 + n / 100.0]
        if any(_close(float(m.group(2)), n) for m in _PM.finditer(body)):
            alts.append(n + 12 if n < 12 else n)
        if alts:
            alternatives[n] = tuple(alts)
        elif _consumed_by_arithmetic(n, body):
            # 「船少一条」→ 现值 3 减 1 得 2，DSL 里该出现的是 2；
            # 1 被算掉了，不出现是对的。判它"丢数"等于要求用户把算式写两遍。
            exempt.add(n)

    def _kept(n: float) -> bool:
        if n in exempt:
            return True
        if any(_close(n, p) for p in produced):
            return True
        alts = alternatives.get(n)
        return bool(alts) and any(_close(alt, p) for alt in alts for p in produced)

    missing = [n for n in original if not _kept(n)]
    extra = [n for n in produced if not any(_close(n, o) for o in original)]
    return {"applicable": True, "route": route,
            "original": original, "produced": produced,
            "missing": missing, "extra": extra,
            "dropped": len(missing), "invented": len(extra)}


def verify(utterance: str, raw: dict[str, Any],
           ctx: "session.Context | None" = None) -> Report:
    """跑完 A1~A14，返回报告。raw 是模型原始产物（未归一化）。

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
            check_quantifier(utterance, product),
            check_executable(product),
        ],
        segments=pieces,
    )
