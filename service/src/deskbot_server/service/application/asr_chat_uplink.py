"""``/asr_chat`` 打包帧：``u32be(json_len) + json_utf8 + optional_binary``。

与固件 ``PackedFrame`` / ``parse_packed_frame`` / ``send_packed_bin`` 对称。
服务端仍兼容旧上行：裸 audio / TEXT+BIN。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal, Sequence, Union

logger = logging.getLogger("deskbot-server")

PendingKind = Literal["audio", "camera_frame"]

_MAX_NEXT_BIN_LEN = 512 * 1024
# 与固件 ``DESKBOT_MAX_PACKED_JSON_LEN`` 对齐（TTS anim 音素帧易超 16KB）
_MAX_PACKED_JSON_LEN = 64 * 1024


@dataclass
class PackedFrame:
    """打包帧：已解析 JSON + 同帧 optional binary（media）。"""

    doc: dict[str, Any]
    bin_len: int = 0
    bin: bytes = b""


def pack_ws_downlink_frame(
    text: str, media: Union[bytes, Sequence[bytes], None] = None
) -> bytes:
    """下行单帧：``u32be(json_len) + json_utf8 + concat(media)``。"""
    raw = text.encode("utf-8")
    if not raw or len(raw) > _MAX_PACKED_JSON_LEN:
        raise ValueError(f"packed json_len invalid: {len(raw)}")
    if media is None:
        blob = b""
    elif isinstance(media, (bytes, bytearray, memoryview)):
        blob = bytes(media)
    else:
        blob = b"".join(bytes(b) for b in media if b)
    return len(raw).to_bytes(4, "big", signed=False) + raw + blob


def parse_packed_frame(data: bytes) -> PackedFrame | None:
    """解析打包 BIN 为 PackedFrame；失败返回 None（不打日志，便于走旧路径）。"""
    if data is None or len(data) < 4:
        return None
    json_len = int.from_bytes(data[0:4], "big", signed=False)
    if json_len == 0 or json_len > _MAX_PACKED_JSON_LEN:
        return None
    if 4 + json_len > len(data):
        return None
    if data[4:5] != b"{":
        return None
    try:
        text = data[4 : 4 + json_len].decode("utf-8")
        doc = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    media = data[4 + json_len :]
    return PackedFrame(doc=doc, bin_len=len(media), bin=media)


def coerce_next_bin_len(data: dict[str, Any]) -> int:
    """``next_bin_len`` > 0 表示同帧 media（或旧路径下一条独立 BIN）长度；兼容旧草案 ``next_bin``+``len``。"""
    raw = data.get("next_bin_len")
    if raw is None and data.get("next_bin") in (1, True, "1"):
        raw = data.get("len")
    try:
        n = int(raw or 0)
    except (TypeError, ValueError):
        return 0
    if n <= 0:
        return 0
    if n > _MAX_NEXT_BIN_LEN:
        logger.warning("[/asr_chat] next_bin_len=%d 超过上限 %d，截断", n, _MAX_NEXT_BIN_LEN)
        return _MAX_NEXT_BIN_LEN
    return n


def coerce_opus_frames(data: dict[str, Any]) -> int | None:
    """Opus batch 帧数；缺省或 1 表示单帧 binary。"""
    raw = data.get("frames")
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 1 else None


def coerce_audio_flush(data: dict[str, Any]) -> bool:
    """audio JSON 上的 ``flush:1``：本包喂完后做 Silero flush。"""
    raw = data.get("flush")
    if raw in (1, True, "1"):
        return True
    try:
        return int(raw) == 1
    except (TypeError, ValueError):
        return False


@dataclass
class PendingUplinkBinary:
    kind: PendingKind
    length: int
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    opus_frames: int | None = None
    flush: bool = False
