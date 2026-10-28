import base64
import uuid

import httpx
from fastapi import HTTPException

from ..config import get_settings

CLONE_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/voice_clone"
QUERY_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/get_voice"


def create_voice(sample: bytes, content_type: str) -> tuple[str, str]:
    settings = get_settings()
    if not settings.volcengine_api_key or not settings.volcengine_clone_resource_id:
        raise HTTPException(status_code=503, detail="尚未配置火山引擎音色复刻 API Key 或资源包")
    voice_id = f"voice_bridge_{uuid.uuid4().hex[:16]}"
    request_id = str(uuid.uuid4())
    headers = {"X-Api-Key": settings.volcengine_api_key, "X-Api-Resource-Id": settings.volcengine_clone_resource_id, "X-Api-Request-Id": request_id, "Content-Type": "application/json"}
    body = {"speaker_id": voice_id, "audio": {"data": base64.b64encode(sample).decode(), "format": content_type.split("/")[-1].replace("mpeg", "mp3")}, "language": "zh-CN"}
    try:
        response = httpx.post(CLONE_ENDPOINT, headers=headers, json=body, timeout=180)
        response.raise_for_status()
        result = response.json()
        status = str(result.get("speaker_status", result.get("status", ""))).lower()
        if status and status not in {"success", "succeeded", "active", "2", "3"}:
            raise ValueError("provider did not activate voice")
        return voice_id, "已提交云端复刻；实际可用状态以服务商返回为准"
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"音色复刻服务返回错误：{exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="音色复刻失败，请检查资源包和音频样本") from exc
