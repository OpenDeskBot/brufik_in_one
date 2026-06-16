"""配置加载：YAML + LLM system prompt 外置文件。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from deskbot_server.paths import DEFAULT_CONFIG_PATH


def load_config(path: str | None = None) -> dict[str, Any]:
    config_path = Path(path or DEFAULT_CONFIG_PATH).resolve()
    with config_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    _resolve_llm_system_prompt(cfg, config_path.parent)
    return cfg


def resolve_llm_system_prompt(llm_cfg: dict[str, Any], config_dir: str | Path) -> str:
    """从 llm 配置段解析 system prompt（文件优先于内联）。"""
    if not isinstance(llm_cfg, dict):
        return ""
    file_ref = llm_cfg.get("system_prompt_file")
    if file_ref:
        p = Path(file_ref)
        if not p.is_absolute():
            p = Path(config_dir) / p
        return p.read_text(encoding="utf-8").strip()
    raw = llm_cfg.get("system_prompt")
    return str(raw).strip() if raw else ""


def _resolve_llm_system_prompt(cfg: dict[str, Any], config_dir: Path) -> None:
    llm = cfg.get("llm")
    if not isinstance(llm, dict):
        return
    if llm.get("system_prompt_file"):
        llm["system_prompt"] = resolve_llm_system_prompt(llm, config_dir)


def save_config(cfg: dict[str, Any], path: str | Path | None = None) -> None:
    """写回 ``config.yaml``（会丢失 YAML 注释，与调试页其它保存行为一致）。"""
    config_path = Path(path or DEFAULT_CONFIG_PATH).resolve()
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
