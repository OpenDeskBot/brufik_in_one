from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from deskbot_server.auth.api_key_service import (
    create_api_key,
    get_api_key_usage_today,
    get_user_device_usage_summary,
    get_user_usage_summary,
    get_user_usage_today,
    list_api_keys_for_user,
    revoke_api_key,
)
from deskbot_server.auth.device_service import list_devices_for_user, user_owns_device
from deskbot_server.auth.service import change_password, get_user_by_id, update_display_name
from deskbot_server.emotion_expr_map_store import (
    load_emotion_expr_map,
    save_emotion_expr_map,
)
from deskbot_server.llm.runtime import resolve_llm_config, resolve_system_llm_config
from deskbot_server.llm_config_store import (
    SUPPORTED_PROTOCOLS,
    get_active_model_id,
    list_llm_models,
)
from deskbot_server.web.session_device import get_current_device_id

# No url_prefix: 2C consumer routes live at root (/home, /voice, /my/*)
bp = Blueprint("app2c", __name__)


@bp.get("/home")
@login_required
def home():
    return render_template("app2c/home.html", active_nav="home")


@bp.get("/voice")
@login_required
def voice():
    return render_template("app2c/voice.html", active_nav="voice")


@bp.get("/expr")
@login_required
def expr():
    return render_template("app2c/expr.html", active_nav="expr")


@bp.get("/my/memories")
@login_required
def memories():
    return render_template("app2c/memories.html", active_nav="memory")


@bp.get("/my/reminders")
@login_required
def reminders():
    return render_template("app2c/reminders.html", active_nav="remind")


@bp.get("/my/people")
@login_required
def people():
    return render_template("app2c/people.html", active_nav="people")


@bp.get("/my/devices")
@login_required
def devices():
    return render_template("app2c/devices.html", active_nav="device")


@bp.get("/advanced")
@login_required
def advanced():
    return render_template("app2c/advanced.html", active_nav="advanced")


def _totals_payload(row: dict) -> dict:
    return {
        "asr_bytes": int(row.get("asr_bytes") or 0),
        "face_bytes": int(row.get("face_bytes") or 0),
        "llm_bytes": int(row.get("llm_bytes") or 0),
        "tts_bytes": int(row.get("tts_bytes") or 0),
        "total_bytes": int(row.get("total_bytes") or 0),
        "quota_bytes": int(row.get("quota_bytes") or 0),
    }


def _api_key_payload(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.key_prefix,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "daily_quota_bytes": int(row.daily_quota_bytes or 0),
        "today": _totals_payload(get_api_key_usage_today(row.id)),
    }


@bp.get("/api/advanced")
@login_required
def advanced_summary_get():
    user = get_user_by_id(current_user.id)
    devices = list_devices_for_user(current_user.id)
    current_device_id = get_current_device_id()
    keys = list_api_keys_for_user(current_user.id)
    usage = get_user_usage_summary(current_user.id, days=14)
    device_usage = get_user_device_usage_summary(current_user.id, days=14)
    user_today = get_user_usage_today(current_user.id)

    llm = {
        "device_id": current_device_id,
        "protocols": list(SUPPORTED_PROTOCOLS),
        "models": [],
        "active_model_id": None,
        "active": None,
        "system_default": None,
        "error": "",
    }
    system_default = resolve_system_llm_config()
    llm["system_default"] = {
        "display_name": system_default.display_name,
        "model": system_default.model,
        "api_base": system_default.api_base or "",
    }
    if current_device_id and user_owns_device(current_user.id, current_device_id):
        llm["models"] = list_llm_models(current_device_id, mask_key=True)
        llm["active_model_id"] = get_active_model_id(current_device_id)
        try:
            resolved = resolve_llm_config(current_device_id)
            llm["active"] = {
                "display_name": resolved.display_name,
                "model": resolved.model,
                "source": resolved.source,
                "api_base": resolved.api_base or "",
            }
        except ValueError as exc:
            llm["error"] = str(exc)
    elif current_device_id:
        llm["error"] = "设备不属于当前账号"
    else:
        llm["error"] = "请先选择设备"

    return jsonify(
        {
            "ok": True,
            "user": {
                "email": getattr(user, "email", "") if user else "",
                "display_name": getattr(user, "display_name", "") if user else "",
            },
            "devices": [
                {
                    "device_id": d.device_id,
                    "display_name": d.display_name or d.device_id,
                    "is_current": d.device_id == current_device_id,
                }
                for d in devices
            ],
            "current_device_id": current_device_id,
            "usage": {
                "today": _totals_payload(user_today),
                "fourteen_day": _totals_payload(usage.get("totals") or {}),
                "today_by_device": device_usage.get("today_by_device") or [],
            },
            "api_keys": [_api_key_payload(k) for k in keys],
            "llm": llm,
        }
    )


@bp.patch("/api/advanced/profile")
@login_required
def advanced_profile_patch():
    payload = request.get_json(silent=True) or {}
    try:
        update_display_name(current_user.id, str(payload.get("display_name") or ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    user = get_user_by_id(current_user.id)
    return jsonify(
        {
            "ok": True,
            "user": {
                "email": getattr(user, "email", ""),
                "display_name": getattr(user, "display_name", "") or "",
            },
        }
    )


@bp.post("/api/advanced/password")
@login_required
def advanced_password_post():
    payload = request.get_json(silent=True) or {}
    old_password = str(payload.get("old_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirm = str(payload.get("confirm_password") or "")
    if new_password != confirm:
        return jsonify({"ok": False, "error": "两次新密码不一致"}), 400
    try:
        change_password(current_user.id, old_password, new_password)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.post("/api/advanced/api-keys")
@login_required
def advanced_api_key_post():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "default").strip()
    try:
        raw, row = create_api_key(current_user.id, name=name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "raw_key": raw, "api_key": _api_key_payload(row)})


@bp.delete("/api/advanced/api-keys/<key_id>")
@login_required
def advanced_api_key_delete(key_id: str):
    if not revoke_api_key(current_user.id, key_id):
        return jsonify({"ok": False, "error": "API Key 不存在"}), 404
    return jsonify({"ok": True})


def _owned_device_or_error():
    device_id = (request.args.get("device_id") or get_current_device_id() or "").strip()
    if not device_id:
        return None, (jsonify({"ok": False, "error": "请先选择设备"}), 400)
    if not user_owns_device(current_user.id, device_id):
        return None, (jsonify({"ok": False, "error": "设备不属于当前账号"}), 403)
    return device_id, None


@bp.get("/api/emotion_expr_map")
@login_required
def emotion_expr_map_get():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    return jsonify({"ok": True, "device_id": device_id, "map": load_emotion_expr_map(device_id=device_id)})


@bp.post("/api/emotion_expr_map")
@login_required
def emotion_expr_map_post():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    mapping = payload.get("map")
    if not isinstance(mapping, dict):
        return jsonify({"ok": False, "error": "map 必须是对象"}), 400
    try:
        saved = save_emotion_expr_map(mapping, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "map": saved})
