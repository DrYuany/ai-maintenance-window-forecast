"""对照跑批：同一批话，两边各读一遍，打印一张表。

用法：
    python scripts/bench_compare.py                 # 默认 10 条
    python scripts/bench_compare.py --save          # 另存 markdown

为什么要有这个脚本：页面是给人看的，脚本是给回归用的。
改了提示词之后，先跑这个看两边各过几条，再去页面上确认观感——
顺序反了，就会拿"看起来不错"当验证。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pipeline  # noqa: E402
import rulebench  # noqa: E402

CASES: list[tuple[str, str]] = [
    ("单句改参数", "把船租降到8万"),
    ("复合句（预算+约束）", "任务001和任务002不要同一天做，钱不超过300万"),
    ("复合句（两个参数）", "人工单价涨到200，任务003最晚11月9日修完"),
    ("改一半问一半", "把船租降到8万，为什么这么贵"),
    ("比较问句", "经济优先和风险优先差多少"),
    ("能力缺口（全部）", "成本压到最低，但高风险的必须全修"),
    ("能力缺口（范围）", "只排风机003的任务"),
    ("口语预算", "钱不超过300万"),
    ("口语翻倍", "船租翻倍了"),
    ("相对时间", "明天风大，先别出海"),
]


def run(allow_repair: bool = True) -> list[dict]:
    rows: list[dict] = []
    for tag, utterance in CASES:
        rule = rulebench.understand_rule(utterance)
        started = time.monotonic()
        llm = pipeline.understand(utterance, allow_repair=allow_repair)
        hit = sum(1 for p in rule.parts if p.claimed)
        rows.append({
            "tag": tag,
            "utterance": utterance,
            "rule_stage": rule.stage,
            "rule_claimed": hit,
            "rule_parts": len(rule.parts),
            "rule_items": sum(len(v) for v in rule.dsl.values()),
            "rule_dropped": rule.numbers["missing"],
            "rule_fallback_dropped": (rule.fallback or {}).get("numbers", {}).get("missing", []),
            "rule_errors": rule.compile_errors,
            "llm_route": llm.route,
            "llm_items": sum(len(v) for v in (llm.product.get("dsl") or {}).values()),
            "llm_dropped": llm.numbers.get("missing", []),
            "llm_errors": llm.compile_errors,
            "llm_fails": [c.code for c in llm.report.checks if c.verdict == "fail"] if llm.report else [],
            "llm_ms": llm.latency_ms,
            "attempts": llm.attempts,
            "wall": int((time.monotonic() - started) * 1000),
        })
    return rows


def _rule_cell(r: dict) -> str:
    if r["rule_parts"]:
        return f"{r['rule_stage']}（{r['rule_claimed']}/{r['rule_parts']} 段 · {r['rule_items']} 项）"
    return f"{r['rule_stage']}（{r['rule_items']} 项）"


def to_markdown(rows: list[dict]) -> str:
    pure_dsl = [r for r in rows if r["rule_stage"] in {"batch", "compound", "rules", "legacy"}]
    pure_gate = [r for r in rows if r["rule_stage"] in {"not_change_guard", "gate"}]
    partial = [r for r in rows if r["rule_stage"] == "needs_llm" and r["rule_claimed"]]
    rest = len(rows) - len(pure_dsl) - len(pure_gate)
    lead = (
        f"**{len(rows)} 条里，规则层能独立交出 DSL 的有 {len(pure_dsl)} 条"
        f"（{'、'.join(r['tag'] for r in pure_dsl) or '无'}）；"
        f"另有 {len(pure_gate)} 条被确定性判死、不需要模型"
        f"（{'、'.join(r['tag'] for r in pure_gate) or '无'}）。**\n\n"
        f"剩下 {rest} 条交给大模型，其中 {len(partial)} 条只是**某一段**认下来了"
        f"（{'、'.join(r['tag'] for r in partial) or '无'}），整句照样得模型读——"
        "`parse()` 要求每一段都命中才合并。\n\n"
    )
    head = ("| 场景 | 原话 | 规则层（认下/拆出） | 规则侧丢数 | LLM侧 | 项数 | 未过 | 耗时 |\n"
            "|---|---|---|---|---|---|---|---|\n")
    body = ""
    for r in rows:
        # 规则层没交出 DSL 时，原文数字全都会被算成"丢"——那是噪声，
        # 不是"它读漏了"。这种情况只报兜底读漏了什么。
        if not r["rule_items"]:
            fb = "、".join(str(int(x)) for x in r["rule_fallback_dropped"])
            dropped = f"—（兜底丢 {fb}）" if fb else "—"
        else:
            dropped = "、".join(str(int(x)) for x in r["rule_dropped"]) or "—"
        fails = "、".join(r["llm_fails"]) or "全过"
        body += (f"| {r['tag']} | {r['utterance']} | {_rule_cell(r)} | {dropped} | "
                 f"{r['llm_route']} | {r['llm_items']} | {fails} | {r['llm_ms']}ms |\n")
    return lead + head + body


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", action="store_true", help="存成 markdown")
    parser.add_argument("--no-repair", action="store_true", help="关掉退回重试")
    args = parser.parse_args()

    rows = run(allow_repair=not args.no_repair)
    md = to_markdown(rows)
    print(md)
    if args.save:
        target = ROOT / "docs" / "对照结果.md"
        head = (
            "# 对照跑批结果\n\n"
            "> 由 `python scripts/bench_compare.py --save` 生成\n>\n"
            "> **这是一次快照。** 模型侧同一句话重跑可能给出不同结果，\n"
            "> 单次胜负不能当结论——要看 `稳定性.md`（每条跑 5 遍的分布）。\n"
            "> 规则侧那一列是确定性的，重跑不变。\n\n"
        )
        target.write_text(head + md, encoding="utf-8")
        print(f"已写入 {target}")


if __name__ == "__main__":
    main()
