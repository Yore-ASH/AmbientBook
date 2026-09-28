"""HTML routes.

The pages are thin shells: everything they show comes from the JSON API, so the
same templates work whether the site is served by the Flask dev server or by
gunicorn behind nginx.

Unlike the API, a page that needs a login *redirects* instead of answering 401,
so a bookmarked URL lands on the sign-in form rather than a JSON error.
"""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, redirect, render_template, request, url_for

from .auth import current_user

bp = Blueprint("pages", __name__)


def page_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("pages.index"))
        return view(*args, **kwargs)

    return wrapped


def page_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("pages.index"))
        if not user["is_admin"]:
            return redirect(url_for("pages.index"))
        return view(*args, **kwargs)

    return wrapped


@bp.get("/")
def index():
    from . import db

    user = current_user()
    redirect_to = request.args.get("next", "")
    return render_template(
        "index.html",
        user=user,
        needs_setup=db.count_users(db.get_db()) == 0,
        redirect_to=redirect_to,
    )


@bp.get("/editor")
@bp.get("/editor/<plot_id>")
@page_login_required
def editor(plot_id: str = ""):
    return render_template("editor.html", user=current_user(), plot_id=plot_id)


@bp.get("/play")
@bp.get("/play/<plot_id>")
@page_login_required
def player(plot_id: str = ""):
    return render_template("player.html", user=current_user(), plot_id=plot_id)


@bp.get("/admin")
@page_admin_required
def admin_page():
    return render_template("admin.html", user=current_user())
