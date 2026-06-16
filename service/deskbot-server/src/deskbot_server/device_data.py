"""按设备隔离 ``data/device/{device_id}/`` 下的配置与模板文件。"""
from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from deskbot_server.paths import DATA_DIR

logger = logging.getLogger("deskbot-server")

DEVICE_DATA_ROOT = DATA_DIR / "device"
LLM_SYSTEM_FILENAME = "llm_system.txt"
_DEVICE_ID_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def _normalize_device_id(device_id: Optional[str]) -> str:
    return str(device_id or "").strip()


def device_data_dir(device_id: str) -> Path:
    did = _normalize_device_id(device_id)
    if not did:
        raise ValueError("device_id required")
    if not _DEVICE_ID_SAFE.match(did):
        raise ValueError(f"invalid device_id: {did!r}")
    return DEVICE_DATA_ROOT / did


def global_llm_system_path() -> Path:
    return DATA_DIR / LLM_SYSTEM_FILENAME


def device_llm_system_path(device_id: str) -> Path:
    return device_data_dir(device_id) / LLM_SYSTEM_FILENAME


def list_data_json_files() -> list[Path]:
    """``data/`` 根目录下的 JSON 模板（不含子目录）。"""
    return sorted(p for p in DATA_DIR.glob("*.json") if p.is_file())


def list_data_seed_files() -> list[Path]:
    """设备目录初始化时从 ``data/`` 复制的模板（JSON + LLM system prompt）。"""
    files = list_data_json_files()
    llm = global_llm_system_path()
    if llm.is_file():
        files.append(llm)
    return files


def resolve_json_path(global_path: str, device_id: Optional[str] = None) -> str:
    """有 ``device_id`` 时解析到 ``data/device/{id}/``，否则保持全局路径。"""
    did = _normalize_device_id(device_id)
    if not did:
        return global_path
    base = os.path.basename(global_path)
    return str(device_data_dir(did) / base)


def ensure_device_llm_system_file(device_id: str) -> Path | None:
    """设备 ``llm_system.txt`` 缺失时从 ``data/llm_system.txt`` 复制；返回设备侧路径。"""
    did = _normalize_device_id(device_id)
    if not did:
        return None
    global_path = global_llm_system_path()
    if not global_path.is_file():
        return None
    path = device_llm_system_path(did)
    if path.is_file():
        return path
    ddir = device_data_dir(did)
    ddir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(global_path, path)
    logger.info(
        "[device_data] 复制 llm_system.txt device_id=%s src=%s",
        did,
        global_path,
    )
    return path


def load_llm_system_prompt(device_id: Optional[str] = None) -> str:
    """读取 LLM system prompt：设备目录优先，否则 ``data/llm_system.txt``，再回退 config。"""
    did = _normalize_device_id(device_id)
    if did:
        path = ensure_device_llm_system_file(did) or device_llm_system_path(did)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    global_path = global_llm_system_path()
    if global_path.is_file():
        return global_path.read_text(encoding="utf-8").strip()
    from deskbot_server.config import load_config

    cfg = load_config()
    return str((cfg.get("llm") or {}).get("system_prompt") or "").strip()


def save_llm_system_prompt(content: str, *, device_id: str) -> Path:
    """保存设备级 LLM system prompt 到 ``data/device/{device_id}/llm_system.txt``。"""
    ddir = device_data_dir(device_id)
    ddir.mkdir(parents=True, exist_ok=True)
    path = ddir / LLM_SYSTEM_FILENAME
    text = (content or "").strip()
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")
    return path


def ensure_device_data_initialized(device_id: str) -> bool:
    """创建设备目录并从 ``data/`` 复制缺失模板；返回是否新复制了文件。"""
    did = _normalize_device_id(device_id)
    if not did:
        return False
    ddir = device_data_dir(did)
    ddir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in list_data_seed_files():
        dst = ddir / src.name
        if dst.exists():
            continue
        shutil.copy2(src, dst)
        copied += 1
    if copied:
        logger.info(
            "[device_data] 初始化 device_id=%s dir=%s copied=%d",
            did,
            ddir,
            copied,
        )
    return copied > 0
