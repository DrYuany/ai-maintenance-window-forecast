import hashlib
import hmac
import time
from urllib.parse import urlencode

from fastapi import HTTPException

from ..config import get_settings
from . import audio_cache


def publish(data: bytes, content_type: str) -> tuple[str, str]:
    settings = get_settings()
    if not settings.public_base_url.startswith("https://"):
        raise HTTPException(status_code=503, detail="ASR 录音识别需要配置可从公网访问的 HTTPS PUBLIC_BASE_URL")
    key = audio_cache.put(data, content_type, ttl=300)
    expires = int(time.time()) + 300
    payload = f"{key}:{expires}".encode()
    signature = hmac.new(settings.app_secret_key.encode(), payload, hashlib.sha256).hexdigest()
    url = f"{settings.public_base_url.rstrip('/')}/api/audio/{key}?{urlencode({'expires': expires, 'signature': signature})}"
    return key, url


def verify(key: str, expires: int, signature: str) -> bytes:
    settings = get_settings()
    if expires < int(time.time()) or expires > int(time.time()) + 305:
        raise HTTPException(status_code=404, detail="音频临时地址已过期")
    expected = hmac.new(settings.app_secret_key.encode(), f"{key}:{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=404, detail="音频临时地址无效")
    item = audio_cache.get(key)
    if not item:
        raise HTTPException(status_code=404, detail="音频临时地址已失效")
    return item.data
