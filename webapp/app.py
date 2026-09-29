"""Run the site with Flask's built-in server.

Handy for a smoke test on the server, but **not** for production: use gunicorn
instead (see ``deploy/README.md``).

    python -m webapp                 # 0.0.0.0:8888
    python -m webapp --port 9000
    python -m webapp --debug
"""

from __future__ import annotations

import argparse

from . import create_app
from .config import Config, DevConfig


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="运行 TSCP 剧情网站（开发服务器）")
    parser.add_argument(
        "--host", default=Config.HOST, help="默认 %s" % Config.HOST
    )
    parser.add_argument(
        "--port", type=int, default=Config.PORT, help="默认 %d" % Config.PORT
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    app = create_app(DevConfig if args.debug else Config)
    print("TSCP 剧情工坊： http://%s:%d" % (args.host, args.port))
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
