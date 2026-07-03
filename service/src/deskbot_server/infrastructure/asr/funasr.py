from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

import numpy as np

from deskbot_server.asr_model_dir import asr_model_dir_ready, ensure_asr_quant_onnx, has_quant_onnx
from deskbot_server.core.concurrency import asr_infer_slot
from deskbot_server.core.settings import AppSettings
from deskbot_server.paths import MODELS_DIR, PROJECT_ROOT
from deskbot_server.util import pcm_to_wav_bytes

logger = logging.getLogger("deskbot-server")


class FunAsrAdapter:
    """FunASR SenseVoice ASR：优先 ``model_quant.onnx``（CPU），否则回退 PyTorch。"""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._language = settings.asr.language
        self._min_text_len = settings.asr.text_filter.min_text_len
        self._min_chinese_ratio = settings.asr.text_filter.min_chinese_ratio
        self._use_quant_onnx = settings.asr.use_quant_onnx
        self._onnx_threads = settings.asr.onnx_intra_op_threads
        model_dir = self._resolve_model_dir(settings.asr.model_dir)
        self._validate_model_dir(model_dir)
        self._model_dir = model_dir
        self._onnx_model: Any = None
        self._pt_model: Any = None

        if self._use_quant_onnx:
            if not has_quant_onnx(model_dir):
                ensure_asr_quant_onnx(model_dir)
            if has_quant_onnx(model_dir):
                self._onnx_model = self._load_onnx_model(model_dir)
                logger.info(
                    "[ASR] 使用量化 ONNX 推理 model_dir=%s threads=%d",
                    model_dir,
                    self._onnx_threads,
                )
            else:
                logger.warning(
                    "[ASR] model_quant.onnx 不可用，回退 PyTorch model.pt model_dir=%s",
                    model_dir,
                )

        if self._onnx_model is None:
            from funasr import AutoModel

            self._pt_model = AutoModel(
                model=model_dir,
                disable_update=True,
                hub=settings.asr.hub,
            )
            logger.info("[ASR] 使用 PyTorch 推理 model_dir=%s", model_dir)

    def _load_onnx_model(self, model_dir: str) -> Any:
        from funasr_onnx import SenseVoiceSmall

        threads = self._onnx_threads
        env_raw = (os.environ.get("DESKBOT_ASR_ONNX_THREADS") or "").strip()
        if env_raw:
            try:
                threads = max(1, int(env_raw))
            except ValueError:
                pass
        return SenseVoiceSmall(
            model_dir,
            batch_size=1,
            quantize=True,
            intra_op_num_threads=threads,
        )

    @staticmethod
    def _resolve_model_dir(config_model_dir: str) -> str:
        local_default = MODELS_DIR / "SenseVoiceSmall"
        candidates: list[str] = []
        env_dir = (os.environ.get("ASR_MODEL_DIR") or "").strip()
        if env_dir:
            candidates.append(env_dir)
        if config_model_dir:
            candidates.append(config_model_dir)
        candidates.append(str(local_default))

        seen: set[str] = set()
        for raw in candidates:
            path = FunAsrAdapter._normalize_model_path(raw)
            if not path or path in seen:
                continue
            seen.add(path)
            if os.path.isdir(path):
                return path

        for raw in candidates:
            path = FunAsrAdapter._normalize_model_path(raw)
            if path:
                return path
        return ""

    @staticmethod
    def _normalize_model_path(raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return ""
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return str(path.resolve())

    @staticmethod
    def _validate_model_dir(model_dir: str) -> None:
        if not model_dir or not os.path.isdir(model_dir):
            raise ValueError(
                f"ASR 模型目录不存在: {model_dir or '(未配置)'}\n"
                f"请执行: cd {PROJECT_ROOT} && python scripts/download_model.py\n"
                f"并在 .env 中设置 ASR_MODEL_DIR=./models/SenseVoiceSmall"
            )
        if asr_model_dir_ready(model_dir):
            return
        raise ValueError(
            f"ASR 模型目录缺少权重文件（如 model.pt / model_quant.onnx）。当前目录: {model_dir}。"
            "请先下载完整 SenseVoiceSmall 模型。"
        )

    async def transcribe(self, pcm_bytes: bytes, sample_rate: int) -> str:
        async with asr_infer_slot():
            if self._onnx_model is not None:
                lines = await asyncio.to_thread(
                    self._transcribe_onnx,
                    pcm_bytes,
                    sample_rate,
                )
            else:
                lines = await asyncio.to_thread(
                    self._transcribe_pytorch,
                    pcm_bytes,
                    sample_rate,
                )
        if not lines:
            return ""
        raw_text = str(lines[0] if isinstance(lines, list) else lines).strip()
        return self._normalize_text(raw_text)

    def _transcribe_onnx(self, pcm_bytes: bytes, sample_rate: int) -> list[str]:
        waveform = self._pcm_to_waveform(pcm_bytes, sample_rate)
        lang = self._language if self._language in ("auto", "zh", "en", "yue", "ja", "ko") else "zh"
        return self._onnx_model(
            waveform,
            language=lang,
            textnorm="withitn",
        )

    def _transcribe_pytorch(self, pcm_bytes: bytes, sample_rate: int) -> list:
        wav_bytes = pcm_to_wav_bytes(pcm_bytes, sample_rate)
        result = self._pt_model.generate(
            input=wav_bytes,
            cache={},
            language=self._language,
            use_itn=True,
        )
        if not result:
            return []
        return [str(result[0].get("text", "")).strip()]

    @staticmethod
    def _pcm_to_waveform(pcm_bytes: bytes, sample_rate: int) -> np.ndarray:
        if not pcm_bytes:
            return np.zeros(0, dtype=np.float32)
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16)
        wave = pcm.astype(np.float32) / 32768.0
        if sample_rate != 16000:
            import librosa

            wave = librosa.resample(wave, orig_sr=sample_rate, target_sr=16000)
        return wave

    def is_valid_text(self, text: str) -> bool:
        from deskbot_server.asr.text_filter import is_asr_text_acceptable

        return is_asr_text_acceptable(
            text,
            min_len=self._min_text_len,
            min_chinese_ratio=self._min_chinese_ratio,
        )

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = re.sub(r"<\|[^|]+?\|>", "", text)
        return text.strip()
