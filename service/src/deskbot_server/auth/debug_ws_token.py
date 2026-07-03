"""已登录调试台 WebSocket 短期令牌（替代免费 API Key）。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SALT = "deskbot-debug-ws-v1"


def _default_ttl_days() -> int:
    raw = (os.environ.get("DESKBOT_DEBUG_WS_TOKEN_DAYS") or "7").strip()
    try:
        days = int(raw)
    except ValueError:
        days = 7
    return max(1, min(days, 30))


def _secret_key() -> str:
    return (os.environ.get("DESKBOT_WEB_SECRET_KEY") or "").strip() or "dev-insecure-change-me"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret_key(), salt=_SALT)


@dataclass(frozen=True)
class DebugWsTokenInfo:
    token: str
    user_id: str
    expires_in: int


def issue_debug_ws_token(user_id: str) -> DebugWsTokenInfo:
    uid = str(user_id or "").strip()
    if not uid:
        raise ValueError("user_id required")
    expires_in = _default_ttl_days() * 86400
    token = _serializer().dumps({"uid": uid})
    return DebugWsTokenInfo(token=token, user_id=uid, expires_in=expires_in)


def verify_debug_ws_token(token: str) -> str | None:
    raw = str(token or "").strip()
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=_default_ttl_days() * 86400)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    uid = str(data.get("uid") or "").strip()
    return uid or None


def extract_debug_token_from_query(qargs: dict) -> str | None:
    for key in ("debug_token", "debugtoken", "ws_token"):
        val = str(qargs.get(key) or "").strip()
        if val:
            return val
    return None
