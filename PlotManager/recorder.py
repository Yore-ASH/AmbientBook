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

from tscp_player.audio import MusicPlayer
from tscp_player.lyrics import Lyrics, format_time, parse_time, timed_from_marks

try:  # pragma: no cover - depends on the optional GUI package
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


if QT_AVAILABLE:

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

            controls = QHBoxLayout()
            for button in (self.start_button, self.stop_button, self.clear_button):
                controls.addWidget(button)
            controls.addStretch(1)

            self.clock_label = QLabel("当前时间 0.00s")
            self.progress_label = QLabel("已录 0/%d 行" % len(self.lines))

            self.table = QTableWidget(len(self.lines), 3)
            self.table.setHorizontalHeaderLabels(["#", "时间", "歌词"])
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setEditTriggers(
                QAbstractItemView.EditTrigger.DoubleClicked
                | QAbstractItemView.EditTrigger.EditKeyPressed
            )
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.setColumnWidth(0, 44)
            self.table.setColumnWidth(1, 110)
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
            self._marks[self._cursor] = time.monotonic() - self._started_at
            self._cursor += 1
            self._refresh()
            if self._cursor >= len(self.lines):
                self.stop()

        def _tick(self) -> None:
            if self._started_at is None:
                self.clock_label.setText("当前时间 0.00s")
                return
            self.clock_label.setText(
                "当前时间 %.2fs" % (time.monotonic() - self._started_at)
            )

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

        # -- table ---------------------------------------------------------
        def _refresh(self) -> None:
            for row, mark in enumerate(self._marks):
                item = self.table.item(row, 1)
                item.setText("" if mark is None else "%.2f" % mark)
                item.setBackground(
                    Qt.GlobalColor.transparent if mark is None else Qt.GlobalColor.darkGreen
                )
            done = sum(1 for mark in self._marks if mark is not None)
            self.progress_label.setText("已录 %d/%d 行" % (done, len(self.lines)))
            if self._cursor < len(self.lines):
                self.table.selectRow(self._cursor)

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
