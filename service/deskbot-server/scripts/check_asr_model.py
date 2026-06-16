#!/usr/bin/env python3
"""检查 ASR 模型目录是否就绪；退出码 0=已就绪，1=缺失。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deskbot_server.asr_model_dir import asr_model_dir_ready  # noqa: E402


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "models" / "SenseVoiceSmall"
    sys.exit(0 if asr_model_dir_ready(target) else 1)


if __name__ == "__main__":
    main()
