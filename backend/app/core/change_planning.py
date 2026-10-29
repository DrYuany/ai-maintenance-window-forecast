"""变更计划：自然语言 → DSL 校验 → PlanningRequest 映射（运行时与工具共用）。

从 agent_runtime 抽出，原因是 agent 的 solve 工具也需要同一条链路：
模型决定「要改排程」→ 调 solve → 内部先走 DSL，表达不了再回退旧规划器。

只做「解析 + 映射 + 预览」，不碰求解、不碰版本、不碰待确认状态机。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DSL_LABELS: dict[str, str] = {
    "co_occur": "互斥（不同天/同时）：{tasks}",
    "precedence": "先后顺序：{before} → {after}",
    "earliest": "最早开工：{task} 不早于 {time}",
    "deadline": "最晚完工：{task} 不晚于 {time}",
    "fix": "固定作业线：{task} 只上 {line} 号线",
    "forbidden_dates": "禁止开工日期：{dates}",
    "must": "必修：{task}",
    "exclude": "排除：{task}",
    "cardinality": "完成数 {op} {value}",
    "budget": "预算：{quantity} {op} {value}",
}


def format_dsl_preview(dsl: dict[str, Any]) -> str:
    """把 DSL 约束/目标渲染成待确认预览。"""
    lines = ["已基于当前配置准备自定义约束/目标，尚未求解。", "变更预览："]
    index = 1
    for spec in dsl.get("constraints") or []:
        label = DSL_LABELS.get(spec.get("type"), str(spec))
        try:
            text = label.format(**spec)
        except (KeyError, IndexError):
            text = json.dumps(spec, ensure_ascii=False)
        lines.append(f"{index}. {text}")
        index += 1
    for spec in dsl.get("objectives") or []:
        kind = spec.get("type")
        if kind == "strategy":
            mode = spec.get("mode")
            suffix = f"（{mode}）" if mode else ""
            lines.append(f"{index}. 优化策略：{spec.get('name')}{suffix}")
        elif kind == "weighted":
            weights = "、".join(
                f"{name} {value:.2f}" for name, value in (spec.get("weights") or {}).items()
            )
            lines.append(f"{index}. 加权目标：{weights}")
        elif kind == "lexmin":
            stages = " → ".join(
                f"{'最小化' if s.get('dir') == 'minimize' else '最大化'}{s.get('q')}"
                for s in spec.get("stages", [])
            )
            lines.append(f"{index}. 自定义分层目标：{stages}")
        else:
            lines.append(f"{index}. 自定义目标：{json.dumps(spec, ensure_ascii=False)}")
        index += 1
    for spec in dsl.get("parameters") or []:
        lines.append(f"{index}. 参数：{spec.get('field')} {spec.get('op', '=')} {spec.get('value')}")
        index += 1
    for spec in dsl.get("data") or []:
        lines.append(f"{index}. 数据假设：{spec.get('kind')} {json.dumps(spec, ensure_ascii=False)}")
        index += 1
    lines.extend([
        "这次会生成独立沙盘版本，不覆盖现有方案。",
        "回复“确认生成沙盘”执行；回复“取消”放弃本次变更。",
    ])
    return "\n".join(lines)


@dataclass
class DslChange:
    """一次 DSL 变更计划的产物。"""

    dsl: dict[str, Any]
    request: Any
    operations: list[dict[str, Any]] = field(default_factory=list)
    residual: bool = False
    timeline: bool = False
    custom: bool = False
    preview: str = ""
    source: str = "llm"
    confidence: float = 1.0

    @property
    def expressible(self) -> bool:
        """能否完全用 PlanningRequest 表达（否则要保留 DSL 段）。"""
        return not (self.residual or self.timeline or self.custom)


def plan_dsl_change(
    content: str,
    current: Any,
    base_version_id: str | None,
    context_block: str | None = None,
) -> DslChange | None:
    """解析 → 校验 → 映射。解析不到 DSL 或表达不了时返回 None。"""
    try:
        from app.core.dsl.llm_parser import parse as dsl_parse
        from app.core.dsl.planning_bridge import custom_objectives, dsl_to_request
        from app.core.model_change import _diff_requests
    except Exception:  # pragma: no cover - DSL 包缺失时静默回退
        return None

    try:
        parsed = dsl_parse(content, context_block=context_block)
    except Exception:  # noqa: BLE001 - LLM 不可用/失败由上层决定
        return None
    if parsed.route != "dsl" or not parsed.dsl:
        return None

    updated, residual, timeline = dsl_to_request(parsed.dsl, current)
    custom = custom_objectives(parsed.dsl)
    operations = _diff_requests(current, updated)
    preview = (
        format_dsl_preview(parsed.dsl)
        if (residual or timeline or custom) or not operations
        else ""
    )
    return DslChange(
        dsl=parsed.dsl,
        request=updated,
        operations=operations,
        residual=residual,
        timeline=timeline,
        custom=custom,
        preview=preview,
        source=parsed.source,
        confidence=parsed.confidence,
    )
