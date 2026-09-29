"""Configuration for the TSCP web app.

Everything is overridable with environment variables so the same code runs in a
local checkout and on a deployed Flask site.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

#: Repository root, so the app can import ``tscp_player`` and ``PlotManager``.
ROOT = Path(__file__).resolve().parent.parent


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Production-ish defaults; override with environment variables."""

    SECRET_KEY = os.environ.get("TSCP_SECRET_KEY", "dev-secret-change-me")
    DATA_DIR = _env_path("TSCP_DATA_DIR", ROOT / "webapp" / "data")

    #: Where the bundled dev runner listens.  ``0.0.0.0`` so a fresh Ubuntu box
    #: is reachable straight away; put nginx in front for anything public.
    HOST = os.environ.get("TSCP_HOST", "0.0.0.0")
    PORT = int(os.environ.get("TSCP_PORT", "8888"))

    #: Audio uploads are large, so this is generous by default.
    MAX_CONTENT_LENGTH = int(os.environ.get("TSCP_MAX_UPLOAD_MB", "512")) * 1024 * 1024

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    #: Turn on behind HTTPS so the cookie is never sent in the clear.
    SESSION_COOKIE_SECURE = _env_flag("TSCP_SECURE_COOKIES")
    PERMANENT_SESSION_LIFETIME = timedelta(days=14)

    #: The first account to register becomes an administrator.
    FIRST_USER_IS_ADMIN = _env_flag("TSCP_FIRST_USER_IS_ADMIN", True)
    #: Set to False to close public sign-up (admins still create accounts).
    ALLOW_REGISTRATION = _env_flag("TSCP_ALLOW_REGISTRATION", True)

    #: Number of reverse-proxy hops to trust for ``X-Forwarded-*`` headers.
    #: 1 is right for a single nginx sitting in front; 0 disables it.
    PROXY_HOPS = int(os.environ.get("TSCP_PROXY_HOPS", "1"))


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class DevConfig(Config):
    DEBUG = True
