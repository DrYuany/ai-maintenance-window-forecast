from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.solve_jobs import SolveJobRepository
from app.db.database import Database


def test_solve_job_is_persisted_and_soft_prompt_does_not_relabel_unknown(temp_db_path) -> None:
    repository = SolveJobRepository(Database(temp_db_path))
    submitted = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)
    job = repository.create(
        conversation_id="conv-1",
        request_payload={"strategy": "高风险优先抢修"},
        model_fingerprint="model-1",
        now=submitted,
    )

    repository.mark_running(job.id, now=submitted + timedelta(seconds=1))
    persisted = repository.get(job.id, now=submitted + timedelta(seconds=181))

    assert persisted is not None
    assert persisted.status == "RUNNING"
    assert persisted.solverStatus == "UNKNOWN"
    assert persisted.needsUserDecision is True
    assert persisted.elapsedSeconds == 180

    deferred = repository.defer_decision(job.id, seconds=180, now=submitted + timedelta(seconds=181))
    assert deferred.needsUserDecision is False
    assert repository.get(job.id, now=submitted + timedelta(seconds=200)).needsUserDecision is False
    assert repository.get(job.id, now=submitted + timedelta(seconds=362)).needsUserDecision is True


def test_completed_job_survives_repository_recreation(temp_db_path) -> None:
    database = Database(temp_db_path)
    first = SolveJobRepository(database)
    job = first.create("conv-1", {"strategy": "成本最低优先"}, "model-1")
    first.mark_completed(job.id, {"summary": "ok"}, solver_status="FEASIBLE")

    restored = SolveJobRepository(Database(temp_db_path)).get(job.id)

    assert restored is not None
    assert restored.status == "COMPLETED"
    assert restored.solverStatus == "FEASIBLE"
    assert restored.result == {"summary": "ok"}


def test_interrupted_running_jobs_are_recovered_as_unknown_failures(temp_db_path) -> None:
    repository = SolveJobRepository(Database(temp_db_path))
    job = repository.create("conv-1", {"strategy": "成本最低优先"}, "model-1")
    repository.mark_running(job.id)

    recovered = SolveJobRepository(Database(temp_db_path)).recover_interrupted_jobs()
    restored = repository.get(job.id)

    assert recovered == 1
    assert restored.status == "FAILED"
    assert restored.solverStatus == "UNKNOWN"
    assert "服务重启" in restored.error

