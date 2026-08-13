"""Controller 鉴权装饰器。

- HTTP（web REST）：``@require_api_auth`` — Web 会话 token
- Device WS：``@require_device_ws`` — 仅要求 ``device_id``；合法 ``pin_code`` 可选缓存供绑定
- Web WS 订阅：``@require_web_ws_subscriber_auth`` — debug_token + 设备归属
- Web WS pipeline：``@require_web_ws_pipeline_auth`` — 订阅走 debug；设备侧仅要求 device_id
"""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse

from deskbot_server.infrastructure.ws.starlette_compat import StarletteWsCompat
from deskbot_server.utils.pin_code import normalize_pin_code, validate_pin_code
from deskbot_server.utils.util import _extract_device_id, _extract_pin_code, _parse_query, _split_path

F = TypeVar("F", bound=Callable[..., Any])


def _resolve_user_id_from_session(request_or_qargs: Any, headers: Any = None) -> str | None:
    """从 web session token 解析 user_id。"""
    from deskbot_server.auth.debug_ws_token import extract_debug_token_from_query, verify_debug_ws_token

    qargs = request_or_qargs if isinstance(request_or_qargs, dict) else {}
    raw_token = extract_debug_token_from_query(qargs)
    if not raw_token and headers is not None:
        for key in ("X-Deskbot-Web-Token", "X-Deskbot-Debug-Token"):
            val = str(headers.get(key) or "").strip()
            if val:
                raw_token = val
                break
    if not raw_token:
        return None
    return verify_debug_ws_token(raw_token)


def request_qargs(request: Request) -> dict:
    return {k.lower(): v for k, v in request.query_params.multi_items()}


def require_api_auth(fn: F) -> F:
    """HTTP 鉴权：Web 会话 token。成功写入 ``request.state.user_id``。

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

        # 优先从 web session（cookie）获取 user_id
        uid = request.session.get("user_id") if hasattr(request, "session") else None
        if not uid:
            # 回退到 debug_token
            qargs = request_qargs(request)
            uid = _resolve_user_id_from_session(qargs, request.headers)

        request.state.user_id = str(uid).strip() if uid else None
        return await fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def device_access_denied(user_id: str | None, device_id: str | None) -> JSONResponse | None:
    """检查用户是否有权操作指定设备。无 user_id 时放行（免费/匿名访问）。"""
    if not user_id:
        return None
    did = str(device_id or "").strip()
    if not did:
        return None
    from deskbot_server.service.user_service import UserService

    if not UserService().user_owns_device(user_id, did):
        msg = "无权操作该设备"
        from deskbot_server.ws.device_pin import get_online_pin

        dev = UserService().get_device(did)
        if dev is not None and dev.owner_user_id == user_id:
            stored = normalize_pin_code(getattr(dev, "pin_code", None))
            online = get_online_pin(did)
            if validate_pin_code(stored) and online and online != stored:
                msg = "设备 PIN 已变更，请用屏幕上的新 Pin Code 重新绑定后再下发"
        return JSONResponse(status_code=403, content={"ok": False, "error": "forbidden_device", "message": msg})
    return None


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
        return await fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


# 兼容旧名
require_device_ws_auth = require_device_ws


async def _ws_require_debug_auth(
    websocket, qargs: dict, *, device_id: str | None = None, require_device: bool = False
) -> bool:
    """调试订阅 WS：web session debug_token。成功返回 True。"""
    uid = _resolve_user_id_from_session(qargs)
    if not uid:
        import logging

        logger = logging.getLogger("deskbot-server")
        logger.warning("debug subscriber WS rejected: auth_required device_id=%s", device_id)
        await websocket.close(code=1008, reason="auth_required")
        return False

    did = str(device_id or "").strip()
    if require_device and not did:
        import logging

        logger = logging.getLogger("deskbot-server")
        logger.warning("debug subscriber WS rejected: device_id_required user_id=%s", uid)
        await websocket.close(code=1008, reason="device_id_required")
        return False
    if did:
        from deskbot_server.service.user_service import UserService

        try:
            allowed = UserService().user_owns_device(uid, did)
        except Exception:
            import logging

            logger = logging.getLogger("deskbot-server")
            logger.exception("debug subscriber WS device ownership check failed user_id=%s device_id=%s", uid, did)
            await websocket.close(code=1008, reason="auth_db_error")
            return False
        if not allowed:
            import logging

            logger = logging.getLogger("deskbot-server")
            logger.warning("debug subscriber WS rejected: forbidden_device user_id=%s device_id=%s", uid, did)
            await websocket.close(code=1008, reason="forbidden_device")
            return False

    import logging

    logger = logging.getLogger("deskbot-server")
    logger.info("debug_token WS auth user_id=%s device_id=%s", uid, did or None)
    return True


def require_web_ws_subscriber_auth(fn: F) -> F:
    """Web 调试订阅 WS（如 ``/camera_view``）：debug_token，并要求 device_id 归属。"""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any):
        websocket = _find_websocket(args, kwargs)
        if websocket is None:
            return
        compat, qargs, device_id, _pin = await _accept_ws_context(websocket)
        ok = await _ws_require_debug_auth(compat, qargs, device_id=device_id, require_device=True)
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
            ok = await _ws_require_debug_auth(compat, qargs, device_id=device_id, require_device=True)
            if not ok:
                return
            websocket.state.pin_code = None
        else:
            if not device_id:
                await compat.close(code=1008, reason="device_id_required")
                return
            pin = normalize_pin_code(pin_code)
            websocket.state.pin_code = pin if validate_pin_code(pin) else None
        websocket.state.ws = compat
        websocket.state.qargs = qargs
        websocket.state.device_id = device_id
        websocket.state.is_subscriber = is_subscriber
        return await fn(*args, **kwargs)

    return wrapper  # type: ignore[return-value]
