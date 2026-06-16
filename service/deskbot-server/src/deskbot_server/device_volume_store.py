"""设备播放音量持久化（``data/device_volume.json`` 或 ``data/device/{id}/device_volume.json``）。"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from deskbot_server.constants import DEVICE_VOLUME_FILE
from deskbot_server.device_data import resolve_json_path
from deskbot_server.pb.servo_pcm import parse_pb_volume

_DEFAULT_VOLUME = 80


def _load_doc(*, device_id: Optional[str] = None) -> dict[str, Any]:
    path = resolve_json_path(DEVICE_VOLUME_FILE, device_id)
    if not os.path.isfile(path):
        return {"default": _DEFAULT_VOLUME, "devices": {}}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return {"default": _DEFAULT_VOLUME, "devices": {}}
    if not isinstance(raw, dict):
        return {"default": _DEFAULT_VOLUME, "devices": {}}
    return raw


def _save_doc(doc: dict[str, Any], *, device_id: Optional[str] = None) -> None:
    path = resolve_json_path(DEVICE_VOLUME_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_default_volume() -> int:
    doc = _load_doc()
    v = parse_pb_volume(doc.get("default"))
    return v if v is not None else _DEFAULT_VOLUME


def get_device_volume(device_id: Optional[str] = None) -> int:
    """读取设备音量；无记录时用 ``default``。"""
    dev = str(device_id or "").strip()
    if dev:
        doc = _load_doc(device_id=dev)
        v = parse_pb_volume(doc.get("default"))
        if v is not None:
            return v
        devices = doc.get("devices")
        if isinstance(devices, dict) and dev in devices:
            v = parse_pb_volume(devices.get(dev))
            if v is not None:
                return v
    doc = _load_doc()
    if dev:
        devices = doc.get("devices")
        if isinstance(devices, dict) and dev in devices:
            v = parse_pb_volume(devices.get(dev))
            if v is not None:
                return v
    v = parse_pb_volume(doc.get("default"))
    return v if v is not None else _DEFAULT_VOLUME


def persist_device_volume(volume: object, *, device_id: Optional[str] = None) -> int:
    """写入音量并落盘；有 ``device_id`` 时只写设备目录，不写全局文件。"""
    v = parse_pb_volume(volume)
    if v is None:
        v = _DEFAULT_VOLUME
    dev = str(device_id or "").strip()
    if dev:
        doc = _load_doc(device_id=dev)
        doc["default"] = v
        _save_doc(doc, device_id=dev)
        return v
    doc = _load_doc()
    doc["default"] = v
    _save_doc(doc)
    return v
