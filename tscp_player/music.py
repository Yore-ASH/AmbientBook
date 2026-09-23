"""Bind every script line to the music state it should be heard with.

The player and the timing editors share one rule: a track is only (re)loaded
when the script actually switches to a *different* track.  Asking for the track
that is already playing lets it continue, so a sentence always begins exactly
where the previous sentence left off.  This is also why the timeline is stored
as an accumulating offset instead of a per-line restart flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from .format import Dialogue, Directive, Script, visible_text_length


#: Music directive values which mean "stop the music".
STOP_WORDS = frozenset({"", "stop", "none", "停止"})


@dataclass(frozen=True)
class MusicCue:
    """The music state at the start of one script line.

    ``offset`` is the position inside ``track`` in seconds.  It accumulates
    while the track plays, which is what makes per-sentence debugging resume
    from the end of the previous sentence instead of restarting the file.
    """

    track: Optional[str] = None
    offset: float = 0.0
    playing: bool = False

    @property
    def silent(self) -> bool:
        """True when this line should be heard without music."""

        return not self.playing or self.track is None

    def describe(self) -> str:
        if self.silent:
            return "无音乐"
        return "%s @ %.2fs" % (self.track, self.offset)


EMPTY_CUE = MusicCue()


def _seconds(value: object) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0
    if number != number or number in (float("inf"), float("-inf")):  # NaN/inf
        return 0.0
    return max(0.0, number)


def _dialogue_duration(item: Dialogue) -> float:
    """Seconds a dialogue line occupies.

    The compiled format guarantees exactly one delay per *visible* character, so
    surplus entries (only possible in hand-written data) are ignored rather than
    inflating the music offset.
    """

    expected = visible_text_length(item.text)
    values = [_seconds(value) for value in item.delays]
    return sum(values[:expected])


def line_durations(script: Script) -> List[float]:
    """Return how many seconds each line occupies.

    Dialogue lines use their recorded per-character delays, so a script that has
    not been timed yet reports zero for every line.  ``<s>`` reports its wait,
    and ``<c>``/``<p>`` take no time.
    """

    durations: List[float] = []
    for item in script.lines:
        if isinstance(item, Dialogue):
            durations.append(_dialogue_duration(item))
        elif isinstance(item, Directive) and item.command == "s":
            durations.append(_seconds(item.value))
        else:
            durations.append(0.0)
    return durations


def _apply_music(
    track: Optional[str], offset: float, value: str
) -> tuple[Optional[str], float, bool]:
    """Resolve one ``<p>`` directive into the new music state."""

    key = value.strip()
    if key.lower() in STOP_WORDS:
        return None, 0.0, False
    if key == track:
        # The requested track is already loaded: keep playing it from wherever
        # it is now.  This is the whole point of the music timeline.
        return track, offset, True
    # A genuinely different track starts from its beginning.
    return key, 0.0, True


@dataclass
class MusicTimeline:
    """One :class:`MusicCue` per script line, in script order."""

    cues: List[MusicCue]

    def __len__(self) -> int:
        return len(self.cues)

    def at(self, index: int) -> MusicCue:
        """Return the cue for *index*, or an empty cue when out of range."""

        if 0 <= index < len(self.cues):
            return self.cues[index]
        return EMPTY_CUE

    def track_changed(self, index: int) -> bool:
        """Whether line *index* switches to a different track than line -1.

        A ``None`` result (silence) also counts as a change, so callers can
        decide to stop the music.
        """

        return self.at(index).track != (self.at(index - 1) if index > 0 else EMPTY_CUE).track

    def first_offset_of(self, track: str) -> Optional[float]:
        """Offset at which *track* first starts, or ``None`` when unused."""

        for cue in self.cues:
            if cue.track == track and cue.playing:
                return cue.offset
        return None


def build_timeline(
    script: Script, durations: Optional[Sequence[float]] = None
) -> MusicTimeline:
    """Walk *script* and return the music state at the start of every line.

    ``durations`` overrides :func:`line_durations`; the timing editor uses this
    to preview a timeline built from delays that are not yet written back into
    the script.
    """

    if durations is None:
        resolved = line_durations(script)
    else:
        resolved = list(durations)
        if len(resolved) != len(script.lines):
            raise ValueError("durations must have one entry per script line")

    cues: List[MusicCue] = []
    track: Optional[str] = None
    offset = 0.0
    playing = False

    for index, item in enumerate(script.lines):
        if isinstance(item, Directive) and item.command == "p":
            track, offset, playing = _apply_music(track, offset, item.value)
        cues.append(MusicCue(track, offset, playing))
        if playing:
            offset += resolved[index]

    return MusicTimeline(cues)
