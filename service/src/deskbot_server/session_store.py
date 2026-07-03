"""设备对话 Session：``data/device/{device_id}/session/{session_id}.json``。"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Optional

from deskbot_server.device_data import device_data_dir

SESSION_IDLE_SECONDS = 10 * 60
_MAX_HISTORY_TURNS = 30
_MAX_TITLE_LEN = 48
_META_FILENAME = "_meta.json"


def _session_root(device_id: str) -> str:
    return str(device_data_dir(device_id) / "session")


def _session_path(device_id: str, session_id: str) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id required")
    return os.path.join(_session_root(device_id), f"{sid}.json")


def _meta_path(device_id: str) -> str:
    return os.path.join(_session_root(device_id), _META_FILENAME)


def _now_ts() -> float:
    return time.time()


def _truncate_title(text: str) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return "新对话"
    if len(raw) <= _MAX_TITLE_LEN:
        return raw
    return raw[: _MAX_TITLE_LEN - 1] + "…"


def _load_json(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return raw if isinstance(raw, dict) else None


def _save_json(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _load_meta(device_id: str) -> dict[str, Any]:
    raw = _load_json(_meta_path(device_id))
    if not raw:
        return {}
    return raw


def _save_meta(device_id: str, *, session_id: str, updated_at: float) -> None:
    _save_json(
        _meta_path(device_id),
        {
            "current_session_id": session_id,
            "current_updated_at": updated_at,
        },
    )


def _normalize_message(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    role = str(raw.get("role") or "").strip().lower()
    if role not in ("user", "assistant"):
        return None
    message = str(raw.get("message") or raw.get("content") or "").strip()
    if not message:
        return None
    ts = raw.get("ts")
    try:
        ts_f = float(ts) if ts is not None else _now_ts()
    except (TypeError, ValueError):
        ts_f = _now_ts()
    return {"role": role, "message": message, "ts": ts_f}


def load_session(device_id: str, session_id: str) -> dict[str, Any] | None:
    raw = _load_json(_session_path(device_id, session_id))
    if not raw:
        return None
    messages: list[dict[str, Any]] = []
    for item in raw.get("messages") or []:
        norm = _normalize_message(item)
        if norm:
            messages.append(norm)
    return {
        "session_id": str(raw.get("session_id") or session_id),
        "device_id": str(raw.get("device_id") or device_id),
        "title": str(raw.get("title") or "新对话"),
        "created_at": float(raw.get("created_at") or 0),
        "updated_at": float(raw.get("updated_at") or 0),
        "messages": messages,
    }


def save_session(session: dict[str, Any]) -> None:
    dev = str(session.get("device_id") or "").strip()
    sid = str(session.get("session_id") or "").strip()
    if not dev or not sid:
        raise ValueError("device_id and session_id required")
    payload = {
        "session_id": sid,
        "device_id": dev,
        "title": str(session.get("title") or "新对话"),
        "created_at": float(session.get("created_at") or _now_ts()),
        "updated_at": float(session.get("updated_at") or _now_ts()),
        "messages": list(session.get("messages") or []),
    }
    _save_json(_session_path(dev, sid), payload)
    _save_meta(dev, session_id=sid, updated_at=payload["updated_at"])


def create_session(
    device_id: str,
    *,
    title: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    ts = float(now if now is not None else _now_ts())
    session = {
        "session_id": uuid.uuid4().hex[:16],
        "device_id": str(device_id).strip(),
        "title": _truncate_title(title or ""),
        "created_at": ts,
        "updated_at": ts,
        "messages": [],
    }
    save_session(session)
    return session


def _session_is_idle(session: dict[str, Any], *, now: float) -> bool:
    updated = float(session.get("updated_at") or 0)
    if updated <= 0:
        return True
    return (now - updated) > SESSION_IDLE_SECONDS


def ensure_active_session(
    device_id: str,
    *,
    user_text: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """返回当前可用 session；距上次对话超过 10 分钟则新建。"""
    dev = str(device_id or "").strip()
    if not dev:
        raise ValueError("device_id required")
    ts = float(now if now is not None else _now_ts())
    meta = _load_meta(dev)
    current_id = str(meta.get("current_session_id") or "").strip()
    session: dict[str, Any] | None = None
    if current_id:
        session = load_session(dev, current_id)
    if session is None or _session_is_idle(session, now=ts):
        title = _truncate_title(user_text or "") if user_text else "新对话"
        return create_session(dev, title=title, now=ts)
    return session


def session_history_for_llm(
    device_id: str,
    session_id: str,
    *,
    max_turns: int = _MAX_HISTORY_TURNS,
) -> list[dict[str, str]]:
    """将已存 session 消息转为 LLM ``history_messages``（role/content）。"""
    session = load_session(device_id, session_id)
    if not session:
        return []
    rows = list(session.get("messages") or [])
    cap = max(0, int(max_turns)) * 2
    if cap > 0:
        rows = rows[-cap:]
    out: list[dict[str, str]] = []
    for row in rows:
        role = str(row.get("role") or "").strip()
        message = str(row.get("message") or "").strip()
        if role in ("user", "assistant") and message:
            out.append({"role": role, "content": message})
    return out


def append_turn(
    device_id: str,
    session_id: str,
    user_text: str,
    assistant_text: str,
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """追加一轮 user/assistant 消息并更新 session 时间戳。"""
    dev = str(device_id or "").strip()
    sid = str(session_id or "").strip()
    if not dev or not sid:
        raise ValueError("device_id and session_id required")
    session = load_session(dev, sid)
    if session is None:
        session = create_session(dev, title=_truncate_title(user_text), now=now)
        sid = session["session_id"]
    ts = float(now if now is not None else _now_ts())
    user_msg = str(user_text or "").strip()
    assistant_msg = str(assistant_text or "").strip()
    if user_msg:
        session["messages"].append({"role": "user", "message": user_msg, "ts": ts})
    if assistant_msg:
        session["messages"].append({"role": "assistant", "message": assistant_msg, "ts": ts})
    session["updated_at"] = ts
    if len(session.get("messages") or []) <= 2 and user_msg:
        session["title"] = _truncate_title(user_msg)
    save_session(session)
    return session


def list_recent_sessions(device_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    root = _session_root(device_id)
    if not os.path.isdir(root):
        return []
    rows: list[dict[str, Any]] = []
    for name in os.listdir(root):
        if not name.endswith(".json") or name == _META_FILENAME:
            continue
        sid = name[:-5]
        session = load_session(device_id, sid)
        if not session:
            continue
        rows.append(
            {
                "session_id": session["session_id"],
                "title": session.get("title") or "新对话",
                "created_at": session.get("created_at"),
                "updated_at": session.get("updated_at"),
                "message_count": len(session.get("messages") or []),
            }
        )
    rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=True)
    cap = max(1, min(int(limit), 50))
    return rows[:cap]


def get_current_session(device_id: str) -> dict[str, Any] | None:
    meta = _load_meta(device_id)
    sid = str(meta.get("current_session_id") or "").strip()
    if not sid:
        return None
    return load_session(device_id, sid)


def execute_session_tool(raw: dict[str, Any], *, device_id: str) -> dict[str, Any]:
    """LLM ``session`` 工具：查询当前与最近 session。"""
    dev = str(device_id or "").strip()
    if not dev:
        raise ValueError("session 需要 device_id")
    action = str(raw.get("action") or raw.get("op") or "current").strip().lower()
    if action in ("current", "now", "active"):
        session = get_current_session(dev)
        if session is None:
            return {"tool": "session", "action": "current", "ok": True, "session": None}
        return {
            "tool": "session",
            "action": "current",
            "ok": True,
            "session": _session_summary(session, include_messages=True),
        }
    if action in ("list", "ls", "recent"):
        limit = raw.get("limit") or raw.get("max") or 10
        sessions = list_recent_sessions(dev, limit=int(limit))
        return {
            "tool": "session",
            "action": "list",
            "ok": True,
            "sessions": sessions,
            "count": len(sessions),
        }
    sid = str(raw.get("session_id") or raw.get("id") or "").strip()
    if action in ("get", "read", "query"):
        if not sid:
            session = get_current_session(dev)
            if session is None:
                return {"tool": "session", "action": "get", "ok": True, "session": None}
            return {
                "tool": "session",
                "action": "get",
                "ok": True,
                "session": _session_summary(session, include_messages=True),
            }
        session = load_session(dev, sid)
        if session is None:
            raise ValueError(f"未找到 session id={sid}")
        return {
            "tool": "session",
            "action": "get",
            "ok": True,
            "session": _session_summary(session, include_messages=True),
        }
    raise ValueError(f"未知 session action: {action}")


def _session_summary(session: dict[str, Any], *, include_messages: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "session_id": session.get("session_id"),
        "title": session.get("title"),
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "message_count": len(session.get("messages") or []),
    }
    if include_messages:
        out["messages"] = list(session.get("messages") or [])
    return out
