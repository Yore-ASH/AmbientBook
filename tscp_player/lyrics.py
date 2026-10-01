"""Lyrics for music tracks: LRC parsing, timed lines and per-script typography.

Everything here is dependency-free so the parsers and the font selection can be
tested without Qt.  A lyric file is always normalised to LRC inside a plot, no
matter whether the author supplied a finished ``.lrc`` or a plain ``.txt`` whose
line timings were recorded by hand.

Two richer forms are understood on top of plain LRC:

* a line written as ``原文|翻译`` carries a translation, shown beside the
  original;
* a whole document may instead be the JSON form (``tscp-lyrics 1``), used when
  lines need their own font or colour -- neither of which LRC can express.

The JSON form is always written *beside* the ``.lrc``, never instead of it, so
the lyrics stay readable by anything that understands LRC.
"""

from __future__ import annotations

import json
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

#: Separates a line's original from its translation.
TRANSLATION_SEPARATOR = "|"
#: ``FORMAT`` value of the JSON lyric document.
LYRIC_DOCUMENT_FORMAT = "tscp-lyrics 1"


class LyricError(ValueError):
    """Raised when a lyric document cannot be parsed."""


def split_translation(text: str) -> Tuple[str, str]:
    """Split ``原文|翻译`` into its two halves.

    Only the first separator counts, so a translation may itself contain one.
    A line without a separator is all original.
    """

    original, separator, translation = text.partition(TRANSLATION_SEPARATOR)
    if not separator:
        return text.strip(), ""
    return original.strip(), translation.strip()


@dataclass(frozen=True)
class LyricLine:
    """One timed line.

    ``text`` is the original only; a translation lives in its own field so the
    two can be laid out on either side of the screen.  ``font`` and ``color``
    are per-line overrides -- empty means "use the track's setting" -- and are
    the reason a plot may also carry the JSON lyric document.
    """

    time: float
    text: str
    translation: str = ""
    font: str = ""
    color: str = ""
    #: A translation may be styled on its own; empty means "follow the original".
    translation_font: str = ""
    translation_color: str = ""

    @property
    def has_translation(self) -> bool:
        return bool(self.translation)

    @property
    def has_style(self) -> bool:
        return bool(
            self.font
            or self.color
            or self.translation_font
            or self.translation_color
        )

    @property
    def text_font(self) -> str:
        """Family for the original half."""

        return self.font

    @property
    def text_color(self) -> str:
        """Colour for the original half."""

        return self.color

    @property
    def translated_font(self) -> str:
        """Family for the translation, falling back to the original's."""

        return self.translation_font or self.font

    @property
    def translated_color(self) -> str:
        """Colour for the translation, falling back to the original's."""

        return self.translation_color or self.color

    @property
    def lrc_text(self) -> str:
        """How this line goes into a plain ``.lrc``, translation included."""

        if self.translation:
            return "%s%s%s" % (self.text, TRANSLATION_SEPARATOR, self.translation)
        return self.text


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

        line = self.line_at(seconds)
        return None if line is None else line.text

    def line_at(self, seconds: float) -> Optional[LyricLine]:
        """The whole line visible at *seconds*, or ``None``.

        Needed wherever the styling or the translation matters, not just the
        text.
        """

        index = self.index_at(seconds)
        return None if index < 0 else self.lines[index]

    def next_after(self, seconds: float) -> Optional[LyricLine]:
        for line in self.lines:
            if line.time > seconds:
                return line
        return None


def _seconds(hours: str, minutes: str, rest: str) -> float:
    # ``rest`` may use a colon or a dot before the fractional part.
    return int(hours or 0) * 3600.0 + int(minutes) * 60.0 + float(rest.replace(":", "."))


def _karaoke_row(line: str) -> Optional[LyricLine]:
    """Convert one ``{"t": ms, "c": [{"tx": ...}]}`` row.

    Some lyric exporters interleave this shape with ordinary LRC rows -- it is
    how they attach per-syllable timing.  We keep the line and its start time
    and drop the syllable breakdown, which is far better than the alternative of
    silently skipping the row and losing the lyrics altogether.
    """

    if not line.startswith("{"):
        return None
    try:
        document = json.loads(line)
    except ValueError:
        return None
    if not isinstance(document, dict):
        return None
    milliseconds = document.get("t")
    chunks = document.get("c")
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)):
        return None
    if not isinstance(chunks, list):
        return None

    pieces: List[str] = []
    for chunk in chunks:
        if isinstance(chunk, dict):
            pieces.append(str(chunk.get("tx", chunk.get("text", ""))))
        elif isinstance(chunk, str):
            pieces.append(chunk)
    body = "".join(pieces).strip()
    if not body:
        return None
    original, translation = split_translation(body)
    return LyricLine(
        time=float(milliseconds) / 1000.0, text=original, translation=translation
    )


def parse_lrc(text: str) -> Lyrics:
    """Parse LRC text into timed lines.

    Supports several timestamps on one line, the ``[offset:±ms]`` tag and the
    usual ``[ti:]``/``[ar:]`` metadata (which is ignored, except for ``offset``).
    Lines without a timestamp are skipped, so a stray header never becomes a
    lyric -- but a per-line karaoke JSON row is understood rather than skipped,
    and ``原文|翻译`` is split into a text and a translation.
    """

    # Windows editors happily save .lrc with a byte-order mark, which would
    # otherwise ride along invisibly on the first lyric line.
    if text.startswith("\ufeff"):
        text = text[1:]

    collected: List[LyricLine] = []
    offset = 0.0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{"):
            converted = _karaoke_row(line)
            if converted is not None:
                collected.append(converted)
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
        original, translation = split_translation(body)
        for hours, minutes, rest in stamps:
            collected.append(
                LyricLine(
                    _seconds(hours, minutes, rest), original, translation=translation
                )
            )
    if not collected:
        return Lyrics([])
    if offset:
        collected = [
            LyricLine(
                max(0.0, item.time + offset),
                item.text,
                item.translation,
                item.font,
                item.color,
                item.translation_font,
                item.translation_color,
            )
            for item in collected
        ]
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
    """Write timed lines back out as LRC so the file stays hand-editable.

    A translation is joined back with ``|``, which is how it came in.
    """

    output = [
        "[%s]%s" % (format_time(line.time), line.lrc_text) for line in lyrics.lines
    ]
    return "\n".join(output) + ("\n" if output else "")


# --------------------------------------------------------------------------
# the JSON document: LRC plus per-line font and colour
# --------------------------------------------------------------------------

def has_line_styles(lyrics: Lyrics) -> bool:
    """Whether any line needs the JSON document to survive a round trip."""

    return any(line.has_style for line in lyrics.lines)


def serialize_lyric_document(lyrics: Lyrics) -> str:
    """Write the richer form: timings, translations and per-line styling.

    Only keys that carry information are emitted, so a document for ordinary
    lyrics stays short and obvious.
    """

    lines: List[Dict[str, object]] = []
    for line in lyrics.lines:
        entry: Dict[str, object] = {"time": round(line.time, 3), "text": line.text}
        if line.translation:
            entry["translation"] = line.translation
        if line.font:
            entry["font"] = line.font
        if line.color:
            entry["color"] = line.color
        if line.translation_font:
            entry["translation_font"] = line.translation_font
        if line.translation_color:
            entry["translation_color"] = line.translation_color
        lines.append(entry)
    document = {"FORMAT": LYRIC_DOCUMENT_FORMAT, "LINES": lines}
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def looks_like_lyric_document(text: str) -> bool:
    """Whether *text* is the JSON form rather than LRC."""

    stripped = text.lstrip("\ufeff \t\r\n")
    return stripped.startswith("{") and '"LINES"' in text


def parse_lyric_document(text: str) -> Lyrics:
    """Read the JSON form back.  Raises :class:`LyricError` if it is malformed."""

    try:
        document = json.loads(text.lstrip("\ufeff"))
    except ValueError as exc:
        raise LyricError("歌词文档不是合法 JSON：%s" % exc) from exc
    if not isinstance(document, dict):
        raise LyricError("歌词文档必须是对象")

    raw_lines = document.get("LINES")
    if not isinstance(raw_lines, list):
        raise LyricError("歌词文档缺少 LINES")

    collected: List[LyricLine] = []
    for entry in raw_lines:
        if not isinstance(entry, dict):
            raise LyricError("LINES 的每一项都必须是对象")
        seconds = entry.get("time")
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise LyricError("歌词行缺少 time：%r" % (entry,))
        body = str(entry.get("text", ""))
        collected.append(
            LyricLine(
                time=float(seconds),
                text=body,
                translation=str(entry.get("translation", "") or ""),
                font=str(entry.get("font", "") or ""),
                color=str(entry.get("color", "") or ""),
                translation_font=str(entry.get("translation_font", "") or ""),
                translation_color=str(entry.get("translation_color", "") or ""),
            )
        )
    collected.sort(key=lambda item: (item.time, item.text))
    return Lyrics(collected)


def parse_lyrics(text: str) -> Lyrics:
    """Parse either form, so callers do not have to know which they hold."""

    if looks_like_lyric_document(text):
        return parse_lyric_document(text)
    return parse_lrc(text)


def lyric_source_lines(text: str) -> List[str]:
    """Split a plain ``lyrics.txt`` into the lines that need recording.

    Blank lines and ``#`` comments are dropped.  If the file already contains
    timestamps it is treated as LRC and only the texts are returned, so a
    half-finished recording can be resumed.
    """

    if TIMESTAMP_RE.search(text) or looks_like_lyric_document(text):
        return [line.lrc_text for line in parse_lyrics(text).lines if line.text]
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


def to_html(
    text: str,
    color: str = "#ffffff",
    available: Iterable[str] = (),
    family: str = "",
) -> str:
    """Render one lyric line as HTML using a font per script.

    Chinese gets 宋体, Latin gets Times New Roman in italic, and anything else
    falls back to the best installed family for its script - or to the widget's
    own default when nothing suitable is installed.  The result is plain inline
    HTML with no background, ready for a transparent label.

    Passing *family* forces that family for the whole line, which is how a
    per-line font override is honoured.
    """

    if not text:
        return ""
    known = list(available)
    pieces = []
    for segment, script in text_runs(text):
        chosen = family or pick_family(script, known)
        style = "color:%s;" % (color or "#ffffff")
        if chosen:
            style += "font-family:'%s';" % chosen
        if not family and wants_italic(script):
            style += "font-style:italic;"
        # A normal space, deliberately: ``&nbsp;`` cannot be broken, so one
        # measuring error turns into text running off the window instead of a
        # line break the renderer could still make by itself.
        escaped = escape(segment)
        pieces.append('<span style="%s">%s</span>' % (style, escaped))
    return "".join(pieces)


# --------------------------------------------------------------------------
# wrapping and the two-column layout
# --------------------------------------------------------------------------

def wrap_units(text: str) -> List[str]:
    """Split *text* into the smallest pieces a line break may fall between.

    Latin words stay whole; a wide (CJK) character is its own unit so Chinese
    wraps at any character, which is what it is supposed to do.
    """

    units: List[str] = []
    buffer = ""
    for character in text:
        if character.isspace():
            if buffer:
                units.append(buffer)
                buffer = ""
            units.append(" ")
        elif unicodedata.east_asian_width(character) in ("W", "F"):
            if buffer:
                units.append(buffer)
                buffer = ""
            units.append(character)
        else:
            buffer += character
    if buffer:
        units.append(buffer)
    return units


def wrap_text(text: str, measure, limit: float) -> List[str]:
    """Break *text* into lines no wider than *limit*, according to *measure*.

    *measure* takes a string and returns its width, so this stays free of Qt and
    can be tested with a plain character count.
    """

    lines: List[str] = []
    for paragraph in str(text).split("\n"):
        current = ""
        for unit in wrap_units(paragraph):
            candidate = current + unit
            if current and measure(candidate.rstrip()) > limit:
                lines.append(current.rstrip())
                current = "" if unit == " " else unit
            else:
                current = candidate
        lines.append(current.rstrip())
    return lines or [""]


def _centre_pad(lines: List[str], rows: int) -> List[str]:
    """Grow *lines* to *rows* by padding evenly above and below."""

    missing = rows - len(lines)
    if missing <= 0:
        return lines
    top = missing // 2
    return [""] * top + lines + [""] * (missing - top)


def plan_lyric_rows(
    original: str,
    translation: str,
    measure,
    max_width: float,
    gap: float = 0.0,
) -> List[Tuple[str, str]]:
    """Lay one lyric line out as rows of ``(原文, 翻译)``.

    With no translation the line is simply wrapped.  With one, the two halves go
    side by side when they fit; when they do not, each gets half the width and
    wraps on its own, so a long pair stays readable instead of running off the
    screen.  Whichever half needs fewer rows is centred against the other.
    """

    original = str(original)
    translation = str(translation)
    if not translation:
        return [(line, "") for line in wrap_text(original, measure, max_width)]

    if measure(original) + gap + measure(translation) <= max_width:
        return [(original, translation)]

    column = max(1.0, (max_width - gap) / 2.0)
    left = wrap_text(original, measure, column)
    right = wrap_text(translation, measure, column)
    rows = max(len(left), len(right))
    return list(zip(_centre_pad(left, rows), _centre_pad(right, rows)))
