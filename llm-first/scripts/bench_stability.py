"""同一句话跑 N 遍，看它稳不稳。

为什么单独一个脚本：
`bench_compare.py` 是**一次快照**——同一句话重跑，模型可能这次给 dsl、下次给 clarify。
拿单次结果写结论，等于用一次掷硬币的结果说这枚硬币是正面。
所以那张对照表旁边必须有一张分布表，否则读者没法知道"未过 A4"是偶发还是必然。

规则侧不参与抖动统计：它是确定性的，同一句永远同一个结果（这也是它能当基线的原因）。

**必须关掉缓存**：第一版忘了关，同一句第 2、3 遍直接命中缓存，
于是"跑 3 遍"实际只有 1 遍是真跑，分布表全是假的。
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bench_compare  # noqa: E402
import pipeline  # noqa: E402
import rulebench  # noqa: E402


def run(repeat: int) -> list[dict]:
    rows: list[dict] = []
    for tag, utterance in bench_compare.CASES:
        rule = rulebench.understand_rule(utterance)
        routes: Counter[str] = Counter()
        fails: Counter[str] = Counter()
        attempts: list[int] = []
        latency: list[int] = []
        for _ in range(repeat):
            out = pipeline.understand(utterance, allow_repair=True, use_cache=False)
            routes[out.route] += 1
            attempts.append(out.attempts)
            latency.append(out.latency_ms)
            for check in (out.report.checks if out.report else []):
                if check.verdict == "fail":
                    fails[check.code] += 1
        rows.append({
            "tag": tag,
            "utterance": utterance,
            "rule": rule.stage if not rule.parts
                    else f"{rule.stage}（{sum(p.claimed for p in rule.parts)}/{len(rule.parts)}）",
            "routes": routes,
            "fails": fails,
            "repeat": repeat,
            "cold_ms": latency[0] if latency else 0,
            "avg_ms": int(sum(latency) / max(1, len(latency))),
            "repair_rate": sum(1 for a in attempts if a > 1) / max(1, len(attempts)),
        })
    return rows


def to_markdown(rows: list[dict]) -> str:
    repeat = rows[0]["repeat"] if rows else 0
    unstable = [r for r in rows if len(r["routes"]) > 1]
    repaired = [r for r in rows if r["repair_rate"] > 0]
    still_fail = [r for r in rows if r["fails"]]
    lead = (
        f"每条跑 **{repeat}** 遍。三条结论：\n\n"
        f"1. **最终动作会抖**：{len(unstable)}/{len(rows)} 条在不同轮次给出过不同 route"
        f"（{'、'.join(r['tag'] for r in unstable) or '无'}）。\n"
        f"2. **退回重试确实在干活**：{len(repaired)}/{len(rows)} 条触发过至少一次重试"
        f"（{'、'.join(r['tag'] for r in repaired) or '无'}）。\n"
        f"3. **重试之后仍判死的**：{len(still_fail)}/{len(rows)} 条"
        f"（{'、'.join(r['tag'] for r in still_fail) or '无'}）。\n\n"
        "规则侧那一列在每一轮里都一样——拿它当基线，抖动的只能是模型侧。\n\n"
    )
    head = ("| 场景 | 原话 | 规则侧 | 最终 action 分布 | 判死项 | 需重试的轮次 | 平均耗时 |\n"
            "|---|---|---|---|---|---|---|\n")
    body = ""
    for r in rows:
        dist = "　".join(f"{k}×{v}" for k, v in r["routes"].most_common())
        fails = "、".join(f"{k}×{v}" for k, v in r["fails"].most_common()) or "—"
        body += (f"| {r['tag']} | {r['utterance']} | {r['rule']} | {dist} | {fails} | "
                 f"{int(r['repair_rate'] * 100)}% | {r['avg_ms']}ms（首发 {r['cold_ms']}ms） |\n")
    return lead + head + body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--repeat", type=int, default=3, help="每条跑几遍")
    parser.add_argument("--save", action="store_true", help="存成 markdown")
    args = parser.parse_args()

    rows = run(args.repeat)
    md = to_markdown(rows)
    print(md)
    if args.save:
        target = ROOT / "docs" / "稳定性.md"
        target.write_text(
            "# 稳定性：同一句话跑多遍\n\n"
            f"> 由 `python scripts/bench_stability.py -n {args.repeat} --save` 生成\n\n" + md,
            encoding="utf-8",
        )
        print(f"已写入 {target}")


if __name__ == "__main__":
    main()
