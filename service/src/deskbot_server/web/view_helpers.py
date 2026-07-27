"""FastAPI 视图辅助：模板、JSON、登录态与 ViewAPIRoute。"""

from __future__ import annotations

import inspect
import secrets
from functools import wraps
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from starlette.datastructures import UploadFile

from deskbot_server.auth.session_user import SessionUser
from deskbot_server.service.user_service import UserService
from deskbot_server.web.session_device import get_current_device_id
from deskbot_server.web.urls import get_flashed_messages, url_for

_templates = None


def set_templates(templates) -> None:
    global _templates
    _templates = templates


def get_templates():
    return _templates


def jsonify(*args: Any, **kwargs: Any) -> JSONResponse:
    if args and kwargs:
        raise TypeError("jsonify args/kwargs exclusive")
    if kwargs:
        payload = kwargs
    elif len(args) == 1:
        payload = args[0]
    else:
        payload = args
    return JSONResponse(payload)


def redirect(location: str, code: int = 302) -> RedirectResponse:
    return RedirectResponse(url=location, status_code=code)


def safe_next_url(raw: str | None, fallback: str = "/home") -> str:
    if not raw:
        return fallback
    parsed = urlparse(raw)
    if parsed.netloc or parsed.scheme:
        return fallback
    if not raw.startswith("/"):
        return fallback
    return raw


def new_csrf_token() -> str:
    return secrets.token_urlsafe(16)


def args_get(request: Request, key: str, default: Any = None, type: Any = None) -> Any:
    val = request.query_params.get(key)
    if val is None:
        return default
    if type is not None:
        try:
            return type(val)
        except Exception:
            return default
    return val


def form_get(request: Request, key: str, default: Any = None) -> Any:
    form = getattr(request.state, "form", None)
    if form is None:
        return default
    if hasattr(form, "get"):
        val = form.get(key)
    else:
        val = form.get(key) if isinstance(form, dict) else None
    if val is None:
        return default
    return val


def files_get(request: Request, key: str, default: Any = None) -> Any:
    form = getattr(request.state, "form", None)
    if form is None:
        return default
    val = form.get(key) if hasattr(form, "get") else None
    if isinstance(val, UploadFile):
        return val
    return default


def read_upload_bytes(upload: UploadFile) -> bytes:
    """同步读取上传文件（``UploadFile.read`` 为 async，视图为 sync def）。"""
    raw = getattr(upload, "file", None)
    if raw is not None:
        try:
            raw.seek(0)
        except Exception:
            pass
        return raw.read()
    # 兜底：若已有同步 read
    data = upload.read()
    if inspect.isawaitable(data):
        raise RuntimeError("UploadFile.read is async; use upload.file.read() in sync views")
    return data


def get_json(request: Request, *, silent: bool = False, force: bool = False) -> Any:
    data = getattr(request.state, "json", None)
    if data is not None:
        return data
    if not force and not is_json_request(request):
        if silent:
            return None
        return None
    return None


def get_body(request: Request, *, as_text: bool = False) -> bytes | str:
    data = getattr(request.state, "body", None)
    if data is None:
        data = b""
    if as_text:
        return data.decode("utf-8", errors="replace")
    return data


def is_json_request(request: Request) -> bool:
    ctype = (request.headers.get("content-type") or "").lower()
    return "application/json" in ctype


def login_user(request: Request, user: SessionUser, remember: bool = True) -> None:
    request.session["user_id"] = user.id
    if remember:
        request.session["remember"] = True
    request.state.current_user = user


def logout_user(request: Request) -> None:
    request.session.pop("user_id", None)
    request.session.pop("remember", None)
    request.state.current_user = False


def render_template(request: Request, name: str, **context: Any) -> HTMLResponse:
    if _templates is None:
        raise RuntimeError("templates not configured")
    from deskbot_server.web.deps import load_session_user

    user = load_session_user(request)
    display_name = None
    current_device_id = None
    is_developer = False
    if user is not None:
        display_name = getattr(user, "display_name", None) or user.email
        current_device_id = get_current_device_id(request)
        db_user = UserService().get_user(user.id)
        is_developer = bool(db_user and getattr(db_user, "is_developer", False))
    context.setdefault("nav_user_email", user.email if user is not None else None)
    context.setdefault("nav_display_name", display_name)
    context.setdefault("nav_current_device_id", current_device_id)
    context.setdefault("nav_is_developer", is_developer)
    context.setdefault("url_for", url_for)
    context.setdefault("get_flashed_messages", lambda with_categories=False: get_flashed_messages(request, with_categories))
    return _templates.TemplateResponse(request, name, context)


def convert_view_result(result: Any) -> Response:
    if isinstance(result, Response):
        return result
    if isinstance(result, tuple):
        body, status = result[0], result[1]
        headers = result[2] if len(result) > 2 else None
        if isinstance(body, Response):
            body.status_code = status
            return body
        if isinstance(body, dict):
            resp = JSONResponse(body, status_code=status)
        else:
            resp = HTMLResponse(str(body), status_code=status)
        if headers:
            resp.headers.update(headers)
        return resp
    if isinstance(result, dict):
        return JSONResponse(result)
    if result is None:
        return Response(status_code=204)
    return HTMLResponse(str(result))


def _wrap_endpoint(endpoint: Callable) -> Callable:
    if getattr(endpoint, "_view_wrapped", False):
        return endpoint

    if inspect.iscoroutinefunction(endpoint):

        @wraps(endpoint)
        async def async_wrapped(*args, **kwargs):
            return convert_view_result(await endpoint(*args, **kwargs))

        async_wrapped._view_wrapped = True  # type: ignore[attr-defined]
        return async_wrapped

    @wraps(endpoint)
    def sync_wrapped(*args, **kwargs):
        return convert_view_result(endpoint(*args, **kwargs))

    sync_wrapped._view_wrapped = True  # type: ignore[attr-defined]
    return sync_wrapped


class ViewAPIRoute(APIRoute):
    """把视图返回值（含 ``(body, status)``）统一转成 Starlette Response。"""

    def __init__(self, *args: Any, **kwargs: Any):
        endpoint = kwargs.get("endpoint")
        if endpoint is not None:
            kwargs["endpoint"] = _wrap_endpoint(endpoint)
        super().__init__(*args, **kwargs)
