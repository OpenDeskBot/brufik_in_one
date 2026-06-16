from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

from deskbot_server.util import _format_ts, _json_msg
from deskbot_server.ws.ws_send import _safe_send

if TYPE_CHECKING:
    from deskbot_server.ws.device_pipeline import DevicePipelineBroker


async def _emit_stage(
    websocket,
    dp_broker: Optional["DevicePipelineBroker"],
    device_id: Optional[str],
    request_id: Optional[str],
    stage: str,
    *,
    client_fields: Optional[dict] = None,
    event_fields: Optional[dict] = None,
    send_client: bool = True,
) -> dict:
    """同时向设备 ws 发 ``{"type": <stage>, ...}`` 并把 ``pipeline_stage`` 推给页面订阅者。

    - ``client_fields``：写入下发给设备的 JSON（会强制附带 ``type``/``request_id``）；
    - ``event_fields``：写入 ``pipeline_stage.event`` 的额外字段（例如 ``asr_ms``
      等）。当 ``dp_broker``/``device_id``/``request_id`` 任一为空时跳过广播；
    - ``send_client=False``：只广播给页面不下发到设备。
    返回 ``{"ts", "t_mono"}`` 便于调用方记录时刻。
    """
    now_ts = time.time()
    now_mono = time.monotonic()
    if send_client:
        msg = {"type": stage}
        if request_id:
            msg["request_id"] = request_id
        if client_fields:
            for k, v in client_fields.items():
                msg[k] = v
        await _safe_send(websocket, _json_msg(msg))
    if dp_broker is not None and device_id and request_id:
        event = {
            "device_id": device_id,
            "request_id": request_id,
            "stage": stage,
            "ts": now_ts,
            "t_mono": now_mono,
            "received_at": _format_ts(now_ts),
        }
        if client_fields:
            for k, v in client_fields.items():
                if k not in event:
                    event[k] = v
        if event_fields:
            event.update(event_fields)
        await dp_broker.broadcast_to_device(
            device_id, {"type": "pipeline_stage", "event": event}
        )
        progress = dict(event)
        progress.setdefault("status", "running")
        await dp_broker.publish(progress)
    return {"ts": now_ts, "t_mono": now_mono}
