from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, login_required

from deskbot_server.auth.api_key_service import (
    create_api_key,
    revoke_api_key,
)
from deskbot_server.web.helpers import fetch_live_device_details
from deskbot_server.auth.device_service import (
    bind_device,
    list_devices_for_user,
    unbind_device,
    user_owns_device,
)
from deskbot_server.auth.service import change_password, update_display_name
from deskbot_server.face_profiles_store import (
    delete_face_profile,
    list_face_profiles_summary,
    update_face_profile_name,
)
from deskbot_server.memory_store import (
    add_memory,
    delete_memory,
    get_memory,
    list_memory_entries_for_device,
    update_memory,
)
from deskbot_server.scheduled_task_service import (
    count_scheduled_tasks_for_device,
    delete_scheduled_task,
    list_scheduled_tasks_for_device,
)
from deskbot_server.llm_config_store import (
    SUPPORTED_PROTOCOLS,
    add_llm_model,
    delete_llm_model,
    get_active_model_id,
    get_llm_model,
    list_llm_models,
    set_active_llm_model,
    update_llm_model,
)
from deskbot_server.llm.runtime import (
    ResolvedLlmConfig,
    build_chat_model,
    chat_completion,
    resolve_llm_config,
    resolve_system_llm_config,
)
from deskbot_server.web.session_device import (
    clear_current_device,
    get_current_device_id,
    set_current_device_id,
)

bp = Blueprint("app", __name__, url_prefix="/app")


def _fmt_bytes(n: int) -> str:
    n = int(n or 0)
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.2f} GB"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


@bp.app_template_filter("fmt_bytes")
def fmt_bytes_filter(n):
    return _fmt_bytes(n)


def _flatten_usage_daily_rows(
    stats: list[dict],
    *,
    label_key: str,
    sub_id_key: str,
    sub_label_key: str | None = None,
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


@bp.post("/settings/profile")
@login_required
def update_profile_post():
    try:
        update_display_name(current_user.id, request.form.get("display_name") or "")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("app2c.advanced"))
    flash("用户名称已更新", "success")
    return redirect(url_for("app2c.advanced"))


@bp.post("/settings/password")
@login_required
def change_password_post():
    old_password = request.form.get("old_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if new_password != confirm:
        flash("两次新密码不一致", "error")
        return redirect(url_for("app2c.advanced"))
    try:
        change_password(current_user.id, old_password, new_password)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("app2c.advanced"))
    flash("密码已更新", "success")
    return redirect(url_for("app2c.advanced"))


@bp.post("/settings/api-keys")
@login_required
def create_api_key_post():
    name = (request.form.get("key_name") or "default").strip()
    try:
        raw, _row = create_api_key(current_user.id, name=name)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("app2c.advanced"))
    session["new_api_key_raw"] = raw
    flash("API Key 已创建，请立即复制保存（仅显示一次）", "success")
    return redirect(url_for("app2c.advanced"))


@bp.post("/settings/api-keys/<key_id>/revoke")
@login_required
def revoke_api_key_post(key_id: str):
    if not revoke_api_key(current_user.id, key_id):
        flash("API Key 不存在", "error")
    else:
        flash("API Key 已吊销", "success")
    return redirect(url_for("app2c.advanced"))


@bp.get("/api/devices")
@login_required
def api_list_devices():
    devices = list_devices_for_user(current_user.id)
    live_map = fetch_live_device_details(user_id=current_user.id)
    current = get_current_device_id()
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


@bp.post("/api/devices")
@login_required
def api_bind_device():
    payload = request.get_json(silent=True) or {}
    device_id = str(payload.get("device_id") or request.form.get("device_id") or "").strip()
    display_name = str(payload.get("display_name") or "").strip() or None
    if not device_id:
        return jsonify({"ok": False, "error": "device_id required"}), 400
    try:
        device = bind_device(current_user.id, device_id, display_name=display_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    set_current_device_id(device.device_id)
    return jsonify({"ok": True, "device": {"device_id": device.device_id}, "current_device_id": device.device_id})


@bp.post("/api/devices/select")
@login_required
def api_select_device():
    payload = request.get_json(silent=True) or {}
    device_id = str(payload.get("device_id") or "").strip()
    if not device_id:
        clear_current_device()
        return jsonify({"ok": True, "current_device_id": None})
    if not user_owns_device(current_user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    set_current_device_id(device_id)
    return jsonify({"ok": True, "current_device_id": device_id})


@bp.delete("/api/devices/<device_id>")
@login_required
def api_unbind_device(device_id: str):
    if not unbind_device(current_user.id, device_id):
        return jsonify({"ok": False, "error": "设备不存在"}), 404
    if get_current_device_id() == device_id:
        clear_current_device()
    return jsonify({"ok": True})


@bp.get("/api/scheduled-tasks")
@login_required
def api_list_scheduled_tasks():
    device_id = str(request.args.get("device_id") or get_current_device_id() or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not user_owns_device(current_user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    page = max(1, int(request.args.get("page") or 1))
    per_page = int(request.args.get("per_page") or 10)
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


@bp.get("/api/face-profiles")
@login_required
def api_list_face_profiles():
    device_id = str(request.args.get("device_id") or get_current_device_id() or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not user_owns_device(current_user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    profiles = list_face_profiles_summary(device_id=device_id)
    return jsonify({"ok": True, "device_id": device_id, "profiles": profiles})


@bp.delete("/api/face-profiles/<int:person_id>")
@login_required
def api_delete_face_profile(person_id: int):
    device_id = str(request.args.get("device_id") or get_current_device_id() or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not user_owns_device(current_user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    if not delete_face_profile(person_id, device_id=device_id):
        return jsonify({"ok": False, "error": "人脸档案不存在"}), 404
    return jsonify({"ok": True})


@bp.put("/api/face-profiles/<int:person_id>")
@bp.patch("/api/face-profiles/<int:person_id>")
@login_required
def api_update_face_profile(person_id: int):
    device_id = str(request.args.get("device_id") or get_current_device_id() or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not user_owns_device(current_user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name 不能为空"}), 400
    try:
        profile = update_face_profile_name(person_id, name, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if profile is None:
        return jsonify({"ok": False, "error": "人脸档案不存在"}), 404
    return jsonify({"ok": True, "profile": profile})


def _require_owned_device_id() -> tuple[str | None, tuple | None]:
    device_id = str(request.args.get("device_id") or get_current_device_id() or "").strip()
    if not device_id:
        return None, (jsonify({"ok": False, "error": "请先选择设备"}), 400)
    if not user_owns_device(current_user.id, device_id):
        return None, (jsonify({"ok": False, "error": "设备不属于当前账号"}), 403)
    return device_id, None


def _consume_settings_test_quota():
    from deskbot_server.application.settings_test_limit import (
        SETTINGS_TEST_DAILY_LIMIT,
        SettingsTestLimitExceeded,
        check_and_consume_settings_test,
        client_ip_from_request,
    )

    try:
        snap = check_and_consume_settings_test(
            user_id=current_user.id,
            client_ip=client_ip_from_request(request),
        )
    except SettingsTestLimitExceeded as exc:
        return None, (
            jsonify(
                {
                    "ok": False,
                    "error": str(exc),
                    "daily_limit": SETTINGS_TEST_DAILY_LIMIT,
                }
            ),
            429,
        )
    return {
        "daily_limit": SETTINGS_TEST_DAILY_LIMIT,
        "user_remaining": snap.user_remaining,
        "ip_remaining": snap.ip_remaining,
    }, None


@bp.get("/api/memories")
@login_required
def api_list_memories():
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    entries = list_memory_entries_for_device(device_id)
    return jsonify({"ok": True, "device_id": device_id, "memories": entries, "count": len(entries)})


@bp.post("/api/memories")
@login_required
def api_create_memory():
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or request.form.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text 不能为空"}), 400
    try:
        entry = add_memory(text, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "memory": entry})


@bp.get("/api/memories/<entry_id>")
@login_required
def api_get_memory(entry_id: str):
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    entry = get_memory(entry_id, device_id=device_id)
    if entry is None:
        return jsonify({"ok": False, "error": "记忆不存在"}), 404
    return jsonify({"ok": True, "memory": entry})


@bp.put("/api/memories/<entry_id>")
@bp.patch("/api/memories/<entry_id>")
@login_required
def api_update_memory(entry_id: str):
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    payload = request.get_json(silent=True) or {}
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


@bp.delete("/api/memories/<entry_id>")
@login_required
def api_delete_memory(entry_id: str):
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    if not delete_memory(entry_id, device_id=device_id):
        return jsonify({"ok": False, "error": "记忆不存在"}), 404
    return jsonify({"ok": True})


@bp.get("/api/llm-models")
@login_required
def api_list_llm_models():
    device_id, err = _require_owned_device_id()
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
        }
    )


@bp.post("/api/llm-models")
@login_required
def api_create_llm_model():
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    payload = request.get_json(silent=True) or {}
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


@bp.post("/api/llm-models/test")
@login_required
def api_test_llm_model():
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    quota, limit_err = _consume_settings_test_quota()
    if limit_err:
        return limit_err
    payload = request.get_json(silent=True) or {}
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
            [{"role": "user", "content": prompt}],
            config=config,
            json_mode=False,
            temperature=0.7,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 502

    return jsonify(
        {
            "ok": True,
            "reply": reply,
            "meta": {
                "model": meta.get("model"),
                "display_name": meta.get("display_name"),
                "usage": meta.get("usage"),
            },
            "quota": quota,
        }
    )


@bp.put("/api/llm-models/<model_id>")
@login_required
def api_update_llm_model(model_id: str):
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    payload = request.get_json(silent=True) or {}
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


@bp.delete("/api/llm-models/<model_id>")
@login_required
def api_delete_llm_model(model_id: str):
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    if not delete_llm_model(device_id, model_id):
        return jsonify({"ok": False, "error": "模型不存在"}), 404
    return jsonify({"ok": True})


@bp.post("/api/llm-models/select")
@login_required
def api_select_llm_model():
    device_id, err = _require_owned_device_id()
    if err:
        return err
    assert device_id is not None
    payload = request.get_json(silent=True) or {}
    model_id = payload.get("model_id")
    if model_id is not None:
        model_id = str(model_id).strip() or None
    try:
        active = set_active_llm_model(device_id, model_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "active_model_id": active})


def _tts_cfg_from_payload(payload: dict):
    from deskbot_server.tts.doubao import DoubaoTtsConfig, load_doubao_tts_config, resolve_optional_secret

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


@bp.get("/api/tts/config")
@login_required
def api_tts_config_get():
    from deskbot_server.tts.doubao import load_doubao_tts_config

    cfg = load_doubao_tts_config()
    return jsonify({"ok": True, "config": cfg.masked()})


@bp.post("/api/tts/config")
@login_required
def api_tts_config_post():
    from deskbot_server.tts.doubao import load_doubao_tts_config
    from deskbot_server.tts.env_store import save_doubao_tts_env

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object"}), 400

    from deskbot_server.tts.doubao import load_doubao_tts_config, resolve_optional_secret

    base = load_doubao_tts_config()
    api_key = resolve_optional_secret(payload.get("api_key"), base.api_key)
    if not api_key:
        return jsonify({"ok": False, "error": "api_key 不能为空"}), 400
    try:
        save_doubao_tts_env(payload)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    cfg = load_doubao_tts_config()
    return jsonify({"ok": True, "config": cfg.masked()})


@bp.get("/api/tts/speakers")
@login_required
def api_tts_speakers():
    from deskbot_server.tts.speakers import list_doubao_tts_speaker_presets

    return jsonify({"ok": True, "speakers": list_doubao_tts_speaker_presets()})


@bp.post("/api/tts/preview")
@login_required
def api_tts_preview():
    import asyncio
    import base64

    from deskbot_server.tts.doubao import synthesize_doubao_tts
    from deskbot_server.util import pcm_to_wav_bytes

    quota, limit_err = _consume_settings_test_quota()
    if limit_err:
        return limit_err
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "试听文本不能为空"}), 400
    cfg = _tts_cfg_from_payload(payload)
    if not cfg.api_key:
        return jsonify({"ok": False, "error": "请先配置火山 API Key"}), 400
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


@bp.delete("/api/scheduled-tasks/<task_id>")
@login_required
def api_delete_scheduled_task(task_id: str):
    device_id = str(request.args.get("device_id") or get_current_device_id() or "").strip()
    if not device_id:
        return jsonify({"ok": False, "error": "请先选择设备"}), 400
    if not user_owns_device(current_user.id, device_id):
        return jsonify({"ok": False, "error": "设备不属于当前账号"}), 403
    if not delete_scheduled_task(task_id, device_id=device_id):
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    return jsonify({"ok": True})
