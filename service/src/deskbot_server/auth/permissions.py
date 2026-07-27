from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from deskbot_server.auth.session_user import SessionUser
from deskbot_server.web.deps import require_user
from deskbot_server.web.urls import flash, url_for


def current_user_is_developer(user: SessionUser | None) -> bool:
    if user is None:
        return False
    return bool(getattr(user, "is_developer", False))


def require_developer(request: Request, user: SessionUser = Depends(require_user)) -> SessionUser:
    if not current_user_is_developer(user):
        path = request.url.path
        if path.startswith("/api/") or "application/json" in (request.headers.get("accept") or ""):
            raise HTTPException(status_code=403, detail={"ok": False, "error": "需要开发者权限"})
        flash(request, "需要开发者权限", "error")
        raise HTTPException(status_code=307, headers={"Location": url_for("app2c.home")})
    return user


RequireDeveloper = Annotated[SessionUser, Depends(require_developer)]
