from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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


logger = logging.getLogger(__name__)

DEMO_PASS_COOKIE = "mwa_demo_pass"


def _demo_access_code() -> str:
    return os.environ.get("DEMO_ACCESS_CODE", "").strip()


def _attach_demo_guard(app: FastAPI) -> None:
    """演示模式：一组口令换一张七天有效的通行证。

    没设 DEMO_ACCESS_CODE 时完全不生效，本地开发和自动化测试不受影响。
    """
    code = _demo_access_code()
    if not code:
        return

    gate = """<!doctype html><meta charset="utf-8">
<title>需要访问口令</title>
<body style="font-family:system-ui,-apple-system,'Segoe UI',sans-serif;display:grid;
place-items:center;height:100vh;margin:0;background:#fafafa;color:#1a1a1a">
<div style="text-align:center;max-width:420px;padding:0 24px">
<div style="width:40px;height:40px;border-radius:10px;background:#155fc5;margin:0 auto 20px"></div>
<h1 style="font-size:18px;font-weight:500;margin:0 0 10px">这是一个受口令保护的演示</h1>
<p style="font-size:14px;line-height:1.6;color:#666;margin:0">
请使用收到的邀请链接打开。<br>链接失效时，在网址后加上
<code style="background:#eee;padding:2px 5px;border-radius:4px">?k=口令</code> 即可。</p>
</div></body>"""

    @app.middleware("http")
    async def demo_guard(request: Request, call_next):
        path = request.url.path
        if path == "/api/health":
            return await call_next(request)
        if request.cookies.get(DEMO_PASS_COOKIE) == code:
            return await call_next(request)
        if request.query_params.get("k") == code:
            response = await call_next(request)
            response.set_cookie(DEMO_PASS_COOKIE, code, max_age=7 * 24 * 3600,
                                httponly=True, samesite="lax")
            return response
        if path.startswith("/api/"):
            return JSONResponse({"detail": "演示口令无效，请使用邀请链接打开"}, status_code=403)
        return HTMLResponse(gate, status_code=403)


def _attach_static_site(app: FastAPI) -> None:
    """演示模式：同源托管前端构建产物，一个端口同时出页面和接口。

    必须挂在所有 API 路由之后，否则 mount("/") 会吞掉未匹配的接口请求。
    """
    build_dir = Path(__file__).resolve().parents[2] / "build"
    if not (build_dir / "index.html").exists():
        logger.warning("找不到前端构建产物 %s，本次只提供 API", build_dir)
        return
    app.mount("/", StaticFiles(directory=str(build_dir), html=True), name="site")


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

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {"ok": True, "demo": bool(_demo_access_code())}

    _attach_demo_guard(app)
    _attach_static_site(app)
    return app


app = create_app()
