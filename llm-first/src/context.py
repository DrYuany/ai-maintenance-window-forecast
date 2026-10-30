"""会话基准：今天是几号。

为什么需要这个。`明天风大，先别出海` 在没有任何基准时是**无解**的：

- 编一个具体日期 → A4 判"猜的"，退回；
- 把"明天"原样写进日期字段 → A4+A9 判"没落地"，退回。

两条判据都对，因为"明天"确实无法落地。但这意味着**再严的判据也解决不了这条**——
缺的不是判据，是基准。

给了基准，判据从"不许落日期"翻成"落出的日期必须等于 基准+偏移"，
比原来更严：原来只要不落就算过，现在落错了要判死。

一个刻意的设计：基准**必须显式传入**。没有传的时候（比如老测试、老调用），
判据退回"相对时间落日期即猜"的旧行为——而不是偷偷用 `date.today()`。
理由：用系统时间当基准，会让同一条判据在今天就过、明天就不过，
测试会变成看日历脸色的东西。宁可显式一点。
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any

_CN_DIGIT = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_CN_WEEKDAY = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# 能唯一确定的相对说法 → 相对今天的天数。落在表里的才允许换算，
# 不在表里的（"下周""月底""下个月"）不是因为难，而是因为**它们本身有歧义**：
# 下周是从明天起算还是从周一起算？月底是这个月还是下个月？机器猜一个就是编造。
_OFFSET = {
    "今天": 0, "今日": 0,
    "明天": 1, "明日": 1, "次日": 1, "隔天": 1,
    "后天": 2,
    "大后天": 3,
    "昨天": -1, "昨日": -1,
    "前天": -2,
}

_WEEKDAY_RE = re.compile(r"(下下|下|本|这|上)(?:个)?(?:周|星期|礼拜)([一二三四五六日天])")
_BARE_WEEK_RE = re.compile(r"(下下|下|本|这|上)(?:个)?(?:周|星期|礼拜)(?![一二三四五六日天])")
# 有两个以上可能解释的说法：机器不许猜，遇到就退回澄清。
_AMBIGUOUS = ("下周", "本周", "这周", "上周", "月底", "月初", "下个月", "这个月",
              "下下周", "上个月", "年底", "月初", "季度末")


@dataclass(frozen=True)
class Context:
    today: _dt.date
    tz: str = "+08:00"
    base_id: str = "基座01"
    # 当前场景的参数现值（参数名 → (值, 单位)）。空的就不给这一段提示词。
    params: tuple[tuple[str, Any, str], ...] = ()
    # 海事窗口的起止（编号 → (开始, 结束)）。"窗口02提前两天"要它才能算。
    windows: tuple[tuple[str, str, str], ...] = ()

    # ── 展示 ────────────────────────────────────────────────────────────
    @property
    def iso(self) -> str:
        return self.today.isoformat()

    @property
    def weekday(self) -> str:
        return _CN_WEEKDAY[self.today.weekday()]

    def line(self) -> str:
        return f"今天是 {self.iso}（{self.weekday}，UTC{self.tz}）。"

    # ── 换算 ────────────────────────────────────────────────────────────
    def resolve(self, token: str) -> _dt.date | None:
        """把一个相对时间说法换算成日期；算不出来返回 None。

        算不出来有两种情况，都不许猜：
          1. 说法本身有歧义（"下周""月底"）；
          2. 这个说法不在词表里（"等风小了"）。
        """
        text = (token or "").strip()
        if not text:
            return None
        if text in _OFFSET:
            return self.today + _dt.timedelta(days=_OFFSET[text])
        found = _WEEKDAY_RE.search(text)
        if found:
            prefix, day_char = found.group(1), found.group(2)
            target = _CN_DIGIT[day_char] - 1          # 周一=0
            monday = self.today - _dt.timedelta(days=self.today.weekday())
            shift = {"本": 0, "这": 0, "下": 1, "下下": 2, "上": -1}[prefix]
            return monday + _dt.timedelta(days=shift * 7 + target)
        return None

    def resolves(self, token: str) -> bool:
        return self.resolve(token) is not None

    def param_block(self) -> str:
        """现值表。**为什么要有这一段**：`船少一条看看` 这种相对改动，
        没有现值就只能靠猜或者反问——而"现在几条"是系统自己知道的事，
        让用户重复一遍是产品的问题，不是用户的问题。"""
        if not self.params:
            return ""
        rows = "\n".join(f"| {name} | {value} | {unit} |" for name, value, unit in self.params)
        block = (
            "# 现在是多少\n\n"
            "下表是当前场景的参数值。用户说「少一条」「涨两成」「砍一半」这类**相对改动**时，\n"
            "在这个基础上算出目标值，`op` 用 `=` 写绝对值，并在 `calc` 里写清算式\n"
            "（例如 `calc=\"3-1=2\"`）。\n\n"
            "| 参数 | 现值 | 单位 |\n|---|---|---|\n" + rows
        )
        if self.windows:
            win = "\n".join(f"| {name} | {start} | {end} |" for name, start, end in self.windows)
            block += (
                "\n\n海事窗口的起止也在这里——「窗口02提前两天开始」这种改动要用它换算：\n\n"
                "| 窗口 | 开始 | 结束（不含） |\n|---|---|---|\n" + win
            )
        return block

    def next_day(self, day: int) -> str:
        """基准日之后**最近**的 day 号。

        "只说几号没给月份"的说法只有一个答案：**最近**的那一个。
        12月6日不是"另一种可能"，因为 11月6日已经比它近。
        31号在小月不存在时老实说不存在，不往前也不往后凑。
        """
        for attempt in range(3):
            month = (self.today.month - 1 + attempt) % 12 + 1
            year = self.today.year + (self.today.month - 1 + attempt) // 12
            try:
                hit = _dt.date(year, month, day)
            except ValueError:
                continue
            if hit >= self.today:
                return hit.isoformat()
        return f"{day}号不存在"

    def rule_block(self) -> str:
        """日期口径里**逐字不变**的那部分（"怎么算"的规矩）。

        拆出来只有一个理由：LLM 的提示词缓存**只认前缀**，开头第 N 个字一变，
        后面全部重新读一遍。"今天是 2026-10-31" 这种话每轮都不一样，
        混在这一段里会污染后面上万字；具体的值放在 `values_block()`。

        写这段的铁律：**不许出现任何具体日期**。
        `tests/test_prompt.py` 有一条测试拿两个不同的基准日各生成一遍，
        这段必须逐字相同——谁往里塞一个今天就会红。
        """
        return (
            "# 日期怎么定\n\n"
            "日期字段**只收具体日子**。`day` / `dates` / `start` / `finish` / `deadline` / `at` "
            "这些键不接受「明天」「月底」「下周」这种说法——把没定的事写成定了，比说不懂更糟。\n\n"
            "| 原文的说法 | 怎么办 |\n|---|---|\n"
            "| 今天／明天／后天／大后天、下周X、本周X | 本轮基准和换算结果在文末"
            "「本轮的基准」那一段，照抄，并在 `calc` 里写清算式 |\n"
            "| 只说「几号」没给月份 | 取基准日之后**最近**的那个该日；"
            "更远的同号日子不算「另一种可能」 |\n"
            "| 「下周」「月底」「下个月」「下旬」 | 本身有歧义，算不出来。**不要猜**——"
            "写进 `unassigned`，`reason=need_info`，让用户补一句具体日子 |\n\n"
            "一次说几个号的，每个都各自按「最近」那一格取。\n\n"
            "「最近」是唯一答案，别反问。说\"六号\"就是指基准之后最近的那个 6 号，"
            "再下一个月的 6 号不是\"另一种可能\"——回一句\"您说的是这个月还是下个月\"，"
            "是把已经确定的事说成不确定的。"
        )

    def values_block(self) -> str:
        """本轮的实际基准 + 现值 + 窗口起止。**每轮都可能变**，所以排在提示词最后。"""
        samples = []
        for token in ("今天", "明天", "后天", "大后天"):
            samples.append(f"{token}={self.resolve(token).isoformat()}")
        monday = self.today - _dt.timedelta(days=self.today.weekday())
        tail = (f"下周一={(monday + _dt.timedelta(days=7)).isoformat()}，"
                f"本周五={(monday + _dt.timedelta(days=4)).isoformat()}")
        tomorrow = self.resolve("明天").isoformat()
        blocks = [
            "# 本轮的基准\n\n"
            f"{self.line()}所有日期都按这个基准换算。\n\n"
            f"这次能直接抄的结果：{'、'.join(samples)}；{tail}。\n\n"
            f"只说「几号」时：基准 {self.iso}，说\"六号\"就是 {self.next_day(6)}。\n\n"
            f"抄的时候在 `calc` 里写清怎么算的，例如 `calc=\"明天={tomorrow}\"`。",
        ]
        param = self.param_block()
        if param:
            blocks.append(param)
        return "\n\n".join(blocks)


def from_iso(text: str | None, *, tz: str = "+08:00") -> Context:
    """从 `"2026-10-30"` 造一个基准；解析不了就用今天（并在调用处记一笔）。"""
    if text:
        try:
            return Context(today=_dt.date.fromisoformat(text.strip()[:10]), tz=tz)
        except ValueError:
            pass
    return Context(today=_dt.date.today(), tz=tz)


def default(with_params: bool = False) -> Context:
    ctx = Context(today=_dt.date.today())
    return with_params_for(ctx) if with_params else ctx


def param_values() -> tuple[tuple[str, Any, str], ...]:
    """读一遍参数快照。**失败就返回空**，让提示词少一段——不许因为读不到数据就报错。"""
    try:
        import paths  # noqa: F401  （挂 sys.path）
        from pathlib import Path

        from app.core.dsl.registry import parameters as param_reg
        from product_data import load_project

        root = Path(__file__).resolve().parents[2]
        data = load_project(root, base_id="基座01")
        out = []
        for row in param_reg._rows():
            if param_reg.route_of(row["参数名称"]) != "parameters":
                continue
            value = data.params.get(param_reg.to_internal_key(row["参数名称"]))
            if value is None:
                value = row.get("默认值")
            out.append((row["参数名称"], value, row.get("单位", "")))
        return tuple(out)
    except Exception:
        return ()


_CACHE: dict[str, Context] = {}


def window_values() -> tuple[tuple[str, str, str], ...]:
    """读海事窗口的起止。**失败就返回空**，同 param_values。"""
    try:
        import paths  # noqa: F401
        from pathlib import Path

        from product_data import load_project

        root = Path(__file__).resolve().parents[2]
        data = load_project(root, base_id="基座01")
        out = []
        for win in data.observation_windows:
            out.append((win["编号"], data.stamp(win["开始"]), data.stamp(win["结束"])))
        return tuple(out)
    except Exception:
        return ()


def with_params_for(ctx: Context) -> Context:
    """给上下文接上现值。按"基准日 + 基座"缓存，同一进程里只读一次数据。"""
    if ctx.params:
        return ctx
    key = f"{ctx.iso}|{ctx.base_id}"
    hit = _CACHE.get(key)
    if hit is None:
        hit = Context(today=ctx.today, tz=ctx.tz, base_id=ctx.base_id,
                      params=param_values(), windows=window_values())
        _CACHE[key] = hit
    return hit


def find_relative(text: str) -> list[str]:
    """原文里出现的、需要基准才能落地的相对时间说法（长的优先，避免"大后天"被拆成"后天"）。

    分三轮：先认能唯一确定的词，再认"下周X"这类带星期的写法，最后才认有歧义的。
    顺序不能反——"下周一"里含"下周"，先扫歧义词会把能算的当成不能算的。
    """
    hits: list[str] = []
    stripped = text or ""

    for token in sorted(_OFFSET, key=len, reverse=True):
        if token in stripped:
            hits.append(token)
        stripped = stripped.replace(token, " ")

    for match in _WEEKDAY_RE.finditer(stripped):
        hits.append(match.group(0))
    stripped = _WEEKDAY_RE.sub(" ", stripped)

    for token in _AMBIGUOUS:
        if token in stripped:
            hits.append(token)

    seen: list[str] = []
    for hit in hits:
        if hit not in seen:
            seen.append(hit)
    return seen
