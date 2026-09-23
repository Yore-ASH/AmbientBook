"""Timing and naming models for the ``.tscps`` to ``.tscp`` converter."""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
)

from tscp_player.format import (
    ANSI_SEQUENCE_RE,
    Dialogue,
    Directive,
    Script,
    parse_tscps,
    serialize_tscp,
)
from tscp_player.music import MusicTimeline, build_timeline


PathLike = Union[str, Path]


class TimingMode(str, Enum):
    """How the stopwatch behaves between dialogue lines."""

    PER_SENTENCE = "sentence"
    CONTINUOUS = "continuous"
    # Friendly aliases used by callers that describe the UI labels.
    SENTENCE = "sentence"


def normalize_mode(mode: Union[TimingMode, str]) -> TimingMode:
    if isinstance(mode, TimingMode):
        return mode
    value = str(mode).strip().lower()
    aliases = {
        "sentence": TimingMode.PER_SENTENCE,
        "per_sentence": TimingMode.PER_SENTENCE,
        "per-sentence": TimingMode.PER_SENTENCE,
        "逐句": TimingMode.PER_SENTENCE,
        "continuous": TimingMode.CONTINUOUS,
        "stream": TimingMode.CONTINUOUS,
        "连续": TimingMode.CONTINUOUS,
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise ValueError("unknown timing mode: %s" % mode) from exc


def visible_characters(text: str) -> List[str]:
    """Return characters which should receive timing entries.

    ANSI control sequences may be literal (``\\033[31m``) or already expanded
    terminal escapes.  Neither form is visible and neither consumes a key.
    """

    return list(ANSI_SEQUENCE_RE.sub("", text))


def is_timed_character(character: str) -> bool:
    """Return whether *character* consumes a keyboard timing event.

    Every visible character is timed, punctuation and symbols included, so an
    author can give a comma or a full stop its own pause.  Only whitespace is
    still rendered immediately, because a space has no length of its own to
    perform.  ``unicodedata`` is used instead of an ASCII-only table so Chinese
    and full-width punctuation behave the same as their ASCII counterparts.
    """

    if not character or character.isspace():
        return False
    return not unicodedata.category(character).startswith("Z")


def timed_character_positions(text: str) -> List[int]:
    """Return visible indices which need a key press.

    Every visible character needs one except whitespace.  Indices refer to
    :func:`visible_characters`, not the raw string; ANSI escape sequences
    therefore never affect the positions.
    """

    return [
        index
        for index, character in enumerate(visible_characters(text))
        if is_timed_character(character)
    ]


def timed_character_indices(text: str) -> List[int]:
    """Return only the visible indices which consume timing keys."""

    return timed_character_positions(text)


def split_pages(script: Script) -> List[Script]:
    """Split source content at ``<c>`` without retaining the boundary."""

    pages: List[Script] = []
    current = []
    for event in script.lines:
        if isinstance(event, Directive) and event.command.lower() == "c":
            if current:
                pages.append(Script(current))
                current = []
            continue
        current.append(event)
    if current or not pages:
        pages.append(Script(current))
    return pages


def compiled_name(source: PathLike) -> str:
    """Return the canonical compiled filename for a source filename."""

    path = Path(source)
    if path.suffix.lower() in {".tscps", ".tscp"}:
        return path.with_suffix(".tscp").name
    return path.name + ".tscp"


def output_path(source: PathLike, destination: Optional[PathLike] = None) -> Path:
    """Resolve an output path, avoiding ``foo.tscps.tscp`` surprises."""

    source_path = Path(source)
    if destination is None:
        return source_path.with_name(compiled_name(source_path))
    target = Path(destination)
    if target.suffix.lower() not in {".tscp", ""}:
        target = target.with_suffix(".tscp")
    elif not target.suffix:
        target = target.with_suffix(".tscp")
    return target


@dataclass
class _SentenceTiming:
    characters: List[str]
    timed_positions: List[int]
    previous: Optional[float] = None
    delays: List[float] = None  # type: ignore[assignment]
    _timed_cursor: int = 0

    def __post_init__(self) -> None:
        # The serializer requires one delay per visible character.  Punctuation
        # gets an explicit zero while only timed positions advance this cursor.
        self.delays = [0.0] * len(self.characters)

    def start(self, timestamp: float) -> None:
        self.previous = timestamp

    def record(self, timestamp: float) -> bool:
        if self.complete:
            return False
        if self.previous is None:
            self.previous = timestamp
        visible_index = self.timed_positions[self._timed_cursor]
        self.delays[visible_index] = max(0.0, timestamp - self.previous)
        self.previous = timestamp
        self._timed_cursor += 1
        return True

    @property
    def complete(self) -> bool:
        return self._timed_cursor == len(self.timed_positions)

    @property
    def current_visible_index(self) -> Optional[int]:
        if self.complete:
            return None
        return self.timed_positions[self._timed_cursor]


def normalise_delays(delays: Sequence[float], text: str) -> List[float]:
    """Force *delays* to hold exactly one non-negative entry per visible char."""

    expected = len(visible_characters(text))
    values = [max(0.0, float(value)) for value in delays]
    if len(values) < expected:
        values.extend([0.0] * (expected - len(values)))
    return values[:expected]


class KeyboardTimingModel:
    """Small event model suitable for terminals, Qt, or other front ends.

    :meth:`arm` prepares the first sentence without starting the clock, so the
    first accepted key becomes the timing baseline: the pause between pressing
    开始 and pressing the first key is never recorded.  :meth:`start` keeps the
    older behaviour where the baseline is set immediately.

    ``targets`` restricts which dialogue lines need key input.  Lines outside
    the target set keep their ``base_delays`` untouched, which is how a single
    sentence can be re-designed without losing the rest of the recording.  When
    ``targets`` is ``None`` every dialogue is recorded, as before.
    """

    def __init__(
        self,
        source: Script,
        mode: Union[TimingMode, str] = TimingMode.PER_SENTENCE,
        clock: Callable[[], float] = time.monotonic,
        accepted_keys: Optional[Iterable[object]] = None,
        event_callback: Optional[Callable[[int, object], None]] = None,
        base_delays: Optional[Mapping[int, Sequence[float]]] = None,
        targets: Optional[Iterable[int]] = None,
    ) -> None:
        self.source = source
        self.mode = normalize_mode(mode)
        self.clock = clock
        self.accepted_keys = (
            {"Enter", "Return", "\r", "\n", "Space", " "}
            if accepted_keys is None
            else {str(key) for key in accepted_keys}
        )
        self.event_callback = event_callback
        self._dialogues = [
            (index, item)
            for index, item in enumerate(source.lines)
            if isinstance(item, Dialogue)
        ]
        self._ordinal = {
            index: ordinal for ordinal, (index, _) in enumerate(self._dialogues)
        }
        # Delays already carried by the source are kept unless the caller
        # overrides them, so a timed script can be re-opened and only one
        # sentence re-recorded.
        seeded: Dict[int, List[float]] = {}
        for index, item in self._dialogues:
            if item.delays:
                seeded[index] = normalise_delays(item.delays, item.text)
        for index, values in (base_delays or {}).items():
            line = source.lines[int(index)]
            if isinstance(line, Dialogue):
                seeded[int(index)] = normalise_delays(values, line.text)
        self.base_delays: Dict[int, List[float]] = seeded
        if targets is None:
            self._order = [index for index, _ in self._dialogues]
        else:
            wanted = {int(index) for index in targets}
            self._order = [
                index for index, _ in self._dialogues if index in wanted
            ]
        self._delays: Dict[int, List[float]] = {}
        self._position = 0
        self._active: Optional[_SentenceTiming] = None
        self._started = False
        self._continuous_previous: Optional[float] = None
        self._current_event_index: Optional[int] = None
        self._current_dialogue_index: Optional[int] = None
        self._handled_directives: List[int] = []

    @property
    def armed(self) -> bool:
        """Whether the first target is prepared but the clock has not started."""

        return self._active is not None and not self._started

    def arm(self) -> None:
        """Prepare the first target *without* starting the clock.

        The first accepted key then becomes the baseline, so the delay between
        pressing 开始/重新计时 and pressing the first key is discarded instead of
        landing on the first character.
        """

        if self._started or self._active is not None:
            return
        self._activate(self.clock())

    def start(self, timestamp: Optional[float] = None) -> None:
        if self._started:
            return
        self._started = True
        self._activate(timestamp if timestamp is not None else self.clock())

    def _activate(self, timestamp: float) -> None:
        while self._position < len(self._order):
            index = self._order[self._position]
            dialogue = self.source.lines[index]
            self._process_directives_before(index)
            characters = visible_characters(dialogue.text)
            self._current_event_index = index
            self._current_dialogue_index = self._ordinal.get(index)
            if self.event_callback is not None:
                self.event_callback(index, dialogue)
            timed_positions = timed_character_positions(dialogue.text)
            if not timed_positions:
                self._delays[index] = [0.0] * len(characters)
                self._position += 1
                continue
            self._active = _SentenceTiming(characters, timed_positions)
            if self.mode is TimingMode.CONTINUOUS and self._continuous_previous is not None:
                self._active.start(self._continuous_previous)
            else:
                self._active.start(timestamp)
            return
        self._active = None
        self._process_trailing_directives()
        self._current_event_index = None
        self._current_dialogue_index = None

    def _process_directives_before(self, dialogue_index: int) -> None:
        """Notify the UI about directives between the previous and next line."""

        start = 0
        if self._position:
            start = self._order[self._position - 1] + 1
        for index in range(start, dialogue_index):
            item = self.source.lines[index]
            if isinstance(item, Directive) and index not in self._handled_directives:
                self._handled_directives.append(index)
                self._current_event_index = index
                if self.event_callback is not None:
                    self.event_callback(index, item)

    def _process_trailing_directives(self) -> None:
        start = self._order[-1] + 1 if self._order else 0
        for index in range(start, len(self.source.lines)):
            item = self.source.lines[index]
            if isinstance(item, Directive) and index not in self._handled_directives:
                self._handled_directives.append(index)
                self._current_event_index = index
                if self.event_callback is not None:
                    self.event_callback(index, item)

    def handle_key(self, key: object, timestamp: Optional[float] = None) -> bool:
        """Handle one keyboard event and return whether it was accepted."""

        key_name = str(key)
        if key_name not in self.accepted_keys:
            return False
        now = self.clock() if timestamp is None else timestamp
        if not self._started:
            self._started = True
            if self._active is None:
                self._activate(now)
            else:
                # The first accepted key defines the baseline for an armed run.
                self._active.start(now)
        if self._active is None:
            return False
        accepted = self._active.record(now)
        if not accepted:
            return False
        self._continuous_previous = now
        if self._active.complete:
            index = self._order[self._position]
            self._delays[index] = list(self._active.delays)
            self._position += 1
            # Sentence mode gets a fresh baseline for every line.  Continuous
            # mode deliberately carries the last accepted key across lines.
            next_index = (
                self._order[self._position]
                if self._position < len(self._order)
                else None
            )
            next_needs_timing = next_index is not None and bool(
                timed_character_positions(self.source.lines[next_index].text)
            )
            next_start = (
                self.clock()
                if self.mode is TimingMode.PER_SENTENCE
                and next_needs_timing
                else now
            )
            self._activate(
                next_start
            )
        return True

    @property
    def targets(self) -> List[int]:
        """Dialogue line indices this run records, in script order."""

        return list(self._order)

    @property
    def complete(self) -> bool:
        return self._started and self._position >= len(self._order)

    @property
    def current_event_index(self) -> Optional[int]:
        """Index of the currently selected source event."""

        return self._current_event_index

    @property
    def current_dialogue_index(self) -> Optional[int]:
        return self._current_dialogue_index

    @property
    def current_sentence_index(self) -> Optional[int]:
        """One-based sentence/dialogue index for status displays."""

        return None if self._current_dialogue_index is None else self._current_dialogue_index + 1

    @property
    def current_character_index(self) -> Optional[int]:
        return self._active.current_visible_index if self._active else None

    @property
    def recorded_count(self) -> int:
        completed = sum(
            len(timed_character_positions(item.text))
            for index, item in enumerate(self.source.lines)
            if isinstance(item, Dialogue) and index in self._delays
        )
        return completed + (self._active._timed_cursor if self._active else 0)

    @property
    def total_count(self) -> int:
        return sum(len(timed_character_positions(item.text)) for item in self.source.lines if isinstance(item, Dialogue))

    @property
    def run_total_count(self) -> int:
        """Timing slots this particular run is responsible for."""

        return sum(
            len(timed_character_positions(self.source.lines[index].text))
            for index in self._order
        )

    @property
    def visible_count(self) -> int:
        return sum(len(visible_characters(item.text)) for item in self.source.lines if isinstance(item, Dialogue))

    def progress_for_event(self, index: int) -> Tuple[int, int]:
        """Return ``(recorded, total)`` timing slots for a dialogue event.

        A line outside this run that already has ``base_delays`` counts as fully
        recorded, because it keeps those values unchanged.
        """

        item = self.source.lines[index]
        if not isinstance(item, Dialogue):
            return (0, 0)
        total = len(timed_character_positions(item.text))
        if index in self._delays:
            return (total, total)
        if self._current_event_index == index and self._active is not None:
            return (self._active._timed_cursor, total)
        if index in self.base_delays:
            return (total, total)
        return (0, total)

    def known_delays(self) -> Dict[int, List[float]]:
        """Every delay known so far, kept lines merged with this run's results.

        The active sentence contributes its in-progress values, so callers can
        preview the music timeline while a sentence is still being recorded.
        """

        merged: Dict[int, List[float]] = dict(self.base_delays)
        merged.update(self._delays)
        if self._active is not None and self._current_event_index is not None:
            merged[self._current_event_index] = list(self._active.delays)
        return merged

    def preview_script(self) -> Script:
        """The script with every delay known so far, without a completeness check."""

        known = self.known_delays()
        lines = []
        for index, item in enumerate(self.source.lines):
            if isinstance(item, Dialogue):
                values = known.get(index)
                if values is None:
                    values = [0.0] * len(visible_characters(item.text))
                lines.append(Dialogue(item.character, item.text, list(values)))
            else:
                lines.append(item)
        return Script(lines)

    def music_timeline(self) -> MusicTimeline:
        """The music state bound to every line, from the delays known so far."""

        return build_timeline(self.preview_script())

    def result(self) -> Script:
        if not self.complete:
            raise RuntimeError("timing is not complete")
        return self.preview_script()


def compile_script(
    source: Script,
    mode: Union[TimingMode, str] = TimingMode.PER_SENTENCE,
    input_fn: Optional[Callable[[str], object]] = None,
    output_fn: Optional[Callable[[str], object]] = None,
    clock: Callable[[], float] = time.monotonic,
) -> Script:
    """Record one keyboard event per timed character in *source*."""

    input_fn = input if input_fn is None else input_fn
    output_fn = print if output_fn is None else output_fn
    selected_mode = normalize_mode(mode)
    model = KeyboardTimingModel(source, selected_mode, clock=clock)
    model.start(clock())
    for index, item in enumerate(source.lines):
        if not isinstance(item, Dialogue):
            continue
        output_fn(
            "\n" + (("[%s]" % item.character) if item.character else "[旁白]") + item.text
        )
        for character in (visible_characters(item.text)[index] for index in timed_character_positions(item.text)):
            input_fn("按 Enter 播放下一个字（%s）: " % character)
            model.handle_key("Enter")
    return model.result()


def compile_file(
    source_path: PathLike,
    output_path_value: Optional[PathLike] = None,
    mode: Union[TimingMode, str] = TimingMode.PER_SENTENCE,
    input_fn: Optional[Callable[[str], object]] = None,
    output_fn: Optional[Callable[[str], object]] = None,
    clock: Callable[[], float] = time.monotonic,
) -> Path:
    """Compile a source file and return the written output path."""

    source = parse_tscps(Path(source_path).read_text(encoding="utf-8"))
    target = output_path(source_path, output_path_value)
    target.write_text(
        serialize_tscp(
            compile_script(source, mode, input_fn=input_fn, output_fn=output_fn, clock=clock)
        ),
        encoding="utf-8",
    )
    return target


# Descriptive aliases make the small event model convenient for GUI adapters.
TimingRecorder = KeyboardTimingModel
CharacterTimingModel = KeyboardTimingModel
name_for_source = compiled_name
compiled_path = output_path
