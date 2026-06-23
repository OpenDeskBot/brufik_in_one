from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user

from deskbot_server.auth.debug_ws_token import issue_debug_ws_token
from deskbot_server.auth.device_service import user_owns_device
from deskbot_server.llm.utils import llm_pb_scenes_prompt_appendix, parse_llm_reply
from deskbot_server.camera_face_config_store import (
    load_camera_face_cfg_file,
    normalize_camera_face_document,
    save_camera_face_cfg_file,
)
from deskbot_server.camera_face_tune import apply_camera_face_tune
from deskbot_server.constants import (
    CAMERA_FACE_CFG_FILE,
    FACE_EXPR_SCENES_FILE,
    FACE_MOUTH_BY_PHONEME_FILE,
    FACE_PROFILES_FILE,
    SCENE_PLAYBOOKS_FILE,
    SERVO_CFG_FILE,
    USER_MEMORY_FILE,
)
from deskbot_server.device_data import (
    load_llm_system_prompt,
    resolve_json_path,
    save_llm_system_prompt,
)
from deskbot_server.face_expr_scenes_store import (
    load_face_expr_scenes_file,
    normalize_face_expr_scenes,
    save_face_expr_scenes_file,
)
from deskbot_server.scene_playbooks_store import (
    load_scene_playbooks_file,
    normalize_scene_playbooks,
    save_scene_playbooks_file,
)
from deskbot_server.face_mouth_config_store import (
    face_mouth_api_payload,
    load_face_mouth_cfg_file,
    normalize_face_mouth_groups,
    save_face_mouth_cfg_file,
)
from deskbot_server.application.face_registration import register_face_for_device
from deskbot_server.face_profiles_store import load_face_profiles
from deskbot_server.memory_store import add_memory, delete_memory, list_memory_for_device
from deskbot_server.util import pcm_to_wav_bytes
from deskbot_server.servo_config_store import (
    load_servo_cfg_file,
    normalize_servo_document,
    save_servo_cfg_file,
)
from deskbot_server.web.helpers import (
    ALLOWED_LLM_ROLES,
    beijing_time_str,
    camera_view_ws_base,
    deskbot_http_base,
    deskbot_ws_default,
    device_pipeline_ws_base,
    load_config,
    phoneme_tts_ws_call,
    tcp_alive,
    tts_phoneme_streaming_url,
)
from deskbot_server.web.session_device import get_current_device_id

bp = Blueprint("debug", __name__)
logger = logging.getLogger("deskbot-server")


def _deny_foreign_device(device_id: str):
    did = (device_id or "").strip()
    if not did:
        return None
    if not user_owns_device(current_user.id, did):
        return jsonify({"ok": False, "error": "无权操作该设备"}), 403
    return None


def _effective_device_id(*, required: bool = True) -> str | None:
    """调试页当前设备：query/body 优先，否则 session。"""
    did = str(request.args.get("device_id") or "").strip()
    if not did and request.method != "GET":
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            did = str(payload.get("device_id") or "").strip()
    if not did:
        did = get_current_device_id() or ""
    if not did:
        return None if not required else None
    return did or None


def _require_device_id():
    did = _effective_device_id(required=True)
    if not did:
        return None, (jsonify({"ok": False, "error": "请先选择设备", "t": time.time()}), 400)
    denied = _deny_foreign_device(did)
    if denied:
        return None, denied
    return did, None


@bp.get("/health")
def health_ok():
    return ("ok", 200)


@bp.get("/api/debug/ws_token")
def api_debug_ws_token():
    """已登录用户获取调试台 WebSocket 令牌（默认 7 天有效）。"""
    info = issue_debug_ws_token(current_user.id)
    return jsonify(
        {
            "ok": True,
            "token": info.token,
            "expires_in": info.expires_in,
        }
    )


@bp.get("/debug/devices")
def debug_devices():
    from deskbot_server.pb.display import FACE_LCD_HEIGHT, FACE_LCD_WIDTH
    from deskbot_server.pb.shapes import _default_mouth_fallback_shape, default_face_circles

    fc = default_face_circles()
    mouth_fb = _default_mouth_fallback_shape().get("elements") or []
    expr_default_anim = {
        "elements": {
            "nose": fc.get("nose") or [],
            "eye_l": fc.get("eye_l") or [],
            "eye_r": fc.get("eye_r") or [],
            "mouth": mouth_fb,
            "extra": [],
        },
    }
    from deskbot_server.auth.device_service import list_devices_for_user

    owned = list_devices_for_user(current_user.id)
    owned_device_rows = [
        {"device_id": d.device_id, "display_name": d.display_name or d.device_id}
        for d in owned
    ]
    current = get_current_device_id()
    owned_ids = {d.device_id for d in owned}
    if current and current not in owned_ids:
        current = owned[0].device_id if owned else None

    debug_ws = issue_debug_ws_token(current_user.id)

    return render_template(
        "debug_devices.html",
        active_nav="devices",
        device_pipeline_ws_base=device_pipeline_ws_base(),
        camera_view_ws_base=camera_view_ws_base(),
        deskbot_http_base=deskbot_http_base(),
        deskbot_debug_ws_token=debug_ws,
        face_lcd_w=FACE_LCD_WIDTH,
        face_lcd_h=FACE_LCD_HEIGHT,
        expr_default_anim=expr_default_anim,
        current_device_id=current or "",
        owned_device_rows=owned_device_rows,
    )


@bp.get("/debug/tts")
def debug_tts():
    from deskbot_server.tts.doubao import load_doubao_tts_config
    from deskbot_server.tts.speakers import list_doubao_tts_speaker_presets

    cfg = load_doubao_tts_config()
    initial = {
        "api_key": "",
        "api_key_set": bool(cfg.api_key),
        "speaker": cfg.speaker,
        "resource_id": cfg.resource_id,
        "model": cfg.model,
        "ws_url": cfg.ws_url,
        "sample_rate": cfg.sample_rate,
        "audio_format": cfg.audio_format,
    }
    return render_template(
        "debug_tts.html",
        active_nav="tts",
        initial_config=initial,
        speaker_presets=list_doubao_tts_speaker_presets(),
    )


@bp.get("/api/doubao_tts/speakers")
def api_doubao_tts_speakers():
    from deskbot_server.tts.speakers import list_doubao_tts_speaker_presets

    return jsonify({"ok": True, "speakers": list_doubao_tts_speaker_presets(), "t": time.time()})


@bp.get("/api/doubao_tts/config")
def api_doubao_tts_config_get():
    from deskbot_server.tts.doubao import load_doubao_tts_config

    cfg = load_doubao_tts_config()
    return jsonify({"ok": True, "config": cfg.masked(), "t": time.time()})


@bp.post("/api/doubao_tts/config")
def api_doubao_tts_config_post():
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
    return jsonify({"ok": True, "config": cfg.masked(), "t": time.time()})


def _doubao_cfg_from_payload(payload: dict):
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


@bp.post("/api/doubao_tts/synthesize")
def api_doubao_tts_synthesize():
    from deskbot_server.tts.doubao import synthesize_doubao_tts

    payload = request.get_json(force=True, silent=True) or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "空文本"}), 400
    cfg = _doubao_cfg_from_payload(payload)
    t0 = time.monotonic()
    try:
        result = asyncio.run(synthesize_doubao_tts(text, cfg))
    except ValueError as exc:
        logger.warning("豆包 TTS 参数错误: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.exception(
            "豆包 TTS 合成失败 text_len=%d speaker=%r resource_id=%s elapsed_ms=%d",
            len(text),
            cfg.speaker,
            cfg.resource_id,
            elapsed_ms,
        )
        return jsonify({"ok": False, "error": str(exc), "elapsed_ms": elapsed_ms}), 502
    wav = pcm_to_wav_bytes(result.pcm, result.sample_rate)
    return jsonify(
        {
            "ok": True,
            "sample_rate": result.sample_rate,
            "pcm_total_bytes": len(result.pcm),
            "elapsed_ms": result.elapsed_ms,
            "events": result.events,
            "speaker": cfg.speaker,
            "ws_url": cfg.ws_url,
            "wav_base64": base64.b64encode(wav).decode("ascii"),
        }
    )


@bp.get("/debug/llm")
def debug_llm():
    from deskbot_server.auth.device_service import list_devices_for_user
    from deskbot_server.llm.utils import llm_pb_plan_prompt_appendix

    cfg = load_config()
    llm_cfg = cfg.get("llm", {}) or {}
    owned = list_devices_for_user(current_user.id)
    owned_device_rows = [
        {"device_id": d.device_id, "display_name": d.display_name or d.device_id}
        for d in owned
    ]
    current = get_current_device_id()
    owned_ids = {d.device_id for d in owned}
    if current and current not in owned_ids:
        current = owned[0].device_id if owned else None
    initial_prompt = load_llm_system_prompt(current) if current else load_llm_system_prompt()

    return render_template(
        "debug_llm.html",
        active_nav="llm",
        llm_model=llm_cfg.get("model_name", ""),
        llm_base_url=llm_cfg.get("base_url", ""),
        llm_system_prompt=initial_prompt,
        llm_plan_appendix=llm_pb_plan_prompt_appendix(device_id=current or None),
        current_device_id=current or "",
        owned_device_rows=owned_device_rows,
    )


@bp.get("/debug/paddlespeech")
def debug_paddlespeech():
    cfg = load_config()
    tts = cfg.get("tts") or {}
    return render_template(
        "debug_paddlespeech.html",
        active_nav="paddle",
        default_spk=int(tts.get("spk_id", 0)),
        sample_rate=int(tts.get("sample_rate", 24000)),
        phoneme_ws_url=tts_phoneme_streaming_url(cfg),
    )


@bp.get("/debug/simulation")
def debug_simulation():
    from deskbot_server.pb.display import FACE_LCD_HEIGHT, FACE_LCD_WIDTH
    from deskbot_server.pb.shapes import default_face_circles

    cfg = load_config()
    tts = cfg.get("tts") or {}
    return render_template(
        "debug_simulation.html",
        active_nav="sim",
        default_spk=int(tts.get("spk_id", 0)),
        sample_rate=int(tts.get("sample_rate", 24000)),
        face_lcd_w=FACE_LCD_WIDTH,
        face_lcd_h=FACE_LCD_HEIGHT,
        default_face_circles=default_face_circles(),
    )


@bp.post("/api/paddlespeech/phoneme_tts")
def api_paddlespeech_phoneme_tts():
    """服务端代理调用 ``streaming_phoneme``，返回音素表与整段 WAV（base64）供页面播放。"""
    payload = request.get_json(force=True, silent=True) or {}
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "空文本"}), 400
    spk_id = int(payload.get("spk_id", 0))
    cfg = load_config()
    ws_url = tts_phoneme_streaming_url(cfg)
    sr = int((cfg.get("tts") or {}).get("sample_rate", 24000))
    try:
        pcm, display = asyncio.run(phoneme_tts_ws_call(ws_url, text, spk_id))
    except Exception as exc:  # pragma: no cover
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 502
    wav = pcm_to_wav_bytes(pcm, sr)
    return jsonify(
        {
            "ok": True,
            "ws_url_used": ws_url,
            "sample_rate": sr,
            "segments": display,
            "wav_base64": base64.b64encode(wav).decode("ascii"),
            "pcm_total_bytes": len(pcm),
        }
    )


@bp.get("/api/llm/system_prompt")
def api_llm_system_prompt_get():
    device_id, err = _require_device_id()
    if err:
        return err
    return jsonify(
        {
            "ok": True,
            "device_id": device_id,
            "system_prompt": load_llm_system_prompt(device_id),
            "file": "llm_system.txt",
            "t": time.time(),
        }
    )


@bp.post("/api/llm/system_prompt")
def api_llm_system_prompt_post():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object", "t": time.time()}), 400
    device_id, err = _require_device_id()
    if err:
        return err
    content = str(payload.get("system_prompt") or payload.get("content") or "")
    try:
        path = save_llm_system_prompt(content, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "device_id": device_id,
            "system_prompt": load_llm_system_prompt(device_id),
            "file": os.path.basename(path),
            "t": time.time(),
        }
    )


@bp.post("/api/llm/chat")
def llm_chat():
    payload = request.get_json(force=True, silent=True) or {}
    user_text = str(payload.get("text") or "").strip()
    raw_history = payload.get("history") or []

    if not user_text:
        return jsonify({"ok": False, "error": "空文本"}), 400

    cfg = load_config()
    llm_cfg = cfg.get("llm", {}) or {}
    debug_device_id = str(payload.get("device_id") or "").strip()
    if debug_device_id:
        denied = _deny_foreign_device(debug_device_id)
        if denied:
            return denied

    default_system_prompt = load_llm_system_prompt(debug_device_id or None) or llm_cfg.get(
        "system_prompt", "你是中文助手，请简洁回答。每次回答不超过50字"
    )
    raw_sys = payload.get("system_prompt")
    if isinstance(raw_sys, str) and raw_sys.strip():
        system_prompt = raw_sys
    else:
        system_prompt = default_system_prompt

    from deskbot_server.llm.runtime import resolve_llm_config

    try:
        llm_runtime_cfg = resolve_llm_config(debug_device_id or None)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not llm_runtime_cfg.api_key or "请替换" in llm_runtime_cfg.api_key:
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "LLM API Key 未配置（设备 LLM 管理或环境变量 LLM_API_KEY / DASHSCOPE_API_KEY）",
                }
            ),
            400,
        )

    sys_content = (
        f"{system_prompt}\n当前时间是: {beijing_time_str()}（北京时间，东八区）"
    )
    from deskbot_server.llm.utils import (
        llm_device_screen_appendix,
        llm_static_context_prompt_appendix,
    )
    from deskbot_server.llm.user_message import build_llm_user_message

    sys_content += "\n" + llm_device_screen_appendix(debug_device_id or None)
    px = llm_pb_scenes_prompt_appendix(device_id=debug_device_id or None)
    if px:
        sys_content += "\n" + px
    fx = llm_static_context_prompt_appendix(debug_device_id or None)
    if fx:
        sys_content += "\n\n" + fx

    raw_ctx = payload.get("device_context")
    device_context: str | None = None
    if isinstance(raw_ctx, dict):
        device_context = json.dumps(raw_ctx, ensure_ascii=False)
    elif isinstance(raw_ctx, str) and raw_ctx.strip():
        device_context = raw_ctx.strip()

    messages = [
        {
            "role": "system",
            "content": sys_content,
        }
    ]
    for item in raw_history:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "")
        if role not in ALLOWED_LLM_ROLES or not content:
            continue
        if role == "system":
            continue
        messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": build_llm_user_message(
                user_text,
                device_id=debug_device_id or None,
                device_context=device_context,
            ),
        }
    )

    try:
        from deskbot_server.llm.runtime import litellm_completion
    except Exception as exc:  # pragma: no cover
        return jsonify({"ok": False, "error": f"litellm 未安装: {exc}"}), 500

    try:
        t0 = time.monotonic()
        raw, meta = litellm_completion(
            messages,
            device_id=debug_device_id or None,
            temperature=float(payload.get("temperature", 0.7)),
            config=llm_runtime_cfg,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        parsed = parse_llm_reply(raw)
        return jsonify(
            {
                "ok": True,
                "reply": parsed["reply"],
                "raw": parsed["raw"],
                "moves": parsed.get("moves") or [],
                "anims": parsed.get("anims") or [],
                "tools": parsed.get("tools") or [],
                "servo": parsed.get("servo") or [],
                "scenes": parsed.get("scenes") or [],
                "json_ok": parsed["json_ok"],
                "need_reply": parsed.get("need_reply", True),
                "model": meta.get("model"),
                "model_source": meta.get("source"),
                "model_display_name": meta.get("display_name"),
                "elapsed_ms": elapsed_ms,
                "usage": meta.get("usage"),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500


@bp.get("/api/servo_config")
def api_servo_config_get():
    device_id, err = _require_device_id()
    if err:
        return err
    cfg_path = resolve_json_path(SERVO_CFG_FILE, device_id)
    try:
        cfg = load_servo_cfg_file(device_id=device_id)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    if cfg is None:
        return jsonify(
            {
                "ok": True,
                "exists": False,
                "file": os.path.basename(cfg_path),
                "device_id": device_id,
                "t": time.time(),
            }
        )
    return jsonify(
        {
            "ok": True,
            "exists": True,
            "config": cfg,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.post("/api/servo_config")
def api_servo_config_post():
    device_id, err = _require_device_id()
    if err:
        return err
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object", "t": time.time()}), 400
    cfg_path = resolve_json_path(SERVO_CFG_FILE, device_id)
    try:
        cfg = normalize_servo_document(payload, require_presets=True)
        save_servo_cfg_file(cfg, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "config": cfg,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.get("/api/camera_face_config")
def api_camera_face_config_get():
    device_id, err = _require_device_id()
    if err:
        return err
    cfg_path = resolve_json_path(CAMERA_FACE_CFG_FILE, device_id)
    cfg = load_config()
    base = dict(cfg.get("camera_face") or {})
    try:
        file_cfg = load_camera_face_cfg_file(device_id=device_id)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    merged = {**base, **(file_cfg or {})}
    try:
        norm = normalize_camera_face_document(merged)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "config": norm,
            "file": os.path.basename(cfg_path),
            "exists": file_cfg is not None,
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.post("/api/camera_face_config")
def api_camera_face_config_post():
    device_id, err = _require_device_id()
    if err:
        return err
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object", "t": time.time()}), 400
    cfg_path = resolve_json_path(CAMERA_FACE_CFG_FILE, device_id)
    try:
        cfg = normalize_camera_face_document(payload)
        save_camera_face_cfg_file(cfg, device_id=device_id)
        apply_camera_face_tune(cfg)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "config": cfg,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "hint": "检测器 num_faces/置信度需 ESP32 重连 /asr_chat 后生效",
            "t": time.time(),
        }
    )


@bp.get("/api/face_profiles")
def api_face_profiles_get():
    device_id, err = _require_device_id()
    if err:
        return err
    cfg_path = resolve_json_path(FACE_PROFILES_FILE, device_id)
    try:
        profiles = load_face_profiles(device_id=device_id)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "profiles": profiles,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.post("/api/face_profiles/register")
def api_face_profiles_register():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object", "t": time.time()}), 400
    name = str(payload.get("name") or "").strip()
    device_id = str(payload.get("device_id") or "").strip()
    face_id_raw = payload.get("face_id")
    if not name:
        return jsonify({"ok": False, "error": "name required", "t": time.time()}), 400
    if not device_id or face_id_raw is None:
        return jsonify({"ok": False, "error": "device_id and face_id required", "t": time.time()}), 400
    denied = _deny_foreign_device(device_id)
    if denied:
        return denied
    try:
        face_id = int(face_id_raw)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "face_id must be int", "t": time.time()}), 400
    try:
        out = register_face_for_device(device_id, name, face_id=face_id, extra=payload)
        profile = out["profile"]
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 400
    return jsonify(
        {
            "ok": True,
            "profile": profile,
            "file": os.path.basename(resolve_json_path(FACE_PROFILES_FILE, device_id)),
            "device_id": device_id,
            "hint": (
                "档案已写入（InsightFace 512 维）；请 ESP32 重连 /asr_chat 后正对镜头识别。"
                "旧几何档案需重新「保存人名」"
            ),
            "t": time.time(),
        }
    )


@bp.get("/api/user_memory")
def api_user_memory_get():
    device_id, err = _require_device_id()
    if err:
        return err
    cfg_path = resolve_json_path(USER_MEMORY_FILE, device_id)
    try:
        entries = list_memory_for_device(device_id)
    except (OSError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "entries": entries,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.post("/api/user_memory")
def api_user_memory_post():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object", "t": time.time()}), 400
    text = str(payload.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "text required", "t": time.time()}), 400
    device_id, err = _require_device_id()
    if err:
        return err
    try:
        entry = add_memory(text, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 400
    return jsonify({"ok": True, "entry": entry, "t": time.time()})


@bp.delete("/api/user_memory/<entry_id>")
def api_user_memory_delete(entry_id: str):
    device_id, err = _require_device_id()
    if err:
        return err
    try:
        ok = delete_memory(entry_id, device_id=device_id)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    if not ok:
        return jsonify({"ok": False, "error": "not found", "t": time.time()}), 404
    return jsonify({"ok": True, "id": entry_id, "t": time.time()})


@bp.get("/api/face_mouth_by_phoneme")
def api_face_mouth_by_phoneme_get():
    device_id, err = _require_device_id()
    if err:
        return err
    cfg_path = resolve_json_path(FACE_MOUTH_BY_PHONEME_FILE, device_id)
    try:
        groups = load_face_mouth_cfg_file(seed_if_missing=True, device_id=device_id)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    payload = face_mouth_api_payload(groups or [])
    return jsonify(
        {
            "ok": True,
            "exists": os.path.isfile(cfg_path),
            **payload,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.post("/api/face_mouth_by_phoneme")
def api_face_mouth_by_phoneme_post():
    device_id, err = _require_device_id()
    if err:
        return err
    payload = request.get_json(silent=True)
    cfg_path = resolve_json_path(FACE_MOUTH_BY_PHONEME_FILE, device_id)
    try:
        groups = normalize_face_mouth_groups(payload if payload is not None else [])
        save_face_mouth_cfg_file(groups, device_id=device_id)
        out = face_mouth_api_payload(groups)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            **out,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.get("/api/face_expr_scenes")
def api_face_expr_scenes_get():
    device_id, err = _require_device_id()
    if err:
        return err
    cfg_path = resolve_json_path(FACE_EXPR_SCENES_FILE, device_id)
    try:
        rows = load_face_expr_scenes_file(seed_if_missing=True, device_id=device_id)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "config": rows or [],
            "exists": os.path.isfile(cfg_path),
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.post("/api/face_expr_scenes")
def api_face_expr_scenes_post():
    device_id, err = _require_device_id()
    if err:
        return err
    payload = request.get_json(silent=True)
    cfg_path = resolve_json_path(FACE_EXPR_SCENES_FILE, device_id)
    try:
        rows = normalize_face_expr_scenes(payload if payload is not None else [])
        saved = save_face_expr_scenes_file(rows, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "config": saved,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.get("/api/scene_playbooks")
def api_scene_playbooks_get():
    device_id, err = _require_device_id()
    if err:
        return err
    cfg_path = resolve_json_path(SCENE_PLAYBOOKS_FILE, device_id)
    try:
        rows = load_scene_playbooks_file(seed_if_missing=True, device_id=device_id)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "config": rows or [],
            "exists": os.path.isfile(cfg_path),
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.post("/api/scene_playbooks")
def api_scene_playbooks_post():
    device_id, err = _require_device_id()
    if err:
        return err
    payload = request.get_json(silent=True)
    cfg_path = resolve_json_path(SCENE_PLAYBOOKS_FILE, device_id)
    try:
        rows = normalize_scene_playbooks(payload if payload is not None else [])
        save_scene_playbooks_file(rows, device_id=device_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc), "t": time.time()}), 500
    return jsonify(
        {
            "ok": True,
            "config": rows,
            "file": os.path.basename(cfg_path),
            "device_id": device_id,
            "t": time.time(),
        }
    )


@bp.get("/api/health")
def health():
    cfg = load_config()
    deskbot_host = os.environ.get("DESKBOT_SERVER_HOST") or cfg.get("server", {}).get("host", "127.0.0.1")
    if deskbot_host == "0.0.0.0":
        deskbot_host = "127.0.0.1"
    deskbot_port = int(os.environ.get("DESKBOT_SERVER_PORT") or cfg.get("server", {}).get("port", 9000))

    tts_url = os.environ.get("TTS_WS_URL") or cfg.get(
        "tts", {}
    ).get("ws_url", "ws://127.0.0.1:8092/paddlespeech/tts/streaming")
    # 简单解析 ws://host:port/path
    tts_host = "127.0.0.1"
    tts_port = 8092
    try:
        remain = tts_url.split("://", 1)[1]
        host_port = remain.split("/", 1)[0]
        tts_host = host_port.split(":")[0]
        tts_port = int(host_port.split(":")[1])
    except Exception:
        pass

    return jsonify(
        {
            "deskbot_server": tcp_alive(deskbot_host, deskbot_port),
            "tts_server": tcp_alive(tts_host, tts_port),
            "deskbot_target": f"{deskbot_host}:{deskbot_port}",
            "tts_target": f"{tts_host}:{tts_port}",
        }
    )


