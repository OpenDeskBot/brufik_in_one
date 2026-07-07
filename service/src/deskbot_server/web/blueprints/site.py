from __future__ import annotations

from flask import Blueprint, redirect, url_for

bp = Blueprint("site", __name__)


@bp.get("/")
def index():
    # 无独立官网首页：根路径直接进入控制台；未登录会被 @login_required 引导到登录页。
    return redirect(url_for("app2c.home"))
