"""只看**第一遍**：不开修复轮，看模型第一次交卷到底差在哪。

背景：接上工具循环后，core 套件从「需重试 2 条」涨到「需重试 33 条」。
两件事必须分开：通过率掉了多少，和「首轮合格率」掉了多少。修复轮会把首轮的
错误藏起来（反正第二遍过了），只看最终通过率看不出首轮质量塌了。

跑法：
    cd llm-first && python scripts/diag_first_pass.py A01 A09 B01 G01 J08
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "eval"))
sys.path.insert(0, str(ROOT.parent / "backend"))

import cases as casebook  # noqa: E402
import context as session  # noqa: E402
import pipeline  # noqa: E402

DEFAULT_IDS = ["A01", "A02", "A05", "A09", "B01", "B02", "C08", "G01", "G03", "J08"]


def main(argv: list[str]) -> int:
    wanted = argv[1:] or DEFAULT_IDS
    cases = {case["id"]: case for case in casebook.suite("core")}
    ctx = session.from_iso("2026-10-30")

    first_pass = 0
    for cid in wanted:
        case = cases.get(cid)
        if case is None:
            print(f"{cid}: 用例不存在")
            continue
        # allow_repair=False 就是「只交一次卷」：attempts=1，error 里留着首轮差在哪。
        outcome = pipeline.understand(case["text"], ctx=ctx, use_cache=False, allow_repair=False)
        ok = outcome.route == "dsl" and not outcome.report.failures and not outcome.compile_errors
        first_pass += 1 if ok else 0
        detail = (outcome.error or "")[:150]
        print(
            f"{cid:<4} route={outcome.route:<8} 首轮合格={'是' if ok else '否'} "
            f"{outcome.latency_ms:>5}ms 工具{len(outcome.tool_calls)}次 {detail}",
            flush=True,
        )
    print(f"\n首轮合格 {first_pass}/{len(wanted)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
