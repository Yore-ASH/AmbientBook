"""Utilities and optional Qt GUI for editing compiled TSCP plots.

The data helpers in this package deliberately do not import PySide6.  This
keeps format tooling and package inspection usable in environments where the
optional GUI dependency is not installed.
"""

from .model import (
    character_keys_from_source,
    load_script,
    parse_delay_text,
    save_script,
)

__all__ = [
    "character_keys_from_source",
    "load_script",
    "parse_delay_text",
    "save_script",
]
