from __future__ import annotations

from fastapi import APIRouter, Request

from deskbot_server.service.face_profile_service import (
    delete_face_profile,
    list_face_profiles_summary,
    update_face_profile_name,
)
from deskbot_server.dao.llm_config_store import (
    SUPPORTED_PROTOCOLS,
    add_llm_model,
    delete_llm_model,
    get_active_model_id,
    get_llm_model,
    list_llm_models,
    set_active_llm_model,
    update_llm_model,
)
from deskbot_server.dao.device_memory_mapper import add_memory, delete_memory, get_memory, list_memory_for_device, update_memory
from deskbot_server.infrastructure.llm.runtime import (
    ResolvedLlmConfig,
    build_chat_model,
    chat_completion,
    resolve_llm_config,
    resolve_system_llm_config,
)
from deskbot_server.service.miot_service import (
    authorize_and_sync,
    error_payload,
    get_bind_url,
    get_status,
    load_homes_cache,
    miot_sdk_available,
    parse_auth_payload,
    sync_homes,
)
from deskbot_server.service.miot_service import unbind as miot_unbind
from deskbot_server.service.scheduled_task_service import (
    count_scheduled_tasks_for_device,
    delete_scheduled_task,
    list_scheduled_tasks_for_device,
)
from deskbot_server.service.user_service import UserService
from deskbot_server.web.deps import RequireUser
from deskbot_server.web.helpers import fetch_live_device_details
from deskbot_server.web.session_device import clear_current_device, get_current_device_id, set_current_device_id
from deskbot_server.web.urls import flash, url_for
from deskbot_server.web.view_helpers import ViewAPIRoute, form_get, get_json, jsonify, redirect

router = APIRouter(route_class=ViewAPIRoute, prefix="/app", tags=["app"])


def _fmt_bytes(n: int) -> str:
    n = int(n or 0)
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.2f} GB"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def fmt_bytes_filter(n):
    return _fmt_bytes(n)


def _flatten_usage_daily_rows(
    stats: list[dict], *, label_key: str, sub_id_key: str, sub_label_key: str | None = None
) -> list[dict]:
    rows: list[dict] = []
    for item in stats:
        label = str(item.get(label_key) or item.get(sub_id_key) or "")
        sub_id = str(item.get(sub_id_key) or "")
        sub_label = str(item.get(sub_label_key) or "") if sub_label_key else sub_id
        for day in item.get("days") or []:
            if not isinstance(day, dict):
                continue
            rows.append(
                {
                    "label": label,
                    "sub_id": sub_id,
                    "sub_label": sub_label,
                    "date": str(day.get("date") or ""),
                    "asr_bytes": int(day.get("asr_bytes") or 0),
                    "face_bytes": int(day.get("face_bytes") or 0),
                    "llm_bytes": int(day.get("llm_bytes") or 0),
                    "tts_bytes": int(day.get("tts_bytes") or 0),
                    "total_bytes": int(day.get("total_bytes") or 0),
                }
            )
    rows.sort(key=lambda r: (r["date"], r["sub_id"]), reverse=True)
    return rows


@router.post("/settings/profile")
def update_profile_post(request: Request, user: RequireUser):
    try:
        UserService().update_display_name(user.id, form_get(request, "display_name") or "")
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect(url_for("app2c.advanced"))
    flash(request, "用户名称已更新", "success")
    return redirect(url_for("app2c.advanced"))


@router.post("/settings/password")
def change_password_post(request: Request, user: RequireUser):
    old_password = form_get(request, "old_password") or ""
    new_password = form_get(request, "new_password") or ""
    confirm = form_get(request, "confirm_password") or ""
    if new_password != confirm:
        flash(request, "两次新密码不一致", "error")
        return redirect(url_for("app2c.advanced"))
    try:
        UserService().change_password(user.id, old_password, new_password)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return redirect(url_for("app2c.advanced"))
    flash(request, "密码已更新", "success")
    return redirect(url_for("app2c.advanced"))


@router.get("/api/devices")
def api_list_devices(request: Request, user: RequireUser):
    devices = UserService().list_devices(user.id)
    live_map = fetch_live_device_details(user_id=user.id)
    current = get_current_device_id(request)
    return jsonify(
        {
            "ok": True,
            "devices": [
                {
                    "id": d.id,
                    "device_id": d.device_id,
                    "display_name": d.display_name or d.device_id,
                    "claimed_at": d.claimed_at.isoformat() if d.claimed_at else None,
                    "online": live_map.get(d.device_id, {}).get("online", False),
                    "last_seen": live_map.get(d.device_id, {}).get("last_seen", "—"),
                    "is_current": d.device_id == current,
                }
                for d in devices
            ],
            "current_device_id": current,
        }
    )


@router.post("/api/devices")
def api_bind_device(request: Request, user: RequireUser):
    payload = get_json(request, silent=True) or {}
    device_id = str(payload.get("device_id") or form_get(request, "device_id") or "").strip()
    display_name = str(payload.get("display_name") or "").strip() or None
    if not device_id:
        return jsonify({"ok": False, "error": "绑定失败：请输入 device_id"}), 400
    try:
        device = UserService().bind_device(user.id, device_id, display_name=display_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    set_current_device_id(request, device.device_id)
    return jsonify(
        {
            "ok": True,
            "message": "绑定成功",
            "device": {"device_id": device.device_id},
            "current_device_id": device.device_id,
        }
    )


@router.post("/api/devices/select")
def api_select_device(request: Request, user: RequireUser):
    payload = get_json(request, silent=True) or {}
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        clear_current_device(request)
        return jsonify({"ok": True, "current_device_id": None})
    if not UserService().user_owns_device(user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    set_current_device_id(request, device_id)
    return jsonify({"ok": True, "current_device_id": device_id})


@router.delete("/api/devices/{device_id}")
def api_unbind_device(request: Request, user: RequireUser, device_id: str):
    if not UserService().unbind_device(user.id, device_id):
        return jsonify({"ok": False, "error": "设备不存在"}), 404
    if get_current_device_id(request) == device_id:
        clear_current_device(request)
    return jsonify({"ok": True})


@router.post("/api/devices/{device_id}/reset-id")
def api_reset_device_id(request: Request, user: RequireUser, device_id: str):
    if not UserService().user_owns_device(user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    try:
        new_device_id = UserService().reset_device_id(device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    # 同步更新 session 中的当前设备
    if get_current_device_id(request) == device_id:
        set_current_device_id(request, new_device_id)
    return jsonify({"ok": True, "device_id": new_device_id})


@router.get("/api/scheduled-tasks")
def api_list_scheduled_tasks(request: Request, user: RequireUser):
    device_id = str(request.query_params.get("device_id") or get_current_device_id(request) or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not UserService().user_owns_device(user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    page = max(1, int(request.query_params.get("page") or 1))
    per_page = int(request.query_params.get("per_page") or 10)
    if per_page not in (10, 50, 100, 200):
        per_page = 10
    total = count_scheduled_tasks_for_device(device_id)
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * per_page
    tasks = list_scheduled_tasks_for_device(device_id, limit=per_page, offset=offset)
    return jsonify(
        {
            "ok": True,
            "device_id": device_id,
            "tasks": tasks,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        }
    )


@router.get("/api/face-profiles")
def api_list_face_profiles(request: Request, user: RequireUser):
    device_id = str(request.query_params.get("device_id") or get_current_device_id(request) or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not UserService().user_owns_device(user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    profiles = list_face_profiles_summary(device_id=device_id)
    return jsonify({"ok": True, "device_id": device_id, "profiles": profiles})


@router.delete("/api/face-profiles/{profile_id}")
def api_delete_face_profile(request: Request, user: RequireUser, profile_id: int):
    device_id = str(request.query_params.get("device_id") or get_current_device_id(request) or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not UserService().user_owns_device(user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    if not delete_face_profile(profile_id, device_id=device_id):
        return jsonify({"ok": False, "error": "人脸档案不存在"}), 404
    return jsonify({"ok": True})


@router.put("/api/face-profiles/{profile_id}")
@router.patch("/api/face-profiles/{profile_id}")
def api_update_face_profile(request: Request, user: RequireUser, profile_id: int):
    device_id = str(request.query_params.get("device_id") or get_current_device_id(request) or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not UserService().user_owns_device(user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    payload = get_json(request, silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name 不能为空"}), 400
    try:
        profile = update_face_profile_name(profile_id, name, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if profile is None:
        return jsonify({"ok": False, "error": "人脸档案不存在"}), 404
    return jsonify({"ok": True, "profile": profile})


def _require_owned_device_id(request: Request, user) -> tuple[str | None, tuple | None]:
    device_id = str(request.query_params.get("device_id") or get_current_device_id(request) or "").strip()
    if not device_id:
        return None, (jsonify({"ok": False, "error": "请先选择设备"}), 400)
    if not UserService().user_owns_device(user.id, device_id):
        return None, (jsonify({"ok": False, "error": "设备不属于当前账号"}), 403)
    return device_id, None


def _consume_settings_test_quota(device_id: str):
    from deskbot_server.service.application.settings_test_limit import (
        SETTINGS_TEST_DAILY_LIMIT,
        SettingsTestLimitExceeded,
        check_and_consume_settings_test,
    )

    try:
        snap = check_and_consume_settings_test(device_id=device_id)
    except SettingsTestLimitExceeded as exc:
        return None, (jsonify({"ok": False, "error": str(exc), "daily_limit": SETTINGS_TEST_DAILY_LIMIT}), 429)
    return {"daily_limit": SETTINGS_TEST_DAILY_LIMIT, "remaining": snap.remaining}, None


@router.get("/api/memories")
def api_list_memories(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    entries = list_memory_for_device(device_id)
    return jsonify({"ok": True, "device_id": device_id, "memories": entries, "count": len(entries)})


@router.post("/api/memories")
def api_create_memory(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    payload = get_json(request, silent=True) or {}
    text = str(payload.get("text") or form_get(request, "text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text 不能为空"}), 400
    try:
        entry = add_memory(text, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "memory": entry})


@router.get("/api/memories/{entry_id}")
def api_get_memory(request: Request, user: RequireUser, entry_id: str):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    entry = get_memory(entry_id, device_id=device_id)
    if entry is None:
        return jsonify({"ok": False, "error": "记忆不存在"}), 404
    return jsonify({"ok": True, "memory": entry})


@router.put("/api/memories/{entry_id}")
@router.patch("/api/memories/{entry_id}")
def api_update_memory(request: Request, user: RequireUser, entry_id: str):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    payload = get_json(request, silent=True) or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text 不能为空"}), 400
    try:
        entry = update_memory(entry_id, text, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if entry is None:
        return jsonify({"ok": False, "error": "记忆不存在"}), 404
    return jsonify({"ok": True, "memory": entry})


@router.delete("/api/memories/{entry_id}")
def api_delete_memory(request: Request, user: RequireUser, entry_id: str):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    if not delete_memory(entry_id, device_id=device_id):
        return jsonify({"ok": False, "error": "记忆不存在"}), 404
    return jsonify({"ok": True})


@router.get("/api/llm-models")
def api_list_llm_models(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    models = list_llm_models(device_id, mask_key=True)
    active_model_id = get_active_model_id(device_id)
    try:
        resolved = resolve_llm_config(device_id)
        active = {
            "display_name": resolved.display_name,
            "model": resolved.model,
            "source": resolved.source,
            "api_base": resolved.api_base or "",
        }
    except ValueError as exc:
        active = {"error": str(exc)}
    system_default = resolve_system_llm_config()
    return jsonify(
        {
            "ok": True,
            "device_id": device_id,
            "models": models,
            "active_model_id": active_model_id,
            "active": active,
            "system_default": {
                "display_name": system_default.display_name,
                "model": system_default.model,
                "api_base": system_default.api_base or "",
            },
            "supported_protocols": list(SUPPORTED_PROTOCOLS),
        }
    )


@router.post("/api/llm-models")
def api_create_llm_model(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    payload = get_json(request, silent=True) or {}
    try:
        model = add_llm_model(
            device_id,
            name=str(payload.get("name") or "").strip(),
            model_name=str(payload.get("model_name") or "").strip(),
            protocol=str(payload.get("protocol") or "ark").strip(),
            base_url=str(payload.get("base_url") or "").strip(),
            api_key=str(payload.get("api_key") or "").strip(),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "model": model})


@router.post("/api/llm-models/test")
def api_test_llm_model(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    quota, limit_err = _consume_settings_test_quota(device_id)
    if limit_err:
        return limit_err
    payload = get_json(request, silent=True) or {}
    model_id = str(payload.get("model_id") or "").strip() or None
    name = str(payload.get("name") or "").strip()
    model_name = str(payload.get("model_name") or "").strip()
    protocol = str(payload.get("protocol") or "ark").strip().lower() or "ark"
    base_url = str(payload.get("base_url") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    prompt = str(payload.get("prompt") or "你好，请用一句话介绍你自己。").strip()

    if model_id:
        existing = get_llm_model(device_id, model_id)
        if existing is None:
            return jsonify({"ok": False, "error": "模型不存在"}), 404
        if not name:
            name = existing.name
        if not model_name:
            model_name = existing.model_name
        if not protocol:
            protocol = existing.protocol
        if not base_url:
            base_url = existing.base_url
        if not api_key:
            api_key = existing.api_key

    if not model_name:
        return jsonify({"ok": False, "error": "model_name required"}), 400
    if protocol not in SUPPORTED_PROTOCOLS:
        return jsonify({"ok": False, "error": f"不支持的协议: {protocol}"}), 400

    try:
        config = ResolvedLlmConfig(
            model=build_chat_model(protocol, model_name),
            api_key=api_key,
            api_base=base_url or None,
            protocol=protocol,
            source="test",
            display_name=name or model_name,
        )
        reply, meta = chat_completion(
            [{"role": "user", "content": prompt}], config=config, json_mode=False, temperature=0.7
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify(
        {
            "ok": True,
            "reply": reply,
            "meta": {"model": meta.get("model"), "display_name": meta.get("display_name"), "usage": meta.get("usage")},
            "quota": quota,
        }
    )


@router.put("/api/llm-models/{model_id}")
def api_update_llm_model(request: Request, user: RequireUser, model_id: str):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    payload = get_json(request, silent=True) or {}
    try:
        model = update_llm_model(
            device_id,
            model_id,
            name=payload.get("name"),
            model_name=payload.get("model_name"),
            protocol=payload.get("protocol"),
            base_url=payload.get("base_url"),
            api_key=payload.get("api_key"),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if model is None:
        return jsonify({"ok": False, "error": "模型不存在"}), 404
    return jsonify({"ok": True, "model": model})


@router.delete("/api/llm-models/{model_id}")
def api_delete_llm_model(request: Request, user: RequireUser, model_id: str):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    if not delete_llm_model(device_id, model_id):
        return jsonify({"ok": False, "error": "模型不存在"}), 404
    return jsonify({"ok": True})


@router.post("/api/llm-models/select")
def api_select_llm_model(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    payload = get_json(request, silent=True) or {}
    model_id = payload.get("model_id")
    if model_id is not None:
        model_id = str(model_id).strip() or None
    try:
        active = set_active_llm_model(device_id, model_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "active_model_id": active})


def _tts_cfg_from_payload(payload: dict):
    from deskbot_server.infrastructure.tts.doubao import (
        DoubaoTtsConfig,
        load_doubao_tts_config,
        resolve_optional_secret,
    )

    base = load_doubao_tts_config()
    api_key = resolve_optional_secret(payload.get("api_key"), base.api_key)
    sample_rate_raw = payload.get("sample_rate", base.sample_rate)
    try:
        sample_rate = int(sample_rate_raw)
    except (TypeError, ValueError):
        sample_rate = base.sample_rate
    return DoubaoTtsConfig(
        api_key=api_key,
        speaker=str(payload.get("speaker") or base.speaker).strip(),
        resource_id=str(payload.get("resource_id") or base.resource_id).strip(),
        model=str(payload.get("model") or base.model).strip(),
        ws_url=str(payload.get("ws_url") or base.ws_url).strip(),
        sample_rate=sample_rate,
        audio_format=str(payload.get("audio_format") or base.audio_format).strip(),
    )


@router.get("/api/tts/config")
def api_tts_config_get(request: Request, user: RequireUser):
    from deskbot_server.infrastructure.tts.doubao import load_doubao_tts_config

    cfg = load_doubao_tts_config()
    return jsonify({"ok": True, "config": cfg.masked()})


@router.post("/api/tts/config")
def api_tts_config_post(request: Request, user: RequireUser):
    from deskbot_server.infrastructure.tts.doubao import load_doubao_tts_config
    from deskbot_server.infrastructure.tts.env_store import save_doubao_tts_env

    payload = get_json(request, silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object"}), 400

    from deskbot_server.infrastructure.tts.doubao import resolve_optional_secret

    base = load_doubao_tts_config()
    api_key = resolve_optional_secret(payload.get("api_key"), base.api_key)
    if not api_key:
        return jsonify({"ok": False, "error": "DOUBAO_TTS_API_KEY 不能为空"}), 400
    try:
        save_doubao_tts_env(payload)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    cfg = load_doubao_tts_config()
    return jsonify({"ok": True, "config": cfg.masked()})


@router.get("/api/tts/speakers")
def api_tts_speakers(request: Request, user: RequireUser):
    from deskbot_server.infrastructure.tts.speakers import list_doubao_tts_speaker_presets

    return jsonify({"ok": True, "speakers": list_doubao_tts_speaker_presets()})


@router.post("/api/tts/preview")
def api_tts_preview(request: Request, user: RequireUser):
    import asyncio
    import base64

    from deskbot_server.infrastructure.tts.doubao import synthesize_doubao_tts
    from deskbot_server.utils.util import pcm_to_wav_bytes

    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    quota, limit_err = _consume_settings_test_quota(device_id)
    if limit_err:
        return limit_err
    payload = get_json(request, silent=True) or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "试听文本不能为空"}), 400
    cfg = _tts_cfg_from_payload(payload)
    if not cfg.api_key:
        return jsonify({"ok": False, "error": "请先配置豆包语音 API Key（DOUBAO_TTS_API_KEY）"}), 400
    try:
        result = asyncio.run(synthesize_doubao_tts(text, cfg))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502
    wav = pcm_to_wav_bytes(result.pcm, result.sample_rate)
    return jsonify(
        {
            "ok": True,
            "sample_rate": result.sample_rate,
            "elapsed_ms": result.elapsed_ms,
            "speaker": cfg.speaker,
            "wav_base64": base64.b64encode(wav).decode("ascii"),
            "quota": quota,
        }
    )


@router.delete("/api/scheduled-tasks/{task_id}")
def api_delete_scheduled_task(request: Request, user: RequireUser, task_id: str):
    device_id = str(request.query_params.get("device_id") or get_current_device_id(request) or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not UserService().user_owns_device(user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    if not delete_scheduled_task(task_id, device_id=device_id):
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    return jsonify({"ok": True})


# ----- 米家 IoT -----


@router.get("/api/miot/status")
def api_miot_status(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    ok_sdk, sdk_err = miot_sdk_available()
    if not ok_sdk:
        return jsonify({"ok": False, "error": sdk_err, "sdk_ok": False}), 503
    refresh = str(request.query_params.get("refresh") or "").strip().lower() in ("1", "true", "yes")
    try:
        status = get_status(device_id, refresh=refresh)
    except Exception as exc:
        return jsonify({"ok": False, **error_payload(exc)}), 400
    homes = load_homes_cache(device_id)
    return jsonify(
        {
            "ok": True,
            "sdk_ok": True,
            "device_id": device_id,
            "status": status,
            "homes": homes,
            "device_count": homes.get("device_count") or len(homes.get("devices") or []),
            "scene_count": homes.get("scene_count") or len(homes.get("scenes") or []),
        }
    )


@router.post("/api/miot/bind-url")
def api_miot_bind_url(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    ok_sdk, sdk_err = miot_sdk_available()
    if not ok_sdk:
        return jsonify({"ok": False, "error": sdk_err}), 503
    try:
        url = get_bind_url(device_id)
    except Exception as exc:
        return jsonify({"ok": False, **error_payload(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "auth_url": url,
            "hint": (
                "请在浏览器打开授权链接并登录小米账号。"
                "授权完成后会进入「小米账号授权完成」页面，"
                "点击「复制授权码」，回到本页粘贴即可（也可粘贴完整回调 URL）。"
            ),
        }
    )


@router.post("/api/miot/authorize")
def api_miot_authorize(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    payload = get_json(request, silent=True) or {}
    code = str(payload.get("code") or "").strip()
    state = str(payload.get("state") or "").strip()
    callback = str(payload.get("callback_url") or payload.get("url") or payload.get("payload") or "").strip()
    try:
        if code and state:
            auth_code, auth_state = code, state
        elif callback:
            auth_code, auth_state = parse_auth_payload(callback)
        else:
            raise ValueError("请粘贴授权回调完整 URL，或提供 code 与 state")
        result = authorize_and_sync(device_id, auth_code, auth_state)
    except Exception as exc:
        return jsonify({"ok": False, **error_payload(exc)}), 400
    return jsonify(result)


@router.post("/api/miot/sync")
def api_miot_sync(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    try:
        homes = sync_homes(device_id)
        status = get_status(device_id, refresh=True)
    except Exception as exc:
        import logging

        logging.getLogger("deskbot-server").exception("[miot] sync failed device_id=%s", device_id)
        return jsonify({"ok": False, **error_payload(exc)}), 400
    return jsonify(
        {
            "ok": True,
            "status": status,
            "homes": homes,
            "device_count": homes.get("device_count"),
            "scene_count": homes.get("scene_count"),
        }
    )


@router.post("/api/miot/unbind")
def api_miot_unbind(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    try:
        miot_unbind(device_id)
    except Exception as exc:
        return jsonify({"ok": False, **error_payload(exc)}), 400
    return jsonify({"ok": True})


@router.get("/api/miot/homes")
def api_miot_homes(request: Request, user: RequireUser):
    device_id, err = _require_owned_device_id(request, user)
    if err:
        return err
    assert device_id is not None
    homes = load_homes_cache(device_id)
    return jsonify({"ok": True, "homes": homes})


ENDPOINTS = {
    "app.update_profile_post": "/app/settings/profile",
    "app.change_password_post": "/app/settings/password",
    "app.api_list_devices": "/app/api/devices",
    "app.api_bind_device": "/app/api/devices",
    "app.api_select_device": "/app/api/devices/select",
    "app.api_unbind_device": "/app/api/devices/{device_id}",
    "app.api_list_scheduled_tasks": "/app/api/scheduled-tasks",
    "app.api_list_face_profiles": "/app/api/face-profiles",
    "app.api_delete_face_profile": "/app/api/face-profiles/{profile_id}",
    "app.api_update_face_profile": "/app/api/face-profiles/{profile_id}",
    "app.api_list_memories": "/app/api/memories",
    "app.api_create_memory": "/app/api/memories",
    "app.api_get_memory": "/app/api/memories/{entry_id}",
    "app.api_update_memory": "/app/api/memories/{entry_id}",
    "app.api_delete_memory": "/app/api/memories/{entry_id}",
    "app.api_list_llm_models": "/app/api/llm-models",
    "app.api_create_llm_model": "/app/api/llm-models",
    "app.api_test_llm_model": "/app/api/llm-models/test",
    "app.api_update_llm_model": "/app/api/llm-models/{model_id}",
    "app.api_delete_llm_model": "/app/api/llm-models/{model_id}",
    "app.api_select_llm_model": "/app/api/llm-models/select",
    "app.api_tts_config_get": "/app/api/tts/config",
    "app.api_tts_config_post": "/app/api/tts/config",
    "app.api_tts_speakers": "/app/api/tts/speakers",
    "app.api_tts_preview": "/app/api/tts/preview",
    "app.api_delete_scheduled_task": "/app/api/scheduled-tasks/{task_id}",
    "app.api_miot_status": "/app/api/miot/status",
    "app.api_miot_bind_url": "/app/api/miot/bind-url",
    "app.api_miot_authorize": "/app/api/miot/authorize",
    "app.api_miot_sync": "/app/api/miot/sync",
    "app.api_miot_unbind": "/app/api/miot/unbind",
    "app.api_miot_homes": "/app/api/miot/homes",
}
