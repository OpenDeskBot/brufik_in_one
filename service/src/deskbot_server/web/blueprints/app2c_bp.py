from __future__ import annotations

import mimetypes

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
from deskbot_server.face_expr_scenes_store import (
    load_face_expr_scenes_file,
    save_face_expr_scenes_file,
)
from deskbot_server.face_mouth_config_store import (
    load_face_mouth_cfg_file,
    save_face_mouth_cfg_file,
)
from deskbot_server.llm.env_store import save_llm_env
from deskbot_server.llm.runtime import (
    ResolvedLlmConfig,
    build_chat_model,
    chat_completion,
    resolve_llm_config,
    resolve_system_llm_config,
)
from deskbot_server.llm_config_store import (
    SUPPORTED_PROTOCOLS,
    get_active_model_id,
    list_llm_models,
)
from deskbot_server.web.blueprints.app_bp import _flatten_usage_daily_rows
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


@bp.get("/onboarding")
@login_required
def onboarding():
    return render_template("app2c/onboarding.html")


def _system_llm_payload() -> dict:
    sys = resolve_system_llm_config()
    api_key_set = _llm_api_key_set(sys.api_key)
    return {
        "config": {
            "display_name": sys.display_name,
            "model_name": sys.model,
            "protocol": sys.protocol,
            "base_url": sys.api_base or "",
            "api_key_set": api_key_set,
        },
        "protocols": list(SUPPORTED_PROTOCOLS),
        "needs_config": not api_key_set,
    }


@bp.get("/api/setup/llm")
@login_required
def setup_llm_get():
    return jsonify({"ok": True, **_system_llm_payload()})


@bp.post("/api/setup/llm")
@login_required
def setup_llm_post():
    payload = request.get_json(silent=True) or {}
    model_name = str(payload.get("model_name") or "").strip()
    protocol = str(payload.get("protocol") or "ark").strip().lower() or "ark"
    if not model_name:
        return jsonify({"ok": False, "error": "请填写模型 ID / 推理接入点"}), 400
    if protocol not in SUPPORTED_PROTOCOLS:
        return jsonify({"ok": False, "error": f"不支持的协议: {protocol}"}), 400
    save_llm_env(
        {
            "api_key": payload.get("api_key"),
            "protocol": protocol,
            "model_name": model_name,
            "base_url": payload.get("base_url"),
        }
    )
    result = _system_llm_payload()
    if result["needs_config"]:
        return jsonify({"ok": False, "error": "API Key 未生效，请确认已填写 ARK_API_KEY"}), 400
    return jsonify({"ok": True, **result})


@bp.post("/api/setup/llm/test")
@login_required
def setup_llm_test():
    payload = request.get_json(silent=True) or {}
    current = resolve_system_llm_config()
    model_name = str(payload.get("model_name") or "").strip() or current.model
    protocol = str(payload.get("protocol") or current.protocol or "ark").strip().lower() or "ark"
    base_url = str(payload.get("base_url") or "").strip() or (current.api_base or "")
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key or "*" in api_key or "•" in api_key:
        api_key = str(current.api_key or "").strip()
    prompt = str(payload.get("prompt") or "你好，请用一句话介绍你自己。").strip()

    if not model_name:
        return jsonify({"ok": False, "error": "请填写模型 ID / 推理接入点"}), 400
    if protocol not in SUPPORTED_PROTOCOLS:
        return jsonify({"ok": False, "error": f"不支持的协议: {protocol}"}), 400
    if not _llm_api_key_set(api_key):
        return jsonify({"ok": False, "error": "请填写 ARK_API_KEY"}), 400

    try:
        config = ResolvedLlmConfig(
            model=build_chat_model(protocol, model_name),
            api_key=api_key,
            api_base=base_url or None,
            protocol=protocol,
            source="test",
            display_name=model_name,
        )
        reply, meta = chat_completion(
            [{"role": "user", "content": prompt}],
            config=config,
            json_mode=False,
            temperature=0.7,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - surface provider error to the user
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify(
        {
            "ok": True,
            "reply": reply,
            "meta": {"model": meta.get("model"), "display_name": meta.get("display_name")},
        }
    )


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


def _llm_api_key_set(api_key: str | None) -> bool:
    key = str(api_key or "").strip()
    return bool(key) and "请替换" not in key


def _llm_config_message(*, device_selected: bool, api_key_set: bool, source: str = "") -> str:
    if not device_selected:
        return "请先选择设备，然后在「LLM 模型」里完成大模型配置。"
    if api_key_set:
        return ""
    if source == "system":
        return (
            "需要完成大模型配置：当前使用系统默认模型，但没有可用 API Key。"
            "请展开配置，填写 Ark 模型 ID 与 ARK_API_KEY，保存并设为当前；"
            "也可以在环境变量里设置 LLM_API_KEY 或 ARK_API_KEY。"
        )
    return "需要完成大模型配置：请展开配置，填写模型 ID 与 ARK_API_KEY，保存并设为当前。"


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
    device_daily_rows = _flatten_usage_daily_rows(
        device_usage.get("device_stats") or [],
        label_key="display_name",
        sub_id_key="device_id",
    )
    key_daily_rows = _flatten_usage_daily_rows(
        usage.get("key_stats") or [],
        label_key="name",
        sub_id_key="api_key_id",
        sub_label_key="key_prefix",
    )

    llm = {
        "device_id": current_device_id,
        "protocols": list(SUPPORTED_PROTOCOLS),
        "models": [],
        "active_model_id": None,
        "active": None,
        "system_default": None,
        "error": "",
        "needs_config": True,
        "config_message": _llm_config_message(device_selected=bool(current_device_id), api_key_set=False),
    }
    system_default = resolve_system_llm_config()
    llm["system_default"] = {
        "display_name": system_default.display_name,
        "model": system_default.model,
        "api_base": system_default.api_base or "",
        "api_key_set": _llm_api_key_set(system_default.api_key),
    }
    if current_device_id and user_owns_device(current_user.id, current_device_id):
        llm["models"] = list_llm_models(current_device_id, mask_key=True)
        llm["active_model_id"] = get_active_model_id(current_device_id)
        try:
            resolved = resolve_llm_config(current_device_id)
            api_key_set = _llm_api_key_set(resolved.api_key)
            llm["active"] = {
                "display_name": resolved.display_name,
                "model": resolved.model,
                "source": resolved.source,
                "api_base": resolved.api_base or "",
                "api_key_set": api_key_set,
            }
            llm["needs_config"] = not api_key_set
            llm["config_message"] = _llm_config_message(
                device_selected=True,
                api_key_set=api_key_set,
                source=resolved.source,
            )
        except ValueError as exc:
            llm["error"] = str(exc)
            llm["needs_config"] = True
            llm["config_message"] = str(exc)
    elif current_device_id:
        llm["error"] = "设备不属于当前账号"
        llm["needs_config"] = True
        llm["config_message"] = "设备不属于当前账号，无法配置大模型。"
    else:
        llm["error"] = "请先选择设备"
        llm["needs_config"] = True
        llm["config_message"] = _llm_config_message(device_selected=False, api_key_set=False)

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
                "device_daily_rows": device_daily_rows,
                "key_daily_rows": key_daily_rows,
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
    device_id = (request.args.get("device_id") or "").strip()
    if not device_id and request.is_json:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        device_id = (get_current_device_id() or "").strip()
    if not device_id:
        return None, (jsonify({"ok": False, "error": "请先选择设备"}), 400)
    if not user_owns_device(current_user.id, device_id):
        return None, (jsonify({"ok": False, "error": "设备不属于当前账号"}), 403)
    return device_id, None


def _image_mime_from_upload(filename: str, content_type: str, image_bytes: bytes) -> str:
    mime_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime_type.startswith("image/"):
        return mime_type
    guessed = mimetypes.guess_type(filename or "")[0] or ""
    guessed = guessed.split(";", 1)[0].strip().lower()
    if guessed.startswith("image/"):
        return guessed
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return mime_type


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


@bp.get("/api/face_expr_scenes")
@login_required
def face_expr_scenes_get():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    try:
        rows = load_face_expr_scenes_file(seed_if_missing=True, device_id=device_id) or []
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "device_id": device_id, "config": rows})


@bp.post("/api/face_expr_scenes")
@login_required
def face_expr_scenes_post():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    scenes = payload.get("scenes")
    if scenes is None:
        scenes = payload.get("config")
    if not isinstance(scenes, list):
        return jsonify({"ok": False, "error": "scenes 必须是数组"}), 400
    try:
        saved = save_face_expr_scenes_file(scenes, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "device_id": device_id, "config": saved})


@bp.get("/api/face_mouth_by_phoneme")
@login_required
def face_mouth_by_phoneme_get():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    try:
        groups = load_face_mouth_cfg_file(seed_if_missing=True, device_id=device_id) or []
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "device_id": device_id, "mouth_by_phoneme_groups": groups})


@bp.post("/api/face_mouth_by_phoneme")
@login_required
def face_mouth_by_phoneme_post():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    payload = request.get_json(silent=True) or {}
    groups = payload.get("mouth_by_phoneme_groups")
    if not isinstance(groups, list):
        return jsonify({"ok": False, "error": "mouth_by_phoneme_groups 必须是数组"}), 400
    try:
        save_face_mouth_cfg_file(groups, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "device_id": device_id, "mouth_by_phoneme_groups": groups})


@bp.post("/api/face_design/generate-from-image")
@login_required
def face_design_generate_from_image_post():
    device_id, err = _owned_device_or_error()
    if err:
        return err
    upload = request.files.get("image") or request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "请先上传图片"}), 400
    image_bytes = upload.read()
    prompt = str(request.form.get("prompt") or "").strip()
    mime_type = _image_mime_from_upload(upload.filename, upload.mimetype or upload.content_type or "", image_bytes)
    try:
        from deskbot_server.ark_face_svg import generate_face_svg_from_image

        result = generate_face_svg_from_image(image_bytes, mime_type, prompt=prompt)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({"device_id": device_id, **result})
