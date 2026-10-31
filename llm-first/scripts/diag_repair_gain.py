"""外层循环到底救回了什么——同一条需求跑两遍，只改 allow_repair。

对照组：allow_repair=False → rounds=1，内层工具循环照跑，只是没有第二遍。
实验组：allow_repair=True  → rounds=2，判据不过就带原因退回重写。

**这是最干净的对照**：同一个函数、同一个模型、同一天、同一套判据，
唯一的自变量是"给不给它第二次机会"。

判分复用 bench_suite.grade，不另写一套——自己再写一套判据，
结论就只对我的判据成立。

输出两样：
  1. 汇总：首轮过 / 靠第二遍救回 / 两遍都没过；
  2. 明细 JSON（含产物摘要），用来挑例子看"它到底改了什么"。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT / "scripts"))

import context as session  # noqa: E402
import pipeline  # noqa: E402
import cases as casebook  # noqa: E402
from bench_suite import grade  # noqa: E402


def _digest(outcome) -> dict:
    """产物里最能说明"它理解成什么了"的几样东西。"""
    product = outcome.product or {}
    dsl = product.get("dsl") or {}
    return {
        "route": outcome.route,
        "attempts": outcome.attempts,
        "ms": outcome.latency_ms,
        "error": (outcome.error or "")[:180],
        "params": [f"{i.get('field')}={i.get('value')}" for i in dsl.get("parameters") or []],
        "constraints": [str(i.get("type")) for i in dsl.get("constraints") or []],
        "objectives": [str(i.get("type")) for i in dsl.get("objectives") or []],
        "data": [str(i.get("kind")) for i in dsl.get("data") or []],
        "claims": product.get("claims") or [],
        "fills": [str(i.get("text") or i) for i in product.get("fills") or []][:6],
        "unassigned": [str(i) for i in product.get("unassigned") or []][:4],
        "fails": [c.code for c in (outcome.report.checks if outcome.report else []) if c.verdict == "fail"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="core")
    ap.add_argument("--today", default="2026-10-30")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-route", default="",
                    help="只跑 source_route 等于它的（backend 套件里 chitchat 不编译，跑它没意义）")
    ap.add_argument("--out", default="/tmp/repair_gain.json")
    args = ap.parse_args()

    ctx = session.from_iso(args.today)
    cases = casebook.suite(args.suite)
    if args.only_route:
        cases = [c for c in cases if c.get("source_route") == args.only_route]
    if args.limit:
        cases = cases[: args.limit]

    tally: Counter = Counter()
    rows: list[dict] = []

    for index, case in enumerate(cases, 1):
        text = case["text"]
        # 两次都关缓存：否则第二遍会直接拿第一遍的缓存，等于没跑。
        without = pipeline.understand(text, ctx=ctx, use_cache=False, allow_repair=False)
        row_a = grade(case, without)
        withr = pipeline.understand(text, ctx=ctx, use_cache=False, allow_repair=True)
        row_b = grade(case, withr)

        if row_a["ok"]:
            verdict = "首轮就过"
        elif row_b["ok"]:
            verdict = "第二遍救回"
        else:
            verdict = "两遍都没过"
        tally[verdict] += 1

        rows.append({
            "id": case["id"], "tag": case["tag"], "text": text,
            "note": case.get("note", ""), "verdict": verdict,
            "first": {**row_a, "digest": _digest(without)},
            "second": {**row_b, "digest": _digest(withr)},
        })
        mark = {"首轮就过": "·", "第二遍救回": "★", "两遍都没过": "✗"}[verdict]
        print(f"{index:3d}/{len(cases)} {mark} {case['id']:<4} {verdict:<8} {text[:30]}", flush=True)

    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== 汇总 ===")
    for key in ("首轮就过", "第二遍救回", "两遍都没过"):
        print(f"  {key}: {tally[key]}")
    print(f"  明细: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
