"""PySide6 launcher for the small compiled-TSCP editor.

PySide6 is intentionally optional.  Importing this module, or the
``TSCPEditor`` package, remains safe without the GUI dependency installed.
run ``python TSCPEditor/Main.py`` after installing PySide6 to start the editor.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

# When this file is launched directly, Python puts TSCPEditor (rather than
# the repository root) on sys.path.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tscp_player.format import Dialogue, Directive, Script, TSCPError, visible_text_length

try:
    from .model import (
        load_script,
        parse_delay_text,
        save_script,
    )
except ImportError:  # Support ``python TSCPEditor/Main.py`` from the repo root.
    from TSCPEditor.model import (  # type: ignore[no-redef]
        load_script,
        parse_delay_text,
        save_script,
    )

try:  # pragma: no cover - exercised only when the optional GUI is installed
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QPlainTextEdit,
        QSpinBox,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the local environment
    QT_AVAILABLE = False


if QT_AVAILABLE:

    class TSCPEditorWindow(QMainWindow):
        """A small event-list editor for the compiled TSCP format."""

        def __init__(self) -> None:
            super().__init__()
            self.script = Script()
            self.current_path: Optional[Path] = None
            self._loading_form = False
            self.setWindowTitle("TSCPEditor - TSCP 剧情编辑器")
            self.resize(1050, 650)
            self._build_ui()
            self._render_table()

        def _build_ui(self) -> None:
            open_button = QPushButton("打开 .tscp")
            open_button.clicked.connect(self.open_file)
            save_button = QPushButton("保存")
            save_button.clicked.connect(self.save_file)
            save_as_button = QPushButton("另存为…")
            save_as_button.clicked.connect(self.save_as)
            new_button = QPushButton("新建")
            new_button.clicked.connect(self.new_script)
            toolbar = QHBoxLayout()
            for button in (
                new_button,
                open_button,
                save_button,
                save_as_button,
            ):
                toolbar.addWidget(button)
            toolbar.addStretch(1)

            self.table = QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["类型", "角色缩写", "预览"])
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self._selection_changed)

            self.type_combo = QComboBox()
            self.type_combo.addItem("角色对白", "dialogue")
            self.type_combo.addItem("旁白", "narrator")
            self.type_combo.addItem("暂停", "sleep")
            self.type_combo.addItem("清空屏幕", "clear")
            self.type_combo.addItem("播放音乐", "music")
            self.type_combo.currentIndexChanged.connect(self._update_form_visibility)

            self.character_combo = QComboBox()
            self.character_combo.setEditable(True)
            self.character_combo.setToolTip(
                "可以从剧情包加载角色缩写，也可以手动输入。"
            )
            self.text_edit = QPlainTextEdit()
            self.text_edit.setPlaceholderText("输入对白或旁白文本")
            self.text_edit.setMinimumHeight(150)
            self.value_edit = QLineEdit()
            self.value_edit.setPlaceholderText("输入秒数或音乐简称")
            self.default_delay = QDoubleSpinBox()
            self.default_delay.setRange(0.0, 86400.0)
            self.default_delay.setDecimals(6)
            self.default_delay.setSingleStep(0.01)
            self.default_delay.setValue(0.05)
            self.delay_edit = QLineEdit()
            self.delay_edit.setPlaceholderText(
                "可选：[0.1, 0.2] 或 0.1, 0.2（留空使用默认时长）"
            )
            self.delay_edit.setToolTip(
                "延迟数量必须与文本字符数量完全一致。"
            )
            self.character_label = QLabel("角色缩写")
            self.text_label = QLabel("文本")
            self.value_label = QLabel("参数")
            self.delay_label = QLabel("逐字延迟")

            form = QFormLayout()
            form.addRow("事件类型", self.type_combo)
            form.addRow(self.character_label, self.character_combo)
            form.addRow(self.text_label, self.text_edit)
            form.addRow(self.value_label, self.value_edit)
            form.addRow("默认延迟（秒）", self.default_delay)
            form.addRow(self.delay_label, self.delay_edit)

            add_button = QPushButton("添加事件")
            add_button.clicked.connect(self.add_event)
            update_button = QPushButton("更新选中事件")
            update_button.clicked.connect(self.update_event)
            delete_button = QPushButton("删除选中事件")
            delete_button.clicked.connect(self.delete_event)
            buttons = QHBoxLayout()
            buttons.addWidget(add_button)
            buttons.addWidget(update_button)
            buttons.addWidget(delete_button)
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
            self.delay_label.setVisible(dialogue)
            self.delay_edit.setVisible(dialogue)
            self.default_delay.setVisible(dialogue)
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
                    kind = {
                        "s": "暂停",
                        "c": "清空屏幕",
                        "p": "播放音乐",
                    }.get(event.command, event.command)
                    character = ""
                    preview = event.value
                values = [kind, character, preview]
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.blockSignals(False)

        def _selection_changed(self) -> None:
            rows = self.table.selectionModel().selectedRows()
            if not rows:
                return
            row = rows[0].row()
            if 0 <= row < len(self.script.lines):
                self._event_to_form(self.script.lines[row])

        def _event_to_form(self, event: object) -> None:
            self._loading_form = True
            try:
                if isinstance(event, Dialogue):
                    kind = "dialogue" if event.character else "narrator"
                    self.type_combo.setCurrentIndex(self.type_combo.findData(kind))
                    self.character_combo.setEditText(event.character or "")
                    self.text_edit.setPlainText(event.text)
                    self.default_delay.setValue(event.delays[0] if event.delays else 0.05)
                    self.delay_edit.setText(
                        json.dumps(event.delays, ensure_ascii=False)
                        if event.delays
                        else ""
                    )
                    self.value_edit.clear()
                else:
                    kind = {"s": "sleep", "c": "clear", "p": "music"}.get(
                        event.command, "clear"
                    )
                    self.type_combo.setCurrentIndex(self.type_combo.findData(kind))
                    self.value_edit.setText(event.value)
                    self.text_edit.clear()
                    self.delay_edit.clear()
            finally:
                self._loading_form = False
            self._update_form_visibility()

        def _event_from_form(self) -> object:
            kind = self.type_combo.currentData()
            if kind in {"dialogue", "narrator"}:
                text = self.text_edit.toPlainText()
                character = (
                    self.character_combo.currentText().strip() if kind == "dialogue" else None
                )
                if kind == "dialogue" and (
                    not character
                    or any(char in character for char in "|]\r\n")
                ):
                    raise ValueError("角色缩写不能为空，且不能包含 | 或 ]")
                delays = parse_delay_text(
                    self.delay_edit.text(), visible_text_length(text), self.default_delay.value()
                )
                return Dialogue(character, text, delays)
            if kind == "sleep":
                value = self.value_edit.text().strip()
                try:
                    if float(value) < 0:
                        raise ValueError
                except ValueError as exc:
                    raise ValueError("休息时长必须是非负数字") from exc
                return Directive("s", value)
            if kind == "music":
                return Directive("p", self.value_edit.text().strip())
            return Directive("c")

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
            self.script.lines[rows[0].row()] = event
            self._render_table()
            self.table.selectRow(rows[0].row())
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
            self.current_path = None
            self._render_table()
            self.setWindowTitle("TSCP 剧情编辑器")
            self.statusBar().showMessage("已新建剧情")

        def open_file(self) -> None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "打开 TSCP 文件", "", "TSCP 文件 (*.tscp);;所有文件 (*)"
            )
            if filename:
                self._open_path(Path(filename))

        def _open_path(self, path: Path) -> None:
            try:
                self.script = load_script(path)
            except (OSError, TSCPError, UnicodeError) as exc:
                self._show_error("无法打开剧情文件：%s" % exc)
                return
            self.current_path = path
            self._render_table()
            self.setWindowTitle("TSCP 剧情编辑器 - %s" % path.name)
            self.statusBar().showMessage("已打开：%s" % path)

        def save_file(self) -> None:
            if self.current_path is None:
                self.save_as()
                return
            self._save_path(self.current_path)

        def save_as(self) -> None:
            filename, _ = QFileDialog.getSaveFileName(
                self, "保存 TSCP 文件", "", "TSCP 文件 (*.tscp);;所有文件 (*)"
            )
            if filename:
                path = Path(filename)
                if path.suffix.lower() != ".tscp":
                    path = path.with_suffix(".tscp")
                self._save_path(path)

        def _save_path(self, path: Path) -> None:
            try:
                save_script(path, self.script)
            except (OSError, TSCPError, UnicodeError, ValueError) as exc:
                self._show_error("无法保存剧情文件：%s" % exc)
                return
            self.current_path = path
            self.setWindowTitle("TSCP 剧情编辑器 - %s" % path.name)
            self.statusBar().showMessage("已保存：%s" % path)

        def _show_error(self, message: str) -> None:
            QMessageBox.critical(self, "TSCPEditor", message)


else:
    TSCPEditorWindow = None  # type: ignore[assignment,misc]


# Keep the old class name available to third-party launchers.
PlotWriterWindow = TSCPEditorWindow


def main(argv: Optional[List[str]] = None) -> int:
    """Launch the GUI, optionally opening a compiled plot path."""

    parser = argparse.ArgumentParser(description="编辑 TSCP 剧情文件")
    parser.add_argument("path", nargs="?", help="要打开的 .tscp 文件")
    args = parser.parse_args(argv)
    if not QT_AVAILABLE:
        raise RuntimeError(
            "TSCPEditor 需要安装可选依赖 PySide6。请运行 "
            "`python -m pip install PySide6`。"
        )
    app = QApplication(sys.argv[:1])
    window = TSCPEditorWindow()
    if args.path:
        window._open_path(Path(args.path))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
