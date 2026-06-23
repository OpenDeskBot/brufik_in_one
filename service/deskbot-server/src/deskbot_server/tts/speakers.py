"""豆包 TTS 音色预设（火山引擎大模型音色列表）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_PKG_ROOT = Path(__file__).resolve().parents[3]
_SPEAKERS_JSON = _PKG_ROOT / "data" / "doubao_tts_speakers.json"


@dataclass(frozen=True)
class DoubaoTtsSpeakerPreset:
    label: str
    id: str
    scene: str = ""
    description: str = ""
    resource_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def suggest_resource_id(speaker: str) -> str:
    """根据 speaker ID 推断 Resource ID。"""
    s = (speaker or "").strip()
    if not s:
        return "seed-tts-2.0"
    if s.startswith("S_"):
        return "seed-icl-2.0"
    if "_uranus_" in s or s.startswith("saturn_"):
        return "seed-tts-2.0"
    if "_mars_" in s or "_moon_" in s or s.startswith("ICL_"):
        return "seed-tts-1.0"
    return "seed-tts-2.0"


def _row_to_preset(row: dict[str, Any]) -> DoubaoTtsSpeakerPreset:
    speaker_id = str(row.get("id") or "").strip()
    return DoubaoTtsSpeakerPreset(
        label=str(row.get("label") or speaker_id).strip(),
        id=speaker_id,
        scene=str(row.get("scene") or "").strip(),
        description=str(row.get("description") or "").strip(),
        resource_id=str(row.get("resource_id") or suggest_resource_id(speaker_id)).strip(),
    )


@lru_cache(maxsize=1)
def _load_speaker_presets() -> tuple[DoubaoTtsSpeakerPreset, ...]:
    rows: list[dict[str, Any]] = []
    if _SPEAKERS_JSON.is_file():
        raw = json.loads(_SPEAKERS_JSON.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            rows.extend(item for item in raw if isinstance(item, dict))

    by_id: dict[str, DoubaoTtsSpeakerPreset] = {}
    for row in rows:
        preset = _row_to_preset(row)
        if preset.id:
            by_id[preset.id] = preset

    return tuple(by_id.values())


def list_doubao_tts_speaker_presets() -> list[dict[str, Any]]:
    items = sorted(
        _load_speaker_presets(),
        key=lambda item: (
            0 if "_uranus_" in item.id or item.id.startswith("saturn_") else 1,
            item.scene,
            item.label,
        ),
    )
    return [item.to_dict() for item in items]


def find_doubao_tts_speaker_preset(speaker_id: str) -> DoubaoTtsSpeakerPreset | None:
    needle = (speaker_id or "").strip()
    if not needle:
        return None
    for item in _load_speaker_presets():
        if item.id == needle:
            return item
    return None
