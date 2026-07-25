"""VAD（语音活动检测）服务。"""

from __future__ import annotations

from deskbot_server.service.pipeline.audio import AudioConfig, ConnectionSession
from deskbot_server.service.pipeline.silero_vad import SileroVadConfig, SileroVadStream
from deskbot_server.utils.singleton import SingletonMeta


class VadService(metaclass=SingletonMeta):
    """基于 Silero 的 VAD；按连接创建独立流式实例。"""

    def __init__(self) -> None:
        self._audio_cfg: AudioConfig | None = None

    def configure(self, audio_cfg: AudioConfig) -> None:
        self._audio_cfg = audio_cfg

    @property
    def audio_cfg(self) -> AudioConfig:
        if self._audio_cfg is None:
            raise RuntimeError("VadService 尚未 configure")
        return self._audio_cfg

    def create_stream(self, cfg: SileroVadConfig | None = None) -> SileroVadStream:
        audio = self.audio_cfg
        if cfg is None:
            cfg = SileroVadConfig(
                model_path=audio.silero_model_path,
                threshold=audio.silero_threshold,
                threshold_low=audio.silero_threshold_low,
                min_silence_ms=audio.max_silence_ms,
                min_speech_ms=audio.min_speech_ms,
                pre_speech_ms=audio.pre_speech_ms,
            )
        return SileroVadStream(cfg, sample_rate=audio.sample_rate)

    def create_connection_session(self, chat) -> ConnectionSession:
        return ConnectionSession(chat, self.audio_cfg)
