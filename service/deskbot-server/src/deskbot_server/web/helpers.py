from __future__ import annotations

import datetime as _dt
import json
import os
import socket
import time

from flask import request

from deskbot_server.config import load_config as _load_config_yaml, save_config as _save_config_yaml
from deskbot_server.paths import DEFAULT_CONFIG_PATH
from deskbot_server.util import pcm_to_wav_bytes

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

CONFIG_PATH = DEFAULT_CONFIG_PATH


def load_config():
    return _load_config_yaml(str(CONFIG_PATH))


def save_config(cfg):
    _save_config_yaml(cfg, CONFIG_PATH)


def tcp_alive(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _prefer_access_host(raw_host: str) -> str:
    host = (raw_host or "").strip()
    if host and host not in ("0.0.0.0", "::", "127.0.0.1", "localhost"):
        return host
    req_host = (request.host or "").split(":", 1)[0].strip()
    if req_host and req_host not in ("0.0.0.0", "::", "localhost"):
        return req_host
    return "127.0.0.1"


def deskbot_ws_base() -> tuple[str, int]:
    cfg = load_config()
    srv = cfg.get("server") or {}
    public = (os.environ.get("DESKBOT_WEB_PUBLIC_HOST") or "").strip() or str(
        srv.get("web_public_host") or ""
    ).strip()
    if public:
        host = public
    else:
        host = _prefer_access_host(
            os.environ.get("DESKBOT_SERVER_HOST") or srv.get("host", "127.0.0.1")
        )
    port = int(os.environ.get("DESKBOT_SERVER_PORT") or srv.get("port", 9000))
    return host, port


def deskbot_ws_default() -> str:
    cfg = load_config()
    host, port = deskbot_ws_base()
    ws_path = os.environ.get("DESKBOT_WS_PATH") or cfg.get("server", {}).get("ws_path", "/asr_chat")
    if not str(ws_path).startswith("/"):
        ws_path = f"/{ws_path}"
    return f"ws://{host}:{port}{ws_path}"


def device_pipeline_ws_base() -> str:
    host, port = deskbot_ws_base()
    return f"ws://{host}:{port}/device_pipeline"


def camera_view_ws_base() -> str:
    host, port = deskbot_ws_base()
    return f"ws://{host}:{port}/camera_view"


def deskbot_http_base() -> str:
    return "/proxy/deskbot"


def _fetch_upstream_devices(*, user_id: str | None = None, timeout: float = 1.5) -> list[dict]:
    from urllib import error as urlerror
    from urllib import parse as urlparse
    from urllib import request as urlrequest

    base = deskbot_upstream_base().rstrip("/")
    url = f"{base}/api/devices"
    headers = {"Accept": "application/json"}
    query = ""
    uid = str(user_id or "").strip()
    if uid:
        from deskbot_server.auth.debug_ws_token import issue_debug_ws_token

        tok = issue_debug_ws_token(uid).token
        query = urlparse.urlencode({"debug_token": tok})
    else:
        from deskbot_server.auth.api_key_service import read_free_api_key_raw

        upstream_key = read_free_api_key_raw()
        if upstream_key:
            headers["X-API-Key"] = upstream_key
    if query:
        url = f"{url}?{query}"
    req = urlrequest.Request(url, headers=headers, method="GET")
    try:
        with urlrequest.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urlerror.URLError, urlerror.HTTPError, json.JSONDecodeError, OSError, TimeoutError):
        return []
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, list):
        return []
    return [d for d in devices if isinstance(d, dict)]


def fetch_live_device_details(*, user_id: str | None = None, timeout: float = 1.5) -> dict[str, dict]:
    """查询 deskbot-server 内存注册表，返回 device_id -> {online, last_seen}。"""
    out: dict[str, dict] = {}
    for row in _fetch_upstream_devices(user_id=user_id, timeout=timeout):
        did = str(row.get("device_id") or "").strip()
        if not did:
            continue
        out[did] = {
            "online": bool(row.get("online")),
            "last_seen": str(row.get("last_seen") or "—"),
        }
    return out


def fetch_online_device_map(*, user_id: str | None = None, timeout: float = 1.5) -> dict[str, bool]:
    """查询 deskbot-server 内存注册表，返回 device_id -> online。"""
    return {
        did: bool(info.get("online"))
        for did, info in fetch_live_device_details(user_id=user_id, timeout=timeout).items()
    }


def deskbot_upstream_base() -> str:
    cfg = load_config()
    srv = cfg.get("server") or {}
    host = os.environ.get("DESKBOT_SERVER_HOST") or srv.get("host", "127.0.0.1")
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = int(os.environ.get("DESKBOT_SERVER_PORT") or srv.get("port", 9000))
    return f"http://{host}:{port}"


def beijing_time_str() -> str:
    if ZoneInfo is not None:
        now = _dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    else:
        now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    return now.strftime("%Y-%m-%d %H:%M:%S") + " " + weekdays[now.weekday()]


def resolve_llm_api_key() -> str:
    return (
        os.environ.get("LLM_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("QWEN_API_KEY")
        or ""
    )


ALLOWED_LLM_ROLES = {"system", "user", "assistant"}
