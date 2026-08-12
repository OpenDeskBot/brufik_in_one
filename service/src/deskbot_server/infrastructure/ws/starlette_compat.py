"""把 Starlette/FastAPI WebSocket 适配成现有 ``websockets`` 风格 API。

既有 ``asr_chat`` / ``camera_*`` / ``device_pipeline`` 依赖：
``send`` / ``recv`` / ``async for`` / ``close`` / ``path`` / ``remote_address`` /
``request.headers``，以及 ``websockets.exceptions.ConnectionClosed``。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from websockets.exceptions import ConnectionClosedError


class StarletteWsCompat:
    """对 FastAPI ``WebSocket`` 的薄包装，尽量保持旧 handler 可复用。"""

    def __init__(self, websocket: WebSocket):
        self._ws = websocket
        q = websocket.url.query
        path = websocket.url.path or ""
        self.path = f"{path}?{q}" if q else path
        client = websocket.client
        self.remote_address: tuple[str, int] | None = (client.host, client.port) if client is not None else None
        self.request = SimpleNamespace(path=self.path, headers=websocket.headers)

    @property
    def raw(self) -> WebSocket:
        return self._ws

    async def accept(self) -> None:
        if self._ws.client_state == WebSocketState.CONNECTING:
            await self._ws.accept()

    async def send(self, message: Any) -> None:
        if isinstance(message, (bytes, bytearray, memoryview)):
            await self._ws.send_bytes(bytes(message))
            return
        await self._ws.send_text(message if isinstance(message, str) else str(message))

    async def recv(self) -> str | bytes:
        try:
            message = await self._ws.receive()
        except WebSocketDisconnect as exc:
            raise ConnectionClosedError(None, None) from exc
        mtype = message.get("type")
        if mtype == "websocket.disconnect":
            raise ConnectionClosedError(None, None)
        if message.get("text") is not None:
            return message["text"]
        if message.get("bytes") is not None:
            return message["bytes"]
        raise ConnectionClosedError(None, None)

    def __aiter__(self) -> "StarletteWsCompat":
        return self

    async def __anext__(self) -> str | bytes:
        try:
            return await self.recv()
        except (ConnectionClosedError, WebSocketDisconnect):
            raise StopAsyncIteration from None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        try:
            if self._ws.client_state != WebSocketState.DISCONNECTED:
                await self._ws.close(code=code, reason=reason or "")
        except Exception:
            pass
