"""The timeline view used by the timing step.

Two tracks share one time axis: the story (blocks you can drag to change how
long a line takes) and the music (segments, with every lyric line marked).
Supplements get their own thin lane because they run alongside the story rather
than inside it.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from Studio import model as studio
from tscp_player.format import Dialogue, Directive, Script
from tscp_player.lyrics import LyricLine
from tscp_player.music import build_timeline, line_durations, note_spans
from tscp_player.plot import Character, MusicTrack

#: Where the lanes begin, leaving room for the labels down the left.
GUTTER = 118
RULER_HEIGHT = 24
TEXT_LANE_HEIGHT = 54
MUSIC_LANE_HEIGHT = 42
NOTE_LANE_HEIGHT = 30
LANE_GAP = 4
#: How close to a block's right edge counts as grabbing its handle.
EDGE_GRAB = 7

DEFAULT_SCALE = 70.0            # pixels per second
MIN_SCALE = 8.0
MAX_SCALE = 600.0

BACKDROP = "#101215"
LANE = "#171a1f"
LANE_EDGE = "#262c34"
TEXT = "#d8dde3"
DIM = "#7d8794"
ACCENT = "#f6d365"
SLEEP_FILL = "#242a31"
MARKER = "#4b5563"


def kind_colour(style: str, fallback: str = "#5b6472") -> QColor:
    """The colour a character's style paints with, for a block fill."""

    import re

    match = re.search(r"\x1b\[([0-9;]*)m", str(style).replace(r"\033", "\x1b"))
    if not match:
        return QColor(fallback)
    table = {
        30: "#000000", 31: "#cc3333", 32: "#33cc66", 33: "#cccc33",
        34: "#4488ff", 35: "#cc66cc", 36: "#33cccc", 37: "#eeeeee",
        90: "#777777", 91: "#ff6666", 92: "#66ee88", 93: "#ffff66",
        94: "#66aaff", 95: "#ee88ee", 96: "#66eeee", 97: "#ffffff",
    }
    for raw in match.group(1).split(";"):
        try:
            code = int(raw or 0)
        except ValueError:
            continue
        if code in table:
            return QColor(table[code])
    return QColor(fallback)


class TimelineView(QWidget):
    """A draggable picture of how the script spends its time."""

    blockSelected = Signal(int)
    durationChanged = Signal(int, float)
    statusMessage = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.script: Optional[Script] = None
        self.characters: Dict[str, Character] = {}
        self.tracks: Dict[str, MusicTrack] = {}
        self.lyrics: Dict[str, List[LyricLine]] = {}
        self.selected: Optional[int] = None
        self.scale = DEFAULT_SCALE
        self.offset = 0.0                  # leftmost second on screen

        self._durations: List[float] = []
        self._starts: List[float] = []
        self._drag_index: Optional[int] = None
        self._drag_origin = 0.0
        self._drag_seconds = 0.0
        self._panning = False
        self._pan_anchor = 0

        self.setMinimumHeight(
            RULER_HEIGHT + TEXT_LANE_HEIGHT + MUSIC_LANE_HEIGHT + NOTE_LANE_HEIGHT + 40
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    # -- contents ------------------------------------------------------
    def set_script(
        self,
        script: Optional[Script],
        characters: Optional[Dict[str, Character]] = None,
        tracks: Optional[Dict[str, MusicTrack]] = None,
        lyrics: Optional[Dict[str, List[LyricLine]]] = None,
    ) -> None:
        self.script = script
        self.characters = dict(characters or {})
        self.tracks = dict(tracks or {})
        self.lyrics = dict(lyrics or {})
        self.rebuild()

    def rebuild(self) -> None:
        """Recompute the layout from the script."""

        if self.script is None:
            self._durations, self._starts = [], []
        else:
            self._durations = line_durations(self.script)
            starts: List[float] = []
            clock = 0.0
            for value in self._durations:
                starts.append(clock)
                clock += value
            self._starts = starts
        self.update()

    @property
    def total_seconds(self) -> float:
        return sum(self._durations) if self._durations else 0.0

    def select(self, index: Optional[int]) -> None:
        self.selected = index
        self.update()

    # -- geometry ------------------------------------------------------
    def x_for(self, seconds: float) -> float:
        return GUTTER + (seconds - self.offset) * self.scale

    def seconds_for(self, x: float) -> float:
        return self.offset + (max(GUTTER, x) - GUTTER) / self.scale

    def lane_tops(self) -> Dict[str, float]:
        ruler = 0.0
        text = RULER_HEIGHT
        music = text + TEXT_LANE_HEIGHT + LANE_GAP
        note = music + MUSIC_LANE_HEIGHT + LANE_GAP
        return {"ruler": ruler, "text": text, "music": music, "note": note}

    def set_scale(self, value: float, anchor_x: Optional[float] = None) -> None:
        """Zoom, keeping the second under *anchor_x* (or the left edge) still."""

        new_scale = max(MIN_SCALE, min(MAX_SCALE, float(value)))
        if anchor_x is None or anchor_x <= GUTTER:
            self.scale = new_scale
        else:
            anchor_time = self.seconds_for(anchor_x)
            self.scale = new_scale
            self.offset = anchor_time - (anchor_x - GUTTER) / self.scale
        self.offset = max(0.0, self.offset)
        self.update()

    def fit(self) -> None:
        width = max(1, self.width() - GUTTER - 16)
        total = self.total_seconds or 1.0
        self.scale = max(MIN_SCALE, min(MAX_SCALE, width / total))
        self.offset = 0.0
        self.update()

    def _block_rect(self, index: int) -> QRectF:
        top = self.lane_tops()["text"]
        start = self._starts[index]
        width = max(2.0, self._durations[index] * self.scale)
        if not isinstance(self.script.lines[index], Dialogue):
            width = max(3.0, min(width, 10.0))
        return QRectF(self.x_for(start), top + 3, width, TEXT_LANE_HEIGHT - 6)

    def index_at(self, x: float, y: float) -> Optional[int]:
        if self.script is None:
            return None
        top = self.lane_tops()["text"]
        if not (top <= y <= top + TEXT_LANE_HEIGHT):
            return None
        for index in range(len(self.script.lines)):
            if self._block_rect(index).contains(x, y):
                return index
        return None

    # -- painting ------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(BACKDROP))

        tops = self.lane_tops()
        self._paint_lane(painter, tops["text"], TEXT_LANE_HEIGHT, "文字")
        self._paint_lane(painter, tops["music"], MUSIC_LANE_HEIGHT, "音乐")
        self._paint_lane(painter, tops["note"], NOTE_LANE_HEIGHT, "补充")

        if self.script is None:
            painter.setPen(QColor(DIM))
            painter.drawText(
                QRectF(GUTTER, tops["text"], self.width() - GUTTER, TEXT_LANE_HEIGHT),
                Qt.AlignmentFlag.AlignCenter,
                "先选择一个剧本",
            )
            return

        self._paint_ruler(painter, tops["ruler"])
        self._paint_music(painter, tops["music"])
        self._paint_notes(painter, tops["note"])
        self._paint_text(painter, tops["text"])
        self._paint_labels(painter, tops)

    def _paint_lane(self, painter: QPainter, top: float, height: float, label: str) -> None:
        painter.fillRect(
            QRectF(GUTTER, top, max(0, self.width() - GUTTER), height), QColor(LANE)
        )
        painter.setPen(QColor(LANE_EDGE))
        painter.drawLine(0, int(top + height), self.width(), int(top + height))

        painter.setPen(QColor(DIM))
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            QRectF(10, top, GUTTER - 16, height),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
        )
        font.setBold(False)
        painter.setFont(font)

    def _paint_labels(self, painter: QPainter, tops: Dict[str, float]) -> None:
        if self.selected is None or self.script is None:
            return
        if not 0 <= self.selected < len(self.script.lines):
            return
        painter.setPen(QColor(ACCENT))
        for lane in ("text", "music", "note"):
            top = tops[lane]
            height = {
                "text": TEXT_LANE_HEIGHT, "music": MUSIC_LANE_HEIGHT,
                "note": NOTE_LANE_HEIGHT,
            }[lane]
            painter.drawRect(
                QRectF(GUTTER - 1, top + 0.5, 1.5, height - 1)
            )

    def _paint_ruler(self, painter: QPainter, top: float) -> None:
        painter.fillRect(
            QRectF(GUTTER, top, max(0, self.width() - GUTTER), RULER_HEIGHT),
            QColor(LANE),
        )
        # Choose a tick spacing that stays readable at the current zoom.
        step = 1.0
        for candidate in (0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
            if candidate * self.scale >= 56:
                step = candidate
                break
        else:
            step = 600.0

        painter.setPen(QColor(DIM))
        first = int(self.offset / step) * step
        seconds = first
        while True:
            x = self.x_for(seconds)
            if x > self.width():
                break
            if x >= GUTTER:
                painter.drawLine(int(x), int(top + RULER_HEIGHT - 6), int(x), int(top + RULER_HEIGHT))
                painter.drawText(
                    QRectF(x + 3, top, 60, RULER_HEIGHT),
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    "%g s" % seconds,
                )
            seconds += step

        if self._drag_index is not None:
            painter.setPen(QColor(ACCENT))
            x = self.x_for(self._drag_origin + self._drag_seconds)
            painter.drawLine(int(x), int(top), int(x), self.height())
            painter.drawText(
                QRectF(x + 4, top, 90, RULER_HEIGHT),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                "%.2f s" % (self._drag_origin + self._drag_seconds),
            )

    def _paint_text(self, painter: QPainter, top: float) -> None:
        metrics = QFontMetrics(painter.font())
        for index, item in enumerate(self.script.lines):
            rect = self._block_rect(index)
            if rect.right() < GUTTER or rect.left() > self.width():
                continue
            if isinstance(item, Dialogue):
                character = self.characters.get(item.character or "")
                colour = kind_colour(character.style if character else "")
                fill = QColor(colour)
                fill.setAlpha(70 if index != self.selected else 120)
                painter.setBrush(fill)
                painter.setPen(QPen(QColor(ACCENT if index == self.selected else colour), 2))
                painter.drawRoundedRect(rect, 4, 4)
                painter.setPen(QColor(TEXT))
                inner = rect.adjusted(6, 2, -6, -2)
                if inner.width() > 12:
                    painter.drawText(
                        inner,
                        int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                        metrics.elidedText(
                            item.text, Qt.TextElideMode.ElideRight, int(inner.width())
                        ),
                    )
            elif isinstance(item, Directive) and item.command == "s":
                painter.setBrush(QColor(SLEEP_FILL))
                painter.setPen(QPen(QColor(LANE_EDGE), 1))
                painter.drawRoundedRect(rect, 3, 3)
            else:
                # clear / music markers are instants: draw a thin tick.
                painter.setPen(QPen(QColor(MARKER), 2))
                painter.drawLine(
                    int(rect.left()), int(top + 8), int(rect.left()), int(top + TEXT_LANE_HEIGHT - 8)
                )

    def _paint_music(self, painter: QPainter, top: float) -> None:
        if not self._durations:
            return
        palette = ("#3f6ea8", "#7a5aa8", "#3f8f7a", "#a8763f", "#a83f6e")
        cues = build_timeline(self.script, self._durations).cues
        order: List[str] = []
        for cue in cues:
            if cue.track and cue.track not in order:
                order.append(cue.track)

        metrics = QFontMetrics(painter.font())
        total = self.total_seconds
        for index, cue in enumerate(cues):
            if not cue.playing or not cue.track:
                continue
            # The run lasts until the next cue that is not this same track.
            end = total
            for later in range(index + 1, len(cues)):
                if not cues[later].playing or cues[later].track != cue.track:
                    end = self._starts[later]
                    break
            start_time = self._starts[index]
            if end <= start_time:
                continue
            rect = QRectF(
                self.x_for(start_time),
                top + 5,
                max(2.0, (end - start_time) * self.scale),
                MUSIC_LANE_HEIGHT - 10,
            )
            if rect.right() < GUTTER or rect.left() > self.width():
                continue
            fill = QColor(palette[order.index(cue.track) % len(palette)])
            fill.setAlpha(150)
            painter.setBrush(fill)
            painter.setPen(QPen(fill.lighter(130), 1))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor(TEXT))
            clip = QRectF(rect.left() + 5, rect.top(), max(0.0, rect.width() - 10), rect.height())
            if clip.width() > 20:
                label = studio.track_label(cue.track, self.tracks)
                painter.drawText(
                    clip,
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    metrics.elidedText(label, Qt.TextElideMode.ElideRight, int(clip.width())),
                )
            self._paint_lyric_marks(painter, top, index, cue, end)

    def _paint_lyric_marks(self, painter, top, index, cue, end) -> None:
        """Mark every lyric line of the track that is playing here.

        Lyric times are relative to the start of the audio, and a cue says how
        far in the track already is, so a line lands at
        ``segment_start + (lyric_time - cue_offset)``.
        """

        lines = self.lyrics.get(cue.track) or []
        if not lines:
            return
        segment_start = self._starts[index]
        span = end - segment_start
        painter.setPen(QPen(QColor(ACCENT), 1, Qt.PenStyle.DotLine))
        for line in lines:
            local = float(line.time)
            if local < cue.offset or local > cue.offset + span:
                continue
            x = self.x_for(segment_start + (local - cue.offset))
            if x < GUTTER - 2 or x > self.width():
                continue
            painter.drawLine(int(x), int(top + 3), int(x), int(top + MUSIC_LANE_HEIGHT - 3))

    def _paint_notes(self, painter: QPainter, top: float) -> None:
        from tscp_player.music import note_spans

        metrics = QFontMetrics(painter.font())
        for span in note_spans(self.script, self._durations):
            rect = QRectF(
                self.x_for(span.start),
                top + 5,
                max(3.0, span.seconds * self.scale),
                NOTE_LANE_HEIGHT - 10,
            )
            if rect.right() < GUTTER or rect.left() > self.width():
                continue
            colour = QColor(span.color or "#8a93a0")
            fill = QColor(colour)
            fill.setAlpha(90)
            painter.setBrush(fill)
            painter.setPen(QPen(colour, 1))
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(QColor(colour))
            clip = QRectF(rect.left() + 5, rect.top(), rect.width() - 10, rect.height())
            if clip.width() > 20:
                painter.drawText(
                    clip,
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    metrics.elidedText(span.text, Qt.TextElideMode.ElideRight, int(clip.width())),
                )

    # -- interaction ---------------------------------------------------
    def _handle_at(self, x: float, y: float) -> Optional[int]:
        index = self.index_at(x, y)
        if index is None or self.script is None:
            return None
        if not isinstance(self.script.lines[index], Dialogue):
            return None
        rect = self._block_rect(index)
        if abs(x - rect.right()) <= EDGE_GRAB:
            return index
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        position = event.position()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_anchor = int(position.x())
            return
        handle = self._handle_at(position.x(), position.y())
        if handle is not None:
            self._drag_index = handle
            self._drag_origin = self._starts[handle]
            self._drag_seconds = self._durations[handle]
            self.select(handle)
            self.statusMessage.emit("拖动右边缘改变这一句的时长")
            self.update()
            return
        index = self.index_at(position.x(), position.y())
        if index is not None:
            self.select(index)
            self.blockSelected.emit(index)
        else:
            self.select(None)
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        position = event.position()
        if self._panning:
            delta = int(position.x()) - self._pan_anchor
            self._pan_anchor = int(position.x())
            self.offset = max(0.0, self.offset - delta / self.scale)
            self.update()
            return
        if self._drag_index is not None:
            self._drag_seconds = max(
                0.0, self.seconds_for(position.x()) - self._drag_origin
            )
            self.update()
            return
        self.setCursor(
            Qt.CursorShape.SizeHorCursor
            if self._handle_at(position.x(), position.y()) is not None
            else Qt.CursorShape.ArrowCursor
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._panning and event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            return
        if self._drag_index is None:
            return
        index, seconds = self._drag_index, self._drag_seconds
        self._drag_index = None
        self.update()
        if seconds > 0.01:
            self.durationChanged.emit(index, seconds)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt naming
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.set_scale(self.scale * (1.2 ** steps), event.position().x())
        event.accept()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        if not self._durations:
            return
        # Keep at least a little of the story on screen after a resize.
        self.offset = max(0.0, min(self.offset, max(0.0, self.total_seconds - 1.0)))
