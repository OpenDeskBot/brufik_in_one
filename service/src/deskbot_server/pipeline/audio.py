from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import opuslib_next

from deskbot_server.pipeline.opus_uplink import decode_opus_uplink
from deskbot_server.pipeline.silero_vad import SileroVadConfig, SileroVadStream

if TYPE_CHECKING:
    from deskbot_server.application.chat_service import ChatService

logger = logging.getLogger("deskbot-server")


@dataclass
class AudioConfig:
    input_codec: str
    sample_rate: int
    channels: int
    min_speech_ms: int
    max_silence_ms: int
    pre_speech_ms: int
    silero_model_path: str = ""
    silero_threshold: float = 0.5
    silero_threshold_low: float = 0.2


@dataclass(frozen=True)
class RomUplinkFlush:
    """flush 时 Silero 收尾得到的 PCM 句段（可能为空）。"""

    pcm: bytes
    sample_rate: int
    channels: int
    codec: str


class ConnectionSession:
    """``/asr_chat`` 音频上行：Opus/PCM → PCM → Silero VAD 切句。"""

    def __init__(self, pipeline: ChatService, audio_cfg: AudioConfig):
        self.pipeline = pipeline
        self.audio_cfg = audio_cfg

        self.decoder = None
        if audio_cfg.input_codec == "opus":
            self.decoder = opuslib_next.Decoder(audio_cfg.sample_rate, audio_cfg.channels)

        self.rom_sr = audio_cfg.sample_rate
        self.rom_ch = audio_cfg.channels
        self.rom_codec = audio_cfg.input_codec
        self.lock = asyncio.Lock()
        self._uplink_open = False

        model_path = audio_cfg.silero_model_path
        if not model_path:
            model_path = str(
                Path(__file__).resolve().parents[3] / "models" / "silero_vad" / "silero_vad.onnx"
            )
        elif not Path(model_path).is_absolute():
            model_path = str(Path(__file__).resolve().parents[3] / model_path)
        self._silero_cfg = SileroVadConfig(
            model_path=model_path,
            threshold=audio_cfg.silero_threshold,
            threshold_low=audio_cfg.silero_threshold_low,
            min_silence_ms=audio_cfg.max_silence_ms,
            min_speech_ms=audio_cfg.min_speech_ms,
            pre_speech_ms=audio_cfg.pre_speech_ms,
        )
        self._vad = SileroVadStream(self._silero_cfg, sample_rate=audio_cfg.sample_rate)

    def _decode(self, payload: bytes, codec: Optional[str] = None, *, opus_frames: Optional[int] = None) -> bytes:
        use_codec = (codec or self.rom_codec or self.audio_cfg.input_codec).lower()
        if use_codec == "pcm16":
            return payload
        if use_codec == "opus":
            if self.decoder is None:
                self.decoder = opuslib_next.Decoder(self.rom_sr, self.rom_ch)
            return decode_opus_uplink(
                self.decoder,
                payload,
                sample_rate=self.rom_sr,
                opus_frames=opus_frames,
            )
        raise ValueError(f"unsupported codec: {use_codec}")

    async def feed_audio(
        self,
        payload: bytes,
        codec: Optional[str] = None,
        *,
        sample_rate: Optional[int] = None,
        channels: Optional[int] = None,
        opus_frames: Optional[int] = None,
    ) -> tuple[Optional[bytes], bool, Optional[bytes]]:
        """返回 ``(utterance_pcm_or_none, uplink_started, None)``。

        Opus 解码与 Silero VAD 推理均为 CPU 密集型同步操作，通过
        ``run_in_executor`` 移到线程池执行，避免阻塞 asyncio 事件循环。
        事件循环阻塞会导致 TCP 接收缓冲区积压，触发 zero-window 流控，
        进而使 ESP32 端 TCP 发送缓冲区满，sendBIN 超时失败。
        Lock 保证解码器与 VAD 的状态串行访问，与 run_in_executor 不冲突。
        """
        async with self.lock:
            loop = asyncio.get_running_loop()

            # --- Opus 解码（CPU 密集，移到线程池）---
            _decode_fn = lambda: self._decode(payload, codec, opus_frames=opus_frames)  # noqa: E731
            try:
                pcm = await loop.run_in_executor(None, _decode_fn)
            except Exception as exc:
                logger.warning(
                    "[ROM uplink] audio decode skip codec=%s len=%d sr=%d frames=%s: %s",
                    codec or self.rom_codec,
                    len(payload),
                    self.rom_sr,
                    opus_frames,
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
            if pcm and not self._uplink_open:
                self._uplink_open = True
                uplink_started = True
                logger.info(
                    "[ROM uplink] 首帧 PCM sr=%d ch=%d codec=%s chunk=%d opus_frames=%s",
                    self.rom_sr,
                    self.rom_ch,
                    self.rom_codec,
                    len(pcm),
                    opus_frames,
                )

            # --- Silero VAD 推理（CPU 密集，移到线程池）---
            if pcm:
                utterance = await loop.run_in_executor(None, self._vad.feed_pcm, pcm)
            else:
                utterance = None
            return utterance, uplink_started, None

    def flush(self) -> Optional[RomUplinkFlush]:
        utterance = self._vad.flush()
        self._reset_rom()
        if not utterance:
            return None
        out = RomUplinkFlush(
            pcm=utterance,
            sample_rate=self.rom_sr,
            channels=self.rom_ch,
            codec=self.rom_codec,
        )
        duration_ms = int(len(utterance) / 2 / max(1, self.rom_sr) * 1000)
        logger.info(
            "[ROM uplink] flush utterance pcm_bytes=%d sr=%d ch=%d codec=%s duration_ms=%d",
            len(utterance),
            self.rom_sr,
            self.rom_ch,
            self.rom_codec,
            duration_ms,
        )
        return out

    def _reset_rom(self) -> None:
        self._uplink_open = False
        self.rom_sr = self.audio_cfg.sample_rate
        self.rom_ch = self.audio_cfg.channels
        self.rom_codec = self.audio_cfg.input_codec

    def cancel_rom_uplink(self) -> None:
        """设备端 TTS 打断：重置 VAD 状态。"""
        self._vad = SileroVadStream(
            self._silero_cfg,
            sample_rate=self.audio_cfg.sample_rate,
        )
        self._reset_rom()
        logger.info("[ROM uplink] audio_cancel reset Silero VAD state")
