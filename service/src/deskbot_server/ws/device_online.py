"""设备在线状态缓存（供 Web 绑定验证）。"""

from __future__ import annotations

import threading

__all__ = ["clear_device_online", "is_device_online", "set_device_online"]

_online_devices: set[str] = set()
_lock = threading.Lock()


def set_device_online(device_id: str) -> None:
    did = str(device_id or "").strip()
    if not did:
        return
    with _lock:
        _online_devices.add(did)


def clear_device_online(device_id: str) -> None:
    did = str(device_id or "").strip()
    if not did:
        return
    with _lock:
        _online_devices.discard(did)


def is_device_online(device_id: str) -> bool:
    did = str(device_id or "").strip()
    if not did:
        return False
    with _lock:
        return did in _online_devices
