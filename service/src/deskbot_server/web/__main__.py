"""python -m deskbot_server.web — FastAPI Web 控制台（默认可与主服务同端口合并）。"""

from __future__ import annotations

import os

from deskbot_server.utils.env import load_dotenv
from deskbot_server.utils.logging_setup import setup_logging
from deskbot_server.web.app import app, web_debug_enabled


def main() -> None:
    load_dotenv()
    setup_logging()
    import uvicorn

    host = (os.environ.get("DESKBOT_WEB_HOST") or "0.0.0.0").strip()
    port = int(os.environ.get("DESKBOT_WEB_PORT") or "5050")
    uvicorn.run(app, host=host, port=port, log_level="debug" if web_debug_enabled() else "info")


if __name__ == "__main__":
    main()
