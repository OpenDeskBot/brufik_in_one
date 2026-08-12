"""自动应答状态：重导出 ``dao.auto_reply_store``，供 service 层使用。"""

from deskbot_server.dao.auto_reply_store import (
    get_asr_voice_auto_reply_enabled,
    set_asr_voice_auto_reply_enabled,
)

__all__ = ["get_asr_voice_auto_reply_enabled", "set_asr_voice_auto_reply_enabled"]
