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


