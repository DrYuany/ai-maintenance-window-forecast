from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_secret_key: str = ""
    shared_access_password: str = ""
    allowed_origins: str = "http://localhost:5173"
    public_base_url: str = ""
    database_path: str = "./data/voice_bridge.sqlite3"
    max_upload_bytes: int = 25 * 1024 * 1024
    max_recording_seconds: int = 600
    max_tts_cache_bytes: int = 15 * 1024 * 1024
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-flash"
    llm_timeout_seconds: int = 120
    llm_max_output_tokens: int = 16000
    volcengine_app_id: str = ""
    volcengine_access_token: str = ""
    volcengine_api_key: str = ""
    volcengine_asr_resource_id: str = "volc.bigasr.auc_turbo"
    volcengine_tts_resource_id: str = ""
    volcengine_tts_voice_type: str = ""
    volcengine_tts_cluster: str = "volcano_tts"
    volcengine_clone_resource_id: str = ""
    preset_voices_json: str = "[]"

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def sqlite_path(self) -> Path:
        path = Path(self.database_path)
        return path if path.is_absolute() else Path(__file__).resolve().parents[1] / path

    @property
    def preset_voices(self) -> list[dict]:
        import json
        try:
            value = json.loads(self.preset_voices_json)
            return value if isinstance(value, list) else []
        except (ValueError, TypeError):
            return []


@lru_cache
def get_settings() -> Settings:
    return Settings()

