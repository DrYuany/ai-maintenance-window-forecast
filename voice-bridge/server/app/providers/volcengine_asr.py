import uuid

import httpx
from fastapi import HTTPException

from ..config import get_settings

ENDPOINT = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"


def recognize(audio_url: str) -> tuple[str, str]:
    settings = get_settings()
    if not settings.volcengine_api_key:
        raise HTTPException(status_code=503, detail="尚未配置 VOLCENGINE_API_KEY")
    request_id = str(uuid.uuid4())
    headers = {"X-Api-Key": settings.volcengine_api_key, "X-Api-Resource-Id": settings.volcengine_asr_resource_id, "X-Api-Request-Id": request_id, "X-Api-Sequence": "-1", "Content-Type": "application/json"}
    try:
        response = httpx.post(ENDPOINT, headers=headers, json={"user": {"uid": "voice-bridge"}, "audio": {"url": audio_url}, "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True, "show_utterances": False}}, timeout=180)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("unexpected response")
        transcript = (result.get("result") or {}).get("text") or result.get("text") or ""
        if not transcript.strip():
            raise ValueError("empty transcription")
        return transcript.strip(), request_id
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"語音識別服務返回錯誤：{exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="语音识别失败，请检查 ASR 密钥、资源包和音频格式") from exc
