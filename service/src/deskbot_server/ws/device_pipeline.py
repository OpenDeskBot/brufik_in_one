"""/device_pipeline WS 入口：外部生产者 + web 订阅者。"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from websockets.exceptions import ConnectionClosed

from deskbot_server.utils.util import (
    _extract_device_id,
    _json_msg,
    _parse_query,
    _split_path,
    _ws_request_path,
)
from deskbot_server.utils.ws_utils import WsUtils

if TYPE_CHECKING:
    from deskbot_server.service.bus_service import BusService
    from deskbot_server.service.device_ws_service import DeviceWsService

logger = logging.getLogger("deskbot-server")


async def handle_device_pipeline(websocket, bus: BusService, registry: DeviceWsService) -> None:
    """/device_pipeline WS 入口。

    协议：
      - 生产者连接 URL 形如 ``ws://host:9000/device_pipeline?device_id=xxx``，device_id 必填。
      - 订阅者连接 URL 形如 ``ws://host:9000/device_pipeline?role=subscriber&device_id=xxx``，
        device_id 可选，作为过滤条件；不传则收到全部设备事件。
    """
    from deskbot_server.service.bus_service import BusService as _BS

    req_path = _ws_request_path(websocket)
    _, query = _split_path(req_path)
    qargs = _parse_query(query)
    role = (qargs.get("role") or "").lower() or None
    url_device = _extract_device_id(qargs)
    is_subscriber = role in ("subscriber", "sub", "viewer", "consumer")
    peer = WsUtils.peer_str(websocket)

    await WsUtils.safe_send(
        websocket,
        _json_msg(
            {
                "type": "ready",
                "channel": "device_pipeline",
                "max_events": bus.max_events,
                "device_id": None if is_subscriber else url_device,
                "device_filter": url_device if is_subscriber else None,
            }
        ),
    )

    try:
        if is_subscriber:
            logger.info("[/device_pipeline] 订阅者接入 peer=%s device_filter=%s", peer, url_device)
            await bus.subscribe_ws(websocket, url_device)
            try:
                async for msg in websocket:
                    if isinstance(msg, (bytes, bytearray)):
                        continue
                    try:
                        d = json.loads(msg)
                    except Exception:
                        continue
                    if d.get("type") == "ping":
                        await WsUtils.safe_send(websocket, _json_msg({"type": "pong"}))
            finally:
                await bus.unsubscribe_ws(websocket)
            return

        if not url_device:
            logger.warning(
                "[/device_pipeline] 拒绝生产者：缺失 device_id peer=%s path=%s —— "
                "需用 ws://host:9000/device_pipeline?device_id=<设备ID>",
                peer,
                req_path,
            )
            await WsUtils.safe_send(
                websocket, _json_msg({"type": "error", "message": "producer 必须在 URL 中携带 device_id"})
            )
            await websocket.close(code=1008, reason="device_id required")
            return

        logger.info("[/device_pipeline] 生产者接入 device_id=%s peer=%s", url_device, peer)
        await registry.register(url_device, websocket)
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
                    await WsUtils.safe_send(websocket, _json_msg({"type": "pong"}))
                    continue

                evt = _BS.normalize_event(data, default_device_id=url_device)
                if not evt:
                    await WsUtils.safe_send(
                        websocket, _json_msg({"type": "pipeline_rejected", "reason": "invalid_payload"})
                    )
                    continue
                evt["device_id"] = url_device

                stored = await bus.pub(url_device, evt)
                await registry.touch(url_device, evt.get("status"))
                await WsUtils.safe_send(websocket, _json_msg({"type": "pipeline_ack", "seq": stored["seq"]}))
        finally:
            await registry.unregister(url_device, websocket)
    except ConnectionClosed as closed:
        logger.info("/device_pipeline WS 已关闭: %s", closed)
