from __future__ import annotations

import json
import logging
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from flask import Blueprint, Response, jsonify, request
from flask_login import current_user, login_required

from deskbot_server.auth.api_key_service import read_free_api_key_raw
from deskbot_server.auth.debug_ws_token import issue_debug_ws_token
from deskbot_server.auth.device_service import device_ids_for_user, user_owns_device
from deskbot_server.web.helpers import deskbot_upstream_base
from deskbot_server.web.session_device import get_current_device_id

logger = logging.getLogger("deskbot-server")

bp = Blueprint("proxy", __name__, url_prefix="/proxy/deskbot")

_DEVICE_PARAM_KEYS = ("device_id", "deviceid", "device", "id")


def _extract_device_id_from_request() -> str | None:
    for key in _DEVICE_PARAM_KEYS:
        val = (request.args.get(key) or "").strip()
        if val:
            return val
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            val = str(payload.get("device_id") or "").strip()
            if val:
                return val
    return None


def _device_scoped_paths() -> set[str]:
    return {
        "/api/device_servo",
        "/api/device_tts",
        "/api/device_pb_scene",
        "/api/device_pb_anim",
        "/api/device_pb_expr_scene",
        "/api/device_pb_scenes",
        "/api/device_face_play",
    }


def _requires_device_id(path: str) -> bool:
    return path in _device_scoped_paths()


@bp.route("/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@login_required
def proxy_deskbot(subpath: str):
    path = "/" + subpath.lstrip("/")
    method = request.method.upper()

    if method == "OPTIONS":
        return Response(status=204)

    allowed_ids = device_ids_for_user(current_user.id)

    if path == "/api/devices":
        upstream = _forward(method, path, user_id=current_user.id, allowed_ids=allowed_ids)
        if upstream.status_code != 200:
            return upstream
        try:
            data = upstream.get_json()
        except Exception:
            return upstream
        devices = data.get("devices") if isinstance(data, dict) else None
        if isinstance(devices, list):
            filtered = [d for d in devices if str(d.get("device_id") or "") in allowed_ids]
            data["devices"] = filtered
            return jsonify(data)
        return upstream

    device_id = _extract_device_id_from_request()
    if device_id:
        if device_id not in allowed_ids:
            return jsonify({"ok": False, "error": "无权操作该设备"}), 403
    elif _requires_device_id(path):
        current = get_current_device_id()
        if not current:
            return jsonify({"ok": False, "error": "请先选择设备"}), 400
        device_id = current
        if device_id not in allowed_ids:
            return jsonify({"ok": False, "error": "无权操作该设备"}), 403

    if path == "/api/face_profiles/register" and request.is_json:
        payload = request.get_json(silent=True) or {}
        did = str(payload.get("device_id") or "").strip()
        if did and not user_owns_device(current_user.id, did):
            return jsonify({"ok": False, "error": "无权操作该设备"}), 403

    return _forward(method, path, device_id=device_id, user_id=current_user.id, allowed_ids=allowed_ids)


def _upstream_auth_headers(*, user_id: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    uid = str(user_id or "").strip()
    if uid:
        headers["X-Deskbot-Web-Token"] = issue_debug_ws_token(uid).token
        return headers
    upstream_key = read_free_api_key_raw()
    if upstream_key:
        headers["X-API-Key"] = upstream_key
    return headers


def _forward(
    method: str,
    path: str,
    *,
    device_id: str | None = None,
    user_id: str | None = None,
    allowed_ids: set[str] | None = None,
) -> Response:
    base = deskbot_upstream_base().rstrip("/")
    query = request.query_string.decode("utf-8")
    if device_id and "device_id=" not in query:
        extra = urlparse.urlencode({"device_id": device_id})
        query = f"{query}&{extra}" if query else extra
    url = f"{base}{path}"
    if query:
        url = f"{url}?{query}"

    headers = _upstream_auth_headers(user_id=user_id)
    data = None
    if method in ("POST", "PUT", "PATCH"):
        if request.is_json:
            payload = request.get_json(silent=True)
            if isinstance(payload, dict) and device_id and not payload.get("device_id"):
                payload = {**payload, "device_id": device_id}
            data = json.dumps(payload or {}).encode("utf-8")
            headers["Content-Type"] = "application/json"
        else:
            data = request.get_data()
            ctype = request.content_type
            if ctype:
                headers["Content-Type"] = ctype

    req = urlrequest.Request(url, data=data, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=120) as resp:
            body = resp.read()
            status = resp.status
            ctype = resp.headers.get("Content-Type", "application/json")
    except urlerror.HTTPError as exc:
        body = exc.read()
        status = exc.code
        ctype = exc.headers.get("Content-Type", "application/json")
    except Exception as exc:
        logger.warning("proxy 转发失败 %s %s: %s", method, url, exc)
        return jsonify({"ok": False, "error": f"upstream error: {exc}"}), 502

    return Response(body, status=status, content_type=ctype)
