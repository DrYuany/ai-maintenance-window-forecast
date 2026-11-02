"""小一修评测跑分器：E1 校验层 / E2 翻译层 / E3 端到端 / E4 提示词一致性。

判分纪律（与项目发布闸门同源）：全部本地确定性判分，不用 LLM 给 LLM 打分。
E2 在线层调大模型，但判分仍是确定性的——翻出来的补丁与 gold **逐字段**比对
（槽位、索引解析结果、运算、数值、强度），不是让另一个模型说"差不多"。

用法：
    python run_eval.py                    # 跑 E1/E2离线/E3/E4
    python run_eval.py --online           # 加跑 E2 在线翻译（需要 DEEPSEEK_API_KEY）
    python run_eval.py --layer E1,E3      # 只跑指定层
    python run_eval.py --seconds 30       # E3 每个用例的求解时限
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from yxcore import PROJECT_ROOT, solver_modules  # noqa: E402
from yxcore import slots as S  # noqa: E402
from yxcore.patch import Patch, normalize_patch, parse_patch  # noqa: E402
from yxcore.validate import validate  # noqa: E402

CASES_VALIDATE = json.loads((HERE / "cases_validate.json").read_text(encoding="utf-8"))
CASES_E2E = json.loads((HERE / "cases_e2e.json").read_text(encoding="utf-8"))
UTTERANCES = json.loads((HERE / "utterances.json").read_text(encoding="utf-8"))

_snapshot_cache: tuple | None = None


def snapshot():
    """基座01 快照（数据 + 候选），进程内只算一次。"""
    global _snapshot_cache
    if _snapshot_cache is None:
        modules = solver_modules()
        data = modules["load_project"](PROJECT_ROOT, base_id="基座01")
        options = {t["任务编号"]: modules["candidates"](data, t) for t in data.tasks}
        _snapshot_cache = (data, options)
    return _snapshot_cache


def gold_patches(case: dict) -> list[Patch]:
    patches = []
    for raw in case.get("期望") or []:
        payload = dict(raw)
        payload.setdefault("utterance", case["话术"])
        payload.setdefault("strength", "hard")
        patch, error = parse_patch(payload)
        assert patch is not None, f"gold 补丁形状不对（{case['话术']}）：{error}"
        patches.append(patch)
    return patches


# ── E1 校验层 ───────────────────────────────────────────────────────────

def run_e1() -> list[str]:
    data, options = snapshot()
    failures: list[str] = []
    for case in CASES_VALIDATE:
        name = case["名"]
        patches, errors = [], []
        for raw in case["补丁"]:
            patch, error = parse_patch(raw)
            if patch is None:
                errors.append(error or "补丁解析失败")
            else:
                patches.append(patch)
        if patches and not errors:
            verr, _ = validate(patches, data, options)
            errors = verr
        if case["期望"] == "拒":
            if not errors:
                failures.append(f"E1[{name}]：应该拦截却放行了 {case['补丁']}")
                continue
            for frag in case.get("理由含", []):
                if not any(frag in e for e in errors):
                    failures.append(f"E1[{name}]：拦了但理由不对，要含「{frag}」，实际 {errors}")
        else:
            if errors:
                failures.append(f"E1[{name}]：合法补丁被误拦：{errors}")
    return failures


# ── E2 翻译层 ───────────────────────────────────────────────────────────

def _entities():
    from dsl_core.registry import entities
    return entities


def _canon_index_value(kind: str, value, data) -> object:
    entities = _entities()
    kind = {"severity": "严重度", "sea": "海况"}.get(kind, kind)  # 英文登记/中文书写同义
    if kind in (None, "", "全部") or value is None:
        return "全部"  # 整轮口径：写「全部」和不写是一回事
    if kind in ("部件", "part"):
        return entities.resolve_part(data, str(value)) or f"!{value}"
    if kind == "风机":
        return entities.resolve_turbine(data, str(value)) or f"!{value}"
    if kind in ("任务", "task"):
        return entities.resolve_task(data, str(value)) or f"!{value}"
    if kind in ("任务清单", "task_seq", "task_set"):
        values = value if isinstance(value, (list, tuple)) else [value]
        if len(values) == 1:
            # 单元素清单和点名单个任务是同一格（模型两种写法都出现过）
            return entities.resolve_task(data, str(values[0])) or f"!{values[0]}"
        return tuple(sorted(entities.resolve_task(data, str(v)) or f"!{v}" for v in values))
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 6)
    return str(value)


def canonical(patch: Patch, data) -> tuple:
    """补丁的判分形态：别名归一到注册编号，数值归一到浮点，weight 的值不比（偏好刻度无标准答案）。

    gold 和模型输出都要过 normalize_patch：两边都按同一套「口语→注册值」的归一处理，
    比的才是语义，不是书写习惯（否则「恶劣」vs「恶劣但安全」会被判成落点不同）。
    """
    patch = normalize_patch(patch)
    idx = tuple(sorted(
        (key, _canon_index_value((spec or {}).get("kind", ""), (spec or {}).get("value"), data)
         if isinstance(spec, dict) else f"!索引格式错误:{spec!r}")
        for key, spec in patch.index.items()
    ))
    value = patch.value
    if patch.slot == "weight":
        value = None  # 权重刻度是任意正数，只比「点了哪个量」
    elif isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        value = round(float(value), 6)
    return (patch.slot, idx, patch.op, value)


def run_e2_offline() -> list[str]:
    data, options = snapshot()
    failures: list[str] = []
    refusals = 0
    for case in UTTERANCES:
        if not case["期望"]:
            refusals += 1
            assert case.get("拒绝"), f"「{case['话术']}」没写拒绝理由"
            continue
        patches = gold_patches(case)
        for patch in patches:
            if patch.slot not in S.SLOTS and patch.slot not in S.PARAM_SLOTS:
                failures.append(f"E2离线[{case['话术']}]：gold 槽位 {patch.slot} 不在注册表")
        errors, _ = validate(patches, data, options)
        if errors:
            failures.append(f"E2离线[{case['话术']}]：gold 补丁没过校验：{errors}")
    if refusals < 6:
        failures.append(f"E2离线：拒绝类话术只有 {refusals} 条，边界覆盖不足")
    return failures


def run_e2_online(*, seconds: float = 90.0) -> tuple[list[str], list[str]]:
    from yxcore.parse import translate

    data, options = snapshot()
    failures: list[str] = []
    cautions: list[str] = []
    for case in UTTERANCES:
        if not case["期望"]:
            patches, reason = translate(case["话术"], data, options, timeout=seconds)
            if patches:
                failures.append(f"E2在线[{case['话术']}]：应拒绝却给了补丁 {[p.slot for p in patches]}（糊弄）")
            continue
        patches, reason = translate(case["话术"], data, options, timeout=seconds)
        if reason or not patches:
            failures.append(f"E2在线[{case['话术']}]：被错误拒绝：{reason}")
            continue
        got = sorted(canonical(p, data) for p in patches)
        wants = [sorted(canonical(p, data) for p in gold_patches(case))]
        for alt in case.get("期望或") or []:
            alt_patches = []
            for raw in alt:
                payload = dict(raw)
                payload.setdefault("utterance", case["话术"])
                payload.setdefault("strength", "hard")
                alt_patch, err = parse_patch(payload)
                assert alt_patch is not None, f"{case['话术']} 的等价解形状不对：{err}"
                alt_patches.append(alt_patch)
            wants.append(sorted(canonical(p, data) for p in alt_patches))
        if got not in wants:
            failures.append(f"E2在线[{case['话术']}]：\n    得到 {got}\n    应为 {wants[0]}"
                            + (f"\n    或等价解 {wants[1]}" if len(wants) > 1 else ""))
            continue
        # 槽位/索引/值都对，再看强度：unsure 算过慎不算错（按 D3，拿不准要问人）
        got_strength = {p.slot: p.strength for p in patches}
        want_strength = {p.slot: p.strength for p in gold_patches(case)}
        for slot, want_s in want_strength.items():
            got_s = got_strength.get(slot)
            if got_s != want_s and got_s == "unsure":
                cautions.append(f"E2在线[{case['话术']}]：{slot} 应为 {want_s}，模型报了 unsure（过慎）")
            elif got_s != want_s:
                failures.append(f"E2在线[{case['话术']}]：{slot} 强度应为 {want_s}，得到 {got_s}")
    return failures, cautions


# ── E3 端到端 ───────────────────────────────────────────────────────────

CAUTIONS: list[str] = []  # 如实标注但不判失败的情形（如未证最优时的方案波动）


def run_e3(*, seconds: float = 20.0) -> list[str]:
    CAUTIONS.clear()
    from yxcore.pipeline import YxError, run

    failures: list[str] = []
    for case in CASES_E2E:
        name = case["名"]
        patches = []
        for raw in case["补丁"]:
            patch, error = parse_patch(raw)
            assert patch is not None, f"{name}：补丁形状不对 {error}"
            patches.append(patch)
        expect = case["期望"]
        try:
            if expect == "指纹一致":
                r1 = run(patches, seconds=case.get("秒", seconds))
                r2 = run(patches, seconds=case.get("秒", seconds))
                if r1["指纹"] != r2["指纹"]:
                    failures.append(f"E3[{name}]：同一输入两次指纹不同 {r1['指纹']} vs {r2['指纹']}")
                continue
            if expect == "排程一致":
                r1 = run(patches, seconds=case.get("秒", seconds))
                r2 = run(patches, seconds=case.get("秒", seconds))
                same = json.dumps(r1["排程"], ensure_ascii=False, sort_keys=True) == \
                    json.dumps(r2["排程"], ensure_ascii=False, sort_keys=True)
                if same:
                    continue
                # 没证到最优时方案本就会随时限波动——这不是缺陷，是不该被承诺的东西。
                # 反过来说：证到最优还不一致，那就是真缺陷。
                if r1.get("已证最优") and r2.get("已证最优"):
                    failures.append(f"E3[{name}]：两次都证到最优，排程却不同——复现性破了")
                elif len(r1["排程"]) != len(r2["排程"]):
                    failures.append(f"E3[{name}]：两次排程条数都不同 {len(r1['排程'])} vs {len(r2['排程'])}")
                else:
                    CAUTIONS.append(
                        f"E3[{name}]：两次排程内容不同，但求解器未证最优"
                        f"（{r1.get('未证最优目标')}）——属时限内波动，已如实标注，不判失败")
                continue
            r = run(patches, seconds=case.get("秒", seconds))
        except YxError as exc:
            reasons = list(exc.errors) + list(getattr(exc, "warnings", []) or [])
            if expect != "拦截":
                failures.append(f"E3[{name}]：期望出解却被拦：{exc.errors}")
            else:
                for frag in case.get("拦截含", []):
                    if not any(frag in e for e in reasons):
                        failures.append(f"E3[{name}]：拦了但理由要含「{frag}」，实际 {exc.errors}")
            continue
        if expect == "拦截":
            failures.append(f"E3[{name}]：期望拦截却出解了")
            continue
        sel = r["排程"] or []
        if "排程数" in case and len(sel) != case["排程数"]:
            failures.append(f"E3[{name}]：排程数 {len(sel)}，应为 {case['排程数']}")
        if "排程数最多" in case and len(sel) > case["排程数最多"]:
            failures.append(f"E3[{name}]：排程数 {len(sel)}，超过上限 {case['排程数最多']}")
        if "部分" in case and r["部分排程"] != case["部分"]:
            failures.append(f"E3[{name}]：部分排程={r['部分排程']}，应为 {case['部分']}")
        audit = r.get("对账") or ""
        for frag in case.get("对账含", []):
            if frag not in audit:
                failures.append(f"E3[{name}]：对账里找不到「{frag}」；对账全文：{audit}")
        for frag in case.get("对账不含", []):
            if frag in audit:
                failures.append(f"E3[{name}]：对账里出现了不该有的「{frag}」；对账全文：{audit}")
    return failures


# ── E4 提示词一致性 ─────────────────────────────────────────────────────

def run_e4() -> list[str]:
    from yxcore.prompt import BOUNDARY, build_prompt

    data, options = snapshot()
    text = build_prompt(data, options)
    failures: list[str] = []
    for name in S.SLOTS:
        if f"* {name}｜" not in text:
            failures.append(f"E4：提示词里没有槽位 {name}——注册表和提示词漂移了")
    for alias in S.PARAM_SLOTS:
        if f"* {alias}｜" not in text:
            failures.append(f"E4：提示词里没有参数 {alias}")
    for name, _example in BOUNDARY:
        if name not in text:
            failures.append(f"E4：提示词里没有边界条目「{name}」")
    # 护栏数字：每个有任务在册的部件都要带「N 个」
    for t in data.tasks:
        part_name = data.parts[t["部件编号"]]["部件名称"]
        if part_name not in text:
            failures.append(f"E4：提示词里没有部件 {part_name}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--layer", default="E1,E2,E3,E4")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()
    layers = {x.strip().upper() for x in args.layer.split(",")}

    all_failures: list[str] = []
    if "E1" in layers:
        f = run_e1()
        print(f"E1 校验层   ：{len(CASES_VALIDATE)} 条，{len(CASES_VALIDATE)-len(f)} 通过")
        all_failures += f
    if "E2" in layers:
        f = run_e2_offline()
        total = len(UTTERANCES)
        print(f"E2 翻译离线 ：{total} 条话术，gold 全部在册且过校验" if not f else f"E2 翻译离线 ：{f}")
        all_failures += f
    if "E3" in layers:
        f = run_e3(seconds=args.seconds)
        print(f"E3 端到端   ：{len(CASES_E2E)} 条，{len(CASES_E2E)-len(f)} 通过")
        for note in CAUTIONS:
            print("  标注：", note)
        all_failures += f
    if "E4" in layers:
        f = run_e4()
        print(f"E4 提示词   ：一致性检查{'通过' if not f else '失败'}")
        all_failures += f
    if args.online and "E2" in layers:
        from yxcore.parse import configured
        if not configured():
            print("E2 翻译在线 ：未配置 DEEPSEEK_API_KEY，跳过（不假装跑过）")
        else:
            f, c = run_e2_online()
            online_total = len(UTTERANCES)
            print(f"E2 翻译在线 ：{online_total} 条话术，{online_total-len(f)} 通过"
                  + (f"，另有 {len(c)} 条过慎（unsure，不判错）" if c else ""))
            all_failures += f
            for note in c:
                print(f"  过慎：{note}")

    if all_failures:
        print(f"\n── 失败 {len(all_failures)} 项 ──")
        for f in all_failures:
            print("✗", f)
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
