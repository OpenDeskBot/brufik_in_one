from __future__ import annotations

from fastapi import APIRouter

from deskbot_server.web.flaskish import FlaskishAPIRoute, redirect, url_for

router = APIRouter(route_class=FlaskishAPIRoute, tags=["site"])


@router.get("/")
def index():
    # 无独立官网首页：根路径直接进入控制台；未登录会被 @login_required 引导到登录页。
    return redirect(url_for("app2c.home"))


ENDPOINTS = {"site.index": "/"}
