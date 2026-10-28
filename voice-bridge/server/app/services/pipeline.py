from fastapi import HTTPException

from ..config import get_settings
from ..db import transaction
from ..providers import qwen, volcengine_asr, volcengine_tts
from ..quota import mark_stage, save_result, settle_audio, fail_request, save_transcript
from . import audio_cache
from .asr_audio_store import publish


def run_translation(request_id: str, device_id: str, conversation_id: str, request_audio: bytes, content_type: str, audio_seconds: float, summary: dict, recent_turns: list[dict], speed: float, voice_id: str) -> dict:
    settings = get_settings()
    llm_dispatched = False
    llm_usage_tokens = 0
    audio_key = None
    try:
        audio_key, signed_url = publish(request_audio, content_type)
        mark_stage(request_id, device_id, "transcribing")
        transcript, provider_request_id = volcengine_asr.recognize(signed_url)
        audio_cache.delete(audio_key)
        audio_key = None
        settle_audio(request_id, device_id, True)
        if not transcript.strip():
            raise HTTPException(status_code=422, detail="没有识别到清晰的人声")
        save_transcript(request_id, device_id, transcript)
        mark_stage(request_id, device_id, "translating", provider_request_id)
        llm_dispatched = True
        translation, updated_summary, tokens = qwen.translate(transcript, summary, recent_turns)
        llm_usage_tokens = tokens
        mark_stage(request_id, device_id, "synthesizing")
        voice = settings.volcengine_tts_voice_type
        preset = next((item for item in settings.preset_voices if item.get("id") == voice_id), None)
        if preset:
            voice = preset.get("provider_voice_id") or voice
        elif voice_id:
            with transaction() as conn:
                row = conn.execute("SELECT provider_voice_id FROM voices WHERE id=? AND device_id=? AND status='active' AND hidden=0", (voice_id, device_id)).fetchone()
            if row is None:
                raise HTTPException(status_code=400, detail="所选音色不可用")
            voice = row["provider_voice_id"]
        audio, tts_tokens = volcengine_tts.synthesize(translation, voice, speed)
        audio_key = audio_cache.put(audio, "audio/mpeg", ttl=30 * 60)
        counted_tokens = tokens + tts_tokens
        llm_usage_tokens = counted_tokens
        save_result(request_id, device_id, transcript, translation, updated_summary, audio_key, counted_tokens)
        usage = _usage(device_id, conversation_id)
        return {"id": request_id, "status": "succeeded", "transcript": transcript, "translation": translation, "summary": updated_summary, "tokens": tokens, "audio_seconds": audio_seconds, "audio_url": f"/api/audio/play/{audio_key}", "usage": usage}
    except HTTPException as exc:
        if audio_key:
            audio_cache.delete(audio_key)
        if not llm_dispatched and request_audio:
            settle_audio(request_id, device_id, True)
        elif llm_usage_tokens:
            _settle_failed_known_usage(request_id, device_id, llm_usage_tokens, str(exc.detail))
            raise
        fail_request(request_id, device_id, str(exc.detail), llm_dispatched)
        raise
    except Exception as exc:
        if audio_key:
            audio_cache.delete(audio_key)
        if not llm_dispatched and request_audio:
            settle_audio(request_id, device_id, True)
        elif llm_usage_tokens:
            _settle_failed_known_usage(request_id, device_id, llm_usage_tokens, "TTS 生成失败；LLM 用量已结算")
            raise HTTPException(status_code=502, detail="TTS 生成失败，LLM 用量已结算，可重试新的播报请求") from exc
        fail_request(request_id, device_id, "云端处理失败", llm_dispatched)
        raise HTTPException(status_code=502, detail="云端处理失败，请稍后查看任务状态") from exc


def _usage(device_id: str, conversation_id: str) -> dict:
    from ..quota import usage_payload
    return usage_payload(device_id, conversation_id)


def _settle_failed_known_usage(request_id: str, device_id: str, tokens: int, error: str) -> None:
    from ..quota import settle_tokens
    settle_tokens(request_id, device_id, tokens, status="failed", error=error[:500])
