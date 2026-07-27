"""按设备隔离 ``data/{device_id}_{pin_code}/`` 下的配置与模板文件。"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from deskbot_server.utils.paths import DATA_DIR
from deskbot_server.ws.device_pin import device_storage_dirname, resolve_pin_for_storage, validate_pin_code

logger = logging.getLogger("deskbot-server")

LLM_SYSTEM_FILENAME = "llm_system.txt"
_DEVICE_ID_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
_STORAGE_DIR_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")

# 所有设备共用 ``data/global/``，不再复制到设备目录。
SHARED_CONFIG_NAMES = frozenset({"deskbot-face.json", "camera_face.json", LLM_SYSTEM_FILENAME})


def _normalize_device_id(device_id: Optional[str]) -> str:
    return str(device_id or "").strip()


def global_config_dir() -> Path:
    return DATA_DIR / "global"


def is_shared_config_basename(name: str) -> bool:
    return name in SHARED_CONFIG_NAMES


def device_data_dir(device_id: str, pin_code: Optional[str] = None) -> Path:
    did = _normalize_device_id(device_id)
    if not did:
        raise ValueError("device_id required")
    if not _DEVICE_ID_SAFE.match(did):
        raise ValueError(f"invalid device_id: {did!r}")
    pin = str(pin_code or "").strip() if pin_code is not None else (resolve_pin_for_storage(did) or "")
    if not validate_pin_code(pin):
        raise ValueError(f"pin_code required for device data dir device_id={did!r}")
    dirname = device_storage_dirname(did, pin)
    if not _STORAGE_DIR_SAFE.match(dirname):
        raise ValueError(f"invalid device storage dirname: {dirname!r}")
    return DATA_DIR / dirname


def global_llm_system_path() -> Path:
    return global_config_dir() / LLM_SYSTEM_FILENAME


def device_llm_system_path(device_id: str) -> Path:
    """历史兼容：设备级 llm 已废弃，统一读 ``data/global/llm_system.txt``。"""
    return global_llm_system_path()


def list_data_json_files() -> list[Path]:
    """``data/`` 根目录下的 JSON 模板（不含子目录与 ``data/global/``）。"""
    return sorted(p for p in DATA_DIR.glob("*.json") if p.is_file() and not is_shared_config_basename(p.name))


def list_data_seed_files() -> list[Path]:
    """设备目录初始化时从 ``data/`` 复制的模板（不含 ``data/global/`` 共用项）。"""
    return list_data_json_files()


def resolve_json_path(global_path: str, device_id: Optional[str] = None, pin_code: Optional[str] = None) -> str:
    """共用配置解析到 ``data/global/``；其余有 ``device_id`` 时解析到 ``data/{id}_{pin}/``。"""
    base = os.path.basename(global_path)
    if is_shared_config_basename(base):
        return str(global_config_dir() / base)
    did = _normalize_device_id(device_id)
    if not did:
        return global_path
    return str(device_data_dir(did, pin_code) / base)


def load_llm_system_prompt(device_id: Optional[str] = None) -> str:
    """读取 LLM system prompt：统一 ``data/global/llm_system.txt``，再回退 config。"""
    del device_id
    global_path = global_llm_system_path()
    if global_path.is_file():
        return global_path.read_text(encoding="utf-8").strip()
    from deskbot_server.config import load_config

    cfg = load_config()
    return str((cfg.get("llm") or {}).get("system_prompt") or "").strip()


def save_llm_system_prompt(content: str, *, device_id: str = "") -> Path:
    """保存共用 LLM system prompt 到 ``data/global/llm_system.txt``。"""
    del device_id
    gdir = global_config_dir()
    gdir.mkdir(parents=True, exist_ok=True)
    path = global_llm_system_path()
    text = (content or "").strip()
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")
    return path


def ensure_device_data_initialized(device_id: str, pin_code: str) -> bool:
    """创建设备目录并从 ``data/`` 复制缺失模板；返回是否新复制了文件。"""
    did = _normalize_device_id(device_id)
    pin = str(pin_code or "").strip()
    if not did or not validate_pin_code(pin):
        return False
    ddir = device_data_dir(did, pin)
    ddir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for src in list_data_seed_files():
        dst = ddir / src.name
        if dst.exists():
            continue
        import shutil

        shutil.copy2(src, dst)
        copied += 1
    if copied:
        logger.info("[device_data] 初始化 device_id=%s pin=%s dir=%s copied=%d", did, pin, ddir, copied)
    return copied > 0
