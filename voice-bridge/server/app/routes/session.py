from fastapi import APIRouter, HTTPException, Request, Response

from ..config import get_settings
from ..models import LoginBody
from ..security import clear_session, compare_password, existing_device_id, issue_cookies, new_device_id

router = APIRouter(prefix="/api/session", tags=["session"])


@router.post("")
def login(body: LoginBody, request: Request, response: Response):
    expected = get_settings().shared_access_password
    if not expected:
        raise HTTPException(status_code=503, detail="服务端尚未配置共享访问口令")
    if not compare_password(body.password, expected):
        raise HTTPException(status_code=401, detail="口令不正确")
    device_id = existing_device_id(request) or new_device_id()
    issue_cookies(response, device_id)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    clear_session(response)
    return {"ok": True}
