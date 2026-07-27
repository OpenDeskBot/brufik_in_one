from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from deskbot_server.web.urls import url_for
from deskbot_server.web.view_helpers import ViewAPIRoute, redirect

router = APIRouter(route_class=ViewAPIRoute, tags=["site"])


@router.get("/")
def index(request: Request):
    del request
    return redirect(url_for("app2c.home"))


@router.get("/health")
def health(request: Request):
    del request
    return JSONResponse({"ok": True, "service": "deskbot-web"})


ENDPOINTS = {"site.index": "/", "site.health": "/health"}
