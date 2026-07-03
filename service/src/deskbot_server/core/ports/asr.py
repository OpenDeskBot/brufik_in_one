from __future__ import annotations

from typing import Protocol


class AsrPort(Protocol):
    async def transcribe(self, pcm_bytes: bytes, sample_rate: int) -> str: ...

    def is_valid_text(self, text: str) -> bool: ...
