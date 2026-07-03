"""核心层：配置、类型、端口（Protocol）定义，不依赖 WebSocket / FunASR / Flask。"""

from deskbot_server.core.settings import AppSettings

__all__ = ["AppSettings"]
