"""Author-facing inline marks that compile down to what the player runs.

Writing raw ANSI escapes into a script is unreadable and easy to get wrong, so
the source may use two tags instead:

    \\co?00ffaa染成这个颜色\\co      colour a run of text
    \\ge系统提示\\ge                  treat the run as one unit

Both are *markers*, not syntax the runtime has to understand.  ``\\co`` becomes a
real SGR sequence and ``\\ge`` disappears entirely, which is what makes the whole
scheme cost nothing in the compiled format: that format already stores one delay
per visible character, so a group is simply several characters that happen to
share a timestamp.

The escape character is a backslash, and plot text does not otherwise contain
one, so no existing script changes meaning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import List, Tuple

#: ``\co?RRGGBB`` opens a colour; ``\co`` closes it.
COLOUR_OPEN = r"\\co\?([0-9a-fA-F]{6})"
COLOUR_CLOSE = r"\\co"
#: ``\ge`` toggles grouping: the same token opens and closes.
GROUP_TOGGLE = r"\\ge"

MARK_RE = re.compile("|".join((COLOUR_OPEN, COLOUR_CLOSE, GROUP_TOGGLE)))

#: A true-colour SGR pair: set the foreground, or reset everything.
ANSI_RESET = "\033[0m"


def _colour_escape(hex_value: str) -> str:
    """``00ffaa`` -> the SGR sequence that selects it as the foreground."""

    red, green, blue = (int(hex_value[index:index + 2], 16) for index in (0, 2, 4))
    return "\033[38;2;%d;%d;%dm" % (red, green, blue)


@dataclass(frozen=True)
class Marked:
    """A marked-up string, taken apart."""

    #: The text with every mark resolved to what the renderer expects.
    rendered: str
    #: The text with every mark removed -- what a reader actually sees.
    visible: str
    #: ``(start, end)`` half-open ranges in *visible* index space.
    groups: Tuple[Tuple[int, int], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.visible)


def parse(text: str) -> Marked:
    """Take a marked-up string apart.

    Unbalanced marks are tolerated: an unclosed colour runs to the end of the
    line, and an unclosed group covers the rest of it.  Refusing to render
    because a tag was forgotten would be a worse trade than showing it.
    """

    rendered: List[str] = []
    visible: List[str] = []
    groups: List[Tuple[int, int]] = []
    open_group: int = -1
    position = 0

    for match in MARK_RE.finditer(text):
        chunk = text[position:match.start()]
        if chunk:
            rendered.append(chunk)
            visible.append(chunk)
        position = match.end()

        token = match.group(0)
        if token.startswith("\\co?"):
            rendered.append(_colour_escape(match.group(1)))
        elif token == "\\co":
            rendered.append(ANSI_RESET)
        else:                                   # \ge toggles
            if open_group < 0:
                open_group = sum(len(item) for item in visible)
            else:
                groups.append((open_group, sum(len(item) for item in visible)))
                open_group = -1

    tail = text[position:]
    if tail:
        rendered.append(tail)
        visible.append(tail)

    if open_group >= 0:
        # An unclosed group runs to the end rather than being dropped.
        groups.append((open_group, sum(len(item) for item in visible)))

    return Marked(
        rendered="".join(rendered),
        visible="".join(visible),
        groups=tuple(groups),
    )


def strip(text: str) -> str:
    """The text a reader sees: every mark removed."""

    return parse(text).visible


def render(text: str) -> str:
    """The text a renderer wants: colours as real escapes, groups removed."""

    return parse(text).rendered


def groups(text: str) -> Tuple[Tuple[int, int], ...]:
    """Ranges in visible index space that appear as one unit."""

    return parse(text).groups


def has_marks(text: str) -> bool:
    """Whether the string uses any of this markup at all."""

    return MARK_RE.search(text) is not None


def expand_colours(text: str) -> str:
    """Turn ``\\co`` into real escapes but leave ``\\ge`` in place.

    The parser does this so everything downstream keeps dealing with ordinary
    ANSI.  Group markers stay because the timing model still has to find them --
    they are invisible to length and rendering, but not to the recorder.
    """

    if "\\co" not in text:
        return text

    def replace(match) -> str:
        token = match.group(0)
        if token.startswith("\\co?"):
            return _colour_escape(match.group(1))
        if token == "\\co":
            return ANSI_RESET
        return token                       # a \ge, left alone

    return re.sub("|".join((COLOUR_OPEN, COLOUR_CLOSE)), replace, text)


def strip_groups(text: str) -> str:
    """Remove the group markers, leaving everything else as it is."""

    return text.replace("\\ge", "") if "\\ge" in text else text


#: The true-colour sequence :func:`_colour_escape` produces, and a plain reset.
ANSI_TRUECOLOR_RE = re.compile(r"\x1b\[38;2;(\d+);(\d+);(\d+)m")
ANSI_RESET_RE = re.compile(r"\x1b\[0m")


def contract_colours(text: str) -> str:
    """Turn our own escapes back into marks, for writing a source file.

    The text held in memory has real escape sequences in it -- that is what the
    renderers want.  A ``.tscps`` is read by a person, so the marks go back in
    on the way out.  Escapes that did not come from a mark (a character's own
    style, say) are left exactly as they are: there is no mark that means the
    same thing, and inventing one would change how the line looks.
    """

    if "\x1b" not in text:
        return text

    def contract(match) -> str:
        red, green, blue = (int(value) for value in match.groups())
        return "\\co?%02x%02x%02x" % (red, green, blue)

    # A function rather than a replacement string: ``re`` reads ``\c`` in a
    # replacement as a broken escape.
    return ANSI_RESET_RE.sub(
        lambda _match: "\\co", ANSI_TRUECOLOR_RE.sub(contract, text)
    )


# --------------------------------------------------------------------------
# tokenising, for an editor that shows the markup instead of hiding it
# --------------------------------------------------------------------------

#: Every SGR sequence, literal spelling or real escape, that sets a colour.
ANSI_COLOUR_RE = re.compile(
    r"(?:\\033|\\x1b|\x1b)\[([0-9;]*)m"
)

TEXT = "text"
COLOUR = "colour"
RESET = "reset"
GROUP = "group"
ANSI = "ansi"

#: The eight/eight-bright palette the renderers understand.
ANSI_PALETTE = {
    30: "#000000", 31: "#cc3333", 32: "#33cc66", 33: "#cccc33",
    34: "#4488ff", 35: "#cc66cc", 36: "#33cccc", 37: "#eeeeee",
    90: "#777777", 91: "#ff6666", 92: "#66ee88", 93: "#ffff66",
    94: "#66aaff", 95: "#ee88ee", 96: "#66eeee", 97: "#ffffff",
}


@dataclass(frozen=True)
class Token:
    """One piece of a marked-up line, as an editor would show it.

    ``kind`` is one of :data:`TEXT`, :data:`COLOUR`, :data:`RESET`,
    :data:`GROUP` or :data:`ANSI`.  ``source`` is the exact text that has to go
    back into the file; for a text run it is the run itself, and for a marker it
    is the marker.
    """

    kind: str
    source: str
    #: For a text run: the colour in effect, as ``#rrggbb`` or ``""``.
    colour: str = ""
    #: For a marker: a short label an editor can show on the chip.
    label: str = ""


def _colour_from_sgr(parameters: str) -> str:
    """The ``#rrggbb`` a sequence selects, or ``""`` when it is not a colour."""

    parts = [int(value or 0) for value in parameters.split(";")]
    index = 0
    while index < len(parts):
        code = parts[index]
        if code == 38 and index + 1 < len(parts):
            if parts[index + 1] == 2 and index + 4 < len(parts):
                red, green, blue = parts[index + 2:index + 5]
                return "#%02x%02x%02x" % (red & 0xFF, green & 0xFF, blue & 0xFF)
            if parts[index + 1] == 5:
                return ""
        if code in ANSI_PALETTE:
            return ANSI_PALETTE[code]
        index += 1
    return ""


def tokenize(text: str) -> List[Token]:
    """Break a line into text runs and the markers between them.

    An editor needs to see the markup rather than have it applied, so that a
    colour can be inspected, moved, or deleted.  Each text run carries the
    colour that was in effect where it sits, which is what lets the run be drawn
    in the right colour without re-parsing.
    """

    tokens: List[Token] = []
    colour = ""
    group_open = False
    position = 0
    pattern = re.compile(
        "|".join(
            (
                "(" + COLOUR_OPEN + ")",
                "(" + COLOUR_CLOSE + ")",
                "(" + GROUP_TOGGLE + ")",
                "(" + ANSI_COLOUR_RE.pattern + ")",
            )
        )
    )

    for match in pattern.finditer(text):
        chunk = text[position:match.start()]
        if chunk:
            tokens.append(Token(TEXT, chunk, colour=colour))
        position = match.end()
        token = match.group(0)

        # The token is re-read rather than pulled out by group number: the
        # alternatives nest their own groups, so numbering is easy to get wrong
        # and silently picks up the wrong piece.
        if token.startswith("\\co?"):
            colour = "#" + token[4:].lower()
            tokens.append(Token(COLOUR, token, colour=colour, label=colour))
        elif token == "\\co":
            colour = ""
            tokens.append(Token(RESET, token, label="还原"))
        elif token == "\\ge":
            group_open = not group_open
            tokens.append(
                Token(GROUP, token, label="整体开始" if group_open else "整体结束")
            )
        else:
            parameters = ANSI_COLOUR_RE.match(token)
            codes = parameters.group(1) if parameters else ""
            selected = _colour_from_sgr(codes)
            if selected:
                colour = selected
            elif codes.strip() in ("0", ""):
                colour = ""
            tokens.append(Token(ANSI, token, colour=colour, label=token))

    tail = text[position:]
    if tail:
        tokens.append(Token(TEXT, tail, colour=colour))
    return tokens


# --------------------------------------------------------------------------
# turning tokens into something an editor can draw
# --------------------------------------------------------------------------

def blocks(text: str) -> List[List[Token]]:
    """Group tokens into blocks: a marker and everything it governs.

    An editor wants to draw a box around ``\\co?00ffaa绿灯\\co`` as one thing,
    rather than leaving three unrelated chips in a row.  A colour block runs
    from its opening marker to the matching reset; a group block runs from one
    ``\\ge`` to the next.  Anything between blocks stands on its own.
    """

    grouped: List[List[Token]] = []
    open_colour: List[Token] = []
    open_group: List[Token] = []

    for token in tokenize(text):
        if token.kind == COLOUR:
            if open_colour:                      # an unclosed one, flush it
                grouped.append(open_colour)
            open_colour = [token]
            continue
        if token.kind == RESET and open_colour:
            open_colour.append(token)
            grouped.append(open_colour)
            open_colour = []
            continue
        if token.kind == GROUP:
            if open_group:
                open_group.append(token)
                grouped.append(open_group)
                open_group = []
            else:
                open_group = [token]
            continue
        if open_colour:
            open_colour.append(token)
        elif open_group:
            open_group.append(token)
        else:
            grouped.append([token])

    # Unclosed markers still get shown, running to the end of the line.
    if open_colour:
        grouped.append(open_colour)
    if open_group:
        grouped.append(open_group)
    return grouped


#: Chip colours, kept in one place so the editor and any other view agree.
CHIP_BACKGROUND = "#2f3640"
CHIP_BORDER = "#57606f"
CHIP_TEXT = "#dfe4ea"
GROUP_BACKGROUND = "#3d3a2f"
GROUP_BORDER = "#8a7a3f"


def to_editor_html(text: str, *, background: str = "#1e2229", foreground: str = "#e8e8e8") -> str:
    """Render a line for an editor: markup as framed chips, text in its colour.

    The markup stays visible and labelled, which is the whole point -- hiding a
    colour behind its effect makes it impossible to see, move, or delete.
    """

    output: List[str] = []
    for block in blocks(text):
        markers = [token for token in block if token.kind != TEXT]
        runs = [token for token in block if token.kind == TEXT]

        if not markers:
            for token in runs:
                output.append(_span(token.source, token.colour, foreground))
            continue

        open_token = markers[0]
        label = open_token.label
        if open_token.kind == ANSI:
            # A raw escape: show the bytes, tinted with what they select.
            chip_colour = open_token.colour or CHIP_TEXT
        elif open_token.kind == GROUP:
            chip_colour = "#ffd166"
        else:
            chip_colour = open_token.colour or CHIP_TEXT

        framed = ""

        chips = "".join(
            _chip(token.label, chip_colour, group=token.kind == GROUP)
            for token in markers
        )
        body = "".join(
            _span(token.source, token.colour, foreground) for token in runs
        )
        border = GROUP_BORDER if open_token.kind == GROUP else CHIP_BORDER
        fill = GROUP_BACKGROUND if open_token.kind == GROUP else "transparent"
        output.append(
            '<span style="border:1px solid %s;border-radius:4px;'
            'background:%s;padding:0 2px;">%s%s</span>' % (border, fill, chips, body)
        )

    return (
        '<div style="background:%s;color:%s;padding:6px 8px;'
        'font-family:Consolas,monospace;">%s</div>' % (background, foreground, "".join(output))
    )


def _span(text: str, colour: str, fallback: str) -> str:
    painted = colour or fallback
    return '<span style="color:%s;">%s</span>' % (painted, escape(text))


def _chip(label: str, colour: str, *, group: bool = False) -> str:
    fill = GROUP_BACKGROUND if group else CHIP_BACKGROUND
    border = GROUP_BORDER if group else CHIP_BORDER
    return (
        '<span style="background:%s;border:1px solid %s;border-radius:3px;'
        'color:%s;padding:0 3px;font-size:8pt;">%s</span>'
        % (fill, border, colour, escape(label))
    )




