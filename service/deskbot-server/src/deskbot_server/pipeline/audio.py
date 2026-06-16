from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import opuslib_next
import webrtcvad

if TYPE_CHECKING:
    from deskbot_server.application.chat_service import ChatService

logger = logging.getLogger("deskbot-server")

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


class ConnectionSession:
    def __init__(self, pipeline: ChatService, audio_cfg: AudioConfig):
        self.pipeline = pipeline
        self.audio_cfg = audio_cfg
        self.vad = webrtcvad.Vad(audio_cfg.vad_mode)
        self.frame_bytes = int(audio_cfg.sample_rate * (audio_cfg.frame_ms / 1000.0) * 2)
        self.max_silence_frames = max(1, audio_cfg.max_silence_ms // audio_cfg.frame_ms)
        self.min_speech_frames = max(1, audio_cfg.min_speech_ms // audio_cfg.frame_ms)
        self.pre_frames = max(1, audio_cfg.pre_speech_ms // audio_cfg.frame_ms)
        self.pre_buffer = deque(maxlen=self.pre_frames)

        self.decoder = None
        if audio_cfg.input_codec == "opus":
            self.decoder = opuslib_next.Decoder(audio_cfg.sample_rate, audio_cfg.channels)

        self.pcm_feed = bytearray()
        self.in_speech = False
        self.silence_frames = 0
        self.speech_frames = 0
        self.current_utterance = bytearray()
        self.lock = asyncio.Lock()

    def _decode(self, payload: bytes, codec: Optional[str] = None) -> bytes:
        use_codec = (codec or self.audio_cfg.input_codec).lower()
        if use_codec == "pcm16":
            return payload
        if use_codec == "opus":
            if self.decoder is None:
                self.decoder = opuslib_next.Decoder(self.audio_cfg.sample_rate, self.audio_cfg.channels)
            return self.decoder.decode(payload, self.audio_cfg.sample_rate // 100)
        raise ValueError(f"unsupported codec: {use_codec}")

    async def feed_audio(self, payload: bytes, codec: Optional[str] = None) -> Optional[bytes]:
        async with self.lock:
            try:
                pcm = self._decode(payload, codec)
            except Exception as exc:
                logger.debug(
                    "audio decode skip codec=%s len=%d: %s",
                    codec or self.audio_cfg.input_codec,
                    len(payload),
                    exc,
                )
                return None
            self.pcm_feed.extend(pcm)
            utterance = None

            while len(self.pcm_feed) >= self.frame_bytes:
                frame = bytes(self.pcm_feed[: self.frame_bytes])
                del self.pcm_feed[: self.frame_bytes]

                is_speech = self.vad.is_speech(frame, self.audio_cfg.sample_rate)
                self.pre_buffer.append(frame)

                if is_speech:
                    self.silence_frames = 0
                    self.speech_frames += 1
                    if not self.in_speech:
                        self.in_speech = True
                        for old in self.pre_buffer:
                            self.current_utterance.extend(old)
                    self.current_utterance.extend(frame)
                elif self.in_speech:
                    self.silence_frames += 1
                    self.current_utterance.extend(frame)
                    if self.silence_frames >= self.max_silence_frames:
                        utt_bytes = len(self.current_utterance)
                        duration_ms = int(
                            utt_bytes / 2 / self.audio_cfg.sample_rate * 1000
                        )
                        if self.speech_frames >= self.min_speech_frames:
                            utterance = bytes(self.current_utterance)
                            logger.info(
                                "[VAD] 触发语音段 duration=%dms speech_frames=%d/min=%d "
                                "silence_frames=%d/max=%d (frame_ms=%d, mode=%d)",
                                duration_ms,
                                self.speech_frames,
                                self.min_speech_frames,
                                self.silence_frames,
                                self.max_silence_frames,
                                self.audio_cfg.frame_ms,
                                self.audio_cfg.vad_mode,
                            )
                        else:
                            logger.info(
                                "[VAD] 丢弃过短语音段 duration=%dms speech_frames=%d<min=%d "
                                "(frame_ms=%d, mode=%d)",
                                duration_ms,
                                self.speech_frames,
                                self.min_speech_frames,
                                self.audio_cfg.frame_ms,
                                self.audio_cfg.vad_mode,
                            )
                        self._reset_state()
                        break

            return utterance

    def flush(self) -> Optional[bytes]:
        if self.in_speech and self.speech_frames >= self.min_speech_frames:
            audio = bytes(self.current_utterance)
            self._reset_state()
            return audio
        self._reset_state()
        return None

    def _reset_state(self) -> None:
        self.in_speech = False
        self.silence_frames = 0
        self.speech_frames = 0
        self.current_utterance = bytearray()
        self.pre_buffer.clear()
