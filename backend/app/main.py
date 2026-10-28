from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent as agent_api
from app.api import conversations as conversations_api
from app.api import dashboard as dashboard_api
from app.api import versions as versions_api
from app.core.agent_runtime import AgentRuntime
from app.core.agent_memory import (
    ConversationSummaryRepository,
    DecisionMemoryRepository,
    UserProfileRepository,
)
from app.core.agent_trace import AgentTraceRepository
from app.core.memory_manager import MemoryManager
from app.core.maintenance_product_executor import MaintenanceProductExecutor
from app.core.result_normalizer import ResultNormalizer
from app.core.version_manager import VersionManager
from app.core.solve_jobs import SolveJobRepository
from app.db.database import Database
from app.db.models import (
    CandidateRepository,
    ConversationRepository,
    MessageRepository,
    ModelArtifactRepository,
    VersionRepository,
    WorkingMemoryRepository,
)


def load_local_env() -> None:
    if os.environ.get("SKIP_LOCAL_ENV") == "1":
        return
    candidate_paths = [
        Path(__file__).resolve().parents[2] / ".env.local",
        Path(__file__).resolve().parents[3] / ".env.local",
    ]
    for path in candidate_paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def create_app() -> FastAPI:
    load_local_env()
    app = FastAPI(title="Maintenance Window Agent API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    db = Database()
    conversation_repo = ConversationRepository(db)
    message_repo = MessageRepository(db)
    memory_repo = WorkingMemoryRepository(db)
    version_repo = VersionRepository(db)
    model_artifact_repo = ModelArtifactRepository(db)
    candidate_repo = CandidateRepository(db)
    memory_manager = MemoryManager(memory_repo)
    solve_jobs = SolveJobRepository(db)
    solve_jobs.recover_interrupted_jobs()
    trace_repo = AgentTraceRepository(db)
    summary_repo = ConversationSummaryRepository(db)
    profile_repo = UserProfileRepository(db)
    decision_repo = DecisionMemoryRepository(db)
    runtime = AgentRuntime(
        conversations=conversation_repo,
        messages=message_repo,
        memories=memory_manager,
        versions=version_repo,
        model_artifacts=model_artifact_repo,
        candidates=candidate_repo,
        normalizer=ResultNormalizer(),
        version_manager=VersionManager(),
        jobs=solve_jobs,
        product_executor=MaintenanceProductExecutor(),
        traces=trace_repo,
        summary_repo=summary_repo,
        profile_repo=profile_repo,
        decision_repo=decision_repo,
    )

    app.dependency_overrides[conversations_api.get_conversation_repo] = lambda: conversation_repo
    app.dependency_overrides[conversations_api.get_memory_manager] = lambda: memory_manager
    app.dependency_overrides[conversations_api.get_message_repo] = lambda: message_repo
    app.dependency_overrides[agent_api.get_runtime] = lambda: runtime
    app.dependency_overrides[dashboard_api.get_runtime] = lambda: runtime
    app.dependency_overrides[versions_api.get_runtime] = lambda: runtime
    app.dependency_overrides[versions_api.get_version_repo] = lambda: version_repo

    app.include_router(conversations_api.router)
    app.include_router(agent_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(versions_api.router)
    return app


app = create_app()
