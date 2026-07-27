from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request

from deskbot_server.auth.session_user import SessionUser
from deskbot_server.db.engine import remove_session
from deskbot_server.service.user_service import UserService
from deskbot_server.web.deps import RequireUser, load_session_user
from deskbot_server.web.urls import flash, url_for
from deskbot_server.web.view_helpers import ViewAPIRoute, form_get, redirect, render_template

router = APIRouter(route_class=ViewAPIRoute, tags=["auth"])


def _safe_next_url(raw: str | None) -> str:
    if not raw:
        return url_for("app2c.home")
    parsed = urlparse(raw)
    if parsed.netloc or parsed.scheme:
        return url_for("app2c.home")
    if not raw.startswith("/"):
        return url_for("app2c.home")
    return raw


@router.get("/login")
def login(request: Request):
    if load_session_user(request) is not None:
        return redirect(url_for("app2c.home"))
    return render_template(
        request,
        "auth/login.html",
        next_url=_safe_next_url(request.query_params.get("next")),
    )


@router.post("/login")
def login_post(request: Request):
    users = UserService()
    email = users.normalize_email(form_get(request, "email") or "")
    password = form_get(request, "password") or ""
    next_url = _safe_next_url(form_get(request, "next") or request.query_params.get("next"))

    try:
        user = users.login(email, password)
    except ValueError:
        flash(request, "邮箱或密码错误", "error")
        return render_template(request, "auth/login.html", next_url=next_url, email=email), 401

    from deskbot_server.web.view_helpers import login_user

    login_user(request, SessionUser(user), remember=True)
    remove_session()
    return redirect(next_url)


@router.get("/register")
def register(request: Request):
    if load_session_user(request) is not None:
        return redirect(url_for("app2c.home"))
    return render_template(request, "auth/register.html")


@router.post("/register")
def register_post(request: Request):
    users = UserService()
    email = users.normalize_email(form_get(request, "email") or "")
    password = form_get(request, "password") or ""
    confirm = form_get(request, "confirm_password") or ""
    if password != confirm:
        flash(request, "两次密码不一致", "error")
        return render_template(request, "auth/register.html", email=email), 400
    try:
        user = users.register(email, password)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return render_template(request, "auth/register.html", email=email), 400

    from deskbot_server.web.view_helpers import login_user

    login_user(request, SessionUser(user), remember=True)
    remove_session()
    flash(request, "注册成功，欢迎加入！", "success")
    from deskbot_server.infrastructure.llm.runtime import resolve_system_llm_config

    key = str(resolve_system_llm_config().api_key or "").strip()
    if not key or "请替换" in key:
        return redirect(url_for("app2c.advanced", tab="llm"))
    return redirect(url_for("app2c.home"))


@router.post("/logout")
def logout(request: Request, user: RequireUser):
    from deskbot_server.web.view_helpers import logout_user

    logout_user(request)
    remove_session()
    flash(request, "已退出登录", "info")
    return redirect(url_for("site.index"))


ENDPOINTS = {
    "auth.login": "/login",
    "auth.login_post": "/login",
    "auth.register": "/register",
    "auth.register_post": "/register",
    "auth.logout": "/logout",
}
