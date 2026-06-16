from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

from deskbot_server.core.settings import AppSettings
from deskbot_server.llm.runtime import litellm_completion
from deskbot_server.llm.user_message import build_llm_user_message
from deskbot_server.llm.utils import (
    llm_device_screen_appendix,
    llm_pb_scenes_prompt_appendix,
    llm_static_context_prompt_appendix,
)

logger = logging.getLogger("deskbot-server")


class OpenAiLlmAdapter:
    """LiteLLM 适配器：支持设备级模型配置，未设置时回退系统默认。"""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._default_system_prompt = settings.llm.system_prompt or (
            '你是中文语音助手，请简洁回答。每次只输出 JSON：{"tts":"…","servo":[]}。'
        )

    @staticmethod
    def _beijing_time_str() -> str:
        if ZoneInfo is not None:
            now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
        else:
            now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return now.strftime("%Y-%m-%d %H:%M:%S") + " " + weekdays[now.weekday()]

    def _resolve_system_prompt(self, *, device_id: Optional[str] = None) -> str:
        from deskbot_server.device_data import load_llm_system_prompt

        return load_llm_system_prompt(device_id) or self._default_system_prompt

    def _build_system_prompt(
        self,
        *,
        device_id: Optional[str] = None,
    ) -> str:
        base = f"{self._resolve_system_prompt(device_id=device_id)}\n当前时间是: {self._beijing_time_str()}（北京时间，东八区）"
        base += "\n" + llm_device_screen_appendix(device_id)
        px = llm_pb_scenes_prompt_appendix(device_id=device_id)
        if px:
            base += "\n" + px
        fx = llm_static_context_prompt_appendix(device_id)
        if fx:
            base += "\n\n" + fx
        return base

    async def complete(
        self,
        user_text: str,
        *,
        device_context: Optional[str] = None,
        device_id: Optional[str] = None,
        history_messages: Optional[list[dict[str, str]]] = None,
        extra_messages: Optional[list[dict[str, str]]] = None,
    ) -> str:
        system_content = self._build_system_prompt(device_id=device_id)
        user_content = build_llm_user_message(
            user_text,
            device_id=device_id,
            device_context=device_context,
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
        ]
        if history_messages:
            messages.extend(history_messages)
        messages.append({"role": "user", "content": user_content})
        if extra_messages:
            messages.extend(extra_messages)

        def _chat() -> str:
            content, _meta = litellm_completion(messages, device_id=device_id, temperature=0.7)
            return content

        return await asyncio.to_thread(_chat)
