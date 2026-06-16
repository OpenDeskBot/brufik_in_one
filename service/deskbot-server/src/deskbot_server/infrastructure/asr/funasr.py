from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional

from funasr import AutoModel

from deskbot_server.asr_model_dir import asr_model_dir_ready
from deskbot_server.core.concurrency import asr_infer_slot
from deskbot_server.core.settings import AppSettings
from deskbot_server.paths import MODELS_DIR, PROJECT_ROOT
from deskbot_server.util import save_temp_wav

logger = logging.getLogger("deskbot-server")


class FunAsrAdapter:
    """FunASR SenseVoice ASR 适配器。"""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._language = settings.asr.language
        self._min_text_len = settings.asr.text_filter.min_text_len
        self._min_chinese_ratio = settings.asr.text_filter.min_chinese_ratio
        model_dir = self._resolve_model_dir(settings.asr.model_dir)
        self._validate_model_dir(model_dir)
        self._model = AutoModel(
            model=model_dir,
            disable_update=True,
            hub=settings.asr.hub,
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

        # 返回首个候选供 _validate_model_dir 打出明确路径
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
            wav_path = await asyncio.to_thread(save_temp_wav, pcm_bytes, sample_rate)
            try:
                result = await asyncio.to_thread(
                    self._model.generate,
                    input=wav_path,
                    cache={},
                    language=self._language,
                    use_itn=True,
                )
            finally:
                try:
                    os.remove(wav_path)
                except OSError:
                    pass
        if not result:
            return ""
        raw_text = str(result[0].get("text", "")).strip()
        return self._normalize_text(raw_text)

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
