import json
import time
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..config import get_settings
from ..db import transaction
from ..security import Identity, require_identity
from ..services.audio_validation import read_recording
from ..providers.volcengine_clone import create_voice

router = APIRouter(prefix="/api/voices", tags=["voices"])


@router.get("")
def list_voices(identity: Identity = Depends(require_identity)):
    settings = get_settings()
    voices = []
    for item in settings.preset_voices:
        if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
            continue
        voices.append({"id": item["id"], "name": item["name"], "description": item.get("description", ""), "kind": "preset", "enabled": bool(item.get("provider_voice_id"))})
    with transaction() as conn:
        rows = conn.execute("SELECT id,name,description,status FROM voices WHERE device_id=? AND hidden=0 ORDER BY created_at", (identity.device_id,)).fetchall()
    voices.extend({"id": row["id"], "name": row["name"], "description": row["description"], "kind": "cloned", "enabled": row["status"] == "active"} for row in rows)
    return voices


@router.post("/clone")
async def clone_voice(audio: UploadFile = File(...), authorized: bool = Form(False), identity: Identity = Depends(require_identity)):
    if not authorized:
        raise HTTPException(status_code=400, detail="请先确认已获声音本人授权")
    data, duration, content_type = await read_recording(audio)
    if duration < 5 or duration > 30:
        raise HTTPException(status_code=422, detail="声音样本时长需要在 5 到 30 秒之间")
    provider_id, description = create_voice(data, content_type)
    voice_id = str(uuid.uuid4())
    with transaction(immediate=True) as conn:
        conn.execute("INSERT INTO voices(id,device_id,name,description,provider_voice_id,status,authorized,created_at) VALUES(?,?,?,?,?,?,1,?)", (voice_id, identity.device_id, "我的声音", description, provider_id, "active", time.time()))
    return {"id": voice_id, "name": "我的声音", "description": description, "kind": "cloned", "enabled": True}
