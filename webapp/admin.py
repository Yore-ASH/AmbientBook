"""The administrator API: accounts and every plot on the site.

Two invariants are enforced here so an admin cannot lock everybody out: you can
never demote, deactivate or delete **yourself**, and the last remaining
administrator cannot be removed or demoted.
"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from werkzeug.security import generate_password_hash

from . import db, storage
from .auth import AuthError, admin_required, current_user, public_user, validate_password

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _target_user(user_id: int):
    user = db.find_user_by_id(db.get_db(), user_id)
    if user is None:
        raise AuthError("用户不存在")
    return user


def _guard_self_and_last_admin(user, *, demoting: bool, removing: bool) -> None:
    actor = current_user()
    if user["id"] == actor["id"]:
        raise AuthError("不能对自己执行这个操作")
    if user["is_admin"] and (demoting or removing):
        if db.count_admins(db.get_db()) <= 1:
            raise AuthError("至少要保留一名管理员")


@bp.get("/overview")
@admin_required
def overview():
    connection = db.get_db()
    plots = db.list_plots(connection)
    total_bytes = 0
    for row in plots:
        path = storage.plot_path(current_app.config["DATA_DIR"], row["id"])
        if path.is_file():
            total_bytes += path.stat().st_size
    return jsonify(
        {
            "users": db.count_users(connection),
            "admins": db.count_admins(connection),
            "plots": len(plots),
            "bytes": total_bytes,
        }
    )


@bp.get("/users")
@admin_required
def list_users():
    connection = db.get_db()
    users = [public_user(user) for user in db.list_users(connection)]
    for user in users:
        user["plot_count"] = db.count_plots(connection, user["id"])
    return jsonify({"users": users})


@bp.post("/users")
@admin_required
def create_user():
    payload = request.get_json(silent=True) or {}
    connection = db.get_db()
    try:
        from .auth import validate_username

        username = validate_username(payload.get("username", ""))
        password = validate_password(payload.get("password", ""))
        if db.find_user_by_name(connection, username) is not None:
            raise AuthError("这个用户名已经被占用")
        role = str(payload.get("role", db.ROLE_USER)).strip().lower()
        if role not in {db.ROLE_USER, db.ROLE_ADMIN}:
            raise AuthError("角色只能是 user 或 admin")
        user = db.create_user(
            connection,
            username=username,
            password_hash=generate_password_hash(password),
            role=role,
            display_name=str(payload.get("display_name", "")).strip() or username,
            email=str(payload.get("email", "")).strip(),
        )
    except AuthError as exc:
        return _error(str(exc))
    return jsonify({"user": public_user(user)}), 201


@bp.patch("/users/<int:user_id>")
@admin_required
def update_user(user_id: int):
    payload = request.get_json(silent=True) or {}
    connection = db.get_db()
    try:
        user = _target_user(user_id)
        fields = {}

        if "role" in payload:
            role = str(payload["role"]).strip().lower()
            if role not in {db.ROLE_USER, db.ROLE_ADMIN}:
                raise AuthError("角色只能是 user 或 admin")
            if role != user["role"]:
                _guard_self_and_last_admin(
                    user, demoting=role == db.ROLE_USER, removing=False
                )
            fields["role"] = role

        if "is_active" in payload:
            active = bool(payload["is_active"])
            if not active:
                _guard_self_and_last_admin(user, demoting=False, removing=True)
            fields["is_active"] = active

        for key in ("display_name", "email"):
            if key in payload:
                fields[key] = str(payload[key]).strip()

        if payload.get("password"):
            fields["password_hash"] = generate_password_hash(
                validate_password(payload["password"])
            )

        updated = db.update_user(connection, user_id, **fields)
    except AuthError as exc:
        return _error(str(exc))
    return jsonify({"user": public_user(updated)})


@bp.delete("/users/<int:user_id>")
@admin_required
def delete_user(user_id: int):
    connection = db.get_db()
    try:
        user = _target_user(user_id)
        _guard_self_and_last_admin(user, demoting=True, removing=True)
        for row in db.list_plots(connection, owner_id=user_id):
            try:
                storage.delete_plot_file(current_app.config["DATA_DIR"], row["id"])
            except storage.StorageError:
                pass
        db.delete_user(connection, user_id)
    except AuthError as exc:
        return _error(str(exc))
    return jsonify({"ok": True})


@bp.get("/plots")
@admin_required
def list_all_plots():
    connection = db.get_db()
    rows = db.list_plots(connection)
    for row in rows:
        path = storage.plot_path(current_app.config["DATA_DIR"], row["id"])
        row["bytes"] = path.stat().st_size if path.is_file() else 0
    return jsonify({"plots": rows})


@bp.get("/plots/<plot_id>")
@admin_required
def get_any_plot(plot_id: str):
    row = db.get_plot(db.get_db(), plot_id)
    if row is None:
        return _error("剧情包不存在", 404)
    try:
        package = storage.load(current_app.config["DATA_DIR"], plot_id)
    except storage.StorageError as exc:
        return _error(str(exc), 500)
    return jsonify({"plot": row, "detail": storage.describe(package)})


@bp.delete("/plots/<plot_id>")
@admin_required
def delete_any_plot(plot_id: str):
    row = db.get_plot(db.get_db(), plot_id)
    if row is None:
        return _error("剧情包不存在", 404)
    try:
        storage.delete_plot_file(current_app.config["DATA_DIR"], plot_id)
    except storage.StorageError as exc:
        return _error(str(exc), 500)
    db.delete_plot_row(db.get_db(), plot_id)
    return jsonify({"ok": True})
