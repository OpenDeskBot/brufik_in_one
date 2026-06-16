"""``streaming_phoneme`` 中英混合 ONNX 合成（``fastspeech2_mix``，不依赖官方流式 cnndecoder 引擎）。"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional

import numpy as np
import yaml
from paddlespeech.cli.log import logger
from paddlespeech.cli.tts.infer import TTSExecutor

from .phoneme import (
    collect_pcm_int16_from_wav,
    flatten_phone_ids_from_frontend,
    id_to_symbol_map_from_frontend,
    sanitize_mix_tts_text,
    split_pcm_by_phonemes,
)


@dataclass(frozen=True)
class PhonemeTtsConfig:
    enabled: bool = True
    lang: str = "mix"
    am: str = "fastspeech2_mix"
    voc: str = "hifigan_csmsc"
    default_spk_id: int = 174
    device: str = "cpu"
    cpu_threads: int = 4


_ENGINE: Optional["MixPhonemeEngine"] = None


def load_phoneme_tts_config(config_file: str) -> PhonemeTtsConfig:
    with open(config_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    raw = data.get("phoneme_tts") or {}
    if not isinstance(raw, dict):
        raw = {}
    return PhonemeTtsConfig(
        enabled=bool(raw.get("enabled", True)),
        lang=str(raw.get("lang", "mix")),
        am=str(raw.get("am", "fastspeech2_mix")),
        voc=str(raw.get("voc", "hifigan_csmsc")),
        default_spk_id=int(raw.get("spk_id", raw.get("default_spk_id", 174))),
        device=str(raw.get("device", "cpu")),
        cpu_threads=int(raw.get("cpu_threads", 4)),
    )


def init_mix_phoneme_engine(config_file: str) -> MixPhonemeEngine | None:
    global _ENGINE
    cfg = load_phoneme_tts_config(config_file)
    if not cfg.enabled or cfg.lang != "mix":
        _ENGINE = None
        return None
    _ENGINE = MixPhonemeEngine(cfg)
    _ENGINE.ensure_loaded()
    logger.info(
        f"phoneme_tts mix engine ready: am={cfg.am} voc={cfg.voc} "
        f"spk_id={cfg.default_spk_id} sr={_ENGINE.sample_rate}"
    )
    return _ENGINE


def get_mix_phoneme_engine() -> MixPhonemeEngine | None:
    return _ENGINE


def is_mix_phoneme_enabled() -> bool:
    return _ENGINE is not None


class MixPhonemeEngine:
    """基于 ``TTSExecutor`` ONNX 的整句 mix 合成，供 WebSocket 音素对齐。"""

    def __init__(self, cfg: PhonemeTtsConfig) -> None:
        self.cfg = cfg
        self._executor: TTSExecutor | None = None
        self.sample_rate = 24000

    def ensure_loaded(self) -> TTSExecutor:
        if self._executor is not None:
            return self._executor
        logger.info("Loading phoneme_tts ONNX models (first run may download weights)...")
        from paddlespeech.resource import CommonTaskResource

        ex = TTSExecutor()
        ex.task_resource = CommonTaskResource(task="tts", model_format="onnx")
        ex._init_from_path_onnx(
            am=self.cfg.am,
            voc=self.cfg.voc,
            lang=self.cfg.lang,
            device=self.cfg.device,
            cpu_threads=self.cfg.cpu_threads,
        )
        ex.infer_onnx(text="。", lang=self.cfg.lang, am=self.cfg.am, spk_id=self.cfg.default_spk_id)
        self._executor = ex
        self.sample_rate = int(getattr(ex, "am_fs", 24000) or 24000)
        return ex

    @property
    def handler(self) -> Any:
        """与 ``flatten_phone_ids`` 兼容的伪 connection_handler。"""
        ex = self.ensure_loaded()
        return SimpleNamespace(
            executor=ex,
            config=SimpleNamespace(lang=self.cfg.lang, am=self.cfg.am),
        )

    def synthesize_segments(
        self, text: str, spk_id: int | None = None
    ) -> tuple[int, list[dict[str, Any]]]:
        ex = self.ensure_loaded()
        sid = self.cfg.default_spk_id if spk_id is None else int(spk_id)
        clean = sanitize_mix_tts_text(text)
        h = self.handler
        phone_ids = flatten_phone_ids_from_frontend(h, clean)
        pcm = collect_pcm_int16_from_wav(ex, clean, sid, self.cfg.lang, self.cfg.am)
        id_to_sym = id_to_symbol_map_from_frontend(h)
        segments = split_pcm_by_phonemes(
            pcm, phone_ids, sample_rate=self.sample_rate, id_to_sym=id_to_sym
        )
        return self.sample_rate, segments
