import secrets
import uuid
from typing import NamedTuple

from fastapi import HTTPException, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import get_settings

SESSION_COOKIE = "voice_session"
DEVICE_COOKIE = "voice_device"
SESSION_MAX_AGE = 12 * 60 * 60
DEVICE_MAX_AGE = 365 * 24 * 60 * 60


class Identity(NamedTuple):
    device_id: str


def _serializer(salt: str) -> URLSafeTimedSerializer:
    key = get_settings().app_secret_key
    if len(key) < 32:
        raise HTTPException(status_code=503, detail="服务端 APP_SECRET_KEY 未配置或长度不足")
    return URLSafeTimedSerializer(key, salt=salt)


def issue_cookies(response: Response, device_id: str) -> None:
    settings = get_settings()
    secure = settings.public_base_url.startswith("https://")
    response.set_cookie(SESSION_COOKIE, _serializer("voice-session").dumps({"ok": True}), max_age=SESSION_MAX_AGE, httponly=True, secure=secure, samesite="lax", path="/")
    response.set_cookie(DEVICE_COOKIE, _serializer("voice-device").dumps({"id": device_id}), max_age=DEVICE_MAX_AGE, httponly=True, secure=secure, samesite="lax", path="/")


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def require_device(request: Request) -> Identity:
    """Authenticate the signed, persistent device identity for short-lived audio playback."""
    device_id = existing_device_id(request)
    if not device_id:
        raise HTTPException(status_code=401, detail="设备身份无效，请重新登录")
    return Identity(device_id=device_id)


def require_device(request: Request) -> Identity:
    """Authenticate the signed, persistent device identity for short-lived audio playback."""
    device_id = existing_device_id(request)
    if not device_id:
        raise HTTPException(status_code=401, detail="设备身份无效，请重新登录")
    return Identity(device_id=device_id)


def _loads(cookie: str | None, salt: str, max_age: int) -> dict | None:
    if not cookie:
        return None
    try:
        value = _serializer(salt).loads(cookie, max_age=max_age)
        return value if isinstance(value, dict) else None
    except (BadSignature, SignatureExpired):
        return None


def require_identity(request: Request) -> Identity:
    session = _loads(request.cookies.get(SESSION_COOKIE), "voice-session", SESSION_MAX_AGE)
    device_id = existing_device_id(request)
    if not session or not session.get("ok") or not device_id:
        raise HTTPException(status_code=401, detail="登录已过期，请重新输入邀请口令")
    return Identity(device_id=device_id)


def new_device_id() -> str:
    return str(uuid.uuid4())


def existing_device_id(request: Request) -> str | None:
    device = _loads(request.cookies.get(DEVICE_COOKIE), "voice-device", DEVICE_MAX_AGE)
    value = device.get("id") if device else None
    return str(value) if value else None


def compare_password(actual: str, expected: str) -> bool:
    return bool(expected) and secrets.compare_digest(actual, expected)

