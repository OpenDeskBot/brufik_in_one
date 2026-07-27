from __future__ import annotations

import mimetypes

from fastapi import APIRouter, Request

from deskbot_server.web.deps import RequireUser
from deskbot_server.web.urls import flash, url_for
from deskbot_server.web.view_helpers import ViewAPIRoute, files_get, form_get, get_json, is_json_request, jsonify, redirect, render_template

from deskbot_server.service.user_service import UserService
from deskbot_server.dao.api_key_service import (
    create_api_key,
    get_api_key_usage_today,
    get_user_device_usage_summary,
    get_user_usage_summary,
    get_user_usage_today,
    list_api_keys_for_user,
    revoke_api_key,
)
from deskbot_server.dao.emotion_expr_map_store import load_emotion_expr_map, save_emotion_expr_map
from deskbot_server.dao.face_expr_scenes_store import load_face_expr_scenes_file, save_face_expr_scenes_file
from deskbot_server.dao.face_mouth_config_store import load_face_mouth_cfg_file, save_face_mouth_cfg_file
from deskbot_server.dao.llm_config_store import SUPPORTED_PROTOCOLS, get_active_model_id, list_llm_models
from deskbot_server.infrastructure.llm.env_store import save_llm_env
from deskbot_server.infrastructure.llm.runtime import (
    ResolvedLlmConfig,
    build_chat_model,
    chat_completion,
    resolve_llm_config,
    resolve_system_llm_config,
)
from deskbot_server.web.blueprints.app_bp import _flatten_usage_daily_rows
from deskbot_server.web.helpers import camera_view_ws_base, device_pipeline_ws_base
from deskbot_server.web.session_device import get_current_device_id

# No url_prefix: 2C consumer routes live at root (/home, /voice, /my/*)
router = APIRouter(route_class=ViewAPIRoute, tags=["app2c"])


def _default_robot_face_payload() -> dict:
    from deskbot_server.pb.display import FACE_LCD_HEIGHT, FACE_LCD_WIDTH
    from deskbot_server.pb.shapes import _default_mouth_fallback_shape, default_face_circles

    face = default_face_circles()
    return {
        "face_lcd_w": FACE_LCD_WIDTH,
        "face_lcd_h": FACE_LCD_HEIGHT,
        "expr_default_anim": {
            "elements": {
                "nose": face.get("nose") or [],
                "eye_l": face.get("eye_l") or [],
                "eye_r": face.get("eye_r") or [],
                "mouth": (_default_mouth_fallback_shape().get("elements") or []),
                "extra": [],
            }
        },
    }


@router.get("/home")
def home(request: Request, user: RequireUser):
    return render_template(request, 
        "app2c/home.html", active_nav="home", camera_view_ws_base=camera_view_ws_base(), **_default_robot_face_payload()
    )


@router.get("/voice")
def voice(request: Request, user: RequireUser):
    return render_template(request, "app2c/voice.html", active_nav="voice")


@router.get("/expr")
def expr(request: Request, user: RequireUser):
    return render_template(request, "app2c/expr.html", active_nav="expr")


@router.get("/lab")
def lab(request: Request, user: RequireUser):
    return render_template(request, 
        "app2c/lab.html",
        active_nav="lab",
        camera_view_ws_base=camera_view_ws_base(),
        device_pipeline_ws_base=device_pipeline_ws_base(),
        **_default_robot_face_payload(),
    )


@router.get("/my/memories")
def memories(request: Request, user: RequireUser):
    return render_template(request, "app2c/memories.html", active_nav="memory")


@router.get("/my/reminders")
def reminders(request: Request, user: RequireUser):
    return render_template(request, "app2c/reminders.html", active_nav="remind")


@router.get("/my/people")
def people(request: Request, user: RequireUser):
    return render_template(request, "app2c/people.html", active_nav="people")


@router.get("/my/devices")
def devices(request: Request, user: RequireUser):
    return render_template(request, "app2c/devices.html", active_nav="device")


@router.get("/my/miot")
def miot(request: Request, user: RequireUser):
    return render_template(request, "app2c/miot.html", active_nav="miot")


@router.get("/advanced")
def advanced(request: Request, user: RequireUser):
    return render_template(request, "app2c/advanced.html", active_nav="advanced")


@router.get("/onboarding")
def onboarding(request: Request, user: RequireUser):
    return redirect(url_for("app2c.advanced", tab="llm"))


# 引导只让用户填 API Key；文本类功能默认路由到这个火山方舟模型（与图片生成同款），用户可切换。
DEFAULT_TEXT_MODEL = "doubao-seed-2-1-pro-260628"


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
        "default_model": DEFAULT_TEXT_MODEL,
        "protocols": list(SUPPORTED_PROTOCOLS),
        "needs_config": not api_key_set,
    }


@router.get("/api/setup/llm")
def setup_llm_get(request: Request, user: RequireUser):
    return jsonify({"ok": True, **_system_llm_payload()})


@router.post("/api/setup/llm")
def setup_llm_post(request: Request, user: RequireUser):
    payload = get_json(request, silent=True) or {}
    api_key = str(payload.get("api_key") or "").strip()
    model_name = str(payload.get("model_name") or "").strip() or DEFAULT_TEXT_MODEL
    protocol = str(payload.get("protocol") or "ark").strip().lower() or "ark"
    if not api_key or "*" in api_key or "•" in api_key:
        return jsonify({"ok": False, "error": "请填写火山方舟 API Key"}), 400
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


@router.post("/api/setup/llm/test")
def setup_llm_test(request: Request, user: RequireUser):
    payload = get_json(request, silent=True) or {}
    current = resolve_system_llm_config()
    model_name = str(payload.get("model_name") or "").strip() or DEFAULT_TEXT_MODEL
    protocol = str(payload.get("protocol") or "ark").strip().lower() or "ark"
    # base_url 留空时交给协议默认解析（ark→火山方舟地址），不要沿用旧的系统默认地址
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key or "*" in api_key or "•" in api_key:
        api_key = str(current.api_key or "").strip()
    prompt = str(payload.get("prompt") or "你好，请用一句话介绍你自己。").strip()

    if not model_name:
        return jsonify({"ok": False, "error": "请填写模型名称"}), 400
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
            [{"role": "user", "content": prompt}], config=config, json_mode=False, temperature=0.7
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - surface provider error to the user
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify(
        {"ok": True, "reply": reply, "meta": {"model": meta.get("model"), "display_name": meta.get("display_name")}}
    )


def _list_ark_models(api_key: str, base_url: str | None = None) -> list[dict]:
    """用数据面 API Key 拉取火山方舟模型清单，过滤掉已下线(Shutdown)的。

    返回 [{id, name, status}]，id 为可直接用于 --model 的完整模型 ID。
    """
    import json as _json
    import urllib.request

    from deskbot_server.infrastructure.llm.runtime import ARK_OPENAI_BASE_URL

    url = (str(base_url or "").strip() or ARK_OPENAI_BASE_URL).rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - 固定火山方舟地址
        data = _json.loads(resp.read().decode("utf-8"))
    # 只保留能做对话/多模态理解的模型，过滤 embedding / 语音 / 图像视频生成等非对话族
    non_chat = ("embedding", "-tts", "-asr", "seedream", "seedance", "seededit", "-voice", "music", "podcast")
    out: list[dict] = []
    for m in data.get("data", []) or []:
        if m.get("status") == "Shutdown":
            continue
        mid = str(m.get("id") or "").strip()
        if not mid or any(k in mid for k in non_chat):
            continue
        out.append({"id": mid, "name": m.get("name") or mid, "status": m.get("status") or "active"})
    return out


@router.post("/api/setup/llm/models")
def setup_llm_models(request: Request, user: RequireUser):
    payload = get_json(request, silent=True) or {}
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key or "*" in api_key or "•" in api_key:
        api_key = str(resolve_system_llm_config().api_key or "").strip()
    if not _llm_api_key_set(api_key):
        return jsonify({"ok": False, "error": "请先填写火山方舟 API Key"}), 400
    try:
        models = _list_ark_models(api_key, payload.get("base_url"))
    except Exception as exc:  # noqa: BLE001 - surface fetch error to the user
        return jsonify({"ok": False, "error": f"获取模型清单失败：{exc}"}), 502
    return jsonify({"ok": True, "models": models, "default_model": DEFAULT_TEXT_MODEL})


@router.post("/api/debug/reset-account")
def debug_reset_account(request: Request, user: RequireUser):
    """调试用：把当前账号重置回新用户状态（解绑所有设备、吊销 API Key、清除本机大模型配置）。"""
    from deskbot_server.dao.api_key_service import revoke_api_key
    from deskbot_server.infrastructure.llm.env_store import clear_llm_env
    from deskbot_server.web.session_device import clear_current_device

    uid = user.id
    cleared = {"devices": 0, "api_keys": 0}
    for d in UserService().list_devices(uid):
        try:
            if UserService().unbind_device(uid, d.device_id):
                cleared["devices"] += 1
        except Exception:  # noqa: BLE001 - best effort
            pass
    for k in list_api_keys_for_user(uid):
        try:
            if revoke_api_key(uid, k.id):
                cleared["api_keys"] += 1
        except Exception:  # noqa: BLE001 - best effort
            pass
    clear_current_device(request)
    clear_llm_env()
    return jsonify({"ok": True, "cleared": cleared})


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
        if api_key_set:
            return "本机大模型已配置；绑定并选择小歪后，可继续做按设备覆盖。"
        return "请先在「模型配置」里完成大模型配置；绑定小歪后，可继续做按设备覆盖。"
    if api_key_set:
        return ""
    if source == "system":
        return (
            "需要完成大模型配置：当前使用系统默认模型，但没有可用 API Key。"
            "请展开配置，填写 Ark 模型 ID 与 ARK_API_KEY，保存并设为当前；"
            "也可以在环境变量里设置 ARK_API_KEY / VOLCENGINE_API_KEY / LLM_API_KEY。"
        )
    return "需要完成大模型配置：请展开配置，填写模型 ID 与 ARK_API_KEY，保存并设为当前。"


@router.get("/api/advanced")
def advanced_summary_get(request: Request, user: RequireUser):
    user = UserService().get_user(user.id)
    devices = UserService().list_devices(user.id)
    current_device_id = get_current_device_id(request)
    keys = list_api_keys_for_user(user.id)
    usage = get_user_usage_summary(user.id, days=14)
    device_usage = get_user_device_usage_summary(user.id, days=14)
    user_today = get_user_usage_today(user.id)
    device_daily_rows = _flatten_usage_daily_rows(
        device_usage.get("device_stats") or [], label_key="display_name", sub_id_key="device_id"
    )
    key_daily_rows = _flatten_usage_daily_rows(
        usage.get("key_stats") or [], label_key="name", sub_id_key="api_key_id", sub_label_key="key_prefix"
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
    if current_device_id and UserService().user_owns_device(user.id, current_device_id):
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
                device_selected=True, api_key_set=api_key_set, source=resolved.source
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
        system_api_key_set = bool(llm["system_default"] and llm["system_default"].get("api_key_set"))
        llm["needs_config"] = not system_api_key_set
        llm["config_message"] = _llm_config_message(
            device_selected=False, api_key_set=system_api_key_set, source=system_default.source
        )

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


@router.patch("/api/advanced/profile")
def advanced_profile_patch(request: Request, user: RequireUser):
    payload = get_json(request, silent=True) or {}
    try:
        UserService().update_display_name(user.id, str(payload.get("display_name") or ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    user = UserService().get_user(user.id)
    return jsonify(
        {
            "ok": True,
            "user": {"email": getattr(user, "email", ""), "display_name": getattr(user, "display_name", "") or ""},
        }
    )


@router.post("/api/advanced/password")
def advanced_password_post(request: Request, user: RequireUser):
    payload = get_json(request, silent=True) or {}
    old_password = str(payload.get("old_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirm = str(payload.get("confirm_password") or "")
    if new_password != confirm:
        return jsonify({"ok": False, "error": "两次新密码不一致"}), 400
    try:
        UserService().change_password(user.id, old_password, new_password)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


@router.post("/api/advanced/api-keys")
def advanced_api_key_post(request: Request, user: RequireUser):
    payload = get_json(request, silent=True) or {}
    name = str(payload.get("name") or "default").strip()
    try:
        raw, row = create_api_key(user.id, name=name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "raw_key": raw, "api_key": _api_key_payload(row)})


@router.delete("/api/advanced/api-keys/{key_id}")
def advanced_api_key_delete(request: Request, user: RequireUser, key_id: str):
    if not revoke_api_key(user.id, key_id):
        return jsonify({"ok": False, "error": "API Key 不存在"}), 404
    return jsonify({"ok": True})


def _owned_device_or_error(request: Request, user):
    device_id = (request.query_params.get("device_id") or "").strip()
    if not device_id and is_json_request(request):
        payload = get_json(request, silent=True) or {}
        if isinstance(payload, dict):
            device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        device_id = str(form_get(request, "device_id") or "").strip()
    if not device_id:
        device_id = (get_current_device_id(request) or "").strip()
    if not device_id:
        return None, (jsonify({"ok": False, "error": "请先选择设备"}), 400)
    if not UserService().user_owns_device(user.id, device_id):
        return None, (jsonify({"ok": False, "error": "设备不属于当前账号"}), 403)
    return device_id, None


def _optional_owned_device_or_error(request: Request, user):
    device_id = (request.query_params.get("device_id") or "").strip()
    if not device_id and is_json_request(request):
        payload = get_json(request, silent=True) or {}
        if isinstance(payload, dict):
            device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        device_id = str(form_get(request, "device_id") or "").strip()
    if not device_id:
        device_id = (get_current_device_id(request) or "").strip()
    if not device_id:
        return "", None
    if not UserService().user_owns_device(user.id, device_id):
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


@router.get("/api/emotion_expr_map")
def emotion_expr_map_get(request: Request, user: RequireUser):
    device_id, err = _owned_device_or_error(request, user)
    if err:
        return err
    return jsonify({"ok": True, "device_id": device_id, "map": load_emotion_expr_map(device_id=device_id)})


@router.post("/api/emotion_expr_map")
def emotion_expr_map_post(request: Request, user: RequireUser):
    device_id, err = _owned_device_or_error(request, user)
    if err:
        return err
    payload = get_json(request, silent=True) or {}
    mapping = payload.get("map")
    if not isinstance(mapping, dict):
        return jsonify({"ok": False, "error": "map 必须是对象"}), 400
    try:
        saved = save_emotion_expr_map(mapping, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "map": saved})


@router.get("/api/face_expr_scenes")
def face_expr_scenes_get(request: Request, user: RequireUser):
    device_id, err = _owned_device_or_error(request, user)
    if err:
        return err
    try:
        rows = load_face_expr_scenes_file(seed_if_missing=True, device_id=device_id) or []
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "device_id": device_id, "config": rows})


@router.post("/api/face_expr_scenes")
def face_expr_scenes_post(request: Request, user: RequireUser):
    device_id, err = _owned_device_or_error(request, user)
    if err:
        return err
    payload = get_json(request, silent=True) or {}
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


@router.get("/api/face_mouth_by_phoneme")
def face_mouth_by_phoneme_get(request: Request, user: RequireUser):
    device_id, err = _owned_device_or_error(request, user)
    if err:
        return err
    try:
        groups = load_face_mouth_cfg_file(seed_if_missing=True, device_id=device_id) or []
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "device_id": device_id, "mouth_by_phoneme_groups": groups})


@router.post("/api/face_mouth_by_phoneme")
def face_mouth_by_phoneme_post(request: Request, user: RequireUser):
    device_id, err = _owned_device_or_error(request, user)
    if err:
        return err
    payload = get_json(request, silent=True) or {}
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


@router.post("/api/scene_playbook/export_plan")
def scene_playbook_export_plan_post(request: Request, user: RequireUser):
    device_id, err = _owned_device_or_error(request, user)
    if err:
        return err
    payload = get_json(request, silent=True) or {}
    playbook = payload.get("playbook")
    if not isinstance(playbook, dict):
        return jsonify({"ok": False, "error": "missing playbook"}), 400
    try:
        from deskbot_server.dao.scene_playbooks_store import normalize_playbook
        from deskbot_server.service.scene_playbook_runner import playbook_debug_snapshot

        pb = normalize_playbook(playbook)
        snap = playbook_debug_snapshot(pb, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "device_id": device_id, **snap})


@router.post("/api/face_design/generate-from-image")
def face_design_generate_from_image_post(request: Request, user: RequireUser):
    device_id, err = _optional_owned_device_or_error(request, user)
    if err:
        return err
    from deskbot_server.web.view_helpers import read_upload_bytes

    upload = files_get(request, "image") or files_get(request, "file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "请先上传图片"}), 400
    image_bytes = read_upload_bytes(upload)
    prompt = str(form_get(request, "prompt") or "").strip()
    mime_type = _image_mime_from_upload(
        upload.filename, getattr(upload, "mimetype", None) or getattr(upload, "content_type", None) or "", image_bytes
    )
    try:
        from deskbot_server.vision.ark_face_svg import generate_face_svg_from_image

        result = generate_face_svg_from_image(image_bytes, mime_type, prompt=prompt)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
    return jsonify({"device_id": device_id, **result})


ENDPOINTS = {
    "app2c.home": "/home",
    "app2c.voice": "/voice",
    "app2c.expr": "/expr",
    "app2c.lab": "/lab",
    "app2c.memories": "/my/memories",
    "app2c.reminders": "/my/reminders",
    "app2c.people": "/my/people",
    "app2c.devices": "/my/devices",
    "app2c.miot": "/my/miot",
    "app2c.advanced": "/advanced",
    "app2c.onboarding": "/onboarding",
    "app2c.setup_llm_get": "/api/setup/llm",
    "app2c.setup_llm_post": "/api/setup/llm",
    "app2c.setup_llm_test": "/api/setup/llm/test",
    "app2c.setup_llm_models": "/api/setup/llm/models",
    "app2c.debug_reset_account": "/api/debug/reset-account",
    "app2c.advanced_summary_get": "/api/advanced",
    "app2c.advanced_profile_patch": "/api/advanced/profile",
    "app2c.advanced_password_post": "/api/advanced/password",
    "app2c.advanced_api_key_post": "/api/advanced/api-keys",
    "app2c.advanced_api_key_delete": "/api/advanced/api-keys/{key_id}",
    "app2c.emotion_expr_map_get": "/api/emotion_expr_map",
    "app2c.emotion_expr_map_post": "/api/emotion_expr_map",
    "app2c.face_expr_scenes_get": "/api/face_expr_scenes",
    "app2c.face_expr_scenes_post": "/api/face_expr_scenes",
    "app2c.face_mouth_by_phoneme_get": "/api/face_mouth_by_phoneme",
    "app2c.face_mouth_by_phoneme_post": "/api/face_mouth_by_phoneme",
    "app2c.scene_playbook_export_plan_post": "/api/scene_playbook/export_plan",
    "app2c.face_design_generate_from_image_post": "/api/face_design/generate-from-image",
}
