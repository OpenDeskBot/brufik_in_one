from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol


class LlmPort(Protocol):
    async def complete(
        self,
        user_text: str,
        *,
        device_context: str | None = None,
        device_id: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        extra_messages: list[dict[str, str]] | None = None,
        on_tts_ready: Callable[[str], Awaitable[None]] | None = None,
    ) -> str: ...
