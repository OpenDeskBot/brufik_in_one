"""音素序列 → 逐帧 anim 行（嘴/眼/鼻/extra）。"""

from __future__ import annotations

import copy
from typing import Any, Optional

from deskbot_server.pb.shapes import (
    _blink_eye_phase,
    _default_mouth_fallback_shape,
    _normalize_face_bundle_extra,
    _normalize_face_bundle_eyes_nose,
    _normalize_mouth_entry,
    _normalize_offset,
    apply_offset_to_primitives,
    expand_mouth_by_phoneme,
    simplify_phoneme_key,
)


def _anim_row_from_elements(elements: dict[str, Any], *, chunk_ms: int, phoneme: str = "") -> dict[str, Any]:
    el = elements if isinstance(elements, dict) else {}
    row: dict[str, Any] = {
        "elements": {
            "mouth": copy.deepcopy(el.get("mouth") if isinstance(el.get("mouth"), list) else []),
            "nose": copy.deepcopy(el.get("nose") if isinstance(el.get("nose"), list) else []),
            "eye_l": copy.deepcopy(el.get("eye_l") if isinstance(el.get("eye_l"), list) else []),
            "eye_r": copy.deepcopy(el.get("eye_r") if isinstance(el.get("eye_r"), list) else []),
            "extra": copy.deepcopy(el.get("extra") if isinstance(el.get("extra"), list) else []),
        },
        "ms": chunk_ms,
    }
    if phoneme:
        row["phoneme"] = phoneme
    return row


def _phoneme_seq_from_design(
    segments: list[dict[str, Any]],
    design: dict[str, Any],
) -> list[dict[str, Any]]:
    """``deskbot-face.json`` 音素表达式：每片直接使用匹配帧的完整 ``elements``。"""
    out: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments or []):
        ph = str(seg.get("phoneme") or "").strip()
        chunk_ms = int(seg.get("ms") or 0)
        from deskbot_server.face_design_store import find_phoneme_expression, pick_expression_elements

        expr = find_phoneme_expression(design, ph)
        if expr is None and ph:
            expr = find_phoneme_expression(design, "_") or find_phoneme_expression(design, "sil")
        elements = pick_expression_elements(expr, at_ms=0)
        if not elements:
            elements = pick_expression_elements(find_phoneme_expression(design, "sil"), at_ms=0)
        out.append(
            {
                "idx": idx,
                "chunk_ms": chunk_ms,
                "anim": [_anim_row_from_elements(elements, chunk_ms=chunk_ms, phoneme=ph)],
            }
        )
    return out


def phoneme_seq_to_anim_seq(
    segments: list[dict[str, Any]],
    face_bundle: dict[str, Any],
    *,
    device_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """返回每片 ``idx, chunk_ms, phoneme, anim``（与仿真页 / pb 一致）。

    ``face_bundle`` 结构：

    - ``mouth_by_phoneme``：音素 -> ``{ "elements", "offset" }`` 或图元列表；共享条 **仅** 放在 ``mouth_by_phoneme_groups``。
    - ``mouth_by_phoneme_groups``（可选）：``{ "states", "elements", "offset" }`` 数组；与 ``mouth_by_phoneme`` 合并展开后，
      同音素以对象内单键为准（``expand_mouth_by_phoneme``）。
    - ``eye_l`` / ``eye_r``：``default`` / ``open`` / ``close`` 图元列表；共享态 **仅** 放在 ``eye_l_groups`` / ``eye_r_groups``（``states`` + ``elements``，无 ``offset``）。
      ``_normalize_face_bundle_eyes_nose`` 展开后去掉组条键。眨眼相位由 ``metadata.blink`` 的 ``open_ms`` / ``close_ms`` 决定。
    - ``nose``：``default`` 列表；共享 **仅** ``nose_groups``（``states`` 仅 ``"default"``）。
    - ``extra``：任意 **态名字符串** → 图元列表（与单眼某态、鼻 ``default`` 同级结构）；共享 **仅** ``extra_groups``（``states`` + ``elements``，无 ``offset``）。当前播放哪一态由 ``metadata.extra_state`` 指定（缺省 ``"default"``）；该片仍应用口型 ``offset`` 对 **鼻、左眼、右眼、extra** 做整体平移（与眼鼻一致）。
    - ``metadata.blink``: ``open_ms``, ``close_ms``；``metadata.extra_state``：附加层态名；其它键可扩展，未知忽略。

    每片动画相位取 **该片开始时刻** 的累计毫秒（从首片起算）。
    """
    from deskbot_server.face_design_store import _load_face_design_cached

    design = _load_face_design_cached(device_id=device_id)
    if isinstance(design, dict) and design.get("phonemes"):
        return _phoneme_seq_from_design(segments, design)

    work = copy.deepcopy(face_bundle) if isinstance(face_bundle, dict) else {}
    _normalize_face_bundle_eyes_nose(work)
    _normalize_face_bundle_extra(work)

    mouth_raw = work.get("mouth_by_phoneme") if isinstance(work, dict) else None
    mouth_gr = work.get("mouth_by_phoneme_groups") if isinstance(work, dict) else None
    mouth_by = expand_mouth_by_phoneme(
        mouth_raw if isinstance(mouth_raw, dict) else {},
        mouth_gr if isinstance(mouth_gr, list) else None,
    )
    fb_mouth = _normalize_mouth_entry(mouth_by.get("_"))
    if not fb_mouth["elements"]:
        fb_mouth = _default_mouth_fallback_shape()

    eye_l = work.get("eye_l") if isinstance(work.get("eye_l"), dict) else {}
    eye_r = work.get("eye_r") if isinstance(work.get("eye_r"), dict) else {}
    nose = work.get("nose") if isinstance(work.get("nose"), dict) else {"default": []}
    extra_lut = work.get("extra") if isinstance(work.get("extra"), dict) else {}

    meta = work.get("metadata") if isinstance(work.get("metadata"), dict) else {}
    blink_cfg = meta.get("blink") if isinstance(meta.get("blink"), dict) else {}
    extra_state = str(meta.get("extra_state") or "default").strip() or "default"

    def _pick_eye(eye: dict[str, Any], phase: str) -> list[dict[str, Any]]:
        d = eye.get("default") if isinstance(eye.get("default"), list) else []
        o = eye.get("open") if isinstance(eye.get("open"), list) else []
        c = eye.get("close") if isinstance(eye.get("close"), list) else []
        if phase == "default":
            return d or o or c
        if phase == "open":
            return o or d or c
        return c or d or o

    cum_ms = 0
    out: list[dict[str, Any]] = []
    for idx, seg in enumerate(segments or []):
        ph = str(seg.get("phoneme") or "").strip()
        chunk_ms = int(seg.get("ms") or 0)
        lookup = simplify_phoneme_key(ph)
        raw_mouth = mouth_by.get(ph)
        if raw_mouth is None and lookup != ph:
            raw_mouth = mouth_by.get(lookup)
        if raw_mouth is None:
            raw_mouth = mouth_by.get("_")
        mouth_entry = _normalize_mouth_entry(raw_mouth if raw_mouth is not None else fb_mouth)
        if not mouth_entry["elements"]:
            mouth_entry = copy.deepcopy(fb_mouth)
        dx, dy = _normalize_offset(mouth_entry.get("offset"))

        phase = _blink_eye_phase(cum_ms, blink_cfg)
        eye_l_raw = _pick_eye(eye_l, phase)
        eye_r_raw = _pick_eye(eye_r, phase)
        nose_raw = nose.get("default") if isinstance(nose.get("default"), list) else []
        extra_raw = (
            extra_lut.get(extra_state)
            if isinstance(extra_lut.get(extra_state), list)
            else None
        )
        if extra_raw is None:
            extra_raw = (
                extra_lut.get("default")
                if isinstance(extra_lut.get("default"), list)
                else []
            )

        mouth_prims = copy.deepcopy(mouth_entry["elements"])
        eye_l_prims = apply_offset_to_primitives(eye_l_raw, dx, dy)
        eye_r_prims = apply_offset_to_primitives(eye_r_raw, dx, dy)
        nose_prims = apply_offset_to_primitives(nose_raw, dx, dy)
        extra_prims = apply_offset_to_primitives(extra_raw, dx, dy)

        out.append(
            {
                "idx": idx,
                "chunk_ms": chunk_ms,
                "anim": [
                    {
                        "elements": {
                            "mouth": mouth_prims,
                            "nose": nose_prims,
                            "eye_l": eye_l_prims,
                            "eye_r": eye_r_prims,
                            "extra": extra_prims,
                        },
                        "ms": chunk_ms,
                        **({"phoneme": ph} if ph else {}),
                    }
                ],
            }
        )
        cum_ms += chunk_ms
    return out

