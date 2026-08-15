"""杂项工具：异常格式化、时间戳、PB ACK 校验、请求 ID 生成。"""

from __future__ import annotations

import datetime as _dt
import json
import time
import traceback
import uuid
from typing import Any

# 向后兼容重导出：已迁移到 utils/audio.py
from deskbot_server.utils.audio import pcm_to_wav_bytes, save_temp_wav  # noqa: F401

# 向后兼容重导出：已迁移到 utils/ws_parse.py，保留旧名（下划线前缀）
from deskbot_server.utils.ws_parse import extract_device_id as _extract_device_id  # noqa: F401
from deskbot_server.utils.ws_parse import parse_query as _parse_query
from deskbot_server.utils.ws_parse import split_path as _split_path
from deskbot_server.utils.ws_parse import ws_request_path as _ws_request_path

__all__ = [
    "_extract_device_id",
    "_format_ts",
    "_json_msg",
    "_ms_between",
    "_new_request_id",
    "_normalize_incoming_pb_ack",
    "_parse_query",
    "_split_path",
    "_ws_request_path",
    "format_exc_detail",
    "pcm_to_wav_bytes",
    "save_temp_wav",
]


def format_exc_detail(exc: Exception) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def _ms_between(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return round((b - a) * 1000.0, 1)


def _format_ts(ts: float) -> str:
    try:
        return _dt.datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return ""


def _normalize_incoming_pb_ack(data: dict[str, Any]) -> dict[str, Any] | None:
    """校验 ESP32 上行的 ``pb_ack``，供入库与注入 LLM。"""
    if not isinstance(data, dict) or data.get("type") != "pb_ack":
        return None
    out: dict[str, Any] = {"type": "pb_ack"}
    r = data.get("req")
    out["req"] = r if isinstance(r, str) else ""
    try:
        out["idx"] = int(data["idx"])
    except Exception:
        out["idx"] = 0
    try:
        out["space"] = int(data.get("space", 0))
    except Exception:
        out["space"] = 0
    ack_type = data.get("ack_type", "")
    out["ack_type"] = ack_type if isinstance(ack_type, str) and ack_type in ("pb_chunk", "pb_end") else ""
    return out


def _new_request_id() -> str:
    """生成 /asr_chat 每一轮的 request_id（短 uuid），用于跨阶段追踪。"""
    return uuid.uuid4().hex[:16]


def _json_msg(payload: dict) -> str:
    """为调试页面补充统一时间戳（秒，单调时钟），用于精确统计各阶段耗时。"""
    p = dict(payload)
    p.setdefault("t_mono", time.monotonic())
    return json.dumps(p)
