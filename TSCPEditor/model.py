"""Dependency-free helpers used by TSCPEditor and its tests."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List, Union

from tscp_player.format import (
    Script,
    parse_tscp,
    parse_tscps,
    serialize_tscp,
    visible_text_length,
)


PathLike = Union[str, Path]


def load_script(path: PathLike) -> Script:
    """Read a compiled UTF-8 ``.tscp`` file."""

    return parse_tscp(Path(path).read_text(encoding="utf-8"))


def save_script(path: PathLike, script: Script) -> None:
    """Serialize *script* to a compiled UTF-8 ``.tscp`` file."""

    Path(path).write_text(serialize_tscp(script), encoding="utf-8")


def character_keys_from_source(path: PathLike) -> List[str]:
    """Return character keys from a source plot or ``Scripts/__init__.json``.

    Source plots are useful even when no package metadata exists.  Metadata is
    also accepted because it is the normal place for display names and styles.
    Keys retain their first-seen order.
    """

    source_path = Path(path)
    if source_path.is_dir():
        metadata = source_path / "Scripts" / "__init__.json"
        if metadata.exists():
            source_path = metadata
        else:
            raise ValueError("source directory has no Scripts/__init__.json")

    text = source_path.read_text(encoding="utf-8")
    keys: List[str] = []
    if source_path.suffix.lower() == ".json":
        data = json.loads(text)
        characters = data.get("CHARACTERS", {})
        if not isinstance(characters, dict):
            raise ValueError("CHARACTERS must be an object")
        keys.extend(str(key) for key in characters)
    else:
        script = parse_tscps(text)
        for line in script.lines:
            character = getattr(line, "character", None)
            if character and character not in keys:
                keys.append(character)
    return keys


def parse_delay_text(
    value: str, text_length: int, default_delay: float = 0.05
) -> List[float]:
    """Parse the editor's optional JSON/comma-separated per-character delays.

    A blank value applies ``default_delay`` to every character.  The result is
    always validated against the text length required by     ``serialize_tscp``.  ANSI presentation sequences do not count as
    characters, matching the compiled format's timing rules.
    """

    if text_length < 0:
        raise ValueError("text length cannot be negative")
    if not value.strip():
        values = [float(default_delay)] * text_length
    else:
        raw = value.strip()
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = [part.strip() for part in raw.split(",") if part.strip()]
        if not isinstance(decoded, list):
            raise ValueError("delays must be a JSON list or comma-separated list")
        try:
            values = [float(item) for item in decoded]
        except (TypeError, ValueError) as exc:
            raise ValueError("delays must contain numbers") from exc
    if len(values) != text_length:
        raise ValueError(
            "delay count (%d) must match text length (%d)"
            % (len(values), text_length)
        )
    if any(not math.isfinite(item) or item < 0 for item in values):
        raise ValueError("delays must be finite and non-negative")
    return values
