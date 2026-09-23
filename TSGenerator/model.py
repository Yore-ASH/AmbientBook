"""Dependency-free helpers for the TSGenerator source editor."""

from __future__ import annotations

import math
import json
from pathlib import Path
from typing import Dict, List, Union

from tscp_player.format import (
    Directive,
    Dialogue,
    Script,
    parse_tscps,
    serialize_tscps,
)
from tscp_player.plot import Character


PathLike = Union[str, Path]


def load_script(path: PathLike) -> Script:
    """Read a UTF-8 source ``.tscps`` file."""

    return parse_tscps(Path(path).read_text(encoding="utf-8"))


def save_script(path: PathLike, script: Script) -> None:
    """Write a UTF-8 source ``.tscps`` file."""

    Path(path).write_text(serialize_tscps(script), encoding="utf-8")


def character_keys(script: Script) -> List[str]:
    """Return character keys in first-seen order."""

    result: List[str] = []
    for line in script.lines:
        if isinstance(line, Dialogue) and line.character and line.character not in result:
            result.append(line.character)
    return result


def _character_metadata_path(path: PathLike) -> Path:
    """Resolve a source, Scripts directory, or metadata path to JSON metadata."""

    candidate = Path(path)
    if candidate.is_file():
        if candidate.name == "__init__.json":
            return candidate
        return candidate.parent / "__init__.json"
    if candidate.suffix.lower() in {".tscp", ".tscps"}:
        return candidate.parent / "__init__.json"
    if candidate.name == "Scripts":
        return candidate / "__init__.json"
    if (candidate / "Scripts").is_dir():
        return candidate / "Scripts" / "__init__.json"
    return candidate / "__init__.json"


def load_character_config(path: PathLike) -> Dict[str, Character]:
    """Load character abbreviations and styles from ``Scripts/__init__.json``.

    ``path`` may be a ``.tscps`` source file, a plot package, its ``Scripts``
    directory, or the metadata file itself.  A source file outside a plot
    package simply has no configured characters.
    """

    metadata_path = _character_metadata_path(path)
    if not metadata_path.is_file():
        return {}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("metadata root must be an object")
        values = metadata.get("CHARACTERS", {})
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("无法读取角色配置：%s" % metadata_path) from exc
    if not isinstance(values, dict):
        raise ValueError("角色配置 CHARACTERS 必须是对象")
    characters: Dict[str, Character] = {}
    for key, value in values.items():
        if not isinstance(value, dict) or "NAME" not in value:
            raise ValueError("角色配置缺少 NAME：%s" % key)
        characters[str(key)] = Character(str(value["NAME"]), str(value.get("STYLE", "")))
    return characters


def character_keys_from_metadata(path: PathLike) -> List[str]:
    """Return configured character abbreviations in metadata order."""

    return list(load_character_config(path))


def character_keys_from_plot(path: PathLike) -> List[str]:
    """Alias for loading configured abbreviations from a plot package."""

    return character_keys_from_metadata(path)


def character_keys_from_source(path: PathLike) -> List[str]:
    """Read a source file and merge configured and source character keys."""

    result = character_keys_from_metadata(path)
    result.extend(key for key in character_keys(load_script(path)) if key not in result)
    return result


def split_pages(script: Script) -> List[Script]:
    """Split source events at clear-screen directives.

    The clear directive is a page boundary rather than page content.  Empty
    pages caused by consecutive boundaries are omitted, while an empty script
    still produces one page for the preview controls.
    """

    pages: List[Script] = []
    current = []
    for event in script.lines:
        if isinstance(event, Directive) and event.command.strip().lower() == "c":
            if current:
                pages.append(Script(current))
                current = []
            continue
        current.append(event)
    if current or not pages:
        pages.append(Script(current))
    return pages


def validate_character_key(value: str) -> str:
    """Validate and return a normalized source character key."""

    key = value.strip()
    if not key:
        raise ValueError("角色缩写不能为空")
    if any(char in key for char in "]\r\n"):
        raise ValueError("角色缩写不能包含 ] 或换行")
    return key


def parse_sleep_text(value: str) -> str:
    """Validate a sleep duration and return its trimmed source spelling."""

    duration = value.strip()
    try:
        number = float(duration)
    except (TypeError, ValueError) as exc:
        raise ValueError("休息时长必须是非负数字") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError("休息时长必须是非负数字")
    return duration


def validate_directive(command: str, value: str = "") -> Directive:
    """Validate a GUI directive and return its format model.

    ``c`` has no parameter, ``s`` takes a finite non-negative number, and
    ``p`` takes a non-empty music key.  Newlines are rejected for every value
    because a source event occupies exactly one line.
    """

    normalized = command.strip().lower()
    normalized = {
        "sleep": "s",
        "clear": "c",
        "music": "p",
    }.get(normalized, normalized)
    if normalized not in {"s", "c", "p"}:
        raise ValueError("不支持的指令：%s" % command)
    if "\r" in value or "\n" in value:
        raise ValueError("指令参数不能包含换行")
    if normalized == "c":
        if value.strip():
            raise ValueError("清空指令不能有参数")
        return Directive("c")
    if normalized == "s":
        return Directive("s", parse_sleep_text(value))
    music = value.strip()
    if not music:
        raise ValueError("音乐简称不能为空")
    return Directive("p", music)
