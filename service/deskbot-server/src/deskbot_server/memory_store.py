"""用户长期记忆（``data/user_memory.json`` 或 ``data/device/{id}/user_memory.json``），注入 LLM system prompt。"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Optional

from deskbot_server.constants import USER_MEMORY_FILE
from deskbot_server.device_data import resolve_json_path

_MAX_ENTRIES = 200
_MAX_PROMPT_ENTRIES = 30


def _normalize_entry(raw: object, *, device_id: str = "") -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("entry must be object")
    text = str(raw.get("text") or raw.get("value") or "").strip()
    if not text:
        raise ValueError("text required")
    entry_id = str(raw.get("id") or "").strip() or uuid.uuid4().hex[:12]
    dev = str(raw.get("device_id") if raw.get("device_id") is not None else device_id).strip()
    created = raw.get("created_at")
    try:
        created_at = float(created) if created is not None else time.time()
    except (TypeError, ValueError):
        created_at = time.time()
    return {
        "id": entry_id,
        "device_id": dev,
        "text": text,
        "created_at": created_at,
    }


def load_memory_entries(*, device_id: Optional[str] = None) -> list[dict[str, Any]]:
    path = resolve_json_path(USER_MEMORY_FILE, device_id)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    items = raw.get("entries") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        try:
            out.append(_normalize_entry(item, device_id=str(device_id or "")))
        except ValueError:
            continue
    return out


def save_memory_entries(entries: list[dict[str, Any]], *, device_id: Optional[str] = None) -> None:
    norm = [_normalize_entry(e, device_id=str(device_id or "")) for e in entries]
    path = resolve_json_path(USER_MEMORY_FILE, device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"entries": norm}, f, ensure_ascii=False, indent=2)
        f.write("\n")


def list_memory_for_device(device_id: Optional[str] = None, *, limit: int = _MAX_PROMPT_ENTRIES) -> list[dict[str, Any]]:
    """设备专属记忆；有 ``device_id`` 时读设备目录文件，否则读全局并含无 device 标记项。"""
    dev = str(device_id or "").strip()
    cap = max(1, min(int(limit), _MAX_ENTRIES))
    if dev:
        rows = load_memory_entries(device_id=dev)
        rows.sort(key=lambda e: float(e.get("created_at") or 0), reverse=True)
        return rows[:cap]
    rows = load_memory_entries()
    matched = [
        e
        for e in rows
        if not str(e.get("device_id") or "").strip()
    ]
    matched.sort(key=lambda e: float(e.get("created_at") or 0), reverse=True)
    return matched[:cap]


def add_memory(text: str, *, device_id: Optional[str] = None) -> dict[str, Any]:
    dev = str(device_id or "").strip()
    entries = load_memory_entries(device_id=dev or None)
    entry = _normalize_entry({"text": text, "device_id": dev})
    entries.append(entry)
    if len(entries) > _MAX_ENTRIES:
        entries = sorted(entries, key=lambda e: float(e.get("created_at") or 0))[-_MAX_ENTRIES:]
    save_memory_entries(entries, device_id=dev or None)
    return entry


def get_memory(entry_id: str, *, device_id: Optional[str] = None) -> dict[str, Any] | None:
    eid = str(entry_id or "").strip()
    if not eid:
        return None
    dev = str(device_id or "").strip()
    for entry in load_memory_entries(device_id=dev or None):
        if str(entry.get("id") or "") == eid:
            return dict(entry)
    return None


def update_memory(entry_id: str, text: str, *, device_id: Optional[str] = None) -> dict[str, Any] | None:
    eid = str(entry_id or "").strip()
    new_text = str(text or "").strip()
    if not eid or not new_text:
        raise ValueError("id 与 text 不能为空")
    dev = str(device_id or "").strip()
    entries = load_memory_entries(device_id=dev or None)
    found = False
    updated: dict[str, Any] | None = None
    for entry in entries:
        if str(entry.get("id") or "") == eid:
            entry["text"] = new_text
            updated = dict(entry)
            found = True
            break
    if not found or updated is None:
        return None
    save_memory_entries(entries, device_id=dev or None)
    return updated


def delete_memory(entry_id: str, *, device_id: Optional[str] = None) -> bool:
    eid = str(entry_id or "").strip()
    if not eid:
        return False
    dev = str(device_id or "").strip()
    entries = load_memory_entries(device_id=dev or None)
    kept = [e for e in entries if str(e.get("id") or "") != eid]
    if len(kept) == len(entries):
        return False
    save_memory_entries(kept, device_id=dev or None)
    return True


def list_memory_entries_for_device(device_id: str, *, limit: int = _MAX_ENTRIES) -> list[dict[str, Any]]:
    """设备记忆列表（按创建时间倒序）。"""
    dev = str(device_id or "").strip()
    if not dev:
        return []
    rows = load_memory_entries(device_id=dev)
    rows.sort(key=lambda e: float(e.get("created_at") or 0), reverse=True)
    cap = max(1, min(int(limit), _MAX_ENTRIES))
    return rows[:cap]
