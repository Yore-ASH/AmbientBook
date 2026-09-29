"""SQLite storage for accounts and plot ownership.

Standard library only: the site has no migration tooling to install, and the
schema is small enough that ``CREATE TABLE IF NOT EXISTS`` is the whole story.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',
    display_name  TEXT    NOT NULL DEFAULT '',
    email         TEXT    NOT NULL DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS plots (
    id         TEXT    PRIMARY KEY,
    owner_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS plots_owner ON plots(owner_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

ROLE_USER = "user"
ROLE_ADMIN = "admin"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# connection handling
# --------------------------------------------------------------------------

def db_path(data_dir) -> Path:
    return Path(data_dir) / "app.db"


def open_db(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A busy timeout plus WAL lets several gunicorn workers read at once while
    # one writes, instead of failing with "database is locked" under load.
    connection = sqlite3.connect(str(path), timeout=15.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def init_db(data_dir) -> Path:
    """Create the schema if it is missing; safe to call on every start."""

    path = db_path(data_dir)
    connection = open_db(path)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()
    return path


def get_db() -> sqlite3.Connection:
    """The connection for the current request."""

    if "db" not in g:
        g.db = open_db(db_path(current_app.config["DATA_DIR"]))
    return g.db


def close_db(exc=None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------

def row_to_user(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    user = dict(row)
    user["is_active"] = bool(user["is_active"])
    user["is_admin"] = user["role"] == ROLE_ADMIN
    return user


def count_users(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])


def create_user(
    connection: sqlite3.Connection,
    *,
    username: str,
    password_hash: str,
    role: str = ROLE_USER,
    display_name: str = "",
    email: str = "",
) -> Dict[str, Any]:
    cursor = connection.execute(
        "INSERT INTO users (username, password_hash, role, display_name, email, is_active, created_at)"
        " VALUES (?, ?, ?, ?, ?, 1, ?)",
        (username, password_hash, role, display_name, email, now()),
    )
    connection.commit()
    return find_user_by_id(connection, cursor.lastrowid)


def find_user_by_id(connection: sqlite3.Connection, user_id: int) -> Optional[Dict[str, Any]]:
    return row_to_user(
        connection.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    )


def find_user_by_name(connection: sqlite3.Connection, username: str) -> Optional[Dict[str, Any]]:
    return row_to_user(
        connection.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    )


def list_users(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = connection.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [row_to_user(row) for row in rows]


def update_user(connection: sqlite3.Connection, user_id: int, **fields) -> Optional[Dict[str, Any]]:
    allowed = {"role", "display_name", "email", "is_active", "password_hash"}
    changes = {key: value for key, value in fields.items() if key in allowed}
    if not changes:
        return find_user_by_id(connection, user_id)
    assignments = ", ".join("%s = ?" % key for key in changes)
    values = [int(value) if key == "is_active" else value for key, value in changes.items()]
    connection.execute(
        "UPDATE users SET %s WHERE id = ?" % assignments, (*values, int(user_id))
    )
    connection.commit()
    return find_user_by_id(connection, user_id)


def delete_user(connection: sqlite3.Connection, user_id: int) -> None:
    connection.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
    connection.commit()


def count_admins(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = ?", (ROLE_ADMIN,)
        ).fetchone()["n"]
    )


# --------------------------------------------------------------------------
# plots
# --------------------------------------------------------------------------

def row_to_plot(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    return None if row is None else dict(row)


def create_plot_row(
    connection: sqlite3.Connection, owner_id: int, name: str
) -> Dict[str, Any]:
    plot_id = uuid.uuid4().hex
    stamp = now()
    connection.execute(
        "INSERT INTO plots (id, owner_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (plot_id, int(owner_id), name, stamp, stamp),
    )
    connection.commit()
    return get_plot(connection, plot_id)


def get_plot(connection: sqlite3.Connection, plot_id: str) -> Optional[Dict[str, Any]]:
    return row_to_plot(
        connection.execute("SELECT * FROM plots WHERE id = ?", (plot_id,)).fetchone()
    )


def list_plots(
    connection: sqlite3.Connection, owner_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    if owner_id is None:
        rows = connection.execute(
            "SELECT plots.*, users.username AS owner_name FROM plots"
            " JOIN users ON users.id = plots.owner_id ORDER BY plots.updated_at DESC"
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT plots.*, users.username AS owner_name FROM plots"
            " JOIN users ON users.id = plots.owner_id"
            " WHERE plots.owner_id = ? ORDER BY plots.updated_at DESC",
            (int(owner_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def touch_plot(connection: sqlite3.Connection, plot_id: str) -> None:
    connection.execute(
        "UPDATE plots SET updated_at = ? WHERE id = ?", (now(), plot_id)
    )
    connection.commit()


def rename_plot(connection: sqlite3.Connection, plot_id: str, name: str) -> None:
    connection.execute(
        "UPDATE plots SET name = ?, updated_at = ? WHERE id = ?", (name, now(), plot_id)
    )
    connection.commit()


def count_plots(connection: sqlite3.Connection, owner_id: Optional[int] = None) -> int:
    if owner_id is None:
        sql, params = "SELECT COUNT(*) AS n FROM plots", ()
    else:
        sql, params = "SELECT COUNT(*) AS n FROM plots WHERE owner_id = ?", (int(owner_id),)
    return int(connection.execute(sql, params).fetchone()["n"])


def delete_plot_row(connection: sqlite3.Connection, plot_id: str) -> None:
    connection.execute("DELETE FROM plots WHERE id = ?", (plot_id,))
    connection.commit()
