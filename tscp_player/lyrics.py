"""Lyrics for music tracks: LRC parsing, timed lines and per-script typography.

Everything here is dependency-free so the parsers and the font selection can be
tested without Qt.  A lyric file is always normalised to LRC inside a plot, no
matter whether the author supplied a finished ``.lrc`` or a plain ``.txt`` whose
line timings were recorded by hand.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from html import escape
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

#: ``[mm:ss.xx]``, ``[mm:ss]`` or ``[hh:mm:ss.xx]``; hours are optional.
TIMESTAMP_RE = re.compile(r"\[(?:(\d+):)?(\d+):(\d+(?:[.:]\d+)?)\]")
#: ``[ti:...]`` style metadata, plus the ``[offset:...]`` special case
TAG_RE = re.compile(r"^\[([a-zA-Z#]+):(.*)\]$")
OFFSET_RE = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]$", re.IGNORECASE)


class LyricError(ValueError):
    """Raised when a lyric document cannot be parsed."""


@dataclass(frozen=True)
class LyricLine:
    time: float
    text: str


@dataclass
class Lyrics:
    """Timed lyric lines, always sorted by time."""

    lines: List[LyricLine]

    def __bool__(self) -> bool:
        return bool(self.lines)

    def __len__(self) -> int:
        return len(self.lines)

    @property
    def duration(self) -> float:
        return self.lines[-1].time if self.lines else 0.0

    def index_at(self, seconds: float) -> int:
        """Index of the line that should be visible at *seconds*, or ``-1``."""

        found = -1
        for index, line in enumerate(self.lines):
            if line.time <= seconds:
                found = index
            else:
                break
        return found

    def at(self, seconds: float) -> Optional[str]:
        """The lyric text visible at *seconds*, or ``None`` before the first line."""

        index = self.index_at(seconds)
        return None if index < 0 else self.lines[index].text

    def next_after(self, seconds: float) -> Optional[LyricLine]:
        for line in self.lines:
            if line.time > seconds:
                return line
        return None


def _seconds(hours: str, minutes: str, rest: str) -> float:
    # ``rest`` may use a colon or a dot before the fractional part.
    return int(hours or 0) * 3600.0 + int(minutes) * 60.0 + float(rest.replace(":", "."))


def parse_lrc(text: str) -> Lyrics:
    """Parse LRC text into timed lines.

    Supports several timestamps on one line, the ``[offset:±ms]`` tag and the
    usual ``[ti:]``/``[ar:]`` metadata (which is ignored, except for ``offset``).
    Lines without a timestamp are skipped, so a stray header never becomes a
    lyric.
    """

    collected: List[LyricLine] = []
    offset = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = OFFSET_RE.match(line)
        if match:
            offset = int(match.group(1)) / 1000.0
            continue
        stamps = TIMESTAMP_RE.findall(line)
        if not stamps:
            # Plain metadata such as [ti:name], or a malformed line: skip it.
            continue
        body = TIMESTAMP_RE.sub("", line)
        body = TAG_RE.sub("", body).strip()
        for hours, minutes, rest in stamps:
            collected.append(LyricLine(_seconds(hours, minutes, rest), body))
    if not collected:
        return Lyrics([])
    if offset:
        collected = [LyricLine(max(0.0, item.time + offset), item.text) for item in collected]
    collected.sort(key=lambda item: (item.time, item.text))
    return Lyrics(collected)


def parse_time(value: str) -> Optional[float]:
    """Parse ``12.34``, ``0:12.34`` or ``1:02:03.5`` into seconds.

    Returns ``None`` for anything unreadable so callers can keep the previous
    value instead of silently writing a wrong time.
    """

    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        return None
    try:
        total = 0.0
        for part in parts:
            total = total * 60.0 + float(part)
    except ValueError:
        return None
    if total != total or total in (float("inf"), float("-inf")):  # NaN/inf
        return None
    return max(0.0, total)


def format_time(seconds: float) -> str:
    """Render seconds as ``mm:ss.xx`` (with an hour field when needed)."""

    total = max(0.0, float(seconds))
    hours, rest = divmod(total, 3600.0)
    minutes, secs = divmod(rest, 60.0)
    if hours >= 1:
        return "%d:%02d:%05.2f" % (int(hours), int(minutes), secs)
    return "%02d:%05.2f" % (int(minutes), secs)


def serialize_lrc(lyrics: Lyrics) -> str:
    """Write timed lines back out as LRC so the file stays hand-editable."""

    output = ["[%s]%s" % (format_time(line.time), line.text) for line in lyrics.lines]
    return "\n".join(output) + ("\n" if output else "")


def lyric_source_lines(text: str) -> List[str]:
    """Split a plain ``lyrics.txt`` into the lines that need recording.

    Blank lines and ``#`` comments are dropped.  If the file already contains
    timestamps it is treated as LRC and only the texts are returned, so a
    half-finished recording can be resumed.
    """

    if TIMESTAMP_RE.search(text):
        return [line.text for line in parse_lrc(text).lines if line.text]
    result = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            result.append(line)
    return result


def timed_from_marks(lines: Sequence[str], marks: Sequence[float]) -> Lyrics:
    """Build lyrics from recorded line-start times.

    ``marks`` holds one timestamp per entry in ``lines``; extra marks are
    ignored and missing ones are dropped, so a partial recording still produces
    a usable file.
    """

    pairs = [
        LyricLine(max(0.0, float(when)), text)
        for text, when in zip(lines, marks)
        if text
    ]
    pairs.sort(key=lambda item: item.time)
    return Lyrics(pairs)


# --------------------------------------------------------------------------
# typography
# --------------------------------------------------------------------------

HAN = "han"
LATIN = "latin"
JAPANESE = "japanese"
KOREAN = "korean"
CYRILLIC = "cyrillic"
GREEK = "greek"
ARABIC = "arabic"
HEBREW = "hebrew"
THAI = "thai"
DEVANAGARI = "devanagari"
NEUTRAL = "neutral"
OTHER = "other"

#: Fonts tried in order for each script.  Serif families come first because the
#: requested look is 宋体 / Times New Roman, both serif.
FONT_CANDIDATES: Dict[str, Tuple[str, ...]] = {
    HAN: ("宋体", "SimSun", "Noto Serif CJK SC", "Source Han Serif SC",
          "Microsoft YaHei", "SimHei", "PingFang SC"),
    JAPANESE: ("ＭＳ 明朝", "MS Mincho", "Yu Mincho", "Noto Serif CJK JP", "Meiryo"),
    KOREAN: ("바탕", "Batang", "Noto Serif CJK KR", "Malgun Gothic"),
    LATIN: ("Times New Roman", "Liberation Serif", "DejaVu Serif", "Noto Serif"),
    CYRILLIC: ("Times New Roman", "Liberation Serif", "DejaVu Serif", "Noto Serif"),
    GREEK: ("Times New Roman", "Liberation Serif", "DejaVu Serif", "Noto Serif"),
    ARABIC: ("Traditional Arabic", "Amiri", "Noto Naskh Arabic", "Segoe UI"),
    HEBREW: ("David", "Noto Serif Hebrew", "Arial"),
    THAI: ("Angsana New", "Noto Serif Thai", "Tahoma"),
    DEVANAGARI: ("Nirmala UI", "Noto Serif Devanagari", "Mangal"),
    OTHER: ("Noto Sans", "Segoe UI", "Arial", "DejaVu Sans"),
}

#: Only the Latin script is asked for in italic; everything else keeps its
#: family's regular face so the text stays readable.
ITALIC_SCRIPTS = frozenset({LATIN})

_SCRIPT_RANGES: Tuple[Tuple[str, Tuple[Tuple[int, int], ...]], ...] = (
    (HAN, ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF),
           (0x3000, 0x303F), (0xFF00, 0xFFEF))),
    (JAPANESE, ((0x3040, 0x30FF),)),
    (KOREAN, ((0x1100, 0x11FF), (0x3130, 0x318F), (0xAC00, 0xD7AF))),
    (CYRILLIC, ((0x0400, 0x052F),)),
    (GREEK, ((0x0370, 0x03FF), (0x1F00, 0x1FFF))),
    (ARABIC, ((0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF))),
    (HEBREW, ((0x0590, 0x05FF),)),
    (THAI, ((0x0E00, 0x0E7F),)),
    (DEVANAGARI, ((0x0900, 0x097F),)),
    (LATIN, ((0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F))),
)


def script_of(character: str) -> str:
    """Classify one character as a script name, or ``neutral``.

    Letters decide the script; digits, punctuation and spaces are neutral so
    they inherit the script of the text around them instead of breaking a run.
    """

    if not character:
        return NEUTRAL
    point = ord(character)
    for script, ranges in _SCRIPT_RANGES:
        for low, high in ranges:
            if low <= point <= high:
                return script
    if character.isspace() or unicodedata.category(character)[0] in {"N", "P", "Z", "S"}:
        return NEUTRAL
    return OTHER


def text_runs(text: str) -> List[Tuple[str, str]]:
    """Split *text* into ``(segment, script)`` runs for per-script fonts.

    Neutral characters join the run they follow so punctuation is not split off
    into its own font.  A leading neutral run borrows the following script.
    """

    if not text:
        return []
    scripts = [script_of(character) for character in text]

    # Give leading neutrals the script of the first real character.
    lead = next((script for script in scripts if script != NEUTRAL), NEUTRAL)
    for index, script in enumerate(scripts):
        if script != NEUTRAL:
            break
        scripts[index] = lead
    # Then let every other neutral inherit the previous resolved script.
    for index in range(1, len(scripts)):
        if scripts[index] == NEUTRAL:
            scripts[index] = scripts[index - 1]

    runs: List[Tuple[str, str]] = []
    start = 0
    for index in range(1, len(scripts) + 1):
        if index == len(scripts) or scripts[index] != scripts[start]:
            runs.append((text[start:index], scripts[start]))
            start = index
    return runs


def pick_family(script: str, available: Iterable[str]) -> str:
    """Best available font family for *script*, or ``""`` to let Qt fall back."""

    known = {name.casefold() for name in available}
    for candidate in FONT_CANDIDATES.get(script, FONT_CANDIDATES[OTHER]):
        if candidate.casefold() in known:
            return candidate
    return ""


def wants_italic(script: str) -> bool:
    """Whether *script* should be rendered in italic."""

    return script in ITALIC_SCRIPTS


def to_html(text: str, color: str = "#ffffff", available: Iterable[str] = ()) -> str:
    """Render one lyric line as HTML using a font per script.

    Chinese gets 宋体, Latin gets Times New Roman in italic, and anything else
    falls back to the best installed family for its script - or to the widget's
    own default when nothing suitable is installed.  The result is plain inline
    HTML with no background, ready for a transparent label.
    """

    if not text:
        return ""
    known = list(available)
    pieces = []
    for segment, script in text_runs(text):
        family = pick_family(script, known)
        style = "color:%s;" % (color or "#ffffff")
        if family:
            style += "font-family:'%s';" % family
        if wants_italic(script):
            style += "font-style:italic;"
        escaped = escape(segment).replace(" ", "&nbsp;")
        pieces.append('<span style="%s">%s</span>' % (style, escaped))
    return "".join(pieces)
