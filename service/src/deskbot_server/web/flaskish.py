"""FastAPI 下的 Flask 风格兼容层（request / session / url_for / flash 等）。

让既有 web 蓝图以同步 ``def`` 视图迁到 APIRouter，由 FastAPI 线程池执行；
中间件在进入视图前预解析 form/json，避免 Starlette 异步 body API 差异。
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar
from functools import wraps
from typing import Any, Callable, Optional
from urllib.parse import urlencode, urlparse

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from starlette.datastructures import UploadFile

from deskbot_server.auth.flask_user import FlaskUser
from deskbot_server.dao.user_dao import UserDao

_request_var: ContextVar[Optional["FlaskRequestAdapter"]] = ContextVar("fa_request", default=None)
_endpoint_map: dict[str, str] = {}
_FLASH_KEY = "_flashes"


def register_endpoint(name: str, path: str) -> None:
    _endpoint_map[name] = path


def endpoint_path(name: str) -> str | None:
    return _endpoint_map.get(name)


class _ArgsProxy:
    def __init__(self, params):
        self._params = params

    def get(self, key: str, default: Any = None, type: Any = None) -> Any:
        val = self._params.get(key)
        if val is None:
            return default
        if type is not None:
            try:
                return type(val)
            except Exception:
                return default
        return val

    def __getitem__(self, key: str) -> str:
        return self._params[key]

    def __contains__(self, key: object) -> bool:
        return key in self._params

    def to_dict(self) -> dict:
        return dict(self._params)


class _FormProxy:
    def __init__(self, form):
        self._form = form or {}

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self._form, "get"):
            val = self._form.get(key)
        else:
            val = self._form.get(key) if isinstance(self._form, dict) else None
        if val is None:
            return default
        if isinstance(val, UploadFile):
            return val
        return val

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val


class _FilesProxy:
    def __init__(self, form):
        self._form = form

    def get(self, key: str, default: Any = None) -> Any:
        if self._form is None:
            return default
        val = self._form.get(key)
        if isinstance(val, UploadFile):
            return val
        return default

    def __getitem__(self, key: str) -> UploadFile:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val


class _AcceptMimetypes:
    def __init__(self, request: Request):
        self._request = request

    @property
    def best(self) -> str | None:
        accept = self._request.headers.get("accept") or ""
        if "application/json" in accept:
            return "application/json"
        if "text/html" in accept:
            return "text/html"
        return accept.split(",")[0].strip() if accept else None


class FlaskRequestAdapter:
    def __init__(self, request: Request):
        self._request = request
        self.method = request.method
        self.path = request.url.path
        self.url = str(request.url)
        self.host = request.headers.get("host") or (request.client.host if request.client else "")
        self.endpoint = None
        self.args = _ArgsProxy(request.query_params)
        form = getattr(request.state, "form", None)
        self.form = _FormProxy(form)
        self.files = _FilesProxy(form)
        self._json = getattr(request.state, "json", None)
        self._body = getattr(request.state, "body", None)
        self.content_type = request.headers.get("content-type")
        q = request.url.query.encode("utf-8") if request.url.query else b""
        self.query_string = q
        self.accept_mimetypes = _AcceptMimetypes(request)

    @property
    def is_json(self) -> bool:
        ctype = (self.content_type or "").lower()
        return "application/json" in ctype

    def get_json(self, silent: bool = False, force: bool = False) -> Any:
        if self._json is not None:
            return self._json
        if not force and not self.is_json:
            return None
        return None

    def get_data(self, as_text: bool = False) -> bytes | str:
        data = self._body if self._body is not None else b""
        if as_text:
            return data.decode("utf-8", errors="replace")
        return data


class _SessionProxy:
    def get(self, key: str, default: Any = None) -> Any:
        req = _request_var.get()
        if req is None:
            return default
        return req._request.session.get(key, default)

    def pop(self, key: str, default: Any = None) -> Any:
        req = _request_var.get()
        if req is None:
            return default
        return req._request.session.pop(key, default)

    def __setitem__(self, key: str, value: Any) -> None:
        req = _request_var.get()
        if req is None:
            raise RuntimeError("session outside request")
        req._request.session[key] = value

    def __getitem__(self, key: str) -> Any:
        req = _request_var.get()
        if req is None:
            raise RuntimeError("session outside request")
        return req._request.session[key]

    def __contains__(self, key: object) -> bool:
        req = _request_var.get()
        if req is None:
            return False
        return key in req._request.session

    @property
    def modified(self) -> bool:
        return True

    @modified.setter
    def modified(self, _value: bool) -> None:
        return


class _AnonymousUser:
    is_authenticated = False
    is_active = False
    id = None
    email = None
    display_name = None
    is_developer = False


class _CurrentUserProxy:
    def _user(self) -> Any:
        req = _request_var.get()
        if req is None:
            return _AnonymousUser()
        cached = getattr(req._request.state, "current_user", None)
        if cached is not None:
            return cached
        uid = req._request.session.get("user_id")
        if not uid:
            user = _AnonymousUser()
            req._request.state.current_user = user
            return user
        db_user = UserDao().get_by_id(str(uid))
        if db_user is None or not db_user.is_active:
            req._request.session.pop("user_id", None)
            user = _AnonymousUser()
            req._request.state.current_user = user
            return user
        user = FlaskUser(db_user)
        req._request.state.current_user = user
        return user

    def __getattr__(self, name: str) -> Any:
        return getattr(self._user(), name)

    @property
    def is_authenticated(self) -> bool:
        return bool(getattr(self._user(), "is_authenticated", False))


class _CurrentRequestProxy:
    def _req(self) -> FlaskRequestAdapter:
        req = _request_var.get()
        if req is None:
            raise RuntimeError("request outside request context")
        return req

    def __getattr__(self, name: str) -> Any:
        return getattr(self._req(), name)


request = _CurrentRequestProxy()  # type: ignore[assignment]
session = _SessionProxy()
current_user = _CurrentUserProxy()


def bind_request(adapter: FlaskRequestAdapter):
    return _request_var.set(adapter)


def reset_request(token) -> None:
    _request_var.reset(token)


def login_user(user: FlaskUser, remember: bool = True) -> None:
    req = _request_var.get()
    if req is None:
        raise RuntimeError("login_user outside request")
    req._request.session["user_id"] = user.id
    if remember:
        req._request.session["remember"] = True
    req._request.state.current_user = user


def logout_user() -> None:
    req = _request_var.get()
    if req is None:
        return
    req._request.session.pop("user_id", None)
    req._request.session.pop("remember", None)
    req._request.state.current_user = _AnonymousUser()


def flash(message: str, category: str = "message") -> None:
    req = _request_var.get()
    if req is None:
        return
    flashes = list(req._request.session.get(_FLASH_KEY) or [])
    flashes.append((category, message))
    req._request.session[_FLASH_KEY] = flashes


def get_flashed_messages(with_categories: bool = False):
    req = _request_var.get()
    if req is None:
        return []
    flashes = list(req._request.session.pop(_FLASH_KEY, []) or [])
    if with_categories:
        return flashes
    return [m for _c, m in flashes]


def url_for(endpoint: str, **values: Any) -> str:
    if endpoint == "static":
        filename = values.get("filename") or ""
        return f"/static/{filename.lstrip('/')}"
    path = _endpoint_map.get(endpoint)
    if path is None:
        # 常见别名
        aliases = {
            "auth.login": "/login",
            "auth.register": "/register",
            "auth.logout": "/logout",
            "site.index": "/",
            "app2c.home": "/home",
        }
        path = aliases.get(endpoint, f"/{endpoint.replace('.', '/')}")
    # 路径参数替换
    for key, val in list(values.items()):
        token = "{" + key + "}"
        if token in path:
            path = path.replace(token, str(val))
            values.pop(key, None)
    # Flask 风格 <id>
    import re

    for key, val in list(values.items()):
        path2, n = re.subn(rf"<{key}([^>]*)>", str(val), path)
        if n:
            path = path2
            values.pop(key, None)
    query = {k: v for k, v in values.items() if v is not None and k not in ("_external",)}
    if query:
        return f"{path}?{urlencode(query, doseq=True)}"
    return path


def redirect(location: str, code: int = 302) -> RedirectResponse:
    return RedirectResponse(url=location, status_code=code)


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


_templates = None


def set_templates(templates) -> None:
    global _templates
    _templates = templates


def render_template(name: str, **context: Any) -> HTMLResponse:
    if _templates is None:
        raise RuntimeError("templates not configured")
    req = _request_var.get()
    starlette_request = req._request if req else None
    # Jinja2Templates.TemplateResponse needs Request
    if starlette_request is None:
        raise RuntimeError("render_template outside request")
    from deskbot_server.auth.service import get_user_by_id
    from deskbot_server.web.session_device import get_current_device_id

    display_name = None
    current_device_id = None
    is_developer = False
    if current_user.is_authenticated:
        display_name = getattr(current_user, "display_name", None) or current_user.email
        current_device_id = get_current_device_id()
        db_user = get_user_by_id(current_user.id)
        is_developer = bool(db_user and getattr(db_user, "is_developer", False))
    context.setdefault("nav_user_email", current_user.email if current_user.is_authenticated else None)
    context.setdefault("nav_display_name", display_name)
    context.setdefault("nav_current_device_id", current_device_id)
    context.setdefault("nav_is_developer", is_developer)
    context.setdefault("url_for", url_for)
    context.setdefault("get_flashed_messages", get_flashed_messages)
    return _templates.TemplateResponse(starlette_request, name, context)


def login_required(view: Callable):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            path = request.path if _request_var.get() else "/"
            if path.startswith("/api/") or (
                getattr(request, "accept_mimetypes", None) and request.accept_mimetypes.best == "application/json"
            ):
                return jsonify(ok=False, error="unauthorized"), 401
            return redirect(url_for("auth.login", next=path))
        return view(*args, **kwargs)

    return wrapped


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
    if getattr(endpoint, "_flaskish_wrapped", False):
        return endpoint
    import inspect

    if inspect.iscoroutinefunction(endpoint):

        @wraps(endpoint)
        async def async_wrapped(*args, **kwargs):
            return convert_view_result(await endpoint(*args, **kwargs))

        async_wrapped._flaskish_wrapped = True  # type: ignore[attr-defined]
        return async_wrapped

    @wraps(endpoint)
    def sync_wrapped(*args, **kwargs):
        return convert_view_result(endpoint(*args, **kwargs))

    sync_wrapped._flaskish_wrapped = True  # type: ignore[attr-defined]
    return sync_wrapped


class FlaskishAPIRoute(APIRoute):
    """把视图返回值（含 ``(body, status)``）统一转成 Starlette Response。"""

    def __init__(self, *args: Any, **kwargs: Any):
        endpoint = kwargs.get("endpoint")
        if endpoint is not None:
            kwargs["endpoint"] = _wrap_endpoint(endpoint)
        super().__init__(*args, **kwargs)


def wrap_sync_view(view: Callable, *, endpoint_name: str | None = None) -> Callable:
    @wraps(view)
    def endpoint(request: Request, **path_params):
        # FastAPI sync route: already in threadpool; contextvars copied by anyio.
        return convert_view_result(view(**path_params))

    if endpoint_name:
        endpoint.__name__ = endpoint_name.replace(".", "_")
    return endpoint


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
