"""读请求的执行器：`ask` 类 claim 真的有人回答，而不是只统计占比。

三类问题三种取数方式，共同点是**答案必须来自数据，不能来自模型的嘴**：

| about | 用户问什么 | 从哪答 |
|---|---|---|
| `fact` | 「现在船租多少」「每天能干几小时」 | 参数快照（`product_data.load_project`），缺项退回注册表默认值 |
| `reason` | 「为什么这么贵」 | 独立核算：船租/人工/备件/发电损失各占多少 |
| `diff` | 「经济优先和风险优先差多少」 | 同限时下换策略各解一次，比完成数、总成本、最晚完工 |

三条自我约束：

1. **答不上来就说答不上来。** 匹配不到参数不许编一个像的。
2. **必须报口径。** "启发式基线""限时 4 秒""前 6 个任务"——这些不是免责声明，
   是答案的一部分：不知道口径的数，等于没有数。
3. **不调模型。** 这一步全程确定性，所以它的输出可以当基准去校验别的东西。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import execute
import paths  # noqa: F401  （把 ../backend 与求解内核挂进 sys.path）

from app.core.dsl.registry import parameters as param_reg

# 用户口语 → 注册表参数名。**只收能一一对应的**；
# 「风速」这种要分常态/安全的，宁可反问也不猜——猜错的代价是用户按错误的值做决定。
_ALIASES: tuple[tuple[str, str], ...] = (
    ("船租", "船舶日租金"),
    ("船舶日租金", "船舶日租金"),
    ("租船", "船舶日租金"),
    ("人工单价", "维修人工单价"),
    ("人工", "维修人工单价"),
    ("人时", "维修人工单价"),
    ("工时", "每日有效维修上限"),
    ("每天干", "每日有效维修上限"),
    ("干几小时", "每日有效维修上限"),
    ("几小时", "每日有效维修上限"),
    ("维修上限", "每日有效维修上限"),
    ("作业线", "最大作业线"),
    ("几条船", "最大作业线"),
    ("人数", "可调动人员总数"),
    ("人员", "可调动人员总数"),
    ("常态风速", "常态风速上限"),
    ("安全风速", "安全风速上限"),
    ("常态浪高", "常态浪高上限"),
    ("安全浪高", "安全浪高上限"),
    ("夜间转移", "允许夜间转移"),
    ("跨天", "允许跨天暂停"),
    ("开工粒度", "候选开工粒度"),
)

# 策略口语 → 求解器策略名
_STRATEGY_ALIASES: tuple[tuple[str, str], ...] = (
    ("成本最低", "成本最低优先"),
    ("成本", "成本最低优先"),
    ("省钱", "成本最低优先"),
    ("经济", "成本最低优先"),
    ("高风险", "高风险优先抢修"),
    ("风险优先", "高风险优先抢修"),
    ("风险", "高风险优先抢修"),
    ("最短完成", "最短完成优先"),
    ("早点收工", "最短完成优先"),
    ("越快", "最短完成优先"),
    ("最多器件", "检修最多器件优先"),
    ("修得最多", "检修最多器件优先"),
)

_MONEY = re.compile(r"([\d.]+)\s*(亿|万|千)?\s*元?")


@dataclass
class Reading:
    about: str = ""
    question: str = ""
    ok: bool = False
    lines: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    caveat: str = ""
    seconds: float = 0.0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"about": self.about, "question": self.question, "ok": self.ok,
                "lines": self.lines, "data": self.data, "caveat": self.caveat,
                "seconds": self.seconds, "error": self.error}


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.1f} 万元"
    return f"{value:,.0f} 元"


def _when(text: str | None) -> str:
    """`2026-11-18T18:00:00+08:00` → `11月18日 18:00`。年份对本场景没用，留着反而难读。"""
    if not text:
        return "—"
    found = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", str(text))
    if not found:
        return str(text)
    _y, month, day, hour, minute = found.groups()
    return f"{int(month)}月{int(day)}日 {hour}:{minute}"


def _param_rows() -> list[dict]:
    return [row for row in param_reg._rows() if param_reg.route_of(row["参数名称"]) == "parameters"]


# 「风速」「浪高」这类说法对应两个参数，谁也没权替用户挑一个。
# 不猜——两个都答，让用户看到"常态 10 / 安全 14"再决定问的是哪个。
_GENERIC: dict[str, tuple[str, ...]] = {
    "风速": ("常态风速上限", "安全风速上限"),
    "浪高": ("常态浪高上限", "安全浪高上限"),
}


def _match_params(text: str) -> list[str]:
    """这句话在问哪些参数。空列表 = 问的东西不在参数表里。"""
    body = text or ""
    hits: list[str] = []
    for word, name in _ALIASES:
        if word in body:
            hits.append(name)
    for row in _param_rows():
        if row["参数名称"] in body:
            hits.append(row["参数名称"])
    for word, names in _GENERIC.items():
        if word in body and not any(specific in body for specific in names) \
                and not any(specific in hits for specific in names):
            hits.extend(names)
    return list(dict.fromkeys(hits))


def _match_param(text: str) -> tuple[str | None, list[str]]:
    """兼容旧调用：唯一命中才算命中，多个命中当作问的是多个。"""
    names = _match_params(text)
    return (names[0] if len(names) == 1 else None), names


def _fmt(value: Any) -> str:
    """12000.0 显示成 12000；60.0 显示成 60。整数不带小数点。"""
    if isinstance(value, float) and value.is_integer():
        return f"{int(value)}"
    return f"{value}"


def _current(name: str) -> tuple[Any, str]:
    """读现值。快照里没有的，退回注册表默认值，并说明来源。"""
    try:
        from product_data import load_project
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        data = load_project(root, base_id="基座01")
        key = param_reg.to_internal_key(name)
        value = data.params.get(key)
        if value is not None:
            return value, "参数快照"
    except Exception as exc:                                  # 读不到不是致命错误
        return None, f"读取失败：{type(exc).__name__}"
    for row in _param_rows():
        if row["参数名称"] == name:
            return row["默认值"], "注册表默认值（快照里没有这一项）"
    return None, "查不到"


def answer_fact(question: str) -> Reading:
    names = _match_params(question)
    if not names:
        known = "、".join(row["参数名称"] for row in _param_rows())
        return Reading(about="fact", question=question, ok=False,
                       lines=[f"查不了：这句话里没有我能直接查的参数名。",
                              f"能直接查的有：{known}。",
                              "换一个说法问，或者直接说参数名。"],
                       error="no_param")

    lines, data, sources = [], [], set()
    for name in names:
        value, source = _current(name)
        row = next((r for r in _param_rows() if r["参数名称"] == name), {})
        unit = row.get("单位", "")
        lines.append(f"{name}现在是 {_fmt(value)} {unit}（来源：{source}）。")
        lines.append(f"　可改范围：{row.get('取值约束', '—')}。")
        data.append({"param": name, "value": value, "unit": unit,
                     "source": source, "domain": row.get("取值约束")})
        sources.add(source)

    if len(names) > 1:
        lines.insert(0, "你问的可能不是一个东西，两个都报：")
    caveat = ("" if sources == {"参数快照"}
              else "有参数不是当前场景的值（快照里没有），别拿它做决定")
    return Reading(about="fact", question=question,
                   ok=all(item["value"] is not None for item in data),
                   lines=lines, data={"params": data}, caveat=caveat)


def answer_reason(question: str) -> Reading:
    """「为什么这么贵」= 把账摊开。摊不开就只能说"算不出来"。"""
    result = execute.profile()
    if not result.ok:
        return Reading(about="reason", question=question, ok=False,
                       seconds=result.seconds,
                       lines=[f"算不出来：{result.error or result.status}"],
                       error=result.status)

    profiles = result.payload.get("profiles") or []
    if not profiles:
        return Reading(about="reason", question=question, ok=False,
                       seconds=result.seconds, lines=["没有任何可比方案"], error="empty")
    best = min(profiles, key=lambda p: p["总成本"])
    cash, total = best["现金支出"], best["总成本"]
    parts = [("船租", best["船租"]), ("人工", best["人工"]), ("备件", best["备件"]),
             ("其他", best["其他费用"]), ("发电损失", best["发电损失"])]
    parts.sort(key=lambda kv: -kv[1])
    lead, lead_value = parts[0]
    share = (lead_value / total * 100) if total else 0

    lines = [
        f"这批 {best['待修总数']} 个任务，一条基线排程要花 {_money(total)}"
        f"（其中现金支出 {_money(cash)}）。",
        f"最大的一块是**{lead}**：{_money(lead_value)}，占总成本 {share:.0f}%。",
        "拆开看：" + "；".join(f"{name} {_money(value)}" for name, value in parts) + "。",
    ]
    rental_days = best.get("船舶艘天")
    if lead == "船租" and rental_days:
        unit = _current("船舶日租金")[0]
        lines.append(f"船租按 {rental_days} 个「船·天」算，日租 {_money(float(unit or 0))}；"
                     "压船租就是压艘天——把任务排在同一个窗口、同一条作业线上，艘天就少。")
    if best.get("未安排"):
        lines.append(f"另有 {len(best['未安排'])} 个任务这条基线排不进去："
                     f"{'、'.join(best['未安排'][:5])}——它们会变成未完成，"
                     "在成本里体现为发电损失而不是现金支出。")

    return Reading(about="reason", question=question, ok=True, lines=lines,
                   data={"best": best, "profiles": profiles},
                   seconds=result.seconds,
                   caveat="口径：两条启发式基线里取总成本较低的那条，**不是求解器的最优解**。"
                          "它的用处是看结构（谁占大头），不是报最终报价。")


def _strategies_of(text: str) -> list[str]:
    """按在句子里出现的**先后**排，不按词表顺序——
    "成本和最短完成"用户先说的是成本，答案里就该先说成本。"""
    body = text or ""
    hits = sorted(((body.index(word), name) for word, name in _STRATEGY_ALIASES if word in body),
                  key=lambda pair: pair[0])
    seen: list[str] = []
    for _pos, name in hits:
        if name not in seen:
            seen.append(name)
    if len(seen) >= 2:
        return seen[:2]
    if len(seen) == 1:
        rest = [s for s in ("成本最低优先", "高风险优先抢修") if s != seen[0]]
        return [seen[0], rest[0]]
    return ["成本最低优先", "高风险优先抢修"]


def answer_diff(question: str, *, seconds: int = 4, max_tasks: int = 4) -> Reading:
    strategies = _strategies_of(question)
    result = execute.diff(strategies, seconds=seconds, max_tasks=max_tasks)
    if not result.ok:
        return Reading(about="diff", question=question, ok=False, seconds=result.seconds,
                       lines=[f"算不出来：{result.error or result.status}"], error=result.status)

    rows = [r for r in (result.payload.get("rows") or []) if r.get("状态") not in ("ERROR",)]
    if len(rows) < 2:
        return Reading(about="diff", question=question, ok=False, seconds=result.seconds,
                       lines=["两次求解没都拿到方案，没法比"], data={"rows": rows},
                       error="not_comparable")

    left, right = rows[0], rows[1]
    lines = []
    if left.get("总成本") is not None and right.get("总成本") is not None:
        delta = right["总成本"] - left["总成本"]
        better = left if delta > 0 else right
        lines.append(f"{left['策略']} 总成本 {_money(left['总成本'])}，"
                     f"{right['策略']} {_money(right['总成本'])}，"
                     f"差 {_money(abs(delta))}（{better['策略']} 更低）。")
    if left.get("最晚完工") and right.get("最晚完工"):
        lines.append(f"最晚完工：{left['策略']} {_when(left['最晚完工'])}；"
                     f"{right['策略']} {_when(right['最晚完工'])}。")
    if left.get("现金支出") is not None and right.get("现金支出") is not None:
        lines.append(f"现金支出：{_money(left['现金支出'])} 对 {_money(right['现金支出'])}——"
                     "这个才是财务口径，总成本还含发电损失。")
    if left.get("完成任务数") != right.get("完成任务数"):
        lines.append(f"完成数不同：{left['完成任务数']} 对 {right['完成任务数']}，"
                     "完成数不一样时，比总成本没有意义。")

    limited = [r["策略"] for r in rows if r.get("状态") != "OPTIMAL"]
    caveat = (f"口径：只取前 {result.payload.get('tasks')} 个任务、每个策略限时 "
              f"{result.payload.get('seconds')} 秒。")
    if limited:
        caveat += f"其中 {'、'.join(limited)} 在时限内没证明最优，这两个数只能当参考。"
    return Reading(about="diff", question=question, ok=True, lines=lines,
                   data={"rows": rows, "strategies": strategies,
                         "tasks": result.payload.get("tasks"),
                         "seconds": result.payload.get("seconds")},
                   seconds=result.seconds, caveat=caveat)


def answer(question: str, about: str, **kwargs: Any) -> Reading:
    if about == "fact":
        return answer_fact(question)
    if about == "reason":
        return answer_reason(question)
    if about == "diff":
        return answer_diff(question, **kwargs)
    return Reading(about=about or "?", question=question, ok=False,
                   lines=[f"about={about!r} 不在 fact/reason/diff 之内"], error="bad_about")


def answer_all(product: dict[str, Any], **kwargs: Any) -> list[Reading]:
    """把产物里所有 ask claim 跑一遍。同一句话里问两遍同一个东西，只答一次。"""
    out: list[Reading] = []
    seen: set[tuple[str, str]] = set()
    for claim in product.get("claims") or []:
        if claim.get("kind") != "ask":
            continue
        about = claim.get("about") or ""
        key = (about, "|".join(_match_params(str(claim.get("text") or ""))) or str(claim.get("text")))
        if key in seen:
            continue
        seen.add(key)
        out.append(answer(str(claim.get("text") or ""), about, **kwargs))
    return out
