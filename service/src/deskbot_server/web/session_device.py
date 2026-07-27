from __future__ import annotations

from fastapi import Request

from deskbot_server.service.user_service import UserService
from deskbot_server.web.deps import load_session_user

SESSION_DEVICE_KEY = "current_device_id"


def get_current_device_id(request: Request) -> str | None:
    raw = request.session.get(SESSION_DEVICE_KEY)
    if not raw:
        return None
    device_id = str(raw).strip()
    if not device_id:
        return None
    user = load_session_user(request)
    if user is not None and not UserService().user_owns_device(user.id, device_id):
        clear_current_device(request)
        return None
    return device_id


def set_current_device_id(request: Request, device_id: str) -> None:
    request.session[SESSION_DEVICE_KEY] = device_id.strip()


def clear_current_device(request: Request) -> None:
    request.session.pop(SESSION_DEVICE_KEY, None)
