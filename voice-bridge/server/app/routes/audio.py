from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request

from ..services import audio_cache
from ..services.asr_audio_store import verify
from ..security import Identity, require_identity
from ..db import transaction

router = APIRouter(prefix="/api/audio", tags=["audio"])


@router.get("/play/{key}")
def play_audio(key: str, request: Request, identity: Identity = Depends(require_identity)):
    item = audio_cache.get(key)
    if not item:
        raise HTTPException(status_code=404, detail="这段播报音频已过期，请重新提交生成")
    with transaction() as conn:
        valid = conn.execute("SELECT 1 FROM translation_requests WHERE device_id=? AND audio_key=? AND status='succeeded'", (identity.device_id, key)).fetchone()
    if not valid:
        raise HTTPException(status_code=404, detail="音频不存在")
    range_header = request.headers.get("range")
    if range_header:
        try:
            unit, value = range_header.split("=", 1)
            start_text, end_text = value.split("-", 1)
            start = int(start_text)
            end = min(int(end_text) if end_text else len(item.data) - 1, len(item.data) - 1)
            if unit != "bytes" or start < 0 or end < start or start >= len(item.data):
                raise ValueError("invalid range")
            data = item.data[start:end + 1]
            return Response(data, status_code=206, media_type=item.content_type, headers={"Cache-Control": "private, no-store", "Accept-Ranges": "bytes", "Content-Range": f"bytes {start}-{end}/{len(item.data)}", "Content-Length": str(len(data))})
        except ValueError as exc:
            raise HTTPException(status_code=416, detail="音频区间无效", headers={"Content-Range": f"bytes */{len(item.data)}"}) from exc
    return Response(item.data, media_type=item.content_type, headers={"Cache-Control": "private, no-store", "Accept-Ranges": "bytes", "Content-Length": str(len(item.data))})


@router.get("/{key}")
def asr_audio(key: str, expires: int = Query(...), signature: str = Query(...)):
    data = verify(key, expires, signature)
    item = audio_cache.get(key)
    return Response(data, media_type=item.content_type if item else "application/octet-stream", headers={"Cache-Control": "no-store", "Content-Disposition": "inline"})
