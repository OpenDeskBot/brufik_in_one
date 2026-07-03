"""SenseVoiceSmall 本地目录是否已包含可用权重（供 start.sh / download_model / FunASR 共用）。"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("deskbot-server")

# FunASR / ModelScope 常见权重文件名（含子目录布局 iic/SenseVoiceSmall/...）
_WEIGHT_NAMES = frozenset(
    {
        "model.pt",
        "model.onnx",
        "model_quant.onnx",
        "pytorch_model.bin",
        "weights.pb",
    }
)

_QUANT_ONNX_NAME = "model_quant.onnx"
_PT_NAME = "model.pt"


def quant_onnx_path(model_dir: str | Path) -> Path:
    return Path(model_dir) / _QUANT_ONNX_NAME


def has_quant_onnx(model_dir: str | Path) -> bool:
    p = quant_onnx_path(model_dir)
    return p.is_file() and p.stat().st_size > 0


def ensure_asr_quant_onnx(model_dir: str | Path) -> Path | None:
    """若 ``model_quant.onnx`` 不存在且存在 ``model.pt``，通过 funasr-onnx 触发导出。"""
    root = Path(model_dir)
    quant = quant_onnx_path(root)
    if has_quant_onnx(root):
        return quant
    if not (root / _PT_NAME).is_file():
        return None
    try:
        from funasr_onnx import SenseVoiceSmall
    except ImportError as exc:
        logger.warning(
            "[ASR] 缺少 funasr-onnx，无法导出 model_quant.onnx: %s", exc
        )
        return None
    logger.info("[ASR] 导出量化 ONNX → %s", quant)
    SenseVoiceSmall(str(root.resolve()), batch_size=1, quantize=True)
    if has_quant_onnx(root):
        return quant
    logger.error("[ASR] 导出后仍未找到 %s", quant)
    return None


def asr_model_dir_ready(model_dir: str | Path) -> bool:
    root = Path(model_dir)
    if not root.is_dir():
        return False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name in _WEIGHT_NAMES:
            return True
        if path.suffix in (".pt", ".onnx"):
            return True
    return False
