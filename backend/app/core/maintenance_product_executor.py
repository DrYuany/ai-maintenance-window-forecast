from __future__ import annotations

import json
import shutil
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.core.planning_intent import PlanningRequest


class MaintenanceProductExecutionError(RuntimeError):
    pass


class MaintenanceProductExecutor:
    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = (project_root or Path(__file__).resolve().parents[3]).resolve()
        self.scripts_dir = self.project_root / "maintenance-milp-modeler" / "scripts"
        if str(self.scripts_dir) not in sys.path:
            sys.path.insert(0, str(self.scripts_dir))

        self._lock = threading.Lock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._solvers: dict[str, Any] = {}

    def prepare(self, request: PlanningRequest) -> tuple[PlanningRequest, dict[str, Any]]:
        from product_data import load_project

        base_data = load_project(self.project_root, request.base)
        overrides = dict(request.overrides)
        for name, multiplier in request.relative_overrides.items():
            if name not in base_data.params:
                raise ValueError(f"无法对未知参数执行相对调整：{name}")
            overrides[name] = float(base_data.params[name]) * multiplier
        prepared = replace(request, overrides=overrides, relative_overrides={})
        data = load_project(
            self.project_root,
            prepared.base,
            overrides=prepared.overrides,
            data_patches=prepared.data_patches,
        )
        snapshot = {
            "input_hash": data.meta.get("输入哈希"),
            "run_hash": data.meta.get("运行哈希"),
            "as_of": data.meta.get("规划时点"),
            "business_version": data.meta.get("业务版本"),
        }
        return prepared, snapshot

    def execute(
        self,
        request: PlanningRequest,
        job_id: str,
        model_fingerprint: str | None = None,
        dsl: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """dsl：受控 DSL（约束/自定义目标），校验后编译成注入闭包传给求解。"""
        cancel_event: threading.Event | None = None
        try:
            from product_data import load_project
            from product_planner import recommend

            if request.relative_overrides:
                request, _ = self.prepare(request)
            data = load_project(
                self.project_root,
                request.base,
                overrides=request.overrides,
                data_patches=request.data_patches,
            )
            extra_constraints = None
            custom_stages = None
            if dsl:
                from app.core.dsl.validator import validate_dsl

                dsl_errors = validate_dsl(dsl, data)
                if dsl_errors:
                    raise MaintenanceProductExecutionError(
                        "DSL 校验失败：" + "；".join(dsl_errors)
                    )
                from app.core.dsl.compiler import constraints_builder, stages_builder

                if dsl.get("constraints"):
                    extra_constraints = constraints_builder(dsl["constraints"], data)
                if dsl.get("objectives"):
                    custom_stages = stages_builder(dsl["objectives"], data)
            suffix = model_fingerprint or job_id
            base_output = self.project_root / "product-output" / f"agent-model-{suffix[:16]}"
            output = base_output
            result_path = output / "结果.json"
            if result_path.exists():
                cached_report = json.loads(result_path.read_text(encoding="utf-8"))
                has_cached_solution = bool((cached_report.get("求解") or {}).get("有解"))
                has_cached_artifact = (
                    (output / "model_artifact.json").exists()
                    or (output / "diagnostic_model_artifact.json").exists()
                )
                if not has_cached_solution or not has_cached_artifact:
                    output = base_output.with_name(f"{base_output.name}-{job_id[-8:]}")
                    result_path = output / "结果.json"
            with self._lock:
                if result_path.exists():
                    report = json.loads(result_path.read_text(encoding="utf-8"))
                else:
                    cancel_event = threading.Event()
                    self._cancel_events[job_id] = cancel_event
                    def register_solver(solver):
                        self._solvers[job_id] = solver
                    report = recommend(
                        data,
                        output,
                        seconds=request.seconds,
                        strategy=request.strategy,
                        mode=request.mode,
                        weights=request.weights,
                        limits=request.limits or None,
                        on_solver=register_solver,
                        cancelled=cancel_event.is_set,
                        model_fingerprint=model_fingerprint,
                        extra_constraints=extra_constraints,
                        custom_stages=custom_stages,
                    )
                    if cancel_event.is_set():
                        shutil.rmtree(output, ignore_errors=True)
                        raise MaintenanceProductExecutionError("求解已由用户停止")
            report["Agent任务"] = {
                "任务编号": job_id,
                "输出目录": str(output),
                "模型输入": request.to_payload(),
            }
            if dsl:
                report["Agent任务"]["DSL"] = dsl
            artifact_path = output / "model_artifact.json"
            if not artifact_path.exists():
                artifact_path = output / "diagnostic_model_artifact.json"
            if artifact_path.exists():
                report["模型产物"] = json.loads(artifact_path.read_text(encoding="utf-8"))
                report["Agent任务"]["模型产物文件"] = str(artifact_path)
            return report
        except Exception as exc:
            raise MaintenanceProductExecutionError(str(exc)) from exc
        finally:
            self._cancel_events.pop(job_id, None)
            self._solvers.pop(job_id, None)

    def cancel(self, job_id: str) -> bool:
        event = self._cancel_events.get(job_id)
        solver = self._solvers.get(job_id)
        if event:
            event.set()
        if solver:
            solver.StopSearch()
        return bool(event or solver)


def solver_status(report: dict[str, Any]) -> tuple[str, bool]:
    solving = report.get("求解", {})
    has_incumbent = bool(solving.get("有解")) and bool(report.get("推荐"))
    explicit_status = solving.get("解状态")
    if explicit_status in {"OPTIMAL", "FEASIBLE", "UNKNOWN", "INFEASIBLE"}:
        return explicit_status, has_incumbent
    stages = solving.get("阶段", [])
    statuses = [stage.get("状态") for stage in stages]
    if has_incumbent and solving.get("分层全部最优"):
        return "OPTIMAL", True
    if has_incumbent:
        return "FEASIBLE", True
    if statuses and statuses[-1] == "INFEASIBLE":
        return "INFEASIBLE", False
    return "UNKNOWN", False

