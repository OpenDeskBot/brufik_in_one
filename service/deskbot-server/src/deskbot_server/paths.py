"""项目根目录与静态资源路径（src 布局下统一由此解析）。"""
from __future__ import annotations

from pathlib import Path

# deskbot-server/ 目录（含 config.yaml、data/、models/）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
MODELS_DIR = PROJECT_ROOT / "models"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
ENV_FILE = PROJECT_ROOT / ".env"
