import secrets
import time
from dataclasses import dataclass


@dataclass
class CachedAudio:
    data: bytes
    content_type: str
    expires_at: float


_items: dict[str, CachedAudio] = {}


def put(data: bytes, content_type: str, ttl: int = 300) -> str:
    cleanup()
    key = secrets.token_urlsafe(32)
    _items[key] = CachedAudio(data, content_type, time.time() + ttl)
    return key


def get(key: str) -> CachedAudio | None:
    item = _items.get(key)
    if not item or item.expires_at <= time.time():
        _items.pop(key, None)
        return None
    return item


def delete(key: str | None) -> None:
    if key:
        _items.pop(key, None)


def cleanup() -> None:
    now = time.time()
    for key in [key for key, item in _items.items() if item.expires_at <= now]:
        _items.pop(key, None)
