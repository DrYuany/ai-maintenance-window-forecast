import json
import uuid
from ..db import transaction

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from ..config import get_settings
from ..quota import get_request, reserve_request
from ..security import Identity, require_identity
from ..services.audio_validation import read_recording
from ..services.pipeline import run_translation

router = APIRouter(prefix="/api/translations", tags=["translations"])


@router.post("")
async def translate(audio: UploadFile = File(...), conversation_id: str = Form(...), summary: str = Form("{}"), recent_turns: str = Form("[]"), speed: float = Form(1.0), voice_id: str = Form(""), request_id: str = Form(""), identity: Identity = Depends(require_identity)):
    if not 0.5 <= speed <= 2:
        raise HTTPException(status_code=422, detail="语速范围为 0.5 到 2.0")
    try:
        summary_value = json.loads(summary)
        turns_value = json.loads(recent_turns)
        if not isinstance(summary_value, dict) or not isinstance(turns_value, list):
            raise ValueError("wrong json shape")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="对话上下文格式不正确") from exc
    data, duration, content_type = await read_recording(audio)
    req_id = request_id or str(uuid.uuid4())
    existing_row = None
    with transaction() as conn:
        previous = conn.execute("SELECT id FROM translation_requests WHERE device_id=? AND client_request_id=?", (identity.device_id, req_id)).fetchone()
        existing_row = str(previous["id"]) if previous else None
    if existing_row:
        existing = get_request(existing_row, identity.device_id)
        if existing["status"] == "succeeded":
            return _result_from_row(existing, duration)
        if existing["status"] in {"processing", "usage_pending"}:
            raise HTTPException(status_code=409, detail={"request_id": existing_row, "status": existing["status"], "message": "此请求正在处理或对账中，请查询原请求状态"})
        raise HTTPException(status_code=409, detail="此请求已经结束，请重新录制后再提交")
    # Duration is known before dispatch; reserve the maximum configured model output ceiling.
    transcript_token_ceiling = max(1, int(duration * 8))
    if get_settings().llm_max_output_tokens < 1024:
        raise HTTPException(status_code=503, detail="LLM_MAX_OUTPUT_TOKENS 至少需配置为 1024")
    if len(summary) > 12000 or len(turns_value) > 6 or any(not isinstance(turn, dict) or len(str(turn.get("source", ""))) > 10000 or len(str(turn.get("translation", ""))) > 10000 for turn in turns_value):
        raise HTTPException(status_code=413, detail="本次上下文过长，请缩短当前内容后重试")
    estimated_context_tokens = max(0, len(summary) // 3 + len(json.dumps(turns_value, ensure_ascii=False)) // 3)
    estimated_input_tokens = transcript_token_ceiling + estimated_context_tokens
    reserved_id, created = reserve_request(identity.device_id, conversation_id, req_id, duration, estimated_input_tokens, get_settings().llm_max_output_tokens)
    if not created:
        existing = get_request(reserved_id, identity.device_id)
        if existing["status"] == "succeeded":
            return _result_from_row(existing, duration)
        if existing["status"] in {"processing", "usage_pending"}:
            raise HTTPException(status_code=409, detail={"request_id": reserved_id, "status": existing["status"], "message": "此请求正在处理或对账中，请查询原请求状态"})
        raise HTTPException(status_code=409, detail="此请求已经结束，请重新录制后再提交")
    return run_translation(reserved_id, identity.device_id, conversation_id, data, content_type, duration, summary_value, turns_value, speed, voice_id)


@router.get("/{request_id}")
def translation_status(request_id: str, identity: Identity = Depends(require_identity)):
    row = get_request(request_id, identity.device_id)
    result = _result_from_row(row, row["audio_charged"]) if row["status"] == "succeeded" else None
    return {"id": row["id"], "status": row["status"], "detail": row["error"], "usage": _usage_for(row, identity.device_id), "result": result}


def _usage_for(row: dict, device_id: str) -> dict:
    from ..quota import usage_payload
    return usage_payload(device_id, row["conversation_id"])


def _result_from_row(row: dict, duration: float) -> dict:
    from ..quota import usage_payload
    from ..services import audio_cache
    usage = usage_payload(row["device_id"], row["conversation_id"])
    summary = row.get("summary") or {}
    return {"id": row["id"], "status": row["status"], "transcript": row["transcript"], "translation": row["translation"], "summary": summary, "tokens": row["tokens_charged"], "audio_seconds": duration, "audio_url": f"/api/audio/play/{row['audio_key']}" if row.get("audio_key") and audio_cache.get(row["audio_key"]) else None, "usage": usage}
