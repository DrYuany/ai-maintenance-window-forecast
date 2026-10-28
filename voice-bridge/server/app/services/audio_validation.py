import subprocess
from fastapi import HTTPException, UploadFile
from imageio_ffmpeg import get_ffmpeg_exe

from ..config import get_settings


async def read_recording(upload: UploadFile) -> tuple[bytes, float, str]:
    data = await upload.read(get_settings().max_upload_bytes + 1)
    await upload.close()
    if not data:
        raise HTTPException(status_code=400, detail="没有收到录音数据")
    if len(data) > get_settings().max_upload_bytes:
        raise HTTPException(status_code=413, detail="录音文件超过大小上限")
    content_type = (upload.content_type or "audio/webm").split(";")[0].strip().lower()
    if content_type not in {"audio/webm", "audio/mp4", "audio/mpeg", "audio/wav", "audio/ogg"}:
        raise HTTPException(status_code=415, detail="当前录音格式不受支持")
    # ffmpeg probes stdin, so audio remains in memory and is never written to disk.
    try:
        probe = subprocess.run([get_ffmpeg_exe(), "-hide_banner", "-i", "pipe:0"], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
        text = probe.stderr.decode("utf-8", "ignore")
        marker = "Duration: "
        start = text.find(marker)
        if start < 0:
            raise ValueError("duration unavailable")
        duration_text = text[start + len(marker):].split(",", 1)[0].strip()
        if ":" in duration_text:
            hh, mm, ss = duration_text.split(":")
            duration = int(hh) * 3600 + int(mm) * 60 + float(ss)
        else:
            # Input duration may be omitted for streaming containers such as WebM.
            # Decode to a null muxer and read ffmpeg's reported final timestamp.
            decoded = subprocess.run([get_ffmpeg_exe(), "-hide_banner", "-loglevel", "info", "-i", "pipe:0", "-f", "null", "-"], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False)
            diagnostic = decoded.stderr.decode("utf-8", "ignore")
            match = next((line.split("time=", 1)[1].split()[0] for line in diagnostic.splitlines() if "time=" in line), None)
            if not match:
                raise ValueError("duration unavailable")
            hh, mm, ss = match.split(":")
            duration = int(hh) * 3600 + int(mm) * 60 + float(ss)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="录音无法解码或时长无法识别") from exc
    if duration <= 0 or duration > get_settings().max_recording_seconds:
        raise HTTPException(status_code=413, detail=f"录音时长需在 1 到 {get_settings().max_recording_seconds // 60} 分钟之间")
    return data, duration, content_type
