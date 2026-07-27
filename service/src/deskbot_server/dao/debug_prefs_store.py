"""调试页偏好：自动应答、人脸跟随模式等，持久化到 ``config.yaml`` 的 ``debug`` 段。"""

from __future__ import annotations

import logging
from typing import Any

from deskbot_server.config import load_config, save_config
from deskbot_server.service.auto_reply import get_asr_voice_auto_reply_enabled, set_asr_voice_auto_reply_enabled

logger = logging.getLogger("deskbot-server")

_VALID_SERVO_AUTO_MODES = frozenset({"", "follow", "follow_frontal", "gaze"})

_camera_servo_auto_mode: str = ""
_live_service_enabled: bool = True


def normalize_camera_servo_auto_mode(raw: object) -> str:
    mode = str(raw or "").strip()
    return mode if mode in _VALID_SERVO_AUTO_MODES else ""


def get_camera_servo_auto_mode() -> str:
    return _camera_servo_auto_mode


def set_camera_servo_auto_mode(mode: object) -> str:
    global _camera_servo_auto_mode
    _camera_servo_auto_mode = normalize_camera_servo_auto_mode(mode)
    return _camera_servo_auto_mode


def get_live_service_enabled() -> bool:
    return _live_service_enabled


def set_live_service_enabled(enabled: bool) -> None:
    global _live_service_enabled
    _live_service_enabled = bool(enabled)


def debug_prefs_snapshot() -> dict[str, Any]:
    return {
        "asr_auto_reply": get_asr_voice_auto_reply_enabled(),
        "live_service": get_live_service_enabled(),
        "camera_servo_auto_mode": get_camera_servo_auto_mode(),
    }


def _persist_debug_section(**fields: Any) -> None:
    cfg = load_config()
    debug = cfg.setdefault("debug", {})
    if not isinstance(debug, dict):
        debug = {}
        cfg["debug"] = debug
    debug.update(fields)
    save_config(cfg)
    logger.info("[debug_prefs] 已写入 config.yaml debug: %s", fields)


def apply_debug_prefs_from_config(cfg: dict[str, Any] | None = None) -> None:
    """启动时从配置加载调试开关到内存。"""
    doc = cfg if isinstance(cfg, dict) else load_config()
    debug = doc.get("debug")
    if not isinstance(debug, dict):
        return
    if "asr_auto_reply" in debug:
        set_asr_voice_auto_reply_enabled(bool(debug.get("asr_auto_reply")))
    if "live_service" in debug:
        set_live_service_enabled(bool(debug.get("live_service")))
    if "camera_servo_auto_mode" in debug:
        mode = debug.get("camera_servo_auto_mode")
        if not get_asr_voice_auto_reply_enabled():
            mode = ""
        set_camera_servo_auto_mode(mode)


def persist_asr_auto_reply(enabled: bool) -> None:
    set_asr_voice_auto_reply_enabled(enabled)
    if not enabled:
        set_camera_servo_auto_mode("")
        _persist_debug_section(asr_auto_reply=False, camera_servo_auto_mode="")
        return
    _persist_debug_section(asr_auto_reply=True)


def persist_live_service(enabled: bool) -> None:
    set_live_service_enabled(enabled)
    _persist_debug_section(live_service=bool(enabled))


def persist_camera_servo_auto_mode(mode: object) -> str:
    norm = set_camera_servo_auto_mode(mode)
    _persist_debug_section(camera_servo_auto_mode=norm)
    return norm
