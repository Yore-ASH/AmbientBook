"""A single-file editor for ``.tscps`` that shows the markup instead of hiding it.

Two things make this different from editing in a plain text box:

* every marker is drawn as a labelled chip, so a colour can be seen, moved or
  deleted rather than having to be remembered;
* each run is drawn inside a frame -- a colour block gets one style, a ``\\ge``
  group gets another -- so the extent of a block is obvious at a glance.

Both the author-facing marks (``\\co``, ``\\ge``) and the raw ANSI codes older
files already contain are shown the same way.  Nothing has to be converted for a
file to be readable; the conversion button is there for authors who want it.

Run it standalone with::

    python -m Studio.script_editor some.tscps
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

if __package__ in {None, ""}:  # pragma: no cover - standalone entry point
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tscp_player import marks

try:  # pragma: no cover - depends on the optional GUI package
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        QTextBrowser,
        QVBoxLayout,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


#: Repaint delay after a keystroke: re-rendering on every key is wasteful and
#: makes typing feel heavy on a long file.
RENDER_DELAY_MS = 120


if QT_AVAILABLE:

    class ScriptEditorDialog(QDialog):
        """Edit one ``.tscps`` with a live, markup-aware view beside it."""

        def __init__(self, parent=None, path: Optional[Path] = None, text: str = "") -> None:
            super().__init__(parent)
            self.path: Optional[Path] = Path(path) if path else None
            self.dirty = False
            self.setWindowTitle("剧本编辑器")
            self.resize(1100, 640)

            self.source = QPlainTextEdit()
            self.source.setPlaceholderText(
                "在这里写剧本。色块写成 \\co?00ffaa文字\\co，"
                "整体出现写成 \\ge文字\\ge；原有的 ANSI 代码也照样能读。"
            )
            self.source.setStyleSheet("font-family:Consolas,'Courier New',monospace;")
            self.source.textChanged.connect(self._schedule_render)

            self.view = QTextBrowser()
            self.view.setOpenExternalLinks(False)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(self._titled("源文件（保存的就是这里的文本）", self.source))
            splitter.addWidget(self._titled("标记视图（每个块一个框）", self.view))
            splitter.setSizes([520, 580])

            self.open_button = QPushButton("打开…")
            self.open_button.clicked.connect(self.open_file)
            self.save_button = QPushButton("保存")
            self.save_button.clicked.connect(self.save)
            self.save_as_button = QPushButton("另存为…")
            self.save_as_button.clicked.connect(self.save_as)
            self.convert_button = QPushButton("把 ANSI 转成标记")
            self.convert_button.setToolTip(
                "把 \\033[38;2;r;g;bm 这类真彩代码换成 \\co?rrggbb。"
                "换不掉的（比如加粗、16 色）保持原样，不会改变显示效果"
            )
            self.convert_button.clicked.connect(self.convert_ansi)
            self.group_button = QPushButton("选中设为整体")
            self.group_button.setToolTip("用 \\ge 把选中的文字包成一个整体，共用一个时间戳")
            self.group_button.clicked.connect(self.wrap_group)
            self.colour_button = QPushButton("选中上色…")
            self.colour_button.setToolTip("给选中的文字加一个颜色块")
            self.colour_button.clicked.connect(self.wrap_colour)

            tools = QHBoxLayout()
            for button in (
                self.open_button, self.save_button, self.save_as_button,
            ):
                tools.addWidget(button)
            tools.addSpacing(16)
            for button in (self.group_button, self.colour_button, self.convert_button):
                tools.addWidget(button)
            tools.addStretch(1)

            self.status = QLabel()
            self.status.setObjectName("hint")

            # Files are saved from the toolbar; these two hand the edited text
            # back to whoever opened the dialog.
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            self.buttons = buttons

            layout = QVBoxLayout(self)
            layout.addLayout(tools)
            layout.addWidget(splitter, 1)
            row = QHBoxLayout()
            row.addWidget(self.status, 1)
            row.addWidget(buttons)
            layout.addLayout(row)

            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(RENDER_DELAY_MS)
            self._timer.timeout.connect(self.render)

            if text:
                self.source.setPlainText(text)
            elif self.path is not None:
                self.load(self.path)
            self.render()
            self._update_title()

        # -- construction --------------------------------------------------
        @staticmethod
        def _titled(caption: str, widget):
            from PySide6.QtWidgets import QWidget

            holder = QWidget()
            box = QVBoxLayout(holder)
            box.setContentsMargins(0, 0, 0, 0)
            label = QLabel(caption)
            label.setObjectName("hint")
            box.addWidget(label)
            box.addWidget(widget, 1)
            return holder

        # -- files ---------------------------------------------------------
        def load(self, path: Path) -> bool:
            try:
                self.source.setPlainText(Path(path).read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                QMessageBox.critical(self, "剧本编辑器", "读不了这个文件：%s" % exc)
                return False
            self.path = Path(path)
            self.dirty = False
            self._update_title()
            return True

        def open_file(self) -> None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "打开剧本", "", "TSCP 剧本源文件 (*.tscps);;所有文件 (*)"
            )
            if filename:
                self.load(Path(filename))

        def save(self) -> bool:
            if self.path is None:
                return self.save_as()
            return self._write(self.path)

        def save_as(self) -> bool:
            suggestion = str(self.path) if self.path else "未命名.tscps"
            filename, _ = QFileDialog.getSaveFileName(
                self, "保存剧本", suggestion,
                "TSCP 剧本源文件 (*.tscps);;所有文件 (*)",
            )
            if not filename:
                return False
            return self._write(Path(filename))

        def _write(self, target: Path) -> bool:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self.source.toPlainText(), encoding="utf-8")
            except OSError as exc:
                QMessageBox.critical(self, "剧本编辑器", "写不进去：%s" % exc)
                return False
            self.path = target
            self.dirty = False
            self._update_title()
            self.status.setText("已保存 %s" % target.name)
            return True

        def _update_title(self) -> None:
            name = self.path.name if self.path else "未命名"
            self.setWindowTitle("剧本编辑器 - %s%s" % (name, " *" if self.dirty else ""))

        # -- editing helpers -----------------------------------------------
        def _wrap_selection(self, prefix: str, suffix: str, what: str) -> None:
            cursor = self.source.textCursor()
            chosen = cursor.selectedText()
            if not chosen:
                QMessageBox.information(self, "剧本编辑器", "先选中要处理的文字")
                return
            # selectedText() uses U+2029 for line breaks; a mark must not span
            # lines, so a multi-line selection is refused rather than mangled.
            if "\u2029" in chosen:
                QMessageBox.information(self, "剧本编辑器", "一次只能处理一行")
                return
            cursor.insertText("%s%s%s" % (prefix, chosen, suffix))
            self.source.setTextCursor(cursor)

        def wrap_group(self) -> None:
            self._wrap_selection("\\ge", "\\ge", "整体")

        def wrap_colour(self) -> None:
            from PySide6.QtGui import QColor
            from PySide6.QtWidgets import QColorDialog

            chosen = QColorDialog.getColor(
                QColor("#ffffff"), self, "这段文字的颜色"
            )
            if not chosen.isValid():
                return
            value = chosen.name().lstrip("#")
            self._wrap_selection("\\co?%s" % value, "\\co", "颜色")

        def convert_ansi(self) -> None:
            before = self.source.toPlainText()
            after = marks.contract_colours(before)
            if after == before:
                self.status.setText("没有可以转换的真彩色代码（其他 ANSI 会原样保留）")
                return
            self.source.setPlainText(after)
            self.status.setText("已把真彩色代码换成 \\co 标记")

        # -- rendering -----------------------------------------------------
        def _schedule_render(self) -> None:
            self.dirty = True
            self._update_title()
            self._timer.start()

        def render(self) -> None:
            body = []
            for number, line in enumerate(self.source.toPlainText().splitlines(), 1):
                # Literal \033 spellings are shown as text; only real escapes
                # are drawn, so the view does not lie about what is in the file.
                body.append(
                    '<div style="margin:0 0 2px 0;">'
                    '<span style="color:#6b7280;">%s</span>%s</div>'
                    % (str(number).rjust(3), marks.to_editor_html(line))
                )
            self.view.setHtml(
                '<body style="background:#15181d;">%s</body>' % "".join(body)
            )

        # -- window --------------------------------------------------------
        def closeEvent(self, event) -> None:
            if self.dirty and self.source.toPlainText().strip():
                answer = QMessageBox.question(
                    self,
                    "剧本编辑器",
                    "还没保存，要保存吗？",
                    QMessageBox.StandardButton.Save
                    | QMessageBox.StandardButton.Discard
                    | QMessageBox.StandardButton.Cancel,
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    event.ignore()
                    return
                if answer == QMessageBox.StandardButton.Save and not self.save():
                    event.ignore()
                    return
            event.accept()


def main(argv=None) -> int:  # pragma: no cover - interactive entry point
    if not QT_AVAILABLE:
        print("需要 PySide6：pip install -r requirements.txt")
        return 1
    arguments = list(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication([])
    target = Path(arguments[0]) if arguments else None
    dialog = ScriptEditorDialog(None, target)
    dialog.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
