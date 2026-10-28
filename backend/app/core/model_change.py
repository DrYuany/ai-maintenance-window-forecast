from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import re
from dataclasses import dataclass, field
from datetime import datetime

from app.core.planning_intent import PlanningIntentCompiler, PlanningRequest

_PATCH_IDENTITY = PlanningIntentCompiler._patch_identity

# 一句话里的每个“动作单位”：按 X 算一遍 / 用 X 排一版
_SCENARIO_RE = re.compile(
    r"(?:按|用|以|按照|采用)(.+?)"
    r"(?:算一遍|排一遍|算一版|排一版|生成一版|来一版|算一次|排一次|排个|算个|排|算)"
)
_COMPARE_RE = re.compile(r"(?:然后|最后|接着|并且|再|顺便)?(?:帮我)?(?:对比|比较|比一比|比一下)")
_DEFAULT_TOKENS = ("默认", "原样", "不变", "当前配置", "原来的", "照旧")


PARAMETER_LABELS = {
    "船租（元每艘每天）": "船租",
    "人工单价（元每人时）": "人工单价",
    "最多作业线数": "最多作业线数",
    "人员总上限": "人员总上限",
    "每天最多维修小时数": "每天最多维修小时数",
    "风机间转移小时数": "风机间转移时间",
    "每天维修开始时间": "每天开工时刻",
    "每天维修结束时间": "每天收工时刻",
}

# 预览里每个参数该带什么单位。key 用展示名，因为 _append_change 已把原名换成展示名。
PARAMETER_UNITS = {
    "船租": lambda v: f"{float(v) / 10000:.2f} 万元",
    "人工单价": lambda v: f"{float(v):.4g} 元/人时",
    "人员总上限": lambda v: f"{int(v)} 人",
    "风机间转移时间": lambda v: f"{float(v):.4g} 小时",
    "每天最多维修小时数": lambda v: f"{float(v):.4g} 小时",
    "最多作业线数": lambda v: f"{int(v)} 条",
    "每天开工时刻": lambda v: f"{v} 开工",
    "每天收工时刻": lambda v: f"{v} 收工",
}

CONFIRM_TOKENS = (
    "确认生成沙盘",
    "确认生成方案",
    "确认开始",
    "开始求解",
    "发起求解",
    "执行变更",
    "跑吧",
)
CANCEL_TOKENS = ("取消变更", "取消", "先不改", "算了", "停止变更")

SOLVE_EXECUTION_COMMANDS = {
    "发起求解",
    "你发起求解",
    "开始求解",
    "现在开始求解",
    "直接求解",
    "按默认参数求解",
    "用默认参数求解",
    "默认生成",
    "那你就默认生成",
}


@dataclass
class ModelChangePlan:
    request: PlanningRequest
    operations: list[dict[str, Any]]
    preview: str
    source_prompt: str
    base_version_id: str | None
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.errors)


def is_confirmation(message: str) -> bool:
    text = "".join(message.split())
    return any(token in text for token in CONFIRM_TOKENS)


def is_solve_execution_command(message: str) -> bool:
    text = "".join(message.split()).strip("，。！？!?；;")
    return text in SOLVE_EXECUTION_COMMANDS


def is_cancellation(message: str) -> bool:
    text = "".join(message.split())
    return any(token in text for token in CANCEL_TOKENS)


def build_change_plan(
    message: str,
    current: PlanningRequest,
    base_version_id: str | None,
    compiler: PlanningIntentCompiler | None = None,
) -> ModelChangePlan:
    compiler = compiler or PlanningIntentCompiler()
    compiled = compiler.compile_detailed(message, current=current)
    updated = compiled.request
    operations = _diff_requests(current, updated)
    if compiled.errors:
        operations = []
    elif not operations and any(
        token in "".join(message.split())
        for token in ("重新排程", "重新求解", "重新安排", "再算一版")
    ):
        operations.append(
            {
                "kind": "rerun",
                "key": "求解配置",
                "before": "当前方案",
                "after": "沿用当前配置重新求解",
            }
        )
    if compiled.errors:
        preview = "\n".join(
            ["这次没有开始求解，因为有一项设定无法成立：", *compiled.errors]
        )
    elif not operations:
        preview = (
            "我还没有识别出需要修改的模型内容。\n"
            "可以明确说参数（船租、日工时、作业线）、约束（预算、必修任务）、"
            "目标策略或权重，例如“船租上涨 20%，其他不变”。"
        )
        if compiled.notes:
            preview = preview + "\n" + "\n".join(compiled.notes)
    else:
        preview = _format_preview(operations, base_version_id)
        if compiled.notes:
            preview = preview + "\n" + "\n".join(compiled.notes)
    return ModelChangePlan(
        request=updated,
        operations=operations,
        preview=preview,
        source_prompt=message,
        base_version_id=base_version_id,
        notes=compiled.notes,
        errors=compiled.errors,
    )


@dataclass
class ScenarioPlan:
    """一次请求的其中一个场景。每个场景都是相对同一基准的独立变体，不做叠加。"""

    label: str
    request: PlanningRequest
    operations: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    source_prompt: str = ""


@dataclass
class ScenarioBatch:
    scenarios: list[ScenarioPlan]
    compare: bool
    source_prompt: str
    base_version_id: str | None

    @property
    def blocked(self) -> bool:
        return any(item.errors for item in self.scenarios)

    @property
    def preview(self) -> str:
        return _format_batch_preview(self)


def split_scenarios(message: str) -> list[str]:
    """把「先按 A 算一遍，再按 B 算一遍」拆成 [A, B]。"""
    return [match.group(1).strip("，,。.、 ") for match in _SCENARIO_RE.finditer(message)]


def wants_compare(message: str) -> bool:
    return bool(_COMPARE_RE.search(message))


def is_scenario_batch(message: str) -> bool:
    return len(split_scenarios(message)) >= 2


def build_scenario_batch(
    message: str,
    current: PlanningRequest,
    base_version_id: str | None,
    compiler: PlanningIntentCompiler | None = None,
) -> ScenarioBatch:
    compiler = compiler or PlanningIntentCompiler()
    segments = split_scenarios(message)
    plans: list[ScenarioPlan] = []
    for segment in segments:
        if any(token in segment for token in _DEFAULT_TOKENS):
            # “按默认方法算一遍”没有改动项，但仍然是一次真实的求解。
            plans.append(
                ScenarioPlan(
                    label="默认配置",
                    request=current,
                    operations=[{
                        "kind": "rerun",
                        "key": "求解配置",
                        "before": "当前配置",
                        "after": "沿用当前配置重新求解",
                    }],
                    source_prompt=segment,
                )
            )
            continue
        compiled = compiler.compile_detailed(segment, current=current)
        operations = [] if compiled.errors else _diff_requests(current, compiled.request)
        errors = list(compiled.errors)
        if not errors and not operations:
            # 场景里点名了完整配置、只是恰好和当前一致时，它仍然是一次独立求解。
            declared = compiler.declared_strategy(segment)
            if declared:
                operations = [{
                    "kind": "rerun",
                    "key": "求解配置",
                    "before": "当前配置",
                    "after": f"按「{declared}」重新求解",
                }]
            else:
                errors.append(
                    f"没有认出“{segment}”要改什么。可以说参数（船租、日工时、作业线）、"
                    "约束（预算、必修任务、到货、期限）或目标策略。"
                )
        plans.append(
            ScenarioPlan(
                label=segment,
                request=compiled.request,
                operations=operations,
                notes=list(compiled.notes),
                errors=errors,
                source_prompt=segment,
            )
        )
    return ScenarioBatch(
        scenarios=plans,
        compare=wants_compare(message),
        source_prompt=message,
        base_version_id=base_version_id,
    )


def _format_batch_preview(batch: ScenarioBatch) -> str:
    if batch.blocked:
        lines = ["这次没有开始求解，因为其中有的场景无法成立："]
        for index, item in enumerate(batch.scenarios, start=1):
            for error in item.errors:
                lines.append(f"场景 {index}「{item.label}」：{error}")
        return "\n".join(lines)
    lines = [
        f"这句话我拆成了 {len(batch.scenarios)} 个场景，确认后会依次生成 "
        f"{len(batch.scenarios)} 个独立方案，不覆盖现有方案。",
    ]
    for index, item in enumerate(batch.scenarios, start=1):
        detail = "；".join(
            f"{operation['key']}：{_format_value(operation)}" for operation in item.operations
        )
        lines.append(f"场景 {index}「{item.label}」：{detail}")
        for note in item.notes:
            lines.append(f"　　{note}")
    if batch.compare:
        lines.append("全部算完后，我会把这几版放在一起对比。")
    lines.extend(
        [
            "回复“确认生成沙盘”依次执行；回复“取消”放弃本次变更。",
        ]
    )
    return "\n".join(lines)


def _append_change(
    operations: list[dict[str, Any]],
    *,
    kind: str,
    key: str,
    before: Any,
    after: Any,
) -> None:
    if before == after:
        return
    operations.append(
        {
            "kind": kind,
            "key": key,
            "before": before,
            "after": after,
        }
    )


def _diff_requests(current: PlanningRequest, updated: PlanningRequest) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    _append_change(operations, kind="strategy", key="优化策略", before=current.strategy, after=updated.strategy)
    _append_change(operations, kind="mode", key="目标模式", before=current.mode, after=updated.mode)

    for key in sorted(set(current.overrides) | set(updated.overrides)):
        _append_change(
            operations,
            kind="parameter",
            key=PARAMETER_LABELS.get(key, key),
            before=current.overrides.get(key),
            after=updated.overrides.get(key),
        )
    for key in sorted(set(current.relative_overrides) | set(updated.relative_overrides)):
        _append_change(
            operations,
            kind="relative_parameter",
            key=PARAMETER_LABELS.get(key, key),
            before=current.relative_overrides.get(key),
            after=updated.relative_overrides.get(key),
        )
    for key in sorted(set(current.limits) | set(updated.limits)):
        _append_change(
            operations,
            kind="constraint",
            key=key,
            before=current.limits.get(key),
            after=updated.limits.get(key),
        )
    current_patches = {_PATCH_IDENTITY(p): p for p in current.data_patches}
    updated_patches = {_PATCH_IDENTITY(p): p for p in updated.data_patches}
    for key in sorted(updated_patches, key=lambda item: str(item)):
        after = updated_patches[key]
        before = current_patches.get(key)
        if before == after:
            continue
        operations.append(
            {
                "kind": "data_patch",
                "key": _patch_label(after),
                "before": before,
                "after": after,
            }
        )
    current_weights = current.weights or {}
    updated_weights = updated.weights or {}
    if updated_weights:
        for key in sorted(set(current_weights) | set(updated_weights)):
            after = updated_weights.get(key)
            before = current_weights.get(key)
            if not after and not before:
                continue
            _append_change(
                operations,
                kind="weight",
                key=key,
                before=before,
                after=after,
            )
    return operations


def _patch_label(patch: dict[str, Any]) -> str:
    kind = patch.get("kind")
    if kind == "part_arrival":
        return f"备件到货·{patch.get('partName', '')}"
    if kind == "part_hours":
        return f"维修工时·{patch.get('partName', '')}"
    if kind == "task_deadline":
        return f"完工期限·{patch.get('scope', '')}"
    return str(kind)


def _patch_text(patch: dict[str, Any]) -> str:
    kind = patch.get("kind")
    if kind == "part_arrival":
        at = datetime.fromisoformat(patch["at"])
        return f"{at.strftime('%Y-%m-%d')} 到货 {patch['quantity']} 套"
    if kind == "part_hours":
        return f"×{float(patch['factor']):.4g}（基准工时 × 倍率）"
    if kind == "task_deadline":
        at = datetime.fromisoformat(patch["at"])
        return f"{at.strftime('%Y-%m-%d')} 前完成"
    return str(patch)


def _format_value(operation: dict[str, Any]) -> str:
    after = operation["after"]
    before = operation["before"]
    if after is None:
        # 多轮纠错把旧口径改掉时，该键在新请求里不存在
        return "取消该设置（继承当前方案）"
    if operation["kind"] == "data_patch":
        text = _patch_text(after)
        if before is None:
            return text
        return f"{_patch_text(before)} → {text}"
    if operation["kind"] == "relative_parameter":
        return f"×{float(after):.4g}（原值 × 倍率）"
    if operation["kind"] == "constraint":
        if operation["key"] in ("最大总成本", "现金预算"):
            return f"{float(after) / 10000:.2f} 万元"
        if operation["key"] == "必修任务":
            return "、".join(after) if isinstance(after, list) else str(after)
        return str(after)
    if operation["kind"] == "parameter":
        unit = PARAMETER_UNITS.get(operation["key"])
        if unit:
            return unit(after)
    if operation["kind"] == "weight":
        return f"{float(after):.4g}"
    if before is None:
        return str(after)
    return f"{before} → {after}"


def _format_preview(operations: list[dict[str, Any]], base_version_id: str | None) -> str:
    lines = [
        f"已基于{'方案 ' + base_version_id if base_version_id else '当前默认配置'}准备变更，尚未求解。",
        "变更预览：",
    ]
    for index, operation in enumerate(operations, start=1):
        lines.append(f"{index}. {operation['key']}：{_format_value(operation)}")
    lines.extend(
        [
            "未提及字段继承当前方案。",
            "这次会生成独立沙盘版本，不覆盖现有方案。",
            "回复“确认生成沙盘”执行；回复“取消”放弃本次变更。",
        ]
    )
    return "\n".join(lines)
