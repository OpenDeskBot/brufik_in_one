"""设备 ``data/device/{device_id}/tmp`` 目录读写（沙箱）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from deskbot_server.device_data import device_data_dir

_TMP_DIRNAME = "tmp"
_MAX_READ_BYTES = 512_000
_MAX_WRITE_BYTES = 512_000


def device_tmp_root(device_id: str) -> Path:
    root = device_data_dir(device_id) / _TMP_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_device_tmp_path(device_id: str, rel_path: str) -> Path:
    """解析相对路径，禁止越界到 tmp 之外。"""
    rel = str(rel_path or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        raise ValueError("path 不能为空")
    parts = Path(rel).parts
    if ".." in parts:
        raise ValueError("path 不能包含 ..")
    root = device_tmp_root(device_id).resolve()
    target = (root / rel).resolve()
    root_s = str(root)
    target_s = str(target)
    if target_s != root_s and not target_s.startswith(root_s + os.sep):
        raise ValueError("path 越界")
    return target


def read_device_tmp_file(device_id: str, path: str) -> dict[str, Any]:
    target = resolve_device_tmp_path(device_id, path)
    if not target.is_file():
        raise ValueError(f"文件不存在: {path}")
    data = target.read_bytes()
    if len(data) > _MAX_READ_BYTES:
        raise ValueError(f"文件过大（>{_MAX_READ_BYTES} 字节）")
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        encoding = "utf-8-replace"
    return {
        "path": path,
        "size": len(data),
        "encoding": encoding,
        "content": text,
    }


def write_device_tmp_file(device_id: str, path: str, content: str) -> dict[str, Any]:
    target = resolve_device_tmp_path(device_id, path)
    text = str(content if content is not None else "")
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_WRITE_BYTES:
        raise ValueError(f"内容过大（>{_MAX_WRITE_BYTES} 字节）")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {"path": path, "size": len(encoded), "written": True}


def list_device_tmp_files(device_id: str, *, subpath: str = "") -> list[dict[str, Any]]:
    base = device_tmp_root(device_id)
    if subpath:
        base = resolve_device_tmp_path(device_id, subpath)
    if not base.exists():
        return []
    if base.is_file():
        return [{"path": subpath or base.name, "type": "file", "size": base.stat().st_size}]
    root = device_tmp_root(device_id)
    out: list[dict[str, Any]] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        out.append({"path": rel, "type": "file", "size": p.stat().st_size})
    return out
