"""Convert source ``.tscps`` scripts into timed ``.tscp`` files.

The data and keyboard models are dependency-free.  :mod:`Ts2Tp.Main` only
imports PySide6 when the optional GUI is launched.
"""

from .model import (
    KeyboardTimingModel,
    CharacterTimingModel,
    TimingMode,
    TimingRecorder,
    compile_file,
    compile_script,
    compiled_path,
    compiled_name,
    output_path,
    visible_characters,
    timed_character_positions,
    timed_character_indices,
    is_timed_character,
    split_pages,
)

__all__ = [
    "KeyboardTimingModel",
    "CharacterTimingModel",
    "TimingMode",
    "compile_file",
    "compile_script",
    "compiled_name",
    "compiled_path",
    "output_path",
    "visible_characters",
    "timed_character_positions",
    "timed_character_indices",
    "is_timed_character",
    "split_pages",
    "TimingRecorder",
]
