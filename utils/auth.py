from functools import wraps
from flask import redirect, session, url_for, request


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def super_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_user_id"):
            return redirect(url_for("super_admin.login", next=request.path))
        if session.get("admin_role") != "super_admin":
            return redirect(url_for("super_admin.login"))
        return view(*args, **kwargs)

    return wrapped
