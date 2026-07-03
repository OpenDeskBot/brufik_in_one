"""pb 下行 wire 组帧：音素分片 → anim → JSON+binary 对。"""

from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from typing import Any, Optional

from deskbot_server.constants import PB_MAX_WIRE_JSON_BYTES

from deskbot_server.pb.face_bundle import resolve_pb_face_bundle
from deskbot_server.pb.llm_plan import (
    build_anim_rows_for_llm_plan,
    expand_llm_anims,
    expand_llm_moves,
    interleave_tts_segs_with_llm_plan,
)
from deskbot_server.pb.llm_display import apply_llm_display_to_rows
from deskbot_server.pb.phoneme_anim import phoneme_seq_to_anim_seq
from deskbot_server.pb.servo_pcm import (
    PB_CHUNK_MS_MAX,
    PB_ACTION_REPLACE,
    align_pcm_s16le_mono_to_chunk_ms,
    apply_parallel_pb_servo,
    apply_random_pb_servo_actions,
    interleave_tts_phoneme_segs_with_servo_plan,
    merge_pb_subchunks,
    pb_json_messages,
    resolve_pb_device_hints,
)

logger = logging.getLogger("deskbot-server")

def pb_wire_json_bytes(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )


def compact_pb_wire_payload(
    msg: dict[str, Any], *, max_bytes: int | None = None
) -> dict[str, Any]:
    """设备 pb 下行：保留完整 ``anim[]`` 图元，不做裁剪（仅用于日志与测试）。"""
    out = copy.deepcopy(msg)
    limit = PB_MAX_WIRE_JSON_BYTES if max_bytes is None else max_bytes
    sz = pb_wire_json_bytes(out)
    if sz > limit:
        logger.warning(
            "[pb TX] wire JSON %d bytes 超过参考上限 %d（未裁剪 anim；请确认固件 WS TEXT 缓冲）",
            sz,
            limit,
        )
    return out


def device_pb_json_msg(payload: dict[str, Any]) -> str:
    """设备 pb 下行：完整 anim + 紧凑 JSON + ``t_mono``。"""
    p = compact_pb_wire_payload(payload)
    p.setdefault("t_mono", time.monotonic())
    return json.dumps(p, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "align_pcm_s16le_mono_to_chunk_ms",
    "apply_parallel_pb_servo",
    "apply_random_pb_servo_actions",
    "build_pb_wire_pairs",
    "compact_pb_wire_payload",
    "device_pb_json_msg",
    "interleave_tts_phoneme_segs_with_servo_plan",
    "pb_wire_json_bytes",
    "merge_pb_subchunks",
    "pb_json_messages",
    "phoneme_seq_to_anim_seq",
    "resolve_pb_face_bundle",
]


def build_pb_wire_pairs(
    segs: list[dict[str, Any]],
    tts_cfg: dict[str, Any],
    *,
    servo_plan: list[dict[str, Any]] | None = None,
    moves: list[dict[str, Any]] | None = None,
    anims: list[dict[str, Any]] | None = None,
    sample_rate: int,
    request_id: Optional[str] = None,
    random_servo_cfg: Optional[dict[str, Any]] = None,
    volume: int | None = None,
    device_id: Optional[str] = None,
    screen_text: str | None = None,
    screen_text_color: Any = None,
    images: list[dict[str, Any]] | None = None,
    action: str = PB_ACTION_REPLACE,
) -> tuple[list[tuple[dict[str, Any], list[bytes]]], str, int, int]:
    """音素 TTS 分片 → pb wire (msg, binaries) 列表。"""
    face_bundle = resolve_pb_face_bundle(tts_cfg, device_id=device_id)
    move_steps = expand_llm_moves(moves, device_id=device_id)
    anim_frames = expand_llm_anims(anims, device_id=device_id)
    parallel_anim: list[dict[str, Any] | None] | None = None

    if move_steps or anim_frames:
        segs, parallel_servo, parallel_anim = interleave_tts_segs_with_llm_plan(
            segs, move_steps, anim_frames, sample_rate
        )
        logger.info(
            "[pb TX] LLM moves/anims 交错后 segments=%d（move_steps=%d anim_frames=%d）",
            len(segs),
            len(move_steps),
            len(anim_frames),
        )
    else:
        segs, parallel_servo = interleave_tts_phoneme_segs_with_servo_plan(
            segs, servo_plan, sample_rate
        )
        logger.info(
            "[pb TX] 音素分片与 servo 计划交错后 segments=%d（含 hold/补静音承载的多余舵机）",
            len(segs),
        )

    if parallel_anim is not None:
        anim_rows = build_anim_rows_for_llm_plan(
            segs, parallel_anim, face_bundle, device_id=device_id
        )
    else:
        anim_rows = phoneme_seq_to_anim_seq(segs, face_bundle, device_id=device_id)
    pcm_list: list[bytes] = []
    for i, s in enumerate(segs):
        raw = bytes(s.get("pcm") or b"")
        cms = int(anim_rows[i].get("chunk_ms") or s.get("ms") or 0)
        aligned, cms2 = align_pcm_s16le_mono_to_chunk_ms(raw, cms, sample_rate)
        if cms2 != cms:
            anim_rows[i]["chunk_ms"] = cms2
        pcm_list.append(aligned)

    n_llm_servo = apply_parallel_pb_servo(anim_rows, parallel_servo)
    if n_llm_servo:
        logger.info(
            "[pb TX] 已将 %d 条分片附上舵机/hold（parallel 与交错后分片对齐）",
            n_llm_servo,
        )

    merged_rows, merged_pcm = merge_pb_subchunks(
        anim_rows, pcm_list, sample_rate=sample_rate
    )
    apply_llm_display_to_rows(
        merged_rows,
        screen_text=screen_text,
        screen_text_color=screen_text_color,
        images=images,
    )
    logger.info(
        "[pb TX] 分片合并 %d → %d（单包 chunk_ms 上限 %d ms）",
        len(anim_rows),
        len(merged_rows),
        PB_CHUNK_MS_MAX,
    )

    pb_req = request_id or uuid.uuid4().hex[:16]
    pb_vol = resolve_pb_device_hints(tts_cfg, volume=volume, device_id=device_id)
    output_fmt = str(tts_cfg.get("output_codec") or "s16le").lower()
    audio_blobs: list[bytes] = list(merged_pcm)
    opus_frames: list[int] | None = None
    if output_fmt == "opus":
        from deskbot_server.pipeline.opus_downlink import encode_pcm_s16le_to_opus_batch

        audio_blobs = []
        opus_frames = []
        for pcm in merged_pcm:
            blob, nf = encode_pcm_s16le_to_opus_batch(pcm, sample_rate)
            audio_blobs.append(blob)
            opus_frames.append(nf)
        wire_fmt = "opus"
    else:
        wire_fmt = "s16le"
    pairs = pb_json_messages(
        pb_req=pb_req,
        sample_rate=sample_rate,
        fmt=wire_fmt,
        channels=1,
        anim_rows=merged_rows,
        pcm_per_idx=audio_blobs,
        opus_frames_per_idx=opus_frames,
        volume=pb_vol,
        action=action,
    )
    if random_servo_cfg:
        n_ra = apply_random_pb_servo_actions(pairs, random_servo_cfg)
        if n_ra:
            logger.info("[pb TX] 随机舵机动作：%d 片附加 servo", n_ra)

    return pairs, pb_req, len(pairs), sample_rate
