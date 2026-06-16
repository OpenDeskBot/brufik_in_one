#!/usr/bin/env python3
"""下载 SenseVoiceSmall 到 deskbot-server/models/。"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deskbot_server.asr_model_dir import asr_model_dir_ready  # noqa: E402
from modelscope import snapshot_download


def main() -> None:
    target_dir = ROOT / "models" / "SenseVoiceSmall"
    os.makedirs(target_dir, exist_ok=True)
    if asr_model_dir_ready(target_dir):
        print(f"ASR 模型已存在，跳过下载: {target_dir}")
        return
    print(f"开始下载模型到: {target_dir}")
    kwargs: dict = {
        "model_id": "iic/SenseVoiceSmall",
        "local_dir": str(target_dir),
    }
    sig = inspect.signature(snapshot_download)
    if "local_dir_use_symlinks" in sig.parameters:
        kwargs["local_dir_use_symlinks"] = False
    snapshot_download(**kwargs)
    print("模型下载完成。")


if __name__ == "__main__":
    main()
