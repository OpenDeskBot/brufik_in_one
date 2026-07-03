"""上行 Opus 解码：单帧或 length-prefixed 多帧 batch。"""

from __future__ import annotations

import struct
from typing import Optional

import opuslib_next

_OPUS_LP_HDR = struct.Struct("!H")


def opus_frame_samples(sample_rate: int) -> int:
    """20 ms @ sample_rate（与固件/编码器一致）。"""
    return max(int(sample_rate) // 50, 120)


def decode_opus_uplink(
    decoder: opuslib_next.Decoder,
    payload: bytes,
    *,
    sample_rate: int,
    opus_frames: Optional[int] = None,
) -> bytes:
    """解码上行 Opus binary。

    - ``opus_frames`` 缺省或 1：整包为单帧（兼容旧客户端）。
    - ``opus_frames`` > 1：``uint16_be len + opus`` 重复 ``opus_frames`` 次。
    """
    if not payload:
        return b""
    frame_samples = opus_frame_samples(sample_rate)
    if opus_frames is None or opus_frames <= 1:
        return decoder.decode(payload, frame_samples)

    pcm_parts: list[bytes] = []
    offset = 0
    for i in range(opus_frames):
        if offset + _OPUS_LP_HDR.size > len(payload):
            raise ValueError(f"opus batch frame {i}: missing length header")
        (frame_len,) = _OPUS_LP_HDR.unpack_from(payload, offset)
        offset += _OPUS_LP_HDR.size
        if frame_len <= 0 or offset + frame_len > len(payload):
            raise ValueError(
                f"opus batch frame {i}: invalid length {frame_len} "
                f"(remaining={len(payload) - offset})"
            )
        pcm_parts.append(decoder.decode(payload[offset : offset + frame_len], frame_samples))
        offset += frame_len
    if offset != len(payload):
        raise ValueError(f"opus batch trailing bytes: {len(payload) - offset}")
    return b"".join(pcm_parts)
