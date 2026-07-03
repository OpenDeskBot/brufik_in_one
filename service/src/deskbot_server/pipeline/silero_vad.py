"""Silero VAD 流式切分（参考 xiaozhi silero.py）。"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import onnxruntime

logger = logging.getLogger("deskbot-server")

SILERO_CHUNK_SAMPLES = 512  # 32 ms @ 16 kHz


@dataclass(frozen=True)
class SileroVadConfig:
    model_path: str
    threshold: float = 0.5
    threshold_low: float = 0.2
    min_silence_ms: int = 500
    min_speech_ms: int = 250
    pre_speech_ms: int = 300
    frame_window_threshold: int = 3


class SileroVadStream:
    """PCM 流 → 按句切分；纯静音段丢弃。"""

    def __init__(self, cfg: SileroVadConfig, sample_rate: int = 16000):
        self.cfg = cfg
        self.sample_rate = sample_rate
        if not Path(cfg.model_path).is_file():
            raise FileNotFoundError(f"Silero VAD model not found: {cfg.model_path}")

        opts = onnxruntime.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        self._session = onnxruntime.InferenceSession(
            cfg.model_path,
            providers=["CPUExecutionProvider"],
            sess_options=opts,
        )
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, 64), dtype=np.float32)
        self._pending_pcm = bytearray()
        self._voice_window: deque[bool] = deque(maxlen=max(1, cfg.frame_window_threshold))
        self._last_is_voice = False
        self._client_have_voice = False
        self._audio_ms_processed = 0.0
        self._last_voice_audio_ms = 0.0
        self._chunk_ms = SILERO_CHUNK_SAMPLES * 1000.0 / max(1, sample_rate)
        self._pre_roll = bytearray()
        self._pre_roll_cap = max(0, sample_rate * cfg.pre_speech_ms // 1000 * 2)
        self._speech_pcm = bytearray()

    def _reset_utterance(self) -> None:
        self._speech_pcm.clear()
        self._client_have_voice = False

    def _append_pre_roll(self, chunk: bytes) -> None:
        if self._pre_roll_cap <= 0:
            return
        self._pre_roll.extend(chunk)
        overflow = len(self._pre_roll) - self._pre_roll_cap
        if overflow > 0:
            del self._pre_roll[:overflow]

    def _classify_chunk(self, chunk: bytes) -> bool:
        audio_int16 = np.frombuffer(chunk, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        audio_input = np.concatenate(
            [self._context, audio_float32.reshape(1, -1)], axis=1
        ).astype(np.float32)
        out, state = self._session.run(
            None,
            {
                "input": audio_input,
                "state": self._state,
                "sr": np.array(self.sample_rate, dtype=np.int64),
            },
        )
        self._state = state
        self._context = audio_input[:, -64:]
        speech_prob = float(out.item())

        if speech_prob >= self.cfg.threshold:
            is_voice = True
        elif speech_prob <= self.cfg.threshold_low:
            is_voice = False
        else:
            is_voice = self._last_is_voice
        self._last_is_voice = is_voice

        self._voice_window.append(is_voice)
        return self._voice_window.count(True) >= self.cfg.frame_window_threshold

    def _silence_ms_since_voice(self) -> float:
        if not self._client_have_voice:
            return 0.0
        return max(0.0, self._audio_ms_processed - self._last_voice_audio_ms)

    def _maybe_finish_utterance(self, *, force: bool = False) -> Optional[bytes]:
        if not self._client_have_voice or not self._speech_pcm:
            return None
        silence_ms = self._silence_ms_since_voice()
        if not force and silence_ms < self.cfg.min_silence_ms:
            return None
        duration_ms = len(self._speech_pcm) // 2 * 1000 // max(1, self.sample_rate)
        if duration_ms < self.cfg.min_speech_ms:
            logger.debug(
                "[SileroVAD] discard short utterance duration_ms=%d (< %d)",
                duration_ms,
                self.cfg.min_speech_ms,
            )
            self._reset_utterance()
            return None
        utterance = bytes(self._speech_pcm)
        logger.info(
            "[SileroVAD] utterance ready pcm_bytes=%d duration_ms=%d",
            len(utterance),
            duration_ms,
        )
        self._reset_utterance()
        return utterance

    def _process_chunk(self, chunk: bytes) -> Optional[bytes]:
        """喂入一个 Silero 块；若切句完成则返回 utterance PCM。"""
        self._audio_ms_processed += self._chunk_ms
        have_voice = self._classify_chunk(chunk)

        if have_voice:
            if not self._client_have_voice:
                self._speech_pcm.extend(self._pre_roll)
                self._pre_roll.clear()
                self._client_have_voice = True
            self._speech_pcm.extend(chunk)
            self._last_voice_audio_ms = self._audio_ms_processed
            return None

        if not self._client_have_voice:
            self._append_pre_roll(chunk)
            return None

        self._speech_pcm.extend(chunk)
        if self._silence_ms_since_voice() >= self.cfg.min_silence_ms:
            return self._maybe_finish_utterance()
        return None

    def feed_pcm(self, pcm: bytes) -> Optional[bytes]:
        if not pcm:
            return None
        self._pending_pcm.extend(pcm)
        utterance: Optional[bytes] = None

        while len(self._pending_pcm) >= SILERO_CHUNK_SAMPLES * 2:
            chunk = bytes(self._pending_pcm[: SILERO_CHUNK_SAMPLES * 2])
            del self._pending_pcm[: SILERO_CHUNK_SAMPLES * 2]
            finished = self._process_chunk(chunk)
            if finished is not None:
                utterance = finished

        return utterance

    def flush(self) -> Optional[bytes]:
        if self._pending_pcm:
            tail = bytes(self._pending_pcm)
            self._pending_pcm.clear()
            pad_samples = (SILERO_CHUNK_SAMPLES - (len(tail) // 2) % SILERO_CHUNK_SAMPLES) % SILERO_CHUNK_SAMPLES
            if pad_samples:
                tail += b"\x00\x00" * pad_samples
            for i in range(0, len(tail), SILERO_CHUNK_SAMPLES * 2):
                chunk = tail[i : i + SILERO_CHUNK_SAMPLES * 2]
                self._process_chunk(chunk)
        finished = self._maybe_finish_utterance(force=True)
        self._pre_roll.clear()
        self._pending_pcm.clear()
        return finished
