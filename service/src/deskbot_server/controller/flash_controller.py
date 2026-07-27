"""ROM 烧录串口 WebSocket（需登录会话）。"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from deskbot_server.infrastructure.flash.rom_flash import flash_manager, validate_port
from deskbot_server.service.user_service import UserService

logger = logging.getLogger("deskbot-server")

router = APIRouter(tags=["flash-ws"])


def _session_user_id(websocket: WebSocket) -> str | None:
    session = websocket.scope.get("session") or {}
    uid = str(session.get("user_id") or "").strip()
    if not uid:
        return None
    user = UserService().get_user(uid)
    if user is None or not getattr(user, "is_active", True):
        return None
    return uid


@router.websocket("/api/flash/serial/ws")
async def flash_serial_ws(websocket: WebSocket) -> None:
    if _session_user_id(websocket) is None:
        await websocket.close(code=4401)
        return

    port = (websocket.query_params.get("port") or "").strip()
    try:
        port = validate_port(port)
    except ValueError:
        await websocket.close(code=4400)
        return

    await websocket.accept()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    def _on_serial(data: bytes) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, data)

    try:
        flash_manager.free_serial_port(port)
        flash_manager.serial.stop()
        flash_manager.serial.start(port)
        flash_manager.serial.add_listener(_on_serial)
        await websocket.send_json({"ok": True, "event": "connected", "port": port})

        async def _pump_serial() -> None:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                await websocket.send_bytes(chunk)

        pump_task = asyncio.create_task(_pump_serial())
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("type") != "websocket.receive":
                    continue
                if "bytes" in msg and msg["bytes"]:
                    text = msg["bytes"].decode("utf-8", errors="replace")
                    await asyncio.to_thread(flash_manager.serial.write, text)
                elif "text" in msg and msg["text"]:
                    await asyncio.to_thread(flash_manager.serial.write, msg["text"])
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("flash serial ws failed")
        try:
            await websocket.send_json({"ok": False, "error": "serial_ws_failed"})
        except Exception:
            pass
    finally:
        flash_manager.serial.remove_listener(_on_serial)
        flash_manager.serial.stop()
