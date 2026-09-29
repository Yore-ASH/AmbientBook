"""Shared test setup.

The GUI tests must never try to open a real window: a headless Ubuntu box (or a
CI runner) has no display, so Qt is pointed at its offscreen platform before any
``QApplication`` is created.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
