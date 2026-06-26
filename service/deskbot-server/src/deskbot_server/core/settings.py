from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

def _env_bool(name: str) -> Optional[bool]:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


@dataclass(frozen=True)
class ServerSettings:
    host: str = "0.0.0.0"
    port: int = 9000
    ws_path: str = "/asr_chat"
    ws_ping_interval: Optional[float] = 20.0
    ws_ping_timeout: float = 20.0
    asr_chat_device_pb_only: bool = True
    asr_chat_minimal_device_downlink: bool = False
    send_face_info_to_asr_chat: bool = False
    pb_idle_snore_sec: float = 5.0
    pb_idle_snore_scene: str = "sleep_snore"
    pb_idle_silence_sec: float = 2.0
    web_public_host: str = ""
    # 0 = 启动时按 CPU 核数推导（见 core/concurrency.py）
    max_concurrent_asr: int = 0
    max_concurrent_face_infer: int = 0


@dataclass(frozen=True)
class AudioSettings:
    input_codec: str = "opus"
    sample_rate: int = 16000
    channels: int = 1


@dataclass(frozen=True)
class VadSettings:
    mode: int = 2
    frame_ms: int = 30
    min_speech_ms: int = 300
    max_silence_ms: int = 500
    pre_speech_ms: int = 300


@dataclass(frozen=True)
class AsrTextFilterSettings:
    min_text_len: int = 2
    min_chinese_ratio: float = 0.0


@dataclass(frozen=True)
class AsrSettings:
    model_dir: str = ""
    hub: str = "hf"
    language: str = "zh"
    text_filter: AsrTextFilterSettings = field(default_factory=AsrTextFilterSettings)


@dataclass(frozen=True)
class LlmSettings:
    base_url: str = ""
    model_name: str = "qwen-flash"
    system_prompt: str = ""


@dataclass(frozen=True)
class TtsSettings:
    provider: str = "doubao"
    ws_url: str = ""
    lang: str = "zh"
    spk_id: int = 0
    sample_rate: int = 24000
    pb_random_servo: dict[str, Any] = field(default_factory=dict)
    pb_face_bundle_json: str = ""
    pb_face_bundle_file: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppSettings:
    """统一运行时配置：YAML + 环境变量覆盖。"""

    server: ServerSettings
    audio: AudioSettings
    vad: VadSettings
    asr: AsrSettings
    llm: LlmSettings
    tts: TtsSettings
    camera_face: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> AppSettings:
        srv = dict(config.get("server") or {})
        audio = dict(config.get("audio") or {})
        vad = dict(config.get("vad") or {})
        asr = dict(config.get("asr") or {})
        llm = dict(config.get("llm") or {})
        tts = dict(config.get("tts") or {})
        tf = dict(asr.get("text_filter") or {})

        pb_only_env = _env_bool("DESKBOT_ASR_CHAT_DEVICE_PB_ONLY")
        pb_only = (
            pb_only_env
            if pb_only_env is not None
            else bool(srv.get("asr_chat_device_pb_only", True))
        )

        minimal_env = _env_bool("DESKBOT_ASR_CHAT_MINIMAL_DOWNLINK")
        minimal = (
            minimal_env
            if minimal_env is not None
            else bool(srv.get("asr_chat_minimal_device_downlink", False))
        )

        face_info_env = _env_bool("DESKBOT_SEND_FACE_INFO")
        face_info = (
            face_info_env
            if face_info_env is not None
            else bool(srv.get("send_face_info_to_asr_chat", False))
        )

        if os.environ.get("TTS_PROVIDER"):
            tts["provider"] = os.environ["TTS_PROVIDER"]
        if os.environ.get("TTS_WS_URL"):
            tts["ws_url"] = os.environ["TTS_WS_URL"]
        if os.environ.get("TTS_SPK_ID"):
            tts["spk_id"] = int(os.environ["TTS_SPK_ID"])
        if os.environ.get("TTS_SAMPLE_RATE"):
            tts["sample_rate"] = int(os.environ["TTS_SAMPLE_RATE"])

        tts_extra = {
            k: v
            for k, v in tts.items()
            if k
            not in (
                "provider",
                "ws_url",
                "lang",
                "spk_id",
                "sample_rate",
                "pb_random_servo",
                "pb_face_bundle_json",
                "pb_face_bundle_file",
            )
        }

        idle_sec = srv.get("pb_idle_snore_sec", 5)
        if "DESKBOT_PB_IDLE_SNORE_SEC" in os.environ:
            raw_idle = str(os.environ.get("DESKBOT_PB_IDLE_SNORE_SEC", "")).strip()
            try:
                idle_sec = float(raw_idle) if raw_idle else 0.0
            except ValueError:
                pass

        idle_scene = str(srv.get("pb_idle_snore_scene") or "sleep_snore")
        if "DESKBOT_PB_IDLE_SNORE_SCENE" in os.environ:
            idle_scene = (os.environ.get("DESKBOT_PB_IDLE_SNORE_SCENE") or "sleep_snore").strip()

        idle_silence_sec = srv.get("pb_idle_silence_sec", 2)
        if "DESKBOT_PB_IDLE_SILENCE_SEC" in os.environ:
            raw_silence = str(os.environ.get("DESKBOT_PB_IDLE_SILENCE_SEC", "")).strip()
            try:
                idle_silence_sec = float(raw_silence) if raw_silence else 0.0
            except ValueError:
                pass

        return cls(
            server=ServerSettings(
                host=os.environ.get("DESKBOT_SERVER_HOST") or str(srv.get("host", "0.0.0.0")),
                port=int(os.environ.get("DESKBOT_SERVER_PORT") or srv.get("port", 9000)),
                ws_path=os.environ.get("DESKBOT_WS_PATH") or str(srv.get("ws_path", "/asr_chat")),
                ws_ping_interval=_parse_ping_interval(
                    os.environ.get("DESKBOT_WS_PING_INTERVAL"),
                    srv.get("ws_ping_interval", 20),
                ),
                ws_ping_timeout=float(
                    os.environ.get("DESKBOT_WS_PING_TIMEOUT") or srv.get("ws_ping_timeout", 20)
                ),
                asr_chat_device_pb_only=pb_only,
                asr_chat_minimal_device_downlink=minimal,
                send_face_info_to_asr_chat=face_info and not pb_only,
                pb_idle_snore_sec=max(0.0, float(idle_sec)),
                pb_idle_snore_scene=idle_scene or "sleep_snore",
                pb_idle_silence_sec=max(0.0, float(idle_silence_sec)),
                web_public_host=str(srv.get("web_public_host") or ""),
                max_concurrent_asr=int(srv.get("max_concurrent_asr", 0)),
                max_concurrent_face_infer=int(srv.get("max_concurrent_face_infer", 0)),
            ),
            audio=AudioSettings(
                input_codec=str(audio.get("input_codec", "opus")),
                sample_rate=int(audio.get("sample_rate", 16000)),
                channels=int(audio.get("channels", 1)),
            ),
            vad=VadSettings(
                mode=int(vad.get("mode", 2)),
                frame_ms=int(vad.get("frame_ms", 30)),
                min_speech_ms=int(vad.get("min_speech_ms", 300)),
                max_silence_ms=int(vad.get("max_silence_ms", 500)),
                pre_speech_ms=int(vad.get("pre_speech_ms", 300)),
            ),
            asr=AsrSettings(
                model_dir=str(asr.get("model_dir", "")),
                hub=str(asr.get("hub", "hf")),
                language=str(asr.get("language", "zh")),
                text_filter=AsrTextFilterSettings(
                    min_text_len=int(tf.get("min_text_len", 2)),
                    min_chinese_ratio=float(tf.get("min_chinese_ratio", 0.2)),
                ),
            ),
            llm=LlmSettings(
                base_url=str(llm.get("base_url", "")),
                model_name=str(llm.get("model_name", "qwen-flash")),
                system_prompt=str(llm.get("system_prompt", "")),
            ),
            tts=TtsSettings(
                provider=str(tts.get("provider") or "doubao").strip().lower(),
                ws_url=str(tts.get("ws_url", "")),
                lang=str(tts.get("lang", "zh")),
                spk_id=int(tts.get("spk_id", 0)),
                sample_rate=int(tts.get("sample_rate", 24000)),
                pb_random_servo=dict(tts.get("pb_random_servo") or {}),
                pb_face_bundle_json=str(tts.get("pb_face_bundle_json") or ""),
                pb_face_bundle_file=str(tts.get("pb_face_bundle_file") or ""),
                extra=tts_extra,
            ),
            camera_face=dict(config.get("camera_face") or {}),
            raw=config,
        )

    def pb_random_servo_cfg(self) -> Optional[dict[str, Any]]:
        sub = self.tts.pb_random_servo
        env = _env_bool("DESKBOT_PB_RANDOM_SERVO")
        enabled = bool(sub.get("enabled", False))
        if env is True:
            enabled = True
        if env is False:
            enabled = False
        if not enabled:
            return None
        out = dict(sub)
        out["enabled"] = True
        return out

    @property
    def tts_cfg(self) -> dict[str, Any]:
        """兼容旧代码对 dict 形式 tts 配置的访问。"""
        base = {
            "provider": self.tts.provider,
            "ws_url": self.tts.ws_url,
            "lang": self.tts.lang,
            "spk_id": self.tts.spk_id,
            "sample_rate": self.tts.sample_rate,
            "pb_random_servo": self.tts.pb_random_servo,
            "pb_face_bundle_json": self.tts.pb_face_bundle_json,
            "pb_face_bundle_file": self.tts.pb_face_bundle_file,
        }
        base.update(self.tts.extra)
        return base


def _parse_ping_interval(env_val: Optional[str], cfg_val: Any) -> Optional[float]:
    raw = env_val if env_val is not None else str(cfg_val)
    raw = str(raw).strip().lower()
    if raw in ("0", "none", "off", "false"):
        return None
    return max(5.0, float(raw))
