"""向后兼容：BotPipeline 现为 ChatService 别名。"""

from __future__ import annotations

from deskbot_server.application.chat_service import ChatService

BotPipeline = ChatService

__all__ = ["BotPipeline", "ChatService"]
