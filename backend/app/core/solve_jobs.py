from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.db.database import Database, dumps, loads


JobStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
SolverStatus = Literal["OPTIMAL", "FEASIBLE", "UNKNOWN", "INFEASIBLE"]


class SolveJob(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    conversationId: str = Field(validation_alias="conversation_id")
    status: JobStatus
    solverStatus: SolverStatus = "UNKNOWN"
    submittedAt: str
    startedAt: str | None = None
    finishedAt: str | None = None
    elapsedSeconds: int = 0
    needsUserDecision: bool = False
    nextDecisionAt: int = 180
    backgrounded: bool = False
    hasIncumbent: bool = False
    modelFingerprint: str = ""
    phase: str = "queued"
    progressPercent: int = 5
    statusMessage: str = "正在锁定数据快照"
    snapshot: dict[str, Any] | None = None
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None


class SolveJobRepository:
    def __init__(self, database: Database, soft_limit_seconds: int = 180) -> None:
        self.database = database
        self.soft_limit_seconds = soft_limit_seconds

    def create(
        self,
        conversation_id: str,
        request_payload: dict[str, Any],
        model_fingerprint: str,
        snapshot: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> SolveJob:
        submitted = now or datetime.now(timezone.utc)
        job = SolveJob(
            id=f"job_{uuid4().hex[:12]}",
            conversationId=conversation_id,
            status="QUEUED",
            submittedAt=submitted.isoformat(),
            modelFingerprint=model_fingerprint,
            snapshot=snapshot,
            request=request_payload,
        )
        self._save(job)
        return job

    def get(self, job_id: str, now: datetime | None = None) -> SolveJob | None:
        row = self.database.fetch_one("SELECT payload FROM solve_jobs WHERE id = ?", (job_id,))
        if not row:
            return None
        job = SolveJob(**loads(row["payload"]))
        return self._with_elapsed(job, now or datetime.now(timezone.utc))

    def list_by_conversation(self, conversation_id: str) -> list[SolveJob]:
        rows = self.database.fetch_all(
            "SELECT payload FROM solve_jobs WHERE conversation_id = ? ORDER BY submitted_at DESC",
            (conversation_id,),
        )
        now = datetime.now(timezone.utc)
        return [self._with_elapsed(SolveJob(**loads(row["payload"])), now) for row in rows]

    def latest_for_conversation(self, conversation_id: str) -> SolveJob | None:
        row = self.database.fetch_one(
            "SELECT payload FROM solve_jobs WHERE conversation_id = ? ORDER BY submitted_at DESC LIMIT 1",
            (conversation_id,),
        )
        if not row:
            return None
        return self._with_elapsed(SolveJob(**loads(row["payload"])), datetime.now(timezone.utc))

    def recover_interrupted_jobs(self) -> int:
        rows = self.database.fetch_all("SELECT payload FROM solve_jobs")
        recovered = 0
        for row in rows:
            job = SolveJob(**loads(row["payload"]))
            if job.status not in {"QUEUED", "RUNNING"}:
                continue
            self.mark_failed(job.id, "服务重启导致本次求解中断，请重新发起；这不代表模型无解。")
            recovered += 1
        return recovered

    def mark_running(self, job_id: str, now: datetime | None = None) -> SolveJob:
        job = self._require(job_id)
        updated = job.model_copy(
            update={"status": "RUNNING", "startedAt": (now or datetime.now(timezone.utc)).isoformat(),
                    "phase": "solving", "progressPercent": 35, "statusMessage": "正在构建并求解单窗口模型"}
        )
        self._save(updated)
        return updated

    def mark_completed(
        self,
        job_id: str,
        result: dict[str, Any],
        solver_status: SolverStatus,
        has_incumbent: bool | None = None,
        now: datetime | None = None,
    ) -> SolveJob:
        job = self._require(job_id)
        finished = now or datetime.now(timezone.utc)
        elapsed = self._elapsed(job, finished)
        updated = job.model_copy(
            update={
                "status": "COMPLETED",
                "solverStatus": solver_status,
                "hasIncumbent": solver_status in {"OPTIMAL", "FEASIBLE"} if has_incumbent is None else has_incumbent,
                "finishedAt": finished.isoformat(),
                "elapsedSeconds": elapsed,
                "needsUserDecision": False,
                "result": result,
                "phase": "completed",
                "progressPercent": 100,
                "statusMessage": "排程与独立复核已完成",
            }
        )
        self._save(updated)
        return updated

    def mark_failed(self, job_id: str, error: str, now: datetime | None = None) -> SolveJob:
        job = self._require(job_id)
        finished = now or datetime.now(timezone.utc)
        updated = job.model_copy(
            update={
                "status": "FAILED",
                "solverStatus": "UNKNOWN",
                "finishedAt": finished.isoformat(),
                "elapsedSeconds": self._elapsed(job, finished),
                "needsUserDecision": False,
                "error": error,
                "phase": "failed",
                "statusMessage": "执行失败，未将其标记为无解",
            }
        )
        self._save(updated)
        return updated

    def set_backgrounded(self, job_id: str, backgrounded: bool = True) -> SolveJob:
        job = self._require(job_id)
        updated = job.model_copy(update={"backgrounded": backgrounded, "needsUserDecision": False})
        self._save(updated)
        return updated

    def mark_reviewing(self, job_id: str) -> SolveJob:
        job = self._require(job_id)
        updated = job.model_copy(update={"phase": "reviewing", "progressPercent": 85, "statusMessage": "正在独立复核费用与约束"})
        self._save(updated)
        return updated

    def defer_decision(self, job_id: str, seconds: int | None = None, now: datetime | None = None) -> SolveJob:
        job = self.get(job_id, now=now)
        if not job:
            raise ValueError("Solve job not found")
        delay = seconds or self.soft_limit_seconds
        updated = job.model_copy(
            update={
                "backgrounded": False,
                "needsUserDecision": False,
                "nextDecisionAt": job.elapsedSeconds + delay,
            }
        )
        self._save(updated)
        return updated

    def mark_cancelled(self, job_id: str) -> SolveJob:
        job = self._require(job_id)
        now = datetime.now(timezone.utc)
        updated = job.model_copy(
            update={
                "status": "CANCELLED",
                "solverStatus": "UNKNOWN",
                "finishedAt": now.isoformat(),
                "elapsedSeconds": self._elapsed(job, now),
                "needsUserDecision": False,
            }
        )
        self._save(updated)
        return updated

    def _require(self, job_id: str) -> SolveJob:
        job = self.get(job_id)
        if not job:
            raise ValueError("Solve job not found")
        return job

    def _save(self, job: SolveJob) -> None:
        self.database.execute(
            """
            INSERT INTO solve_jobs (id, conversation_id, submitted_at, payload)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
            """,
            (job.id, job.conversationId, job.submittedAt, dumps(job.model_dump())),
        )

    def _with_elapsed(self, job: SolveJob, now: datetime) -> SolveJob:
        if job.status != "RUNNING":
            return job
        elapsed = self._elapsed(job, now)
        return job.model_copy(
            update={
                "elapsedSeconds": elapsed,
                "needsUserDecision": elapsed >= job.nextDecisionAt and not job.backgrounded,
            }
        )

    @staticmethod
    def _elapsed(job: SolveJob, now: datetime) -> int:
        anchor = job.startedAt or job.submittedAt
        started = datetime.fromisoformat(anchor)
        return max(0, int((now - started).total_seconds()))

