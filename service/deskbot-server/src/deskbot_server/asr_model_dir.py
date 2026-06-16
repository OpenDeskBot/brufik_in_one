"""SenseVoiceSmall 本地目录是否已包含可用权重（供 start.sh / download_model / FunASR 共用）。"""
from __future__ import annotations

from pathlib import Path

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
