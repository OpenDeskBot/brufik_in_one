"""下行 pb TTS：PCM s16le → Opus batch（与上行相同的 uint16_be + frame 格式）。"""

from __future__ import annotations

import struct
from typing import Optional

import opuslib_next

from deskbot_server.pipeline.opus_uplink import opus_frame_samples

_OPUS_LP_HDR = struct.Struct("!H")


def encode_pcm_s16le_to_opus_batch(pcm: bytes, sample_rate: int) -> tuple[bytes, int]:
    """mono s16le PCM → ``(opus_batch, frame_count)``。"""
    if not pcm:
        return b"", 0
    frame_samples = opus_frame_samples(sample_rate)
    frame_bytes = frame_samples * 2
    enc = opuslib_next.Encoder(sample_rate, 1, opuslib_next.APPLICATION_AUDIO)
    parts: list[bytes] = []
    nframes = 0
    offset = 0
    while offset < len(pcm):
        chunk = pcm[offset : offset + frame_bytes]
        if len(chunk) < frame_bytes:
            chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
        opus = enc.encode(chunk, frame_samples)
        parts.append(_OPUS_LP_HDR.pack(len(opus)) + opus)
        nframes += 1
        offset += frame_bytes
    return b"".join(parts), nframes


def decode_opus_batch_to_pcm_s16le(
    decoder: opuslib_next.Decoder,
    payload: bytes,
    *,
    sample_rate: int,
    opus_frames: Optional[int] = None,
) -> bytes:
    """Opus batch → mono s16le PCM（供单测与调试）。"""
    if not payload:
        return b""
    frame_samples = opus_frame_samples(sample_rate)
    if opus_frames is None or opus_frames <= 1:
        return decoder.decode(payload, frame_samples)
    pcm_parts: list[bytes] = []
    offset = 0
    for i in range(opus_frames):
        if offset + _OPUS_LP_HDR.size > len(payload):
            raise ValueError(f"opus downlink frame {i}: missing length header")
        (frame_len,) = _OPUS_LP_HDR.unpack_from(payload, offset)
        offset += _OPUS_LP_HDR.size
        if frame_len <= 0 or offset + frame_len > len(payload):
            raise ValueError(f"opus downlink frame {i}: invalid length {frame_len}")
        pcm_parts.append(decoder.decode(payload[offset : offset + frame_len], frame_samples))
        offset += frame_len
    if offset != len(payload):
        raise ValueError(f"opus downlink trailing bytes: {len(payload) - offset}")
    return b"".join(pcm_parts)
