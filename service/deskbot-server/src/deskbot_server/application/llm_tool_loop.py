"""LLM 多轮 tool-call 循环：中间轮执行 tools，末轮走 TTS/pb。"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Optional

from deskbot_server.application.llm_tool_runner import execute_llm_tools
from deskbot_server.llm.utils import parse_llm_reply

if TYPE_CHECKING:
    from deskbot_server.application.chat_service import ChatService

logger = logging.getLogger("deskbot-server")

MAX_LLM_TOOL_ROUNDS = 8

_TOOL_RESULT_STRIP_KEYS = frozenset({"jpeg_base64"})


def is_llm_tool_call(parsed: dict[str, Any]) -> bool:
    """解析结果是否仍含待执行的 tool call。"""
    return bool(parsed.get("tools"))


def _tool_result_for_llm(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    for key in _TOOL_RESULT_STRIP_KEYS:
        if key not in out:
            continue
        val = out.pop(key)
        if isinstance(val, str) and val:
            out[f"{key}_len"] = len(val)
    return out


def build_llm_tool_followup_message(tool_results: list[dict[str, Any]]) -> str:
    """工具执行后反馈给 LLM 的 user 消息。"""
    slim = [_tool_result_for_llm(r) for r in tool_results]
    payload = json.dumps(slim, ensure_ascii=False)
    return (
        "[工具执行结果]\n"
        f"{payload}\n\n"
        "请根据结果继续。若还需调用工具，请输出 JSON 且 ``tools`` 非空（``tts`` 可留空）；"
        "若已完成，请输出最终 JSON，``tools`` 写 [] 并填写 ``tts`` 等字段。"
    )


async def complete_llm_with_tool_loop(
    chat: "ChatService",
    user_text: str,
    *,
    device_id: Optional[str] = None,
    session_id: Optional[str] = None,
    device_context: Optional[str] = None,
    history_messages: Optional[list[dict[str, str]]] = None,
    request_id: Optional[str] = None,
    dp_broker: Optional[Any] = None,
    pipeline_source: Optional[str] = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], str]:
    """多轮 LLM：有 tools 则执行并继续，无 tools 则返回最终 parsed。

    返回 ``(parsed, all_tools, all_tool_results, last_raw_answer)``。
    """
    extra_messages: list[dict[str, str]] = []
    all_tools: list[dict[str, Any]] = []
    all_tool_results: list[dict[str, Any]] = []
    answer = ""
    parsed: dict[str, Any] = parse_llm_reply("")

    for round_idx in range(MAX_LLM_TOOL_ROUNDS):
        answer = await chat.llm(
            user_text,
            device_context=device_context if round_idx == 0 else None,
            device_id=device_id,
            history_messages=history_messages if round_idx == 0 else None,
            extra_messages=extra_messages or None,
        )
        parsed = parse_llm_reply(answer)
        tools = list(parsed.get("tools") or [])

        if not tools:
            break

        if not device_id:
            logger.warning(
                "[LLM] tools 无 device_id，无法执行 device_id=%s req=%s tools=%s",
                device_id,
                request_id,
                tools,
            )
            break

        tool_results = execute_llm_tools(tools, device_id=device_id, session_id=session_id)
        all_tools.extend(tools)
        all_tool_results.extend(tool_results)
        logger.info(
            "[LLM] tool round=%d device_id=%s req=%s tools=%s results=%s",
            round_idx + 1,
            device_id,
            request_id,
            tools,
            tool_results,
        )
        if dp_broker is not None and device_id and request_id:
            tool_names = [
                str(t.get("tool") or "").strip()
                for t in tools
                if str(t.get("tool") or "").strip()
            ]
            await dp_broker.publish(
                {
                    "device_id": device_id,
                    "request_id": request_id,
                    "source": pipeline_source or "asr",
                    "asr_text": user_text,
                    "stage": f"llm_tool_{round_idx + 1}",
                    "status": "running",
                    "llm_text": (
                        f"执行工具: {', '.join(tool_names)}"
                        if tool_names
                        else "执行工具"
                    ),
                }
            )
        extra_messages.append({"role": "assistant", "content": answer})
        extra_messages.append(
            {"role": "user", "content": build_llm_tool_followup_message(tool_results)}
        )
    else:
        logger.warning(
            "[LLM] tool 循环达到上限 %d device_id=%s req=%s",
            MAX_LLM_TOOL_ROUNDS,
            device_id,
            request_id,
        )

    return parsed, all_tools, all_tool_results, answer
