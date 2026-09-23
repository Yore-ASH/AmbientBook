"""Parsers and serializers for the source ``.tscps`` and compiled ``.tscp`` formats."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Union


FORMAT_VERSION = 1
# Match literal spellings used in source files as well as real terminal
# escapes.  CSI is intentionally broader than SGR (``...m``): cursor,
# erase, and other ANSI controls are presentation-only and must not consume
# a timing slot either.
ANSI_SEQUENCE_RE = re.compile(
    r"(?:\\033|\\x1b|\x1b)\[[0-?]*[ -/]*[@-~]"
    r"|\x9b[0-?]*[ -/]*[@-~]"
)


def visible_text_length(text: str) -> int:
    """Count printable characters while ignoring embedded ANSI SGR sequences."""
    return len(ANSI_SEQUENCE_RE.sub("", text))


class TSCPError(ValueError):
    """Raised when a script cannot be parsed."""


@dataclass(frozen=True)
class Dialogue:
    character: Optional[str]
    text: str
    delays: List[float] = field(default_factory=list)


@dataclass(frozen=True)
class Directive:
    command: str
    value: str = ""


Line = Union[Dialogue, Directive]


@dataclass
class Script:
    lines: List[Line] = field(default_factory=list)

    def iter_lines(self) -> Iterable[Line]:
        return iter(self.lines)


def parse_tscps(source: str) -> Script:
    lines: List[Line] = []
    for number, raw in enumerate(source.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("<") and ">" in line:
            end = line.index(">")
            command, value = line[1:end].strip().lower(), line[end + 1:].strip()
            if command not in {"s", "c", "p"}:
                raise TSCPError("line %d: unknown control <%s>" % (number, command))
            if command == "s":
                try:
                    float(value)
                except ValueError as exc:
                    raise TSCPError("line %d: invalid sleep duration" % number) from exc
            lines.append(Directive(command, value))
            continue
        if line.startswith("["):
            end = line.find("]")
            if end < 2:
                raise TSCPError("line %d: malformed character prefix" % number)
            lines.append(Dialogue(line[1:end], line[end + 1:].lstrip()))
        else:
            lines.append(Dialogue(None, line))
    return Script(lines)


def serialize_tscps(script: Script) -> str:
    """Serialize a source ``.tscps`` script.

    Source files intentionally do not contain timing data.  The parser trims
    insignificant whitespace around directives and character prefixes, so the
    serializer emits the canonical equivalent spelling of each parsed event.
    """

    output: List[str] = []
    for item in script.lines:
        if isinstance(item, Dialogue):
            if "\r" in item.text or "\n" in item.text:
                raise TSCPError("dialogue text cannot contain a newline")
            if item.character is None:
                if not item.text.strip():
                    raise TSCPError("narrator text cannot be empty")
                output.append(item.text)
            else:
                character = item.character
                if (
                    not character
                    or "]" in character
                    or "\r" in character
                    or "\n" in character
                ):
                    raise TSCPError("invalid character key")
                output.append("[%s]%s" % (character, item.text))
            continue

        command = item.command.strip().lower()
        if command not in {"s", "c", "p"}:
            raise TSCPError("unsupported directive: " + item.command)
        if "\r" in item.value or "\n" in item.value:
            raise TSCPError("directive value cannot contain a newline")
        if command == "s":
            try:
                duration = float(item.value)
            except (TypeError, ValueError) as exc:
                raise TSCPError("invalid sleep duration") from exc
            if duration < 0:
                raise TSCPError("sleep duration cannot be negative")
        output.append("<%s>%s" % (command, item.value))
    return "\n".join(output) + ("\n" if output else "")


def _encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _decode(value: str, number: int) -> str:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise TSCPError("line %d: invalid text encoding" % number) from exc


def serialize_tscp(script: Script) -> str:
    """Serialize one event per line; text is base64 to make separators safe.

    D|character-or-empty|text|delay,delay
    N|text|delay,delay
    S|seconds, C, and P|music-abbreviation
    """
    output = ["TSCP %d" % FORMAT_VERSION]
    for item in script.lines:
        if isinstance(item, Directive):
            if item.command == "s":
                output.append("S|" + item.value)
            elif item.command == "c":
                output.append("C")
            elif item.command == "p":
                output.append("P|" + item.value)
            else:
                raise TSCPError("unsupported directive: " + item.command)
            continue
        character_count = visible_text_length(item.text)
        durations = item.delays or [0.0] * character_count
        if len(durations) != character_count:
            raise TSCPError("delay count does not match text length")
        encoded_delays = ",".join("%.6f" % max(0.0, float(value)) for value in durations)
        if item.character is None:
            output.append("N|%s|%s" % (_encode(item.text), encoded_delays))
        else:
            output.append("D|%s|%s|%s" % (item.character, _encode(item.text), encoded_delays))
    return "\n".join(output) + "\n"


def parse_tscp(source: str) -> Script:
    rows = source.splitlines()
    if not rows or rows[0].strip() != "TSCP %d" % FORMAT_VERSION:
        raise TSCPError("compiled script must start with 'TSCP %d'" % FORMAT_VERSION)
    result: List[Line] = []
    for number, raw in enumerate(rows[1:], 2):
        if not raw:
            continue
        parts = raw.split("|")
        try:
            if parts[0] == "C" and len(parts) == 1:
                result.append(Directive("c"))
            elif parts[0] == "S" and len(parts) == 2:
                float(parts[1])
                result.append(Directive("s", parts[1]))
            elif parts[0] == "P" and len(parts) == 2:
                result.append(Directive("p", parts[1]))
            elif parts[0] in {"D", "N"}:
                expected = 4 if parts[0] == "D" else 3
                if len(parts) != expected:
                    raise ValueError
                character = parts[1] if parts[0] == "D" else None
                encoded_text = parts[2] if parts[0] == "D" else parts[1]
                encoded_delays = parts[3] if parts[0] == "D" else parts[2]
                text = _decode(encoded_text, number)
                delays = [] if not encoded_delays else [float(v) for v in encoded_delays.split(",")]
                if len(delays) != visible_text_length(text):
                    raise ValueError
                result.append(Dialogue(character, text, delays))
            else:
                raise ValueError
        except (ValueError, IndexError) as exc:
            raise TSCPError("line %d: malformed compiled event" % number) from exc
    return Script(result)
