"""ROM 上行 + 服务端 Silero VAD 切句。"""

from __future__ import annotations

import asyncio
import struct

from pathlib import Path

import numpy as np
import opuslib_next

from deskbot_server.pipeline.audio import AudioConfig, ConnectionSession
from deskbot_server.pipeline.opus_uplink import decode_opus_uplink
from deskbot_server.pipeline.silero_vad import SileroVadConfig, SileroVadStream


class _StubPipeline:
    pass


def _session() -> ConnectionSession:
    cfg = AudioConfig(
        input_codec="opus",
        sample_rate=16000,
        channels=1,
        min_speech_ms=250,
        max_silence_ms=500,
        pre_speech_ms=300,
    )
    session = ConnectionSession(_StubPipeline(), cfg)
    model_path = str(
        Path(__file__).resolve().parents[1] / "models" / "silero_vad" / "silero_vad.onnx"
    )
    session._vad = SileroVadStream(
        SileroVadConfig(
            model_path=model_path,
            threshold=cfg.silero_threshold,
            threshold_low=cfg.silero_threshold_low,
            min_silence_ms=cfg.max_silence_ms,
            min_speech_ms=cfg.min_speech_ms,
            pre_speech_ms=cfg.pre_speech_ms,
            frame_window_threshold=1,
        ),
        sample_rate=cfg.sample_rate,
    )
    return session


def _pcm_tone(duration_ms: int = 900, freq: float = 200.0, amp: int = 20000) -> bytes:
    sr = 16000
    n = sr * duration_ms // 1000
    t = np.arange(n, dtype=np.float32)
    wave = (np.sin(2 * np.pi * freq * t / sr) * amp).astype(np.int16)
    return wave.tobytes()


def _encode_opus_batch(pcm_chunks: list[bytes]) -> tuple[bytes, int]:
    enc = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP)
    parts: list[bytes] = []
    for pcm in pcm_chunks:
        opus = enc.encode(pcm, 320)
        parts.append(struct.pack("!H", len(opus)) + opus)
    return b"".join(parts), len(pcm_chunks)


def test_rom_uplink_emits_utterance_on_speech():
    async def _run() -> None:
        session = _session()
        speech = _pcm_tone(900)
        silence = b"\x00\x00" * (16000 // 2)  # 500 ms
        pcm = speech + silence
        utterance = None
        for i in range(0, len(pcm), 640):
            utt, _, _ = await session.feed_audio(pcm[i : i + 640], "pcm16")
            if utt:
                utterance = utt
        assert utterance is not None
        assert len(utterance) >= 640

    asyncio.run(_run())


def test_rom_uplink_opus_decode_roundtrip():
    async def _run() -> None:
        session = _session()
        pcm = _pcm_tone(200) + (b"\x00\x00" * 640)
        opus = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP).encode(pcm[:640], 320)
        utt, started, _ = await session.feed_audio(opus, "opus")
        assert utt is None
        assert started is True

    asyncio.run(_run())


def test_rom_uplink_opus_batch_decode():
    async def _run() -> None:
        session = _session()
        chunks = [b"\x00\x00" * 640 for _ in range(5)]
        payload, n = _encode_opus_batch(chunks)
        _, started, _ = await session.feed_audio(payload, "opus", opus_frames=n)
        assert started is True

    asyncio.run(_run())


def test_opus_uplink_batch_roundtrip():
    enc = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP)
    dec = opuslib_next.Decoder(16000, 1)
    pcm_in = b"\x00\x00" * 640
    parts = []
    for _ in range(2):
        opus = enc.encode(pcm_in, 320)
        parts.append(struct.pack("!H", len(opus)) + opus)
    payload = b"".join(parts)
    pcm_out = decode_opus_uplink(dec, payload, sample_rate=16000, opus_frames=2)
    assert len(pcm_out) == 1280


def test_rom_uplink_flush_discards_silence():
    async def _run() -> None:
        session = _session()
        enc = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_VOIP)
        silence = b"\x00\x00" * 3200
        for i in range(0, len(silence), 640):
            opus = enc.encode(silence[i : i + 640], 320)
            await session.feed_audio(opus, "opus")
        assert session.flush() is None

    asyncio.run(_run())


def test_post_flush_accepts_new_audio():
    async def _run() -> None:
        session = _session()
        pcm = _pcm_tone(900) + (b"\x00\x00" * (16000 // 2))
        utterance = None
        for i in range(0, len(pcm), 640):
            utt, _, _ = await session.feed_audio(pcm[i : i + 640], "pcm16")
            if utt:
                utterance = utt
        assert utterance is not None
        assert session.flush() is None
        _, started, _ = await session.feed_audio(b"\x00" * 640, "pcm16")
        assert started is True

    asyncio.run(_run())
