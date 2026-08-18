"""设备事件总线：滚动窗口 + WS 订阅广播 + 回调 pub/sub。

DeviceController 发布事件，WebController 订阅展示，二者通过 BusService 解耦。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Coroutine

from deskbot_server.constants import DEVICE_PIPELINE_MAX_EVENTS
from deskbot_server.utils.singleton import SingletonMeta
from deskbot_server.utils.util import _format_ts
from deskbot_server.ws.ws_send import _PerWsFireAndForget

logger = logging.getLogger("deskbot-server")

SubscriberFn = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]

# 高频自动下发在流水窗口内按设备去重（只保留最新一条，避免淹没对话记录）
_DEDUPE_SOURCES = frozenset({"auto_idle_silence"})


def _dedupe_request_id(source: str, device_id: str) -> str | None:
    if source in _DEDUPE_SOURCES:
        return f"__{source}__:{device_id}"
    return None


class BusService(metaclass=SingletonMeta):
    """设备事件总线。

    职责：
    - 滚动窗口：存最近 N 条完整事件，供 ``GET /api/pipeline_recent`` 查询。
    - WS 订阅广播：web 调试页面通过 ``subscribe_ws()`` 接收实时事件。
    - 回调 pub/sub：应用层通过 ``sub()``/``pub()`` 注册回调（与 WS 广播独立）。

    - ``pub()``          ：写入滚动窗口 + WS 广播 + 回调通知。
    - ``broadcast()``    ：纯 WS 广播，不写入窗口（pipeline_stage、camera_frame 等）。
    - ``subscribe_ws()`` / ``unsubscribe_ws()``：web 调试页面 WS 订阅管理。
    - ``sub()`` / ``unsub()``：应用层回调订阅。
    """

    def __init__(self, max_events: int = DEVICE_PIPELINE_MAX_EVENTS) -> None:
        self._max_events = max_events
        self._events: deque = deque(maxlen=max_events)
        # ws -> str | None  过滤的 device_id；None 表示全部
        self._ws_subscribers: dict = {}
        self._lock = asyncio.Lock()
        self._seq = 0
        self._fanout = _PerWsFireAndForget()
        # 回调订阅：device_id -> {sub_id -> fn}
        self._subs: dict[str, dict[str, SubscriberFn]] = defaultdict(dict)

    @property
    def max_events(self) -> int:
        return self._max_events

    # ── 回调 pub/sub ──────────────────────────────────────────────────

    def sub(self, sub_id: str, device_id: str, fn: SubscriberFn) -> None:
        """订阅设备消息回调。同一 sub_id 对同一 device_id 重复调用会覆盖。"""
        self._subs[device_id][sub_id] = fn

    def unsub(self, sub_id: str, device_id: str) -> None:
        """取消回调订阅。"""
        subs = self._subs.get(device_id)
        if subs is not None:
            subs.pop(sub_id, None)
            if not subs:
                del self._subs[device_id]

    def unsub_all(self, sub_id: str) -> None:
        """取消某订阅者在所有设备上的回调订阅。"""
        empty_keys: list[str] = []
        for device_id, subs in self._subs.items():
            subs.pop(sub_id, None)
            if not subs:
                empty_keys.append(device_id)
        for k in empty_keys:
            del self._subs[k]

    # ── 发布 ──────────────────────────────────────────────────────────

    async def pub(self, device_id: str, event: dict[str, Any]) -> dict:
        """发布事件：写入滚动窗口 + WS 广播 + 回调通知。返回带 seq 的事件副本。"""
        async with self._lock:
            self._seq += 1
            evt = dict(event)
            evt["seq"] = self._seq
            device_id = str(device_id or "unknown")
            evt["device_id"] = device_id
            if evt.get("received_ts") is None:
                evt["received_ts"] = time.time()
            if not evt.get("received_at"):
                evt["received_at"] = _format_ts(float(evt["received_ts"]))

            source = str(evt.get("source") or "")
            dedupe_rid = _dedupe_request_id(source, device_id)
            if dedupe_rid:
                evt["request_id"] = dedupe_rid
                self._events = deque(
                    (e for e in self._events if e.get("request_id") != dedupe_rid), maxlen=self._max_events
                )
            else:
                rid = str(evt.get("request_id") or "").strip()
                if rid:
                    self._events = deque(
                        (e for e in self._events if e.get("request_id") != rid), maxlen=self._max_events
                    )
            self._events.appendleft(evt)

            targets = [ws for ws, flt in self._ws_subscribers.items() if not flt or flt == device_id]

        # WS fanout
        msg = json.dumps({"type": "pipeline_event", "event": evt})
        for ws in targets:
            self._fanout.submit(ws, msg)

        # 回调通知
        subs = self._subs.get(device_id)
        if subs:
            tasks = [asyncio.create_task(self._safe_call(fn, device_id, evt)) for fn in subs.values()]
            if tasks:
                await asyncio.gather(*tasks)

        return evt

    async def broadcast(self, device_id: str, payload: dict[str, Any]) -> None:
        """纯广播：实时推送给 WS 订阅者，不写入滚动窗口。

        用于 pipeline_stage、camera_frame 等实时推送。
        """
        device_id = str(device_id or "unknown")
        async with self._lock:
            targets = [ws for ws, flt in self._ws_subscribers.items() if not flt or flt == device_id]
        if not targets:
            return
        msg = json.dumps(payload, ensure_ascii=False)
        for ws in targets:
            self._fanout.submit(ws, msg)

    # ── WS 订阅管理 ──────────────────────────────────────────────────

    async def subscribe_ws(self, ws, device_filter: str | None = None) -> None:
        """注册 WS 订阅者，立即推送滚动窗口快照。"""
        async with self._lock:
            self._ws_subscribers[ws] = device_filter
            if device_filter:
                snap = [e for e in self._events if e.get("device_id") == device_filter]
            else:
                snap = list(self._events)
            max_events = self._max_events
        self._fanout.submit(
            ws,
            json.dumps(
                {"type": "pipeline_snapshot", "items": snap, "device_filter": device_filter, "max_events": max_events}
            ),
        )

    async def unsubscribe_ws(self, ws) -> None:
        """取消 WS 订阅，清理 fanout 资源。"""
        async with self._lock:
            self._ws_subscribers.pop(ws, None)
        self._fanout.discard(ws)

    async def has_subscribers(self, device_id: str | None = None) -> bool:
        """是否有 WS 订阅者在监听该设备（或全部设备）。"""
        device_id = str(device_id or "").strip() or None
        async with self._lock:
            for _ws, flt in self._ws_subscribers.items():
                if not flt or flt == device_id:
                    return True
        return False

    # ── 快照查询 ──────────────────────────────────────────────────────

    def snapshot(self, device_id: str | None = None, limit: int = 100) -> list:
        """返回滚动窗口中的最近事件。"""
        if device_id:
            items = [e for e in self._events if e.get("device_id") == device_id]
        else:
            items = list(self._events)
        if limit > 0:
            items = items[:limit]
        return items

    # ── 自动下发事件 ──────────────────────────────────────────────────

    async def publish_auto_dispatch(
        self,
        device_id: str,
        *,
        request_id: str,
        source: str,
        summary: str,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        """无交互自动下发写入流水窗口（idle、人脸跟随等）。"""
        if not device_id:
            return
        evt: dict = {
            "request_id": request_id,
            "source": source,
            "summary": summary,
            "llm_text": summary,
            "status": status,
            "error": error,
        }
        await self.pub(device_id, evt)

    # ── 外部生产者规范化 ──────────────────────────────────────────────

    @staticmethod
    def normalize_event(data: dict, default_device_id: str | None = None) -> dict | None:
        """把任意上报字典规范化为统一的流水线事件结构。"""
        if not isinstance(data, dict):
            return None
        device_id = data.get("device_id") or data.get("id") or default_device_id
        if not device_id:
            return None
        device_id = str(device_id)

        def _fnum(key: str) -> float | None:
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

    @staticmethod
    async def _safe_call(fn: SubscriberFn, device_id: str, data: dict[str, Any]) -> None:
        try:
            await fn(device_id, data)
        except Exception:
            logger.exception("[BusService] 订阅回调异常 device_id=%s", device_id)
