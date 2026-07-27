"""Controller 鉴权装饰器。

- HTTP（web REST）：``@require_api_auth`` — API Key 或 Web 会话 token
- Device WS：``@require_device_ws`` — 仅要求 ``device_id``；合法 ``pin_code`` 可选缓存供绑定
- Web WS 订阅：``@require_web_ws_subscriber_auth`` — API Key 或 debug_token + 设备归属
- Web WS pipeline：``@require_web_ws_pipeline_auth`` — 订阅走 debug；设备侧仅要求 device_id
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse

from deskbot_server.infrastructure.ws.starlette_compat import StarletteWsCompat
from deskbot_server.service.user_service import UserService
from deskbot_server.utils.util import _extract_device_id, _extract_pin_code, _parse_query, _split_path
from deskbot_server.ws.api_key_gate import ws_require_debug_subscriber_auth
from deskbot_server.ws.device_pin import normalize_pin_code, validate_pin_code

F = TypeVar("F", bound=Callable[..., Any])


def request_qargs(request: Request) -> dict:
    return {k.lower(): v for k, v in request.query_params.multi_items()}


def try_api_auth(request: Request) -> tuple[Any | None, JSONResponse | None]:
    """returns (auth, None) or (None, error_json_response)."""
    from deskbot_server.dao.api_key_service import QuotaExceededError
    from deskbot_server.ws.api_key_gate import QUOTA_MESSAGE, http_require_api_key

    qargs = request_qargs(request)
    try:
        return http_require_api_key(qargs, request.headers), None
    except QuotaExceededError:
        return None, JSONResponse(
            status_code=429, content={"ok": False, "error": "quota_exhausted", "message": QUOTA_MESSAGE}
        )
    except PermissionError:
        return None, JSONResponse(
            status_code=401, content={"ok": False, "error": "api_key_required", "message": "缺少或无效的 API Key"}
        )


def device_access_denied(api_auth: Any, device_id: str | None) -> JSONResponse | None:
    from deskbot_server.ws.api_key_gate import http_require_device_access

    try:
        http_require_device_access(api_auth, device_id)
    except PermissionError:
        msg = "无权操作该设备"
        did = str(device_id or "").strip()
        if did and api_auth is not None and getattr(api_auth, "user_id", None):
            from deskbot_server.ws.device_pin import get_online_pin, normalize_pin_code, validate_pin_code

            dev = UserService().get_device(did)
            if dev is not None and dev.owner_user_id == api_auth.user_id:
                stored = normalize_pin_code(getattr(dev, "pin_code", None))
                online = get_online_pin(did)
                if validate_pin_code(stored) and online and online != stored:
                    msg = "设备 PIN 已变更，请用屏幕上的新 Pin Code 重新绑定后再下发"
        return JSONResponse(status_code=403, content={"ok": False, "error": "forbidden_device", "message": msg})
    return None


def require_api_auth(fn: F) -> F:
    """HTTP 鉴权。失败返回 JSONResponse；成功写入 ``request.state.api_auth``。

    装饰器顺序：``@router.get(...)`` 在上，本装饰器紧贴函数。
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any):
        request = kwargs.get("request")
        if request is None:
            for a in args:
                if isinstance(a, Request):
                    request = a
                    break
        if request is None:
            return JSONResponse(
                status_code=500, content={"ok": False, "error": "missing_request", "message": "缺少 Request"}
            )
        api_auth, err = try_api_auth(request)
        if err is not None:
            return err
        request.state.api_auth = api_auth
        return await fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _find_websocket(args: tuple[Any, ...], kwargs: dict[str, Any]) -> WebSocket | None:
    ws = kwargs.get("websocket")
    if isinstance(ws, WebSocket):
        return ws
    for a in args:
        if isinstance(a, WebSocket):
            return a
    return None


async def _accept_ws_context(websocket: WebSocket) -> tuple[StarletteWsCompat, dict, str | None, str | None]:
    compat = StarletteWsCompat(websocket)
    await compat.accept()
    _path, query = _split_path(compat.path)
    qargs = _parse_query(query)
    device_id = _extract_device_id(qargs)
    pin_code = _extract_pin_code(qargs)
    return compat, qargs, device_id, pin_code


def require_device_ws(fn: F) -> F:
    """设备侧 WS：仅要求 ``device_id``；合法 ``pin_code`` 写入 state 供 online pin 缓存。"""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any):
        websocket = _find_websocket(args, kwargs)
        if websocket is None:
            return
        compat, qargs, device_id, pin_code = await _accept_ws_context(websocket)
        if not device_id:
            await compat.close(code=1008, reason="device_id_required")
            return
        pin = normalize_pin_code(pin_code)
        websocket.state.ws = compat
        websocket.state.qargs = qargs
        websocket.state.device_id = device_id
        websocket.state.pin_code = pin if validate_pin_code(pin) else None
        websocket.state.api_auth = None
        return await fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


# 兼容旧名
require_device_ws_auth = require_device_ws


def require_web_ws_subscriber_auth(fn: F) -> F:
    """Web 调试订阅 WS（如 ``/camera_view``）：API Key 或 debug_token，并要求 device_id 归属。"""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any):
        websocket = _find_websocket(args, kwargs)
        if websocket is None:
            return
        compat, qargs, device_id, _pin = await _accept_ws_context(websocket)
        ok = await ws_require_debug_subscriber_auth(compat, qargs, device_id=device_id, require_device=True)
        if not ok:
            return
        websocket.state.ws = compat
        websocket.state.qargs = qargs
        websocket.state.device_id = device_id
        return await fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def require_web_ws_pipeline_auth(fn: F) -> F:
    """``/device_pipeline``：subscriber 走 debug 鉴权；设备生产者仅要求 device_id。"""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any):
        websocket = _find_websocket(args, kwargs)
        if websocket is None:
            return
        compat, qargs, device_id, pin_code = await _accept_ws_context(websocket)
        role = (qargs.get("role") or "").lower()
        is_subscriber = role in ("subscriber", "sub", "viewer", "consumer")
        if is_subscriber:
            ok = await ws_require_debug_subscriber_auth(compat, qargs, device_id=device_id, require_device=True)
            if not ok:
                return
            websocket.state.api_auth = None
            websocket.state.pin_code = None
        else:
            if not device_id:
                await compat.close(code=1008, reason="device_id_required")
                return
            pin = normalize_pin_code(pin_code)
            websocket.state.pin_code = pin if validate_pin_code(pin) else None
            websocket.state.api_auth = None
        websocket.state.ws = compat
        websocket.state.qargs = qargs
        websocket.state.device_id = device_id
        websocket.state.is_subscriber = is_subscriber
        return await fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
