from __future__ import annotations

import os

from fastapi import FastAPI

from deskbot_server.controller.app import create_fastapi_app


def web_debug_enabled() -> bool:
    raw = (os.environ.get("DESKBOT_WEB_DEBUG") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def create_app() -> FastAPI:
    """仅挂载 Web（测试 / ``python -m deskbot_server.web``）。"""
    app = create_fastapi_app(None, web_only=True)

    def test_client():
        from tests.web_test_client import WebTestClient

        return WebTestClient(app)

    app.test_client = test_client  # type: ignore[attr-defined]
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    host = (os.environ.get("DESKBOT_WEB_HOST") or "0.0.0.0").strip()
    port = int(os.environ.get("DESKBOT_WEB_PORT") or "5050")
    uvicorn.run(app, host=host, port=port, log_level="info")
