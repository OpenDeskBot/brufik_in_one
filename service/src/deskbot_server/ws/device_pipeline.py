from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Optional

from websockets.exceptions import ConnectionClosed

from deskbot_server.constants import DEVICE_PIPELINE_MAX_EVENTS
from deskbot_server.util import (
    _extract_device_id,
    _format_ts,
    _json_msg,
    _parse_query,
    _peer_str,
    _split_path,
    _ws_request_path,
)
from deskbot_server.ws.registry import DeviceRegistry
from deskbot_server.ws.ws_send import _PerWsFireAndForget, _safe_send

logger = logging.getLogger("deskbot-server")

# 高频自动下发在流水窗口内按设备去重（只保留最新一条，避免淹没对话记录）
_PIPELINE_DEDUPE_SOURCES = frozenset({"auto_idle_silence"})


def _pipeline_dedupe_request_id(source: str, device_id: str) -> Optional[str]:
    if source in _PIPELINE_DEDUPE_SOURCES:
        return f"__{source}__:{device_id}"
    return None


class DevicePipelineBroker:
    """维护设备流水线事件的滚动窗口（默认最近 100 条，最新在前），并向订阅者实时广播。

    - 生产者：设备或上游服务把 ASR/LLM/TTS 各阶段的指标推过来。
    - 订阅者：web 后台订阅全部设备或按 device_id 过滤。
    """

    def __init__(self, max_events: int = DEVICE_PIPELINE_MAX_EVENTS) -> None:
        self._max_events = max_events
        self._events: deque = deque(maxlen=max_events)
        # ws -> Optional[str] 过滤的 device_id；None 表示全部
        self._subscribers: dict = {}
        self._lock = asyncio.Lock()
        self._seq = 0
        self._fanout = _PerWsFireAndForget()

    @property
    def max_events(self) -> int:
        return self._max_events

    async def has_subscribers_for_device(self, device_id: Optional[str] = None) -> bool:
        """是否有调试订阅者在监听该设备（或全部设备）。"""
        device_id = str(device_id or "").strip() or None
        async with self._lock:
            for _ws, flt in self._subscribers.items():
                if not flt or flt == device_id:
                    return True
        return False

    async def publish(self, event: dict) -> dict:
        async with self._lock:
            self._seq += 1
            evt = dict(event)
            evt["seq"] = self._seq
            if evt.get("received_ts") is None:
                evt["received_ts"] = time.time()
            if not evt.get("received_at"):
                evt["received_at"] = _format_ts(float(evt["received_ts"]))
            device_id = str(evt.get("device_id") or "unknown")
            evt["device_id"] = device_id
            source = str(evt.get("source") or "")
            dedupe_rid = _pipeline_dedupe_request_id(source, device_id)
            if dedupe_rid:
                evt["request_id"] = dedupe_rid
                self._events = deque(
                    (
                        e
                        for e in self._events
                        if e.get("request_id") != dedupe_rid
                    ),
                    maxlen=self._max_events,
                )
            else:
                rid = str(evt.get("request_id") or "").strip()
                if rid:
                    self._events = deque(
                        (e for e in self._events if e.get("request_id") != rid),
                        maxlen=self._max_events,
                    )
            self._events.appendleft(evt)

            targets = []
            for ws, flt in self._subscribers.items():
                if flt and flt != device_id:
                    continue
                targets.append(ws)

        msg = json.dumps({"type": "pipeline_event", "event": evt})
        for ws in targets:
            self._fanout.submit(ws, msg)
        return evt

    async def add_subscriber(self, ws, device_filter: Optional[str] = None) -> None:
        async with self._lock:
            self._subscribers[ws] = device_filter
            if device_filter:
                snap = [e for e in self._events if e.get("device_id") == device_filter]
            else:
                snap = list(self._events)
            max_events = self._max_events
        self._fanout.submit(
            ws,
            json.dumps(
                {
                    "type": "pipeline_snapshot",
                    "items": snap,
                    "device_filter": device_filter,
                    "max_events": max_events,
                }
            ),
        )

    async def remove_subscriber(self, ws) -> None:
        async with self._lock:
            self._subscribers.pop(ws, None)
        self._fanout.discard(ws)

    async def broadcast_to_device(self, device_id: str, payload: dict) -> None:
        """向订阅了该 device_id（或订阅全部）的订阅者广播一条原始消息，不进入事件窗口。

        用于 ``pipeline_stage`` / ``face_pos`` 等实时推送——它们不是一轮完整事件，
        不适合进入 ``pipeline_recent`` 的滚动窗口。
        """
        device_id = str(device_id or "unknown")
        async with self._lock:
            targets = [
                ws
                for ws, flt in self._subscribers.items()
                if not flt or flt == device_id
            ]
        if not targets:
            return
        msg = json.dumps(payload, ensure_ascii=False)
        for ws in targets:
            self._fanout.submit(ws, msg)

    def snapshot_events(self, device_id: Optional[str] = None, limit: int = 100) -> list:
        if device_id:
            items = [e for e in self._events if e.get("device_id") == device_id]
        else:
            items = list(self._events)
        if limit > 0:
            items = items[:limit]
        return items

    @staticmethod
    def normalize_event(
        data: dict, default_device_id: Optional[str] = None
    ) -> Optional[dict]:
        """把任意上报字典规范化为统一的流水线事件结构。"""
        if not isinstance(data, dict):
            return None
        device_id = data.get("device_id") or data.get("id") or default_device_id
        if not device_id:
            return None
        device_id = str(device_id)

        def _fnum(key: str) -> Optional[float]:
            v = data.get(key)
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        err = data.get("error")
        status_raw = str(data.get("status") or "").strip().lower()
        if status_raw in ("fail", "failed", "error", "err"):
            status = "error"
        elif status_raw in ("ok", "success", "succeed", "succeeded"):
            status = "ok"
        else:
            status = "error" if err else "ok"

        evt: dict = {
            "device_id": device_id,
            "asr_text": data.get("asr_text"),
            "asr_ms": _fnum("asr_ms"),
            "llm_text": data.get("llm_text"),
            "llm_ms": _fnum("llm_ms"),
            "tts_text": data.get("tts_text"),
            "tts_ms": _fnum("tts_ms"),
            "pb_ms": _fnum("pb_ms"),
            "e2e_ms": _fnum("e2e_ms"),
            "status": status,
            "error": err,
        }
        if evt["e2e_ms"] is None:
            evt["e2e_ms"] = _fnum("total_ms")
        cts = _fnum("received_ts") or _fnum("ts")
        if cts is not None and cts > 0:
            evt["received_ts"] = cts
            evt["received_at"] = _format_ts(cts)
        return evt


async def publish_auto_dispatch_event(
    broker: Optional["DevicePipelineBroker"],
    *,
    device_id: str,
    request_id: str,
    source: str,
    summary: str,
    status: str = "ok",
    error: Optional[str] = None,
) -> None:
    """无交互自动下发写入流水窗口（idle、人脸跟随等）。"""
    if broker is None or not device_id:
        return
    evt: dict = {
        "device_id": device_id,
        "request_id": request_id,
        "source": source,
        "summary": summary,
        "llm_text": summary,
        "status": status,
        "error": error,
    }
    await broker.publish(evt)


async def handle_device_pipeline(
    websocket,
    broker: DevicePipelineBroker,
    registry: DeviceRegistry,
) -> None:
    """/device_pipeline WS 入口。

    协议：
      - 生产者连接 URL 形如 ``ws://host:9000/device_pipeline?device_id=xxx``，device_id 必填；
        同一连接上报的事件都会绑定到该 device_id。
      - 订阅者连接 URL 形如 ``ws://host:9000/device_pipeline?role=subscriber&device_id=xxx``，
        device_id 可选，作为过滤条件；不传则收到全部设备事件。
    """
    req_path = _ws_request_path(websocket)
    _, query = _split_path(req_path)
    qargs = _parse_query(query)
    role = (qargs.get("role") or "").lower() or None
    url_device = _extract_device_id(qargs)
    is_subscriber = role in ("subscriber", "sub", "viewer", "consumer")
    peer = _peer_str(websocket)

    await _safe_send(
        websocket,
        _json_msg(
            {
                "type": "ready",
                "channel": "device_pipeline",
                "max_events": broker.max_events,
                "device_id": None if is_subscriber else url_device,
                "device_filter": url_device if is_subscriber else None,
            }
        ),
    )

    try:
        if is_subscriber:
            logger.info(
                "[/device_pipeline] 订阅者接入 peer=%s device_filter=%s",
                peer,
                url_device,
            )
            await broker.add_subscriber(websocket, url_device)
            try:
                async for msg in websocket:
                    if isinstance(msg, (bytes, bytearray)):
                        continue
                    try:
                        d = json.loads(msg)
                    except Exception:
                        continue
                    if d.get("type") == "ping":
                        await _safe_send(websocket, _json_msg({"type": "pong"}))
            finally:
                await broker.remove_subscriber(websocket)
            return

        if not url_device:
            logger.warning(
                "[/device_pipeline] 拒绝生产者：缺失 device_id peer=%s path=%s —— "
                "需用 ws://host:9000/device_pipeline?device_id=<设备ID>",
                peer,
                req_path,
            )
            await _safe_send(
                websocket,
                _json_msg(
                    {
                        "type": "error",
                        "message": "producer 必须在 URL 中携带 device_id",
                    }
                ),
            )
            await websocket.close(code=1008, reason="device_id required")
            return

        logger.info(
            "[/device_pipeline] 生产者接入 device_id=%s peer=%s",
            url_device,
            peer,
        )
        await registry.connect(url_device, "device_pipeline", websocket)
        try:
            async for message in websocket:
                if isinstance(message, (bytes, bytearray)):
                    continue
                try:
                    data = json.loads(message)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue

                msg_type = str(data.get("type") or "").lower()
                if msg_type == "ping":
                    await _safe_send(websocket, _json_msg({"type": "pong"}))
                    continue

                evt = DevicePipelineBroker.normalize_event(
                    data, default_device_id=url_device
                )
                if not evt:
                    await _safe_send(
                        websocket,
                        _json_msg(
                            {
                                "type": "pipeline_rejected",
                                "reason": "invalid_payload",
                            }
                        ),
                    )
                    continue
                evt["device_id"] = url_device

                stored = await broker.publish(evt)
                await registry.touch(url_device, evt.get("status"))
                await _safe_send(
                    websocket,
                    _json_msg({"type": "pipeline_ack", "seq": stored["seq"]}),
                )
        finally:
            await registry.disconnect(websocket)
    except ConnectionClosed as closed:
        logger.info("/device_pipeline WS 已关闭: %s", closed)
