"""python -m deskbot_server"""
from __future__ import annotations

import asyncio
import logging

from deskbot_server.logging_setup import setup_logging
from deskbot_server.main import main

logger = logging.getLogger("deskbot-server")


def cli() -> None:
    setup_logging()
    try:
        asyncio.run(main())
    except Exception:
        logger.exception("deskbot-server 启动失败或主循环异常退出")
        raise


if __name__ == "__main__":
    cli()
