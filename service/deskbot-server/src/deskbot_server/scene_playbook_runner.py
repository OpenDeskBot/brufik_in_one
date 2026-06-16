"""场景编排：转为 LLM moves/anims 计划并与 TTS 交错下发。"""
from __future__ import annotations

from typing import Any

from deskbot_server.pb.llm_plan import expand_llm_anims, expand_llm_moves
from deskbot_server.scene_playbooks_store import normalize_playbook


def playbook_collect_text(playbook: dict[str, Any]) -> str:
    parts: list[str] = []
    top = str(playbook.get("text") or "").strip()
    if top:
        parts.append(top)
    for clip in playbook.get("text_track") or []:
        if not isinstance(clip, dict):
            continue
        t = str(clip.get("text") or "").strip()
        if t:
            parts.append(t)
    return "".join(parts)


def playbook_to_llm_plan(playbook: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """将编排转为 ``(text, moves, anims)``，供 :func:`build_pb_wire_pairs` 交错。"""
    pb = normalize_playbook(playbook)
    text = playbook_collect_text(pb)

    moves: list[dict[str, Any]] = []
    for clip in pb.get("servo_track") or []:
        if not isinstance(clip, dict):
            continue
        ms = max(40, min(120_000, int(clip.get("ms") or 500)))
        preset = str(clip.get("preset") or "").strip()
        if preset:
            moves.append({"move": preset, "ms": ms})
            continue
        moves.append({
            "move": "__custom__",
            "ms": ms,
            "x": int(clip.get("x", 90)),
            "y": int(clip.get("y", 90)),
            "xm": 0 if int(clip.get("xm", 0)) == 0 else 1,
            "ym": 0 if int(clip.get("ym", 0)) == 0 else 1,
        })

    anims: list[dict[str, Any]] = []
    for clip in pb.get("expr_track") or []:
        if not isinstance(clip, dict):
            continue
        scene = str(clip.get("scene") or "").strip()
        if not scene:
            continue
        ms = max(40, min(120_000, int(clip.get("ms") or 500)))
        anims.append({"anim": scene, "ms": ms})

    return text, moves, anims


def playbook_expand_move_steps(moves: list[dict[str, Any]]) -> list[dict[str, int]]:
    """按编排轨顺序展开舵机步（预设可含多 step）。"""
    out: list[dict[str, int]] = []
    for item in moves or []:
        if not isinstance(item, dict):
            continue
        move_id = str(item.get("move") or "").strip()
        ms = max(40, int(item.get("ms") or 500))
        if move_id == "__custom__":
            out.append({
                "xm": int(item.get("xm", 0)),
                "ym": int(item.get("ym", 0)),
                "x": int(item.get("x", 90)),
                "y": int(item.get("y", 90)),
                "ms": ms,
            })
        elif move_id:
            out.extend(expand_llm_moves([{"move": move_id, "ms": ms}]))
    return out


def playbook_expand_anim_frames(anims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return expand_llm_anims(anims)
