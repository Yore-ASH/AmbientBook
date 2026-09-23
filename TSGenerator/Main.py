"""Optional PySide6 launcher for the Chinese ``.tscps`` source editor.

The model remains usable without PySide6.  Run ``python TSGenerator/Main.py``
from the repository root after installing the optional GUI dependency.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tscp_player.format import Dialogue, Directive, Script, TSCPError
from CharacterCreator.model import ANSI_COLORS, build_ansi_style

try:  # pragma: no cover - depends on the optional GUI dependency
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QComboBox,
        QDialog,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the local environment
    QT_AVAILABLE = False

try:
    from .model import (
        character_keys,
        load_character_config,
        load_script,
        save_script,
        split_pages,
        validate_character_key,
        validate_directive,
    )
except ImportError:  # Support ``python TSGenerator/Main.py``.
    from TSGenerator.model import (  # type: ignore[no-redef]
        character_keys,
        load_character_config,
        load_script,
        save_script,
        split_pages,
        validate_character_key,
        validate_directive,
    )


if QT_AVAILABLE:

    class PreviewDialog(QDialog):
        """Dark, monospaced preview for either the whole script or one page."""

        def __init__(
            self,
            script: Script,
            characters: Dict[str, object],
            single_page: bool = False,
            parent: Optional[QWidget] = None,
        ) -> None:
            super().__init__(parent)
            self.script = script
            self.characters = characters
            self.single_page = single_page
            self.pages = split_pages(script)
            self.page_index = -1
            self._running = False
            self._events: List[object] = []
            self._event_index = 0
            self._dialogue_text = ""
            self._dialogue_index = 0
            self._dialogue_tokens = []
            self._has_output = False
            self._phase = "event"
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self.setWindowTitle("内容单页展示" if single_page else "完全预览")
            self.resize(800, 520)
            self._build_ui()
            if single_page:
                self._next_page()
            else:
                self._start_continuous()

        def _build_ui(self) -> None:
            self.output = QTextEdit()
            self.output.setReadOnly(True)
            self.output.setStyleSheet(
                "QTextEdit { background: #000000; color: #f0f0f0; "
                "border: 0; padding: 12px; }"
            )
            self.output.setFont(self.output.font())
            font = self.output.font()
            font.setFamilies(["Cascadia Mono", "Consolas", "monospace"])
            font.setPointSize(12)
            self.output.setFont(font)

            self.delay_label = QLabel("逐字间隔（秒）")
            self.delay_spin = QDoubleSpinBox()
            self.delay_spin.setRange(0.0, 5.0)
            self.delay_spin.setDecimals(3)
            self.delay_spin.setSingleStep(0.01)
            self.delay_spin.setValue(0.02)
            self.delay_spin.setToolTip("源文件没有逐字间隔，预览使用此默认值")
            self.start_button = QPushButton("开始播放")
            self.start_button.clicked.connect(self._start_continuous)
            self.stop_button = QPushButton("停止")
            self.stop_button.clicked.connect(self._stop)
            self.next_button = QPushButton("下一页")
            self.next_button.clicked.connect(self._next_page)
            close_button = QPushButton("关闭")
            close_button.clicked.connect(self.close)
            self.status_label = QLabel("就绪")
            controls = QHBoxLayout()
            controls.addWidget(self.delay_label)
            controls.addWidget(self.delay_spin)
            controls.addWidget(self.start_button)
            controls.addWidget(self.stop_button)
            if self.single_page:
                self.start_button.setVisible(False)
                self.stop_button.setVisible(False)
                self.delay_label.setVisible(False)
                self.delay_spin.setVisible(False)
                controls.addWidget(self.next_button)
            controls.addWidget(self.status_label, 1)
            controls.addWidget(close_button)
            layout = QVBoxLayout(self)
            layout.addWidget(self.output, 1)
            layout.addLayout(controls)

        def _name_width(self) -> int:
            return max(
                (len(character.name) for character in self.characters.values()),
                default=0,
            )

        def _insert_styled_text(self, cursor: QTextCursor, text: str) -> None:
            """Insert text while interpreting literal or real ANSI SGR codes."""
            ansi_pattern = re.compile(r"(?:\\033|\\x1b|\x1b)\[[0-9;]*m")
            current = QTextCharFormat()
            parts = re.split("(" + ansi_pattern.pattern + ")", text)
            for part in parts:
                if not part:
                    continue
                if ansi_pattern.fullmatch(part):
                    current = self._style_format(part)
                    continue
                cursor.setCharFormat(current)
                cursor.insertText(part)
            cursor.setCharFormat(QTextCharFormat())

        def _styled_tokens(self, text: str):
            ansi_pattern = re.compile(r"(?:\\033|\\x1b|\x1b)\[[0-9;]*m")
            current = QTextCharFormat()
            for part in re.split("(" + ansi_pattern.pattern + ")", text):
                if not part:
                    continue
                if ansi_pattern.fullmatch(part):
                    current = self._style_format(part)
                    continue
                for character in part:
                    yield character, QTextCharFormat(current)

        def _append_dialogue(self, event: Dialogue, text: Optional[str] = None) -> None:
            cursor = self.output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            if self._has_output:
                cursor.insertText("\n")
            width = self._name_width()
            if event.character is None:
                cursor.insertText(" " * (width + 4))
                if text is not None:
                    self._insert_styled_text(cursor, text)
                else:
                    self._insert_styled_text(cursor, event.text)
            else:
                character = self.characters.get(event.character)
                name = character.name if character else event.character
                style = character.style if character else ""
                if style:
                    cursor.setCharFormat(self._style_format(style))
                cursor.insertText(name)
                cursor.setCharFormat(QTextCharFormat())
                cursor.insertText(" " * max(0, width - len(name)) + " : ")
                if text is not None:
                    self._insert_styled_text(cursor, text)
                else:
                    self._insert_styled_text(cursor, event.text)
            self._has_output = True
            self.output.setTextCursor(cursor)
            self.output.ensureCursorVisible()

        @staticmethod
        def _style_format(style: str) -> QTextCharFormat:
            fmt = QTextCharFormat()
            style = style.replace(r"\033", "\x1b").replace(r"\x1b", "\x1b")
            ansi = re.search(r"\x1b\[([0-9;]*)m", style)
            if ansi:
                codes = [int(code or 0) for code in ansi.group(1).split(";")]
                colors = {
                    30: "#000000", 31: "#cc3333", 32: "#33cc66", 33: "#cccc33",
                    34: "#4488ff", 35: "#cc66cc", 36: "#33cccc", 37: "#eeeeee",
                    90: "#777777", 91: "#ff6666", 92: "#66ee88", 93: "#ffff66",
                    94: "#66aaff", 95: "#ee88ee", 96: "#66eeee", 97: "#ffffff",
                }
                for code in codes:
                    if code in colors:
                        fmt.setForeground(QColor(colors[code]))
                    elif code == 1:
                        fmt.setFontWeight(700)
                    elif code == 3:
                        fmt.setFontItalic(True)
                    elif code == 4:
                        fmt.setFontUnderline(True)
            elif QColor(style).isValid():
                fmt.setForeground(QColor(style))
            return fmt

        def _clear_output(self) -> None:
            self.output.clear()
            self._has_output = False

        def _schedule(self, seconds: float) -> None:
            self._timer.start(max(0, int(seconds * 1000)))

        def _start_continuous(self) -> None:
            self._stop()
            self._clear_output()
            self._events = list(self.script.lines)
            self._event_index = 0
            self._phase = "event"
            self._running = True
            self.status_label.setText("正在播放")
            self._schedule(0)

        def _stop(self) -> None:
            self._timer.stop()
            self._running = False
            if not self.single_page:
                self.status_label.setText("已停止")

        def _tick(self) -> None:
            if not self._running:
                return
            if self._phase == "dialogue":
                if self._dialogue_index < len(self._dialogue_tokens):
                    cursor = self.output.textCursor()
                    cursor.movePosition(QTextCursor.MoveOperation.End)
                    character, char_format = self._dialogue_tokens[self._dialogue_index]
                    cursor.setCharFormat(char_format)
                    cursor.insertText(character)
                    cursor.setCharFormat(QTextCharFormat())
                    self._dialogue_index += 1
                    self.output.setTextCursor(cursor)
                    self.output.ensureCursorVisible()
                    self._schedule(self.delay_spin.value())
                    return
                self._phase = "event"
                self._schedule(0)
                return

            if self._event_index >= len(self._events):
                self._stop()
                self.status_label.setText("播放完成")
                return
            event = self._events[self._event_index]
            self._event_index += 1
            if isinstance(event, Dialogue):
                self._append_dialogue(event, "")
                self._dialogue_tokens = list(self._styled_tokens(event.text))
                self._dialogue_index = 0
                self._phase = "dialogue"
                self._schedule(0)
            elif event.command == "c":
                self._clear_output()
                self._schedule(0)
            elif event.command == "s":
                try:
                    self._schedule(float(event.value))
                except ValueError:
                    self._schedule(0)
            else:
                self.status_label.setText("音乐：%s（预览不播放音频）" % event.value)
                self._schedule(0)

        def _render_page_event(self, event: object) -> None:
            if isinstance(event, Dialogue):
                self._append_dialogue(event)
            elif getattr(event, "command", "") == "p":
                self.status_label.setText("音乐：%s（预览不播放音频）" % event.value)

        def _next_page(self) -> None:
            self._stop()
            if self.page_index + 1 >= len(self.pages):
                self.status_label.setText("已到最后一页")
                self.next_button.setEnabled(False)
                return
            self.page_index += 1
            self._clear_output()
            for event in self.pages[self.page_index].lines:
                self._render_page_event(event)
            self.status_label.setText("第 %d 页 / 共 %d 页" % (self.page_index + 1, len(self.pages)))
            if self.page_index + 1 >= len(self.pages):
                self.next_button.setText("已是最后一页")
                self.next_button.setEnabled(False)

        def closeEvent(self, event: object) -> None:
            self._stop()
            super().closeEvent(event)


    class TSGeneratorWindow(QMainWindow):
        """Event-list editor for source TSCP files."""

        def __init__(self) -> None:
            super().__init__()
            self.script = Script()
            self.characters: Dict[str, object] = {}
            self.current_path: Optional[Path] = None
            self.setWindowTitle("TSGenerator - TSCP 原稿编辑器")
            self.resize(1050, 650)
            self._build_ui()
            self._render_table()

        def _build_ui(self) -> None:
            new_button = QPushButton("新建")
            new_button.clicked.connect(self.new_script)
            open_button = QPushButton("打开 .tscps")
            open_button.clicked.connect(self.open_file)
            save_button = QPushButton("保存")
            save_button.clicked.connect(self.save_file)
            save_as_button = QPushButton("另存为…")
            save_as_button.clicked.connect(self.save_as)
            continuous_button = QPushButton("完全预览")
            continuous_button.clicked.connect(self.show_continuous_preview)
            page_button = QPushButton("内容单页展示")
            page_button.clicked.connect(self.show_page_preview)
            toolbar = QHBoxLayout()
            for button in (
                new_button,
                open_button,
                save_button,
                save_as_button,
                continuous_button,
                page_button,
            ):
                toolbar.addWidget(button)
            toolbar.addStretch(1)

            self.table = QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["类型", "角色缩写", "内容/参数"])
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self._selection_changed)

            self.type_combo = QComboBox()
            for label, value in (
                ("角色对白", "dialogue"),
                ("旁白", "narrator"),
                ("暂停", "sleep"),
                ("清空屏幕", "clear"),
                ("播放音乐", "music"),
            ):
                self.type_combo.addItem(label, value)
            self.type_combo.currentIndexChanged.connect(self._update_form_visibility)

            self.character_combo = QComboBox()
            self.character_combo.setEditable(True)
            self.character_combo.setToolTip(
                "角色缩写来自 Scripts/__init__.json，也会补充当前文稿中的新缩写。"
            )
            load_characters_button = QPushButton("加载角色配置")
            load_characters_button.clicked.connect(self.load_character_metadata)
            self.text_edit = QPlainTextEdit()
            self.text_edit.setPlaceholderText("输入一行对白或旁白文本")
            self.text_edit.setMinimumHeight(150)
            self.value_edit = QLineEdit()
            self.value_edit.setPlaceholderText("输入秒数或音乐简称")
            self.character_label = QLabel("角色缩写")
            self.text_label = QLabel("文本")
            self.value_label = QLabel("参数")
            self.text_color = QComboBox()
            for label, code in ANSI_COLORS:
                self.text_color.addItem(label, code)
            color_button = QPushButton("给选中文本添加颜色")
            color_button.clicked.connect(self.apply_text_color)

            form = QFormLayout()
            form.addRow("事件类型", self.type_combo)
            form.addRow(self.character_label, self.character_combo)
            form.addRow("", load_characters_button)
            form.addRow(self.text_label, self.text_edit)
            form.addRow(self.value_label, self.value_edit)
            form.addRow("文本颜色", self.text_color)
            form.addRow("", color_button)

            add_button = QPushButton("添加事件")
            add_button.clicked.connect(self.add_event)
            update_button = QPushButton("更新选中事件")
            update_button.clicked.connect(self.update_event)
            delete_button = QPushButton("删除选中事件")
            delete_button.clicked.connect(self.delete_event)
            buttons = QHBoxLayout()
            for button in (add_button, update_button, delete_button):
                buttons.addWidget(button)
            buttons.addStretch(1)

            editor = QWidget()
            editor_layout = QVBoxLayout(editor)
            editor_layout.addLayout(form)
            editor_layout.addLayout(buttons)
            editor_layout.addStretch(1)
            splitter = QSplitter(Qt.Horizontal)
            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_layout.addWidget(self.table)
            splitter.addWidget(left)
            splitter.addWidget(editor)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 2)

            central = QWidget()
            layout = QVBoxLayout(central)
            layout.addLayout(toolbar)
            layout.addWidget(splitter)
            self.setCentralWidget(central)
            self.statusBar().showMessage("就绪")
            self._update_form_visibility()

        def _update_form_visibility(self) -> None:
            kind = self.type_combo.currentData()
            dialogue = kind in {"dialogue", "narrator"}
            self.character_label.setVisible(kind == "dialogue")
            self.character_combo.setVisible(kind == "dialogue")
            self.text_label.setVisible(dialogue)
            self.text_edit.setVisible(dialogue)
            self.value_label.setVisible(kind in {"sleep", "music"})
            self.value_edit.setVisible(kind in {"sleep", "music"})
            if kind == "clear":
                self.value_edit.clear()

        def _render_table(self) -> None:
            self.table.blockSignals(True)
            self.table.setRowCount(len(self.script.lines))
            for row, event in enumerate(self.script.lines):
                if isinstance(event, Dialogue):
                    kind = "角色对白" if event.character else "旁白"
                    character = event.character or ""
                    preview = event.text.replace("\n", " ")
                else:
                    kind = {"s": "暂停", "c": "清空屏幕", "p": "播放音乐"}.get(
                        event.command, event.command
                    )
                    character = ""
                    preview = event.value
                for column, value in enumerate(
                    [kind, character, preview]
                ):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.blockSignals(False)
            self.character_combo.clear()
            keys = list(self.characters)
            keys.extend(key for key in character_keys(self.script) if key not in keys)
            for key in keys:
                character = self.characters.get(key)
                label = "%s（%s）" % (key, character.name) if character else key
                self.character_combo.addItem(label, key)

        def _selection_changed(self) -> None:
            rows = self.table.selectionModel().selectedRows()
            if rows:
                self._event_to_form(self.script.lines[rows[0].row()])

        def _event_to_form(self, event: object) -> None:
            if isinstance(event, Dialogue):
                kind = "dialogue" if event.character else "narrator"
                self.type_combo.setCurrentIndex(self.type_combo.findData(kind))
                index = self.character_combo.findData(event.character)
                if index >= 0:
                    self.character_combo.setCurrentIndex(index)
                else:
                    self.character_combo.setEditText(event.character or "")
                self.text_edit.setPlainText(event.text)
                self.value_edit.clear()
            else:
                kind = {"s": "sleep", "c": "clear", "p": "music"}.get(
                    event.command, "clear"
                )
                self.type_combo.setCurrentIndex(self.type_combo.findData(kind))
                self.value_edit.setText(event.value)
                self.text_edit.clear()
            self._update_form_visibility()

        def _event_from_form(self) -> object:
            kind = self.type_combo.currentData()
            if kind in {"dialogue", "narrator"}:
                text = self.text_edit.toPlainText()
                if "\r" in text or "\n" in text:
                    raise ValueError("对白或旁白必须只占一行")
                if kind == "narrator":
                    if not text.strip():
                        raise ValueError("旁白文本不能为空")
                    return Dialogue(None, text)
                key = self.character_combo.currentData() or self.character_combo.currentText()
                return Dialogue(validate_character_key(key), text)
            return validate_directive(
                {"sleep": "s", "clear": "c", "music": "p"}[kind],
                self.value_edit.text(),
            )

        def apply_text_color(self) -> None:
            cursor = self.text_edit.textCursor()
            selected = cursor.selectedText()
            if not selected:
                self._show_error("请先在文本框中选中要着色的文字。")
                return
            style = build_ansi_style(self.text_color.currentData())
            if not style:
                self._show_error("请选择一种颜色。")
                return
            cursor.insertText(r"\033[" + style[2:] + selected + r"\033[0m")
            self.text_edit.setTextCursor(cursor)

        def add_event(self) -> None:
            try:
                self.script.lines.append(self._event_from_form())
            except ValueError as exc:
                self._show_error(str(exc))
                return
            self._render_table()
            self.statusBar().showMessage("事件已添加")

        def update_event(self) -> None:
            rows = self.table.selectionModel().selectedRows()
            if not rows:
                self._show_error("请先选择要更新的事件。")
                return
            try:
                event = self._event_from_form()
            except ValueError as exc:
                self._show_error(str(exc))
                return
            row = rows[0].row()
            self.script.lines[row] = event
            self._render_table()
            self.table.selectRow(row)
            self.statusBar().showMessage("事件已更新")

        def delete_event(self) -> None:
            rows = self.table.selectionModel().selectedRows()
            if not rows:
                return
            del self.script.lines[rows[0].row()]
            self._render_table()
            self.statusBar().showMessage("事件已删除")

        def new_script(self) -> None:
            self.script = Script()
            self.characters = {}
            self.current_path = None
            self._render_table()
            self.setWindowTitle("TSGenerator - TSCP 原稿编辑器")
            self.statusBar().showMessage("已新建原稿")

        def open_file(self) -> None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "打开 TSCP 原稿", "", "TSCP 原稿 (*.tscps);;所有文件 (*)"
            )
            if filename:
                self._open_path(Path(filename))

        def _open_path(self, path: Path) -> None:
            try:
                script = load_script(path)
                characters = load_character_config(path)
            except (OSError, TSCPError, UnicodeError, ValueError) as exc:
                self._show_error("无法打开原稿：%s" % exc)
                return
            self.script = script
            self.characters = characters
            self.current_path = path
            self._render_table()
            self.setWindowTitle("TSGenerator - %s" % path.name)
            self.statusBar().showMessage("已打开：%s" % path)

        def load_character_metadata(self) -> None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "打开 Scripts/__init__.json", "", "JSON 文件 (*.json);;所有文件 (*)"
            )
            if not filename:
                return
            try:
                self.characters = load_character_config(Path(filename))
            except (OSError, UnicodeError, ValueError) as exc:
                self._show_error("无法读取角色配置：%s" % exc)
                return
            self._render_table()
            self.statusBar().showMessage("已加载 %d 个角色的名称和样式" % len(self.characters))

        def save_file(self) -> None:
            if self.current_path is None:
                self.save_as()
            else:
                self._save_path(self.current_path)

        def save_as(self) -> None:
            filename, _ = QFileDialog.getSaveFileName(
                self, "保存 TSCP 原稿", "", "TSCP 原稿 (*.tscps);;所有文件 (*)"
            )
            if filename:
                path = Path(filename)
                if path.suffix.lower() != ".tscps":
                    path = path.with_suffix(".tscps")
                self._save_path(path)

        def _save_path(self, path: Path) -> None:
            try:
                save_script(path, self.script)
            except (OSError, TSCPError, UnicodeError, ValueError) as exc:
                self._show_error("无法保存原稿：%s" % exc)
                return
            self.current_path = path
            self.setWindowTitle("TSGenerator - %s" % path.name)
            self.statusBar().showMessage("已保存：%s" % path)

        def _show_preview(self, single_page: bool) -> None:
            dialog = PreviewDialog(
                Script(list(self.script.lines)),
                self.characters,
                single_page=single_page,
                parent=self,
            )
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.show()
            # Keep a reference while the non-modal preview is open.
            if not hasattr(self, "_preview_dialogs"):
                self._preview_dialogs = []
            self._preview_dialogs.append(dialog)
            dialog.finished.connect(
                lambda _result: self._preview_dialogs.remove(dialog)
                if dialog in self._preview_dialogs
                else None
            )

        def show_continuous_preview(self) -> None:
            self._show_preview(False)

        def show_page_preview(self) -> None:
            self._show_preview(True)

        def _show_error(self, message: str) -> None:
            QMessageBox.critical(self, "TSGenerator", message)


else:
    TSGeneratorWindow = None  # type: ignore[assignment,misc]


def main(argv: Optional[List[str]] = None) -> int:
    """Launch the editor, optionally opening a source path."""

    parser = argparse.ArgumentParser(description="编辑 TSCP 原稿文件")
    parser.add_argument("path", nargs="?", help="要打开的 .tscps 文件")
    args = parser.parse_args(argv)
    if not QT_AVAILABLE:
        raise RuntimeError(
            "TSGenerator 需要安装可选依赖 PySide6。请运行 "
            "`python -m pip install PySide6`。"
        )
    app = QApplication(sys.argv[:1])
    window = TSGeneratorWindow()
    if args.path:
        window._open_path(Path(args.path))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
