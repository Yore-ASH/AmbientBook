"""Backward-compatible import alias for :mod:`TSCPEditor`.

The editor was renamed to make its compiled-file-only scope explicit.
"""

from TSCPEditor.model import (
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
