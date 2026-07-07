from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from deskbot_server.auth.flask_user import FlaskUser
from deskbot_server.auth.service import (
    create_user,
    get_user_by_email,
    normalize_email,
    verify_password,
)
from deskbot_server.db.engine import remove_session

bp = Blueprint("auth", __name__)


def _safe_next_url(raw: str | None) -> str:
    if not raw:
        return url_for("app2c.home")
    parsed = urlparse(raw)
    if parsed.netloc or parsed.scheme:
        return url_for("app2c.home")
    if not raw.startswith("/"):
        return url_for("app2c.home")
    return raw


@bp.get("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("app2c.home"))
    return render_template("auth/login.html", next_url=_safe_next_url(request.args.get("next")))


@bp.post("/login")
def login_post():
    email = normalize_email(request.form.get("email") or "")
    password = request.form.get("password") or ""
    next_url = _safe_next_url(request.form.get("next") or request.args.get("next"))

    user = get_user_by_email(email)
    if user is None or not user.is_active or not verify_password(user, password):
        flash("邮箱或密码错误", "error")
        return render_template("auth/login.html", next_url=next_url, email=email), 401

    login_user(FlaskUser(user), remember=True)
    remove_session()
    return redirect(next_url)


@bp.get("/register")
def register():
    if current_user.is_authenticated:
        return redirect(url_for("app2c.home"))
    return render_template("auth/register.html")


@bp.post("/register")
def register_post():
    email = normalize_email(request.form.get("email") or "")
    password = request.form.get("password") or ""
    confirm = request.form.get("confirm_password") or ""
    if password != confirm:
        flash("两次密码不一致", "error")
        return render_template("auth/register.html", email=email), 400
    try:
        user = create_user(email, password)
    except ValueError as exc:
        flash(str(exc), "error")
        return render_template("auth/register.html", email=email), 400

    login_user(FlaskUser(user), remember=True)
    remove_session()
    flash("注册成功，欢迎加入！", "success")
    # 新用户：若还没有可用的大模型（本地未配置 Ark/LLM 密钥），先进入统一模型配置页。
    from deskbot_server.llm.runtime import resolve_system_llm_config

    key = str(resolve_system_llm_config().api_key or "").strip()
    if not key or "请替换" in key:
        return redirect(url_for("app2c.advanced", tab="llm"))
    return redirect(url_for("app2c.home"))


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    remove_session()
    flash("已退出登录", "info")
    return redirect(url_for("site.index"))
