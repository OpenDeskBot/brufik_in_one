"""LLM tool 轮过渡语：LLM 未写 ``tts`` 时按工具名兜底合并一句。"""
from __future__ import annotations

from typing import Any

# tool 名 → 短句（合并时去重保序）
_TOOL_INTERIM_PHRASES: dict[str, str] = {
    "websearch": "我帮你搜一下",
    "webfetch": "我打开看看",
    "capture_camera": "我看一下",
    "get_camera_frame": "我看一下",
    "camera_capture": "我看一下",
    "schedule_task": "我记下了",
    "scheduled_task": "我记下了",
    "memory_add": "我记住了",
    "memory_delete": "好，我删掉这条记忆",
    "register_face": "我记住你的样子了",
    "set_camera_follow": "我转过来",
    "set_camera_follow_mode": "我转过来",
    "camera_follow": "我转过来",
    "session": "我换一下话题",
    "read": "我读一下文件",
    "write": "我写进文件里",
    "miot": "我帮你控一下设备",
    "mihome": "我帮你控一下设备",
    "mijia": "我帮你控一下设备",
}

_DEFAULT_PHRASE = "稍等一下"


def _tool_name(raw: dict[str, Any]) -> str:
    return str(raw.get("tool") or raw.get("name") or "").strip().lower()


def phrase_for_tool(tool: str) -> str:
    key = (tool or "").strip().lower()
    if not key:
        return _DEFAULT_PHRASE
    return _TOOL_INTERIM_PHRASES.get(key, _DEFAULT_PHRASE)


def build_tool_interim_tts(tools: list[dict[str, Any]]) -> str:
    """多个 tool 合并为一句口语过渡语，供 TTS 播报。"""
    if not tools:
        return ""
    seen: set[str] = set()
    parts: list[str] = []
    for raw in tools:
        if not isinstance(raw, dict):
            continue
        name = _tool_name(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        parts.append(phrase_for_tool(name))
    if not parts:
        return "稍等一下。"
    if len(parts) == 1:
        return f"稍等，{parts[0]}。"
    body = "，".join(parts[:-1]) + "，" + parts[-1]
    return f"稍等，{body}。"
