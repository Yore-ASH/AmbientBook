"""Helpers and optional GUI for adding entries to ``CHARACTERS``."""

from .model import (
    ANSI_COLORS,
    ANSI_PRESETS,
    CharacterConfig,
    build_ansi_style,
    generate_json_entry,
    make_character_config,
    normalize_ansi,
    validate_abbreviation,
    validate_full_name,
)

__all__ = [
    "ANSI_COLORS",
    "ANSI_PRESETS",
    "CharacterConfig",
    "build_ansi_style",
    "generate_json_entry",
    "make_character_config",
    "normalize_ansi",
    "validate_abbreviation",
    "validate_full_name",
]
