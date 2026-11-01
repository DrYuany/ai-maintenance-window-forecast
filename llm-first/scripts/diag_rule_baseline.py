"""规则版基线与 LLM 版的同集对照（只读，不调模型）。

为什么要这个脚本：
    简历/复盘里说"规则版不行、LLM 版行"，得给同一套判据下的两个数。
    规则版是确定性的，跑一遍就是这个数，重跑不会变——所以它是天然的基线。
    LLM 版的数不在这里跑（要调接口、会抖），用评测记录里同版本的结果。

两套集的口径不同，别混着说：
    core（68 条手写）：有 route + params/constraints/objectives/data 期望，能判"取数完整性"。
    backend（221 条旧集）：只有 route 标签，判不了逐项；而且标签是按规则层当年的能力
    划的——对规则版是主场，对 LLM 版是客场。压力集只看分布，不逐条判对错。

判据口径与 `bench_suite.grade` 一致（同样的 route 期望、同样的"缺项"、
同样的 forbid 只在 route=dsl 时检查、同样的丢数判定），只是把它套在
`RuleOutcome` 上——不另写一套判据，否则结论只对本脚本成立。

用法：
    python scripts/diag_rule_baseline.py
    python scripts/diag_rule_baseline.py --suite backend
    python scripts/diag_rule_baseline.py --suite all --verbose
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "src"))

from eval.cases import suite as load_suite  # noqa: E402
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
        # 只把"必须改"和"必须不改"算进矩阵；route_in 里含 dsl 也含别的，
        # 判什么都算对，那种题对指标没有信息量，单独记成 ambiguous。
        "must_change": want == {"dsl"},
        "must_not_change": "dsl" not in want,
        "ambiguous": "dsl" in want and len(want) > 1,
        "predicted_change": route == "dsl",
        "dropped": len(dropped),
        "invented": int(numbers.get("invented") or 0),
    }


def _report(name: str, cases: list[dict], verbose: bool) -> dict:
    rows, crashed = [], []
    for case in cases:
        try:
            rows.append(_score(case, understand_rule(case["text"])))
        except Exception as exc:  # 规则层直接崩，也算它没交出来
            want = {case["route"]} if "route" in case else set(case.get("route_in") or {"dsl"})
            crashed.append((case["id"], case["text"], f"{type(exc).__name__}: {exc}"))
            rows.append(
                {
                    "id": case["id"], "tag": case.get("tag", ""), "text": case["text"],
                    "route": "<crash>", "want": sorted(want), "pass": False,
                    "reasons": ["规则层抛异常"], "abstain": True,
                    "must_change": want == {"dsl"}, "must_not_change": "dsl" not in want,
                    "ambiguous": "dsl" in want and len(want) > 1,
                    "predicted_change": False, "dropped": 0, "invented": 0,
                }
            )

    total = len(rows)
    passed = sum(r["pass"] for r in rows)
    tp = sum(1 for r in rows if r["must_change"] and r["predicted_change"])
    fp = sum(1 for r in rows if r["must_not_change"] and r["predicted_change"])
    fn = sum(1 for r in rows if r["must_change"] and not r["predicted_change"])
    tn = sum(1 for r in rows if r["must_not_change"] and not r["predicted_change"])
    ambig = sum(1 for r in rows if r["ambiguous"])
    abstain = sum(1 for r in rows if r["abstain"])
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0

    print(f"\n=== {name} · {total} 条 · 规则版（确定性，不调模型）===")
    print(f"  经同一判据全过          {passed}/{total}（{passed / total:.0%}）")
    print(f"  弃权（交给模型）         {abstain} 条")
    print(f"  路由矩阵                TP={tp} FP={fp} FN={fn} TN={tn}（另有 {ambig} 条多选容忍，不计）")
    print(f"  该改的召回              {recall:.0%}（{tp}/{tp + fn}）")
    print(f"  精确（敢改就对）         {precision:.0%}（{tp}/{tp + fp}）")
    print(f"  丢数 {sum(r['dropped'] for r in rows)} 处 · 编数 {sum(r['invented'] for r in rows)} 处")
    if crashed:
        print(f"  规则层抛异常 {len(crashed)} 条：" + "、".join(c[0] for c in crashed))

    by_tag: dict[str, list[dict]] = {}
    for r in rows:
        by_tag.setdefault(r["tag"].split("·")[0], []).append(r)
    print("  分组通过率：" + "　".join(
        f"{tag} {sum(g['pass'] for g in group)}/{len(group)}" for tag, group in by_tag.items()
    ))

    if verbose:
        print("  未通过明细：")
        for r in rows:
            if not r["pass"]:
                print(f"    {r['id']} {r['text'][:22]:<24} {'; '.join(r['reasons'])}")

    return {"name": name, "total": total, "passed": passed, "tp": tp, "fp": fp,
            "fn": fn, "tn": tn, "recall": recall, "precision": precision,
            "dropped": sum(r["dropped"] for r in rows), "abstain": abstain,
            "crashed": len(crashed)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="core", choices=["core", "backend", "all"])
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    names = ["core", "backend"] if args.suite == "all" else [args.suite]
    for name in names:
        _report(name, load_suite(name), args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
