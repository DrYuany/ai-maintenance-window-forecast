from __future__ import annotations

import hashlib
import json
from typing import Any

from app.schemas.agent import CandidateWindow, WindowVersion


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _solver(version: WindowVersion) -> dict[str, Any]:
    return _dict((version.resultSummary or {}).get("solver"))


def _status(version: WindowVersion) -> str:
    value = (
        _solver(version).get("解状态")
        or _solver(version).get("求解状态")
        or _solver(version).get("状态")
    )
    return str(value) if value else "UNKNOWN"


def _money(value: Any) -> float | None:
    try:
        return round(float(value) / 10_000, 2)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_window(version: WindowVersion) -> CandidateWindow | None:
    windows = version.windows or []
    return windows[0] if windows else None


def _window_range(version: WindowVersion) -> dict[str, str | None]:
    window = _first_window(version)
    if not window:
        return {"id": None, "start": None, "end": None}
    return {
        "id": window.windowId,
        "start": window.metrics.startTime,
        "end": window.metrics.endTime,
    }


def _schedule_map(version: WindowVersion) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in version.repairSchedule or []:
        result[item.taskId] = {
            "taskId": item.taskId,
            "turbineId": item.turbineId,
            "componentName": item.componentName,
            "startTime": item.startTime,
            "endTime": item.endTime,
            "durationHours": item.durationHours,
        }
    return result


def _unscheduled_map(version: WindowVersion) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in _list((version.resultSummary or {}).get("unscheduledTasks")):
        raw = _dict(item)
        task_id = raw.get("任务编号") or raw.get("taskId")
        if not task_id:
            continue
        result[str(task_id)] = raw
    return result


def _data_snapshot(version: WindowVersion, artifact: dict[str, Any] | None) -> dict[str, Any]:
    if artifact:
        return _dict(artifact.get("dataSnapshot"))
    summary = _dict((version.resultSummary or {}).get("data"))
    return {
        "asOf": (
            summary.get("规划时点")
            or summary.get("数据时点")
            or summary.get("asOf")
        ),
        "inputHash": summary.get("输入哈希") or summary.get("inputHash"),
    }


def _artifact_parameters(artifact: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not artifact:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in _list(artifact.get("parameters")):
        raw = _dict(item)
        key = str(raw.get("id") or raw.get("label") or "")
        if key:
            result[key] = raw
    return result


def _artifact_stages(artifact: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not artifact:
        return []
    return [
        _dict(item)
        for item in _list(_dict(artifact.get("objectives")).get("stages"))
    ]


def _artifact_constraints(artifact: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not artifact:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in _list(_dict(artifact.get("constraints")).get("groups")):
        raw = _dict(item)
        kind = str(raw.get("kind") or raw.get("id") or "")
        if kind:
            result[kind] = raw
    return result


def _metric(
    key: str,
    label: str,
    left: Any,
    right: Any,
    unit: str | None = None,
    better: str | None = None,
) -> dict[str, Any]:
    delta: float | None = None
    pct: float | None = None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        delta = round(float(right) - float(left), 2)
        if float(left) != 0:
            pct = round((float(right) - float(left)) / abs(float(left)) * 100, 2)
    winner: str | None = None
    if better and delta is not None:
        higher = right > left
        lower = right < left
        if not higher and not lower:
            winner = "tie"
        elif better == "lower":
            winner = "right" if lower else "left"
        elif better == "higher":
            winner = "right" if higher else "left"
    return {
        "key": key,
        "label": label,
        "left": left,
        "right": right,
        "unit": unit,
        "delta": delta,
        "deltaPct": pct,
        "winner": winner,
    }


def _task_rows(
    task_ids: set[str],
    schedule: dict[str, dict[str, Any]],
    other_unscheduled: dict[str, dict[str, Any]],
    limit: int = 12,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    for task_id in sorted(task_ids):
        item = dict(schedule.get(task_id) or {"taskId": task_id})
        if task_id in other_unscheduled:
            other = other_unscheduled[task_id]
            item["otherReason"] = other.get("原因") or other.get("reason")
            item["otherStatus"] = other.get("任务状态") or other.get("status")
        rows.append(item)
    return rows[:limit], len(rows)


def _changed_parameters(
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for key in sorted(set(left) | set(right)):
        before = left.get(key)
        after = right.get(key)
        before_value = before.get("value") if before else None
        after_value = after.get("value") if after else None
        if before_value == after_value and bool(before) == bool(after):
            continue
        changes.append(
            {
                "id": key,
                "label": (after or before or {}).get("label") or key,
                "unit": (after or before or {}).get("unit"),
                "left": before_value,
                "right": after_value,
                "source": (after or before or {}).get("source"),
                "asOf": (after or before or {}).get("asOf"),
            }
        )
    return changes[:limit]


def _changed_stages(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for index in range(max(len(left), len(right))):
        before = left[index] if index < len(left) else {}
        after = right[index] if index < len(right) else {}
        signature_before = (
            before.get("name"),
            before.get("sense"),
            before.get("expressionTermCount"),
        )
        signature_after = (
            after.get("name"),
            after.get("sense"),
            after.get("expressionTermCount"),
        )
        if signature_before == signature_after:
            continue
        changes.append(
            {
                "priority": after.get("priority") or before.get("priority") or index + 1,
                "left": {
                    "name": before.get("name"),
                    "sense": before.get("sense"),
                    "terms": before.get("expressionTermCount"),
                },
                "right": {
                    "name": after.get("name"),
                    "sense": after.get("sense"),
                    "terms": after.get("expressionTermCount"),
                },
            }
        )
    return changes


def _changed_constraints(
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for kind in sorted(set(left) | set(right)):
        before = _number((left.get(kind) or {}).get("count")) or 0
        after = _number((right.get(kind) or {}).get("count")) or 0
        if before == after:
            continue
        changes.append(
            {
                "kind": kind,
                "meaning": (right.get(kind) or left.get(kind) or {}).get("meaning"),
                "left": int(before),
                "right": int(after),
            }
        )
    return changes


def _comparison_id(left: WindowVersion, right: WindowVersion) -> str:
    raw = f"{left.id}|{right.id}|{left.modelFingerprint}|{right.modelFingerprint}"
    return "cmp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def compare_versions(
    left: WindowVersion,
    right: WindowVersion,
    *,
    left_number: int,
    right_number: int,
    left_artifact: dict[str, Any] | None = None,
    right_artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    left_summary = left.resultSummary or {}
    right_summary = right.resultSummary or {}
    left_schedule = _schedule_map(left)
    right_schedule = _schedule_map(right)
    left_unscheduled = _unscheduled_map(left)
    right_unscheduled = _unscheduled_map(right)
    left_snapshot = _data_snapshot(left, left_artifact)
    right_snapshot = _data_snapshot(right, right_artifact)

    left_tasks = set(left_schedule)
    right_tasks = set(right_schedule)
    only_left = left_tasks - right_tasks
    only_right = right_tasks - left_tasks
    moved = {
        task_id
        for task_id in left_tasks & right_tasks
        if left_schedule[task_id].get("startTime") != right_schedule[task_id].get("startTime")
        or left_schedule[task_id].get("endTime") != right_schedule[task_id].get("endTime")
    }

    left_stages = _artifact_stages(left_artifact)
    right_stages = _artifact_stages(right_artifact)
    left_parameters = _artifact_parameters(left_artifact)
    right_parameters = _artifact_parameters(right_artifact)
    left_constraints = _artifact_constraints(left_artifact)
    right_constraints = _artifact_constraints(right_artifact)
    left_solver = _solver(left)
    right_solver = _solver(right)

    same_data = bool(
        left_snapshot.get("inputHash")
        and right_snapshot.get("inputHash")
        and left_snapshot.get("inputHash") == right_snapshot.get("inputHash")
    )
    if not left_snapshot.get("inputHash") or not right_snapshot.get("inputHash"):
        same_data = bool(
            left_snapshot.get("asOf")
            and right_snapshot.get("asOf")
            and left_snapshot.get("asOf") == right_snapshot.get("asOf")
        )
    same_universe = left_summary.get("totalTaskCount") == right_summary.get("totalTaskCount")
    same_objectives = [item.get("name") for item in left_stages] == [
        item.get("name") for item in right_stages
    ]
    same_model = bool(
        left.modelFingerprint
        and right.modelFingerprint
        and left.modelFingerprint == right.modelFingerprint
    )
    cost_comparable = same_data and same_universe
    savings_allowed = cost_comparable and left_tasks == right_tasks

    notes: list[str] = []
    if not same_data:
        notes.append("两次运行的数据快照不同，成本差异不能全部归因于参数或目标。")
    if not same_universe:
        notes.append("两次运行的待修任务总数不同，完成量不能直接横比。")
    if left_tasks != right_tasks:
        notes.append("两次完成的任务集合不同，不能宣称同等服务下节省。")
    if same_model:
        notes.append("两次运行的模型指纹相同；结论应一致，差异更可能来自求解时间或随机性。")
    if not left_artifact or not right_artifact:
        notes.append("至少一侧缺少完整模型产物，参数与约束差异只能部分核对。")

    total_left = _money(left_summary.get("totalCost"))
    total_right = _money(right_summary.get("totalCost"))
    cash_left = _money(left_summary.get("cashCost"))
    cash_right = _money(right_summary.get("cashCost"))
    loss_left = _money(left_summary.get("generationLoss"))
    loss_right = _money(right_summary.get("generationLoss"))
    tasks_left = int(left_summary.get("scheduledTaskCount") or len(left_tasks))
    tasks_right = int(right_summary.get("scheduledTaskCount") or len(right_tasks))
    vessel_left = int(left_summary.get("vesselDays") or 0)
    vessel_right = int(right_summary.get("vesselDays") or 0)

    left_window = _window_range(left)
    right_window = _window_range(right)
    window_metric = _metric(
        "windowStart",
        "窗口开始",
        left_window.get("start"),
        right_window.get("start"),
    )
    metrics = [
        {
            "key": "window",
            "label": "建议窗口",
            "left": left_window,
            "right": right_window,
            "changed": left_window != right_window,
        },
        _metric("totalCost", "总成本", total_left, total_right, "万元", "lower"),
        _metric("cashCost", "现金支出", cash_left, cash_right, "万元", "lower"),
        _metric("generationLoss", "停机损失", loss_left, loss_right, "万元", "lower"),
        {
            "key": "scheduledTasks",
            "label": "完成任务",
            "left": {"completed": tasks_left, "total": left_summary.get("totalTaskCount")},
            "right": {"completed": tasks_right, "total": right_summary.get("totalTaskCount")},
            "unit": "项",
            "delta": tasks_right - tasks_left,
            "winner": (
                "tie"
                if tasks_right == tasks_left
                else "right" if tasks_right > tasks_left else "left"
            ),
        },
        _metric("vesselDays", "船舶艘天", vessel_left, vessel_right, "艘天", "lower"),
        _metric(
            "latestCompletion",
            "最晚完工",
            left_summary.get("latestCompletion"),
            right_summary.get("latestCompletion"),
        ),
        _metric(
            "solverStatus",
            "求解状态",
            _status(left),
            _status(right),
        ),
    ]

    left_only_rows, left_only_total = _task_rows(
        only_left, left_schedule, right_unscheduled
    )
    right_only_rows, right_only_total = _task_rows(
        only_right, right_schedule, left_unscheduled
    )
    moved_rows = [
        {
            "taskId": task_id,
            "left": {
                "startTime": left_schedule[task_id].get("startTime"),
                "endTime": left_schedule[task_id].get("endTime"),
            },
            "right": {
                "startTime": right_schedule[task_id].get("startTime"),
                "endTime": right_schedule[task_id].get("endTime"),
            },
        }
        for task_id in sorted(moved)[:12]
    ]

    layer1 = {
        "left": {
            "id": left.id,
            "planNumber": left_number,
            "label": f"方案{left_number:02d}",
            "generatedAt": left.generatedAt,
            "prompt": left.userPrompt,
            "strategy": left_summary.get("strategy"),
            "objectiveMode": left_summary.get("objectiveMode"),
            "status": _status(left),
            "verified": left_summary.get("verified"),
            "modelFingerprint": left.modelFingerprint,
        },
        "right": {
            "id": right.id,
            "planNumber": right_number,
            "label": f"方案{right_number:02d}",
            "generatedAt": right.generatedAt,
            "prompt": right.userPrompt,
            "strategy": right_summary.get("strategy"),
            "objectiveMode": right_summary.get("objectiveMode"),
            "status": _status(right),
            "verified": right_summary.get("verified"),
            "modelFingerprint": right.modelFingerprint,
        },
        "comparability": {
            "sameDataSnapshot": same_data,
            "sameTaskUniverse": same_universe,
            "sameObjectiveStructure": same_objectives,
            "sameModelFingerprint": same_model,
            "costComparable": cost_comparable,
            "savingsClaimAllowed": savings_allowed,
            "notes": notes,
        },
        "metrics": metrics,
        "windowChange": {
            "changed": left_window != right_window,
            "left": left_window,
            "right": right_window,
            "startMetric": window_metric,
        },
        "quickVerdict": {
            "cheaper": (
                None
                if not cost_comparable or total_left is None or total_right is None
                else "tie"
                if total_left == total_right
                else "right" if total_right < total_left else "left"
            ),
            "moreTasks": (
                "tie" if tasks_left == tasks_right else "right" if tasks_right > tasks_left else "left"
            ),
            "fewerVesselDays": (
                "tie"
                if vessel_left == vessel_right
                else "right" if vessel_right < vessel_left else "left"
            ),
        },
    }

    layer2 = {
        "taskSet": {
            "onlyLeft": left_only_rows,
            "onlyLeftCount": left_only_total,
            "onlyRight": right_only_rows,
            "onlyRightCount": right_only_total,
            "bothCount": len(left_tasks & right_tasks),
            "moved": moved_rows,
            "movedCount": len(moved),
        },
        "costDrivers": {
            "totalCost": _metric("totalCost", "总成本", total_left, total_right, "万元", "lower"),
            "cashCost": _metric("cashCost", "现金支出", cash_left, cash_right, "万元", "lower"),
            "generationLoss": _metric(
                "generationLoss", "停机损失", loss_left, loss_right, "万元", "lower"
            ),
        },
        "parameterChanges": _changed_parameters(left_parameters, right_parameters),
        "objectiveChanges": _changed_stages(left_stages, right_stages),
        "constraintChanges": _changed_constraints(left_constraints, right_constraints),
        "solver": {
            "left": {
                "status": _status(left),
                "elapsedSeconds": left_solver.get("总耗时秒") or left_solver.get("总耗时"),
                "parameters": left_solver.get("求解器参数"),
                "stopReason": left_solver.get("终止原因"),
                "verified": left_summary.get("verified"),
            },
            "right": {
                "status": _status(right),
                "elapsedSeconds": right_solver.get("总耗时秒") or right_solver.get("总耗时"),
                "parameters": right_solver.get("求解器参数"),
                "stopReason": right_solver.get("终止原因"),
                "verified": right_summary.get("verified"),
            },
        },
        "baselines": {
            "left": left_summary.get("baselineSavings"),
            "right": right_summary.get("baselineSavings"),
        },
    }

    layer3 = {
        "drilldown": {
            "versionIds": [left.id, right.id],
            "readTool": "read_versions",
            "taskDetail": "read_versions(versions=[...], include=[\"tasks\", \"unscheduled\"])",
            "modelMath": "read_versions(versions=[...], include=[\"model\"])",
            "note": "逐任务、逐约束或原始模型需要时再单独读取；本对比只带有限条目。",
        },
        "artifactAvailability": {
            "left": bool(left_artifact),
            "right": bool(right_artifact),
        },
        "counts": {
            "leftTasks": len(left_tasks),
            "rightTasks": len(right_tasks),
            "leftUnscheduled": len(left_unscheduled),
            "rightUnscheduled": len(right_unscheduled),
        },
    }

    payload = {
        "schema": "version-compare-v1",
        "comparisonId": _comparison_id(left, right),
        "layer1Facts": layer1,
        "layer2Drivers": layer2,
        "layer3Index": layer3,
    }
    payload["payloadBytes"] = len(
        json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    )
    return payload


def render_compare_summary(payload: dict[str, Any]) -> str:
    layer1 = _dict(payload.get("layer1Facts"))
    layer2 = _dict(payload.get("layer2Drivers"))
    left = _dict(layer1.get("left"))
    right = _dict(layer1.get("right"))
    metrics = {
        item.get("key"): item
        for item in _list(layer1.get("metrics"))
        if isinstance(item, dict)
    }

    def value(key: str, side: str, unit: str = "") -> str:
        item = _dict(metrics.get(key))
        raw = item.get(side)
        if isinstance(raw, dict):
            if "completed" in raw:
                text = f"{raw.get('completed')}/{raw.get('total') or '?'}"
            elif "start" in raw:
                text = f"{raw.get('start')} 至 {raw.get('end')}"
            else:
                text = "未记录"
        elif raw is None:
            text = "未记录"
        else:
            text = str(raw)
        return f"{text} {unit}".strip()

    def delta(key: str, unit: str = "") -> str:
        item = _dict(metrics.get(key))
        raw = item.get("delta")
        if raw is None:
            return "不可比"
        return f"{raw:+.2f} {unit}".strip() if isinstance(raw, float) else f"{raw} {unit}".strip()

    comparability = _dict(layer1.get("comparability"))
    task_set = _dict(layer2.get("taskSet"))
    lines = [
        f"**{left.get('label')} 与 {right.get('label')} 的事实对比**",
        "",
        f"- 窗口：{value('window', 'left')} → {value('window', 'right')}",
        (
            f"- 总成本：{value('totalCost', 'left', '万元')} → "
            f"{value('totalCost', 'right', '万元')}（{delta('totalCost', '万元')}）"
        ),
        (
            f"- 完成任务：{value('scheduledTasks', 'left')} → "
            f"{value('scheduledTasks', 'right')}"
        ),
        (
            f"- 船舶艘天：{value('vesselDays', 'left', '艘天')} → "
            f"{value('vesselDays', 'right', '艘天')}（{delta('vesselDays', '艘天')}）"
        ),
        (
            f"- 求解状态：{value('solverStatus', 'left')} → "
            f"{value('solverStatus', 'right')}"
        ),
        (
            f"- 任务集合：仅左侧 {task_set.get('onlyLeftCount', 0)} 项，"
            f"仅右侧 {task_set.get('onlyRightCount', 0)} 项，"
            f"共同 {task_set.get('bothCount', 0)} 项，改期 {task_set.get('movedCount', 0)} 项"
        ),
        "",
        (
            "可比性："
            + (
                "两次数据快照与任务集合一致，成本可直接比较。"
                if comparability.get("savingsClaimAllowed")
                else "存在数据快照、任务集合或模型产物差异，不能直接宣称节省。"
            )
        ),
    ]
    notes = _list(comparability.get("notes"))
    if notes:
        lines.append("限制：" + "；".join(str(item) for item in notes[:3]))
    lines.append("逐任务、逐约束或原始模型可在方案详情中查看。")
    return "\n".join(lines)
