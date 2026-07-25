"""Controller：FastAPI 应用与扁平路由。"""

from deskbot_server.controller.app import create_fastapi_app
from deskbot_server.controller.runtime import AppRuntime, get_runtime, set_runtime

__all__ = ["AppRuntime", "create_fastapi_app", "get_runtime", "set_runtime"]
