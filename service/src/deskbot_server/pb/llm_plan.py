"""LLM ``moves`` / ``anims`` 计划：预设加载、时长缩放与 TTS 分片交错。"""

from __future__ import annotations

import copy
import logging
from collections import deque
from typing import Any, Optional

from deskbot_server.face_expr_scenes_store import (
    _extract_frame_elements,
    find_design_scene_by_name,
    load_face_expr_scenes_file,
)
from deskbot_server.pb.phoneme_anim import phoneme_seq_to_anim_seq
from deskbot_server.pb.shapes import apply_anim_bg_color_elements
from deskbot_server.pb.servo_pcm import (
    _silence_phoneme_seg,
    anim_elements_from_row,
    make_anim_item,
)
from deskbot_server.servo_config_store import (
    clamp_servo_step,
    load_servo_cfg_file,
    resolve_move_for_perspective,
)

logger = logging.getLogger("deskbot-server")

_FRAME_MS_MIN = 40
_FRAME_MS_MAX = 30_000


def _scale_ms_values(raw_ms: list[int], target_ms: int) -> list[int]:
    """按 ``target_ms / sum(raw_ms)`` 比例缩放各段时长，总和精确等于 ``target_ms``。"""
    n = len(raw_ms)
    if n == 0:
        return []
    target_ms = max(n, min(_FRAME_MS_MAX * n, int(target_ms)))
    weights = [max(1, int(m)) for m in raw_ms]
    total = sum(weights)
    if total <= 0:
        base, rem = divmod(target_ms, n)
        return [base + (1 if i < rem else 0) for i in range(n)]

    exact = [w * target_ms / total for w in weights]
    scaled = [int(x) for x in exact]
    rem = target_ms - sum(scaled)
    order = sorted(range(n), key=lambda i: (exact[i] - scaled[i], i), reverse=True)
    for k in range(abs(rem)):
        idx = order[k % n]
        scaled[idx] += 1 if rem > 0 else -1

    for i in range(n):
        if scaled[i] < _FRAME_MS_MIN:
            need = _FRAME_MS_MIN - scaled[i]
            scaled[i] = _FRAME_MS_MIN
            donor = max(range(n), key=lambda j: scaled[j])
            if donor != i and scaled[donor] > _FRAME_MS_MIN:
                take = min(need, scaled[donor] - _FRAME_MS_MIN)
                scaled[donor] -= take
                need -= take
            if need > 0 and i < n - 1:
                scaled[-1] = max(_FRAME_MS_MIN, scaled[-1] - need)

    diff = target_ms - sum(scaled)
    if diff and scaled:
        j = max(range(n), key=lambda k: scaled[k])
        scaled[j] = max(_FRAME_MS_MIN, min(_FRAME_MS_MAX, scaled[j] + diff))
    return scaled


def _resolve_servo_preset_steps(
    preset_id: str, *, device_id: Optional[str] = None
) -> list[dict[str, Any]]:
    want = str(preset_id or "").strip()
    if not want:
        return []
    try:
        cfg = load_servo_cfg_file(device_id=device_id)
    except (OSError, ValueError):
        return []
    if not cfg:
        return []
    for preset in cfg.get("presets") or []:
        if not isinstance(preset, dict):
            continue
        if str(preset.get("id") or "").strip().lower() == want.lower():
            steps = preset.get("steps")
            if isinstance(steps, list) and steps:
                return copy.deepcopy(steps)
    return []


def preset_default_ms(preset_id: str, *, device_id: Optional[str] = None) -> int:
    """预设动作默认总时长（各 step ``ms`` 之和）。"""
    steps = _resolve_servo_preset_steps(preset_id, device_id=device_id)
    return sum(max(1, int(s.get("ms") or 0)) for s in steps)


def resolve_anim_scene_frames(
    anim_name: str, *, device_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """加载表情场景帧；未找到时回退 ``name=default``。"""
    rows = load_face_expr_scenes_file(seed_if_missing=True, device_id=device_id) or []
    ent = find_design_scene_by_name(rows, anim_name)
    if ent is None and str(anim_name or "").strip().lower() != "default":
        ent = find_design_scene_by_name(rows, "default")
    if ent is None:
        return []
    frames = ent.get("frames")
    if not isinstance(frames, list) or not frames:
        return []
    return copy.deepcopy(frames)


def anim_default_ms(anim_name: str, *, device_id: Optional[str] = None) -> int:
    """动画场景默认总时长（各 frame ``ms`` 之和）。"""
    frames = resolve_anim_scene_frames(anim_name, device_id=device_id)
    return sum(max(1, int(fr.get("ms") or 0)) for fr in frames)


def expand_llm_moves(
    moves: list[dict[str, Any]] | None, *, device_id: Optional[str] = None
) -> list[dict[str, int]]:
    """将 ``[{move, ms}, ...]`` 展开为缩放后的舵机 step 列表。"""
    out: list[dict[str, int]] = []
    for item in moves or []:
        if not isinstance(item, dict):
            continue
        move_id = resolve_move_for_perspective(
            str(item.get("move") or "").strip(),
            device_id=device_id,
        )
        try:
            target_ms = int(item.get("ms", 0))
        except (TypeError, ValueError):
            continue
        if not move_id or target_ms <= 0:
            continue
        if move_id == "__custom__":
            try:
                out.append(
                    clamp_servo_step(
                        {
                            "xm": int(item.get("xm", 0)),
                            "ym": int(item.get("ym", 0)),
                            "x": int(item.get("x", 90)),
                            "y": int(item.get("y", 90)),
                            "ms": int(target_ms),
                        },
                        device_id=device_id,
                    )
                )
            except (TypeError, ValueError):
                continue
            continue
        steps = _resolve_servo_preset_steps(move_id, device_id=device_id)
        if not steps:
            logger.warning("[pb] LLM move 未找到预设 %r", move_id)
            continue
        raw_ms = [max(1, int(s.get("ms") or 400)) for s in steps]
        scaled = _scale_ms_values(raw_ms, target_ms)
        for step, sms in zip(steps, scaled):
            try:
                out.append(
                    clamp_servo_step(
                        {
                            "xm": int(step.get("xm", 1)),
                            "ym": int(step.get("ym", 1)),
                            "x": int(step.get("x", 0)),
                            "y": int(step.get("y", 0)),
                            "ms": int(sms),
                        },
                        device_id=device_id,
                    )
                )
            except (TypeError, ValueError):
                continue
    return out


def expand_llm_anims(
    anims: list[dict[str, Any]] | None, *, device_id: Optional[str] = None
) -> list[dict[str, Any]]:
    """将 ``[{anim, ms}, ...]`` 展开为缩放后的 ``{ms, elements}`` 帧列表。"""
    out: list[dict[str, Any]] = []
    for item in anims or []:
        if not isinstance(item, dict):
            continue
        anim_name = str(item.get("anim") or "").strip()
        try:
            target_ms = int(item.get("ms", 0))
        except (TypeError, ValueError):
            continue
        if not anim_name or target_ms <= 0:
            continue
        frames = resolve_anim_scene_frames(anim_name, device_id=device_id)
        if not frames:
            logger.warning("[pb] LLM anim 未找到场景 %r（default 亦不可用）", anim_name)
            continue
        raw_ms = [max(1, int(fr.get("ms") or 500)) for fr in frames]
        scaled = _scale_ms_values(raw_ms, target_ms)
        for fr, sms in zip(frames, scaled):
            try:
                elements = _extract_frame_elements(fr if isinstance(fr, dict) else {})
            except ValueError:
                continue
            elements = apply_anim_bg_color_elements(
                elements, bg=item.get("bg"), color=item.get("color")
            )
            out.append({"ms": int(sms), "elements": elements})
    return out


def map_anim_frames_to_tts_segs(
    segs: list[dict[str, Any]],
    anim_frames: list[dict[str, Any]],
) -> list[dict[str, Any] | None]:
    """按 TTS 时间轴把 ``anim_frames`` 铺到每个分片（非 1:1 索引）。

    在 ``overlay_ms = min(anim 总时长, TTS 总时长)`` 内，按各分片中点时间选取
    对应表情帧；超出部分不叠加（口型仍由音素驱动）。
    """
    n = len(segs)
    if n == 0 or not anim_frames:
        return [None] * n

    durations = [max(1, int(s.get("ms") or _FRAME_MS_MIN)) for s in segs]
    tts_total = sum(durations)
    if tts_total <= 0:
        return [None] * n

    anim_total = sum(max(1, int(f.get("ms") or _FRAME_MS_MIN)) for f in anim_frames)
    overlay_span = min(anim_total, tts_total)

    out: list[dict[str, Any] | None] = []
    cumulative = 0
    acc = 0
    frame_idx = 0
    for dur in durations:
        mid = cumulative + dur // 2
        cumulative += dur
        if mid >= overlay_span:
            out.append(None)
            continue
        while frame_idx < len(anim_frames) - 1 and acc + max(
            1, int(anim_frames[frame_idx].get("ms") or _FRAME_MS_MIN)
        ) <= mid:
            acc += max(1, int(anim_frames[frame_idx].get("ms") or _FRAME_MS_MIN))
            frame_idx += 1
        els = anim_frames[frame_idx].get("elements")
        out.append(copy.deepcopy(els) if isinstance(els, dict) else None)
    return out


def interleave_tts_segs_with_llm_plan(
    segs: list[dict[str, Any]],
    move_steps: list[dict[str, int]],
    anim_frames: list[dict[str, Any]],
    sample_rate: int,
) -> tuple[list[dict[str, Any]], list[dict[str, int] | None], list[dict[str, Any] | None]]:
    """TTS 分片与 move step 按索引交错；``anims`` 按时间轴铺到全部分片。

    TTS 分片保持音素原始 ``ms``（不随 anim/move 拉长）；仅在没有 TTS 剩余时
    才追加静音片，时长取待消费的 move ``ms``。

    返回 ``(out_segs, parallel_servo, parallel_anim_elements)``。
    """
    if not segs and not move_steps and not anim_frames:
        return [], [], []

    pq: deque[dict[str, Any]] = deque(copy.deepcopy(s) for s in (segs or []))
    out_segs: list[dict[str, Any]] = []
    parallel_servo: list[dict[str, int] | None] = []
    mi = 0

    while pq or mi < len(move_steps):
        if pq:
            seg = pq.popleft()
        else:
            ms = _FRAME_MS_MIN
            if mi < len(move_steps):
                ms = max(ms, int(move_steps[mi].get("ms", _FRAME_MS_MIN)))
            seg = _silence_phoneme_seg(ms, sample_rate)

        servo_cmd: Optional[dict[str, int]] = None
        if mi < len(move_steps):
            servo_cmd = move_steps[mi]

        out_segs.append(seg)
        parallel_servo.append(servo_cmd)
        if mi < len(move_steps):
            mi += 1

    parallel_anim = map_anim_frames_to_tts_segs(out_segs, anim_frames)
    return out_segs, parallel_servo, parallel_anim


def merge_llm_plan_anim_rows(
    segs: list[dict[str, Any]],
    phoneme_rows: list[dict[str, Any]],
    parallel_anim: list[dict[str, Any] | None] | None,
) -> list[dict[str, Any]]:
    """合并 LLM 指定 anim 与音素口型：有真实音素口播时保留音素 ``mouth``。"""
    out: list[dict[str, Any]] = []
    for i, ph_row in enumerate(phoneme_rows):
        row = copy.deepcopy(ph_row)
        seg = segs[i] if i < len(segs) else {}
        has_audio = bool(bytes(seg.get("pcm") or b""))
        plan_el = (parallel_anim or [None] * len(phoneme_rows))[i] if parallel_anim else None
        if isinstance(plan_el, dict) and plan_el:
            merged = copy.deepcopy(plan_el)
            ph_el = anim_elements_from_row(ph_row)
            chunk_ms = int(ph_row.get("chunk_ms") or seg.get("ms") or 1)
            ph_name = str(seg.get("phoneme") or "").strip()
            if not ph_name:
                anim_list = ph_row.get("anim")
                if isinstance(anim_list, list) and anim_list:
                    ph_name = str(anim_list[0].get("phoneme") or "").strip()
            # 纯表情/舵机包的静音 PCM 不应覆盖情绪口型；仅 TTS 音素口播时保留嘴型
            if has_audio and ph_name and isinstance(ph_el.get("mouth"), list):
                merged["mouth"] = copy.deepcopy(ph_el["mouth"])
            row["anim"] = [
                make_anim_item(merged, chunk_ms, phoneme=ph_name or None)
            ]
        out.append(row)
    return out


def build_anim_rows_for_llm_plan(
    segs: list[dict[str, Any]],
    parallel_anim: list[dict[str, Any] | None] | None,
    face_bundle: dict[str, Any],
    *,
    device_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    phoneme_rows = phoneme_seq_to_anim_seq(segs, face_bundle, device_id=device_id)
    return merge_llm_plan_anim_rows(segs, phoneme_rows, parallel_anim)
