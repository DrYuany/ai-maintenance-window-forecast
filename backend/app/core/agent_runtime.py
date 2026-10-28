from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Queue
from threading import Thread
from typing import Any
from uuid import uuid4

from app.core.memory_manager import MemoryManager
from app.core.agent_tools import AgentToolbox
from app.core.agent_memory import (
    ConversationSummaryRepository,
    DecisionMemoryRepository,
    UserProfileRepository,
    build_summary,
)
from app.core.agent_trace import AgentTrace, AgentTraceRepository, ToolCallRecord, new_trace_id
from app.core.conversation_agent import ConversationAgent, ConversationAgentError
from app.core.decision_contract import DecisionCompiler
from app.core.maintenance_product_executor import MaintenanceProductExecutor, solver_status
from app.core.model_change import (
    build_change_plan,
    build_scenario_batch,
    is_cancellation,
    is_confirmation,
    is_scenario_batch,
    is_solve_execution_command,
)
from app.core.model_query import (
    detect_model_question,
    render_capability_answer,
    render_missing_artifact,
    render_model_formula,
    render_parameter_answer,
    render_solution_answer,
    render_structure_answer,
    render_status_answer,
)
from app.core.operations_query import alarm_arguments, detect_operations_question
from app.core.planning_intent import PlanningIntentCompiler, PlanningRequest
from app.core.result_normalizer import ResultNormalizer
from app.core.solve_jobs import SolveJobRepository
from app.core.version_manager import VersionManager
from app.core.version_compare import render_compare_summary
from app.core.version_reference import (
    chronological_versions,
    is_version_switch_request,
    resolve_version_reference,
)
from app.db.models import (
    CandidateRepository,
    ConversationRepository,
    MessageRepository,
    ModelArtifactRepository,
    VersionRepository,
)
from app.schemas.agent import AgentResponse, AssistantMessagePayload, DecisionArtifactPayload, Message, SolveEvidencePayload, SolveJobPayload
from app.schemas.dashboard import DashboardResponse


SHANGHAI_TZ = timezone(timedelta(hours=8))
SKILL_NAME = "maintenance-milp-modeler"


class StreamSink:
    """Small adapter so one runtime can both return JSON and stream SSE."""

    def __init__(self, emit) -> None:
        self.emit = emit
        self.text = ""

    def token(self, text: str) -> None:
        if not text:
            return
        self.text += text
        self.emit({"event": "token", "data": {"text": text}})

    def status(self, label: str) -> None:
        self.emit({"event": "status", "data": {"label": label}})

    def replace(self, text: str) -> None:
        self.text = text
        self.emit({"event": "replace", "data": {"text": text}})

    def finish(self, content: str) -> None:
        if not self.text:
            self.token(content)
        elif self.text.strip() != content.strip():
            self.replace(content)


class AgentRuntime:
    def __init__(
        self,
        conversations: ConversationRepository,
        messages: MessageRepository,
        memories: MemoryManager,
        versions: VersionRepository,
        model_artifacts: ModelArtifactRepository | None,
        candidates: CandidateRepository,
        normalizer: ResultNormalizer,
        version_manager: VersionManager,
        jobs: SolveJobRepository | None = None,
        product_executor: MaintenanceProductExecutor | None = None,
        job_pool: ThreadPoolExecutor | None = None,
        conversation_agent: ConversationAgent | None = None,
        traces: AgentTraceRepository | None = None,
        summary_repo: ConversationSummaryRepository | None = None,
        profile_repo: UserProfileRepository | None = None,
        decision_repo: DecisionMemoryRepository | None = None,
    ) -> None:
        self.conversations = conversations
        self.messages = messages
        self.memories = memories
        self.versions = versions
        self.model_artifacts = model_artifacts
        self.candidates = candidates
        self.normalizer = normalizer
        self.version_manager = version_manager
        self.jobs = jobs
        self.product_executor = product_executor or MaintenanceProductExecutor()
        self.intent_compiler = PlanningIntentCompiler()
        self.job_pool = job_pool or ThreadPoolExecutor(max_workers=2, thread_name_prefix="maintenance-solve")
        self._futures: dict[str, Future] = {}
        self.conversation_agent = conversation_agent or ConversationAgent()
        self.traces = traces
        self.summary_repo = summary_repo
        self.profile_repo = profile_repo
        self.decision_repo = decision_repo
        self._tls = threading.local()

    # ── trace ────────────────────────────────────────────────────────────────

    def _begin_trace(self, conversation_id: str, content: str) -> None:
        self._tls.trace = AgentTrace(
            requestId=new_trace_id(),
            conversationId=conversation_id,
            message=content,
            route="unknown",
        )
        self._tls.trace_started = time.monotonic()

    def _current_trace(self) -> AgentTrace | None:
        return getattr(self._tls, "trace", None)

    def _record_tool_call(self, record: dict[str, Any]) -> None:
        trace = self._current_trace()
        if trace is not None:
            trace.add_tool_call(ToolCallRecord(**record))

    def _finish_trace(
        self,
        conversation_id: str,
        action_type: str,
        solve_evidence: SolveEvidencePayload | None,
    ) -> str | None:
        trace = self._current_trace()
        if trace is None:
            return None
        trace.route = action_type
        trace.latencyMs = round((time.monotonic() - self._tls.trace_started) * 1000, 1)
        trace.model = self.conversation_agent.model
        if solve_evidence and solve_evidence.modelFingerprint:
            trace.modelFingerprint = solve_evidence.modelFingerprint
        trace_id = trace.requestId
        if self.traces is not None:
            self.traces.add(trace)
        self._tls.trace = None
        self._tls.trace_started = None
        return trace_id

    def _update_summary(self, conversation_id: str) -> None:
        """每个回合结束后更新会话摘要；失败不阻塞主流程。"""
        if self.summary_repo is None:
            return
        try:
            history = self.messages.list_by_conversation(conversation_id)
            memory = self.memories.get_or_create(conversation_id)
            summary = build_summary(conversation_id, history, memory.latestResultId)
            self.summary_repo.upsert(summary)
        except Exception:  # pragma: no cover - 摘要失败不阻断对话
            pass

    def submit_message(
        self,
        conversation_id: str,
        content: str,
        selected_version_id: str | None = None,
        selected_window_id: str | None = None,
        sink: StreamSink | None = None,
    ) -> AgentResponse:
        response = self._submit_message(
            conversation_id,
            content,
            selected_version_id=selected_version_id,
            selected_window_id=selected_window_id,
            sink=sink,
        )
        if sink:
            sink.finish(response.assistantMessage.content)
        return response

    def stream_message(
        self,
        conversation_id: str,
        content: str,
        selected_version_id: str | None = None,
        selected_window_id: str | None = None,
    ):
        queue: Queue = Queue()

        def run() -> None:
            try:
                response = self.submit_message(
                    conversation_id,
                    content,
                    selected_version_id=selected_version_id,
                    selected_window_id=selected_window_id,
                    sink=StreamSink(queue.put),
                )
                queue.put({"event": "done", "data": response.model_dump()})
            except Exception as exc:  # pragma: no cover - transport boundary
                queue.put({"event": "error", "data": {"message": str(exc)}})

        Thread(target=run, name=f"agent-stream-{conversation_id}", daemon=True).start()
        while True:
            item = queue.get()
            yield item
            if item.get("event") in {"done", "error"}:
                break

    def _submit_message(
        self,
        conversation_id: str,
        content: str,
        selected_version_id: str | None = None,
        selected_window_id: str | None = None,
        sink: StreamSink | None = None,
    ) -> AgentResponse:
        conversation = self.conversations.get(conversation_id)
        if not conversation:
            raise ValueError("Conversation not found")

        self._begin_trace(conversation_id, content)

        now = datetime.now(SHANGHAI_TZ).isoformat()
        self.messages.add(Message(
            id=f"msg_{uuid4().hex[:8]}",
            conversationId=conversation_id,
            role="user",
            content=content,
            createdAt=now,
        ))
        memory = self.memories.get_or_create(conversation_id)
        selected_version, reference_kind = self._resolve_version(
            conversation_id,
            selected_version_id,
            memory,
            content,
        )
        if reference_kind in {"id", "ordinal", "previous"} and selected_version is None:
            return self._finalize_response(
                conversation_id,
                "没有找到你提到的方案。请确认方案序号或提供 ver_ 开头的版本号。",
                "context_not_found",
                memory.activeView,
                [],
            )
        filters = dict(memory.activeFilters)
        pending = filters.get("pendingChange")
        if isinstance(pending, dict) and (
            selected_version_id or reference_kind in {"id", "ordinal", "latest", "previous"}
        ):
            pending_base = pending.get("baseVersionId")
            if pending_base and selected_version and pending_base != selected_version.id:
                filters.pop("pendingChange", None)
                pending = None
                memory = self.memories.apply_patch(memory, {"activeFilters": filters})

        if isinstance(pending, dict) and is_cancellation(content):
            filters.pop("pendingChange", None)
            self.memories.apply_patch(memory, {"activeFilters": filters})
            return self._finalize_response(
                conversation_id,
                "已取消本次模型变更，没有生成新的沙盘方案。",
                "change_cancelled",
                memory.activeView,
                [],
            )

        if isinstance(pending, dict) and is_confirmation(content):
            filters.pop("pendingChange", None)
            memory = self.memories.apply_patch(memory, {"activeFilters": filters})
            if pending.get("kind") == "batch":
                return self._start_batch_solve(
                    conversation_id=conversation_id,
                    batch=pending,
                    memory=memory,
                )
            if pending.get("kind") == "dsl":
                return self._start_dsl_solve(
                    conversation_id=conversation_id,
                    dsl=pending.get("dsl") or {},
                    content=str(pending.get("sourcePrompt") or content),
                    memory=memory,
                )
            try:
                request = PlanningRequest(**pending["request"])
            except (KeyError, TypeError, ValueError):
                return self._finalize_response(
                    conversation_id,
                    "待确认的模型变更已经失效，请重新描述变更。",
                    "change_expired",
                    memory.activeView,
                    [],
                )
            return self._start_planning_solve(
                conversation_id=conversation_id,
                content=str(pending.get("sourcePrompt") or content),
                request=request,
                memory=memory,
            )

        if is_solve_execution_command(content):
            return self._start_planning_solve(
                conversation_id=conversation_id,
                content=content,
                request=self._planning_request_from_context(memory, selected_version),
                memory=memory,
            )

        model_question = detect_model_question(content)
        if model_question:
            if model_question == "capability":
                return self._answer_capability_question(
                    conversation_id,
                    content,
                    memory,
                )
            if model_question == "environment":
                return self._answer_environment_question(
                    conversation_id,
                    content,
                    memory,
                    selected_version,
                )
            if model_question == "structure" and not selected_version:
                return self._answer_structure_question(
                    conversation_id,
                    content,
                    memory,
                )
            if not selected_version:
                if model_question == "parameter":
                    return self._answer_operations_question(
                        conversation_id,
                        content,
                        "site",
                        "parameters",
                        selected_version,
                        memory,
                        sink=sink,
                    )
                return self._finalize_response(
                    conversation_id,
                    "当前会话还没有可查询的排程方案。请先生成方案，之后我才能解释模型、参数或结果。",
                    "model_query_empty",
                    memory.activeView,
                    [],
                )
            if model_question == "structure":
                model_question = "formula"
            if model_question in {"parameter", "status"}:
                return self._answer_model_question(
                    conversation_id,
                    content,
                    model_question,
                    selected_version,
                    memory,
                )
            return self._answer_agent_question(
                conversation_id,
                content,
                model_question,
                selected_version,
                memory,
                sink=sink,
            )

        if is_version_switch_request(content) and selected_version:
            ordered = chronological_versions(
                self.versions.list_by_conversation(conversation_id)
            )
            plan_number = next(
                (index for index, item in enumerate(ordered, start=1) if item.id == selected_version.id),
                None,
            )
            plan_label = f"{plan_number:02d}" if plan_number else selected_version.id
            filters.pop("pendingChange", None)
            self.memories.apply_patch(memory, {
                "selectedVersionIds": [selected_version.id],
                "activeFilters": filters,
            })
            return self._finalize_response(
                conversation_id,
                (
                    f"已切换到方案 {plan_label}"
                    f"（{selected_version.id}）。后续“这版/这个模型”会绑定该版本；"
                    "只查看和讨论不会触发新求解。"
                ),
                "context_switch",
                memory.activeView,
                [],
            )

        # 一句话里同时提了多个场景时，先按场景拆开，再逐个预览。
        if is_scenario_batch(content):
            current_batch = self._planning_request_from_context(memory, selected_version)
            batch = build_scenario_batch(
                content,
                current_batch,
                selected_version.id if selected_version else memory.latestResultId,
                self.intent_compiler,
            )
            filters["pendingChange"] = {
                "kind": "batch",
                "baseVersionId": batch.base_version_id,
                "scenarios": [
                    {
                        "label": item.label,
                        "request": item.request.to_payload(),
                        "operations": item.operations,
                        "notes": item.notes,
                        "sourcePrompt": item.source_prompt,
                    }
                    for item in batch.scenarios
                ],
                "compare": batch.compare,
                "preview": batch.preview,
                "sourcePrompt": content,
            }
            self.memories.apply_patch(
                memory,
                {
                    "currentGoal": content,
                    "currentIntent": "scenario_batch",
                    "activeFilters": filters,
                },
            )
            return self._finalize_response(
                conversation_id,
                batch.preview,
                "change_preview",
                memory.activeView,
                [],
            )

        planning_request = self.intent_compiler.is_planning_request(content)
        if not planning_request:
            operations_question = detect_operations_question(content)
            if operations_question:
                kind, scope = operations_question
                return self._answer_operations_question(
                    conversation_id,
                    content,
                    kind,
                    scope,
                    selected_version,
                    memory,
                    sink=sink,
                )
            # DSL 增量（第一期）：约束/自定义目标类修改，走预览→确认→求解。
            dsl_result = self._try_dsl_parse(content)
            if (
                dsl_result is not None
                and dsl_result.route == "dsl"
                and dsl_result.dsl is not None
            ):
                base_version_id = selected_version.id if selected_version else memory.latestResultId
                filters["pendingChange"] = {
                    "kind": "dsl",
                    "dsl": dsl_result.dsl,
                    "source": dsl_result.source,
                    "baseVersionId": base_version_id,
                    "sourcePrompt": content,
                    "preview": self._format_dsl_preview(dsl_result.dsl),
                }
                self.memories.apply_patch(
                    memory,
                    {
                        "currentIntent": "dsl_change",
                        "activeFilters": filters,
                    },
                )
                return self._finalize_response(
                    conversation_id,
                    filters["pendingChange"]["preview"],
                    "dsl_preview",
                    memory.activeView,
                    [],
                )
            history = self.messages.list_by_conversation(conversation_id)
            try:
                reply = self._answer_with_conversation_agent(
                    conversation_id=conversation_id,
                    content=content,
                    history=history,
                    memory=memory,
                    selected_version=selected_version,
                    sink=sink,
                )
            except ConversationAgentError:
                reply = (
                    self._fallback_compare_reply(
                        conversation_id,
                        content,
                        selected_version,
                    )
                    or (
                        "正式排程使用 OR-Tools CP-SAT 求解有界整数调度模型。"
                        "自然语言会先编译成固定模型契约，再进行求解和独立复核；"
                        "OPTIMAL、FEASIBLE、UNKNOWN 与 INFEASIBLE 会严格区分。"
                    )
                )
            memory = self.memories.get_or_create(conversation_id)
            self.memories.apply_patch(
                memory,
                {
                    "currentGoal": content,
                    "currentIntent": "agent_answer",
                },
            )
            return self._finalize_response(
                conversation_id,
                reply,
                "assistant_reply",
                memory.activeView,
                [],
            )

        current = self._planning_request_from_context(memory, selected_version)
        base_version_id = selected_version.id if selected_version else memory.latestResultId
        # 第三期：统一解析器优先（旧正则作为其规则层），DSL 表达不了的再回退旧规划器。
        change_plan = self._change_plan_from_dsl(
            content, current, base_version_id,
        )
        dsl_pending: dict[str, Any] | None = None
        if change_plan is None:
            change_plan = build_change_plan(
                content,
                current,
                base_version_id,
                self.intent_compiler,
            )
        elif change_plan.operations:
            # DSL 里含 PlanningRequest 表达不了的约束/目标（co_occur 等）→ 走 DSL 待确认
            from app.core.dsl.planning_bridge import custom_objectives, dsl_to_request

            dsl = self._try_dsl_parse(content)
            if dsl is not None and dsl.dsl:
                residual, timeline = dsl_to_request(dsl.dsl, current)[1:]
                if residual or timeline or custom_objectives(dsl.dsl):
                    dsl_pending = {
                        "kind": "dsl",
                        "dsl": dsl.dsl,
                        "baseVersionId": base_version_id,
                        "sourcePrompt": content,
                        "preview": self._format_dsl_preview(dsl.dsl),
                    }
        if dsl_pending is not None:
            filters["pendingChange"] = dsl_pending
            self.memories.apply_patch(memory, {
                "currentIntent": "dsl_change",
                "activeFilters": filters,
            })
            return self._finalize_response(
                conversation_id,
                dsl_pending["preview"],
                "dsl_preview",
                memory.activeView,
                [],
            )
        if not change_plan.operations:
            return self._finalize_response(
                conversation_id,
                change_plan.preview,
                "change_clarification",
                memory.activeView,
                [],
            )
        filters["pendingChange"] = {
            "baseVersionId": base_version_id,
            "request": change_plan.request.to_payload(),
            "operations": change_plan.operations,
            "preview": change_plan.preview,
            "sourcePrompt": change_plan.source_prompt,
        }
        self.memories.apply_patch(memory, {
            "activeFocusTag": change_plan.request.strategy,
            "activeFilters": filters,
        })
        return self._finalize_response(
            conversation_id,
            change_plan.preview,
            "change_preview",
            memory.activeView,
            [],
        )

    def _start_batch_solve(
        self,
        *,
        conversation_id: str,
        batch: dict[str, Any],
        memory,
    ) -> AgentResponse:
        """一句话里提了多个场景时，一次确认后按场景顺序逐个求解。"""
        scenarios = list(batch.get("scenarios") or [])
        if not scenarios:
            return self._finalize_response(
                conversation_id,
                "这批场景已经失效，请重新描述要算的几个方案。",
                "change_expired",
                memory.activeView,
                [],
            )
        first = scenarios[0]
        try:
            request = PlanningRequest(**first["request"])
        except (KeyError, TypeError, ValueError):
            return self._finalize_response(
                conversation_id,
                "这批场景已经失效，请重新描述要算的几个方案。",
                "change_expired",
                memory.activeView,
                [],
            )
        pending_batch = {
            "remaining": scenarios[1:],
            "total": len(scenarios),
            "completed": [],
            "compare": bool(batch.get("compare")),
            "sourcePrompt": batch.get("sourcePrompt") or "",
            "baseVersionId": batch.get("baseVersionId"),
        }
        return self._start_planning_solve(
            conversation_id=conversation_id,
            content=batch.get("sourcePrompt") or "",
            request=request,
            memory=memory,
            pending_batch=pending_batch,
        )

    def _continue_batch(self, conversation_id: str, job_id: str, version_id: str | None) -> None:
        """一个场景算完后，接着算下一个；全部算完则自动对比。"""
        memory = self.memories.get_or_create(conversation_id)
        batch = (memory.activeFilters or {}).get("pendingBatch")
        if not isinstance(batch, dict):
            return
        completed = list(batch.get("completed") or [])
        if version_id:
            completed.append(version_id)
        remaining = list(batch.get("remaining") or [])
        filters = dict(memory.activeFilters)
        if not remaining:
            filters.pop("pendingBatch", None)
            self.memories.apply_patch(memory, {"activeFilters": filters})
            if batch.get("compare"):
                self._emit_batch_compare(conversation_id, completed)
            return
        nxt = remaining[0]
        filters["pendingBatch"] = {
            **batch,
            "remaining": remaining[1:],
            "completed": completed,
        }
        self.memories.apply_patch(memory, {"activeFilters": filters})
        try:
            request = PlanningRequest(**nxt["request"])
        except (KeyError, TypeError, ValueError):
            self._continue_batch(conversation_id, job_id, None)
            return
        self._start_planning_solve(
            conversation_id=conversation_id,
            content=batch.get("sourcePrompt") or "",
            request=request,
            memory=self.memories.get_or_create(conversation_id),
            pending_batch={
                **batch,
                "remaining": remaining[1:],
                "completed": completed,
            },
        )

    def batch_progress(self, conversation_id: str) -> dict[str, int] | None:
        """当前会话还有没有没算完的场景。前端靠它决定要不要继续轮询。"""
        memory = self.memories.get_or_create(conversation_id)
        batch = (memory.activeFilters or {}).get("pendingBatch")
        if not isinstance(batch, dict):
            return None
        total = int(batch.get("total") or 0)
        if total <= 0:
            return None
        done = len(batch.get("completed") or [])
        return {"position": min(done + 1, total), "total": total}

    def _emit_batch_compare(self, conversation_id: str, version_ids: list[str]) -> None:
        """两个以上场景都算完后，用现成的比较器直接出对比，不再额外问一轮模型。"""
        pairs: list[tuple] = []
        unique: list[str] = []
        for version_id in version_ids:
            if version_id not in unique:
                unique.append(version_id)
        for index in range(len(unique) - 1):
            left = self.versions.get(unique[index])
            right = self.versions.get(unique[index + 1])
            if left and right:
                pairs.append((left, right))
        if not pairs:
            return
        toolbox = self._build_toolbox(conversation_id, None, None)
        blocks = []
        for left, right in pairs:
            blocks.append(render_compare_summary(toolbox.compare_pair(left, right)))
        memory = self.memories.get_or_create(conversation_id)
        self._finalize_response(
            conversation_id,
            f"这两个场景都算完了，下面是它们的对比：\n\n" + "\n\n".join(blocks),
            "batch_compare",
            memory.activeView,
            [item for pair in pairs for item in pair],
        )

    _DSL_LABELS = {
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

    @classmethod
    def _format_dsl_preview(cls, dsl: dict[str, Any]) -> str:
        lines = ["已基于当前配置准备自定义约束/目标，尚未求解。", "变更预览："]
        index = 1
        for spec in dsl.get("constraints") or []:
            label = cls._DSL_LABELS.get(spec.get("type"), str(spec))
            try:
                text = label.format(**spec)
            except (KeyError, IndexError):
                text = json.dumps(spec, ensure_ascii=False)
            lines.append(f"{index}. {text}")
            index += 1
        for spec in dsl.get("objectives") or []:
            if spec.get("type") == "lexmin":
                stages = " → ".join(
                    f"{'最小化' if s.get('dir') == 'minimize' else '最大化'}{s.get('q')}"
                    for s in spec.get("stages", [])
                )
                lines.append(f"{index}. 自定义分层目标：{stages}")
                index += 1
            else:
                lines.append(f"{index}. 自定义目标：{json.dumps(spec, ensure_ascii=False)}")
                index += 1
        lines.extend([
            "这次会生成独立沙盘版本，不覆盖现有方案。",
            "回复“确认生成沙盘”执行；回复“取消”放弃本次变更。",
        ])
        return "\n".join(lines)

    def _try_dsl_parse(self, content: str):
        """DSL 三层解析（增量能力）。解析不了/LLM 不可用返回 None，回退现有路径。

        规则层与门控是确定性的，不产生 API 调用；只有真正的新话术才走到 LLM。
        """
        try:
            from app.core.dsl.llm_parser import ParseUnavailable, parse as dsl_parse
        except Exception:  # pragma: no cover - DSL 包缺失时静默回退
            return None
        try:
            return dsl_parse(content)
        except (ParseUnavailable, Exception):  # noqa: BLE001 - 解析失败回退现有路径
            return None

    def _change_plan_from_dsl(self, content: str, current, base_version_id):
        """统一解析器 → 变更计划（第三期）。DSL 表达不了或没有变化时返回 None 走旧路径。"""
        parsed = self._try_dsl_parse(content)
        if parsed is None or parsed.route != "dsl" or not parsed.dsl:
            return None
        try:
            from app.core.dsl.planning_bridge import dsl_to_request
            from app.core.model_change import ModelChangePlan, _diff_requests, _format_preview

            updated, residual, timeline = dsl_to_request(parsed.dsl, current)
        except Exception:  # pragma: no cover - 桥接失败回退旧路径
            return None
        operations = _diff_requests(current, updated)
        if not operations and not residual and not timeline:
            return None
        preview = (
            self._format_dsl_preview(parsed.dsl)
            if (residual or timeline)
            else _format_preview(operations, base_version_id)
        )
        return ModelChangePlan(
            request=updated,
            operations=operations,
            preview=preview,
            source_prompt=content,
            base_version_id=base_version_id,
            notes=[],
            errors=[],
        )

    def _start_dsl_solve(
        self,
        *,
        conversation_id: str,
        dsl: dict[str, Any],
        content: str,
        memory,
    ) -> AgentResponse:
        """DSL 变更确认后的求解：默认配置 + 注入 DSL 约束/目标。"""
        latest = None
        if memory.latestResultId:
            latest = self.versions.get(memory.latestResultId)
        request = self._planning_request_from_context(memory, latest)
        return self._start_planning_solve(
            conversation_id=conversation_id,
            content=content,
            request=request,
            memory=memory,
            dsl=dsl,
        )

    def _start_planning_solve(
        self,
        *,
        conversation_id: str,
        content: str,
        request: PlanningRequest,
        memory,
        pending_batch: dict[str, Any] | None = None,
        dsl: dict[str, Any] | None = None,
    ) -> AgentResponse:
        if not self.jobs:
            return self._finalize_response(
                conversation_id,
                "求解服务未配置，这次无法发起真实求解；没有生成任何方案。",
                "solve_unavailable",
                memory.activeView,
                [],
            )
        try:
            request, data_snapshot = self.product_executor.prepare(request)
        except MaintenanceProductExecutionError as exc:
            # 数据改动没通过校验时，如实说明，不把它说成无解。
            return self._finalize_response(
                conversation_id,
                f"这次没有开始求解，因为数据改动没有通过校验：{exc}",
                "change_rejected",
                memory.activeView,
                [],
            )
        request_payload = request.to_payload()
        history = self.messages.list_by_conversation(conversation_id)
        artifact = DecisionCompiler().compile(
            {
                "prompt": content,
                "history": [item.model_dump() for item in history],
                "working_memory": memory.model_dump(),
                "business_state": data_snapshot,
                "schema_version": "maintenance-plan-v1",
                "compiler_version": "deterministic-intent-v1",
                "model_version": "registered-language",
                "prompt_version": "none",
                "solver_profile": {"name": "ortools-cp-sat", "random_seed": 17, "workers": 1, "max_seconds": request.seconds},
            },
            {"intent": "maintenance_plan", "request": request_payload, "dsl": dsl or {}},
            {"skill": SKILL_NAME, "request": request_payload, "data_snapshot": data_snapshot, "dsl": dsl or {}},
        )
        # job.request 保持 request 字段在顶层（兼容现有断言），DSL 附加在其上
        job_payload = request_payload if dsl is None else {**request_payload, "kind": "dsl", "dsl": dsl}
        job = self.jobs.create(conversation_id, job_payload, artifact.model_fingerprint, snapshot=data_snapshot)
        filters = dict(memory.activeFilters)
        filters["planningRequest"] = request_payload
        self.memories.apply_patch(memory, {
            "currentIntent": "maintenance_plan",
            "activeFocusTag": request.strategy,
            "activeFilters": filters,
        })

        if pending_batch is not None:
            latest = self.memories.get_or_create(conversation_id)
            batch_filters = dict(latest.activeFilters)
            batch_filters["pendingBatch"] = pending_batch
            self.memories.apply_patch(latest, {"activeFilters": batch_filters})

        if pending_batch is not None:
            position = len(pending_batch.get("completed") or []) + 1
            total = pending_batch.get("total") or 1
            batch_message = (
                f"这是第 {position}/{total} 个场景；"
                f"全部算完后我会把 {total} 个场景放在一起对比。"
                if total > 1
                else ""
            )
        else:
            batch_message = ""
        response = self._finalize_response(
            conversation_id,
            (
                f"已按“{request.strategy}”编译为固定模型契约，"
                f"{'附带自定义约束/目标，' if dsl else ''}真实 CP-SAT 求解已启动。"
                f"{batch_message}任务 {job.id}，最长求解 {request.seconds} 秒；"
                "超过 3 分钟我会询问是否继续，不会把超时说成无解。"
            ),
            "solve_started",
            "P01",
            [],
            decision_artifact=DecisionArtifactPayload(
                canonicalContext=artifact.canonical_context,
                canonicalDecision=artifact.canonical_decision,
                canonicalModel=artifact.canonical_model,
                contextFingerprint=artifact.context_fingerprint,
                decisionFingerprint=artifact.decision_fingerprint,
                modelFingerprint=artifact.model_fingerprint,
            ),
            solve_evidence=SolveEvidencePayload(
                status="UNKNOWN",
                running=True,
                phase="queued",
                modelFingerprint=artifact.model_fingerprint,
            ),
            solve_job=SolveJobPayload(**job.model_dump()),
        )
        self._futures[job.id] = self.job_pool.submit(
            self._execute_job,
            job.id,
            content,
            request,
            artifact,
            dsl,
        )
        return response

    def _resolve_version(
        self,
        conversation_id: str,
        selected_version_id: str | None,
        memory,
        content: str = "",
    ):
        conversation_versions = self.versions.list_by_conversation(conversation_id)
        base_version = None
        if selected_version_id:
            version = self.versions.get(selected_version_id)
            if version and version.conversationId == conversation_id:
                base_version = version
        latest_id = memory.latestResultId
        if base_version is None and latest_id:
            version = self.versions.get(latest_id)
            if version and version.conversationId == conversation_id:
                base_version = version
        if base_version is None and conversation_versions:
            base_version = conversation_versions[0]
        explicit, reference_kind = resolve_version_reference(
            content,
            conversation_versions,
            selected_version=base_version,
        )
        if explicit:
            return explicit, reference_kind
        if reference_kind in {"id", "ordinal", "previous"}:
            return None, reference_kind
        return base_version, reference_kind

    @staticmethod
    def _planning_request_from_context(memory, version) -> PlanningRequest:
        if version and isinstance(version.parameterSnapshot, dict):
            known = {
                key: value
                for key, value in version.parameterSnapshot.items()
                if key in PlanningRequest.__dataclass_fields__
            }
            if known:
                return PlanningRequest(**known)
        payload = (memory.activeFilters or {}).get("planningRequest")
        if isinstance(payload, dict):
            known = {
                key: value
                for key, value in payload.items()
                if key in PlanningRequest.__dataclass_fields__
            }
            return PlanningRequest(**known)
        return PlanningRequest()

    def _answer_capability_question(
        self,
        conversation_id: str,
        content: str,
        memory,
    ) -> AgentResponse:
        """能力问句：有哪些目标、权重、策略可以直接答，不依赖任何方案。"""
        memory = self.memories.apply_patch(
            memory,
            {"currentGoal": content, "currentIntent": "capability"},
        )
        return self._finalize_response(
            conversation_id,
            render_capability_answer(),
            "capability_answer",
            memory.activeView,
            [],
        )

    def _answer_environment_question(
        self,
        conversation_id: str,
        content: str,
        memory,
        version,
    ) -> AgentResponse:
        """风况/电价属确定性数据：直接取基座逐时表，不经过模型转抄。"""
        toolbox = self._build_toolbox(conversation_id, version, None)
        window = self._environment_window(content)
        arguments: dict[str, Any] = (
            {"detail": "window", "window": window}
            if window
            else {
                "days": 30,
                "detail": "chart",
                "defaultRange": self._environment_chart_range(content),
            }
        )
        result = toolbox.execute("read_environment", arguments)
        markdown = (result.get("data") or {}).get("markdown")
        if result.get("status") != "ok" or not markdown:
            reply = (
                (result.get("data") or {}).get("message")
                or "这次没有取到风况或电价序列，不能给出具体数值。"
            )
            action = "environment_unavailable"
        else:
            reply = markdown
            action = "environment_answer"
        memory = self.memories.apply_patch(
            memory,
            {"currentGoal": content, "currentIntent": "environment"},
        )
        return self._finalize_response(
            conversation_id,
            reply,
            action,
            memory.activeView,
            [],
        )

    def _answer_operations_question(
        self,
        conversation_id: str,
        content: str,
        kind: str,
        scope: str | None,
        selected_version,
        memory,
        sink: StreamSink | None = None,
    ) -> AgentResponse:
        """Pure site-data lookups go straight to the read tool.

        This keeps “现在有哪些告警 / 库存还有多少 / 下一个窗口” from depending
        on whether the model remembers that the domain exists.
        """
        toolbox = self._build_toolbox(conversation_id, selected_version, sink)
        if kind == "alarms":
            result = toolbox.execute("read_alarms", alarm_arguments(content))
            action = "alarm_answer"
        else:
            arguments: dict[str, Any] = {"scope": scope or "overview"}
            if scope == "parameters":
                arguments["query"] = content
            result = toolbox.execute("read_site", arguments)
            action = "site_answer"
        markdown = (result.get("data") or {}).get("markdown")
        if result.get("status") != "ok" or not markdown:
            reply = (
                (result.get("data") or {}).get("message")
                or "这次没有取到现场数据，不能给出具体清单或数值。"
            )
            action = f"{action}_unavailable"
        else:
            reply = markdown
        memory = self.memories.apply_patch(
            memory,
            {"currentGoal": content, "currentIntent": action},
        )
        return self._finalize_response(
            conversation_id,
            reply,
            action,
            memory.activeView,
            [],
        )

    @staticmethod
    def _environment_days(content: str) -> int:
        text = "".join(content.split())
        if any(token in text for token in ("一个月", "30天", "四周", "4周")):
            return 30
        if any(token in text for token in ("两周", "14天", "半个月")):
            return 14
        if any(token in text for token in ("三天", "3天")):
            return 3
        match = re.search(r"(\d+)\s*天", text)
        if match:
            return max(1, min(int(match.group(1)), 31))
        return 7

    @classmethod
    def _environment_chart_range(cls, content: str) -> int:
        requested = cls._environment_days(content)
        return min((3, 7, 14, 30), key=lambda value: (abs(value - requested), value))

    @staticmethod
    def _environment_window(content: str) -> str | None:
        text = "".join(content.split())
        match = re.search(r"窗口\s*0*(\d+)", text)
        if match:
            return f"窗口{int(match.group(1)):02d}"
        if any(token in text for token in ("目标窗口", "本次窗口", "这个窗口")):
            return "target"
        return None

    def _answer_structure_question(
        self,
        conversation_id: str,
        content: str,
        memory,
    ) -> AgentResponse:
        """结构问句：还没有方案时，讲模板结构，不假装读过某次求解。"""
        memory = self.memories.apply_patch(
            memory,
            {"currentGoal": content, "currentIntent": "structure"},
        )
        return self._finalize_response(
            conversation_id,
            render_structure_answer(),
            "structure_answer",
            memory.activeView,
            [],
        )

    def _answer_model_question(
        self,
        conversation_id: str,
        content: str,
        question_kind: str,
        version,
        memory,
    ) -> AgentResponse:
        artifact = self.get_model_artifact(version.id)
        patch: dict[str, Any] = {
            "latestResultId": version.id,
            "selectedVersionIds": [version.id],
        }
        if question_kind in {"formula", "parameter"} and not artifact:
            reply = (
                f"{render_missing_artifact(version)}\n"
                "我已按该版本的参数准备好一次可审计重跑；确认后会生成新版本并保存模型产物。"
            )
            filters = dict(memory.activeFilters)
            filters["pendingChange"] = {
                "baseVersionId": version.id,
                "request": self._planning_request_from_context(memory, version).to_payload(),
                "operations": [{
                    "kind": "artifact_rebuild",
                    "key": "模型产物",
                    "before": "缺失",
                    "after": "重新运行并保存完整模型产物",
                }],
                "preview": (
                    f"将按方案 {version.id} 的原始参数重新运行一次，"
                    "生成独立的新版本并保存 ModelArtifact；不覆盖旧版本。"
                ),
                "sourcePrompt": f"重新运行方案 {version.id} 以保存数学模型产物",
            }
            patch["activeFilters"] = filters
        elif question_kind == "formula":
            reply = render_model_formula(version, artifact or {})
        elif question_kind == "parameter":
            reply = render_parameter_answer(version, artifact or {}, content)
        elif question_kind == "status":
            reply = render_status_answer(version)
        else:
            reply = render_solution_answer(version, content)
        self.memories.apply_patch(memory, patch)
        return self._finalize_response(
            conversation_id,
            reply,
            f"model_{question_kind}",
            memory.activeView,
            [],
        )

    def _build_toolbox(
        self,
        conversation_id: str,
        selected_version,
        sink: StreamSink | None,
    ) -> AgentToolbox:
        def on_tool(name: str, _result: dict[str, Any]) -> None:
            if sink:
                labels = {
                    "read_versions": "已读取方案事实",
                    "read_environment": "已读取风况与电价",
                    "read_catalog": "已读取目标目录",
                    "read_alarms": "已读取告警清单",
                    "read_site": "已读取现场数据",
                    "solve": "已准备变更预览",
                }
                sink.status(labels.get(name, f"已执行 {name}"))

        return AgentToolbox(
            conversation_id=conversation_id,
            versions=self.versions,
            model_artifacts=self.model_artifacts,
            selected_version_id=selected_version.id if selected_version else None,
            on_tool=on_tool,
            on_tool_trace=self._record_tool_call,
        )

    def _answer_with_conversation_agent(
        self,
        *,
        conversation_id: str,
        content: str,
        history,
        memory,
        selected_version,
        sink: StreamSink | None,
    ) -> str:
        toolbox = self._build_toolbox(conversation_id, selected_version, sink)
        reply = self.conversation_agent.answer(
            message=content,
            history=history,
            memory=memory,
            toolbox=toolbox,
            selected_version=selected_version,
            on_token=sink.token if sink else None,
            on_status=sink.status if sink else None,
        )
        if toolbox.pending_change:
            latest = self.memories.get_or_create(conversation_id)
            filters = dict(latest.activeFilters)
            filters["pendingChange"] = toolbox.pending_change
            self.memories.apply_patch(
                latest,
                {
                    "activeFilters": filters,
                    "activeFocusTag": toolbox.pending_change["request"]["strategy"],
                },
            )
        return reply

    def _fallback_compare_reply(
        self,
        conversation_id: str,
        content: str,
        selected_version,
    ) -> str | None:
        if not any(token in content for token in ("对比", "比较", "差异", "区别")):
            return None
        ordinals = re.findall(r"方案\s*0*(\d{1,3})", content)
        if len(ordinals) < 2:
            return None
        toolbox = self._build_toolbox(conversation_id, selected_version, None)
        resolved = []
        for ordinal in ordinals[:2]:
            version = toolbox.resolve_version(f"方案{ordinal}")
            if version and version.id not in {item.id for item in resolved}:
                resolved.append(version)
        if len(resolved) < 2:
            return None
        return render_compare_summary(toolbox.compare_pair(resolved[0], resolved[1]))

    def _answer_agent_question(
        self,
        conversation_id: str,
        content: str,
        question_kind: str,
        version,
        memory,
        sink: StreamSink | None = None,
    ) -> AgentResponse:
        artifact = self.get_model_artifact(version.id)
        if question_kind in {"formula", "parameter"} and not artifact:
            return self._answer_model_question(
                conversation_id,
                content,
                question_kind,
                version,
                memory,
            )
        history = self.messages.list_by_conversation(conversation_id)
        try:
            reply = self._answer_with_conversation_agent(
                conversation_id=conversation_id,
                content=content,
                history=history,
                memory=memory,
                selected_version=version,
                sink=sink,
            )
        except ConversationAgentError:
            return self._answer_model_question(
                conversation_id,
                content,
                question_kind,
                version,
                memory,
            )
        memory = self.memories.get_or_create(conversation_id)
        self.memories.apply_patch(
            memory,
            {
                "currentGoal": content,
                "currentIntent": f"model_{question_kind}",
                "latestResultId": version.id,
                "selectedVersionIds": [version.id],
            },
        )
        return self._finalize_response(
            conversation_id,
            reply,
            f"model_{question_kind}",
            memory.activeView,
            [],
        )

    def get_job(self, job_id: str):
        if not self.jobs:
            return None
        return self.jobs.get(job_id)

    def get_latest_job(self, conversation_id: str):
        if not self.jobs:
            return None
        return self.jobs.latest_for_conversation(conversation_id)

    def control_job(self, job_id: str, action: str):
        if not self.jobs:
            raise ValueError("Solve jobs are not configured")
        job = self.jobs.get(job_id)
        if not job:
            raise ValueError("Solve job not found")
        if action == "background":
            return self.jobs.set_backgrounded(job_id, True)
        if action == "wait":
            return self.jobs.defer_decision(job_id)
        if action == "stop":
            self.product_executor.cancel(job_id)
            future = self._futures.get(job_id)
            if future:
                future.cancel()
            return self.jobs.mark_cancelled(job_id)
        if action == "use_incumbent" and not job.hasIncumbent:
            raise ValueError("当前尚无可用方案")
        if action == "use_incumbent":
            return job
        raise ValueError("Unknown solve action")

    def _execute_job(
        self,
        job_id: str,
        content: str,
        request: PlanningRequest,
        artifact,
        dsl: dict[str, Any] | None = None,
    ) -> None:
        assert self.jobs is not None
        produced_version_id: str | None = None
        try:
            self.jobs.mark_running(job_id)
            raw_result = self.product_executor.execute(request, job_id, artifact.model_fingerprint, dsl=dsl)
            current = self.jobs.get(job_id)
            if not current or current.status == "CANCELLED":
                return
            self.jobs.mark_reviewing(job_id)
            status, has_incumbent = solver_status(raw_result)
            if has_incumbent:
                version = self.normalizer.build_version_from_product_result(
                    conversation_id=current.conversationId,
                    user_prompt=content,
                    focus_tag=request.strategy,
                    parameter_snapshot=(
                        {**request.to_payload(), "dsl": dsl}
                        if dsl
                        else request.to_payload()
                    ),
                    payload=raw_result,
                    solve_job_id=job_id,
                    model_fingerprint=artifact.model_fingerprint,
                )
                self.versions.add(version)
                produced_version_id = version.id
                if self.model_artifacts and isinstance(raw_result.get("模型产物"), dict):
                    self.model_artifacts.add(
                        version.id,
                        version.conversationId,
                        version.generatedAt,
                        raw_result["模型产物"],
                    )
                self.candidates.replace_for_version(current.conversationId, version.id, version.windows)
                memory = self.memories.get_or_create(current.conversationId)
                self.memories.apply_patch(memory, {
                    "latestResultId": version.id,
                    "selectedVersionIds": [version.id],
                    "activeView": "P01",
                })
                summary = version.resultSummary
                reply = self._format_product_reply(status, summary, version)
                self._finalize_response(
                    current.conversationId,
                    reply,
                    "solve_completed",
                    "P01",
                    [version],
                    solve_evidence=self._product_solve_evidence(raw_result, artifact.model_fingerprint, status),
                    solve_job_id=job_id,
                )
                artifact_payload = DecisionArtifactPayload(
                    canonicalContext=artifact.canonical_context,
                    canonicalDecision=artifact.canonical_decision,
                    canonicalModel=artifact.canonical_model,
                    contextFingerprint=artifact.context_fingerprint,
                    decisionFingerprint=artifact.decision_fingerprint,
                    modelFingerprint=artifact.model_fingerprint,
                )
                stored_result = {
                    "versionId": version.id,
                    "summary": summary,
                    "decisionArtifact": artifact_payload.model_dump(),
                }
            else:
                reason = "求解达到时限，尚未找到可用方案，但未证明无解。" if status == "UNKNOWN" else "模型已由求解器证明不可行。"
                self._finalize_response(
                    current.conversationId,
                    reason,
                    "solve_completed",
                    "P01",
                    [],
                    solve_evidence=self._product_solve_evidence(raw_result, artifact.model_fingerprint, status),
                    solve_job_id=job_id,
                )
                stored_result = {
                    "summary": reason,
                    "decisionArtifact": DecisionArtifactPayload(
                        canonicalContext=artifact.canonical_context,
                        canonicalDecision=artifact.canonical_decision,
                        canonicalModel=artifact.canonical_model,
                        contextFingerprint=artifact.context_fingerprint,
                        decisionFingerprint=artifact.decision_fingerprint,
                        modelFingerprint=artifact.model_fingerprint,
                    ).model_dump(),
                }
            self.jobs.mark_completed(job_id, stored_result, status, has_incumbent=has_incumbent)
            current = self.jobs.get(job_id)
            if current:
                self._continue_batch(current.conversationId, job_id, produced_version_id)
        except Exception as exc:
            current = self.jobs.get(job_id)
            if current and current.status != "CANCELLED":
                message = f"求解任务执行失败：{exc}。这不是无解，当前状态记为 UNKNOWN。"
                self._finalize_response(current.conversationId, message, "solve_failed", "P01", [])
                self.jobs.mark_failed(job_id, str(exc))
                self._continue_batch(current.conversationId, job_id, None)

    @staticmethod
    def _format_product_reply(status: str, summary: dict, version) -> str:
        total_cost = float(summary.get("totalCost") or 0)
        completed = summary.get("scheduledTaskCount", 0)
        total = summary.get("totalTaskCount", 0)
        windows = "、".join(
            f"{item.windowId}（{item.metrics.startTime} 至 {item.metrics.endTime}）"
            for item in version.windows
        )
        proof = "已证明最优" if status == "OPTIMAL" else "已找到并复核可行方案，尚未证明最优"
        savings = summary.get("baselineSavings", {})
        savings_text = "；".join(
            f"较{name}节省 {value / 10000:.2f} 万元"
            for name, value in savings.items()
            if value is not None and value >= 0
        )
        return (
            f"排程完成：{proof}。\n"
            f"建议窗口：{windows or '暂无'}\n"
            f"完成任务：{completed}/{total}；总成本：{total_cost / 10000:.2f} 万元"
            + (f"；{savings_text}" if savings_text else "")
            + "。点击下方方案卡可查看完整排程和审计证据。"
        )

    @staticmethod
    def _product_solve_evidence(raw_result: dict, model_fingerprint: str, status: str) -> SolveEvidencePayload:
        solving = raw_result.get("求解", {})
        stages = solving.get("阶段", [])
        last = stages[-1] if stages else {}
        return SolveEvidencePayload(
            status=status,
            hasIncumbent=bool(solving.get("有解")),
            proof=status in {"OPTIMAL", "INFEASIBLE"},
            elapsedSeconds=float(solving.get("总耗时秒", 0)),
            phase="solve",
            reason=None if status != "UNKNOWN" else "time_limit_or_search_incomplete",
            running=False,
            objectiveValue=last.get("目标值") or solving.get("模型成本"),
            bestBound=last.get("界"),
            modelFingerprint=model_fingerprint,
        )

    def handle_message(self, conversation_id: str, content: str) -> AgentResponse:
        """单一入口：与 submit_message 走同一条编排，不再有第二套 skill 链路。"""
        return self._submit_message(conversation_id, content)

    def get_dashboard(
        self,
        conversation_id: str,
        sort_mode: str = "latest_first",
    ) -> DashboardResponse:
        versions = self.versions.list_by_conversation(conversation_id)
        candidates = self.candidates.list_by_conversation(conversation_id)
        included_versions = {
            item.id for item in versions if item.includedInCandidatePool
        }
        visible_candidates = [
            item
            for item in candidates
            if item.versionId in included_versions and not item.isDeleted
        ]
        sorted_candidates = self.version_manager.sort_candidates(
            visible_candidates,
            sort_mode,
        )
        recommendations = self.version_manager.compute_top_recommendations(
            sorted_candidates,
            versions,
            sort_mode,
        )
        return DashboardResponse(
            conversationId=conversation_id,
            sortMode=sort_mode,
            versions=versions,
            candidateWindowPool=sorted_candidates,
            topRecommendations=recommendations,
        )

    def include_version(self, version_id: str, include: bool) -> None:
        version = self.versions.get(version_id)
        if not version:
            raise ValueError("Version not found")
        version = version.model_copy(update={"includedInCandidatePool": include})
        self.versions.update(version)

    def get_model_artifact(self, version_id: str) -> dict | None:
        if not self.model_artifacts:
            return None
        return self.model_artifacts.get(version_id)

    def get_trace(self, request_id: str) -> AgentTrace | None:
        if not self.traces:
            return None
        return self.traces.get(request_id)

    def list_traces(self, conversation_id: str, limit: int = 50) -> list[AgentTrace]:
        if not self.traces:
            return []
        return self.traces.list_by_conversation(conversation_id, limit)

    def delete_version(self, version_id: str) -> None:
        self.versions.delete(version_id)
        self.candidates.mark_deleted_by_version(version_id)

    def delete_candidate(self, candidate_id: str) -> None:
        self.candidates.delete(candidate_id)

    def reorder_candidates(self, items: list[tuple[str, int]]) -> None:
        self.candidates.reorder(items)

    def _finalize_response(
        self,
        conversation_id: str,
        content: str,
        action_type: str,
        recommended_view: str,
        new_versions,
        memory_patch: dict | None = None,
        decision_artifact: DecisionArtifactPayload | None = None,
        solve_evidence: SolveEvidencePayload | None = None,
        solve_job: SolveJobPayload | None = None,
        solve_job_id: str | None = None,
    ) -> AgentResponse:
        now = datetime.now(SHANGHAI_TZ).isoformat()
        trace_id = self._finish_trace(conversation_id, action_type, solve_evidence)
        assistant_message = Message(
            id=f"msg_{uuid4().hex[:8]}",
            conversationId=conversation_id,
            role="assistant",
            content=content,
            createdAt=now,
            versionIds=[item.id for item in new_versions],
            jobId=solve_job.id if solve_job else solve_job_id,
        )
        self.messages.add(assistant_message)
        self._update_summary(conversation_id)

        conversation = self.conversations.get(conversation_id)
        if conversation:
            self.conversations.update(
                conversation.model_copy(
                    update={
                        "updatedAt": now,
                        "lastMessagePreview": content,
                        "activeView": recommended_view,
                    }
                )
            )

        dashboard = self.get_dashboard(conversation_id)
        return AgentResponse(
            assistantMessage=AssistantMessagePayload(
                id=assistant_message.id,
                content=assistant_message.content,
                versionIds=assistant_message.versionIds,
            ),
            actionType=action_type,
            recommendedView=recommended_view,
            memoryPatch=memory_patch or {},
            newVersions=new_versions,
            candidateWindowPool=dashboard.candidateWindowPool,
            topRecommendations=dashboard.topRecommendations,
            dashboardState={"sortMode": dashboard.sortMode},
            decisionArtifact=decision_artifact,
            solveEvidence=solve_evidence,
            solveJob=solve_job,
            traceId=trace_id,
        )
