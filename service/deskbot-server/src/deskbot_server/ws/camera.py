"""WebSocket：``/camera_view`` 调试预览订阅。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from websockets.exceptions import ConnectionClosed

from deskbot_server.application.camera_broker import CameraImageBroker
from deskbot_server.util import (
    _extract_device_id,
    _json_msg,
    _parse_query,
    _peer_str,
    _split_path,
    _ws_request_path,
)
from deskbot_server.ws.ws_send import _safe_send

logger = logging.getLogger("deskbot-server")

__all__ = ["CameraImageBroker", "handle_camera_view"]


async def handle_camera_view(
    websocket,
    image_broker: CameraImageBroker,
) -> None:
    req_path = _ws_request_path(websocket)
    _, query = _split_path(req_path)
    qargs = _parse_query(query)
    url_device = _extract_device_id(qargs)
    peer = _peer_str(websocket)
    logger.info("[/camera_view] 订阅者接入 peer=%s device_filter=%s", peer, url_device)

    await _safe_send(
        websocket,
        _json_msg(
            {
                "type": "ready",
                "channel": "camera_view",
                "device_filter": url_device,
                "expects": "binary JPEG frames preceded by camera_frame meta",
            }
        ),
    )

    await image_broker.add_subscriber(websocket, url_device)
    try:
        async for msg in websocket:
            if isinstance(msg, (bytes, bytearray)):
                continue
            try:
                d = json.loads(msg)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("type") == "ping":
                await _safe_send(websocket, _json_msg({"type": "pong"}))
    except ConnectionClosed as closed:
        logger.info(
            "/camera_view WebSocket 已关闭 peer=%s device_filter=%s: %s",
            peer,
            url_device,
            closed,
        )
    finally:
        await image_broker.remove_subscriber(websocket)
