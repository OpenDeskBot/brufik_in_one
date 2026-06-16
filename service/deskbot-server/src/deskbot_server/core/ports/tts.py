from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class PhonemeSegment:
    phoneme: str
    ms: int
    pcm: bytes
    phoneme_id: Any = None


class TtsPort(Protocol):
    async def synthesize_phoneme_segments(self, text: str) -> tuple[int, list[PhonemeSegment]]: ...
