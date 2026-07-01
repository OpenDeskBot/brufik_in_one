"""ROM 上行：首帧 audio 至 flush 累积，不做服务端 VAD 切分。"""

from __future__ import annotations

import asyncio

from deskbot_server.pipeline.audio import AudioConfig, ConnectionSession


class _StubPipeline:
    pass


def _session() -> ConnectionSession:
    cfg = AudioConfig(
        input_codec="pcm16",
        sample_rate=16000,
        channels=1,
        vad_mode=3,
        frame_ms=30,
        min_speech_ms=250,
        max_silence_ms=500,
        pre_speech_ms=300,
    )
    return ConnectionSession(_StubPipeline(), cfg)


def test_rom_uplink_accumulates_until_flush():
    async def _run() -> None:
        session = _session()
        chunk_a = b"\x01\x00" * 320
        chunk_b = b"\x02\x00" * 320
        _, started_a, _ = await session.feed_audio(
            chunk_a, "pcm16", sample_rate=16000, channels=1
        )
        _, started_b, _ = await session.feed_audio(chunk_b, "pcm16")
        assert started_a is True
        assert started_b is False
        flushed = session.flush()
        assert flushed is not None
        assert flushed.pcm == chunk_a + chunk_b
        assert flushed.sample_rate == 16000
        assert flushed.channels == 1
        assert flushed.codec == "pcm16"
        assert session.flush() is None

    asyncio.run(_run())


def test_feed_audio_does_not_emit_midstream_utterance():
    async def _run() -> None:
        session = _session()
        utterance, started, discarded = await session.feed_audio(b"\x00\x00" * 8000, "pcm16")
        assert utterance is None
        assert started is True
        assert discarded is None

    asyncio.run(_run())


def test_post_flush_discard_window():
    async def _run() -> None:
        session = _session()
        chunk = b"\x03\x00" * 320
        await session.feed_audio(chunk, "pcm16")
        flushed = session.flush()
        assert flushed is not None
        stray, started, _ = await session.feed_audio(b"\x04\x00" * 320, "pcm16")
        assert stray is None
        assert started is False
        assert session.flush() is None

    asyncio.run(_run())
