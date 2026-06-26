"""表情场景：读写 ``deskbot-face.json`` 的 ``emotions`` 段。"""
from __future__ import annotations

import copy
import re
from typing import Any, Optional
from deskbot_server.pb.display import scale_primitive, scale_primitives
from deskbot_server.pb.shapes import PB_ACTION_REPLACE, PB_LEVEL_DEBUG, apply_pb_dispatch_fields

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$", re.I)
_FRAME_MS_MIN = 40
_FRAME_MS_MAX = 30000

# 与 face_bundle metadata.blink 一致
_DEFAULT_SPEECH_BLINK_OPEN_MS = 2300
_DEFAULT_SPEECH_BLINK_CLOSE_MS = 180

_DEFAULT_SPEECH_NOSE: list[dict[str, Any]] = [
    {"shape": "circle", "x": 64, "y": 34, "r": 5},
]


def _default_speech_eye_l(rh: int, y: int, rw: int = 6) -> dict[str, Any]:
    return {"shape": "ellipse_fill", "x": 40, "y": y, "rw": rw, "rh": rh}


def _default_speech_eye_r(rh: int, y: int, rw: int = 6) -> dict[str, Any]:
    return {"shape": "ellipse_fill", "x": 80, "y": y, "rw": rw, "rh": rh}


def default_speech_blink_scene() -> dict[str, Any]:
    """正常说话时的默认眨眼（眼+鼻；嘴由音素层填充）。"""
    nose = copy.deepcopy(_DEFAULT_SPEECH_NOSE)
    half = _DEFAULT_SPEECH_BLINK_CLOSE_MS // 3
    closed = _DEFAULT_SPEECH_BLINK_CLOSE_MS - half * 2
    empty_mouth: list[dict[str, Any]] = []

    def _blink_elements(eye_l: dict[str, Any], eye_r: dict[str, Any]) -> dict[str, Any]:
        return {
            "mouth": copy.deepcopy(empty_mouth),
            "nose": scale_primitives(copy.deepcopy(nose)),
            "eye_l": scale_primitives([eye_l]),
            "eye_r": scale_primitives([eye_r]),
            "extra": [],
        }

    return {
        "name": "default",
        "title": "正常说话（眨眼）",
        "frames": [
            {
                "ms": _DEFAULT_SPEECH_BLINK_OPEN_MS,
                "elements": _blink_elements(
                    _default_speech_eye_l(6, 10, 6),
                    _default_speech_eye_r(6, 10, 6),
                ),
            },
            {
                "ms": half,
                "elements": _blink_elements(
                    _default_speech_eye_l(3, 10, 6),
                    _default_speech_eye_r(3, 10, 6),
                ),
            },
            {
                "ms": closed,
                "elements": _blink_elements(
                    _default_speech_eye_l(1, 10, 5),
                    _default_speech_eye_r(1, 10, 5),
                ),
            },
            {
                "ms": half,
                "elements": _blink_elements(
                    _default_speech_eye_l(3, 10, 6),
                    _default_speech_eye_r(3, 10, 6),
                ),
            },
        ],
    }


def _mk_frame(
    ms: int,
    *,
    mouth: list[dict[str, Any]],
    eye_l: list[dict[str, Any]],
    eye_r: list[dict[str, Any]],
    nose: Optional[list[dict[str, Any]]] = None,
    extra: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    return {
        "ms": ms,
        "elements": {
            "mouth": scale_primitives(copy.deepcopy(mouth)),
            "nose": scale_primitives(
                copy.deepcopy(nose if nose is not None else _DEFAULT_SPEECH_NOSE),
            ),
            "eye_l": scale_primitives(copy.deepcopy(eye_l)),
            "eye_r": scale_primitives(copy.deepcopy(eye_r)),
            "extra": scale_primitives(copy.deepcopy(extra or [])),
        },
    }


def _hold_scene(
    name: str,
    title: str,
    frames: list[dict[str, Any]],
) -> dict[str, Any]:
    if not frames:
        raise ValueError("frames required")
    return {"name": name, "title": title, "frames": frames}


def builtin_emotion_scenes() -> list[dict[str, Any]]:
    """常见内置情绪表情（供模板或测试）。"""
    e_open_l = [{"shape": "ellipse_fill", "x": 40, "y": 10, "rw": 6, "rh": 6}]
    e_open_r = [{"shape": "ellipse_fill", "x": 80, "y": 10, "rw": 6, "rh": 6}]
    return [
        _hold_scene(
            "angry",
            "生气",
            [
                _mk_frame(
                    480,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 56, "y": 48, "w": 26, "h": 5, "radius": 1},
                        {"shape": "line", "x1": 58, "y1": 51, "x2": 80, "y2": 51},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 12, "rw": 5, "rh": 2}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 12, "rw": 5, "rh": 2}],
                    extra=[
                        {"shape": "line", "x1": 30, "y1": 7, "x2": 46, "y2": 10},
                        {"shape": "line", "x1": 74, "y1": 10, "x2": 90, "y2": 7},
                    ],
                ),
                _mk_frame(
                    480,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 55, "y": 47, "w": 28, "h": 6, "radius": 1},
                        {"shape": "line", "x1": 57, "y1": 51, "x2": 81, "y2": 51},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 12, "rw": 5, "rh": 2}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 12, "rw": 5, "rh": 2}],
                    extra=[
                        {"shape": "line", "x1": 29, "y1": 6, "x2": 47, "y2": 9},
                        {"shape": "line", "x1": 73, "y1": 9, "x2": 91, "y2": 6},
                    ],
                ),
            ],
        ),
        _hold_scene(
            "sad",
            "悲伤",
            [
                _mk_frame(
                    560,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 58, "y": 50, "w": 24, "h": 5, "radius": 2},
                        {"shape": "line", "x1": 60, "y1": 52, "x2": 80, "y2": 55},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 13, "rw": 5, "rh": 3}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 13, "rw": 5, "rh": 3}],
                ),
                _mk_frame(
                    560,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 58, "y": 51, "w": 24, "h": 5, "radius": 2},
                        {"shape": "line", "x1": 60, "y1": 53, "x2": 80, "y2": 56},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 14, "rw": 5, "rh": 3}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 14, "rw": 5, "rh": 3}],
                ),
            ],
        ),
        _hold_scene(
            "wry_smile",
            "苦笑",
            [
                _mk_frame(
                    520,
                    mouth=[
                        {"shape": "line", "x1": 58, "y1": 50, "x2": 84, "y2": 46},
                        {"shape": "round_rect_outline", "x": 70, "y": 44, "w": 14, "h": 4, "radius": 1},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 11, "rw": 5, "rh": 3}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 10, "rw": 6, "rh": 5}],
                ),
                _mk_frame(
                    520,
                    mouth=[
                        {"shape": "line", "x1": 58, "y1": 51, "x2": 84, "y2": 47},
                        {"shape": "round_rect_outline", "x": 70, "y": 45, "w": 14, "h": 4, "radius": 1},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 11, "rw": 5, "rh": 3}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 10, "rw": 6, "rh": 5}],
                ),
            ],
        ),
        _hold_scene(
            "fake_smile",
            "假笑",
            [
                _mk_frame(
                    500,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 54, "y": 44, "w": 32, "h": 7, "radius": 2},
                        {"shape": "line", "x1": 56, "y1": 49, "x2": 84, "y2": 49},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 11, "rw": 4, "rh": 2}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 11, "rw": 4, "rh": 2}],
                ),
                _mk_frame(
                    500,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 54, "y": 44, "w": 32, "h": 7, "radius": 2},
                        {"shape": "line", "x1": 56, "y1": 49, "x2": 84, "y2": 48},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 11, "rw": 4, "rh": 2}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 11, "rw": 4, "rh": 2}],
                ),
            ],
        ),
        _hold_scene(
            "fawning",
            "谄媚",
            [
                _mk_frame(
                    480,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 56, "y": 43, "w": 30, "h": 9, "radius": 3},
                        {"shape": "line", "x1": 58, "y1": 48, "x2": 84, "y2": 48},
                    ],
                    eye_l=e_open_l,
                    eye_r=e_open_r,
                    extra=[
                        {"shape": "circle", "x": 34, "y": 8, "r": 1},
                        {"shape": "circle", "x": 90, "y": 8, "r": 1},
                    ],
                ),
                _mk_frame(
                    480,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 56, "y": 42, "w": 30, "h": 10, "radius": 3},
                        {"shape": "line", "x1": 58, "y1": 47, "x2": 84, "y2": 47},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 9, "rw": 7, "rh": 7}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 9, "rw": 7, "rh": 7}],
                    extra=[
                        {"shape": "circle", "x": 33, "y": 7, "r": 1},
                        {"shape": "circle", "x": 91, "y": 7, "r": 1},
                    ],
                ),
            ],
        ),
        _hold_scene(
            "shy",
            "害羞",
            [
                _mk_frame(
                    540,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 60, "y": 48, "w": 16, "h": 4, "radius": 2},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 38, "y": 12, "rw": 5, "rh": 4}],
                    eye_r=[{"shape": "ellipse_fill", "x": 78, "y": 12, "rw": 5, "rh": 4}],
                    extra=[
                        {"shape": "ellipse_fill", "x": 28, "y": 38, "rw": 4, "rh": 2},
                        {"shape": "ellipse_fill", "x": 100, "y": 38, "rw": 4, "rh": 2},
                    ],
                ),
                _mk_frame(
                    540,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 60, "y": 48, "w": 16, "h": 4, "radius": 2},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 37, "y": 13, "rw": 5, "rh": 3}],
                    eye_r=[{"shape": "ellipse_fill", "x": 77, "y": 13, "rw": 5, "rh": 3}],
                    extra=[
                        {"shape": "ellipse_fill", "x": 28, "y": 38, "rw": 5, "rh": 3},
                        {"shape": "ellipse_fill", "x": 100, "y": 38, "rw": 5, "rh": 3},
                    ],
                ),
            ],
        ),
        _hold_scene(
            "astonished",
            "吃惊",
            [
                _mk_frame(
                    400,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 58, "y": 42, "w": 18, "h": 16, "radius": 8},
                        {"shape": "round_rect_outline", "x": 59, "y": 43, "w": 16, "h": 14, "radius": 7},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 9, "rw": 7, "rh": 7}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 9, "rw": 7, "rh": 7}],
                ),
                _mk_frame(
                    440,
                    mouth=[
                        {"shape": "round_rect_outline", "x": 56, "y": 40, "w": 22, "h": 20, "radius": 10},
                        {"shape": "round_rect_outline", "x": 57, "y": 41, "w": 20, "h": 18, "radius": 9},
                    ],
                    eye_l=[{"shape": "ellipse_fill", "x": 40, "y": 8, "rw": 8, "rh": 8}],
                    eye_r=[{"shape": "ellipse_fill", "x": 80, "y": 8, "rw": 8, "rh": 8}],
                ),
            ],
        ),
    ]


def _extract_frame_elements(raw: dict[str, Any]) -> dict[str, Any]:
    els = raw.get("elements")
    if isinstance(els, dict):
        return copy.deepcopy(els)
    anim = raw.get("anim")
    if isinstance(anim, dict) and isinstance(anim.get("elements"), dict):
        return copy.deepcopy(anim["elements"])
    raise ValueError("frame.elements required (legacy frame.anim.elements also accepted)")


def _normalize_design_frame(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("frame must be an object")
    try:
        ms = int(raw.get("ms", 500))
    except (TypeError, ValueError) as exc:
        raise ValueError("frame.ms must be int") from exc
    ms = max(_FRAME_MS_MIN, min(_FRAME_MS_MAX, ms))
    elements = _extract_frame_elements(raw)
    return {"ms": ms, "elements": elements}


def normalize_design_scene(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("scene entry must be an object")
    name = str(raw.get("name") or raw.get("id") or "").strip()
    if not name:
        raise ValueError("name required")
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid name {name!r} (use [a-z0-9_])")
    title = str(raw.get("title") or raw.get("title_zh") or name).strip()
    frames_raw = raw.get("frames")
    if not isinstance(frames_raw, list) or not frames_raw:
        raise ValueError(f"scene {name!r} requires non-empty frames[]")
    frames = [_normalize_design_frame(f) for f in frames_raw]
    return {"name": name, "title": title, "frames": frames}


def normalize_face_expr_scenes(raw: object) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        inner = raw.get("scenes")
        if isinstance(inner, list):
            items = inner
        elif isinstance(inner, dict):
            items = []
            for k, v in inner.items():
                if isinstance(v, dict):
                    items.append({**v, "name": k})
                else:
                    items.append({"name": k, "frames": v})
        else:
            items = raw.get("items") if isinstance(raw.get("items"), list) else []
    else:
        raise ValueError("body must be a JSON array")
    return [normalize_design_scene(x) for x in items]


def load_face_expr_scenes_file(
    *, seed_if_missing: bool = True, device_id: Optional[str] = None
) -> Optional[list[dict[str, Any]]]:
    from deskbot_server.face_design_store import (
        _load_face_design_cached,
        emotions_as_scenes,
        ensure_face_design_file,
    )

    if seed_if_missing:
        ensure_face_design_file(device_id=device_id)
    design = _load_face_design_cached(device_id=device_id)
    if not isinstance(design, dict):
        return None if not seed_if_missing else []
    return emotions_as_scenes(design)


def save_face_expr_scenes_file(
    rows: list[dict[str, Any]], *, device_id: Optional[str] = None
) -> list[dict[str, Any]]:
    from deskbot_server.face_design_store import (
        apply_emotion_scenes_to_design,
        emotions_as_scenes,
        ensure_face_design_file,
        save_face_design_file,
    )

    design = ensure_face_design_file(device_id=device_id)
    norm_rows = normalize_face_expr_scenes(rows)
    updated = apply_emotion_scenes_to_design(design, norm_rows)
    saved_doc = save_face_design_file(updated, device_id=device_id)
    return emotions_as_scenes(saved_doc)


def design_frames_to_pb_chain(
    frames: list[dict[str, Any]],
    *,
    runtime_req: str,
) -> list[dict[str, Any]]:
    """将设计页 ``[{ ms, elements }, ...]`` 转为可下发的 pb 链（合并后 ``chunk_ms`` ≤ 10s）。"""
    from deskbot_server.pb.servo_pcm import (
        PB_CHUNK_MS_MAX,
        load_tts_cfg_for_pb_hints,
        make_anim_item,
        merge_pb_subchunks,
        pb_json_messages,
        resolve_pb_device_hints,
    )

    if not frames:
        return []
    sub_rows: list[dict[str, Any]] = []
    pcm_empty: list[bytes] = []
    for fr in frames:
        ms = max(_FRAME_MS_MIN, min(_FRAME_MS_MAX, int(fr.get("ms") or 500)))
        elements = _extract_frame_elements(fr if isinstance(fr, dict) else {})
        sub_rows.append(
            {
                "chunk_ms": ms,
                "anim": [make_anim_item(elements, ms)],
            }
        )
        pcm_empty.append(b"")
    merged_rows, merged_pcm = merge_pb_subchunks(
        sub_rows, pcm_empty, sample_rate=24000, max_chunk_ms=PB_CHUNK_MS_MAX
    )
    pb_vol, pb_cam_fps = resolve_pb_device_hints(load_tts_cfg_for_pb_hints())
    pairs = pb_json_messages(
        pb_req=runtime_req,
        sample_rate=24000,
        fmt="s16le",
        channels=1,
        anim_rows=merged_rows,
        pcm_per_idx=merged_pcm,
        action=PB_ACTION_REPLACE,
        level=PB_LEVEL_DEBUG,
        volume=pb_vol,
        cam_fps=pb_cam_fps,
    )
    apply_pb_dispatch_fields(
        [msg for msg, _ in pairs], action=PB_ACTION_REPLACE, level=PB_LEVEL_DEBUG
    )
    return pairs


def find_design_scene_by_name(rows: list[dict[str, Any]], name: str) -> Optional[dict[str, Any]]:
    want = str(name or "").strip().lower()
    if not want:
        return None
    for row in rows:
        if str(row.get("name") or "").strip().lower() == want:
            return row
        alias = row.get("alias")
        if isinstance(alias, list):
            for raw in alias:
                if str(raw or "").strip().lower() == want:
                    return row
    return None
