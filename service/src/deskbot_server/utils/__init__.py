"""通用工具：路径、环境、日志与杂项辅助。"""

from deskbot_server.utils.async_helpers import run_blocking, spawn
from deskbot_server.utils.env import load_dotenv
from deskbot_server.utils.paths import DATA_DIR, DEFAULT_CONFIG_PATH, ENV_FILE, MODELS_DIR, PROJECT_ROOT, PROMPTS_DIR
from deskbot_server.utils.singleton import SingletonMeta

__all__ = [
    "DATA_DIR",
    "DEFAULT_CONFIG_PATH",
    "ENV_FILE",
    "MODELS_DIR",
    "PROJECT_ROOT",
    "PROMPTS_DIR",
    "SingletonMeta",
    "load_dotenv",
    "run_blocking",
    "spawn",
]


def __getattr__(name: str):
    # 延迟导入，避免 constants ↔ logging_setup 环依赖
    if name == "setup_logging":
        from deskbot_server.utils.logging_setup import setup_logging

        return setup_logging
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
