"""将 ``face_bundle`` 写成易读的 JSON：所有图元数组内每项单行，结构适度折叠。"""

from __future__ import annotations

import json
from typing import Any

from deskbot_server.pb.anim_defaults import (
    is_extra_elements_group_entry,
    is_eye_elements_group_entry,
    is_mouth_phoneme_group_entry,
    is_nose_elements_group_entry,
)


def _prim_line(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))


def _fmt_mouth_entry_lines(entry: Any) -> list[str]:
    """口型对象：``elements`` 每项一行 + 单行 ``offset``。"""
    if not isinstance(entry, dict):
        entry = {}
    els = entry.get("elements")
    if not isinstance(els, list):
        els = []
    off = entry.get("offset") if isinstance(entry.get("offset"), dict) else {}
    try:
        ox, oy = int(off.get("x", 0)), int(off.get("y", 0))
    except (TypeError, ValueError):
        ox, oy = 0, 0
    lines: list[str] = ['      "elements": [']
    for i, p in enumerate(els):
        if not isinstance(p, dict):
            p = {}
        c = "," if i < len(els) - 1 else ""
        lines.append(f"        {_prim_line(p)}{c}")
    lines.append("      ],")
    lines.append(f'      "offset": {{"x": {ox}, "y": {oy}}}')
    return lines


def _fmt_mouth_group_lines(entry: dict[str, Any]) -> list[str]:
    """口型共享条：``states`` + ``elements`` + ``offset``。"""
    st = entry.get("states")
    if not isinstance(st, list):
        st = []
    st_json = json.dumps([str(x) for x in st], ensure_ascii=False, separators=(", ", ": "))
    lines: list[str] = [f'      "states": {st_json},']
    els = entry.get("elements")
    if not isinstance(els, list):
        els = []
    lines.append('      "elements": [')
    for i, p in enumerate(els):
        if not isinstance(p, dict):
            p = {}
        c = "," if i < len(els) - 1 else ""
        lines.append(f"        {_prim_line(p)}{c}")
    lines.append("      ],")
    off = entry.get("offset") if isinstance(entry.get("offset"), dict) else {}
    try:
        ox, oy = int(off.get("x", 0)), int(off.get("y", 0))
    except (TypeError, ValueError):
        ox, oy = 0, 0
    lines.append(f'      "offset": {{"x": {ox}, "y": {oy}}}')
    return lines


def _fmt_elements_group_lines(entry: dict[str, Any]) -> list[str]:
    """眼/鼻共享条：``states`` + ``elements``（无 ``offset``）。"""
    st = entry.get("states")
    if not isinstance(st, list):
        st = []
    st_json = json.dumps([str(x) for x in st], ensure_ascii=False, separators=(", ", ": "))
    lines: list[str] = [f'      "states": {st_json},']
    els = entry.get("elements")
    if not isinstance(els, list):
        els = []
    lines.append('      "elements": [')
    for i, p in enumerate(els):
        if not isinstance(p, dict):
            p = {}
        c = "," if i < len(els) - 1 else ""
        lines.append(f"        {_prim_line(p)}{c}")
    lines.append("      ]")
    return lines


def _fmt_mouth_groups_array(valid_groups: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ['  "mouth_by_phoneme_groups": [']
    for i, g in enumerate(valid_groups):
        lines.append("    {")
        lines.extend(_fmt_mouth_group_lines(g))
        lines.append("    }" + ("," if i < len(valid_groups) - 1 else ""))
    lines.append("  ],")
    return lines


def _fmt_eye_groups_array(side: str, valid_groups: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = [f'  "{side}_groups": [']
    for i, g in enumerate(valid_groups):
        lines.append("    {")
        lines.extend(_fmt_elements_group_lines(g))
        lines.append("    }" + ("," if i < len(valid_groups) - 1 else ""))
    lines.append("  ],")
    return lines


def _fmt_nose_groups_array(valid_groups: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ['  "nose_groups": [']
    for i, g in enumerate(valid_groups):
        lines.append("    {")
        lines.extend(_fmt_elements_group_lines(g))
        lines.append("    }" + ("," if i < len(valid_groups) - 1 else ""))
    lines.append("  ],")
    return lines


def _fmt_extra_groups_array(valid_groups: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = ['  "extra_groups": [']
    for i, g in enumerate(valid_groups):
        lines.append("    {")
        lines.extend(_fmt_elements_group_lines(g))
        lines.append("    }" + ("," if i < len(valid_groups) - 1 else ""))
    lines.append("  ],")
    return lines


def _fmt_primitive_key_lines(key: str, prims: Any, indent_inner: str) -> list[str]:
    if not isinstance(prims, list):
        prims = []
    lines = [f'{indent_inner}"{key}": [']
    pad = indent_inner + "  "
    for i, p in enumerate(prims):
        if not isinstance(p, dict):
            p = {}
        c = "," if i < len(prims) - 1 else ""
        lines.append(f"{pad}{_prim_line(p)}{c}")
    lines.append(f"{indent_inner}]")
    return lines


_FMT_TOP_KEYS = (
    "mouth_by_phoneme_groups",
    "mouth_by_phoneme",
    "eye_l_groups",
    "eye_l",
    "eye_r_groups",
    "eye_r",
    "nose_groups",
    "nose",
    "extra_groups",
    "extra",
    "metadata",
)


def format_face_bundle_json_lines(data: dict[str, Any]) -> list[str]:
    """生成行列表（无首尾换行聚合）。"""
    lines: list[str] = ["{"]

    mg = data.get("mouth_by_phoneme_groups")
    if isinstance(mg, list) and mg:
        valid_mg = [g for g in mg if isinstance(g, dict) and is_mouth_phoneme_group_entry(g)]
        if valid_mg:
            lines.extend(_fmt_mouth_groups_array(valid_mg))

    mb = data.get("mouth_by_phoneme")
    if not isinstance(mb, dict):
        mb = {}
    ph_keys = list(mb.keys())
    if not ph_keys:
        lines.append('  "mouth_by_phoneme": {},')
    else:
        lines.append('  "mouth_by_phoneme": {')
        for i, ph in enumerate(ph_keys):
            pk = json.dumps(ph, ensure_ascii=False)
            lines.append(f"    {pk}: {{")
            lines.extend(_fmt_mouth_entry_lines(mb[ph]))
            lines.append("    }" + ("," if i < len(ph_keys) - 1 else ""))
        lines.append("  },")

    for side in ("eye_l", "eye_r"):
        gk = f"{side}_groups"
        raw_g = data.get(gk)
        if isinstance(raw_g, list) and raw_g:
            valid_eg = [g for g in raw_g if isinstance(g, dict) and is_eye_elements_group_entry(g)]
            if valid_eg:
                lines.extend(_fmt_eye_groups_array(side, valid_eg))
        part = data.get(side) if isinstance(data.get(side), dict) else {}
        keys_in = [k for k in ("default", "open", "close") if isinstance(part.get(k), list)]
        if not keys_in:
            lines.append(f'  "{side}": {{}},')
        else:
            lines.append(f'  "{side}": {{')
            for ki, k in enumerate(keys_in):
                block = _fmt_primitive_key_lines(k, part.get(k), "    ")
                if ki < len(keys_in) - 1:
                    block[-1] = block[-1] + ","
                lines.extend(block)
            lines.append("  },")

    ng_raw = data.get("nose_groups")
    has_nose_groups = False
    if isinstance(ng_raw, list) and ng_raw:
        valid_ng = [g for g in ng_raw if isinstance(g, dict) and is_nose_elements_group_entry(g)]
        if valid_ng:
            lines.extend(_fmt_nose_groups_array(valid_ng))
            has_nose_groups = True

    nose = data.get("nose") if isinstance(data.get("nose"), dict) else {}
    nd = nose.get("default")
    if isinstance(nd, list) and nd:
        lines.append('  "nose": {')
        lines.extend(_fmt_primitive_key_lines("default", nd, "    "))
        lines.append("  },")
    elif has_nose_groups:
        lines.append('  "nose": {},')
    else:
        lines.append('  "nose": {"default": []},')

    exg_raw = data.get("extra_groups")
    has_extra_groups = False
    if isinstance(exg_raw, list) and exg_raw:
        valid_exg = [
            g for g in exg_raw if isinstance(g, dict) and is_extra_elements_group_entry(g)
        ]
        if valid_exg:
            lines.extend(_fmt_extra_groups_array(valid_exg))
            has_extra_groups = True

    extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
    ex_keys = sorted(
        k for k in extra if not str(k).startswith("_") and isinstance(extra[k], list)
    )
    if ex_keys:
        lines.append('  "extra": {')
        for ki, ek in enumerate(ex_keys):
            block = _fmt_primitive_key_lines(ek, extra[ek], "    ")
            if ki < len(ex_keys) - 1:
                block[-1] = block[-1] + ","
            lines.extend(block)
        lines.append("  },")
    elif has_extra_groups:
        lines.append('  "extra": {},')
    else:
        lines.append('  "extra": {"default": []},')

    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    lines.append(
        f'  "metadata": {json.dumps(meta, ensure_ascii=False, separators=(", ", ": "))}'
    )

    extra_keys = [k for k in data if k not in _FMT_TOP_KEYS]
    for ek in extra_keys:
        lines[-1] = lines[-1] + ","
        v = data[ek]
        lines.append(f"  {json.dumps(ek)}: {json.dumps(v, ensure_ascii=False)}")

    lines.append("}")
    return lines


def write_face_bundle_json(path: str, data: dict[str, Any]) -> None:
    text = "\n".join(format_face_bundle_json_lines(data)) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
