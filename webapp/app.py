"""Run the site locally.

Production uses a WSGI server instead, for example::

    gunicorn "webapp:create_app()" --bind 127.0.0.1:8000 --workers 2 --timeout 120

The timeout matters: packing a plot with a large FLAC takes a while.
"""

from __future__ import annotations

import argparse
import os

from . import create_app
from .config import Config, DevConfig


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="运行 TSCP 剧情网站")
    parser.add_argument("--host", default=os.environ.get("TSCP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("TSCP_PORT", "5000"))
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("TSCP_DEBUG", "").strip().lower() in {"1", "true", "yes"},
    )
    args = parser.parse_args(argv)

    app = create_app(DevConfig if args.debug else Config)
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
