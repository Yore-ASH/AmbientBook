"""Dependency-free character entry and ANSI style helpers.

The project stores character metadata in ``Scripts/__init__.json``.  This
module deliberately does not import PySide6 so it can be used by scripts and
tests on installations that only need the terminal player.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional, Tuple

# ``None`` means that no foreground color is selected.  Values are the
# standard ANSI foreground codes, including the commonly supported bright set.
ANSI_COLORS = (
    ("无颜色", None),
    ("黑色", 30),
    ("红色", 31),
    ("绿色", 32),
    ("黄色", 33),
    ("蓝色", 34),
    ("紫色", 35),
    ("青色", 36),
    ("白色", 37),
    ("亮黑色", 90),
    ("亮红色", 91),
    ("亮绿色", 92),
    ("亮黄色", 93),
    ("亮蓝色", 94),
    ("亮紫色", 95),
    ("亮青色", 96),
    ("亮白色", 97),
)

ANSI_PRESETS = {
    name: code for name, code in ANSI_COLORS if code is not None
}

_SGR_RE = re.compile(r"^(?:\x1b|\\033|\\x1b|\\u001b)\[([0-9;]*)m$")
_ABBREVIATION_RE = re.compile(r"^[^\]\r\n]+$")


@dataclass(frozen=True)
class CharacterConfig:
    """A validated character configuration."""

    abbreviation: str
    full_name: str
    style: str = ""


def validate_abbreviation(value: str) -> str:
    """Return a trimmed abbreviation suitable as a dialogue key."""

    result = str(value).strip()
    if not result:
        raise ValueError("角色缩写不能为空")
    if not _ABBREVIATION_RE.fullmatch(result):
        raise ValueError("角色缩写不能包含 ] 或换行")
    return result


def validate_full_name(value: str) -> str:
    """Return a trimmed display name suitable for JSON."""

    result = str(value).strip()
    if not result:
        raise ValueError("角色全名不能为空")
    if "\r" in result or "\n" in result:
        raise ValueError("角色全名不能包含换行")
    return result


def _sgr_codes(style: str) -> Tuple[int, ...]:
    match = _SGR_RE.fullmatch(style)
    if not match:
        raise ValueError("ANSI 样式必须是类似 \\033[33;1m 的 SGR 序列")
    raw_codes = match.group(1)
    if not raw_codes:
        return (0,)
    try:
        codes = tuple(int(part) for part in raw_codes.split(";"))
    except ValueError as exc:
        raise ValueError("ANSI 样式包含无效代码") from exc
    if any(code < 0 or code > 107 for code in codes):
        raise ValueError("ANSI 样式包含超出范围的代码")
    # SGR 3/23 are italic on/off.  Character styles intentionally keep the
    # editor's supported vocabulary to colors, bold, underline, and reset.
    if 3 in codes or 23 in codes:
        raise ValueError("不支持斜体 ANSI 样式")
    return codes


def normalize_ansi(value: str) -> str:
    """Validate and normalize an ANSI SGR sequence.

    Literal escape characters and the common ``\\033``/``\\x1b``/``\\u001b`` spellings are
    accepted.  The result contains a real escape character; ``json.dumps``
    turns it into the portable ``\\u001b`` spelling in generated JSON.
    """

    raw = str(value).strip()
    if not raw:
        return ""
    codes = _sgr_codes(raw)
    return "\033[" + ";".join(str(code) for code in codes) + "m"


def build_ansi_style(
    color_code: Optional[int] = None,
    *,
    bold: bool = False,
    underline: bool = False,
) -> str:
    """Build a supported SGR style from a color and text attributes."""

    if color_code is not None:
        color_code = int(color_code)
        if color_code not in {code for _, code in ANSI_COLORS if code is not None}:
            raise ValueError("不支持的 ANSI 颜色代码")
    codes = []
    if bold:
        codes.append(1)
    if underline:
        codes.append(4)
    if color_code is not None:
        codes.append(color_code)
    return "\033[" + ";".join(str(code) for code in codes) + "m" if codes else ""


def _json_string(value: str) -> str:
    # ``ensure_ascii=True`` keeps ESC as ``\\u001b`` when a literal ESC is
    # supplied, while normal project input remains readable Unicode.
    return json.dumps(value, ensure_ascii=True)


def generate_json_entry(
    abbreviation: str,
    full_name: str,
    style: str = "",
) -> str:
    """Generate one valid, indentation-ready entry for ``CHARACTERS``.

    The returned text excludes the surrounding ``CHARACTERS`` braces and has
    no trailing comma, so it can be pasted next to an existing final entry.
    ANSI styles are stored as escaped ``\\u001b`` text when parsed as JSON,
    matching the project's metadata examples.
    """

    key = validate_abbreviation(abbreviation)
    name = validate_full_name(full_name)
    normalized_style = normalize_ansi(style)
    lines = [
        "%s: {" % _json_string(key),
        "    \"NAME\": %s," % _json_string(name),
        "    \"STYLE\": %s" % _json_string(normalized_style),
        "}",
    ]
    return "\n".join(lines)


def generate_json_entries(rows: list[tuple[str, str, str]]) -> str:
    """Generate a complete ``CHARACTERS`` JSON object from multiple rows."""
    entries = []
    seen = set()
    for abbreviation, full_name, style in rows:
        key = validate_abbreviation(abbreviation)
        if key in seen:
            raise ValueError("角色缩写重复：%s" % key)
        seen.add(key)
        entry = generate_json_entry(key, full_name, style)
        entries.append("    " + entry.replace("\n", "\n    "))
    return "{\n" + ",\n".join(entries) + "\n}"


def parse_batch_text(source: str) -> list[tuple[str, str, str]]:
    """Parse ``缩写<TAB>全名<TAB>ANSI样式`` rows for batch generation."""
    rows = []
    for number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) not in {2, 3}:
            raise ValueError("第 %d 行必须是：缩写<TAB>全名<TAB>ANSI样式" % number)
        rows.append((parts[0], parts[1], parts[2] if len(parts) == 3 else ""))
    if not rows:
        raise ValueError("批量输入不能为空")
    return rows


def make_character_config(
    abbreviation: str,
    full_name: str,
    style: str = "",
) -> CharacterConfig:
    """Validate and return a reusable character configuration object."""

    return CharacterConfig(
        validate_abbreviation(abbreviation),
        validate_full_name(full_name),
        normalize_ansi(style),
    )
