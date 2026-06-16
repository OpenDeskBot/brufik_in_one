from __future__ import annotations

import json
import logging

from deskbot_server.auth.api_key_service import (
    ApiKeyAuth,
    QuotaExceededError,
    authenticate_api_key,
    extract_api_key_from_headers,
    extract_api_key_from_query,
)
from deskbot_server.auth.debug_ws_token import (
    extract_debug_token_from_query,
    verify_debug_ws_token,
)

logger = logging.getLogger("deskbot-server")

QUOTA_MESSAGE = "今日配额已用完（1GB/日），请明日再试或充值购买"


def resolve_api_key(*, qargs: dict, headers) -> ApiKeyAuth | None:
    raw = extract_api_key_from_query(qargs) or extract_api_key_from_headers(headers)
    if not raw:
        return None
    return authenticate_api_key(raw)


def ws_auth_failure_response(*, reason: str, message: str) -> tuple[int, list, bytes]:
    body = json.dumps({"ok": False, "error": reason, "message": message}, ensure_ascii=False)
    return (
        401,
        [("Content-Type", "application/json"), ("Access-Control-Allow-Origin", "*")],
        body.encode("utf-8"),
    )


def ws_quota_failure_response() -> tuple[int, list, bytes]:
    return ws_auth_failure_response(reason="quota_exhausted", message=QUOTA_MESSAGE)


async def ws_require_debug_subscriber_auth(
    websocket,
    qargs: dict,
    *,
    device_id: str | None = None,
    require_device: bool = False,
) -> bool:
    """调试订阅 WS：API Key 或已登录用户签发的 ``debug_token``。成功返回 True。"""
    headers = getattr(websocket, "request", None)
    hdr_map = headers.headers if headers is not None else {}
    auth = resolve_api_key(qargs=qargs, headers=hdr_map)
    if auth is not None:
        try:
            from deskbot_server.auth.api_key_service import check_quota

            check_quota(auth.api_key_id, 0)
        except QuotaExceededError:
            await websocket.close(code=1008, reason="quota_exhausted")
            return False
        return True

    raw_token = extract_debug_token_from_query(qargs)
    if not raw_token:
        logger.warning("debug subscriber WS rejected: auth_required device_id=%s", device_id)
        await websocket.close(code=1008, reason="auth_required")
        return False

    user_id = verify_debug_ws_token(raw_token)
    if not user_id:
        logger.warning("debug subscriber WS rejected: invalid_debug_token device_id=%s", device_id)
        await websocket.close(code=1008, reason="invalid_debug_token")
        return False

    did = str(device_id or "").strip()
    if require_device and not did:
        logger.warning("debug subscriber WS rejected: device_id_required user_id=%s", user_id)
        await websocket.close(code=1008, reason="device_id_required")
        return False
    if did:
        from deskbot_server.auth.device_service import user_owns_device

        try:
            allowed = user_owns_device(user_id, did)
        except Exception:
            logger.exception(
                "debug subscriber WS device ownership check failed user_id=%s device_id=%s",
                user_id,
                did,
            )
            await websocket.close(code=1008, reason="auth_db_error")
            return False
        if not allowed:
            logger.warning(
                "debug subscriber WS rejected: forbidden_device user_id=%s device_id=%s",
                user_id,
                did,
            )
            await websocket.close(code=1008, reason="forbidden_device")
            return False

    logger.info("debug_token WS auth user_id=%s device_id=%s", user_id, did or None)
    return True


async def ws_require_api_key(websocket, qargs: dict) -> ApiKeyAuth | None:
    headers = getattr(websocket, "request", None)
    hdr_map = headers.headers if headers is not None else {}
    auth = resolve_api_key(qargs=qargs, headers=hdr_map)
    if auth is None:
        await websocket.close(code=1008, reason="api_key_required")
        return None
    try:
        from deskbot_server.auth.api_key_service import check_quota

        check_quota(auth.api_key_id, 0)
    except QuotaExceededError:
        await websocket.close(code=1008, reason="quota_exhausted")
        return None
    return auth


def http_require_api_key(qargs: dict, headers) -> ApiKeyAuth:
    auth = resolve_api_key(qargs=qargs, headers=headers)
    if auth is None:
        raise PermissionError("api_key_required")
    from deskbot_server.auth.api_key_service import check_quota

    check_quota(auth.api_key_id, 0)
    return auth


def http_require_device_access(auth: ApiKeyAuth | None, device_id: str | None) -> None:
    """用户绑定的 API Key 仅能操作本人已绑定设备；免费 Key（无 user_id）不校验设备归属。"""
    if auth is None or not auth.user_id:
        return
    did = str(device_id or "").strip()
    if not did:
        return
    from deskbot_server.auth.device_service import user_owns_device

    if not user_owns_device(auth.user_id, did):
        raise PermissionError("forbidden_device")


def record_turn_usage(
    api_key_id: str,
    *,
    device_id: str | None = None,
    asr_bytes: int = 0,
    face_bytes: int = 0,
    llm_bytes: int = 0,
    tts_bytes: int = 0,
) -> None:
    from deskbot_server.auth.api_key_service import QuotaExceededError, record_usage_checked

    try:
        if asr_bytes:
            record_usage_checked(api_key_id, "asr", asr_bytes, device_id=device_id)
        if face_bytes:
            record_usage_checked(api_key_id, "face", face_bytes, device_id=device_id)
        if llm_bytes:
            record_usage_checked(api_key_id, "llm", llm_bytes, device_id=device_id)
        if tts_bytes:
            record_usage_checked(api_key_id, "tts", tts_bytes, device_id=device_id)
    except QuotaExceededError:
        logger.warning("API Key %s 配额耗尽 device_id=%s", api_key_id, device_id)
