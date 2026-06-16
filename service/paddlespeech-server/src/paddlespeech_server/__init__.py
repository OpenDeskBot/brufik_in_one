"""PaddleSpeech TTS sidecar with phoneme-aligned streaming WebSocket."""

from __future__ import annotations

from pathlib import Path


def _read_version() -> str:
    vf = Path(__file__).resolve().parents[2] / ".paddlespeech-server_version"
    if vf.is_file():
        raw = vf.read_text(encoding="utf-8").strip()
        return raw.lstrip("v")
    return "0.0.0"


__version__ = _read_version()
