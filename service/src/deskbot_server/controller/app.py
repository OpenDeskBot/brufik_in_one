"""FastAPI 应用工厂。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from deskbot_server.controller.device_controller import router as device_router
from deskbot_server.controller.flash_controller import router as flash_router
from deskbot_server.controller.runtime import AppRuntime, set_runtime
from deskbot_server.controller.web_controller import router as web_router
from deskbot_server.web.mount import mount_web

logger = logging.getLogger("deskbot-server")


def create_fastapi_app(runtime: AppRuntime | None = None, *, web_only: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if runtime is not None:
            set_runtime(runtime)
            logger.info("FastAPI started ws_path=%s", runtime.ws_path)
        try:
            yield
        finally:
            if runtime is not None and runtime.scheduler is not None:
                stop = getattr(runtime.scheduler, "stop", None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:
                        logger.exception("scheduler.stop failed")
            try:
                from deskbot_server.service.camera_face_service import CameraFaceService

                CameraFaceService.shutdown_pool()
            except Exception:
                logger.exception("CameraFaceService.shutdown_pool failed")
            logger.info("FastAPI stopped")

    app = FastAPI(title="deskbot-server", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Deskbot-Web-Token", "X-Deskbot-Debug-Token"],
    )

    mount_web(app)
    app.include_router(flash_router)
    if runtime is not None and not web_only:
        app.include_router(device_router)
        app.include_router(web_router)
        ws_path = runtime.ws_path if runtime.ws_path.startswith("/") else f"/{runtime.ws_path}"
        if ws_path != "/asr_chat":
            from deskbot_server.controller.device_controller import asr_chat

            app.add_api_websocket_route(ws_path, asr_chat)
        app.state.runtime = runtime
    return app
