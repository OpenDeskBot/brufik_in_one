"""对话编排服务：持有 ``application.ChatService`` 单例引用。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Optional

from deskbot_server.utils.singleton import SingletonMeta

if TYPE_CHECKING:
    from deskbot_server.service.application.chat_service import ChatService


class ChatAppService(metaclass=SingletonMeta):
    """对现有 ``ChatService`` 的单例门面，供 Controller 取用。"""

    def __init__(self) -> None:
        self._chat: ChatService | None = None

    def bind(self, chat: "ChatService") -> None:
        self._chat = chat

    @property
    def chat(self) -> "ChatService":
        if self._chat is None:
            raise RuntimeError("ChatAppService 尚未 bind")
        return self._chat

    async def asr(self, pcm_bytes: bytes, sample_rate: int) -> str:
        return await self.chat.asr(pcm_bytes, sample_rate)

    def is_valid_asr_text(self, text: str) -> bool:
        return self.chat.is_valid_asr_text(text)

    async def llm(
        self,
        text: str,
        *,
        device_context: str | None = None,
        device_id: str | None = None,
        history_messages: list[dict[str, str]] | None = None,
        extra_messages: list[dict[str, str]] | None = None,
        on_tts_ready: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        return await self.chat.llm(
            text,
            device_context=device_context,
            device_id=device_id,
            history_messages=history_messages,
            extra_messages=extra_messages,
            on_tts_ready=on_tts_ready,
        )

    async def tts_phoneme_segments(self, text: str) -> tuple[int, list]:
        return await self.chat.tts_phoneme_segments(text)

    def __getattr__(self, name: str):
        return getattr(self.chat, name)
