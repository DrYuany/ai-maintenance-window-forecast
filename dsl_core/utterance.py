"""原话 ↔ DSL 对账。

整条链是：用户原话 →（模型解析）→ DSL →（编译器）→ 数学模型 →（reconcile）→ 产物。
`reconcile.py` 管后一段：DSL 里写的，产物是不是真做到了。
这一段管前一段：**用户原话里那些机器能判的诉求，DSL 里到底有没有落点**。

原来这一段完全空着。真出过的事：用户说"只修前 8 个"，模型编出 8 条 must，
DSL 内部自洽，对账通过，求解却把 11 个任务全排上——用户批准的是一个不存在的
变更，而且没有任何一层会发现。

## 只判三类能判死的事

不判"模型理解得对不对"——那需要另一个模型来判，且无法证明，等于用一句更流畅
的话掩盖一句流畅的话。只判三类能用集合运算或算术判死的事：

1. 数量：原话说"只修 / 最多 N 个"，这份 DSL 最多会不会排出超过 N 个
2. 对象：原话点名排除 / 限定 / 先后的任务、风机，DSL 里有没有对应条目
3. 数值：原话给的日期、工时，DSL 里那个数是不是同一个

抽不到断言的句子不进检查项（不假装核过），但会进"未核对片段"，由预览原样
列给用户看。宁可说"这句我没核"，不能说"全部核对通过"。

## 三态

- 违例：原话与 DSL 直接冲突，或原话的硬要求 DSL 里根本没有落点 → 不出方案
- 未核对：抽到了断言，但缺判定所需的信息（任务不在册、时间读不出来）
- 通过：每条断言都在 DSL 里找到了落点，且不冲突
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PASS = "通过"
FAIL = "违例"
UNVERIFIED = "未核对"


# ─────────────────────────── 在册世界 ───────────────────────────


@dataclass(frozen=True)
class World:
    """判定所需的最小事实集：哪些任务在册、各属哪个风机、什么部件、多严重。

    `parts` 的键是部件编号，值原样保留部件库行——`compiler.task_matches` 的
    `part` 维度要读 `data.parts`，所以这里必须同名同形，能直接复用那份过滤逻辑。
    """

    tasks: tuple[dict[str, Any], ...]
    parts: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def task_ids(self) -> set[str]:
        return {str(row["任务编号"]) for row in self.tasks}

    def by_turbine(self, turbine: str) -> set[str]:
        return {str(row["任务编号"]) for row in self.tasks if str(row.get("风机编号")) == turbine}

    def row(self, task_id: str) -> dict[str, Any] | None:
        for row in self.tasks:
            if str(row["任务编号"]) == task_id:
                return row
        return None


def world_from_data(data: Any) -> World:
    """从 `load_project` 的产物取判定所需字段（不保留整份数据）。"""
    tasks = tuple(
        {
            "任务编号": str(row.get("任务编号")),
            "风机编号": str(row.get("风机编号") or ""),
            "严重等级": str(row.get("严重等级") or ""),
            "部件编号": str(row.get("部件编号") or ""),
        }
        for row in (getattr(data, "tasks", None) or [])
    )
    parts = {str(pid): dict(row) for pid, row in (getattr(data, "parts", None) or {}).items()}
    return World(tasks=tasks, parts=parts)


_WORLD_CACHE: dict[str, World | None] = {}


def world_from_project(project_root: Any = None) -> World | None:
    """读一次项目在册数据，缓存住。读不到返回 None（调用方必须如实说读不到）。"""
    key = str(project_root or "")
    if key in _WORLD_CACHE:
        return _WORLD_CACHE[key]
    world: World | None = None
    try:
        from .runner import PROJECT_ROOT, _solver_modules

        load_project = _solver_modules()[0]
        data = load_project(project_root or PROJECT_ROOT, base_id="基座01")
        world = world_from_data(data)
    except Exception:  # noqa: BLE001 - 读不到就是读不到，由调用方报出来
        world = None
    _WORLD_CACHE[key] = world
    return world


# ─────────────────────────── 原话切分与断言抽取 ───────────────────────────


@dataclass
class Claim:
    kind: str
    segment: str
    expect: str
    payload: dict[str, Any] = field(default_factory=dict)


_SEGMENT_SPLIT = re.compile(r"[，。；;！!？?\n]+")
_QUESTION = re.compile(r"[？?]|吗|呢|多少|为什么|怎么|行不行|可不可以|是不是")
# 一句里出现这些词才值得进"未核对片段"（"谢谢""好的"这类不列）
_WORTH_LISTING = re.compile(r"修|排|任务|风机|小时|月|日|前|别|不|只|最多|至少|先|后|其余|剩下|其他")

# 任务 / 风机引用。裸三位数只在不像年份时才算（2026 这种四位数会被前后断言挡掉）。
_TASK_REF = re.compile(r"任务\s*(\d{1,3})|[Tt]\s*(\d{1,3})|(?<![\d.])(\d{3})(?![\d.])")
_TURBINE_REF = re.compile(r"(\d{1,3})\s*号风机|风机\s*(\d{1,3})")
_RANGE = re.compile(r"(\d{1,3})\s*(?:号)?\s*(?:到|至|~|-|—|–)\s*(?:任务|T)?\s*(\d{1,3})")

_CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

_COUNT = re.compile(
    r"(只|仅|最多|至多|顶多)\s*(?:修|做|排|安排|完成|干|处理|抢修)?\s*(?:前\s*)?"
    r"(\d{1,3}|[一二两三四五六七八九十]{1,2})\s*(个|台|项)(?!\s*(?:船|线|作业线|人|人员|小时|分钟))"
)
_NEGATION = re.compile(
    r"(?:不修|不排|别修|别排|不要修|不要排|不要安排|不安排|不用修|不用排|排除|剔除|去掉|跳过|不做|不干)"
)
_DATE = re.compile(r"(?:(\d{4})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
_DAY_RANGE = re.compile(r"\s*(?:到|至|-|~|—|–)\s*(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*[日号]")
_DEADLINE_HINT = re.compile(r"(?:之前|以前|前|内)\s*(?:必须|要|得|完成|修完|做完|干完)?")
_DEADLINE_PREFIX = re.compile(r"(?:不(?:能|得|可)?晚于|最晚|最迟|不得超过)\s*$")
_BAN_HINT = re.compile(r"(?:别|不要|不能|禁止|不得|不许)\s*(?:开工|作业|施工|动工|上人)")
# 备件到货/入库是数据变更，不是任务期限
_SUPPLY_WORDS = re.compile(r"备件|到货|入库|到港|采购|库存|领料")
_HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*个?\s*小时")
_ORDER_BEFORE = re.compile(r"先(?:修|做|排|安排|干|处理)?")


def _segments(text: str) -> list[str]:
    return [part.strip() for part in _SEGMENT_SPLIT.split(text) if part.strip()]


def _is_question(segment: str) -> bool:
    return _QUESTION.search(segment) is not None


def _refs(text: str) -> list[tuple[str, str]]:
    """句子里点到的任务/风机，按出现顺序去重。"""
    turbines: list[tuple[int, int, str]] = []
    for match in _TURBINE_REF.finditer(text):
        raw = match.group(1) or match.group(2)
        turbines.append((match.start(), match.end(), f"风机{int(raw):03d}"))
    found: list[tuple[int, str]] = []
    for match in _TASK_REF.finditer(text):
        if any(start <= match.start() < end for start, end, _ in turbines):
            continue
        raw = match.group(1) or match.group(2) or match.group(3)
        found.append((match.start(), f"任务{int(raw):03d}"))
    merged: list[tuple[int, str]] = [(start, ref) for start, _, ref in turbines] + found
    merged.sort()
    seen: list[tuple[str, str]] = []
    for _pos, ref in merged:
        kind = "turbine" if ref.startswith("风机") else "task"
        if (kind, ref) not in seen:
            seen.append((kind, ref))
    return seen


def _expand_ranges(segment: str) -> list[str]:
    """「009 到 011」补成 009/010/011。只认两位数以上的区间，避免把「8 到 9 个」当任务。"""
    out: list[str] = []
    for match in _RANGE.finditer(segment):
        low, high = int(match.group(1)), int(match.group(2))
        if high <= low or high - low > 50:
            continue
        out.extend(f"任务{value:03d}" for value in range(low, high + 1))
    return out


def _cn_digits(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value in _CN_DIGIT:
        return _CN_DIGIT[value]
    if value == "十":
        return 10
    return None


def _count_claims(segment: str) -> list[Claim]:
    match = _COUNT.search(segment)
    if not match:
        return []
    qualifier, raw, _unit = match.groups()
    number = _cn_digits(raw)
    if number is None:
        return []
    strict = qualifier in ("只", "仅")
    return [
        Claim(
            kind="count",
            segment=segment,
            expect=f"{'只修' if strict else '最多修'} {number} 个任务",
            payload={"n": number, "strict": strict},
        )
    ]


def _exclude_claims(segment: str) -> list[Claim]:
    claims: list[Claim] = []
    for match in _NEGATION.finditer(segment):
        window = segment[max(0, match.start() - 16): match.end() + 16]
        refs = _refs(window)
        if not refs:
            continue
        tasks = [ref for kind, ref in refs if kind == "task"]
        turbines = [ref for kind, ref in refs if kind == "turbine"]
        extra = [ref for ref in _expand_ranges(window) if ref not in tasks]
        tasks.extend(extra)
        tasks.sort()
        if not tasks and not turbines:
            continue
        label = "、".join(tasks + turbines)
        claims.append(
            Claim(
                kind="exclude",
                segment=segment,
                expect=f"不修 {label}",
                payload={"tasks": tasks, "turbines": turbines},
            )
        )
    return claims


def _date_claims(segment: str) -> list[Claim]:
    # 备件到货 / 入库这类是**数据变更**，不是任务期限。混进来会把一条正确的
    # part_arrival 判成"没写期限"，然后挡住一次好变更。
    if _SUPPLY_WORDS.search(segment):
        return []
    claims: list[Claim] = []
    covered: set[int] = set()
    for match in _DATE.finditer(segment):
        if match.start() in covered:
            continue
        month, day = int(match.group(2)), int(match.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            continue
        days = _day_range(segment, match.end(), month, day)
        covered.update(range(match.start(), match.end()))
        tail = segment[match.end(): match.end() + 14]
        head = segment[max(0, match.start() - 20): match.start()]
        refs = [ref for kind, ref in _refs(head) if kind == "task"]
        if _BAN_HINT.search(tail):
            # "11月3日到5日别开工" 是一个区间：只核 3 号，4、5 号照样会被排活。
            for value in days:
                claims.append(
                    Claim(
                        kind="forbidden_date",
                        segment=segment,
                        expect=f"{month} 月 {value} 日不许开工",
                        payload={"month": month, "day": value},
                    )
                )
        elif (_DEADLINE_HINT.match(tail) or _DEADLINE_PREFIX.search(head)) and "开工" not in head:
            claims.append(
                Claim(
                    kind="deadline",
                    segment=segment,
                    expect=(
                        f"{'、'.join(refs)} 最晚 {month} 月 {day} 日完成"
                        if refs
                        else f"最晚 {month} 月 {day} 日完成"
                    ),
                    payload={"month": month, "day": day, "tasks": refs},
                )
            )
    return claims


def _day_range(segment: str, offset: int, month: int, day: int) -> list[int]:
    """「11月3日到5日」「3号到11月5号」→ [3, 4, 5]。认不出来就只返回那一天。"""
    match = _DAY_RANGE.match(segment, offset)
    if not match:
        return [day]
    end_month = int(match.group(1)) if match.group(1) else month
    end_day = int(match.group(2))
    if end_month != month or end_day <= day or end_day - day > 31:
        return [day]
    return list(range(day, end_day + 1))



def _hours_claim(segment: str, world: World) -> list[Claim]:
    match = _HOURS.search(segment)
    if not match:
        return []
    hours = float(match.group(1))
    if hours <= 0:
        return []
    part_name = next(
        (
            str(row.get("部件名称"))
            for row in world.parts.values()
            if row.get("部件名称") and str(row["部件名称"]) in segment
        ),
        None,
    )
    refs = [ref for kind, ref in _refs(segment) if kind == "task"]
    if part_name is None and not refs:
        return []
    severity = next(
        (value for word, value in (("严重", "高"), ("高危", "高"), ("中等", "中"), ("轻微", "低"))
         if word in segment),
        None,
    )
    label = part_name or "、".join(refs)
    return [
        Claim(
            kind="hours",
            segment=segment,
            expect=f"{label} 的维修工时改成 {hours:g} 小时",
            payload={"hours": hours, "part": part_name, "tasks": refs, "severity": severity},
        )
    ]


def _order_claim(segment: str) -> list[Claim]:
    if "先" not in segment or "再" not in segment:
        return []
    pivot = segment.find("再")
    before = [ref for kind, ref in _refs(segment[:pivot]) if kind == "task"]
    after = [ref for kind, ref in _refs(segment[pivot:]) if kind == "task"]
    if not before or not after:
        return []
    return [
        Claim(
            kind="order",
            segment=segment,
            expect=f"先修 {before[0]}，再修 {after[0]}",
            payload={"before": before[0], "after": after[0]},
        )
    ]


def _turbine_scope_claims(segment: str) -> list[Claim]:
    match = re.search(r"只\s*(?:修|动|排|安排|做|干)?\s*(?:(\d{1,3})\s*号风机|风机\s*(\d{1,3}))", segment)
    if not match:
        return []
    raw = match.group(1) or match.group(2)
    return [
        Claim(
            kind="turbine_scope",
            segment=segment,
            expect=f"只修 风机{int(raw):03d} 上的任务",
            payload={"turbine": f"风机{int(raw):03d}"},
        )
    ]


def extract_claims(utterance: str, world: World) -> list[Claim]:
    """把一句人话拆成若干条可判定断言。抽不到的句子不进结果。"""
    claims: list[Claim] = []
    for segment in _segments(utterance or ""):
        if _is_question(segment):
            continue
        claims.extend(_count_claims(segment))
        claims.extend(_exclude_claims(segment))
        claims.extend(_date_claims(segment))
        claims.extend(_hours_claim(segment, world))
        claims.extend(_order_claim(segment))
        claims.extend(_turbine_scope_claims(segment))
    # 同一句里"排除"和"数量"可能都抽到，各自独立判定；去重避免同义重复
    seen: set[tuple[str, str]] = set()
    unique: list[Claim] = []
    for claim in claims:
        key = (claim.kind, claim.expect)
        if key in seen:
            continue
        seen.add(key)
        unique.append(claim)
    return unique


# ─────────────────────────── DSL 侧事实 ───────────────────────────


@dataclass
class _Facts:
    excluded: set[str] = field(default_factory=set)
    musts: set[str] = field(default_factory=set)
    caps: list[tuple[dict[str, Any], str, Any]] = field(default_factory=list)
    deadlines: dict[str, str] = field(default_factory=dict)
    deadline_filters: list[tuple[dict[str, Any], str]] = field(default_factory=list)
    forbidden: list[tuple[int, int]] = field(default_factory=list)
    precedence: list[tuple[str, str]] = field(default_factory=list)
    fixes: dict[str, Any] = field(default_factory=dict)
    hours_patches: list[dict[str, Any]] = field(default_factory=list)


def _matches(task: dict[str, Any], spec: dict[str, Any], world: World) -> bool:
    from .compiler import task_matches

    return task_matches(task, spec, world)


def _facts(dsl: dict[str, Any], world: World) -> _Facts:
    facts = _Facts()
    for spec in dsl.get("constraints") or []:
        ctype = str(spec.get("type"))
        if ctype == "exclude":
            if spec.get("task"):
                facts.excluded.add(str(spec["task"]))
            elif spec.get("filter"):
                facts.excluded.update(
                    str(row["任务编号"]) for row in world.tasks
                    if _matches(row, spec["filter"], world)
                )
        elif ctype == "must":
            if spec.get("task"):
                facts.musts.add(str(spec["task"]))
            elif spec.get("filter"):
                facts.musts.update(
                    str(row["任务编号"]) for row in world.tasks
                    if _matches(row, spec["filter"], world)
                )
        elif ctype == "cardinality":
            facts.caps.append((spec.get("filter") or {}, str(spec.get("op") or "="), spec.get("value")))
        elif ctype == "deadline":
            if spec.get("task"):
                facts.deadlines[str(spec["task"])] = str(spec.get("time") or "")
            elif spec.get("filter"):
                facts.deadline_filters.append((spec["filter"], str(spec.get("time") or "")))
        elif ctype == "forbidden_dates":
            for item in spec.get("dates") or []:
                stamp = _month_day_of(str(item))
                if stamp:
                    facts.forbidden.append(stamp)
        elif ctype == "precedence":
            facts.precedence.append((str(spec.get("before")), str(spec.get("after"))))
        elif ctype == "fix":
            facts.fixes[str(spec.get("task"))] = spec.get("line")
    for spec in dsl.get("data") or []:
        kind = str(spec.get("kind"))
        if kind == "part_hours":
            facts.hours_patches.append(spec)
        elif kind == "task_deadline" and spec.get("at"):
            stamp = _month_day_of(str(spec["at"]))
            if stamp is None:
                continue
            severity = spec.get("severity") or (spec.get("filter") or {}).get("severity")
            if severity:
                facts.deadline_filters.append(({"severity": severity}, str(spec["at"])))
            elif spec.get("task"):
                facts.deadlines[str(spec["task"])] = str(spec["at"])
    return facts


def _month_day_of(text: str) -> tuple[int, int] | None:
    match = re.search(r"(?:(\d{4})-)?(\d{1,2})-(\d{1,2})", text)
    if not match:
        return None
    return int(match.group(2)), int(match.group(3))


def _deadline_of(task_id: str, facts: _Facts, world: World) -> tuple[int, int] | None:
    if task_id in facts.deadlines:
        return _month_day_of(facts.deadlines[task_id])
    row = world.row(task_id)
    for spec, when in facts.deadline_filters:
        if row is not None and _matches(row, spec, world):
            stamp = _month_day_of(when)
            if stamp:
                return stamp
    return None


def _base_hours(row: dict[str, Any], world: World) -> float | None:
    part = world.parts.get(str(row.get("部件编号")))
    if not part:
        return None
    level = {"高": "高", "中": "中", "低": "低"}.get(str(row.get("严重等级")))
    if level is None:
        return None
    value = part.get(f"{level}严重度常态维修工时（小时）")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _factor_for(row: dict[str, Any], facts: _Facts, world: World) -> tuple[float, bool]:
    """该任务实际生效的工时倍数（多条按乘算，与编译器一致），以及有没有被改过。"""
    part = world.parts.get(str(row.get("部件编号"))) or {}
    factor, touched = 1.0, False
    for spec in facts.hours_patches:
        ref = spec.get("part") or spec.get("partId")
        if not ref:
            continue
        if str(ref) in (str(row.get("部件编号")), str(part.get("部件名称")), str(part.get("部件编号"))):
            try:
                factor *= float(spec.get("factor") or 1.0)
            except (TypeError, ValueError):
                continue
            touched = True
    return factor, touched


def _effective_hours(row: dict[str, Any], facts: _Facts, world: World) -> float | None:
    base = _base_hours(row, world)
    if base is None:
        return None
    factor, _touched = _factor_for(row, facts, world)
    return base * factor


# ─────────────────────────── 判定 ───────────────────────────


def _cap(facts: _Facts, world: World, partial: bool) -> tuple[int, int]:
    """这份 DSL 最多会排几个任务：(上限, 在册任务数)。"""
    total = len(world.task_ids)
    if not partial:
        # 完整模式下每个任务都被强制排入，任何"少修"都做不到
        return total, total
    remaining = total - len(facts.excluded & world.task_ids)
    for spec, op, value in facts.caps:
        if spec:
            continue  # 带 filter 的完成数是别的口径，不能当成总数上限
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if op in ("<=", "=", "=="):
            remaining = min(remaining, number)
    return max(remaining, 0), total


def _judge(claim: Claim, facts: _Facts, world: World, partial: bool) -> tuple[str, str]:
    if claim.kind == "count":
        number = int(claim.payload["n"])
        cap, total = _cap(facts, world, partial)
        strict = bool(claim.payload.get("strict"))
        scope = f"在册 {total} 个任务，排除 {len(facts.excluded & world.task_ids)} 个"
        if strict:
            if cap == number:
                return PASS, f"最多会排 {cap} 个，与你说的一致（{scope}）"
            if cap > number:
                return FAIL, (
                    f"这份改动没有把数量压到 {number} 个：{scope}，最多会排 {cap} 个。"
                    f"要只修 {number} 个，得加「完成数 ≤ {number}」，或把其余任务列进排除"
                )
            return FAIL, f"最多只能排 {cap} 个，做不到你说的 {number} 个（{scope}）"
        if cap <= number:
            return PASS, f"最多会排 {cap} 个，没超过 {number}（{scope}）"
        return FAIL, (
            f"这份改动没有数量上限：{scope}，最多会排 {cap} 个，超过了 {number}。"
            f"要限 {number} 个，得加「完成数 ≤ {number}」，或把其余任务列进排除"
        )

    if claim.kind == "exclude":
        wanted: list[str] = []
        unknown: list[str] = []
        for task_id in claim.payload.get("tasks") or []:
            (wanted if task_id in world.task_ids else unknown).append(task_id)
        for turbine in claim.payload.get("turbines") or []:
            members = world.by_turbine(turbine)
            if members:
                wanted.extend(sorted(members))
            else:
                unknown.append(turbine)
        missing = [task_id for task_id in wanted if task_id not in facts.excluded]
        if missing:
            return FAIL, f"{'、'.join(missing)} 不在排除列表里，求解会照排"
        if wanted:
            return PASS, f"{'、'.join(wanted)} 都已排除"
        return UNVERIFIED, f"{'、'.join(unknown)} 不在当前在册任务里，无法确认"

    if claim.kind == "deadline":
        month, day = int(claim.payload["month"]), int(claim.payload["day"])
        tasks = claim.payload.get("tasks") or []
        if not tasks:
            any_match = any(
                stamp == (month, day)
                for stamp in (
                    [_month_day_of(value) for value in facts.deadlines.values()]
                    + [_month_day_of(value) for _spec, value in facts.deadline_filters]
                )
                if stamp
            )
            if any_match:
                return PASS, f"DSL 里有 {month} 月 {day} 日的期限"
            return UNVERIFIED, "原话没点名哪几个任务，DSL 里也没有这个日期的期限"
        wrong: list[str] = []
        missing: list[str] = []
        for task_id in tasks:
            stamp = _deadline_of(task_id, facts, world)
            if stamp is None:
                missing.append(task_id)
            elif stamp != (month, day):
                wrong.append(f"{task_id}（DSL 写的是 {stamp[0]} 月 {stamp[1]} 日）")
        if missing and not wrong:
            return FAIL, (
                f"{'、'.join(missing)} 在 DSL 里没有任何完工期限，"
                f"原话要求的 {month} 月 {day} 日不会生效"
            )
        if wrong:
            return FAIL, "期限日期对不上：" + "、".join(wrong) + f"，原话是 {month} 月 {day} 日"
        return PASS, f"{'、'.join(tasks)} 的期限就是 {month} 月 {day} 日"

    if claim.kind == "forbidden_date":
        month, day = int(claim.payload["month"]), int(claim.payload["day"])
        if (month, day) in facts.forbidden:
            return PASS, f"DSL 里已禁止 {month} 月 {day} 日开工"
        return FAIL, f"DSL 里没有禁止 {month} 月 {day} 日开工，那天仍会被排活"

    if claim.kind == "order":
        before, after = str(claim.payload["before"]), str(claim.payload["after"])
        if any(a == before and b == after for a, b in facts.precedence):
            return PASS, f"DSL 里 {before} 先于 {after}"
        if any(a == after and b == before for a, b in facts.precedence):
            return FAIL, f"DSL 把顺序写反了：现在要求 {after} 先于 {before}"
        return FAIL, f"DSL 里没有 {before} → {after} 的先后约束，两者会各自排"

    if claim.kind == "turbine_scope":
        turbine = str(claim.payload["turbine"])
        members = world.by_turbine(turbine)
        if not members:
            return UNVERIFIED, f"{turbine} 名下没有在册任务"
        if not partial:
            return FAIL, (
                f"完整模式会把在册 {len(world.task_ids)} 个任务全排上，"
                f"不止 {turbine} 的 {len(members)} 个"
            )
        schedulable = world.task_ids - facts.excluded
        others = sorted(schedulable - members)
        if not others:
            return PASS, f"可排的只剩 {turbine} 的任务"
        return FAIL, f"还会排 {len(others)} 个其他风机的任务（如 {'、'.join(others[:3])}）"

    if claim.kind == "hours":
        hours = float(claim.payload["hours"])
        part = claim.payload.get("part")
        tasks = list(claim.payload.get("tasks") or [])
        if not tasks and part:
            severity = claim.payload.get("severity")
            tasks = [
                str(row["任务编号"]) for row in world.tasks
                if str((world.parts.get(str(row.get("部件编号"))) or {}).get("部件名称")) == str(part)
                and (severity is None or str(row.get("严重等级")) == severity)
            ]
        if not tasks:
            return UNVERIFIED, "原话没点名改哪个任务/部件，也没有匹配的在册任务"
        values: list[str] = []
        mismatch: list[str] = []
        for task_id in tasks:
            row = world.row(task_id)
            if row is None:
                continue
            actual = _effective_hours(row, facts, world)
            if actual is None:
                continue
            values.append(f"{task_id} 现在 {actual:g} 小时")
            if abs(actual - hours) > 0.01:
                mismatch.append(task_id)
        if not values:
            return UNVERIFIED, "部件库里没有这几个任务的工时口径，没法核"
        if mismatch:
            return FAIL, "；".join(values) + f"，与你说的 {hours:g} 小时不一致"
        return PASS, "；".join(values)

    return UNVERIFIED, "没有对应的判定规则"


def _item(claim: Claim, verdict: str, evidence: str) -> dict[str, Any]:
    return {
        "类型": claim.kind,
        "诉求": claim.expect,
        "原话": claim.segment[:60],
        "结论": verdict,
        "证据": evidence,
    }


def audit_utterance(
    utterance: str,
    dsl: dict[str, Any] | None,
    world: World | None,
    *,
    partial: bool | None = None,
) -> dict[str, Any]:
    """原话 ↔ DSL 对账。`world` 缺失时如实报"没法核"，不当成通过。"""
    text = (utterance or "").strip()
    payload = dsl or {}
    if world is None:
        return {
            "原话": text,
            "检查项": 0,
            "对上": 0,
            "违例": [],
            "未核对": [],
            "明细": [],
            "未核对片段": [],
            "通过": False,
            "结论": "读不到在册任务清单，这句话没法核对",
        }
    if partial is None:
        from .compiler import needs_partial

        partial = needs_partial(payload)
    facts = _facts(payload, world)
    claims = extract_claims(text, world)
    checks = []
    for claim in claims:
        verdict, evidence = _judge(claim, facts, world, bool(partial))
        checks.append(_item(claim, verdict, evidence))
    violations = [item for item in checks if item["结论"] == FAIL]
    unverified = [item for item in checks if item["结论"] == UNVERIFIED]
    covered = {claim.segment for claim in claims}
    leftovers = [
        segment[:30]
        for segment in _segments(text)
        if segment not in covered
        and not _is_question(segment)
        and _WORTH_LISTING.search(segment)
        and len(segment) >= 4
    ]
    if not checks:
        conclusion = "没有可核对的诉求"
    elif violations:
        conclusion = "对不上"
    elif unverified:
        conclusion = "部分没能核对"
    else:
        conclusion = "对得上"
    return {
        "原话": text,
        "检查项": len(checks),
        "对上": sum(1 for item in checks if item["结论"] == PASS),
        "违例": violations,
        "未核对": unverified,
        "明细": checks,
        "未核对片段": leftovers[:3],
        # 一条断言都没抽到就不算通过：那不是"核过且没问题"，是"根本没核"。
        "通过": bool(checks) and not violations and not unverified,
        "结论": conclusion,
    }


def describe(audit: dict[str, Any], limit: int = 3) -> str:
    """一句话说清哪里对不上（给错误提示用）。"""
    violations = audit.get("违例") or []
    if violations:
        return "；".join(f"{item['诉求']}，但{item['证据']}" for item in violations[:limit])
    unverified = audit.get("未核对") or []
    if unverified:
        return "；".join(f"{item['诉求']}：{item['证据']}" for item in unverified[:limit])
    if audit.get("检查项"):
        return f"原话与 DSL 一致（{audit['检查项']} 条诉求）"
    if audit.get("结论"):
        return str(audit["结论"])
    return "这句话里没有系统能核对的具体诉求"


def format_audit(audit: dict[str, Any]) -> str:
    """预览里原样展示的核对结果。没有断言也要说清楚"没核"，不能默认通过。"""
    if audit.get("出错"):
        return f"原话核对：{audit['出错']}（这句话没有被核对过，不等于它没问题）"
    checked = int(audit.get("检查项") or 0)
    violations = audit.get("违例") or []
    unverified = audit.get("未核对") or []
    lines: list[str] = []
    if checked == 0:
        if audit.get("结论") and audit["结论"] != "没有可核对的诉求":
            # 比如读不到在册任务清单：这不是"没诉求"，是"核不了"，得说清楚。
            lines.append(f"原话核对：{audit['结论']}")
        else:
            lines.append(
                "原话核对：这句话里没有系统能核对的具体诉求（数量／对象／日期／工时／先后），"
                "下面的变更按 DSL 执行，但**不能说它等于你要的那件事**"
            )
    elif violations:
        lines.append(f"原话核对：{checked} 条诉求里有 {len(violations)} 条对不上")
    elif unverified:
        passed = int(audit.get("对上") or 0)
        lines.append(f"原话核对：{checked} 条诉求，对上 {passed} 条，{len(unverified)} 条没能核对")
    else:
        lines.append(f"原话核对：{checked} 条诉求全部对上")
    for item in audit.get("明细") or []:
        mark = "对不上" if item["结论"] == FAIL else (
            "没能核对" if item["结论"] == UNVERIFIED else "对上了"
        )
        lines.append(f"- {item['诉求']}（{mark}）：{item['证据']}")
    leftovers = audit.get("未核对片段") or []
    if leftovers:
        lines.append("这些片段系统没有核对：" + "｜".join(f"「{item}」" for item in leftovers))
    return "\n".join(lines)
