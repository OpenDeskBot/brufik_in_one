from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from deskbot_server.util import _format_ts

logger = logging.getLogger("deskbot-server")

class DeviceRegistry:
    """维护当前通过 WebSocket 接入的设备会话，是 `/api/devices` 的唯一真源。

    - 每个设备一条记录：device_id + 在线状态 + 各通道（asr_chat / face_pos / device_pipeline）
      当前的活跃连接数 + 最近一次事件时间。
    - 生产者握手时调用 ``connect``，断开时调用 ``disconnect``；订阅者不入库。
    - 仅保留内存态，没有持久化（重启 deskbot-server 即清空）。
    """

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
        logger.info(
            "[DeviceRegistry] %s device_id=%s channel=%s channels=%s 设备表容量=%d",
            "注册新设备" if is_new else "复用已注册设备",
            device_id,
            channel,
            snapshot_ch,
            total_devices,
        )
        from deskbot_server.device_data import ensure_device_data_initialized

        try:
            initialized = await asyncio.to_thread(ensure_device_data_initialized, device_id)
            if initialized:
                logger.info(
                    "[DeviceRegistry] 已初始化设备数据目录 device_id=%s",
                    device_id,
                )
        except Exception as exc:
            logger.warning(
                "[DeviceRegistry] 初始化设备数据目录失败 device_id=%s err=%s",
                device_id,
                exc,
            )
        return dict(dev)

    async def disconnect(self, ws) -> Optional[dict]:
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
        logger.info(
            "[DeviceRegistry] 注销 device_id=%s channel=%s 剩余通道=%s online=%s",
            device_id,
            channel,
            snapshot_ch,
            still_online,
        )
        return dict(dev)

    async def touch(self, device_id: str, status: Optional[str] = None) -> None:
        """`/asr_chat` 每完成一轮流水线时调用，刷新最后状态与时间。"""
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
        """保存该设备最近一次上行的 ``pb_ack``（内存态，供 LLM 与调试页使用）。"""
        if not device_id or not isinstance(ack, dict):
            return
        from deskbot_server.ws.pb_ack_waiter import pb_ack_gate

        await pb_ack_gate.notify(device_id, ack)
        async with self._lock:
            dev = self._devices.get(device_id)
            if dev is None:
                logger.warning(
                    "[pb_ack] 设备未在注册表，忽略 device_id=%s",
                    device_id,
                )
                return
            now = time.time()
            dev["last_pb_ack"] = dict(ack)
            dev["last_pb_ack_ts"] = now
            dev["last_pb_ack_mono"] = time.monotonic()

    async def pb_ack_llm_context(self, device_id: Optional[str]) -> Optional[str]:
        """返回该设备最近一次 ``pb_ack`` 的紧凑 JSON 字符串；无则 ``None``。"""
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
        items.sort(
            key=lambda d: float(d.get("last_seen_ts") or 0.0),
            reverse=True,
        )
        return items
