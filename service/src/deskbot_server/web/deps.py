"""FastAPI 依赖：会话用户解析与鉴权。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from deskbot_server.auth.session_user import SessionUser
from deskbot_server.service.user_service import UserService


def load_session_user(request: Request) -> SessionUser | None:
    cached = getattr(request.state, "current_user", None)
    if cached is not None:
        if cached is False:
            return None
        return cached
    uid = request.session.get("user_id")
    if not uid:
        request.state.current_user = False
        return None
    db_user = UserService().get_user(str(uid))
    if db_user is None or not db_user.is_active:
        request.session.pop("user_id", None)
        request.state.current_user = False
        return None
    user = SessionUser(db_user)
    request.state.current_user = user
    return user


async def get_current_user(request: Request) -> SessionUser | None:
    return load_session_user(request)


def require_user(request: Request) -> SessionUser:
    user = load_session_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail={"ok": False, "error": "unauthorized"})
    return user


RequireUser = Annotated[SessionUser, Depends(require_user)]
