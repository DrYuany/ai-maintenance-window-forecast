import json
import time
import uuid
from fastapi import HTTPException

from .db import transaction

CONVERSATION_AUDIO_CAP = 24 * 60 * 60
CONVERSATION_TOKEN_CAP = 1_500_000
DEVICE_AUDIO_CAP = 240 * 60 * 60
DEVICE_TOKEN_CAP = 15_000_000
ACTIVE_CONVERSATION_CAP = 10
RECONCILIATION_SECONDS = 10 * 60


def usage_payload(device_id: str, conversation_id: str) -> dict:
    with transaction() as conn:
        conversation = conn.execute("SELECT state, device_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if conversation is None or conversation["device_id"] != device_id or conversation["state"] != "active":
            raise HTTPException(status_code=404, detail="对话不存在或已删除")
        chat = conn.execute("SELECT * FROM conversation_usage WHERE conversation_id=?", (conversation_id,)).fetchone()
        device = conn.execute("SELECT * FROM device_usage WHERE device_id=?", (device_id,)).fetchone()
        chat_audio = float(chat["audio_used"] + chat["audio_reserved"])
        chat_tokens = int(chat["tokens_used"] + chat["tokens_reserved"])
        device_audio = float(device["audio_used"] + device["audio_reserved"])
        device_tokens = int(device["tokens_used"] + device["tokens_reserved"])
        return {
            "audio_seconds_used": int(chat["audio_used"]),
            "audio_seconds_reserved": int(chat["audio_reserved"]),
            "audio_seconds_remaining": max(0, int(CONVERSATION_AUDIO_CAP - chat_audio)),
            "llm_tokens_used": int(chat["tokens_used"]),
            "llm_tokens_reserved": int(chat["tokens_reserved"]),
            "llm_tokens_remaining": max(0, CONVERSATION_TOKEN_CAP - chat_tokens),
            "device_audio_seconds_remaining": max(0, int(DEVICE_AUDIO_CAP - device_audio)),
            "device_llm_tokens_remaining": max(0, DEVICE_TOKEN_CAP - device_tokens),
        }


def reserve_request(device_id: str, conversation_id: str, client_request_id: str, audio_seconds: float, input_tokens: int, max_output_tokens: int) -> tuple[str, bool]:
    now = time.time()
    request_id = str(uuid.uuid4())
    token_ceiling = max(1, input_tokens) + max_output_tokens
    with transaction(immediate=True) as conn:
        previous = conn.execute("SELECT id, status FROM translation_requests WHERE device_id=? AND client_request_id=?", (device_id, client_request_id)).fetchone()
        if previous:
            return str(previous["id"]), False
        conversation = conn.execute("SELECT state, device_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if conversation is None or conversation["device_id"] != device_id or conversation["state"] != "active":
            raise HTTPException(status_code=404, detail="对话不存在或已删除")
        chat = conn.execute("SELECT * FROM conversation_usage WHERE conversation_id=?", (conversation_id,)).fetchone()
        device = conn.execute("SELECT * FROM device_usage WHERE device_id=?", (device_id,)).fetchone()
        if chat["audio_used"] + chat["audio_reserved"] + audio_seconds > CONVERSATION_AUDIO_CAP:
            raise HTTPException(status_code=429, detail="这段对话的音频时长额度已用完")
        if chat["tokens_used"] + chat["tokens_reserved"] + token_ceiling > CONVERSATION_TOKEN_CAP:
            raise HTTPException(status_code=429, detail="这段对话剩余 token 不足以预留本次请求")
        if device["audio_used"] + device["audio_reserved"] + audio_seconds > DEVICE_AUDIO_CAP:
            raise HTTPException(status_code=429, detail="设备累计音频时长额度已用完")
        if device["tokens_used"] + device["tokens_reserved"] + token_ceiling > DEVICE_TOKEN_CAP:
            raise HTTPException(status_code=429, detail="设备累计 token 额度不足以预留本次请求")
        conn.execute("UPDATE conversation_usage SET audio_reserved=audio_reserved+?, tokens_reserved=tokens_reserved+? WHERE conversation_id=?", (audio_seconds, token_ceiling, conversation_id))
        conn.execute("UPDATE device_usage SET audio_reserved=audio_reserved+?, tokens_reserved=tokens_reserved+? WHERE device_id=?", (audio_seconds, token_ceiling, device_id))
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        conn.execute("INSERT INTO translation_requests(id,device_id,conversation_id,client_request_id,status,stage,created_at,updated_at,audio_reserved,tokens_reserved) VALUES(?,?,?,?,?,?,?,?,?,?)", (request_id, device_id, conversation_id, client_request_id, "processing", "reserved", now, now, audio_seconds, token_ceiling))
    return request_id, True


def _request(conn, request_id: str, device_id: str):
    row = conn.execute("SELECT * FROM translation_requests WHERE id=? AND device_id=?", (request_id, device_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="处理任务不存在")
    return row


def mark_stage(request_id: str, device_id: str, stage: str, provider_request_id: str | None = None) -> None:
    with transaction(immediate=True) as conn:
        _request(conn, request_id, device_id)
        conn.execute("UPDATE translation_requests SET stage=?,provider_request_id=COALESCE(?,provider_request_id),updated_at=? WHERE id=?", (stage, provider_request_id, time.time(), request_id))


def settle_audio(request_id: str, device_id: str, dispatched: bool) -> None:
    with transaction(immediate=True) as conn:
        row = _request(conn, request_id, device_id)
        reserved = float(row["audio_reserved"])
        if not reserved:
            return
        chat = conn.execute("SELECT audio_reserved FROM conversation_usage WHERE conversation_id=?", (row["conversation_id"],)).fetchone()
        device = conn.execute("SELECT audio_reserved FROM device_usage WHERE device_id=?", (device_id,)).fetchone()
        charge = reserved if dispatched else 0.0
        conn.execute("UPDATE conversation_usage SET audio_reserved=?,audio_used=audio_used+? WHERE conversation_id=?", (max(0, chat["audio_reserved"] - reserved), charge, row["conversation_id"]))
        conn.execute("UPDATE device_usage SET audio_reserved=?,audio_used=audio_used+? WHERE device_id=?", (max(0, device["audio_reserved"] - reserved), charge, device_id))
        conn.execute("UPDATE translation_requests SET audio_reserved=0,audio_charged=?,updated_at=? WHERE id=?", (charge, time.time(), request_id))


def settle_tokens(request_id: str, device_id: str, actual_tokens: int, status: str = "processing", error: str | None = None) -> None:
    with transaction(immediate=True) as conn:
        row = _request(conn, request_id, device_id)
        reserved = int(row["tokens_reserved"])
        if not reserved:
            return
        charge = max(0, min(int(actual_tokens), reserved))
        chat = conn.execute("SELECT tokens_reserved FROM conversation_usage WHERE conversation_id=?", (row["conversation_id"],)).fetchone()
        device = conn.execute("SELECT tokens_reserved FROM device_usage WHERE device_id=?", (device_id,)).fetchone()
        conn.execute("UPDATE conversation_usage SET tokens_reserved=?,tokens_used=tokens_used+? WHERE conversation_id=?", (max(0, chat["tokens_reserved"] - reserved), charge, row["conversation_id"]))
        conn.execute("UPDATE device_usage SET tokens_reserved=?,tokens_used=tokens_used+? WHERE device_id=?", (max(0, device["tokens_reserved"] - reserved), charge, device_id))
        conn.execute("UPDATE translation_requests SET tokens_reserved=0,tokens_charged=?,updated_at=?,error=COALESCE(?,error),status=?,reconcile_deadline=NULL WHERE id=?", (charge, time.time(), error, status, request_id))


def mark_usage_unknown(request_id: str, device_id: str, error: str) -> None:
    now = time.time()
    with transaction(immediate=True) as conn:
        _request(conn, request_id, device_id)
        conn.execute("UPDATE translation_requests SET status='usage_pending',stage='usage_unknown',reconcile_deadline=?,updated_at=?,error=? WHERE id=?", (now + RECONCILIATION_SECONDS, now, error[:500], request_id))


def release_unstarted_tokens(request_id: str, device_id: str, error: str) -> None:
    settle_tokens(request_id, device_id, 0, status="failed", error=error[:500])


def save_result(request_id: str, device_id: str, transcript: str, translation: str, summary: dict, audio_key: str | None, actual_tokens: int) -> None:
    with transaction(immediate=True) as conn:
        row = _request(conn, request_id, device_id)
        reserved = int(row["tokens_reserved"])
        charge = max(0, min(actual_tokens, reserved))
        chat = conn.execute("SELECT tokens_reserved FROM conversation_usage WHERE conversation_id=?", (row["conversation_id"],)).fetchone()
        device = conn.execute("SELECT tokens_reserved FROM device_usage WHERE device_id=?", (device_id,)).fetchone()
        conn.execute("UPDATE conversation_usage SET tokens_reserved=?,tokens_used=tokens_used+? WHERE conversation_id=?", (max(0, chat["tokens_reserved"] - reserved), charge, row["conversation_id"]))
        conn.execute("UPDATE device_usage SET tokens_reserved=?,tokens_used=tokens_used+? WHERE device_id=?", (max(0, device["tokens_reserved"] - reserved), charge, device_id))
        conn.execute("UPDATE translation_requests SET status='succeeded',stage='complete',tokens_reserved=0,tokens_charged=?,transcript=?,translation=?,summary_json=?,audio_key=?,updated_at=?,reconcile_deadline=NULL,error=NULL WHERE id=?", (charge, transcript, translation, json.dumps(summary, ensure_ascii=False), audio_key, time.time(), request_id))


def fail_request(request_id: str, device_id: str, error: str, llm_dispatched: bool) -> None:
    if llm_dispatched:
        mark_usage_unknown(request_id, device_id, error)
    else:
        release_unstarted_tokens(request_id, device_id, error)
        with transaction(immediate=True) as conn:
            conn.execute("UPDATE translation_requests SET status='failed',stage='failed',updated_at=?,error=? WHERE id=? AND device_id=?", (time.time(), error[:500], request_id, device_id))


def finalize_unknown_if_due(request_id: str, device_id: str) -> None:
    with transaction(immediate=True) as conn:
        row = _request(conn, request_id, device_id)
        if row["status"] != "usage_pending" or row["reconcile_deadline"] is None or row["reconcile_deadline"] > time.time():
            return
        reserved = int(row["tokens_reserved"])
        chat = conn.execute("SELECT tokens_reserved FROM conversation_usage WHERE conversation_id=?", (row["conversation_id"],)).fetchone()
        device = conn.execute("SELECT tokens_reserved FROM device_usage WHERE device_id=?", (device_id,)).fetchone()
        conn.execute("UPDATE conversation_usage SET tokens_reserved=?,tokens_used=tokens_used+? WHERE conversation_id=?", (max(0, chat["tokens_reserved"] - reserved), reserved, row["conversation_id"]))
        conn.execute("UPDATE device_usage SET tokens_reserved=?,tokens_used=tokens_used+? WHERE device_id=?", (max(0, device["tokens_reserved"] - reserved), reserved, device_id))

        conn.execute("UPDATE translation_requests SET status='failed',stage='reconciled_unknown',tokens_reserved=0,tokens_charged=?,updated_at=?,reconcile_deadline=NULL,error=? WHERE id=?", (reserved, time.time(), '模型请求结果未知，按预留额度结算', request_id))

def get_request(request_id: str, device_id: str) -> dict:
    finalize_unknown_if_due(request_id, device_id)
    with transaction() as conn:
        row = _request(conn, request_id, device_id)
        output = dict(row)
        if row["summary_json"]:
            output["summary"] = json.loads(row["summary_json"])
        return output


def save_transcript(request_id: str, device_id: str, transcript: str) -> None:
    with transaction(immediate=True) as conn:
        _request(conn, request_id, device_id)
        conn.execute("UPDATE translation_requests SET transcript=?,updated_at=? WHERE id=?", (transcript, time.time(), request_id))

