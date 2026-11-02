"""端到端编排：补丁 → 求解 → 回显。骨架抄 dsl_core/runner.run，但只有四步。

    load → 编译（四路通道）→ 校验（静态预检）→ solve

大一修还多一步「意图对账」（reconcile），那是第 2 期的事。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import slots as S
from .apply import CompiledRequest, compile_patches, needs_partial
from .patch import Patch, fingerprint
from .validate import validate
from . import PROJECT_ROOT, solver_modules


class YxError(RuntimeError):
    """拦截：校验没过或编译失败。携带全部错误信息，调用方直接说给用户听。"""

    def __init__(self, errors: list[str], warnings: list[str] | None = None) -> None:
        super().__init__("；".join(errors))
        self.errors = errors
        self.warnings = warnings or []


def run(
    patches: list[Patch],
    *,
    base_id: str = "基座01",
    as_of: str | None = None,
    seconds: int = 30,
    output: Path | None = None,
    strategy: str = "成本最低优先",
    max_tasks: int | None = None,
) -> dict[str, Any]:
    """跑一次小一修求解。返回 {排程, 求解, 回显, 指纹, 数据, 警告}。"""
    modules = solver_modules()
    load_project = modules["load_project"]

    base_data = load_project(PROJECT_ROOT, base_id=base_id, as_of=as_of)
    request, errors = compile_patches(patches, base_data, base_data)
    if errors:
        raise YxError(errors)

    try:
        data = load_project(
            PROJECT_ROOT, base_id=base_id, as_of=as_of,
            overrides=request.overrides or None,
            data_patches=request.data_patches or None,
            timeline_patches=request.timeline_patches or None,
        )
    except ValueError as exc:
        # 内核抛的本来就是说给用户听的（"这次改了不会影响结果"），
        # 但它是裸 ValueError，走出去会被当成系统内部错误。
        raise YxError([str(exc)]) from exc
    if max_tasks:
        data.tasks = data.tasks[:max_tasks]

    options = {t["任务编号"]: modules["candidates"](data, t) for t in data.tasks}
    errors, warnings = validate(patches, data, options)
    registry = S.audit_registry()
    if registry:
        # 注册表带病就不许出方案——「加了族却没对账」这类窟窿必须在这里炸出来
        raise YxError(["槽位注册表自检未通过：" + "；".join(registry)])
    if errors:
        raise YxError(errors, warnings)

    request.partial = request.partial or needs_partial(patches, data, options)

    if request.strategy:
        strategy = request.strategy
    fp = fingerprint(patches, {"基座": base_id, "策略": strategy,
                               "部分排程": request.partial, "时限": seconds})
    output = output or (PROJECT_ROOT / "小一修" / "runs" / fp[:12])
    output.mkdir(parents=True, exist_ok=True)

    hint = modules["heuristic"](data, options, "严重性优先")
    kwargs = request.as_solve_kwargs()
    selected, info = modules["solve"](
        data, options, output, seconds=seconds, strategy=strategy, hint=hint, **kwargs
    )
    recommended = None
    if selected is not None:
        try:
            recommended = modules["account"](data, selected)
        except ValueError as exc:
            # 独立复核没过：这份排程自己站不住，不能带着"复核通过"交出去
            raise YxError([f"求解结果未通过独立复核：{exc}"], warnings) from exc
    return {
        "排程": selected,
        "推荐": recommended,
        "求解": info,
        "回显": request.echo,
        "待确认": request.uncertain,
        "指纹": fp,
        "数据": data.meta,
        "警告": warnings,
        "部分排程": request.partial,
        "请求": request,
    }


def baseline(base_id: str = "基座01", seconds: int = 30, **kwargs) -> dict[str, Any]:
    """不带任何补丁的基线求解——每次对比都要有它，否则说不清「改了这个数」带来什么。"""
    return run([], base_id=base_id, seconds=seconds, **kwargs)


def describe(result: dict[str, Any]) -> str:
    """把结果说成人话：排了几个、花多少、哪些旋钮生效了。"""
    lines: list[str] = []
    selected = result["排程"]
    lines.append(f"排程 {'成功' if selected else '未产出'}："
                 + (f"{len(selected)} 个任务" if selected else "无"))
    if result["回显"]:
        lines.append("生效的旋钮：" + "；".join(result["回显"]))
    if result["待确认"]:
        lines.append("需要你确认：" + "；".join(result["待确认"]))
    if result["警告"]:
        lines.append("提醒：" + "；".join(result["警告"]))
    rec = result.get("推荐")
    if rec:
        lines.append(f"现金支出 {rec.get('现金支出')} 元，总成本 {rec.get('总成本')} 元，"
                     f"租船 {rec.get('船舶艘天')} 艘天")
    lines.append(f"指纹 {result['指纹'][:12]}")
    return "\n".join(lines)
