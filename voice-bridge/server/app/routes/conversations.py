import time
import uuid
import json

from fastapi import APIRouter, Depends, HTTPException, Response

from ..db import transaction
from ..models import ConversationCreate, ConversationRename
from ..quota import ACTIVE_CONVERSATION_CAP, usage_payload
from ..security import Identity, require_identity

router = APIRouter(prefix="/api/conversations", tags=["conversations"])
EMPTY_SUMMARY = {"topic": "", "entities_and_glossary": [], "translation_preferences": [], "unresolved_references": []}


@router.get("")
def list_conversations(identity: Identity = Depends(require_identity)):
    with transaction() as conn:
        rows = conn.execute("SELECT c.id,c.title,c.created_at,c.updated_at,r.id AS request_id,r.transcript,r.translation,r.summary_json,r.tokens_charged,r.audio_charged,r.audio_key,r.created_at AS turn_created_at FROM conversations c LEFT JOIN translation_requests r ON r.conversation_id=c.id AND r.status='succeeded' WHERE c.device_id=? AND c.state='active' ORDER BY c.updated_at DESC,r.created_at ASC", (identity.device_id,)).fetchall()
        items = {}
        for row in rows:
            item = items.setdefault(row["id"], {"id": row["id"], "title": row["title"], "createdAt": int(row["created_at"] * 1000), "updatedAt": int(row["updated_at"] * 1000), "turns": [], "summary": EMPTY_SUMMARY})
            if row["request_id"]:
                from ..services import audio_cache
                item["turns"].append({"id": row["request_id"], "source": row["transcript"] or "", "translation": row["translation"] or "", "createdAt": int(row["turn_created_at"] * 1000), "tokens": row["tokens_charged"], "audioSeconds": row["audio_charged"], "audioUrl": f"/api/audio/play/{row['audio_key']}" if row["audio_key"] and audio_cache.get(row["audio_key"]) else None})
                if row["summary_json"]:
                    item["summary"] = json.loads(row["summary_json"])
        return list(items.values())


@router.post("")
def create_conversation(body: ConversationCreate, identity: Identity = Depends(require_identity)):
    now = time.time()
    conversation_id = str(uuid.uuid4())
    with transaction(immediate=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM conversations WHERE device_id=? AND state='active'", (identity.device_id,)).fetchone()[0]
        if count >= ACTIVE_CONVERSATION_CAP:
            raise HTTPException(status_code=409, detail="最多保留 10 个活跃对话，请先删除一个对话")
        conn.execute("INSERT INTO conversations(id,device_id,title,created_at,updated_at) VALUES(?,?,?,?,?)", (conversation_id, identity.device_id, body.title.strip(), now, now))
        conn.execute("INSERT INTO conversation_usage(conversation_id) VALUES(?)", (conversation_id,))
        conn.execute("INSERT OR IGNORE INTO device_usage(device_id) VALUES(?)", (identity.device_id,))
        conn.execute("UPDATE device_usage SET audio_reserved=audio_reserved+(SELECT COALESCE(SUM(audio_reserved),0) FROM conversation_usage cu JOIN conversations c ON c.id=cu.conversation_id WHERE c.device_id=?), tokens_reserved=tokens_reserved+(SELECT COALESCE(SUM(tokens_reserved),0) FROM conversation_usage cu JOIN conversations c ON c.id=cu.conversation_id WHERE c.device_id=?) WHERE device_id=?", (identity.device_id, identity.device_id, identity.device_id))
        conn.execute("UPDATE device_usage SET audio_reserved=audio_reserved+(SELECT COALESCE(SUM(audio_reserved),0) FROM conversation_usage cu JOIN conversations c ON c.id=cu.conversation_id WHERE c.device_id=?), tokens_reserved=tokens_reserved+(SELECT COALESCE(SUM(tokens_reserved),0) FROM conversation_usage cu JOIN conversations c ON c.id=cu.conversation_id WHERE c.device_id=?) WHERE device_id=?", (identity.device_id, identity.device_id, identity.device_id))
    return {"id": conversation_id, "title": body.title.strip(), "createdAt": int(now * 1000), "updatedAt": int(now * 1000), "turns": [], "summary": EMPTY_SUMMARY}


@router.patch("/{conversation_id}")
def rename_conversation(conversation_id: str, body: ConversationRename, identity: Identity = Depends(require_identity)):
    with transaction(immediate=True) as conn:
        result = conn.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=? AND device_id=? AND state='active'", (body.title.strip(), time.time(), conversation_id, identity.device_id))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="对话不存在或已删除")
        row = conn.execute("SELECT id,title,created_at,updated_at FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    return {"id": row["id"], "title": row["title"], "createdAt": int(row["created_at"] * 1000), "updatedAt": int(row["updated_at"] * 1000), "turns": [], "summary": EMPTY_SUMMARY}


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str, response: Response, identity: Identity = Depends(require_identity)):
    with transaction(immediate=True) as conn:
        row = conn.execute("SELECT state FROM conversations WHERE id=? AND device_id=?", (conversation_id, identity.device_id)).fetchone()
        if row is None or row["state"] != "active":
            raise HTTPException(status_code=404, detail="对话不存在或已删除")
        pending = conn.execute("SELECT 1 FROM translation_requests WHERE conversation_id=? AND status IN ('processing','usage_pending') LIMIT 1", (conversation_id,)).fetchone()
        if pending:
            raise HTTPException(status_code=409, detail="这段对话仍有云端任务处理中或待对账，完成后再删除")
        conn.execute("UPDATE conversations SET state='deleted',deleted_at=?,updated_at=? WHERE id=?", (time.time(), time.time(), conversation_id))


@router.get("/{conversation_id}/usage")
def conversation_usage(conversation_id: str, identity: Identity = Depends(require_identity)):
    return usage_payload(identity.device_id, conversation_id)
