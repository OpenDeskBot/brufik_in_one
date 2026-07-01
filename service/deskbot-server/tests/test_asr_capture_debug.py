"""ASR 收音调试事件（device_pipeline asr_capture）。"""

from __future__ import annotations

import asyncio
import base64
import io
import wave

from deskbot_server.ws.asr_chat import _publish_asr_capture


class _CaptureBroker:
    def __init__(self) -> None:
        self.messages: list[tuple[str, dict]] = []

    async def broadcast_to_device(self, device_id: str, payload: dict) -> None:
        self.messages.append((device_id, payload))


def test_publish_asr_capture_includes_wav_and_flags():
    async def _run() -> None:
        broker = _CaptureBroker()
        pcm = b"\x00\x01" * 8000  # 0.5s @ 16kHz mono s16le
        await _publish_asr_capture(
            broker,
            "deskbot_test",
            request_id="req1",
            pcm_segment=pcm,
            sample_rate=16000,
            asr_text="你好",
            asr_ms=42.5,
            asr_valid=True,
            channels=1,
            codec="pcm16",
        )
        assert len(broker.messages) == 1
        dev, payload = broker.messages[0]
        assert dev == "deskbot_test"
        assert payload["type"] == "asr_capture"
        evt = payload["event"]
        assert evt["asr_valid"] is True
        assert evt["asr_text"] == "你好"
        assert evt["audio_ms"] == 500
        assert evt["pcm_bytes"] == len(pcm)
        assert evt["sample_rate"] == 16000
        assert evt["channels"] == 1
        assert evt["codec"] == "pcm16"
        assert evt["asr_ms"] == 42.5
        raw = base64.b64decode(evt["wav_base64"])
        with wave.open(io.BytesIO(raw), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 16000
            assert len(wf.readframes(wf.getnframes())) == len(pcm)

    asyncio.run(_run())
