from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
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
from deskbot_server.web.helpers import fetch_online_device_map
from deskbot_server.auth.device_service import (
    bind_device,
    list_devices_for_user,
    unbind_device,
    user_owns_device,
)
from deskbot_server.auth.service import change_password, get_user_by_id, update_display_name
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
    delete_scheduled_task,
    list_scheduled_tasks_for_device,
)
from deskbot_server.llm_config_store import (
    SUPPORTED_PROTOCOLS,
    add_llm_model,
    delete_llm_model,
    get_active_model_id,
    list_llm_models,
    set_active_llm_model,
    update_llm_model,
)
from deskbot_server.llm.runtime import resolve_llm_config, resolve_system_llm_config
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


@bp.get("/")
@login_required
def dashboard():
    devices = list_devices_for_user(current_user.id)
    usage = get_user_usage_summary(current_user.id, days=7)
    today = usage["totals"]
    return render_template(
        "app/dashboard.html",
        devices=devices,
        current_device_id=get_current_device_id(),
        usage_totals=today,
        active_nav="app",
    )


@bp.get("/devices")
@login_required
def devices_page():
    devices = list_devices_for_user(current_user.id)
    online_map = fetch_online_device_map()
    device_rows = [
        {
            "device_id": d.device_id,
            "display_name": d.display_name or d.device_id,
            "online": online_map.get(d.device_id, False),
            "is_current": d.device_id == get_current_device_id(),
        }
        for d in devices
    ]
    return render_template(
        "app/devices.html",
        devices=devices,
        device_rows=device_rows,
        current_device_id=get_current_device_id(),
        active_nav="devices_mgr",
    )


@bp.get("/scheduled-tasks")
@login_required
def scheduled_tasks_page():
    devices = list_devices_for_user(current_user.id)
    current_device_id = get_current_device_id()
    tasks: list[dict] = []
    if current_device_id and user_owns_device(current_user.id, current_device_id):
        tasks = list_scheduled_tasks_for_device(current_device_id)
    return render_template(
        "app/scheduled_tasks.html",
        devices=devices,
        current_device_id=current_device_id,
        tasks=tasks,
        active_nav="scheduled_tasks",
    )


@bp.get("/face-profiles")
@login_required
def face_profiles_page():
    devices = list_devices_for_user(current_user.id)
    current_device_id = get_current_device_id()
    profiles: list[dict] = []
    if current_device_id and user_owns_device(current_user.id, current_device_id):
        profiles = list_face_profiles_summary(device_id=current_device_id)
    return render_template(
        "app/face_profiles.html",
        devices=devices,
        current_device_id=current_device_id,
        profiles=profiles,
        active_nav="face_profiles",
    )


def _memory_rows_for_template(device_id: str) -> list[dict]:
    from datetime import datetime, timedelta, timezone

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Shanghai")
    except ImportError:
        tz = timezone(timedelta(hours=8))
    rows: list[dict] = []
    for item in list_memory_entries_for_device(device_id):
        ts = float(item.get("created_at") or 0)
        if ts > 0:
            created_fmt = datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_fmt = "—"
        rows.append({**item, "created_at_fmt": created_fmt})
    return rows


@bp.get("/memories")
@login_required
def memories_page():
    devices = list_devices_for_user(current_user.id)
    current_device_id = get_current_device_id()
    memories: list[dict] = []
    if current_device_id and user_owns_device(current_user.id, current_device_id):
        memories = _memory_rows_for_template(current_device_id)
    return render_template(
        "app/memories.html",
        devices=devices,
        current_device_id=current_device_id,
        memories=memories,
        active_nav="memories",
    )


@bp.get("/llm-models")
@login_required
def llm_models_page():
    devices = list_devices_for_user(current_user.id)
    current_device_id = get_current_device_id()
    models: list[dict] = []
    active_model_id: str | None = None
    active_config: dict | None = None
    system_default = resolve_system_llm_config()
    if current_device_id and user_owns_device(current_user.id, current_device_id):
        models = list_llm_models(current_device_id, mask_key=True)
        active_model_id = get_active_model_id(current_device_id)
        try:
            resolved = resolve_llm_config(current_device_id)
            active_config = {
                "display_name": resolved.display_name,
                "model": resolved.model,
                "source": resolved.source,
                "api_base": resolved.api_base or "",
            }
        except ValueError:
            active_config = None
    return render_template(
        "app/llm_models.html",
        devices=devices,
        current_device_id=current_device_id,
        models=models,
        active_model_id=active_model_id,
        active_config=active_config,
        system_default={
            "display_name": system_default.display_name,
            "model": system_default.model,
            "api_base": system_default.api_base or "",
        },
        protocols=SUPPORTED_PROTOCOLS,
        active_nav="llm_models",
    )


@bp.get("/usage")
@login_required
def usage_page():
    summary = get_user_usage_summary(current_user.id, days=14)
    device_summary = get_user_device_usage_summary(current_user.id, days=14)
    keys = list_api_keys_for_user(current_user.id)
    key_usage = []
    for k in keys:
        u = get_api_key_usage_today(k.id)
        key_usage.append(
            {
                "id": k.id,
                "name": k.name,
                "prefix": k.key_prefix,
                "today": u,
            }
        )
    user_today = get_user_usage_today(current_user.id)
    device_daily_rows = _flatten_usage_daily_rows(
        device_summary.get("device_stats") or [],
        label_key="display_name",
        sub_id_key="device_id",
    )
    key_daily_rows = _flatten_usage_daily_rows(
        summary.get("key_stats") or [],
        label_key="name",
        sub_id_key="api_key_id",
        sub_label_key="key_prefix",
    )
    return render_template(
        "app/usage.html",
        summary=summary,
        user_today=user_today,
        device_summary=device_summary,
        key_usage=key_usage,
        device_daily_rows=device_daily_rows,
        key_daily_rows=key_daily_rows,
        active_nav="usage",
    )


@bp.get("/settings")
@login_required
def settings_page():
    user = get_user_by_id(current_user.id)
    keys = list_api_keys_for_user(current_user.id)
    new_key = session.pop("new_api_key_raw", None)
    return render_template(
        "app/account_settings.html",
        user=user,
        api_keys=keys,
        new_api_key=new_key,
        active_nav="account",
    )


@bp.post("/settings/profile")
@login_required
def update_profile_post():
    try:
        update_display_name(current_user.id, request.form.get("display_name") or "")
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("app.settings_page"))
    flash("用户名称已更新", "success")
    return redirect(url_for("app.settings_page"))


@bp.post("/settings/password")
@login_required
def change_password_post():
    old_password = request.form.get("old_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""
    if new_password != confirm:
        flash("两次新密码不一致", "error")
        return redirect(url_for("app.settings_page"))
    try:
        change_password(current_user.id, old_password, new_password)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("app.settings_page"))
    flash("密码已更新", "success")
    return redirect(url_for("app.settings_page"))


@bp.post("/settings/api-keys")
@login_required
def create_api_key_post():
    name = (request.form.get("key_name") or "default").strip()
    try:
        raw, _row = create_api_key(current_user.id, name=name)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("app.settings_page"))
    session["new_api_key_raw"] = raw
    flash("API Key 已创建，请立即复制保存（仅显示一次）", "success")
    return redirect(url_for("app.settings_page"))


@bp.post("/settings/api-keys/<key_id>/revoke")
@login_required
def revoke_api_key_post(key_id: str):
    if not revoke_api_key(current_user.id, key_id):
        flash("API Key 不存在", "error")
    else:
        flash("API Key 已吊销", "success")
    return redirect(url_for("app.settings_page"))


@bp.get("/api/devices")
@login_required
def api_list_devices():
    devices = list_devices_for_user(current_user.id)
    return jsonify(
        {
            "ok": True,
            "devices": [
                {
                    "id": d.id,
                    "device_id": d.device_id,
                    "display_name": d.display_name or d.device_id,
                    "claimed_at": d.claimed_at.isoformat() if d.claimed_at else None,
                }
                for d in devices
            ],
            "current_device_id": get_current_device_id(),
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
    tasks = list_scheduled_tasks_for_device(device_id)
    return jsonify({"ok": True, "device_id": device_id, "tasks": tasks})


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
            protocol=str(payload.get("protocol") or "openai").strip(),
            base_url=str(payload.get("base_url") or "").strip(),
            api_key=str(payload.get("api_key") or "").strip(),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "model": model})


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
