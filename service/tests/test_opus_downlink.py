"""下行 pb TTS Opus batch 编解码 roundtrip。"""

from __future__ import annotations

import struct

import numpy as np
import opuslib_next

from deskbot_server.pipeline.opus_downlink import (
    decode_opus_batch_to_pcm_s16le,
    encode_pcm_s16le_to_opus_batch,
)
from deskbot_server.pipeline.opus_uplink import opus_frame_samples


def _tone_pcm(sample_rate: int, duration_ms: int = 200, freq: float = 440.0) -> bytes:
    n = sample_rate * duration_ms // 1000
    t = np.arange(n, dtype=np.float32)
    wave = (np.sin(2 * np.pi * freq * t / sample_rate) * 12000).astype(np.int16)
    return wave.tobytes()


def test_opus_downlink_roundtrip_24k():
    sr = 24000
    pcm = _tone_pcm(sr, duration_ms=400)
    batch, nframes = encode_pcm_s16le_to_opus_batch(pcm, sr)
    assert nframes > 0
    assert len(batch) > 0

    dec = opuslib_next.Decoder(sr, 1)
    out = decode_opus_batch_to_pcm_s16le(dec, batch, sample_rate=sr, opus_frames=nframes)
    frame_samples = opus_frame_samples(sr)
    assert len(out) >= frame_samples * (nframes - 1)


def test_opus_downlink_single_frame():
    sr = 24000
    pcm = _tone_pcm(sr, duration_ms=20)
    enc = opuslib_next.Encoder(sr, 1, opuslib_next.APPLICATION_AUDIO)
    frame_samples = opus_frame_samples(sr)
    opus = enc.encode(pcm[: frame_samples * 2], frame_samples)

    dec = opuslib_next.Decoder(sr, 1)
    out = decode_opus_batch_to_pcm_s16le(dec, opus, sample_rate=sr, opus_frames=1)
    assert len(out) == frame_samples * 2
