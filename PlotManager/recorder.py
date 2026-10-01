"""Record when each lyric line should appear, against the real audio.

A plain ``lyrics.txt`` has no timings, so the author plays the track and presses
a key when each line starts.  The result is ordinary LRC, and the time column
stays editable so a mark can be nudged without re-recording the whole song.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import List, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tscp_player.audio import MusicPlayer, SeekUnsupported, supports_seek
from tscp_player.lyrics import Lyrics, format_time, parse_time, timed_from_marks

try:  # pragma: no cover - depends on the optional GUI package
    from PySide6.QtCore import QEvent, QObject, Qt, QTimer
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QStyle,
        QStyledItemDelegate,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


if QT_AVAILABLE:

    class RecordedTimeDelegate(QStyledItemDelegate):
        """Tint the time cell of a recorded line.

        The tint cannot be an item background role: Qt paints that over the
        selection highlight, which leaves a selected row looking half painted.
        Painting it here, and skipping it while the row is selected, keeps the
        whole-row highlight intact.
        """

        DONE = QColor("#1d4d24")

        def paint(self, painter, option, index) -> None:
            if index.data(Qt.ItemDataRole.UserRole) and not (
                option.state & QStyle.StateFlag.State_Selected
            ):
                painter.fillRect(option.rect, self.DONE)
            super().paint(painter, option, index)

    class LyricsRecorderDialog(QDialog):
        """Press a key per line while the track plays; edit the times afterwards."""

        def __init__(self, parent, audio: Path, lines: List[str]) -> None:
            super().__init__(parent)
            self.audio = Path(audio)
            self.lines = list(lines)
            self._marks: List[Optional[float]] = [None] * len(self.lines)
            self._cursor = 0
            self._started_at: Optional[float] = None
            self._music: Optional[MusicPlayer] = None
            self._audio_broken = False
            self._paused_at: Optional[float] = None

            self.setWindowTitle("歌词录制 - %s" % self.audio.name)
            self.resize(720, 620)
            self._build_ui()
            self._refresh()

            self._timer = QTimer(self)
            self._timer.setInterval(100)
            self._timer.timeout.connect(self._tick)

        # -- construction --------------------------------------------------
        def _build_ui(self) -> None:
            self.hint = QLabel(
                "点「开始录制」后音乐开始播放，每按一次 <b>Enter / Space</b> 记录当前行的出现时间。<br>"
                "时间列可以直接双击修改（支持 12.34 或 0:12.34）。Esc 取消。"
            )
            self.hint.setWordWrap(True)
            self.hint.setStyleSheet("color:#666;")

            self.start_button = QPushButton("开始录制")
            self.start_button.clicked.connect(self.start)
            self.stop_button = QPushButton("停止")
            self.stop_button.clicked.connect(self.stop)
            self.stop_button.setEnabled(False)
            self.clear_button = QPushButton("清空")
            self.clear_button.clicked.connect(self.clear)

            self.pause_button = QPushButton("⏸ 暂停")
            self.pause_button.setToolTip("暂停音乐和计时，再点一次继续")
            self.pause_button.clicked.connect(self.toggle_pause)
            self.pause_button.setEnabled(False)

            self.rewind_button = QPushButton("⟲ 从上一句重听")
            self.rewind_button.setToolTip(
                "回到上一句开始的地方重新播放，时间也跟着回到那里"
            )
            self.rewind_button.clicked.connect(self.rewind_to_previous)
            self.rewind_button.setEnabled(False)

            controls = QHBoxLayout()
            for button in (self.start_button, self.stop_button, self.pause_button):
                controls.addWidget(button)
            controls.addWidget(self.rewind_button)
            controls.addWidget(self.clear_button)
            controls.addStretch(1)

            self.clock_label = QLabel("当前时间 0.00s")
            self.progress_label = QLabel("已录 0/%d 行" % len(self.lines))

            self.table = QTableWidget(len(self.lines), 3)
            self.table.setHorizontalHeaderLabels(["#", "时间", "歌词"])
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
            )
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.setColumnWidth(0, 44)
            self.table.setColumnWidth(1, 110)
            self.table.setItemDelegateForColumn(1, RecordedTimeDelegate(self.table))
            # A QTableWidget eats Space (it toggles the selection) and Return
            # (it starts editing), so the dialog's keyPressEvent never sees the
            # very keys this screen is built around. Intercept them first.
            self.table.installEventFilter(self)
            self.table.viewport().installEventFilter(self)
            for row, text in enumerate(self.lines):
                number = QTableWidgetItem(str(row + 1))
                number.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, 0, number)
                self.table.setItem(row, 1, QTableWidgetItem(""))
                lyric = QTableWidgetItem(text)
                lyric.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, 2, lyric)

            self.buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            self.buttons.accepted.connect(self.accept)
            self.buttons.rejected.connect(self.reject)

            layout = QVBoxLayout(self)
            layout.addWidget(self.hint)
            layout.addLayout(controls)
            status = QHBoxLayout()
            status.addWidget(self.clock_label)
            status.addStretch(1)
            status.addWidget(self.progress_label)
            layout.addLayout(status)
            layout.addWidget(self.table, 1)
            layout.addWidget(self.buttons)

        # -- recording -----------------------------------------------------
        def start(self) -> None:
            """Restart the clock (and the track) so marks are measured from zero."""

            self.clear()
            self._started_at = time.monotonic()
            self._audio_broken = not self._play_audio()
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
            self.pause_button.setEnabled(True)
            self.rewind_button.setEnabled(True)
            self.pause_button.setText("⏸ 暂停")
            self._paused_at = None
            self._timer.start()
            self._tick()
            if self._audio_broken:
                self.hint.setText(
                    "音乐无法播放，只用计时器录制。<br>"
                    "点「开始录制」后每按一次 Enter / Space 记录当前行的出现时间。"
                )

        def _play_audio(self) -> bool:
            try:
                if self._music is None:
                    self._music = MusicPlayer()
            except RuntimeError:
                return False
            try:
                self._music.ensure(str(self.audio), restart=True)
            except (OSError, RuntimeError):
                return False
            return True

        def stop(self) -> None:
            self._timer.stop()
            self._paused_at = None
            self.pause_button.setEnabled(False)
            self.rewind_button.setEnabled(False)
            self.pause_button.setText("⏸ 暂停")

            self.start_button.setEnabled(True)
            self.stop_button.setEnabled(False)
            if self._music is not None:
                self._music.stop()

        def clear(self) -> None:
            self._marks = [None] * len(self.lines)
            self._cursor = 0
            self._started_at = None
            self._refresh()

        def _mark(self) -> None:
            if self._started_at is None or self._cursor >= len(self.lines):
                return
            self._marks[self._cursor] = self._elapsed()
            self._cursor += 1
            self._refresh()
            if self._cursor >= len(self.lines):
                self.stop()

        def _tick(self) -> None:
            if self._started_at is None:
                self.clock_label.setText("当前时间 0.00s")
                return
            suffix = "（已暂停）" if self._paused_at is not None else ""
            self.clock_label.setText("当前时间 %.2fs%s" % (self._elapsed(), suffix))

        def eventFilter(self, watched, event) -> bool:
            """Catch Space/Enter before the table can swallow them.

            Editing a time by hand has to keep working, so the keys are only
            intercepted when no editor is open.
            """

            if event.type() == QEvent.Type.KeyPress:
                if self.table.state() != QAbstractItemView.State.EditingState:
                    key = event.key()
                    if key in (
                        Qt.Key.Key_Return,
                        Qt.Key.Key_Enter,
                        Qt.Key.Key_Space,
                    ):
                        if self._started_at is None:
                            self.start()
                        else:
                            self._mark()
                        return True
            return super().eventFilter(watched, event)

        def keyPressEvent(self, event) -> None:
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                if self._started_at is None:
                    self.start()
                else:
                    self._mark()
                return
            if key == Qt.Key.Key_Escape:
                self.reject()
                return
            super().keyPressEvent(event)

        # -- pause / rewind ------------------------------------------------
        def toggle_pause(self) -> None:
            """Freeze the music and the clock together.

            The clock has to stop with the audio, or every mark taken after a
            pause would be late by however long the pause lasted.
            """

            if self._started_at is None:
                return
            if self._paused_at is None:
                self._paused_at = time.monotonic()
                if self._music is not None:
                    self._music.pause()
                self.pause_button.setText("▶ 继续")
            else:
                # Push the origin forward by the length of the pause.
                self._started_at += time.monotonic() - self._paused_at
                self._paused_at = None
                if self._music is not None:
                    self._music.unpause()
                self.pause_button.setText("⏸ 暂停")
            self._tick()

        def _elapsed(self) -> float:
            """Seconds of audio heard so far, not counting paused time."""

            if self._started_at is None:
                return 0.0
            end = self._paused_at if self._paused_at is not None else time.monotonic()
            return end - self._started_at

        def _line_time(self, row: int) -> float:
            """The time recorded for *row*, from the table or from a mark."""

            item = self.table.item(row, 1)
            text = item.text().strip() if item is not None else ""
            parsed = parse_time(text) if text else None
            if parsed is not None:
                return float(parsed)
            mark = self._marks[row]
            return 0.0 if mark is None else float(mark)

        def rewind_to_previous(self) -> None:
            """Play again from the line before the one being recorded."""

            if self._started_at is None:
                return
            row = max(0, self._cursor - 1)
            target = max(0.0, self._line_time(row))

            if not supports_seek(self.audio):
                QMessageBox.information(
                    self,
                    "从上一句重听",
                    "%s 这种格式无法定位播放，只能从头开始。\n\n"
                    "想从中间重听，请把这首歌换成 OGG 或 MP3 再录时间。" % self.audio.suffix,
                )
                return
            try:
                if self._music is None:
                    self._music = MusicPlayer()
                # A pause would swallow the seek's effect on the clock.
                if self._paused_at is not None:
                    self.toggle_pause()
                self._music.ensure(str(self.audio), restart=True)
                self._music.seek(target)
            except SeekUnsupported as exc:
                QMessageBox.information(self, "从上一句重听", str(exc))
                return
            except (OSError, RuntimeError) as exc:
                QMessageBox.information(self, "从上一句重听", "无法跳转：%s" % exc)
                return

            # Line the clock up with the audio, and put the cursor back so the
            # next key press re-marks that line.
            self._started_at = time.monotonic() - target
            self._cursor = row
            self.table.selectRow(row)
            self.progress_label.setText(
                "已回到第 %d 句（%.2fs）" % (row + 1, target)
            )
            self._tick()

        # -- table ---------------------------------------------------------
        def _refresh(self) -> None:
            for row, mark in enumerate(self._marks):
                item = self.table.item(row, 1)
                item.setText("" if mark is None else "%.2f" % mark)
                # The delegate reads this; it is deliberately not a background
                # role, which would hide the row highlight.
                item.setData(Qt.ItemDataRole.UserRole, mark is not None)
            done = sum(1 for mark in self._marks if mark is not None)
            self.progress_label.setText("已录 %d/%d 行" % (done, len(self.lines)))
            if self._cursor < len(self.lines):
                self.table.selectRow(self._cursor)
                self.table.scrollToItem(
                    self.table.item(self._cursor, 0),
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )

        def _read_times(self) -> Optional[List[float]]:
            """Read the (possibly hand-edited) time column back out."""

            values: List[float] = []
            for row in range(len(self.lines)):
                text = self.table.item(row, 1).text().strip()
                if not text:
                    continue
                seconds = parse_time(text)
                if seconds is None:
                    QMessageBox.warning(
                        self, "歌词录制", "第 %d 行的时间看不懂：%s" % (row + 1, text)
                    )
                    return None
                values.append(seconds)
            return values

        def result_lyrics(self) -> Lyrics:
            """The recorded lyrics; call only after the dialog was accepted."""

            times = []
            for row in range(len(self.lines)):
                seconds = parse_time(self.table.item(row, 1).text())
                if seconds is not None:
                    times.append(seconds)
                else:
                    times.append(0.0)
            return timed_from_marks(self.lines, times)

        # -- dialog --------------------------------------------------------
        def accept(self) -> None:
            if self._read_times() is None:
                return
            if all(self.table.item(row, 1).text().strip() == "" for row in range(len(self.lines))):
                QMessageBox.information(self, "歌词录制", "还没有记录任何一行的时间")
                return
            self.stop()
            super().accept()

        def reject(self) -> None:
            self.stop()
            super().reject()

        def closeEvent(self, event) -> None:
            self.stop()
            if self._music is not None:
                self._music.close()
                self._music = None
            super().closeEvent(event)

        @classmethod
        def record(cls, parent, audio: Path, lines: List[str]) -> Optional[Lyrics]:
            """Run the dialog and return the recorded lyrics, or ``None``."""

            dialog = cls(parent, audio, lines)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return dialog.result_lyrics()
            return None

else:  # pragma: no cover
    LyricsRecorderDialog = None  # type: ignore[assignment,misc]
