"""Accounts: registration, login, logout, and the role decorators.

Sessions are server-side-signed cookies from Flask, so there is nothing extra to
deploy.  Passwords are hashed with Werkzeug's default (currently scrypt).
"""

from __future__ import annotations

import re
from functools import wraps
from typing import Any, Dict, Optional

from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import db

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
USERNAME_MIN, USERNAME_MAX = 3, 32
PASSWORD_MIN = 6


class AuthError(ValueError):
    """Raised for a rejected registration or profile change."""


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def current_user() -> Optional[Dict[str, Any]]:
    """The signed-in user, or ``None``.

    A deactivated account is treated as signed out immediately, even if it still
    holds a valid cookie.
    """

    if "user" not in g:
        user_id = session.get("user_id")
        user = db.find_user_by_id(db.get_db(), user_id) if user_id else None
        g.user = user if user and user["is_active"] else None
        if user is not None and not user["is_active"]:
            session.pop("user_id", None)
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return jsonify({"error": "需要登录"}), 401
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return jsonify({"error": "需要登录"}), 401
        if not user["is_admin"]:
            return jsonify({"error": "需要管理员权限"}), 403
        return view(*args, **kwargs)

    return wrapped


def public_user(user: Dict[str, Any]) -> Dict[str, Any]:
    """The subset of a user row that is safe to hand to the browser."""

    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
        "email": user["email"],
        "role": user["role"],
        "is_admin": user["is_admin"],
        "is_active": user["is_active"],
        "created_at": user["created_at"],
    }


def validate_username(value: str) -> str:
    username = str(value).strip()
    if not USERNAME_MIN <= len(username) <= USERNAME_MAX:
        raise AuthError("用户名长度需在 %d-%d 之间" % (USERNAME_MIN, USERNAME_MAX))
    if not USERNAME_RE.match(username):
        raise AuthError("用户名只能包含字母、数字、下划线、点和短横线")
    return username


def validate_password(value: str) -> str:
    password = str(value)
    if len(password) < PASSWORD_MIN:
        raise AuthError("密码至少 %d 位" % PASSWORD_MIN)
    return password


def register_user(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Shared by public sign-up and the admin's "create user" action."""

    connection = db.get_db()
    username = validate_username(payload.get("username", ""))
    password = validate_password(payload.get("password", ""))
    if db.find_user_by_name(connection, username) is not None:
        raise AuthError("这个用户名已经被占用")

    role = db.ROLE_USER
    if db.count_users(connection) == 0 and current_app.config["FIRST_USER_IS_ADMIN"]:
        # Bootstrapping: the very first account owns the site.
        role = db.ROLE_ADMIN
    elif str(payload.get("role", "")).strip().lower() == db.ROLE_ADMIN:
        raise AuthError("只有管理员可以创建管理员账号")

    return db.create_user(
        connection,
        username=username,
        password_hash=hash_password(password),
        role=role,
        display_name=str(payload.get("display_name", "")).strip() or username,
        email=str(payload.get("email", "")).strip(),
    )


@bp.post("/register")
def register():
    if not current_app.config["ALLOW_REGISTRATION"]:
        return jsonify({"error": "站点已关闭注册，请联系管理员开设账号"}), 403
    try:
        user = register_user(request.get_json(silent=True) or {})
    except AuthError as exc:
        return jsonify({"error": str(exc)}), 400
    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"user": public_user(user)}), 201


@bp.post("/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    user = db.find_user_by_name(db.get_db(), username)
    # One message for both failures so the form cannot be used to probe names.
    if user is None or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "用户名或密码不正确"}), 401
    if not user["is_active"]:
        return jsonify({"error": "账号已被停用，请联系管理员"}), 403
    session.clear()
    session["user_id"] = user["id"]
    session.permanent = True
    return jsonify({"user": public_user(user)})


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
def me():
    user = current_user()
    return jsonify(
        {
            "user": public_user(user) if user else None,
            "allow_registration": current_app.config["ALLOW_REGISTRATION"],
            "needs_setup": db.count_users(db.get_db()) == 0,
        }
    )


@bp.post("/password")
@login_required
def change_password():
    """Let a signed-in user change their own password."""

    payload = request.get_json(silent=True) or {}
    user = current_user()
    if not check_password_hash(user["password_hash"], str(payload.get("current", ""))):
        return jsonify({"error": "当前密码不正确"}), 400
    try:
        new_password = validate_password(payload.get("new", ""))
    except AuthError as exc:
        return jsonify({"error": str(exc)}), 400
    db.update_user(db.get_db(), user["id"], password_hash=hash_password(new_password))
    return jsonify({"ok": True})


@bp.patch("/profile")
@login_required
def update_profile():
    payload = request.get_json(silent=True) or {}
    user = current_user()
    updated = db.update_user(
        db.get_db(),
        user["id"],
        display_name=str(payload.get("display_name", user["display_name"])).strip(),
        email=str(payload.get("email", user["email"])).strip(),
    )
    return jsonify({"user": public_user(updated)})
