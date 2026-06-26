"""face_bundle 加载、合并、热重载与 resolve。"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from typing import Any

import yaml

from deskbot_server.pb.display import scale_primitive
from deskbot_server.pb.shapes import (
    _EYE_ANIM_STATE_KEYS,
    _default_mouth_fallback_shape,
    _normalize_face_bundle_extra,
    _normalize_face_bundle_eyes_nose,
    _normalize_mouth_entry,
    _normalize_offset,
    default_face_circles,
    default_mouth_rect_for_phoneme,
    enumerate_zh_phonemes,
    expand_mouth_by_phoneme,
    is_extra_elements_group_entry,
    is_eye_elements_group_entry,
    is_mouth_phoneme_group_entry,
    is_nose_elements_group_entry,
)

_logger = logging.getLogger(__name__)

_hot_file_cache: dict[str, tuple[float, Any]] = {}
_ensured_json_bundle_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_expr_default_bundle_cache: tuple[str, float, float, dict[str, Any]] | None = None


def _frame_elements(frames: list[Any], idx: int) -> dict[str, Any]:
    if idx < 0 or idx >= len(frames):
        return {}
    row = frames[idx]
    if not isinstance(row, dict):
        return {}
    elements = row.get("elements")
    return elements if isinstance(elements, dict) else {}


def expr_default_pb_face_bundle(*, device_id: str | None = None) -> dict[str, Any]:
    """从 ``deskbot-face.json`` 的 ``idle``/``default`` 取眼/鼻，口型来自 ``phonemes``。"""
    from deskbot_server.face_expr_scenes_store import (
        _DEFAULT_SPEECH_BLINK_CLOSE_MS,
        _DEFAULT_SPEECH_BLINK_OPEN_MS,
        default_speech_blink_scene,
        find_design_scene_by_name,
        load_face_expr_scenes_file,
    )
    from deskbot_server.face_mouth_config_store import groups_to_mouth_bundle, load_face_mouth_cfg_file

    rows = load_face_expr_scenes_file(seed_if_missing=True, device_id=device_id) or []
    ent = (
        find_design_scene_by_name(rows, "default")
        or find_design_scene_by_name(rows, "idle")
        or default_speech_blink_scene()
    )
    frames = ent.get("frames") if isinstance(ent, dict) else []
    if not isinstance(frames, list):
        frames = []

    open_e = _frame_elements(frames, 0)
    half_e = _frame_elements(frames, 1) if len(frames) > 1 else open_e
    close_e = _frame_elements(frames, 2) if len(frames) > 2 else open_e

    def _copy_list(key: str, src: dict[str, Any]) -> list[Any]:
        val = src.get(key)
        return copy.deepcopy(val) if isinstance(val, list) else []

    eye_l_open = _copy_list("eye_l", open_e)
    eye_r_open = _copy_list("eye_r", open_e)
    eye_l_default = _copy_list("eye_l", half_e) or copy.deepcopy(eye_l_open)
    eye_r_default = _copy_list("eye_r", half_e) or copy.deepcopy(eye_r_open)
    eye_l_close = _copy_list("eye_l", close_e) or copy.deepcopy(eye_l_open)
    eye_r_close = _copy_list("eye_r", close_e) or copy.deepcopy(eye_r_open)
    nose_default = _copy_list("nose", open_e)
    extra_default = _copy_list("extra", open_e)

    open_ms = _DEFAULT_SPEECH_BLINK_OPEN_MS
    if frames and isinstance(frames[0], dict):
        try:
            open_ms = int(frames[0].get("ms") or _DEFAULT_SPEECH_BLINK_OPEN_MS)
        except (TypeError, ValueError):
            open_ms = _DEFAULT_SPEECH_BLINK_OPEN_MS

    bundle: dict[str, Any] = {
        "mouth_by_phoneme": {},
        "eye_l": {
            "default": eye_l_default,
            "open": eye_l_open,
            "close": eye_l_close,
        },
        "eye_r": {
            "default": eye_r_default,
            "open": eye_r_open,
            "close": eye_r_close,
        },
        "nose": {"default": nose_default},
        "extra": {"default": extra_default},
        "metadata": {
            "blink": {
                "open_ms": open_ms,
                "close_ms": _DEFAULT_SPEECH_BLINK_CLOSE_MS,
            },
        },
    }

    groups = load_face_mouth_cfg_file(seed_if_missing=True, device_id=device_id) or []
    bundle.update(groups_to_mouth_bundle(groups))
    return ensure_pb_face_bundle_shape(bundle)


def load_expr_default_pb_face_bundle(*, device_id: str | None = None) -> dict[str, Any]:
    """``default`` 表情脸包；随 ``deskbot-face.json`` mtime 热重载。"""
    global _expr_default_bundle_cache
    from deskbot_server.face_design_store import design_file_mtime

    dev = str(device_id or "").strip()
    design_mtime = design_file_mtime(device_id=dev or None)
    key = (dev, design_mtime)
    if _expr_default_bundle_cache is not None and _expr_default_bundle_cache[:2] == key:
        return _expr_default_bundle_cache[2]
    out = expr_default_pb_face_bundle(device_id=dev or None)
    _expr_default_bundle_cache = (dev, design_mtime, out)
    _logger.info(
        "[pb_face_bundle] 已从 deskbot-face 加载 device_id=%s design_mtime=%s",
        dev or "(global)",
        design_mtime,
    )
    return out

def default_pb_face_bundle() -> dict[str, Any]:
    """默认整包：口型按音素 + 默认零偏移；眼为 ``default``/``open``/``close`` 三键；鼻仅 ``default``。

    结构见 ``phoneme_seq_to_anim_seq`` 文档串。
    """
    fc = default_face_circles()
    mouth_by: dict[str, Any] = {}
    for ph in enumerate_zh_phonemes():
        mouth_by[ph] = {
            "elements": default_mouth_rect_for_phoneme(ph),
            "offset": {"x": 0, "y": 0},
        }
    eye_l_open = copy.deepcopy(fc["eye_l"])
    eye_r_open = copy.deepcopy(fc["eye_r"])
    eye_l_blink = [
        scale_primitive({"shape": "line", "x1": 34, "y1": 26, "x2": 50, "y2": 26}),
    ]
    eye_r_blink = [
        scale_primitive({"shape": "line", "x1": 78, "y1": 26, "x2": 94, "y2": 26}),
    ]
    nose0 = copy.deepcopy(fc["nose"])
    return {
        "mouth_by_phoneme": mouth_by,
        "eye_l": {
            "default": copy.deepcopy(eye_l_open),
            "open": copy.deepcopy(eye_l_open),
            "close": eye_l_blink,
        },
        "eye_r": {
            "default": copy.deepcopy(eye_r_open),
            "open": copy.deepcopy(eye_r_open),
            "close": eye_r_blink,
        },
        "nose": {"default": nose0},
        "extra": {"default": []},
        "metadata": {
            "blink": {
                "open_ms": 3000,
                "close_ms": 100,
            },
        },
    }


def demo_pb_face_bundle() -> dict[str, Any]:
    """试玩档：较快眨眼；眼三态均为 ``ellipse_fill``（中心 + ``rw``/``rh`` 半轴），便于 ESP32 对 ``rw``/``rh`` 插值。

    - ``open``：``rw==rh`` → 圆
    - ``close``：``rh`` 很小 → 接近横线
    - ``default``：介于两者之间（略眯）

    启用：``tts.pb_face_bundle: demo`` 或 ``DESKBOT_PB_FACE_BUNDLE=demo``。
    """
    b = default_pb_face_bundle()
    # 左眼中心 (42,26)、右眼 (86,26)；三态同 shape、同键名，固件可对 rw/rh（及可选 x/y）做 lerp
    b["eye_l"] = {
        "default": [scale_primitive({"shape": "ellipse_fill", "x": 42, "y": 26, "rw": 5, "rh": 4})],
        "open": [scale_primitive({"shape": "ellipse_fill", "x": 42, "y": 26, "rw": 7, "rh": 7})],
        "close": [scale_primitive({"shape": "ellipse_fill", "x": 42, "y": 26, "rw": 10, "rh": 1})],
    }
    b["eye_r"] = {
        "default": [scale_primitive({"shape": "ellipse_fill", "x": 86, "y": 26, "rw": 5, "rh": 4})],
        "open": [scale_primitive({"shape": "ellipse_fill", "x": 86, "y": 26, "rw": 7, "rh": 7})],
        "close": [scale_primitive({"shape": "ellipse_fill", "x": 86, "y": 26, "rw": 10, "rh": 1})],
    }
    b["nose"] = {
        "default": [scale_primitive({"shape": "circle", "x": 64, "y": 34, "r": 5})],
    }
    b["metadata"] = {
        "blink": {
            "open_ms": 1400,
            "close_ms": 140,
        },
    }
    mb = b.get("mouth_by_phoneme")
    if isinstance(mb, dict):
        for ph, row in mb.items():
            if not isinstance(row, dict):
                continue
            dx, dy = 0, 0
            if ph in ("_", "sil", "sp", "spl", "spn") or (
                ph and re.match(r"^sp[1-4]$", ph)
            ):
                pass
            elif ph and ph[-1] in "12345":
                body = ph[:-1]
                tone = ph[-1]
                if re.match(r"^(a|o|e|ai|ei|ao|ou|er)", body):
                    dy = -2
                if tone in ("1", "2"):
                    dy -= 1
                elif tone == "3":
                    dx = 2
                elif tone == "4":
                    dx = -2
            else:
                dy = 1
            row["offset"] = {"x": dx, "y": dy}
    return b


def merge_pb_face_bundle(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """在 ``base`` 上叠加 ``overlay``（深拷贝 ``base``）。``metadata`` 子表浅合并。

    ``mouth_by_phoneme_groups``：若 overlay 提供该列表，则整表替换；并先从 ``mouth_by_phoneme`` 中
    删除各共享条 ``states`` 中出现的音素键，以免与「整表默认口型」等基线键叠加后，在展开时被单键覆盖掉共享条。
    ``mouth_by_phoneme``：仅合并 **音素键** → 口型对象或图元列表（共享条 **只** 放在 ``mouth_by_phoneme_groups``）。

    ``eye_l_groups`` / ``eye_r_groups`` / ``nose_groups``：若 overlay 提供列表则整表替换，并先从对应
    ``eye_*`` / ``nose`` 对象中删除组条 ``states`` 中出现的键，再合并 overlay 中 ``eye_*`` 的 ``default``/``open``/``close`` 列表与 ``nose.default``。

    ``extra_groups`` / ``extra``：与鼻类似；``extra`` 为任意态名 → 图元数组。
    """
    out = copy.deepcopy(base)
    if not isinstance(overlay, dict):
        return out
    om = overlay.get("metadata")
    if isinstance(om, dict):
        out.setdefault("metadata", {})
        for mk, mv in om.items():
            if isinstance(mv, dict) and isinstance(out["metadata"].get(mk), dict):
                merged = copy.deepcopy(out["metadata"][mk])
                merged.update(copy.deepcopy(mv))
                out["metadata"][mk] = merged
            else:
                out["metadata"][mk] = copy.deepcopy(mv)
    if "eye_l_groups" in overlay:
        og = overlay["eye_l_groups"]
        if isinstance(og, list):
            out["eye_l_groups"] = copy.deepcopy(og)
            el = out.setdefault("eye_l", {})
            for item in og:
                if is_eye_elements_group_entry(item):
                    for s in item.get("states") or []:
                        if s in _EYE_ANIM_STATE_KEYS:
                            el.pop(str(s), None)
        else:
            out.pop("eye_l_groups", None)
    if "eye_r_groups" in overlay:
        og = overlay["eye_r_groups"]
        if isinstance(og, list):
            out["eye_r_groups"] = copy.deepcopy(og)
            er = out.setdefault("eye_r", {})
            for item in og:
                if is_eye_elements_group_entry(item):
                    for s in item.get("states") or []:
                        if s in _EYE_ANIM_STATE_KEYS:
                            er.pop(str(s), None)
        else:
            out.pop("eye_r_groups", None)
    for key in ("eye_l", "eye_r"):
        if key not in overlay or not isinstance(overlay[key], dict):
            continue
        ov = overlay[key]
        out.setdefault(key, {})
        for sk in ("default", "open", "close"):
            if sk in ov and isinstance(ov[sk], list):
                out[key][sk] = copy.deepcopy(ov[sk])
    if "nose_groups" in overlay:
        og = overlay["nose_groups"]
        if isinstance(og, list):
            out["nose_groups"] = copy.deepcopy(og)
            ns = out.setdefault("nose", {"default": []})
            for item in og:
                if is_nose_elements_group_entry(item):
                    ns.pop("default", None)
        else:
            out.pop("nose_groups", None)
    if "nose" in overlay:
        nv = overlay["nose"]
        out.setdefault("nose", {"default": []})
        if isinstance(nv, dict) and isinstance(nv.get("default"), list):
            out["nose"]["default"] = copy.deepcopy(nv["default"])
    if "mouth_by_phoneme_groups" in overlay:
        og = overlay["mouth_by_phoneme_groups"]
        if isinstance(og, list):
            out["mouth_by_phoneme_groups"] = copy.deepcopy(og)
            mb_strip = out.setdefault("mouth_by_phoneme", {})
            for item in og:
                if is_mouth_phoneme_group_entry(item):
                    for p in item.get("states") or []:
                        pk = str(p).strip()
                        if pk:
                            mb_strip.pop(pk, None)
        else:
            out.pop("mouth_by_phoneme_groups", None)

    mbo = overlay.get("mouth_by_phoneme")
    if isinstance(mbo, dict):
        out.setdefault("mouth_by_phoneme", {})
        for ph, val in mbo.items():
            if is_mouth_phoneme_group_entry(val):
                continue
            cur = out["mouth_by_phoneme"].get(ph)
            if cur is None:
                out["mouth_by_phoneme"][ph] = _normalize_mouth_entry(val)
                continue
            if not isinstance(cur, dict):
                cur = _normalize_mouth_entry(cur)
                out["mouth_by_phoneme"][ph] = cur
            if isinstance(val, dict):
                if val.get("elements") is not None:
                    cur["elements"] = copy.deepcopy(val["elements"])
                if val.get("offset") is not None:
                    ox, oy = _normalize_offset(val["offset"])
                    cur["offset"] = {"x": ox, "y": oy}
            elif isinstance(val, list):
                cur["elements"] = copy.deepcopy(val)
    if "extra_groups" in overlay:
        og = overlay["extra_groups"]
        if isinstance(og, list):
            out["extra_groups"] = copy.deepcopy(og)
            ex = out.setdefault("extra", {})
            if isinstance(ex, dict):
                for item in og:
                    if is_extra_elements_group_entry(item):
                        for s in item.get("states") or []:
                            sk = str(s).strip()
                            if sk:
                                ex.pop(sk, None)
        else:
            out.pop("extra_groups", None)
    if "extra" in overlay and isinstance(overlay["extra"], dict):
        out.setdefault("extra", {})
        for ek, evv in overlay["extra"].items():
            if str(ek).startswith("_"):
                continue
            if isinstance(evv, list):
                out["extra"][str(ek)] = copy.deepcopy(evv)
    _normalize_face_bundle_eyes_nose(out)
    _normalize_face_bundle_extra(out)
    return out


def _resolve_face_bundle_file_path(raw: str) -> str:
    p = (raw or "").strip()
    if not p:
        return ""
    if os.path.isabs(p):
        return p
    from deskbot_server.paths import PROJECT_ROOT

    return str((PROJECT_ROOT / p).resolve())


def load_pb_face_bundle_file(path: str) -> dict[str, Any]:
    """读取 YAML 或 JSON 覆盖层（非完整 ``mouth_by_phoneme`` 也可，只写要改的键）。"""
    path = _resolve_face_bundle_file_path(path)
    with open(path, encoding="utf-8") as f:
        if path.lower().endswith(".json"):
            data = json.load(f)
        else:
            data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def ensure_pb_face_bundle_shape(data: dict[str, Any]) -> dict[str, Any]:
    """补全独立 JSON 配置中缺失的键，保证 ``phoneme_seq_to_anim_seq`` 可用。

    - 当 **既无** ``mouth_by_phoneme`` 内容 **又无** 有效的 ``mouth_by_phoneme_groups`` 时，用 ``default_pb_face_bundle`` 的整表口型。
    - 否则将 ``mouth_by_phoneme_groups`` 与 ``mouth_by_phoneme`` 一并展开后写入 **扁平** ``mouth_by_phoneme``，然后 **删除** ``mouth_by_phoneme_groups``（运行时只保留展开结果）。
    - ``eye_l_groups`` / ``eye_r_groups`` / ``nose_groups`` 在规范化眼、鼻时展开进 ``eye_*`` / ``nose`` 后 **删除**（与口型组条一致）。
    - ``extra_groups`` 展开进 ``extra`` 后删除。
    - 缺 ``"_"`` 时补上默认口型。
    - ``eye_l`` / ``eye_r`` / ``nose`` / ``metadata`` / ``extra`` 缺失时用默认整包对应字段。
    """
    d = copy.deepcopy(data) if isinstance(data, dict) else {}
    fb0 = default_pb_face_bundle()
    mb = d.get("mouth_by_phoneme")
    if not isinstance(mb, dict):
        mb = {}
    gr = d.get("mouth_by_phoneme_groups")
    groups_list = gr if isinstance(gr, list) else None
    has_valid_groups = bool(
        groups_list
        and any(is_mouth_phoneme_group_entry(x) for x in groups_list)
    )
    if not mb and not has_valid_groups:
        d["mouth_by_phoneme"] = copy.deepcopy(fb0["mouth_by_phoneme"])
    else:
        flat = expand_mouth_by_phoneme(mb, groups_list)
        fb_m = fb0["mouth_by_phoneme"]
        for ph in enumerate_zh_phonemes():
            if ph not in flat:
                if isinstance(fb_m, dict) and ph in fb_m:
                    flat[ph] = copy.deepcopy(_normalize_mouth_entry(fb_m[ph]))
                else:
                    flat[ph] = copy.deepcopy(_default_mouth_fallback_shape())
        if "_" not in flat:
            flat["_"] = copy.deepcopy(
                _normalize_mouth_entry(
                    fb_m.get("_") if isinstance(fb_m, dict) else _default_mouth_fallback_shape()
                )
            )
        d["mouth_by_phoneme"] = flat
    d.pop("mouth_by_phoneme_groups", None)
    for k in ("eye_l", "eye_r", "nose", "metadata"):
        if k not in d or d[k] is None:
            d[k] = copy.deepcopy(fb0[k])
    if "extra" not in d or d["extra"] is None:
        d["extra"] = copy.deepcopy(fb0.get("extra", {"default": []}))
    elif not isinstance(d["extra"], dict):
        d["extra"] = {"default": []}
    _normalize_face_bundle_eyes_nose(d)
    _normalize_face_bundle_extra(d)
    return d


def _read_bundle_file_raw(abs_path: str) -> dict[str, Any]:
    with open(abs_path, encoding="utf-8") as f:
        if abs_path.lower().endswith(".json"):
            doc = json.load(f)
        else:
            doc = yaml.safe_load(f)
    return doc if isinstance(doc, dict) else {}


def hot_reload_pb_face_bundle_file(path_raw: str) -> dict[str, Any]:
    """按路径读取配置；若文件 ``mtime`` 变化则重新解析并打日志（热切换）。"""
    ap = _resolve_face_bundle_file_path(path_raw)
    try:
        st = os.stat(ap)
        mtime = float(st.st_mtime)
    except OSError as e:
        _logger.warning("pb_face_bundle 文件不可读 %r: %s", ap, e)
        raise
    hit = _hot_file_cache.get(ap)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    doc = _read_bundle_file_raw(ap)
    _hot_file_cache[ap] = (mtime, doc)
    _logger.info("[pb_face_bundle] 热加载 %s (mtime=%s)", ap, mtime)
    return doc


def load_pb_face_bundle_json_document(path_raw: str) -> dict[str, Any]:
    """读取完整/部分 pb 脸 JSON（或 YAML），并 ``ensure_pb_face_bundle_shape``。

    同一文件 ``mtime`` 不变时复用内存中的已补全结果。
    """
    ap = _resolve_face_bundle_file_path(path_raw)
    try:
        mtime = float(os.stat(ap).st_mtime)
    except OSError:
        raise
    hit = _ensured_json_bundle_cache.get(ap)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    doc = hot_reload_pb_face_bundle_file(path_raw)
    out = ensure_pb_face_bundle_shape(doc)
    _ensured_json_bundle_cache[ap] = (mtime, out)
    return out


def resolve_pb_face_bundle(
    tts_cfg: dict[str, Any] | None, *, device_id: str | None = None
) -> dict[str, Any]:
    """根据 ``tts`` 配置与环境变量选择卡通脸数据包。

    - 默认：``data/deskbot-face.json``（``emotions`` + ``phonemes``，按 mtime 热重载）。
    - ``pb_face_bundle_json`` / ``DESKBOT_PB_FACE_BUNDLE_JSON``：可选外部 JSON/YAML 主配置（高级用法）。
    - ``pb_face_bundle`` / ``DESKBOT_PB_FACE_BUNDLE``：``demo`` 为内置试玩档；否则走 default 表情。
    - ``pb_face_bundle_file`` / ``DESKBOT_PB_FACE_BUNDLE_FILE``：在 base 上再合并一层 overlay。
    """
    cfg = tts_cfg or {}
    json_path = str(
        cfg.get("pb_face_bundle_json")
        or os.environ.get("DESKBOT_PB_FACE_BUNDLE_JSON", "")
        or ""
    ).strip()

    fpath = str(
        cfg.get("pb_face_bundle_file")
        or os.environ.get("DESKBOT_PB_FACE_BUNDLE_FILE", "")
        or ""
    ).strip()

    if json_path:
        try:
            base = load_pb_face_bundle_json_document(json_path)
        except OSError as e:
            _logger.warning("读取 pb_face_bundle_json=%r 失败: %s，回退 default 表情", json_path, e)
            base = None
        if base is not None:
            if fpath:
                try:
                    overlay = hot_reload_pb_face_bundle_file(fpath)
                    return merge_pb_face_bundle(base, overlay)
                except OSError as e:
                    _logger.warning("读取 pb_face_bundle_file=%r 失败: %s", fpath, e)
            return base

    prof = str(
        cfg.get("pb_face_bundle")
        or os.environ.get("DESKBOT_PB_FACE_BUNDLE", "")
        or "default"
    ).strip().lower()
    if prof == "demo":
        base = demo_pb_face_bundle()
    else:
        if prof not in ("", "default", "0", "false", "off"):
            _logger.warning("未知 pb_face_bundle=%r，使用 default 表情", prof)
        base = load_expr_default_pb_face_bundle(device_id=device_id)

    if not fpath:
        return base
    try:
        overlay = hot_reload_pb_face_bundle_file(fpath)
        return merge_pb_face_bundle(base, overlay)
    except OSError as e:
        _logger.warning("读取 pb_face_bundle_file=%r 失败: %s", fpath, e)
        return base
