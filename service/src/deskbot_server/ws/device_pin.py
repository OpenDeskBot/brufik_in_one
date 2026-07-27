"""设备 PIN 校验与在线 PIN 缓存（供 Web 绑定验证；设备 WS 可选上报）。"""

from __future__ import annotations

import re
import threading

_PIN_RE = re.compile(r"^[1-9]\d{3}$")

_online_pins: dict[str, str] = {}
_lock = threading.Lock()


def normalize_pin_code(pin_code: str | None) -> str:
    return str(pin_code or "").strip()


def validate_pin_code(pin_code: str | None) -> bool:
    return bool(_PIN_RE.match(normalize_pin_code(pin_code)))


def device_storage_dirname(device_id: str, pin_code: str) -> str:
    did = str(device_id or "").strip()
    pin = normalize_pin_code(pin_code)
    if not did or not validate_pin_code(pin):
        raise ValueError("device_id and valid pin_code required")
    return f"{did}_{pin}"


def set_online_pin(device_id: str, pin_code: str) -> None:
    did = str(device_id or "").strip()
    pin = normalize_pin_code(pin_code)
    if not did or not validate_pin_code(pin):
        return
    with _lock:
        _online_pins[did] = pin


def get_online_pin(device_id: str) -> str | None:
    did = str(device_id or "").strip()
    if not did:
        return None
    with _lock:
        pin = _online_pins.get(did)
        return pin if pin else None


def clear_online_pin(device_id: str) -> None:
    did = str(device_id or "").strip()
    if not did:
        return
    with _lock:
        _online_pins.pop(did, None)


def is_device_online(device_id: str) -> bool:
    return get_online_pin(device_id) is not None


def resolve_pin_for_storage(device_id: str) -> str | None:
    """WS 运行时优先在线 PIN，否则回退 DB 绑定记录。"""
    pin = get_online_pin(device_id)
    if pin:
        return pin
    from deskbot_server.service.user_service import UserService

    dev = UserService().get_device(device_id)
    if dev is not None:
        stored = normalize_pin_code(getattr(dev, "pin_code", None))
        if validate_pin_code(stored):
            return stored
    return None
