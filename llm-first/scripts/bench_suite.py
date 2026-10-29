"""跑评测集：一条一条过，把失败按原因归类。

判分只看机器能判的东西（route、验收有没有退、丢没丢数、DSL 里有没有那个参数/原语）。
**不判"写得漂不漂亮"**——那件事没有机器判据，硬判就会变成挑刺。

输出两样东西：
  1. 逐条结果（失败项带原因，方便直接改）；
  2. 归因统计（多少条是理解错、多少条是判据误伤、多少条是能力缺口）。

归因是这张表最有用的部分：同样是"没通过"，
"模型读错了"要改提示词，"判据误伤"要改判据，"能力缺口"要补原语——
三种的修法完全不同，混在一起看只会得出"效果不好"这种没用的结论。
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))

import context as session  # noqa: E402
import pipeline  # noqa: E402
import cases as casebook  # noqa: E402


def _dsl_sets(outcome) -> dict[str, set[str]]:
    dsl = outcome.product.get("dsl") or {}
    return {
        "params": {str(item.get("field")) for item in dsl.get("parameters") or []},
        "constraints": {str(item.get("type")) for item in dsl.get("constraints") or []},
        "objectives": {str(item.get("type")) for item in dsl.get("objectives") or []},
        "data": {str(item.get("kind")) for item in dsl.get("data") or []},
    }


def grade(case: dict, outcome) -> dict:
    route = outcome.route
    want_routes = {case["route"]} if "route" in case else set(case.get("route_in") or {"dsl"})
    route_ok = route in want_routes

    fails = [c.code for c in (outcome.report.checks if outcome.report else []) if c.verdict == "fail"]
    unclear = [c.code for c in (outcome.report.checks if outcome.report else []) if c.verdict == "unclear"]
    numbers = outcome.numbers or {}
    missing = list(numbers.get("missing") or []) if numbers.get("applicable", True) else []
    dropped = [int(x) for x in missing]
    present = _dsl_sets(outcome)

    absent: list[str] = []
    for key in ("params", "constraints", "objectives", "data"):
        for name in case.get(key) or []:
            if name not in present[key]:
                absent.append(f"{key}:{name}")

    # 只在"打算执行"时才查不该碰的东西：route=dsl 才会真落模型。
    # clarify 的草稿不会被编译，里面留着一次失败的尝试无害；
    # 这时候判"碰了不该碰的"是把"改错了主意"当成"做错了事"。
    if route == "dsl":
        for name in case.get("forbid_params") or []:
            if name in present["params"]:
                absent.append(f"不该改参数:{name}")
        for name in case.get("forbid_constraints") or []:
            if name in present["constraints"]:
                absent.append(f"不该写约束:{name}")

    has_unassigned = bool(outcome.product.get("unassigned"))
    unassigned_ok = True
    if case.get("unassigned") is True and not has_unassigned:
        unassigned_ok = False
    if case.get("unassigned") is False and has_unassigned:
        unassigned_ok = False

    reasons: list[str] = []
    if not route_ok:
        reasons.append(f"route={route}（要 {sorted(want_routes)}）")
    if fails:
        reasons.append(f"验收退回 {'/'.join(fails)}")
    if dropped:
        reasons.append(f"丢数 {dropped}")
    if absent:
        reasons.append("缺 " + "、".join(absent))
    if not unassigned_ok:
        reasons.append("该有 unassigned 没有" if case.get("unassigned") else "不该有 unassigned")

    return {
        "id": case["id"], "tag": case["tag"], "text": case["text"], "note": case.get("note", ""),
        "route": route, "want": "/".join(sorted(want_routes)),
        "ok": not reasons, "reasons": reasons,
        "fails": fails, "unclear": unclear, "dropped": dropped,
        "attempts": outcome.attempts, "ms": outcome.latency_ms,
        "cached": outcome.cached,
    }


def attribute(rows: list[dict]) -> Counter:
    """把失败归成三类：读错了 / 判据误伤 / 能力缺口 / 其他。

    判据误伤与读错了的区别：验收退了但 route 对、DSL 该有的都有——
    那多半是判据在挑模型没犯的错（假警报），不是理解错。
    """
    out: Counter = Counter()
    for row in rows:
        if row["ok"]:
            out["通过"] += 1
            continue
        only_route = all(r.startswith("route=") for r in row["reasons"])
        judged = any(r.startswith("验收退回") for r in row["reasons"])
        missing_bit = any(r.startswith("缺 ") for r in row["reasons"])
        if only_route:
            out["判断分歧（route 不同，其余都对）"] += 1
        elif judged and not missing_bit and row["route"] == row["want"].split("/")[0]:
            out["判据误伤（route 对、验收退）"] += 1
        elif missing_bit:
            out["表达缺口（DSL 里没有该有的项）"] += 1
        else:
            out["其他"] += 1
    return out


def run(suite_name: str, *, limit: int | None = None, no_cache: bool = True,
        today: str | None = None) -> list[dict]:
    ctx = session.from_iso(today)
    items = casebook.suite(suite_name)
    if limit:
        items = items[:limit]
    rows: list[dict] = []
    for index, case in enumerate(items, start=1):
        started = time.monotonic()
        outcome = pipeline.understand(case["text"], ctx=ctx, use_cache=not no_cache)
        row = grade(case, outcome)
        row["wall"] = int((time.monotonic() - started) * 1000)
        rows.append(row)
        mark = "✓" if row["ok"] else "✗"
        print(f"{index:>3}/{len(items)} {mark} {row['id']:<5} {row['text'][:26]:<28} "
              f"{'；'.join(row['reasons'])[:70]}", flush=True)
    return rows


def repeat(suite_name: str, times: int, *, limit: int | None = None,
           today: str | None = None) -> tuple[list[dict], list[dict]]:
    """同一套用例连跑 N 遍，把"一直错"和"时好时坏"分开。

    单跑一遍会把两件性质完全不同的事混成一个数字：
    一条因为读错而失败，和一条十次里错三次，修法完全不同——
    前者要改提示词，后者要问"为什么同一句话两次结果不一样"。
    实测温度已经是 0，剩下的抖动来自上游采样本身，只能靠重复跑测量，不能靠猜。
    """
    tally: dict[str, dict] = {}
    last: dict[str, dict] = {}
    for round_no in range(1, times + 1):
        print(f"\n── 第 {round_no}/{times} 遍 ──", flush=True)
        rows = run(suite_name, limit=limit, no_cache=True, today=today)
        for row in rows:
            slot = tally.setdefault(row["id"], {"id": row["id"], "text": row["text"],
                                                "tag": row["tag"], "pass": 0, "runs": 0})
            slot["runs"] += 1
            slot["pass"] += 1 if row["ok"] else 0
            last[row["id"]] = row

    rows_out = []
    for case_id, slot in tally.items():
        row = dict(last[case_id])
        row["pass_runs"] = slot["pass"]
        row["runs"] = slot["runs"]
        if slot["pass"] == slot["runs"]:
            row["stability"] = "稳过"
        elif slot["pass"] == 0:
            row["stability"] = "稳错"
        else:
            row["stability"] = "抖动"
        row["ok"] = slot["pass"] == slot["runs"]
        if row["stability"] == "稳过":
            row["reasons"] = []
        elif row["stability"] == "抖动":
            row["reasons"] = [f"{slot['runs'] - slot['pass']}/{slot['runs']} 遍没过"]
        rows_out.append(row)
    return rows_out, list(tally.values())


def to_markdown(rows: list[dict], suite_name: str, today: str, *, times: int = 1) -> str:
    ok = sum(1 for r in rows if r["ok"])
    stats = attribute(rows)
    retried = sum(1 for r in rows if r["attempts"] > 1)
    total_ms = sum(r["ms"] for r in rows)
    stable_pass = sum(1 for r in rows if r.get("stability") == "稳过")
    stable_fail = sum(1 for r in rows if r.get("stability") == "稳错")
    flaky = sum(1 for r in rows if r.get("stability") == "抖动")
    repeat_note = ""
    if times > 1:
        repeat_note = (
            f"连跑 {times} 遍：**稳过 {stable_pass}，稳错 {stable_fail}，抖动 {flaky}**。"
            f"抖动那几条不是「读错」，是同一句话两次结果不一样，单跑一遍看不出来。\n\n"
        )
    head = (
        f"# 评测结果（{suite_name}）\n\n"
        f"> 由 `python scripts/bench_suite.py --suite {suite_name} --save` 生成，基准日期 {today}。\n"
        f"> 模型侧同一句重跑可能不同，这个表是**一次快照**。\n\n"
        f"**{len(rows)} 条：通过 {ok}，未通过 {len(rows) - ok}"
        f"（{100 * ok / max(1, len(rows)):.0f}%）。"
        f"需重试 {retried} 条，平均 {total_ms // max(1, len(rows))} ms。**\n\n"
        f"{repeat_note}"
        f"归因：{'；'.join(f'{k} {v}' for k, v in stats.most_common())}\n\n"
    )
    table = "| # | 场景 | 原话 | route | 期望 | 结果 |\n|---|---|---|---|---|---|\n"
    for row in rows:
        result = "过" if row["ok"] else "；".join(row["reasons"])[:90]
        if times > 1 and row.get("stability") == "抖动":
            result = f"{row['pass_runs']}/{row['runs']} 遍过"
        table += (f"| {row['id']} | {row['tag']} | {row['text']} | {row['route']} | "
                  f"{row['want']} | {result} |\n")
    return head + table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑评测集")
    parser.add_argument("--suite", default="core", choices=["core", "backend", "all"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--today", default=None, help="基准日期，默认系统日期")
    parser.add_argument("--repeat", type=int, default=1, help="同一套连跑几遍，用来看抖动")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--keep-cache", action="store_true")
    args = parser.parse_args(argv)

    ctx = session.from_iso(args.today)
    if args.repeat > 1:
        rows, tally = repeat(args.suite, args.repeat, limit=args.limit, today=ctx.iso)
        stable_pass = sum(1 for t in tally if t["pass"] == t["runs"])
        stable_fail = sum(1 for t in tally if t["pass"] == 0)
        print()
        print(f"== {len(tally)} 条 × {args.repeat} 遍：稳过 {stable_pass}，"
              f"稳错 {stable_fail}，抖动 {len(tally) - stable_pass - stable_fail}")
        for slot in tally:
            if slot["pass"] not in (0, slot["runs"]):
                print(f"   抖动 {slot['id']:<5} {slot['pass']}/{slot['runs']} 过  {slot['text'][:26]}")
        ok = stable_pass
    else:
        rows = run(args.suite, limit=args.limit, no_cache=not args.keep_cache, today=ctx.iso)
        ok = sum(1 for r in rows if r["ok"])
        stats = attribute(rows)
        print()
        print(f"== {len(rows)} 条：过 {ok}，未过 {len(rows) - ok}")
        for name, count in stats.most_common():
            print(f"   {name}: {count}")
    if args.save:
        target = ROOT / "docs" / f"评测-{args.suite}.md"
        target.write_text(to_markdown(rows, args.suite, ctx.iso, times=args.repeat), encoding="utf-8")
        print(f"已写入 {target}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
