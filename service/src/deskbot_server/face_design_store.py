"""统一面部设计文件（``deskbot-face.json``：phonemes + emotions）加载与查表。"""

from __future__ import annotations

import copy
import json
import os
from typing import Any, Optional

from deskbot_server.constants import FACE_DESIGN_FILE
from deskbot_server.device_data import resolve_json_path
from deskbot_server.face_expr_scenes_store import normalize_design_scene
from deskbot_server.pb.shapes import simplify_phoneme_key

_design_cache: tuple[str, float, dict[str, Any] | None] | None = None


def resolve_face_design_path(*, device_id: Optional[str] = None) -> str:
    """所有设备共用 ``data/global/deskbot-face.json``。"""
    del device_id
    return resolve_json_path(FACE_DESIGN_FILE, None)


def _normalize_expression(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("expression must be an object")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("expression.name required")
    alias_raw = raw.get("alias")
    alias = [str(x).strip() for x in alias_raw if str(x).strip()] if isinstance(alias_raw, list) else []
    title = str(raw.get("title") or name).strip()
    scene = normalize_design_scene({**raw, "name": name, "title": title})
    scene["alias"] = alias
    return scene


def normalize_face_design_doc(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("face design must be a JSON object")
    phonemes_raw = raw.get("phonemes")
    if phonemes_raw is None and isinstance(raw.get("phoneme_expressions"), list):
        phonemes_raw = raw.get("phoneme_expressions")
    emotions_raw = raw.get("emotions")
    if emotions_raw is None and isinstance(raw.get("emotion_expressions"), list):
        emotions_raw = raw.get("emotion_expressions")
    if not isinstance(phonemes_raw, list) or not isinstance(emotions_raw, list):
        raise ValueError("face design requires phonemes[] and emotions[]")
    return {
        "name": str(raw.get("name") or "deskbot-face").strip(),
        "description": str(raw.get("description") or "").strip(),
        "phonemes": [_normalize_expression(x) for x in phonemes_raw],
        "emotions": [_normalize_expression(x) for x in emotions_raw],
    }


def load_face_design_file(
    *, seed_if_missing: bool = False, device_id: Optional[str] = None
) -> Optional[dict[str, Any]]:
    del seed_if_missing
    path = resolve_face_design_path(device_id=device_id)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return normalize_face_design_doc(raw)


def ensure_face_design_file(*, device_id: Optional[str] = None) -> dict[str, Any]:
    """加载 ``data/global/deskbot-face.json``。"""
    del device_id
    doc = load_face_design_file()
    if doc is not None:
        return doc
    path = resolve_face_design_path()
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"缺少 {os.path.basename(FACE_DESIGN_FILE)}，请在 data/global/ 下提供全局模板"
        )
    with open(path, encoding="utf-8") as f:
        return normalize_face_design_doc(json.load(f))


def save_face_design_file(
    doc: dict[str, Any], *, device_id: Optional[str] = None
) -> dict[str, Any]:
    norm = normalize_face_design_doc(doc)
    path = resolve_face_design_path(device_id=device_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
        f.write("\n")
    clear_face_design_cache()
    return norm


def clear_face_design_cache() -> None:
    global _design_cache
    _design_cache = None


def design_file_mtime(*, device_id: Optional[str] = None) -> float:
    path = resolve_face_design_path(device_id=device_id)
    try:
        return float(os.stat(path).st_mtime)
    except OSError:
        return 0.0


def _load_face_design_cached(*, device_id: Optional[str] = None) -> dict[str, Any] | None:
    global _design_cache
    path = resolve_face_design_path(device_id=device_id)
    try:
        mtime = float(os.stat(path).st_mtime)
    except OSError:
        return None
    key = str(device_id or "")
    if _design_cache is not None and _design_cache[0] == key and _design_cache[1] == mtime:
        return _design_cache[2]
    doc = load_face_design_file(device_id=device_id)
    _design_cache = (key, mtime, doc)
    return doc


def expression_match_keys(expr: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    name = str(expr.get("name") or "").strip()
    if name:
        keys.append(name)
    for raw in expr.get("alias") or []:
        s = str(raw).strip()
        if s and s not in keys:
            keys.append(s)
    return keys


def find_design_expression(
    expressions: list[dict[str, Any]], key: str, *, phoneme: bool = True
) -> Optional[dict[str, Any]]:
    want = simplify_phoneme_key(str(key or "").strip()) if phoneme else str(key or "").strip().lower()
    if not want:
        return None
    for expr in expressions or []:
        for cand in expression_match_keys(expr):
            norm = simplify_phoneme_key(cand) if phoneme else str(cand).strip().lower()
            if norm == want:
                return expr
    return None


def find_phoneme_expression(
    doc: dict[str, Any] | None, phoneme: str
) -> Optional[dict[str, Any]]:
    if not isinstance(doc, dict):
        return None
    return find_design_expression(doc.get("phonemes") or [], phoneme, phoneme=True)


def find_emotion_expression(doc: dict[str, Any] | None, name: str) -> Optional[dict[str, Any]]:
    if not isinstance(doc, dict):
        return None
    return find_design_expression(merged_emotion_expressions(doc), name, phoneme=False)


def pick_expression_elements(expr: dict[str, Any] | None, *, at_ms: int = 0) -> dict[str, Any]:
    """按表达式内帧时间轴取 ``elements``（默认首帧）。"""
    if not isinstance(expr, dict):
        return {}
    frames = expr.get("frames")
    if not isinstance(frames, list) or not frames:
        return {}
    t = max(0, int(at_ms or 0))
    cursor = 0
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        dur = max(1, int(fr.get("ms") or 800))
        if t < cursor + dur:
            els = fr.get("elements")
            return els if isinstance(els, dict) else {}
        cursor += dur
    last = frames[-1]
    if isinstance(last, dict):
        els = last.get("elements")
        return els if isinstance(els, dict) else {}
    return {}


def merged_emotion_expressions(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    """文件 ``emotions`` + 未被覆盖的内置情绪（``builtin_emotion_scenes``）。"""
    from deskbot_server.face_expr_scenes_store import builtin_emotion_scenes

    file_exprs: list[dict[str, Any]] = []
    if isinstance(doc, dict):
        for expr in doc.get("emotions") or []:
            if isinstance(expr, dict) and str(expr.get("name") or "").strip():
                file_exprs.append(expr)
    seen = {str(e.get("name") or "").strip().lower() for e in file_exprs}
    out = list(file_exprs)
    for raw in builtin_emotion_scenes():
        name = str(raw.get("name") or "").strip()
        if name and name.lower() not in seen:
            out.append(raw)
            seen.add(name.lower())
    return out


def emotions_as_scenes(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    """``emotions`` → ``face_expr_scenes`` 兼容列表（保留 alias 供查表）。"""
    if not isinstance(doc, dict):
        return []
    out: list[dict[str, Any]] = []
    for expr in merged_emotion_expressions(doc):
        if not isinstance(expr, dict):
            continue
        row = {
            "name": str(expr.get("name") or "").strip(),
            "title": str(expr.get("title") or expr.get("name") or "").strip(),
            "frames": expr.get("frames") or [],
        }
        alias = expr.get("alias")
        if isinstance(alias, list) and alias:
            row["alias"] = alias
        out.append(row)
    return out


def phonemes_to_mouth_groups(doc: dict[str, Any] | None) -> list[dict[str, Any]]:
    """``phonemes`` → 调试台口型组视图（仅 mouth 图元）。"""
    from deskbot_server.face_mouth_config_store import collapse_simplified_groups, simplify_group_states

    if not isinstance(doc, dict):
        return []
    raw_groups: list[dict[str, Any]] = []
    for expr in doc.get("phonemes") or []:
        if not isinstance(expr, dict):
            continue
        mouth = pick_expression_elements(expr, at_ms=0).get("mouth")
        if not isinstance(mouth, list):
            mouth = []
        states = [simplify_phoneme_key(k) for k in expression_match_keys(expr)]
        states = simplify_group_states(states)
        if not states:
            continue
        raw_groups.append(
            {
                "states": states,
                "elements": copy.deepcopy(mouth),
                "offset": {"x": 0, "y": 0},
            }
        )
    return collapse_simplified_groups(raw_groups)


def apply_mouth_groups_to_design(
    doc: dict[str, Any],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    """将口型组写回 ``phonemes`` 各帧的 ``elements.mouth``。"""
    from deskbot_server.face_mouth_config_store import normalize_face_mouth_groups

    out = copy.deepcopy(doc)
    phonemes = out.get("phonemes")
    if not isinstance(phonemes, list):
        return out
    for group in normalize_face_mouth_groups(groups):
        mouth = copy.deepcopy(group.get("elements") or [])
        for st in group.get("states") or []:
            expr = find_phoneme_expression(out, str(st))
            if not isinstance(expr, dict):
                continue
            frames = expr.get("frames")
            if not isinstance(frames, list):
                continue
            for fr in frames:
                if not isinstance(fr, dict):
                    continue
                els = fr.get("elements")
                if not isinstance(els, dict):
                    fr["elements"] = {"mouth": copy.deepcopy(mouth)}
                else:
                    els["mouth"] = copy.deepcopy(mouth)
    return out


def apply_emotion_scenes_to_design(
    doc: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """将 ``face_expr_scenes`` 行写回 ``emotions``（保留 doc 元数据与其它 phonemes）。"""
    from deskbot_server.face_expr_scenes_store import normalize_design_scene

    out = copy.deepcopy(doc)
    emotions: list[dict[str, Any]] = []
    for raw in rows or []:
        scene = normalize_design_scene(raw)
        alias = raw.get("alias") if isinstance(raw, dict) else None
        if isinstance(alias, list) and alias:
            scene["alias"] = [str(x).strip() for x in alias if str(x).strip()]
        else:
            scene["alias"] = []
        emotions.append(scene)
    out["emotions"] = emotions
    return out


def _summarize_expression(expr: dict[str, Any], *, kind: str) -> dict[str, Any]:
    frames = expr.get("frames") if isinstance(expr.get("frames"), list) else []
    total_ms = 0
    for fr in frames:
        if isinstance(fr, dict):
            total_ms += max(1, int(fr.get("ms") or 800))
    alias_raw = expr.get("alias")
    alias = [str(x).strip() for x in alias_raw if str(x).strip()] if isinstance(alias_raw, list) else []
    return {
        "kind": kind,
        "name": str(expr.get("name") or "").strip(),
        "title": str(expr.get("title") or expr.get("name") or "").strip(),
        "alias": alias,
        "frames": len(frames),
        "total_ms": total_ms,
    }


def build_face_expression_catalog(*, device_id: Optional[str] = None) -> dict[str, Any]:
    """音素 + 情绪摘要列表，供调试页展示。"""
    doc = _load_face_design_cached(device_id=device_id)
    if not isinstance(doc, dict):
        doc = ensure_face_design_file(device_id=device_id)
    phonemes: list[dict[str, Any]] = []
    for expr in doc.get("phonemes") or []:
        if not isinstance(expr, dict) or not str(expr.get("name") or "").strip():
            continue
        if not isinstance(expr.get("frames"), list) or not expr.get("frames"):
            continue
        phonemes.append(_summarize_expression(expr, kind="phoneme"))
    emotions: list[dict[str, Any]] = []
    for expr in merged_emotion_expressions(doc):
        if not isinstance(expr.get("frames"), list) or not expr.get("frames"):
            continue
        emotions.append(_summarize_expression(expr, kind="emotion"))
    phonemes.sort(key=lambda x: (x["name"].lower(), x["name"]))
    emotions.sort(key=lambda x: (x["name"].lower(), x["name"]))
    return {"phonemes": phonemes, "emotions": emotions}


def resolve_face_expression(
    doc: dict[str, Any] | None, *, kind: str, name: str
) -> Optional[dict[str, Any]]:
    """按 ``kind=phoneme|emotion`` 与 ``name``（含 alias）查表情条目。"""
    k = str(kind or "").strip().lower()
    if k == "phoneme":
        return find_phoneme_expression(doc, name)
    if k in ("emotion", "emotions"):
        return find_emotion_expression(doc, name)
    return None
