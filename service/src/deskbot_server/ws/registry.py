from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from deskbot_server.utils.util import _format_ts
from deskbot_server.ws.device_pin import clear_device_online, set_device_online

logger = logging.getLogger("deskbot-server")


class DeviceRegistry:
    """维护当前通过 WebSocket 接入的设备会话，是 `/api/devices` 的唯一真源。"""

    def __init__(self) -> None:
        self._devices: dict = {}
        self._ws_to_key: dict = {}
        self._lock = asyncio.Lock()

    async def connect(self, device_id: str, channel: str, ws) -> dict:
        if not device_id:
            return {}
        async with self._lock:
            now = time.time()
            dev = self._devices.get(device_id)
            is_new = dev is None
            if is_new:
                dev = {
                    "device_id": device_id,
                    "first_seen_ts": now,
                    "first_seen": _format_ts(now),
                    "channels": {},
                    "total_connections": 0,
                }
                self._devices[device_id] = dev
            chs = dev.setdefault("channels", {})
            chs[channel] = int(chs.get(channel) or 0) + 1
            dev["last_seen_ts"] = now
            dev["last_seen"] = _format_ts(now)
            dev["online"] = True
            dev["total_connections"] = int(dev.get("total_connections") or 0) + 1
            self._ws_to_key[id(ws)] = (device_id, channel)
            snapshot_ch = dict(chs)
            total_devices = len(self._devices)
            dev_snapshot = dict(dev)
        set_device_online(device_id)
        logger.info(
            "[DeviceRegistry] %s device_id=%s channel=%s channels=%s 设备表容量=%d",
            "注册新设备" if is_new else "复用已注册设备",
            device_id,
            channel,
            snapshot_ch,
            total_devices,
        )
        from deskbot_server.utils.device_data import ensure_device_data_initialized

        try:
            initialized = await asyncio.to_thread(ensure_device_data_initialized, device_id)
            if initialized:
                logger.info("[DeviceRegistry] 已初始化设备数据目录 device_id=%s", device_id)
        except Exception as exc:
            logger.warning("[DeviceRegistry] 初始化设备数据目录失败 device_id=%s err=%s", device_id, exc)
        return dev_snapshot

    async def disconnect(self, ws) -> dict | None:
        async with self._lock:
            key = self._ws_to_key.pop(id(ws), None)
            if key is None:
                return None
            device_id, channel = key
            dev = self._devices.get(device_id)
            if dev is None:
                return None
            now = time.time()
            chs = dev.setdefault("channels", {})
            remain = int(chs.get(channel) or 0) - 1
            if remain <= 0:
                chs.pop(channel, None)
            else:
                chs[channel] = remain
            dev["last_seen_ts"] = now
            dev["last_seen"] = _format_ts(now)
            dev["online"] = bool(chs)
            snapshot_ch = dict(chs)
            still_online = dev["online"]
        if not still_online:
            clear_device_online(device_id)
        logger.info(
            "[DeviceRegistry] 注销 device_id=%s channel=%s 剩余通道=%s online=%s",
            device_id,
            channel,
            snapshot_ch,
            still_online,
        )
        return dict(dev)

    async def touch(self, device_id: str, status: str | None = None) -> None:
        if not device_id:
            return
        async with self._lock:
            dev = self._devices.get(device_id)
            if dev is None:
                return
            now = time.time()
            dev["last_seen_ts"] = now
            dev["last_seen"] = _format_ts(now)
            if status:
                dev["last_status"] = status
            dev["event_count"] = int(dev.get("event_count") or 0) + 1

    async def record_pb_ack(self, device_id: str, ack: dict[str, Any]) -> None:
        if not device_id or not isinstance(ack, dict):
            return
        from deskbot_server.ws.pb_ack_waiter import pb_ack_gate

        await pb_ack_gate.notify(device_id, ack)
        async with self._lock:
            dev = self._devices.get(device_id)
            if dev is None:
                logger.warning("[pb_ack] 设备未在注册表，忽略 device_id=%s", device_id)
                return
            now = time.time()
            dev["last_pb_ack"] = dict(ack)
            dev["last_pb_ack_ts"] = now
            dev["last_pb_ack_mono"] = time.monotonic()

    async def pb_ack_llm_context(self, device_id: str | None) -> str | None:
        if not device_id:
            return None
        async with self._lock:
            dev = self._devices.get(device_id)
            if not dev:
                return None
            ack = dev.get("last_pb_ack")
            if not isinstance(ack, dict):
                return None
            return json.dumps(ack, ensure_ascii=False)

    def snapshot(self) -> list:
        items = [dict(d) for d in self._devices.values()]
        items.sort(key=lambda d: float(d.get("last_seen_ts") or 0.0), reverse=True)
        return items
