"""黑色背景的 TSCP 剧情播放器。"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from tscp_player.audio import MusicPlayer
from tscp_player.format import (
    ANSI_SEQUENCE_RE,
    Dialogue,
    Directive,
    Script,
    is_note,
    note_parts,
)
from tscp_player.music import STOP_WORDS
from tscp_player.plot import (
    PlotPackageError,
    available_plots,
    load_plot,
)


def base_dir() -> Path:
    """The folder the user actually sees.

    Frozen by PyInstaller, ``__file__`` points inside the bundle (a temporary
    one with --onefile), so ``source/`` has to be resolved against the
    executable instead -- that is where somebody unzipping the release drops
    their plots.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _plain(text: str) -> str:
    return ANSI_SEQUENCE_RE.sub("", text.replace("\\033", "\033").replace("\\x1b", "\033"))


try:  # pragma: no cover - depends on the optional GUI dependency
    from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QPoint, QTimer, Qt
    from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QGraphicsOpacityEffect,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QStackedWidget,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


def _plot_labels(playlists, source: Path):
    """Name every plot, appending its location when two share a name."""

    names = [package.name for package, _ in playlists]
    counts = {name: names.count(name) for name in names}
    labels = []
    for package, _ in playlists:
        if counts[package.name] == 1:
            labels.append(package.name)
            continue
        try:
            where = package.location.relative_to(source).as_posix()
        except ValueError:
            where = package.location.as_posix()
        labels.append("%s - %s" % (package.name, where))
    return labels


if QT_AVAILABLE:

    from tscp_player.lyricview import LYRIC_POINT_SIZE, LyricsWindow

    class PlayerWindow(QMainWindow):
        """Introduction/selection page followed by an incremental player."""

        def __init__(self, playlists, source=None, problems=()) -> None:
            super().__init__()
            self.playlists = list(playlists)
            self.source = Path(source) if source is not None else Path(".")
            self.problems = list(problems)
            self.labels = _plot_labels(self.playlists, self.source)
            self.package = None
            self.script = None
            self.line_index = 0
            self.char_index = 0
            self.current_delays = []
            self.music = MusicPlayer()
            self._closed = False
            # The lyric overlay is created only when a track actually has lyrics,
            # so a plot without any never pops a second window.
            self.lyrics_window = None
            self._lyrics_started_at = None
            self._lyrics_timer = QTimer(self)
            self._lyrics_timer.setInterval(80)
            self._lyrics_timer.timeout.connect(self._tick_lyrics)

            # The current supplement and when it stops being shown.
            self._note_text = ""
            self._note_colour = ""
            self._note_until = 0.0
            self._note_timer = QTimer(self)
            self._note_timer.setSingleShot(True)
            self._note_timer.timeout.connect(self._expire_note)

            self.setWindowTitle("剧情播放器")
            self.resize(1000, 700)
            self.setStyleSheet("QMainWindow, QWidget { background: #000; color: #ddd; }")

            self.pages = QStackedWidget(self)
            self.intro_page = self._build_intro_page()
            self.play_page = self._build_play_page()
            self.pages.addWidget(self.intro_page)
            self.pages.addWidget(self.play_page)
            self.setCentralWidget(self.pages)

        def _build_intro_page(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(100, 70, 100, 70)
            self.title = QLabel()
            self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.title.setStyleSheet("font-size: 32px; color: #eee;")
            self.description = QLabel()
            self.description.setWordWrap(True)
            self.description.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.description.setStyleSheet("font-size: 16px; color: #999; padding: 18px;")

            list_style = (
                "QListWidget { background: #111; border: 1px solid #333; padding: 8px; }"
                "QListWidget::item { padding: 12px; }"
                "QListWidget::item:selected { background: #333; color: #fff; }"
            )
            self.plot_label = QLabel("剧情包")
            self.plot_label.setStyleSheet("color: #888;")
            self.plot_list = QListWidget()
            self.plot_list.setStyleSheet(list_style)
            self.plot_list.currentRowChanged.connect(self._plot_changed)
            self.script_list = QListWidget()
            self.script_list.setStyleSheet(list_style)

            self.begin_button = QPushButton("开始播放")
            self.begin_button.setMinimumHeight(42)
            self.begin_button.clicked.connect(self._begin_selected)

            multiple = len(self.playlists) > 1
            for label, (package, _) in zip(self.labels, self.playlists):
                item = QListWidgetItem(label)
                item.setToolTip(str(package.location))
                self.plot_list.addItem(item)

            layout.addWidget(self.title)
            layout.addWidget(self.description)
            if multiple:
                layout.addWidget(self.plot_label)
                layout.addWidget(self.plot_list, 1)
            layout.addWidget(self.script_list, 2)
            layout.addWidget(self.begin_button)
            if self.problems:
                note = QLabel("已跳过 %d 个无法读取的剧情包" % len(self.problems))
                note.setStyleSheet("color: #a55;")
                note.setToolTip(
                    "\n".join("%s：%s" % (where, reason) for where, reason in self.problems)
                )
                layout.addWidget(note)
            self.plot_list.setCurrentRow(0)
            self._plot_changed(0)
            return page

        def _plot_changed(self, row: int) -> None:
            if row < 0 or row >= len(self.playlists):
                return
            package, scripts = self.playlists[row]
            self.title.setText(self.labels[row] if row < len(self.labels) else package.name)
            self.description.setText(
                str(package.metadata.get("DESCRIPTION") or "请选择要播放的剧情")
            )
            self.script_list.clear()
            for name in scripts:
                self.script_list.addItem(QListWidgetItem(Path(name).stem))
            if scripts:
                self.script_list.setCurrentRow(0)

        def _build_play_page(self) -> QWidget:
            page = QWidget()
            layout = QVBoxLayout(page)
            self.output = QTextEdit()
            self.output.setReadOnly(True)
            self.output.setUndoRedoEnabled(False)
            self.output.setFont(QFont("Consolas", 16))
            self.output.setStyleSheet(
                "QTextEdit { background: #000; color: #ddd; border: none; padding: 24px; }"
            )
            # Supplements live under the main area, so clearing the story does not
            # take them with it.
            self.supplement = QLabel()
            self.supplement.setWordWrap(True)
            self.supplement.setTextFormat(Qt.TextFormat.PlainText)
            self.supplement.setAlignment(
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
            )
            self.supplement.setVisible(False)
            self.status = QLabel("播放中")
            self.status.setStyleSheet("color: #888; padding: 6px 12px;")
            layout.addWidget(self.output, 1)
            layout.addWidget(self.supplement)
            layout.addWidget(self.status)
            return page

        # -- supplements ---------------------------------------------------
        def _show_note(self, item: Directive) -> None:
            """Present an aside for its own duration, alongside the story."""

            colour, seconds, text = note_parts(item)
            self._note_text = text
            self._note_colour = colour
            self._note_until = time.monotonic() + seconds
            self._paint_note()
            self._note_timer.start(max(1, int(seconds * 1000)))

        def _paint_note(self) -> None:
            """Draw whatever aside is current, without disturbing its clock."""

            if not self._note_text or time.monotonic() >= self._note_until:
                self._expire_note()
                return
            colour = self._note_colour or "#8a93a0"
            self.supplement.setText(self._note_text)
            self.supplement.setStyleSheet(
                "QLabel { color: %s; background: #000; border-top: 1px solid #222;"
                " padding: 12px 24px; font-size: 15px; }" % colour
            )
            self.supplement.setVisible(True)

        def _expire_note(self) -> None:
            self._note_text = ""
            self._note_colour = ""
            self._note_until = 0.0
            self.supplement.clear()
            self.supplement.setVisible(False)

        def _begin_selected(self) -> None:
            plot_row = self.plot_list.currentRow()
            if plot_row < 0 or plot_row >= len(self.playlists):
                return
            package, scripts = self.playlists[plot_row]
            row = self.script_list.currentRow()
            if row < 0 or row >= len(scripts):
                return
            filename = list(scripts)[row]
            self.package = package
            self.script = scripts[filename]
            self.begin_button.setEnabled(False)
            effect = QGraphicsOpacityEffect(self.intro_page)
            self.intro_page.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", self)
            animation.setDuration(700)
            animation.setStartValue(1.0)
            animation.setEndValue(0.0)
            animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
            animation.finished.connect(self._show_play_page)
            self._fade_animation = animation
            animation.start()

        def _show_play_page(self) -> None:
            self.pages.setCurrentWidget(self.play_page)
            self.output.clear()
            self.line_index = 0
            QTimer.singleShot(250, self._next_event)

        def _next_event(self) -> None:
            if self._closed or self.script is None:
                return
            if self.line_index >= len(self.script.lines):
                self._hide_lyrics()
                self.status.setText("剧情播放完成")
                return
            item = self.script.lines[self.line_index]
            self.line_index += 1
            if isinstance(item, Dialogue):
                self._begin_dialogue(item)
            else:
                self._handle_directive(item)

        def _handle_directive(self, item: Directive) -> None:
            if item.command == "c":
                self.output.clear()
                # A supplement is not part of the story text, so clearing must
                # put it back rather than wipe it -- and its remaining time keeps
                # running instead of starting over.
                self._paint_note()
                QTimer.singleShot(0, self._next_event)
            elif is_note(item):
                self._show_note(item)
                QTimer.singleShot(0, self._next_event)
            elif item.command == "p":
                try:
                    if item.value.strip().lower() in STOP_WORDS:
                        self.music.stop()
                        self._hide_lyrics()
                    else:
                        self.music.ensure(str(self.package.music_path(item.value)))
                        self._show_lyrics(item.value)
                except (OSError, RuntimeError, ValueError) as exc:
                    QMessageBox.critical(self, "无法播放剧情", str(exc))
                    return
                QTimer.singleShot(0, self._next_event)
            elif item.command == "s":
                QTimer.singleShot(max(0, round(float(item.value) * 1000)), self._next_event)
            else:
                raise ValueError("未知控制指令: " + item.command)

        # -- lyrics overlay ------------------------------------------------
        def _show_lyrics(self, abbreviation: str) -> None:
            """Open the lyric window when the track actually carries lyrics."""

            lyrics = self.package.lyrics(abbreviation)
            if not lyrics:
                self._hide_lyrics()
                self.status.setText("播放中 · 纯音乐 %s" % abbreviation)
                return
            track = self.package.track(abbreviation)
            if self.lyrics_window is None:
                self.lyrics_window = LyricsWindow()
            self._lyrics_started_at = time.monotonic()
            self.lyrics_window.show_track(lyrics, track.color, 0.0)
            self._lyrics_timer.start()
            self.status.setText(
                "播放中 · 歌词 %s（%d 句）" % (abbreviation, len(lyrics))
            )

        def _tick_lyrics(self) -> None:
            if self.lyrics_window is None or self._lyrics_started_at is None:
                return
            # Ask the mixer where it is rather than counting seconds ourselves:
            # a wall clock keeps running after the track has finished, which
            # left the last line on screen forever.
            if self.music is not None and not self.music.is_busy():
                if self.music.current is not None:
                    self._hide_lyrics()
                    self.status.setText("播放中 · 音乐已结束")
                    return
            seconds = self.music.position() if self.music is not None else None
            if seconds is None:
                seconds = time.monotonic() - self._lyrics_started_at
            self.lyrics_window.update_line(seconds)

        def _hide_lyrics(self) -> None:
            self._lyrics_timer.stop()
            self._lyrics_started_at = None
            if self.lyrics_window is not None:
                self.lyrics_window.clear()
                self.lyrics_window.hide()

        def _begin_dialogue(self, item: Dialogue) -> None:
            width = max((len(value.name) for value in self.package.characters.values()), default=0)
            cursor = self.output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            if self.output.toPlainText():
                cursor.insertBlock()
            if item.character is None:
                cursor.insertText(" " * (width + 4))
                speaker = "旁白"
            else:
                character = self.package.characters.get(item.character)
                name = character.name if character else item.character
                cursor.insertText(" " * (width - len(name)))
                cursor.setCharFormat(self._style_format(character.style if character else ""))
                cursor.insertText(name)
                cursor.setCharFormat(QTextCharFormat())
                cursor.insertText(" : ")
                speaker = name
            self.output.setTextCursor(cursor)
            self._center_cursor(cursor)
            self.current_text = _plain(item.text)
            self.current_delays = list(item.delays)
            self.char_index = 0
            self.status.setText("播放中 · " + speaker)
            self._write_character()

        def _write_character(self) -> None:
            if self.char_index >= len(self.current_text):
                QTimer.singleShot(0, self._next_event)
                return
            cursor = self.output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(self.current_text[self.char_index])
            self.output.setTextCursor(cursor)
            self._center_cursor(cursor)
            delay = self.current_delays[self.char_index] if self.char_index < len(self.current_delays) else 0.0
            self.char_index += 1
            QTimer.singleShot(max(0, round(delay * 1000)), self._write_character)

        def _center_cursor(self, cursor: QTextCursor) -> None:
            self.output.ensureCursorVisible()
            rect = self.output.cursorRect(cursor)
            bar = self.output.verticalScrollBar()
            bar.setValue(max(0, bar.value() + rect.center().y() - self.output.viewport().height() // 2))

        @staticmethod
        def _style_format(style: str) -> QTextCharFormat:
            fmt = QTextCharFormat()
            normalized = str(style).replace(r"\033", "\x1b").replace(r"\x1b", "\x1b").replace(r"\u001b", "\x1b")
            match = re.search(r"\x1b\[([0-9;]*)m", normalized)
            if not match:
                return fmt
            colors = {
                30: "#000000", 31: "#cc3333", 32: "#33cc66", 33: "#cccc33",
                34: "#4488ff", 35: "#cc66cc", 36: "#33cccc", 37: "#eeeeee",
                90: "#777777", 91: "#ff6666", 92: "#66ee88", 93: "#ffff66",
                94: "#66aaff", 95: "#ee88ee", 96: "#66eeee", 97: "#ffffff",
            }
            for code in (int(value or 0) for value in match.group(1).split(";")):
                if code in colors:
                    fmt.setForeground(QColor(colors[code]))
                elif code == 1:
                    fmt.setFontWeight(700)
                elif code == 4:
                    fmt.setFontUnderline(True)
            return fmt

        def closeEvent(self, event) -> None:
            self._closed = True
            self._hide_lyrics()
            if self.lyrics_window is not None:
                self.lyrics_window.close()
                self.lyrics_window = None
            # Stop first: quitting the mixer is not guaranteed to have
            # run if the window is torn down rather than closed.
            self.music.stop()
            self.music.close()
            event.accept()


def load_playlists(source: Path):
    """Every playable plot under *source*, each with its compiled scripts.

    Returns ``(playlists, problems)``.  Every ``.tscpkg`` and plot directory
    under *source* is searched recursively, and one unreadable package must not
    stop the player from opening the rest, so failures are collected rather than
    raised.
    """

    source = Path(source)
    if not source.exists():
        raise PlotPackageError("source 目录不存在：%s" % source)
    candidates = available_plots(source)
    if not candidates:
        raise PlotPackageError("source 中没有找到剧情包（.tscpkg 或剧情目录）：%s" % source)

    playlists = []
    problems = []
    for candidate in candidates:
        try:
            package = load_plot(candidate)
            names = package.script_names()
            if not names:
                problems.append((candidate, "没有 .tscp 剧本"))
                continue
            playlists.append(
                (package, {name: package.load_script(name) for name in names})
            )
        except (PlotPackageError, OSError, ValueError, RuntimeError) as exc:
            problems.append((candidate, str(exc)))
    if not playlists:
        detail = "；".join(
            "%s：%s" % (where.name, reason) for where, reason in problems[:3]
        )
        raise PlotPackageError("没有可播放的剧情包。%s" % detail)
    return playlists, problems


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        # ``python Main.py <folder-or-.tscpkg>`` plays that target directly.
        source = Path(arguments[0]).expanduser()
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
    else:
        source = base_dir() / "source"
    try:
        playlists, problems = load_playlists(source)
    except (PlotPackageError, OSError, ValueError, RuntimeError) as exc:
        print("无法播放剧情: " + str(exc))
        return 1
    for where, reason in problems:
        print("已跳过 %s：%s" % (where, reason))
    if not QT_AVAILABLE:
        print("无法播放剧情：主程序需要 PySide6，请在 .venv 中执行 pip install PySide6")
        return 1
    app = QApplication(sys.argv)
    window = PlayerWindow(playlists, source, problems)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
