from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request

from .config import get_settings
from .db import init_db
from .routes import audio, conversations, session, translations, voices


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="随声译 API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=get_settings().origins, allow_credentials=True, allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"], allow_headers=["*"])
app.include_router(session.router)
app.include_router(conversations.router)
app.include_router(translations.router)
app.include_router(voices.router)
app.include_router(audio.router)


@app.middleware("http")
async def initialize_database_for_test_and_lazy_start(request: Request, call_next):
    # Lifespan initializes normal deployments; this also covers ASGI hosts that disable lifespan.
    init_db()
    return await call_next(request)


@app.get("/api/health")
def health():
    settings = get_settings()
    return {"ok": True, "configured": {"access_password": bool(settings.shared_access_password), "llm": bool(settings.llm_api_key), "asr": bool(settings.volcengine_api_key), "tts": bool(settings.volcengine_api_key and settings.volcengine_tts_resource_id and settings.volcengine_tts_voice_type), "voice_clone": bool(settings.volcengine_api_key and settings.volcengine_clone_resource_id), "public_https_url": settings.public_base_url.startswith("https://")}}
