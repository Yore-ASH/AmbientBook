"""Compatibility launcher for :mod:`TSCPEditor.Main`."""

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from TSCPEditor.Main import *  # noqa: F401,F403
from TSCPEditor.Main import main


if __name__ == "__main__":
    raise SystemExit(main())
