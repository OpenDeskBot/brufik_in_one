"""舵机交错、PCM 对齐与 pb JSON wire 消息。"""

from __future__ import annotations

import copy
import logging
import os
import random
from collections import deque
from typing import Any

from deskbot_server.constants import pb_max_chunk_ms_for_pcm
from deskbot_server.pb.display import FACE_LCD_HEIGHT, FACE_LCD_WIDTH
from deskbot_server.pb.shapes import (
    PB_ACTION_REPLACE,
    PB_LEVEL_TASK,
    apply_pb_device_hints_to_frames,
    attach_pb_device_hints,
    normalize_anim_list_for_wire,
)

logger = logging.getLogger("deskbot-server")

# 联调：单片时长上限；受 ESP32 WS BINARY 单帧上限约束（默认 10000ms≈480KB@24kHz）
_PB_CHUNK_MS_USER = max(100, int(os.environ.get("PB_CHUNK_MS_MAX", "10000")))
PB_CHUNK_MS_MAX = min(_PB_CHUNK_MS_USER, pb_max_chunk_ms_for_pcm(24000))
if PB_CHUNK_MS_MAX < _PB_CHUNK_MS_USER:
    logger.info(
        "[pb] PB_CHUNK_MS_MAX 由 %d 收紧为 %d（ESP32 PCM 单帧上限，约 %d 字节）",
        _PB_CHUNK_MS_USER,
        PB_CHUNK_MS_MAX,
        (PB_CHUNK_MS_MAX * 24000 // 1000) * 2,
    )


def make_anim_item(
    elements: dict[str, Any],
    ms: int,
    *,
    phoneme: str | None = None,
) -> dict[str, Any]:
    """构造 ``anim[]`` 单项：``{ elements, ms, phoneme? }``。"""
    item: dict[str, Any] = {
        "elements": copy.deepcopy(elements),
        "ms": max(1, int(ms)),
    }
    ph = str(phoneme or "").strip()
    if ph:
        item["phoneme"] = ph
    return item


def anim_elements_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """从 anim 行读取 ``elements``（兼容旧 ``anim: {elements}``）。"""
    anim = row.get("anim")
    if isinstance(anim, list):
        for one in anim:
            if isinstance(one, dict) and isinstance(one.get("elements"), dict):
                return one["elements"]
    if isinstance(anim, dict) and isinstance(anim.get("elements"), dict):
        return anim["elements"]
    return {}


def normalize_row_anim_list(row: dict[str, Any]) -> list[dict[str, Any]]:
    """保证 ``row['anim']`` 为 ``[{elements, ms, phoneme?}, ...]``。"""
    anim = row.get("anim")
    chunk_ms = max(1, int(row.get("chunk_ms") or 0))
    if isinstance(anim, list):
        out: list[dict[str, Any]] = []
        for one in anim:
            if not isinstance(one, dict):
                continue
            els = one.get("elements")
            if not isinstance(els, dict):
                continue
            ms = max(1, int(one.get("ms") or chunk_ms))
            out.append(make_anim_item(els, ms, phoneme=one.get("phoneme")))
        if out:
            return out
    if isinstance(anim, dict) and isinstance(anim.get("elements"), dict):
        ph = str(row.get("phoneme") or "").strip() or None
        return [make_anim_item(anim["elements"], chunk_ms, phoneme=ph)]
    return []


def merge_pb_subchunks(
    rows: list[dict[str, Any]],
    pcm_list: list[bytes],
    *,
    sample_rate: int,
    max_chunk_ms: int = PB_CHUNK_MS_MAX,
) -> tuple[list[dict[str, Any]], list[bytes]]:
    """将细粒度分片合并为 ``chunk_ms <= max_chunk_ms`` 的 pb 行。"""
    if not rows:
        return [], []

    merged_rows: list[dict[str, Any]] = []
    merged_pcm: list[bytes] = []
    batch_anim: list[dict[str, Any]] = []
    batch_servos: list[dict[str, Any]] = []
    batch_pcm = b""
    batch_ms = 0

    def _flush() -> None:
        nonlocal batch_anim, batch_servos, batch_pcm, batch_ms
        if not batch_anim:
            batch_servos = []
            batch_pcm = b""
            batch_ms = 0
            return
        row: dict[str, Any] = {
            "chunk_ms": batch_ms,
            "anim": batch_anim,
        }
        if batch_servos:
            row["servo"] = batch_servos
        merged_rows.append(row)
        merged_pcm.append(batch_pcm)
        batch_anim = []
        batch_servos = []
        batch_pcm = b""
        batch_ms = 0

    for i, row in enumerate(rows):
        row_ms = max(1, int(row.get("chunk_ms") or 0))
        anim_items = normalize_row_anim_list(row)
        if not anim_items:
            anim_items = [make_anim_item({}, row_ms)]
        pcm = pcm_list[i] if i < len(pcm_list) else b""
        servos = row.get("servo")
        servo_items: list[dict[str, Any]] = []
        if isinstance(servos, list):
            for cmd in servos:
                if isinstance(cmd, dict) and "xm" in cmd and "ym" in cmd:
                    servo_items.append(
                        {
                            "xm": int(cmd["xm"]),
                            "ym": int(cmd["ym"]),
                            "x": int(cmd["x"]),
                            "y": int(cmd["y"]),
                            "ms": int(cmd["ms"]),
                        }
                    )
        elif isinstance(servos, dict) and "xm" in servos and "ym" in servos:
            servo_items.append(
                {
                    "xm": int(servos["xm"]),
                    "ym": int(servos["ym"]),
                    "x": int(servos["x"]),
                    "y": int(servos["y"]),
                    "ms": int(servos["ms"]),
                }
            )

        if row_ms > max_chunk_ms:
            _flush()
            one_row: dict[str, Any] = {
                "chunk_ms": row_ms,
                "anim": anim_items,
            }
            if servo_items:
                one_row["servo"] = servo_items
            merged_rows.append(one_row)
            merged_pcm.append(pcm)
            continue

        if batch_ms and batch_ms + row_ms > max_chunk_ms:
            _flush()

        batch_anim.extend(copy.deepcopy(anim_items))
        batch_servos.extend(servo_items)
        batch_pcm += pcm
        batch_ms += row_ms

    _flush()

    for i, row in enumerate(merged_rows):
        raw_pcm = merged_pcm[i] if i < len(merged_pcm) else b""
        # 纯表情/场景链：PCM 故意为空，勿按 chunk_ms 填静音（否则固件 expect BIN）
        if not raw_pcm:
            continue
        aligned, cms2 = align_pcm_s16le_mono_to_chunk_ms(
            raw_pcm, int(row.get("chunk_ms") or 0), sample_rate
        )
        merged_pcm[i] = aligned
        if cms2 != int(row.get("chunk_ms") or 0):
            row["chunk_ms"] = cms2

    return merged_rows, merged_pcm


def _silence_phoneme_seg(ms: int, sample_rate: int) -> dict[str, Any]:
    """生成与 TTS 分片同结构的静音片（mono s16le），供 hold / 纯舵机片使用。"""
    ms = max(int(ms), 1)
    sr = max(int(sample_rate), 1)
    n_samples = sr * ms // 1000
    pcm = b"\x00" * (n_samples * 2)
    return {"phoneme": "", "ms": ms, "pcm": pcm}


def interleave_tts_phoneme_segs_with_servo_plan(
    segs: list[dict[str, Any]],
    servo_plan: list[dict[str, Any]] | None,
    sample_rate: int,
) -> tuple[list[dict[str, Any]], list[dict[str, int] | None]]:
    """把 ``servo`` 计划（hold + 位移）与 TTS 音素分片按播放顺序交错。

    - ``{"_hold_ms": H}``：插入 H 毫秒静音片，并在对应 ``parallel_servo`` 写入 **hold**
      ``{xm:2, ym:2, x:0, y:0, ms:H}``（本包双轴不驱动，时长 ms 与 chunk 对齐）。
    - 普通 ``{xm, ym, x, y, ms}``：**优先**消费下一条尚未输出的 TTS 分片并附上该舵机；
      若 TTS 已耗尽，则追加一条 **仅承载该舵机** 的静音片（``chunk_ms`` 与 ``ms`` 对齐，由后续 ``align_pcm`` 修正）。

    处理完计划后，将 **剩余** TTS 分片依次追加（无舵机）。这样「点头 5 次」可写 10 条位移，
    不会因音素只有 2 片而被截断为 2 帧。
    """
    if not segs:
        return [], []
    if not servo_plan:
        return list(segs), [None] * len(segs)

    tokens: list[tuple[str, Any]] = []
    for it in servo_plan:
        if not isinstance(it, dict):
            continue
        if "_hold_ms" in it:
            try:
                h = int(it["_hold_ms"])
            except (TypeError, ValueError):
                continue
            if h > 0:
                tokens.append(("hold", min(h, 30_000)))
            continue
        if "xm" in it and "ym" in it:
            try:
                xm, ym = int(it["xm"]), int(it["ym"])
                x, y = int(it["x"]), int(it["y"])
                ms = int(it["ms"])
            except (TypeError, ValueError, KeyError):
                continue
            if xm not in (0, 1, 2) or ym not in (0, 1, 2) or ms <= 0:
                continue
            tokens.append(("move", {"xm": xm, "ym": ym, "x": x, "y": y, "ms": ms}))

    if not tokens:
        return list(segs), [None] * len(segs)

    pq: deque[dict[str, Any]] = deque(copy.deepcopy(s) for s in segs)
    out_segs: list[dict[str, Any]] = []
    parallel: list[dict[str, int] | None] = []

    for kind, payload in tokens:
        if kind == "hold":
            ms = int(payload)
            out_segs.append(_silence_phoneme_seg(ms, sample_rate))
            parallel.append({"xm": 2, "ym": 2, "x": 0, "y": 0, "ms": ms})
        else:
            cmd = payload
            if pq:
                out_segs.append(pq.popleft())
            else:
                cms = max(int(cmd["ms"]), 40)
                out_segs.append(_silence_phoneme_seg(cms, sample_rate))
            parallel.append(cmd)

    while pq:
        out_segs.append(pq.popleft())
        parallel.append(None)

    return out_segs, parallel


def apply_parallel_pb_servo(
    anim_rows: list[dict[str, Any]],
    parallel: list[dict[str, int] | None] | None,
) -> int:
    """按与 ``anim_rows`` 等长的 ``parallel`` 写入 ``servo[]``；``None`` 表示该片不附加舵机。"""
    if not parallel:
        return 0
    n = 0
    for i, row in enumerate(anim_rows):
        if i >= len(parallel):
            break
        cmd = parallel[i]
        if not isinstance(cmd, dict):
            continue
        row.setdefault("servo", []).append(
            {
                "xm": int(cmd["xm"]),
                "ym": int(cmd["ym"]),
                "x": int(cmd["x"]),
                "y": int(cmd["y"]),
                "ms": int(cmd["ms"]),
            }
        )
        n += 1
    return n


def apply_llm_pb_servo_actions(
    pairs: list[tuple[dict[str, Any], bytes]],
    servo_cmds: list[dict[str, Any]] | None,
) -> int:
    """将 LLM 给出的舵机序列按分片下标与 ``pairs`` 对齐，写入各 ``msg`` 的 ``servo`` 字段。

    第 ``i`` 条舵机指令写到第 ``i`` 个 ``(msg, pcm)``；若指令多于分片则丢弃多余项并打日志。
    返回实际写入的分片数（``apply_random_pb_servo_actions`` 会跳过已有 ``servo`` 的片）。
    """
    if not servo_cmds:
        return 0
    n_pairs = len(pairs)
    if len(servo_cmds) > n_pairs:
        logger.warning(
            "[pb] LLM servo 条数 (%d) 多于音素分片 (%d)，已截断",
            len(servo_cmds),
            n_pairs,
        )
    n = 0
    for i, (msg, _pcm) in enumerate(pairs):
        if i >= len(servo_cmds):
            break
        cmd = servo_cmds[i]
        if not isinstance(cmd, dict):
            continue
        msg.setdefault("servo", []).append(
            {
                "xm": int(cmd["xm"]),
                "ym": int(cmd["ym"]),
                "x": int(cmd["x"]),
                "y": int(cmd["y"]),
                "ms": int(cmd["ms"]),
            }
        )
        n += 1
    return n


def apply_random_pb_servo_actions(
    pairs: list[tuple[dict[str, Any], list[bytes]]],
    cfg: dict[str, Any] | None,
    *,
    rng: random.Random | None = None,
) -> int:
    """在含 PCM 的 pb 分片上按概率附加 ``servo``（双轴相对位移，``xm=ym=1``）。

    用于让 ESP32 在说话时偶尔点头/摆头；不改变 binary PCM。
    返回实际附加了 ``servo`` 的分片数。
    """
    if not cfg or not cfg.get("enabled"):
        return 0
    r = rng or random.Random()
    try:
        p_hit = float(cfg.get("probability", 0.3))
    except (TypeError, ValueError):
        p_hit = 0.3
    p_hit = max(0.0, min(1.0, p_hit))
    try:
        ms_min = int(cfg.get("ms_min", cfg.get("servo_ms_min", 120)))
        ms_max = int(cfg.get("ms_max", cfg.get("servo_ms_max", 280)))
    except (TypeError, ValueError):
        ms_min, ms_max = 120, 280
    if ms_max < ms_min:
        ms_min, ms_max = ms_max, ms_min

    def _irange(key: str, default: tuple[int, int]) -> tuple[int, int]:
        v = cfg.get(key)
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            try:
                a, b = int(v[0]), int(v[1])
                return (min(a, b), max(a, b))
            except (TypeError, ValueError):
                pass
        return default

    rx0, rx1 = _irange("rel_x_range", (-6, 6))
    ry0, ry1 = _irange("rel_y_range", (-6, 6))
    skip_first = bool(cfg.get("skip_first", True))
    skip_last = bool(cfg.get("skip_last", True))

    n = len(pairs)
    added = 0
    for i, (msg, binaries) in enumerate(pairs):
        if not int((msg.get("audio") or {}).get("next_bin_len") or 0):
            continue
        if skip_first and i == 0:
            continue
        if skip_last and n > 1 and i == n - 1:
            continue
        if msg.get("servo"):
            continue
        if r.random() >= p_hit:
            continue
        dx = r.randint(rx0, rx1)
        dy = r.randint(ry0, ry1)
        if dx == 0 and dy == 0:
            if rx1 >= 1:
                dx = 1
            elif rx0 <= -1:
                dx = -1
            elif ry1 >= 1:
                dy = 1
            elif ry0 <= -1:
                dy = -1
            else:
                continue
        msg.setdefault("servo", []).append(
            {
                "xm": 1,
                "ym": 1,
                "x": int(dx),
                "y": int(dy),
                "ms": int(r.randint(ms_min, ms_max)),
            }
        )
        added += 1
    return added


def align_pcm_s16le_mono_to_chunk_ms(
    pcm: bytes, chunk_ms: int, sample_rate: int
) -> tuple[bytes, int]:
    """把 mono s16le PCM 对齐到 ``chunk_ms * sample_rate // 1000 * 2`` 字节。

    与常见设备校验公式一致：``expect_len = chunk_ms * sr / 1000 * 2``（整除）。
    音素均分切片可能多出几个采样，不处理会导致 ``binary length mismatch``。

    若 ``chunk_ms<=0`` 但有 PCM，则用 PCM 长度反推 ``chunk_ms``（floor ms）。
    """
    pcm = pcm[: len(pcm) & ~1]
    if sample_rate <= 0:
        return pcm, max(0, chunk_ms)
    if chunk_ms <= 0:
        if not pcm:
            return pcm, 0
        chunk_ms = max(1, (len(pcm) // 2) * 1000 // sample_rate)
    expected = (chunk_ms * sample_rate // 1000) * 2
    if expected <= 0:
        return pcm, chunk_ms
    if len(pcm) > expected:
        return pcm[:expected], chunk_ms
    if len(pcm) < expected:
        return pcm + b"\x00" * (expected - len(pcm)), chunk_ms
    return pcm, chunk_ms


def parse_pb_volume(raw: Any) -> int | None:
    """解析 ``volume``（0–100）；无效或空则 ``None``（wire 省略，设备不改动）。"""
    if raw is None or raw == "":
        return None
    try:
        return max(0, min(100, int(raw)))
    except (TypeError, ValueError):
        return None


def parse_pb_cam_fps(raw: Any) -> int | None:
    """解析 ``cam_fps``（>0）；无效或 0 则 ``None``（wire 省略，设备不改动）。"""
    if raw is None or raw == "":
        return None
    try:
        fps = int(raw)
    except (TypeError, ValueError):
        return None
    return fps if fps > 0 else None


def resolve_pb_volume_hint(volume: int | None = None) -> int | None:
    """仅当调用方显式传入 ``volume`` 时写入 pb；否则 wire 省略。"""
    return parse_pb_volume(volume) if volume is not None else None


def pb_device_hints_from_tts_cfg(tts_cfg: dict[str, Any] | None) -> int | None:
    cfg = tts_cfg or {}
    return parse_pb_volume(cfg.get("pb_volume"))


def resolve_pb_device_hints(
    tts_cfg: dict[str, Any] | None = None,
    *,
    volume: int | None = None,
    device_id: str | None = None,
) -> int | None:
    """显式 ``volume`` 优先；未设置则 ``None``（不下发、设备保持现状）。"""
    del device_id, tts_cfg  # 不再自动注入设备持久化音量
    return resolve_pb_volume_hint(volume)


def attach_pb_device_hints_from_config(
    target: dict[str, Any] | list[dict[str, Any]],
    tts_cfg: dict[str, Any] | None = None,
) -> None:
    """保留 API；不再自动注入 volume（须由 LLM/调用方显式指定）。"""
    del target, tts_cfg


def pb_expected_binary_lengths(msg: dict[str, Any]) -> list[int]:
    """JSON 之后按序读取的 binary 长度：PCM + ``assets[]``。"""
    out: list[int] = []
    audio_n = int((msg.get("audio") or {}).get("next_bin_len") or 0)
    if audio_n > 0:
        out.append(audio_n)
    assets = msg.get("assets")
    if isinstance(assets, list):
        for one in assets:
            if not isinstance(one, dict):
                continue
            n = int(one.get("next_bin_len") or 0)
            if n > 0:
                out.append(n)
    return out


def pb_json_messages(
    *,
    pb_req: str,
    sample_rate: int,
    fmt: str,
    channels: int,
    anim_rows: list[dict[str, Any]],
    pcm_per_idx: list[bytes],
    assets_per_idx: list[list[bytes]] | None = None,
    opus_frames_per_idx: list[int] | None = None,
    action: str = PB_ACTION_REPLACE,
    level: int = PB_LEVEL_TASK,
    volume: int | None = None,
    cam_fps: int | None = None,
) -> list[tuple[dict[str, Any], list[bytes]]]:
    """生成 ``(pb 字典, 紧随 binary 列表)``；binary 顺序：PCM（若有）→ ``assets[]``。

    单片 ``n == 1`` 使用 ``pb_single``；多片为 ``pb_start`` → ``pb_chunk``* → ``pb_end``。

    ``action``：``replace`` / ``append`` / ``default``；``level``：0–3，语义见协议文档。
    缺省 ``replace`` + ``level=1``（任务态）。

    ``volume`` / ``cam_fps``：仅显式传入时写入 pb；省略表示设备保持现状。
    """
    n = len(anim_rows)
    if n == 0:
        return []
    pairs: list[tuple[dict[str, Any], list[bytes]]] = []
    assets_per_idx = assets_per_idx or []
    for i in range(n):
        row = anim_rows[i]
        is_first = i == 0
        is_last = i == n - 1
        if n == 1:
            # 单片自成一轮：下位机要求 type=pb_single，勿单发 pb_end（多片仍 start→…→end）
            typ = "pb_single"
        elif is_first:
            typ = "pb_start"
        elif is_last:
            typ = "pb_end"
        else:
            typ = "pb_chunk"
        pcm = pcm_per_idx[i] if i < len(pcm_per_idx) else b""
        row_assets = list(anim_rows[i].get("_assets") or [])
        if i < len(assets_per_idx) and assets_per_idx[i]:
            row_assets = list(assets_per_idx[i])
        anim_list = normalize_anim_list_for_wire(normalize_row_anim_list(row))
        if not anim_list:
            anim_list = [make_anim_item({}, int(row.get("chunk_ms") or 1))]
        msg: dict[str, Any] = {
            "type": typ,
            "req": pb_req,
            "idx": i,
            "chunk_ms": int(row.get("chunk_ms") or 0),
            "anim": anim_list,
            "pb_ver": 2,
            "action": action,
            "level": int(level),
        }
        servos = row.get("servo")
        if isinstance(servos, list) and servos:
            msg["servo"] = copy.deepcopy(servos)
        # 无 PCM 时不要带 sr/fmt/ch/audio（与 /api/device_pb_anim 一致），避免固件按 R6 误等 binary
        if pcm:
            if is_first or n == 1:
                msg["sr"] = int(sample_rate)
                msg["fmt"] = fmt
                msg["ch"] = int(channels)
            audio_obj: dict[str, Any] = {"next_bin_len": len(pcm)}
            if fmt == "opus" and opus_frames_per_idx and i < len(opus_frames_per_idx):
                audio_obj["frames"] = int(opus_frames_per_idx[i])
            msg["audio"] = audio_obj
        if row_assets:
            from deskbot_server.pb.llm_display import jpeg_blob_dimensions

            msg["assets"] = [
                {
                    "fmt": "jpeg",
                    "next_bin_len": len(blob),
                    "w": aw,
                    "h": ah,
                }
                for blob in row_assets
                for aw, ah in (jpeg_blob_dimensions(blob),)
            ]
        attach_pb_device_hints(msg, volume=volume, cam_fps=cam_fps)
        binaries: list[bytes] = []
        if pcm:
            binaries.append(pcm)
        binaries.extend(row_assets)
        pairs.append((msg, binaries))
    return pairs
