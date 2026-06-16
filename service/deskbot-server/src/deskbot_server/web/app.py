from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, redirect, request, url_for
from flask_login import LoginManager, current_user

from deskbot_server.auth.flask_user import FlaskUser
from deskbot_server.auth.service import get_user_by_id
from deskbot_server.db import init_database, remove_session
from deskbot_server.env import load_dotenv
from deskbot_server.web.blueprints.app_bp import bp as app_bp
from deskbot_server.web.blueprints.auth_bp import bp as auth_bp
from deskbot_server.web.blueprints.debug_bp import bp as debug_bp
from deskbot_server.web.blueprints.proxy_bp import bp as proxy_bp
from deskbot_server.web.blueprints.site import bp as site_bp

login_manager = LoginManager()
_WEB_DIR = Path(__file__).resolve().parent


def web_debug_enabled() -> bool:
    """Flask 开发服务器 debug 模式（默认关闭，避免暴露 Werkzeug 交互式调试台）。"""
    raw = (os.environ.get("DESKBOT_WEB_DEBUG") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def create_app() -> Flask:
    load_dotenv()
    init_database()

    app = Flask(
        __name__,
        template_folder=str(_WEB_DIR / "templates"),
        static_folder=str(_WEB_DIR / "static"),
    )
    secret = (os.environ.get("DESKBOT_WEB_SECRET_KEY") or "").strip()
    if not secret:
        secret = "dev-insecure-change-me"
    app.config["SECRET_KEY"] = secret
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = 28800
    app.jinja_env.auto_reload = True

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "请先登录"

    @login_manager.user_loader
    def load_user(user_id: str):
        user = get_user_by_id(user_id)
        if user is None or not user.is_active:
            return None
        return FlaskUser(user)

    app.register_blueprint(site_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(app_bp)
    app.register_blueprint(debug_bp)
    app.register_blueprint(proxy_bp)

    @app.before_request
    def require_auth():
        if request.method == "OPTIONS":
            return None
        path = request.path or ""
        public_prefixes = (
            "/login",
            "/register",
            "/health",
        )
        if path == "/" or path.startswith(public_prefixes) or path.startswith("/static/"):
            return None
        if current_user.is_authenticated:
            return None
        if path.startswith("/api/"):
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return redirect(url_for("auth.login", next=path))

    @app.after_request
    def no_cache_debug_pages(resp):
        p = request.path or ""
        if p.startswith("/debug/"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    @app.teardown_appcontext
    def shutdown_session(_exc=None):
        remove_session()

    @app.context_processor
    def inject_globals():
        display_name = None
        if current_user.is_authenticated:
            display_name = getattr(current_user, "display_name", None) or current_user.email
        return {
            "nav_user_email": current_user.email if current_user.is_authenticated else None,
            "nav_display_name": display_name,
        }

    return app


app = create_app()


if __name__ == "__main__":
    host = (os.environ.get("DESKBOT_WEB_HOST") or "0.0.0.0").strip()
    port = int(os.environ.get("DESKBOT_WEB_PORT") or "5050")
    app.run(host=host, port=port, debug=web_debug_enabled())
