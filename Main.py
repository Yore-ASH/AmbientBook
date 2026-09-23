"""黑色背景的 TSCP 剧情播放器。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from tscp_player.audio import MusicPlayer
from tscp_player.format import ANSI_SEQUENCE_RE, Dialogue, Directive, Script
from tscp_player.music import STOP_WORDS
from tscp_player.plot import PlotPackageError, discover_plots


def _plain(text: str) -> str:
    return ANSI_SEQUENCE_RE.sub("", text.replace("\\033", "\033").replace("\\x1b", "\033"))


try:  # pragma: no cover - depends on the optional GUI dependency
    from PySide6.QtCore import QPropertyAnimation, QEasingCurve, QTimer, Qt
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


if QT_AVAILABLE:

    class PlayerWindow(QMainWindow):
        """Introduction/selection page followed by an incremental player."""

        def __init__(self, playlists) -> None:
            super().__init__()
            self.playlists = list(playlists)
            self.package = None
            self.script = None
            self.line_index = 0
            self.char_index = 0
            self.current_delays = []
            self.music = MusicPlayer()
            self._closed = False

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
            for package, _ in self.playlists:
                self.plot_list.addItem(QListWidgetItem(package.name))

            layout.addWidget(self.title)
            layout.addWidget(self.description)
            if multiple:
                layout.addWidget(self.plot_label)
                layout.addWidget(self.plot_list, 1)
            layout.addWidget(self.script_list, 2)
            layout.addWidget(self.begin_button)
            self.plot_list.setCurrentRow(0)
            self._plot_changed(0)
            return page

        def _plot_changed(self, row: int) -> None:
            if row < 0 or row >= len(self.playlists):
                return
            package, scripts = self.playlists[row]
            self.title.setText(package.name)
            self.description.setText(
                str(package.metadata.get("DESCRIPTION", "请选择要播放的剧情"))
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
            self.status = QLabel("播放中")
            self.status.setStyleSheet("color: #888; padding: 6px 12px;")
            layout.addWidget(self.output, 1)
            layout.addWidget(self.status)
            return page

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
                QTimer.singleShot(0, self._next_event)
            elif item.command == "p":
                try:
                    if item.value.strip().lower() in STOP_WORDS:
                        self.music.stop()
                    else:
                        self.music.ensure(str(self.package.music_path(item.value)))
                except (OSError, RuntimeError, ValueError) as exc:
                    QMessageBox.critical(self, "无法播放剧情", str(exc))
                    return
                QTimer.singleShot(0, self._next_event)
            elif item.command == "s":
                QTimer.singleShot(max(0, round(float(item.value) * 1000)), self._next_event)
            else:
                raise ValueError("未知控制指令: " + item.command)

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
            self.music.close()
            event.accept()


def load_playlists(source: Path):
    """Every playable plot under *source*, each with its compiled scripts."""

    packages = discover_plots(source)
    if not packages:
        raise PlotPackageError("source 中没有找到剧情包（.tscpkg 或剧情目录）")
    playlists = []
    for package in packages:
        names = package.script_names()
        if not names:
            continue
        playlists.append((package, {name: package.load_script(name) for name in names}))
    if not playlists:
        raise PlotPackageError("剧情包中没有可播放的 .tscp 文件")
    return playlists


def main() -> int:
    source = Path(__file__).resolve().parent / "source"
    try:
        playlists = load_playlists(source)
    except (PlotPackageError, OSError, ValueError, RuntimeError) as exc:
        print("无法播放剧情: " + str(exc))
        return 1
    if not QT_AVAILABLE:
        print("无法播放剧情：主程序需要 PySide6，请在 .venv 中执行 pip install PySide6")
        return 1
    app = QApplication(sys.argv)
    window = PlayerWindow(playlists)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
