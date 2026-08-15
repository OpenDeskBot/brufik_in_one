"""将 Web 控制台挂到 FastAPI：Session、Jinja、Static、鉴权。"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from deskbot_server.db import init_database, remove_session
from deskbot_server.utils.env import load_dotenv
from deskbot_server.web.deps import load_session_user
from deskbot_server.web.urls import register_endpoint, url_for
from deskbot_server.web.view_helpers import set_templates

logger = logging.getLogger("deskbot-server")

_WEB_DIR = Path(__file__).resolve().parent
_PUBLIC_PREFIXES = ("/login", "/register", "/health", "/docs", "/openapi.json", "/redoc")


def _register_endpoints_from_modules() -> None:
    from deskbot_server.web.blueprints import app2c_bp, app_bp, auth_bp, debug_bp, flash_bp, proxy_bp, site

    for mod in (site, auth_bp, app_bp, app2c_bp, debug_bp, flash_bp, proxy_bp):
        for name, path in getattr(mod, "ENDPOINTS", {}).items():
            register_endpoint(name, path)


async def _prepare_body(request: Request) -> None:
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        request.state.form = None
        request.state.json = None
        request.state.body = b""
        return
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            request.state.json = await request.json()
        except Exception:
            request.state.json = None
        request.state.form = None
        request.state.body = b""
        return
    if "multipart/form-data" in ctype or "application/x-www-form-urlencoded" in ctype:
        try:
            request.state.form = await request.form()
        except Exception:
            request.state.form = None
        request.state.json = None
        request.state.body = b""
        return
    try:
        request.state.body = await request.body()
    except Exception:
        request.state.body = b""
    request.state.form = None
    request.state.json = None


def mount_web(app: FastAPI) -> None:
    """在已有 FastAPI app 上挂载 Web 控制台。"""
    load_dotenv()
    init_database()

    secret = (os.environ.get("DESKBOT_WEB_SECRET_KEY") or "").strip() or "dev-insecure-change-me"

    templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    templates.env.globals["url_for"] = url_for
    set_templates(templates)

    static_dir = _WEB_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    _register_endpoints_from_modules()

    from deskbot_server.web.blueprints.app2c_bp import router as app2c_router
    from deskbot_server.web.blueprints.app_bp import router as app_router
    from deskbot_server.web.blueprints.auth_bp import router as auth_router
    from deskbot_server.web.blueprints.debug_bp import router as debug_router
    from deskbot_server.web.blueprints.flash_bp import router as flash_router
    from deskbot_server.web.blueprints.proxy_bp import router as proxy_router
    from deskbot_server.web.blueprints.site import router as site_router

    @app.middleware("http")
    async def web_auth_middleware(request: Request, call_next):
        await _prepare_body(request)
        try:
            path = request.url.path or ""
            if request.method != "OPTIONS":
                public = path == "/" or path.startswith(_PUBLIC_PREFIXES) or path.startswith("/static/")
                web_protected = (
                    path.startswith("/app/")
                    or path.startswith("/proxy/")
                    or path.startswith("/debug/")
                    or path.startswith("/home")
                    or path.startswith("/voice")
                    or path.startswith("/expr")
                    or path.startswith("/lab")
                    or path.startswith("/my/")
                    or path.startswith("/advanced")
                    or path.startswith("/flash")
                    or path.startswith("/onboarding")
                    or path.startswith("/api/setup/")
                    or path.startswith("/api/advanced")
                    or path.startswith("/api/emotion_expr_map")
                    or path.startswith("/api/face_expr_scenes")
                    or path.startswith("/api/face_mouth")
                    or path.startswith("/api/scene_playbook")
                    or path.startswith("/api/face_design")
                    or path.startswith("/api/flash/")
                    or path.startswith("/api/debug/")
                    or path.startswith("/api/llm/")
                    or path.startswith("/api/tts/")
                    or path.startswith("/api/doubao_tts/")
                    or path.startswith("/api/camera_face_config")
                    or path.startswith("/api/face_profiles")
                    or path.startswith("/api/user_memory")
                    or path.startswith("/api/scene_playbooks")
                )

                if web_protected and not public:
                    user = load_session_user(request)
                    if user is None:
                        if path.startswith("/api/") or "application/json" in (request.headers.get("accept") or ""):
                            return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
                        return RedirectResponse(url=f"/login?next={path}", status_code=302)

            response = await call_next(request)
            if path.startswith("/debug/"):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"
            return response
        finally:
            try:
                remove_session()
            except Exception:
                pass

    app.include_router(site_router)
    app.include_router(auth_router)
    app.include_router(app_router)
    app.include_router(app2c_router)
    app.include_router(flash_router)
    app.include_router(debug_router)
    app.include_router(proxy_router)

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="deskbot_session",
        max_age=604800,
        same_site="lax",
        https_only=False,
    )

    logger.info("Web console mounted on FastAPI (templates=%s)", _WEB_DIR / "templates")
