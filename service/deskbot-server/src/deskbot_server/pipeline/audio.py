from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import opuslib_next

if TYPE_CHECKING:
    from deskbot_server.application.chat_service import ChatService

logger = logging.getLogger("deskbot-server")

# flush 后丢弃上行 PCM 的窗口（与 ROM ``kNoPbOpenMicMs`` 对齐，防止 post-flush 拼进下一段）
ROM_POST_FLUSH_DISCARD_SEC = 3.0
ROM_FLUSH_WARN_DURATION_MS = 11_500


@dataclass
class AudioConfig:
    input_codec: str
    sample_rate: int
    channels: int
    vad_mode: int
    frame_ms: int
    min_speech_ms: int
    max_silence_ms: int
    pre_speech_ms: int


@dataclass(frozen=True)
class RomUplinkFlush:
    """ROM ``audio`` 首帧至 ``flush`` 之间累积的 PCM。"""

    pcm: bytes
    sample_rate: int
    channels: int
    codec: str


class ConnectionSession:
    """``/asr_chat`` 音频上行：按 ROM 协议累积 PCM，不在服务端做 VAD 切分。"""

    def __init__(self, pipeline: ChatService, audio_cfg: AudioConfig):
        self.pipeline = pipeline
        self.audio_cfg = audio_cfg

        self.decoder = None
        if audio_cfg.input_codec == "opus":
            self.decoder = opuslib_next.Decoder(audio_cfg.sample_rate, audio_cfg.channels)

        self.rom_pcm = bytearray()
        self.rom_open = False
        self.rom_sr = audio_cfg.sample_rate
        self.rom_ch = audio_cfg.channels
        self.rom_codec = audio_cfg.input_codec
        self.rom_discard_until = 0.0
        self.lock = asyncio.Lock()

    def _decode(self, payload: bytes, codec: Optional[str] = None) -> bytes:
        use_codec = (codec or self.rom_codec or self.audio_cfg.input_codec).lower()
        if use_codec == "pcm16":
            return payload
        if use_codec == "opus":
            if self.decoder is None:
                self.decoder = opuslib_next.Decoder(self.rom_sr, self.rom_ch)
            return self.decoder.decode(payload, self.rom_sr // 100)
        raise ValueError(f"unsupported codec: {use_codec}")

    async def feed_audio(
        self,
        payload: bytes,
        codec: Optional[str] = None,
        *,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None,
    ) -> tuple[Optional[bytes], bool, Optional[bytes]]:
        """追加 ROM 上行 PCM。返回 ``(None, uplink_started, None)``。"""
        async with self.lock:
            now = time.monotonic()
            if now < self.rom_discard_until:
                return None, False, None

            try:
                pcm = self._decode(payload, codec)
            except Exception as exc:
                logger.debug(
                    "audio decode skip codec=%s len=%d: %s",
                    codec or self.rom_codec,
                    len(payload),
                    exc,
                )
                return None, False, None

            if sample_rate is not None and sample_rate > 0:
                self.rom_sr = int(sample_rate)
            if channels is not None and channels > 0:
                self.rom_ch = int(channels)
            if codec:
                self.rom_codec = codec.lower()

            uplink_started = False
            if pcm:
                if not self.rom_open:
                    self.rom_open = True
                    uplink_started = True
                    logger.info(
                        "[ROM uplink] 首帧 PCM device sr=%d ch=%d codec=%s chunk=%d",
                        self.rom_sr,
                        self.rom_ch,
                        self.rom_codec,
                        len(pcm),
                    )
                self.rom_pcm.extend(pcm)

            return None, uplink_started, None

    def flush(self) -> Optional[RomUplinkFlush]:
        if not self.rom_pcm:
            self._reset_rom()
            return None
        pcm = bytes(self.rom_pcm)
        out = RomUplinkFlush(
            pcm=pcm,
            sample_rate=self.rom_sr,
            channels=self.rom_ch,
            codec=self.rom_codec,
        )
        duration_ms = int(len(pcm) / 2 / max(1, self.rom_sr) * 1000)
        if duration_ms > ROM_FLUSH_WARN_DURATION_MS:
            logger.warning(
                "[ROM uplink] flush duration_ms=%d exceeds ~10s cap — "
                "check device post-flush uplink or missed flush boundary",
                duration_ms,
            )
        logger.info(
            "[ROM uplink] flush pcm_bytes=%d sr=%d ch=%d codec=%s duration_ms=%d",
            len(pcm),
            self.rom_sr,
            self.rom_ch,
            self.rom_codec,
            duration_ms,
        )
        self._reset_rom()
        self.rom_discard_until = time.monotonic() + ROM_POST_FLUSH_DISCARD_SEC
        return out

    def _reset_rom(self) -> None:
        self.rom_pcm.clear()
        self.rom_open = False
        self.rom_sr = self.audio_cfg.sample_rate
        self.rom_ch = self.audio_cfg.channels
        self.rom_codec = self.audio_cfg.input_codec

    def cancel_rom_uplink(self) -> None:
        """丢弃已上行但未 flush 的 PCM（设备端 TTS 打断录音时）。"""
        had = len(self.rom_pcm)
        self._reset_rom()
        if had:
            logger.info("[ROM uplink] audio_cancel discarded %d bytes", had)
