from __future__ import annotations

import datetime as _dt
import io
import json
import os
import tempfile
import time
import traceback
import uuid
import wave
from typing import Any, Optional
from urllib.parse import unquote_plus


def format_exc_detail(exc: Exception) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def pcm_to_wav_bytes(pcm_bytes: bytes, sample_rate: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)
    return buf.getvalue()


def save_temp_wav(pcm_bytes: bytes, sample_rate: int) -> str:
    wav_bytes = pcm_to_wav_bytes(pcm_bytes, sample_rate)
    fd, path = tempfile.mkstemp(prefix="bot_", suffix=".wav")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(wav_bytes)
    return path


def _ms_between(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round((b - a) * 1000.0, 1)


def _format_ts(ts: float) -> str:
    try:
        return (
            _dt.datetime.fromtimestamp(ts)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S %z")
        )
    except Exception:
        return ""


def _normalize_incoming_pb_ack(data: dict[str, Any]) -> Optional[dict[str, Any]]:
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
        out["audio_buf_ms"] = int(data["audio_buf_ms"])
    except Exception:
        out["audio_buf_ms"] = 0
    sv = data.get("servo")
    if isinstance(sv, dict):
        servo_out: dict[str, int] = {}
        for k in ("x", "y", "x_min", "x_max", "y_min", "y_max"):
            if k not in sv:
                continue
            try:
                servo_out[k] = int(sv[k])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        if servo_out:
            out["servo"] = servo_out
    return out


def _ws_request_path(websocket) -> str:
    req_path = getattr(websocket, "path", None)
    if req_path is None:
        req_path = getattr(getattr(websocket, "request", None), "path", None)
    return req_path or ""


def _split_path(raw_path: str) -> tuple:
    """把 `/face_pos?role=subscriber` 拆成 (path, query)。"""
    if not raw_path:
        return "", ""
    if "?" in raw_path:
        path, _, query = raw_path.partition("?")
        return path, query
    return raw_path, ""


def _parse_query(query: str) -> dict:
    out: dict = {}
    if not query:
        return out
    for part in query.split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
        else:
            k, v = part, ""
        out[k.strip().lower()] = unquote_plus(v.strip(), encoding="utf-8", errors="replace")
    return out


def _extract_device_id(qargs: dict) -> Optional[str]:
    """从 URL 查询参数里按兼容顺序取 device_id。

    支持的别名：``device_id`` / ``deviceid`` / ``device`` / ``id``，均大小写不敏感。
    返回已 strip 的字符串，若为空返回 None。
    """
    for key in ("device_id", "deviceid", "device", "id"):
        v = qargs.get(key)
        if v:
            v = str(v).strip()
            if v:
                return v
    return None


def _new_request_id() -> str:
    """生成 /asr_chat 每一轮的 request_id（短 uuid），用于跨阶段追踪。"""
    return uuid.uuid4().hex[:16]


def _peer_str(websocket) -> str:
    """把 websocket 的客户端地址格式化成 host:port；拿不到时返回 ``?``。"""
    try:
        peer = getattr(websocket, "remote_address", None)
        if peer and isinstance(peer, tuple) and len(peer) >= 2:
            return f"{peer[0]}:{peer[1]}"
    except Exception:
        pass
    return "?"


def _json_msg(payload: dict) -> str:
    """为调试页面补充统一时间戳（秒，单调时钟），用于精确统计各阶段耗时。"""
    p = dict(payload)
    p.setdefault("t_mono", time.monotonic())
    return json.dumps(p)
