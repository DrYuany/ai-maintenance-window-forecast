"""规则版基线与 LLM 版的同集对照（只读，不调模型）。

为什么要这个脚本：
    简历/复盘里说"规则版不行、LLM 版行"，得给同一套判据下的两个数。
    规则版是确定性的，跑一遍就是这个数，重跑不会变——所以它是天然的基线。
    LLM 版的数不在这里跑（要调接口、会抖），用评测记录里同版本的结果。

判据口径与 `bench_suite.grade` 一致（同样的 route 期望、同样的"缺项"、
同样的 forbid 只在 route=dsl 时检查、同样的丢数判定），只是把它套在
`RuleOutcome` 上——不另写一套判据，否则结论只对本脚本成立。

用法：
    python scripts/diag_rule_baseline.py
    python scripts/diag_rule_baseline.py --verbose
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "src"))

from eval.cases import SUITE  # noqa: E402
from rulebench import understand_rule  # noqa: E402

_ABSTAIN = {"needs_llm", "unparsed", ""}


def _sets(dsl: dict) -> dict[str, set[str]]:
    return {
        "params": {str(i.get("field")) for i in dsl.get("parameters") or []},
        "constraints": {str(i.get("type")) for i in dsl.get("constraints") or []},
        "objectives": {str(i.get("type")) for i in dsl.get("objectives") or []},
        "data": {str(i.get("kind")) for i in dsl.get("data") or []},
    }


def _score(case: dict, outcome) -> dict:
    want = {case["route"]} if "route" in case else set(case.get("route_in") or {"dsl"})
    route = outcome.route
    route_ok = route in want

    present = _sets(outcome.dsl or {})
    absent = [
        f"{key}:{name}"
        for key in ("params", "constraints", "objectives", "data")
        for name in case.get(key) or []
        if name not in present[key]
    ]

    if route == "dsl":
        absent += [
            f"不该改参数:{n}" for n in case.get("forbid_params") or [] if n in present["params"]
        ]
        absent += [
            f"不该写约束:{n}"
            for n in case.get("forbid_constraints") or []
            if n in present["constraints"]
        ]

    numbers = outcome.numbers or {}
    dropped = list(numbers.get("missing") or []) if numbers.get("applicable", True) else []

    reasons = []
    if not route_ok:
        reasons.append(f"route={route}（要 {sorted(want)}）")
    if absent:
        reasons.append("缺/越界 " + "、".join(absent))
    if dropped:
        reasons.append(f"丢数 {[int(x) for x in dropped]}")

    return {
        "id": case["id"],
        "tag": case.get("tag", ""),
        "text": case["text"],
        "route": route,
        "want": sorted(want),
        "pass": not reasons,
        "reasons": reasons,
        "abstain": route in _ABSTAIN,
        "expected_change": "dsl" in want,
        "predicted_change": route == "dsl",
        "dropped": len(dropped),
        "invented": int(numbers.get("invented") or 0),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    rows, crashed = [], []
    for case in SUITE:
        try:
            rows.append(_score(case, understand_rule(case["text"])))
        except Exception as exc:  # 规则层直接崩，也算它没交出来
            crashed.append((case["id"], case["text"], f"{type(exc).__name__}: {exc}"))
            rows.append(
                {
                    "id": case["id"], "tag": case.get("tag", ""), "text": case["text"],
                    "route": "<crash>", "want": [], "pass": False,
                    "reasons": ["规则层抛异常"], "abstain": True,
                    "expected_change": "dsl" in ({case["route"]} if "route" in case else set(case.get("route_in") or {"dsl"})),
                    "predicted_change": False, "dropped": 0, "invented": 0,
                }
            )
    total = len(rows)
    passed = sum(r["pass"] for r in rows)

    tp = sum(1 for r in rows if r["expected_change"] and r["predicted_change"])
    fp = sum(1 for r in rows if not r["expected_change"] and r["predicted_change"])
    fn = sum(1 for r in rows if r["expected_change"] and not r["predicted_change"])
    tn = sum(1 for r in rows if not r["expected_change"] and not r["predicted_change"])
    abstain = sum(1 for r in rows if r["abstain"])

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0

    print(f"样本 {total} 条 · 规则版（确定性，不调模型）")
    print(f"  经同一判据全过        {passed}/{total}（{passed / total:.0%}）")
    print(f"  自己给不出结论（交给模型） {abstain} 条")
    print(f"  路由矩阵：TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"  该改的识别率（recall） {recall:.0%}　误改率（FP/{tp + fp}） {fp}/{tp + fp}")
    print(f"  精确率（precision）   {precision:.0%}")
    print(f"  丢数合计 {sum(r['dropped'] for r in rows)} 处，编数合计 {sum(r['invented'] for r in rows)} 处")
    if crashed:
        print(f"  规则层直接抛异常 {len(crashed)} 条：" + "、".join(c[0] for c in crashed))
        for cid, text, err in crashed:
            print(f"    {cid} {text} → {err}")

    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        by_tag.setdefault(r["tag"].split("·")[0], []).append(r)
    print("\n  分组通过率：")
    for tag, group in by_tag.items():
        ok = sum(g["pass"] for g in group)
        print(f"    {tag:<6} {ok}/{len(group)}")

    if args.verbose:
        print("\n  未通过明细：")
        for r in rows:
            if not r["pass"]:
                print(f"    {r['id']} {r['text'][:22]:<24} {'; '.join(r['reasons'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
