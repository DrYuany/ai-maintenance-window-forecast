import base64
import json
import uuid

import httpx
from fastapi import HTTPException

from ..config import get_settings

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"


def synthesize(text: str, voice_id: str, speed: float) -> tuple[bytes, int]:
    settings = get_settings()
    if not settings.volcengine_api_key:
        raise HTTPException(status_code=503, detail="尚未配置 VOLCENGINE_API_KEY")
    resource_id = settings.volcengine_tts_resource_id
    voice_type = voice_id or settings.volcengine_tts_voice_type
    if not resource_id or not voice_type:
        raise HTTPException(status_code=503, detail="请配置 VOLCENGINE_TTS_RESOURCE_ID 与默认音色，或选择已配置音色")
    request_id = str(uuid.uuid4())
    headers = {"X-Api-Key": settings.volcengine_api_key, "X-Api-Resource-Id": resource_id, "X-Api-Request-Id": request_id, "Content-Type": "application/json"}
    speech_rate = round((speed - 1.0) * 100)
    body = {"user": {"uid": "voice-bridge"}, "req_params": {"text": text, "speaker": voice_type, "audio_params": {"format": "mp3", "sample_rate": 24000, "speech_rate": speech_rate}}}
    try:
        with httpx.stream("POST", ENDPOINT, headers=headers, json=body, timeout=180) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            words = 0
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    data = payload.get("data")
                    if data:
                        chunks.append(base64.b64decode(data))
                    usage = payload.get("usage") or {}
                    words = max(words, int(usage.get("text_words", 0)))
                except (ValueError, TypeError):
                    continue
        audio = b"".join(chunks)
        if not audio:
            raise ValueError("empty TTS audio")
        return audio, words
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"语音合成服务返回错误：{exc.response.status_code}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="语音合成失败，请检查 TTS 资源与音色配置") from exc
