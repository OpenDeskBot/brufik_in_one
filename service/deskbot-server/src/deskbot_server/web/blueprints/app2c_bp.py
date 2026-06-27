from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from deskbot_server.auth.device_service import user_owns_device
from deskbot_server.emotion_expr_map_store import (
    load_emotion_expr_map,
    save_emotion_expr_map,
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
