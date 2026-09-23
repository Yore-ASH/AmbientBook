"""Compatibility module exposing the converter timing model."""

from .model import (  # noqa: F401
    CharacterTimingModel,
    KeyboardTimingModel,
    TimingMode,
    TimingRecorder,
    compile_file,
    compile_script,
    visible_characters,
    timed_character_positions,
    timed_character_indices,
    is_timed_character,
)

__all__ = [
    "CharacterTimingModel",
    "KeyboardTimingModel",
    "TimingMode",
    "TimingRecorder",
    "compile_file",
    "compile_script",
    "visible_characters",
    "timed_character_positions",
    "timed_character_indices",
    "is_timed_character",
]
