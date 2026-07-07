from __future__ import annotations

from functools import wraps

from flask import flash, jsonify, redirect, request, url_for
from flask_login import current_user


def current_user_is_developer() -> bool:
    if not current_user.is_authenticated:
        return False
    return bool(getattr(current_user, "is_developer", False))


def require_developer(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user_is_developer():
            if request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
                return jsonify({"ok": False, "error": "需要开发者权限"}), 403
            flash("需要开发者权限", "error")
            return redirect(url_for("app2c.home"))
        return view(*args, **kwargs)

    return wrapped
