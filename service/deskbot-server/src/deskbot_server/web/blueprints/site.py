from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("site", __name__)


@bp.get("/")
def index():
    return render_template("site/index.html")
