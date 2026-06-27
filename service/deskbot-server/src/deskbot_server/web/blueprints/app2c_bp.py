from __future__ import annotations

from flask import Blueprint, render_template
from flask_login import login_required

bp = Blueprint("app2c", __name__)


@bp.get("/home")
@login_required
def home():
    return render_template("app2c/home.html", active_nav="home")


@bp.get("/voice")
@login_required
def voice():
    return render_template("app2c/voice.html", active_nav="voice")


@bp.get("/expr")
@login_required
def expr():
    return render_template("app2c/expr.html", active_nav="expr")


@bp.get("/my/memories")
@login_required
def memories():
    return render_template("app2c/memories.html", active_nav="memory")


@bp.get("/my/reminders")
@login_required
def reminders():
    return render_template("app2c/reminders.html", active_nav="remind")


@bp.get("/my/people")
@login_required
def people():
    return render_template("app2c/people.html", active_nav="people")


@bp.get("/my/devices")
@login_required
def devices():
    return render_template("app2c/devices.html", active_nav="device")


@bp.get("/advanced")
@login_required
def advanced():
    return render_template("app2c/advanced.html", active_nav="advanced")
