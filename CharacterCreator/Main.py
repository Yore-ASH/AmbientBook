"""Optional Chinese PySide6 launcher for the character configuration creator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from CharacterCreator.model import (
    ANSI_COLORS,
    build_ansi_style,
    generate_json_entry,
    generate_json_entries,
    make_character_config,
    normalize_ansi,
    parse_batch_text,
)

try:  # pragma: no cover - exercised only when the optional GUI is available
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QCheckBox,
        QComboBox,
        QFormLayout,
        QGroupBox,
        QDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional dependency
    QT_AVAILABLE = False


if QT_AVAILABLE:

    class CharacterCreatorWindow(QMainWindow):
        """Create and copy one ``CHARACTERS`` object entry."""

        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("角色配置生成器")
            self.resize(760, 620)
            self._build_ui()
            self._update_preview()

        def _build_ui(self) -> None:
            self.abbreviation = QLineEdit()
            self.abbreviation.setPlaceholderText("例如 f")
            self.full_name = QLineEdit()
            self.full_name.setPlaceholderText("例如 FISH")
            self.full_name.textChanged.connect(self._update_preview)

            self.color = QComboBox()
            for label, code in ANSI_COLORS:
                self.color.addItem(label, code)
            self.color.currentIndexChanged.connect(self._update_preview)
            self.bold = QCheckBox("粗体（1）")
            self.underline = QCheckBox("下划线（4）")
            self.bold.stateChanged.connect(self._update_preview)
            self.underline.stateChanged.connect(self._update_preview)

            self.custom_ansi = QLineEdit()
            self.custom_ansi.setPlaceholderText(r"\033[33;1m（可粘贴 \x1b 或实际 ESC）")
            self.custom_ansi.setToolTip("填写后优先使用自定义 SGR；不支持斜体")
            self.custom_ansi.textChanged.connect(self._update_preview)

            form = QFormLayout()
            form.addRow("角色缩写：", self.abbreviation)
            form.addRow("角色全名：", self.full_name)
            form.addRow("常用颜色：", self.color)
            attributes = QHBoxLayout()
            attributes.addWidget(self.bold)
            attributes.addWidget(self.underline)
            attributes.addStretch()
            form.addRow("文字属性：", attributes)
            form.addRow("自定义 ANSI：", self.custom_ansi)

            style_box = QGroupBox("预览")
            style_layout = QVBoxLayout(style_box)
            self.preview = QLabel()
            self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview.setMinimumHeight(75)
            self.preview.setStyleSheet(
                "QLabel { background: #111827; color: #f9fafb; "
                "border: 1px solid #374151; padding: 12px; }"
            )
            self.style_label = QLabel()
            style_layout.addWidget(self.preview)
            style_layout.addWidget(self.style_label)

            self.output = QPlainTextEdit()
            self.output.setReadOnly(True)
            self.output.setPlaceholderText("生成的 JSON 条目会显示在这里")
            self.output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

            generate = QPushButton("生成 JSON")
            generate.clicked.connect(self._generate)
            copy = QPushButton("复制到剪贴板")
            copy.clicked.connect(self._copy)
            clear = QPushButton("清空")
            clear.clicked.connect(self._clear)
            batch = QPushButton("批量生成")
            batch.clicked.connect(self._batch_generate)
            buttons = QHBoxLayout()
            buttons.addWidget(generate)
            buttons.addWidget(copy)
            buttons.addWidget(clear)
            buttons.addWidget(batch)
            buttons.addStretch()

            central = QWidget()
            layout = QVBoxLayout(central)
            layout.addLayout(form)
            layout.addWidget(style_box)
            layout.addWidget(QLabel("可粘贴到 CHARACTERS 对象："))
            layout.addWidget(self.output, 1)
            layout.addLayout(buttons)
            self.setCentralWidget(central)
            self.statusBar().showMessage("请选择样式并生成 JSON")

        def _selected_style(self) -> str:
            custom = self.custom_ansi.text().strip()
            if custom:
                return normalize_ansi(custom)
            return build_ansi_style(
                self.color.currentData(),
                bold=self.bold.isChecked(),
                underline=self.underline.isChecked(),
            )

        def _update_preview(self) -> None:
            try:
                style = self._selected_style()
            except ValueError as exc:
                self.style_label.setText("样式错误：" + str(exc))
                self.preview.setText(self.full_name.text() or "角色全名预览")
                return
            self.style_label.setText(
                "ANSI：" + (style.replace("\x1b", r"\033") if style else "（无样式）")
            )
            self.preview.setText(self.full_name.text() or "角色全名预览")
            font = QFont(self.preview.font())
            font.setBold(self.bold.isChecked() and not self.custom_ansi.text().strip())
            font.setUnderline(
                self.underline.isChecked() and not self.custom_ansi.text().strip()
            )
            self.preview.setFont(font)
            color_code = self.color.currentData()
            color_map = {
                30: "#6b7280", 31: "#ef4444", 32: "#22c55e", 33: "#eab308",
                34: "#3b82f6", 35: "#d946ef", 36: "#06b6d4", 37: "#f9fafb",
                90: "#9ca3af", 91: "#f87171", 92: "#4ade80", 93: "#fde047",
                94: "#60a5fa", 95: "#e879f9", 96: "#22d3ee", 97: "#ffffff",
            }
            preview_color = color_map.get(color_code, "#f9fafb")
            self.preview.setStyleSheet(
                "QLabel { background: #111827; color: %s; "
                "border: 1px solid #374151; padding: 12px; }" % preview_color
            )

        def _generate(self) -> None:
            try:
                self.output.setPlainText(
                    generate_json_entry(
                        self.abbreviation.text(),
                        self.full_name.text(),
                        self._selected_style(),
                    )
                )
                self.statusBar().showMessage("已生成，可复制到 CHARACTERS 对象")
            except ValueError as exc:
                QMessageBox.warning(self, "输入有误", str(exc))

        def _copy(self) -> None:
            if not self.output.toPlainText().strip():
                self._generate()
            if self.output.toPlainText().strip():
                QApplication.clipboard().setText(self.output.toPlainText())
                self.statusBar().showMessage("已复制到剪贴板")

        def _batch_generate(self) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("批量生成角色配置")
            dialog.resize(760, 560)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("逐个填写角色并点击“加入列表”，最后统一生成。"))
            batch_form = QFormLayout()
            batch_abbreviation = QLineEdit()
            batch_name = QLineEdit()
            batch_color = QComboBox()
            for label, code in ANSI_COLORS:
                batch_color.addItem(label, code)
            batch_bold = QCheckBox("粗体")
            batch_underline = QCheckBox("下划线")
            batch_custom = QLineEdit()
            batch_custom.setPlaceholderText(r"\033[33;1m（可留空）")
            batch_form.addRow("角色缩写：", batch_abbreviation)
            batch_form.addRow("角色全名：", batch_name)
            batch_form.addRow("常用颜色：", batch_color)
            attrs = QHBoxLayout()
            attrs.addWidget(batch_bold)
            attrs.addWidget(batch_underline)
            attrs.addStretch()
            batch_form.addRow("文字属性：", attrs)
            batch_form.addRow("自定义 ANSI：", batch_custom)
            layout.addLayout(batch_form)
            preview_box = QGroupBox("样式预览")
            preview_layout = QVBoxLayout(preview_box)
            batch_preview = QLabel("角色全名预览")
            batch_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            batch_preview.setMinimumHeight(60)
            batch_preview.setStyleSheet(
                "QLabel { background: #111827; color: #f9fafb; "
                "border: 1px solid #374151; padding: 10px; }"
            )
            preview_layout.addWidget(batch_preview)
            layout.addWidget(preview_box)
            table = QTableWidget(0, 3)
            table.setHorizontalHeaderLabels(["角色缩写", "角色全名", "ANSI 样式"])
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            layout.addWidget(table, 1)
            output = QPlainTextEdit()
            output.setReadOnly(True)
            output.setPlaceholderText("最后统一生成的 CHARACTERS JSON 对象")
            layout.addWidget(output, 1)
            buttons = QHBoxLayout()
            add = QPushButton("加入列表")
            remove = QPushButton("删除选中")
            generate = QPushButton("统一生成")
            copy = QPushButton("复制结果")
            close = QPushButton("关闭")
            buttons.addWidget(add)
            buttons.addWidget(remove)
            buttons.addWidget(generate)
            buttons.addWidget(copy)
            buttons.addWidget(close)
            layout.addLayout(buttons)

            rows: list[tuple[str, str, str]] = []

            def selected_style() -> str:
                custom = batch_custom.text().strip()
                return normalize_ansi(custom) if custom else build_ansi_style(
                    batch_color.currentData(),
                    bold=batch_bold.isChecked(),
                    underline=batch_underline.isChecked(),
                )

            def update_batch_preview() -> None:
                try:
                    style = selected_style()
                    preview_text = batch_name.text() or "角色全名预览"
                    color_code = batch_color.currentData()
                    color_map = {
                        30: "#6b7280", 31: "#ef4444", 32: "#22c55e", 33: "#eab308",
                        34: "#3b82f6", 35: "#d946ef", 36: "#06b6d4", 37: "#f9fafb",
                        90: "#9ca3af", 91: "#f87171", 92: "#4ade80", 93: "#fde047",
                        94: "#60a5fa", 95: "#e879f9", 96: "#22d3ee", 97: "#ffffff",
                    }
                    preview_color = color_map.get(color_code, "#f9fafb")
                    batch_preview.setText(preview_text)
                    batch_preview.setStyleSheet(
                        "QLabel { background: #111827; color: %s; "
                        "border: 1px solid #374151; padding: 10px; }" % preview_color
                    )
                    font = QFont(batch_preview.font())
                    font.setBold(batch_bold.isChecked() and not batch_custom.text().strip())
                    font.setUnderline(
                        batch_underline.isChecked() and not batch_custom.text().strip()
                    )
                    batch_preview.setFont(font)
                except ValueError as exc:
                    batch_preview.setText("样式错误：" + str(exc))

            for widget_signal in (
                batch_name.textChanged,
                batch_custom.textChanged,
                batch_color.currentIndexChanged,
                batch_bold.stateChanged,
                batch_underline.stateChanged,
            ):
                widget_signal.connect(update_batch_preview)

            def refresh_table() -> None:
                table.setRowCount(len(rows))
                for row, values in enumerate(rows):
                    for column, value in enumerate(values):
                        table.setItem(row, column, QTableWidgetItem(value.replace("\x1b", r"\033")))

            def add_row() -> None:
                try:
                    style = selected_style()
                    config = make_character_config(
                        batch_abbreviation.text(), batch_name.text(), style
                    )
                    if any(row[0] == config.abbreviation for row in rows):
                        raise ValueError("角色缩写重复：%s" % config.abbreviation)
                    rows.append((config.abbreviation, config.full_name, config.style))
                    refresh_table()
                    batch_abbreviation.clear()
                    batch_name.clear()
                except ValueError as exc:
                    QMessageBox.warning(dialog, "输入有误", str(exc))

            def remove_row() -> None:
                selected = table.selectionModel().selectedRows()
                if selected:
                    del rows[selected[0].row()]
                    refresh_table()

            def preview_selected() -> None:
                selected = table.selectionModel().selectedRows()
                if not selected:
                    return
                abbreviation, name, style = rows[selected[0].row()]
                batch_preview.setText(name)
                batch_preview.setStyleSheet(
                    "QLabel { background: #111827; color: #f9fafb; "
                    "border: 1px solid #374151; padding: 10px; }"
                )
                batch_custom.setText(style.replace("\x1b", r"\033"))
                self.statusBar().showMessage("正在预览角色：%s（%s）" % (name, abbreviation))

            table.itemSelectionChanged.connect(preview_selected)

            def generate_batch() -> None:
                try:
                    output.setPlainText(generate_json_entries(rows))
                except ValueError as exc:
                    QMessageBox.warning(dialog, "输入有误", str(exc))

            add.clicked.connect(add_row)
            remove.clicked.connect(remove_row)
            generate.clicked.connect(generate_batch)
            def copy_batch() -> None:
                generate_batch()
                if output.toPlainText().strip():
                    QApplication.clipboard().setText(output.toPlainText())

            copy.clicked.connect(copy_batch)
            close.clicked.connect(dialog.close)
            update_batch_preview()
            dialog.exec()

        def _clear(self) -> None:
            self.abbreviation.clear()
            self.full_name.clear()
            self.custom_ansi.clear()
            self.bold.setChecked(False)
            self.underline.setChecked(False)
            self.color.setCurrentIndex(0)
            self.output.clear()
            self.statusBar().showMessage("已清空")


else:
    CharacterCreatorWindow = None  # type: ignore[assignment,misc]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Launch the optional GUI."""

    parser = argparse.ArgumentParser(description="生成 CHARACTERS 角色配置")
    parser.parse_args(argv)
    if not QT_AVAILABLE:
        raise RuntimeError(
            "CharacterCreator 需要安装可选依赖 PySide6。请运行 "
            "`python -m pip install PySide6`。"
        )
    app = QApplication(sys.argv[:1])
    window = CharacterCreatorWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
