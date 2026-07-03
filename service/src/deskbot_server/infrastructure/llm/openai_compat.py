from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

from deskbot_server.core.settings import AppSettings
from deskbot_server.llm.runtime import chat_acompletion
from deskbot_server.llm.user_message import build_llm_user_message
from deskbot_server.llm.utils import (
    llm_device_screen_appendix,
    llm_pb_scenes_prompt_appendix,
    llm_static_context_prompt_appendix,
    parse_llm_reply,
)

logger = logging.getLogger("deskbot-server")


class OpenAiLlmAdapter:
    """OpenAI-compatible 适配器：支持设备级模型配置，未设置时回退系统默认。"""

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
        on_tts_ready: Optional[Callable[[str], Awaitable[None]]] = None,
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

        async def _chat(
            msgs: list[dict[str, str]],
            *,
            json_mode: bool = True,
            stream_tts: bool = False,
        ) -> str:
            content, _meta = await chat_acompletion(
                msgs,
                device_id=device_id,
                temperature=0.7,
                json_mode=json_mode,
                stream=stream_tts,
                on_tts_ready=on_tts_ready if stream_tts else None,
            )
            return content

        answer = await _chat(messages, stream_tts=on_tts_ready is not None)
        parsed = parse_llm_reply(answer)
        if not parsed.get("json_ok"):
            logger.warning(
                "[LLM] 首轮输出非 JSON，重试 device_id=%s preview=%r",
                device_id,
                (answer or "")[:120],
            )
            retry_messages = list(messages)
            retry_messages.append({"role": "assistant", "content": answer})
            retry_messages.append(
                {
                    "role": "user",
                    "content": (
                        "上轮输出不是合法 JSON。请仅输出一个 JSON 对象（不要 markdown 代码围栏、不要解释），"
                        '格式含 need_reply、tts、moves、anims、tools 等字段。'
                    ),
                }
            )
            answer = await _chat(retry_messages, stream_tts=on_tts_ready is not None)
            parsed = parse_llm_reply(answer)
        elif parsed.get("tools") and not (parsed.get("reply") or "").strip():
            logger.warning(
                "[LLM] 仅有 tools 无 tts，重试 device_id=%s tools=%s",
                device_id,
                parsed.get("tools"),
            )
            retry_messages = list(messages)
            retry_messages.append({"role": "assistant", "content": answer})
            retry_messages.append(
                {
                    "role": "user",
                    "content": (
                        "上轮只返回了 tools，缺少完整 JSON 对象与 tts。"
                        "请输出完整 JSON：tools 写 []，tts 写要对用户说的口语。"
                    ),
                }
            )
            answer = await _chat(retry_messages, stream_tts=on_tts_ready is not None)
        return answer
