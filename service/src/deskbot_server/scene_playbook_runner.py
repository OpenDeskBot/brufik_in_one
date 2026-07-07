"""场景编排：``chunks[]`` → 分阶段 PB 下发（每 chunk 对应一轮 pb）。"""
from __future__ import annotations

from typing import Any, Optional

from deskbot_server.pb.llm_plan import expand_llm_anims, expand_llm_moves
from deskbot_server.scene_playbooks_store import normalize_playbook


def playbook_collect_text(playbook: dict[str, Any]) -> str:
    parts: list[str] = []
    for chunk in playbook.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        t = str(chunk.get("text") or "").strip()
        if t:
            parts.append(t)
    return "".join(parts)


def _chunk_to_move(servo: dict[str, Any]) -> dict[str, Any]:
    ms = int(servo.get("ms") or 500)
    preset = str(servo.get("preset") or "").strip()
    if preset:
        return {"move": preset, "ms": ms}
    return {
        "move": "__custom__",
        "ms": ms,
        "x": int(servo.get("x", 90)),
        "y": int(servo.get("y", 90)),
        "xm": 0 if int(servo.get("xm", 0)) == 0 else 1,
        "ym": 0 if int(servo.get("ym", 0)) == 0 else 1,
    }


def _chunk_to_anim(expr: dict[str, Any]) -> dict[str, Any]:
    return {
        "anim": str(expr.get("scene") or "").strip(),
        "ms": int(expr.get("ms") or 500),
    }


def _chunk_to_phase(chunk: dict[str, Any]) -> dict[str, Any] | None:
    text = str(chunk.get("text") or "").strip()
    moves: list[dict[str, Any]] = []
    anims: list[dict[str, Any]] = []
    servo = chunk.get("servo")
    if isinstance(servo, dict):
        moves.append(_chunk_to_move(servo))
    expr = chunk.get("expr")
    if isinstance(expr, dict) and str(expr.get("scene") or "").strip():
        anims.append(_chunk_to_anim(expr))
    if not text and not moves and not anims:
        return None
    if text:
        return {
            "kind": "speech",
            "text": text,
            "moves": moves,
            "anims": anims,
            "leading_move_steps": 0,
            "chunk_id": chunk.get("id"),
        }
    return {
        "kind": "motion",
        "text": "",
        "moves": moves,
        "anims": anims,
        "leading_move_steps": 0,
        "chunk_id": chunk.get("id"),
    }


def playbook_to_phases(
    playbook: dict[str, Any],
    *,
    device_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """每个 ``chunks[]`` 条目 → 一轮 PB（口播 / 纯表情 / 纯舵机 / 组合）。"""
    pb = normalize_playbook(playbook)
    phases: list[dict[str, Any]] = []
    for chunk in pb.get("chunks") or []:
        if not isinstance(chunk, dict):
            continue
        phase = _chunk_to_phase(chunk)
        if phase:
            phases.append(phase)
    return phases


def playbook_to_llm_plan(
    playbook: dict[str, Any],
    *,
    device_id: Optional[str] = None,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], int]:
    """兼容旧接口：合并为单条 TTS 计划（多 chunk 时仅取首段口播）。"""
    phases = playbook_to_phases(playbook, device_id=device_id)
    if not phases:
        return "", [], [], 0
    text_parts: list[str] = []
    moves: list[dict[str, Any]] = []
    anims: list[dict[str, Any]] = []
    leading = 0
    for p in phases:
        t = str(p.get("text") or "").strip()
        if t:
            text_parts.append(t)
        moves.extend(p.get("moves") or [])
        anims.extend(p.get("anims") or [])
    text = "".join(text_parts)
    if not text.strip() and (moves or anims):
        text = "。"
    return text, moves, anims, leading


def playbook_debug_snapshot(
    playbook: dict[str, Any],
    *,
    device_id: Optional[str] = None,
) -> dict[str, Any]:
    pb = normalize_playbook(playbook)
    phases = playbook_to_phases(pb, device_id=device_id)
    text = playbook_collect_text(pb)
    moves: list[dict[str, Any]] = []
    anims: list[dict[str, Any]] = []
    for p in phases:
        moves.extend(p.get("moves") or [])
        anims.extend(p.get("anims") or [])
    move_steps = expand_llm_moves(moves, device_id=device_id)
    return {
        "playbook": pb,
        "text": text,
        "phases": phases,
        "moves": moves,
        "anims": anims,
        "leading_move_steps": 0,
        "move_steps_expanded": move_steps,
    }


def playbook_expand_move_steps(moves: list[dict[str, Any]]) -> list[dict[str, int]]:
    out: list[dict[str, int]] = []
    for item in moves or []:
        if not isinstance(item, dict):
            continue
        move_id = str(item.get("move") or "").strip()
        ms = max(40, int(item.get("ms") or 500))
        if move_id == "__custom__":
            out.append(
                {
                    "xm": int(item.get("xm", 0)),
                    "ym": int(item.get("ym", 0)),
                    "x": int(item.get("x", 90)),
                    "y": int(item.get("y", 90)),
                    "ms": ms,
                }
            )
        elif move_id:
            out.extend(expand_llm_moves([{"move": move_id, "ms": ms}]))
    return out


def playbook_expand_anim_frames(anims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return expand_llm_anims(anims)
