"""Build and edit single-file ``.tscpkg`` plots.

:mod:`PlotManager.model` is dependency-free; :mod:`PlotManager.Main` adds the
optional PySide6 asset manager window.
"""

from .model import (
    MusicEntry,
    PackError,
    PackageInfo,
    ScriptEntry,
    add_music,
    add_script,
    bind_music,
    create_package,
    inspect,
    music_config,
    pack_directory,
    remove_music,
    remove_script,
    script_member_name,
    suggest_abbreviation,
    update_metadata,
)

__all__ = [
    "MusicEntry",
    "PackError",
    "PackageInfo",
    "ScriptEntry",
    "add_music",
    "add_script",
    "bind_music",
    "create_package",
    "inspect",
    "music_config",
    "pack_directory",
    "remove_music",
    "remove_script",
    "script_member_name",
    "suggest_abbreviation",
    "update_metadata",
]
