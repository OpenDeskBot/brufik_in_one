"""WebSocket 连接辅助：链路管理 + peer 格式化 + 安全发送。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, ClassVar, Optional

from starlette.websockets import WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from deskbot_server.constants import SAFE_SEND_TIMEOUT
from deskbot_server.ws.pb_idle_registry import note_pb_idle_after_successful_asr_send

logger = logging.getLogger("deskbot-server")

_WS_OUTBOUND_LOCK_ATTR = "_bot_outbound_send_lock"


class WsUtils:
    """按 ``(device_id, path)`` 管理活跃 WebSocket，并提供 peer / safe_send。"""

    _active: ClassVar[dict[tuple[str, str], Any]] = {}

    @staticmethod
    def _key(device_id: str, path: str) -> tuple[str, str]:
        return (str(device_id), str(path))

    @staticmethod
    async def keep_only_one_link(device_id: Optional[str], path: str, ws: Any) -> None:
        """同一 ``device_id`` + ``path`` 只保留最新一条连接，关闭旧 peer。"""
        if not device_id or ws is None:
            return
        key = WsUtils._key(device_id, path)
        prev = WsUtils._active.get(key)
        if prev is None or prev is ws:
            WsUtils._active[key] = ws
            return
        logger.info("[ws] 关闭旧连接 device_id=%s path=%s (新 peer 接入)", device_id, path)
        try:
            await prev.close(code=1000, reason=f"superseded by new {path}")
        except Exception:
            logger.warning("[ws] 旧连接 close 异常 device_id=%s path=%s", device_id, path, exc_info=True)
        WsUtils._active[key] = ws

    @staticmethod
    def release_link(device_id: Optional[str], path: str, ws: Any) -> bool:
        """连接断开时若仍是当前活跃 peer，则注销；返回是否注销成功。"""
        if not device_id or ws is None:
            return False
        key = WsUtils._key(device_id, path)
        if WsUtils._active.get(key) is ws:
            WsUtils._active.pop(key, None)
            return True
        return False

    @staticmethod
    def is_current_link(device_id: Optional[str], path: str, ws: Any) -> bool:
        if not device_id or ws is None:
            return False
        return WsUtils._active.get(WsUtils._key(device_id, path)) is ws

    @staticmethod
    def peer_str(websocket) -> str:
        """把 websocket 的客户端地址格式化成 host:port；拿不到时返回 ``?``。"""
        try:
            peer = getattr(websocket, "remote_address", None)
            if peer and isinstance(peer, tuple) and len(peer) >= 2:
                return f"{peer[0]}:{peer[1]}"
        except Exception:
            pass
        return "?"

    @staticmethod
    def send_timeout_for_message(message, *, base: float = SAFE_SEND_TIMEOUT) -> float:
        """大 binary 帧适当加长写超时，避免误判为对端挂死。"""
        if isinstance(message, (bytes, bytearray)):
            n = len(message)
            if n > 0:
                return min(60.0, max(base, n / 8000.0 + 2.0))
        return base

    @staticmethod
    def get_ws_send_lock(ws) -> asyncio.Lock:
        lock = getattr(ws, _WS_OUTBOUND_LOCK_ATTR, None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(ws, _WS_OUTBOUND_LOCK_ATTR, lock)
        return lock

    @staticmethod
    async def safe_send_once(websocket, message, *, timeout: Optional[float] = None) -> bool:
        """对 ``websocket`` 执行单次 ``send``（**不**加锁；由调用方保证互斥或独占锁）。

        返回是否成功写出（``True``）；连接已关/超时/其它异常返回 ``False``。
        """
        if timeout is None:
            timeout = WsUtils.send_timeout_for_message(message)
        kind = "bytes" if isinstance(message, (bytes, bytearray)) else "text"
        n = len(message) if isinstance(message, (bytes, bytearray, str)) else 0
        try:
            await asyncio.wait_for(websocket.send(message), timeout=timeout)
            return True
        except (ConnectionClosed, WebSocketDisconnect) as exc:
            code = getattr(exc, "code", None)
            reason = getattr(exc, "reason", None)
            logger.warning(
                "[ws] send 失败 ConnectionClosed peer=%s kind=%s nbytes=%d code=%s reason=%r",
                WsUtils.peer_str(websocket),
                kind,
                n,
                code,
                reason,
            )
            if code == 1009:
                logger.warning(
                    "[ws] 1009 message too big：单帧过大（TEXT 常见为 anim JSON 超 ESP32 上限，binary 约 %d bytes）", n
                )
            return False
        except asyncio.TimeoutError:
            try:
                await websocket.close(code=1011, reason="send timeout")
            except Exception:
                pass
            try:
                peer = WsUtils.peer_str(websocket)
            except Exception:
                peer = "?"
            logger.warning(
                "[ws] safe_send 超时 (>%.1fs)，主动关闭 ws peer=%s msg_kind=%s",
                timeout,
                peer,
                "bytes" if isinstance(message, (bytes, bytearray)) else "text",
            )
            return False
        except Exception as exc:
            logger.warning(
                "[ws] send 失败 %s peer=%s kind=%s nbytes=%d", type(exc).__name__, WsUtils.peer_str(websocket), kind, n
            )
            return False

    @staticmethod
    async def safe_send(websocket, message, *, timeout: Optional[float] = None) -> bool:
        """往 WS 发一条消息；与同连接上其它发送共享互斥锁，保证帧顺序。

        - 客户端已断开：吞掉 ConnectionClosed，避免 ERROR 日志刷屏。
        - **写超时**：超过 ``timeout`` 秒视为对端反压/挂死，主动 ``close()``。
        - 其它异常也被吞掉，默认行为不抛。
        返回是否成功写出。
        """
        if timeout is None:
            timeout = WsUtils.send_timeout_for_message(message)
        ok = False
        async with WsUtils.get_ws_send_lock(websocket):
            ok = await WsUtils.safe_send_once(websocket, message, timeout=timeout)
        if ok:
            note_pb_idle_after_successful_asr_send(websocket)
        return ok
