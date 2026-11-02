"""不可行诊断：说清是哪一格拧过头了，而且理由要带数字。

这是小一修相对大一修的**新增能力**：族是冻结的，所以能在跑求解器之前就把一批
「必然做不到」拦下来（静态预检，见 validate）；真要跑求解器才发现排不出时，也能逐条
归因——因为补丁只有几十个、每一条的形状都预写过，可以一条一条试。

纪律（沿用 dsl_core/diagnose 的）：**只报算得出来的**。算不出来就明说"未能定位"，
不拿"可能冲突"糊过去。
"""
from __future__ import annotations

from typing import Any

from . import slots as S
from .patch import Patch
from .validate import _count_planable

# 单条归因的时限：只要能证明「这一条单独就排不出」，不需要最优解
PROBE_SECONDS = 5


def _capacity_hint(patch: Patch, data, options) -> str | None:
    """这一格现在最多能排几个——能算出来就给数字，算不出来不吭声。"""
    if patch.slot in ("count_min", "count_max"):
        ids = S.group_task_ids(data, patch.index.get("组", {})) or []
        able = _count_planable(data, options, ids)
        return f"这一格里最多能排 {able} 个（当前要求 {patch.value}）"
    if patch.slot in ("bucket_min", "bucket_max"):
        ids = S.group_task_ids(data, patch.index.get("组", {})) or []
        able = _count_planable(data, options, ids, patch.index.get("桶"))
        label = patch.index.get("桶", {}).get("value", "全程")
        return f"{label} 内最多能排 {able} 个（当前要求 {patch.value}）"
    if patch.slot == "cap_max":
        dim = patch.index.get("维度", {}).get("value")
        if dim == "作业线":
            lines = min(data.params["最多作业线数"], data.params["人员总上限"])
            return f"当前一共只有 {lines} 条作业线（要求上限 {patch.value}）"
    return None


def _static_reason(patch: Patch, data, options) -> str | None:
    """静态算得出来的矛盾：不用跑求解器就能判的。"""
    if patch.slot == "count_min" and isinstance(patch.value, (int, float)):
        ids = S.group_task_ids(data, patch.index.get("组", {})) or []
        able = _count_planable(data, options, ids)
        if patch.value > able:
            return f"要求至少 {int(patch.value)} 个，但排得进去的只有 {able} 个"
    if patch.slot == "bucket_min" and isinstance(patch.value, (int, float)):
        ids = S.group_task_ids(data, patch.index.get("组", {})) or []
        able = _count_planable(data, options, ids, patch.index.get("桶"))
        if patch.value > able:
            label = patch.index.get("桶", {}).get("value", "该时段")
            return f"{label} 内要求 {int(patch.value)} 个，但排得进去的只有 {able} 个"
    if patch.slot == "count_max" and patch.value == 0:
        ids = S.group_task_ids(data, patch.index.get("组", {})) or []
        if len(ids) == len(data.tasks):
            return "把所有任务都排除了，这次一个都修不成"
    return None


def explain_infeasible(patches: list[Patch], data, options, info: dict[str, Any],
                       request, seconds: int = PROBE_SECONDS) -> str:
    """给出不可行的原因。返回一段说给用户听的话。"""
    status = str(info.get("解状态") or "")
    head = f"这组旋钮排不出方案（求解器判定：{status or '未给出状态'}）"

    # 求解器没证明不可行（超时/未知）就不能说"做不到"——那是两件事。
    if status and "INFEASIBLE" not in status:
        return (f"没能在时限内找到可行方案（{status}）。这不等于一定排不出来，"
                "可以加长时限再试一次，或者先松开最紧的那条要求。")

    lines = [head]

    # 第一层：不用跑求解器就能定罪的
    static = []
    for patch in patches:
        reason = _static_reason(patch, data, options)
        if reason:
            static.append(f"「{patch.utterance or patch.slot}」：{reason}")
    if static:
        lines.append("直接能看出来的矛盾：")
        lines.extend("  * " + item for item in static)
        return "\n".join(lines)

    # 第二层：逐条单跑，找出「单独就排不出」的那条
    from .pipeline import run  # 延迟导入，避免循环

    base_id = (data.meta or {}).get("场景") or "基座01"
    culprits: list[str] = []
    for patch in patches:
        try:
            run([patch], base_id=base_id, seconds=seconds, _diagnose=False)
        except Exception as exc:  # noqa: BLE001 - 只关心能不能跑出方案
            hint = _capacity_hint(patch, data, options)
            culprits.append(f"「{patch.utterance or patch.slot}」单独就要不到方案"
                            + (f"；{hint}" if hint else f"；{str(exc)[:120]}"))
    if culprits:
        lines.append("逐条查了一遍，这几条单独就排不出：")
        lines.extend("  * " + item for item in culprits)
        lines.append("先松开这几条里的任意一条，再排一次。")
        return "\n".join(lines)

    # 第三层：单条都行，是组合冲突——诚实说，不编造是哪两条
    lines.append("每一条单独都能排出来，是它们合在一起冲突。")
    hints = [h for h in (_capacity_hint(p, data, options) for p in patches) if h]
    if hints:
        lines.append("各条的余量：")
        lines.extend("  * " + h for h in hints)
    lines.append("未能精确定位到是哪两条冲突——本次没有做两两组合排查。"
                 "可以把最紧的那条先松开试试。")
    return "\n".join(lines)
