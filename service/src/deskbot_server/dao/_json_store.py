"""JSON 文件存储工具：统一的文件读写、原子写入、文件锁与错误处理。

供各 store 模块复用，消除重复的 load_json / save_json 样板代码。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger("deskbot-server")

# 按文件路径分配锁，避免不同文件间的不必要竞争
_path_locks: dict[str, threading.Lock] = {}
_path_locks_lock = threading.Lock()


def _get_lock(path: str) -> threading.Lock:
    """获取与文件路径关联的 Lock。"""
    resolved = str(Path(path).resolve())
    with _path_locks_lock:
        lock = _path_locks.get(resolved)
        if lock is None:
            lock = threading.Lock()
            _path_locks[resolved] = lock
        return lock


@contextmanager
def file_lock(path: str):
    """文件级互斥锁上下文管理器，用于保护 load-modify-save 序列。

    Usage::

        with file_lock(path):
            data = load_json_file(path) or {}
            data["key"] = "value"
            save_json_file(path, data)
    """
    with _get_lock(path):
        yield


def load_json_file(path: str | Path, *, default: Any = None) -> dict[str, Any] | None:
    """读取 JSON 文件，缺失或损坏返回 default。

    统一错误处理：文件不存在 → default；JSON 解析失败 → 日志警告 + default。
    """
    p = str(path)
    if not os.path.isfile(p):
        return default
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[json_store] 读取失败 path=%s: %s", p, exc)
        return default
    return raw if isinstance(raw, dict) else default


def save_json_file(path: str | Path, data: Any) -> None:
    """写入 JSON 文件（自动创建父目录，尾部换行）。"""
    p = str(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def atomic_save_json(path: str | Path, data: Any) -> None:
    """原子写入 JSON 文件（先写临时文件再 rename，避免写入中断导致文件损坏）。"""
    p = str(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(p), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, p)
    except BaseException:
        # 写入失败时清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def new_short_id(length: int = 16) -> str:
    """生成短 UUID（默认 16 位 hex）。"""
    import uuid

    return uuid.uuid4().hex[:length]
