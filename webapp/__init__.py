"""The TSCP authoring website.

``create_app`` is the only entry point: it wires the database, the JSON API and
the HTML pages into one Flask application.  Everything the site does with a
``.tscpkg`` goes through the same modules the desktop tools use.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import admin, api, auth, db, pages
from .api import ApiError
from .config import Config, DevConfig, TestConfig

__all__ = ["create_app", "Config", "DevConfig", "TestConfig"]


def create_app(config_object=None, **overrides) -> Flask:
    app = Flask(__name__)
    # ``from_object(None)`` silently loads nothing, so default to Config here
    # rather than letting every setting go missing.
    app.config.from_object(config_object or Config)
    app.config.update(overrides)
    # Chinese plot names should come back readable, not \uXXXX escaped.
    app.json.ensure_ascii = False

    data_dir = Path(app.config["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    db.init_db(data_dir)

    app.teardown_appcontext(db.close_db)

    # Behind nginx, honour X-Forwarded-Proto/Host so redirects and cookies use
    # the public scheme.  Set TSCP_PROXY_HOPS=0 when nothing sits in front.
    hops = int(app.config.get("PROXY_HOPS") or 0)
    if hops > 0:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops, x_port=hops
        )

    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(pages.bp)

    @app.errorhandler(ApiError)
    def api_error(error):
        """An API error carries its own status, so one handler covers them all."""

        return jsonify({"error": str(error)}), error.status

    @app.errorhandler(413)
    def too_large(_error):
        limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        message = "文件太大了，上限 %d MB" % limit
        if request.path.startswith("/api/"):
            return jsonify({"error": message}), 413
        return render_template("error.html", message=message), 413

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "接口不存在"}), 404
        return render_template("error.html", message="页面不存在"), 404

    @app.errorhandler(500)
    def server_error(_error):
        if request.path.startswith("/api/"):
            return jsonify({"error": "服务器内部错误"}), 500
        return render_template("error.html", message="服务器内部错误"), 500

    return app
