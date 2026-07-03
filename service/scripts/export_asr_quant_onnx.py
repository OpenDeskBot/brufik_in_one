#!/usr/bin/env python3
"""若缺少 ``model_quant.onnx``，从 ``model.pt`` 导出量化 ONNX（CPU 推理）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deskbot_server.asr_model_dir import ensure_asr_quant_onnx  # noqa: E402


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "models" / "SenseVoiceSmall"
    path = ensure_asr_quant_onnx(target)
    if path is None:
        print(f"跳过：{target} 无可用 PyTorch 权重或导出失败", file=sys.stderr)
        sys.exit(1)
    print(f"ASR 量化 ONNX 就绪: {path}")


if __name__ == "__main__":
    main()
