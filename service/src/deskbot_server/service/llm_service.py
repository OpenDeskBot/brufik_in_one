"""LLM 对话补全服务。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional

from deskbot_server.core.ports.llm import LlmPort
from deskbot_server.utils.singleton import SingletonMeta


class LlmService(metaclass=SingletonMeta):
    def __init__(self) -> None:
        self._llm: LlmPort | None = None

    def bind(self, llm: LlmPort) -> None:
        self._llm = llm

    @property
    def llm(self) -> LlmPort:
        if self._llm is None:
            raise RuntimeError("LlmService 尚未 bind，请先在 bootstrap 中装配")
        return self._llm

    async def complete(
        self,
        text: str,
        *,
        device_context: str | None = None,
        device_id: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        extra_messages: list[dict[str, str]] | None = None,
        on_tts_ready: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        return await self.llm.complete(
            text,
            device_context=device_context,
            device_id=device_id,
            history_messages=history_messages,
            extra_messages=extra_messages,
            on_tts_ready=on_tts_ready,
        )
