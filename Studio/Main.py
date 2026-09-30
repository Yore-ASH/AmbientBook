"""The unified desktop studio: one window for the whole authoring flow.

Steps, left to right: create characters, write the story (and drop music in),
time every character, write or record the lyrics, then export the ``.tscpkg``.
Nothing is edited as raw text: lines go in through dialogs, and the timing step
records keystrokes against the real music.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import CharacterCreator.model as characters_model
from PlotManager.model import MusicDraft, PackError
from PlotManager.recorder import LyricsRecorderDialog
from Studio import model as studio
from Studio import theme
from Studio.timeline import (
    DEFAULT_SCALE as DEFAULT_ZOOM,
    MAX_SCALE as MAX_ZOOM,
    MIN_SCALE as MIN_ZOOM,
    TimelineView,
)
from Studio.model import (
    CLEAR,
    DIALOGUE,
    MUSIC,
    NARRATION,
    SLEEP,
    EVENT_LABELS,
    StudioError,
    StudioProject,
    describe_event,
    event_kind,
    event_label,
    next_script_name,
)
from Ts2Tp.model import KeyboardTimingModel, TimingMode
from tscp_player.audio import MusicPlayer
from tscp_player.format import (
    ANSI_SEQUENCE_RE,
    Dialogue,
    Directive,
    Script,
    is_note,
    note_parts,
    visible_text_length,
)
from tscp_player.lyrics import lyric_source_lines, parse_lrc, serialize_lrc, timed_from_marks
from tscp_player.music import STOP_WORDS

try:  # pragma: no cover - depends on the optional GUI package
    from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, Signal
    from PySide6.QtGui import (
        QAction,
        QColor,
        QFontMetrics,
        QIcon,
        QKeySequence,
        QPainter,
        QPen,
        QShortcut,
        QTextCursor,
    )
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QColorDialog,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QRadioButton,
        QScrollBar,
        QSlider,
        QSplitter,
        QStackedWidget,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


AUDIO_FILTER = (
    "音频 (*.flac *.mp3 *.ogg *.oga *.opus *.wav *.m4a *.aac);;所有文件 (*)"
)
LYRIC_COLOR_DEFAULT = "#ffffff"

#: Automatic snapshots: every five minutes, backing off while nothing changes so
#: an idle window does not keep rewriting the file.
AUTO_SNAPSHOT_SECONDS = 300
AUTO_SNAPSHOT_MAX_BACKOFF = 12          # 5 min -> ... -> 1 hour
AUTO_SNAPSHOT_MENU_LABEL = "自动快照（每 5 分钟）"

#: How the timing step lets you edit: press keys, drag blocks, or type numbers.
VIEW_RECORD = "record"
VIEW_TIMELINE = "timeline"
VIEW_NUMBERS = "numbers"

#: Shipped alongside this module; regenerate with ``python Studio/make_icon.py``.
ICON_PATH = Path(__file__).resolve().with_name("tscp-studio.ico")


# --------------------------------------------------------------------------
# small shared helpers
# --------------------------------------------------------------------------

def _ansi_css(sequence: str) -> str:
    """Translate a common SGR escape into an inline CSS fragment."""

    normalised = str(sequence).replace(r"\033", "\x1b").replace(r"\x1b", "\x1b")
    match = re.search(r"\x1b\[([0-9;]*)m", normalised)
    if not match:
        return ""
    colors = {
        30: "#000000", 31: "#cc3333", 32: "#33cc66", 33: "#cccc33",
        34: "#4488ff", 35: "#cc66cc", 36: "#33cccc", 37: "#eeeeee",
        90: "#777777", 91: "#ff6666", 92: "#66ee88", 93: "#ffff66",
        94: "#66aaff", 95: "#ee88ee", 96: "#66eeee", 97: "#ffffff",
    }
    css: List[str] = []
    for raw in match.group(1).split(";"):
        code = int(raw or 0)
        if code == 0:
            css = []
        elif code in colors:
            css.append("color:%s" % colors[code])
        elif code == 1:
            css.append("font-weight:bold")
        elif code == 4:
            css.append("text-decoration:underline")
    return ";".join(css)


def _styled_html(text: str, current_index: Optional[int] = None) -> str:
    """Render dialogue text with ANSI styling and an optional current-character mark."""

    parts = re.split("(" + ANSI_SEQUENCE_RE.pattern + ")", text)
    visible_index = 0
    style = ""
    output: List[str] = []
    for part in parts:
        if not part:
            continue
        if ANSI_SEQUENCE_RE.fullmatch(part):
            style = _ansi_css(part)
            continue
        for character in part:
            css = style
            if current_index == visible_index:
                css = (css + ";" if css else "") + (
                    "background:#f6d365;color:#111;font-weight:bold;padding:1px 3px"
                )
            safe = html.escape(character).replace(" ", "&nbsp;")
            output.append("<span style='%s'>%s</span>" % (css, safe) if css else safe)
            visible_index += 1
    return "".join(output)


def _style_css(style: str) -> str:
    """CSS for a character style, usable on a ``QLabel`` or table cell."""

    return _ansi_css(style)


#: Rows in the studio's tables are 22 px: one line of text with no wasted space.
COMPACT_ROW_HEIGHT = 22


def compact_table(table) -> None:
    """Make a table dense: no row-number gutter, fixed short rows.

    A plot with a hundred lines has to stay readable without becoming a
    scrolling maze, so every table in the studio goes through here.
    """

    if not QT_AVAILABLE:  # pragma: no cover - the whole UI is absent
        return
    header = table.verticalHeader()
    header.setVisible(False)
    header.setDefaultSectionSize(COMPACT_ROW_HEIGHT)
    header.setMinimumSectionSize(COMPACT_ROW_HEIGHT)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    table.setShowGrid(False)
    if table.alternatingRowColors() is False:
        table.setAlternatingRowColors(True)


if QT_AVAILABLE:

    # ----------------------------------------------------------------------
    # dialogs
    # ----------------------------------------------------------------------

    class CharacterFields(QWidget):
        """名字 / 颜色 / 粗体 / 下划线 / 自定义 SGR, with a live preview.

        The file-level key still exists (``[f]`` and ``D|f|`` need one) but it is
        derived from the name and tucked away, so the normal flow never asks for
        it.  It only appears when editing a character that already has one, or
        when somebody deliberately wants to pick their own.
        """

        def __init__(
            self,
            parent=None,
            key: str = "",
            name: str = "",
            style: str = "",
            taken: Iterable[str] = (),
        ) -> None:
            super().__init__(parent)
            self.original_key = key
            # The key being edited does not count as a collision with itself.
            self.taken = {str(item) for item in taken} - ({key} if key else set())

            self.name_edit = QLineEdit(name)
            self.name_edit.setPlaceholderText("显示在屏幕上的名字")

            self.color_combo = QComboBox()
            for label, code in characters_model.ANSI_COLORS:
                self.color_combo.addItem(label, code)
            self.bold = QCheckBox("粗体")
            self.underline = QCheckBox("下划线")
            self.custom_edit = QLineEdit()
            self.custom_edit.setPlaceholderText(r"也可以直接填 \033[33;1m 这类 SGR")

            self.preview = QLabel()
            self.preview.setMinimumHeight(46)
            self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.preview.setStyleSheet(
                "background:#111; color:#ddd; border:1px solid #333;"
            )

            # -- the tucked-away file key ----------------------------------
            self.key_edit = QLineEdit(key)
            self.key_edit.setMaximumWidth(140)
            self.key_edit.setPlaceholderText("自动")
            self.manual_key = QCheckBox("手动指定文件内标识")
            self.manual_key.setToolTip(
                "只是 .tscp 文件内部的记号，剧本里看不见它；一般不用管"
            )
            self.manual_key.setChecked(bool(key))
            self.key_edit.setEnabled(bool(key))
            self.key_hint = QLabel()
            self.key_hint.setObjectName("hint")

            key_row = QHBoxLayout()
            key_row.addWidget(self.manual_key)
            key_row.addWidget(self.key_edit)
            key_row.addWidget(self.key_hint, 1)

            attrs = QHBoxLayout()
            attrs.addWidget(self.bold)
            attrs.addWidget(self.underline)
            attrs.addStretch(1)
            holder = QWidget()
            holder.setLayout(attrs)
            key_holder = QWidget()
            key_holder.setLayout(key_row)

            form = QFormLayout(self)
            form.addRow("名字", self.name_edit)
            form.addRow("颜色", self.color_combo)
            form.addRow("样式", holder)
            form.addRow("自定义", self.custom_edit)
            form.addRow("预览", self.preview)
            form.addRow("标识", key_holder)

            for widget in (self.name_edit, self.custom_edit, self.key_edit):
                widget.textChanged.connect(self._refresh_preview)
            self.color_combo.currentIndexChanged.connect(self._refresh_preview)
            self.bold.toggled.connect(self._refresh_preview)
            self.underline.toggled.connect(self._refresh_preview)
            self.manual_key.toggled.connect(self._toggle_key_field)

            if style:
                self.custom_edit.setText(self._as_spelling(style))
            self._refresh_preview()

        @staticmethod
        def _as_spelling(style: str) -> str:
            """Show an existing style back in the ``\\033[...m`` spelling."""

            match = re.search(r"\[([0-9;]*)m", str(style).replace("\x1b", r"\033"))
            return r"\033[%sm" % match.group(1) if match else ""

        def _toggle_key_field(self) -> None:
            self.key_edit.setEnabled(self.manual_key.isChecked())
            if self.manual_key.isChecked() and not self.key_edit.text().strip():
                self.key_edit.setText(self.resolved_key())
            self._refresh_preview()

        def resolved_key(self) -> str:
            if self.manual_key.isChecked():
                chosen = self.key_edit.text().strip()
                if chosen:
                    return chosen
            return studio.suggest_key(self.name_edit.text(), self.taken)

        def resolved_style(self) -> str:
            custom = self.custom_edit.text().strip()
            if custom:
                return characters_model.normalize_ansi(custom)
            return characters_model.build_ansi_style(
                self.color_combo.currentData(),
                bold=self.bold.isChecked(),
                underline=self.underline.isChecked(),
            )

        def _refresh_preview(self) -> None:
            if self.manual_key.isChecked():
                self.key_hint.setText("")
            else:
                self.key_hint.setText("自动：%s" % self.resolved_key())
            name = self.name_edit.text().strip() or "角色名"
            try:
                css = _style_css(self.resolved_style())
            except ValueError as exc:
                self.preview.setText("样式无效：%s" % exc)
                self.preview.setStyleSheet("background:#111; color:#ff8888;")
                return
            self.preview.setStyleSheet(
                "background:#111; color:#ddd; border:1px solid #333;"
            )
            self.preview.setText("<span style='%s'>%s</span>" % (css, html.escape(name)))

        def values(self) -> tuple:
            return (
                self.resolved_key(),
                self.name_edit.text().strip(),
                self.resolved_style(),
            )

        def reset(self) -> None:
            """Empty the fields, ready for the next character."""

            self.name_edit.clear()
            self.custom_edit.clear()
            self.bold.setChecked(False)
            self.underline.setChecked(False)
            self.manual_key.setChecked(False)
            self.key_edit.clear()
            self.key_edit.setEnabled(False)
            self.name_edit.setFocus()

    class CharacterDialog(QDialog):
        """Create or edit one character, with a live style preview."""

        def __init__(
            self,
            parent,
            key: str = "",
            name: str = "",
            style: str = "",
            taken: Iterable[str] = (),
        ) -> None:
            super().__init__(parent)
            self.setWindowTitle("角色")
            self.resize(500, 440)

            self.fields = CharacterFields(self, key, name, style, taken)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

            layout = QVBoxLayout(self)
            layout.addWidget(self.fields, 1)
            layout.addWidget(buttons)

        def values(self) -> tuple:
            return self.fields.values()

        def accept(self) -> None:
            _key, name, _style = self.values()
            if not name:
                QMessageBox.warning(self, "角色", "角色名字不能为空")
                return
            super().accept()

    class BatchAddDialog(QDialog):
        """Add many characters in one sitting.

        This keeps the character generator's batch flow: fill the fields, press
        「添加到列表」, and the row joins the list; 「全部添加」 commits them all at
        once.  The fields never close in between, so a whole cast goes in without
        reopening a dialog for every name.
        """

        def __init__(self, parent, existing: Optional[Dict[str, object]] = None) -> None:
            super().__init__(parent)
            self.setWindowTitle("批量添加角色")
            self.resize(860, 460)
            self.rows: List[tuple] = []
            self.existing = dict(existing or {})
            # A name already in the project identifies that character, so adding
            # it again is an update rather than a second entry under a new key.
            self.by_name = {
                character.name: key for key, character in self.existing.items()
            }

            self.fields = CharacterFields(self, taken=list(self.existing))

            add_button = QPushButton("添加到列表")
            add_button.setDefault(True)
            add_button.clicked.connect(self.add_row)
            self.fields.key_edit.returnPressed.connect(self.add_row)
            self.fields.name_edit.returnPressed.connect(self.add_row)

            remove_button = QPushButton("从列表移除")
            remove_button.clicked.connect(self.remove_row)

            self.table = QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["名字", "样式", "标识"])
            compact_table(self.table)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)

            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.addWidget(self.fields)
            left_layout.addWidget(add_button)
            left_layout.addStretch(1)

            right = QWidget()
            right_layout = QVBoxLayout(right)
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.addWidget(QLabel("待添加的角色"))
            right_layout.addWidget(self.table, 1)
            right_layout.addWidget(remove_button)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(left)
            splitter.addWidget(right)
            splitter.setSizes([430, 430])

            self.hint = QLabel(
                "已有角色在列表里显示为「更新」。列表可以一次全部提交。"
            )
            self.hint.setStyleSheet("color:#777;")

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText("全部添加")
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

            layout = QVBoxLayout(self)
            layout.addWidget(self.hint)
            layout.addWidget(splitter, 1)
            layout.addWidget(buttons)

        def _key_for(self, name: str) -> str:
            """Reuse a key when this name is already known, else derive one."""

            if name in self.by_name:
                return self.by_name[name]
            for key, staged, _style in self.rows:
                if staged == name:
                    return key
            return self.fields.resolved_key()

        def add_row(self) -> None:
            name = self.fields.name_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "角色", "角色名字不能为空")
                return
            style = self.fields.resolved_style()
            key = self._key_for(name)
            for index, (existing_key, _n, _s) in enumerate(self.rows):
                if existing_key == key:
                    self.rows[index] = (key, name, style)
                    break
            else:
                self.rows.append((key, name, style))
                # Keep the next derived key from colliding with this one.
                self.fields.taken.add(key)
            self._refresh_table()
            self.fields.reset()

        def remove_row(self) -> None:
            selected = self.table.selectionModel().selectedRows()
            if not selected:
                QMessageBox.information(self, "角色", "请先在列表里选择一行")
                return
            del self.rows[selected[0].row()]
            self._refresh_table()

        def _refresh_table(self) -> None:
            self.table.setRowCount(len(self.rows))
            for row, (key, name, style) in enumerate(self.rows):
                note = "（更新）" if key in self.existing else ""
                values = (name + note, CharactersStep._style_text(style), key)
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, key)
                    self.table.setItem(row, column, item)
            self.table.resizeColumnsToContents()

        def accept(self) -> None:
            if not self.rows:
                QMessageBox.information(self, "角色", "列表还是空的，先「添加到列表」")
                return
            super().accept()

    class DialogueDialog(QDialog):
        """Write one line of dialogue or narration.

        Characters are picked by **name** — the file key never appears.  Narration
        is a single line and Enter finishes it, because that is how it is written.
        Either kind can drop a coloured character name into the text.
        """

        def __init__(self, parent, studio_window, event: Optional[Dialogue], narrator: bool) -> None:
            super().__init__(parent)
            self.setWindowTitle("添加旁白" if narrator else "角色对白")
            self.resize(640, 300 if narrator else 480)
            self.studio = studio_window
            self.narrator = narrator
            self.characters = dict(studio_window.project.characters)

            # -- who (dialogue only) ---------------------------------------
            self.character_combo = QComboBox()
            for key, character in self.characters.items():
                self.character_combo.addItem(character.name, key)

            # -- what ------------------------------------------------------
            if narrator:
                self.text_edit = QLineEdit()
                self.text_edit.setPlaceholderText("这一句旁白，按 Enter 完成")
                self.text_edit.returnPressed.connect(self.accept)
            else:
                self.text_edit = QPlainTextEdit()
                self.text_edit.setPlaceholderText("这一句要说的话")
                self.text_edit.setMinimumHeight(170)

            # -- drop in a coloured name -----------------------------------
            self.name_combo = QComboBox()
            self.name_combo.addItem("（选择角色）", "")
            for key, character in self.characters.items():
                self.name_combo.addItem(character.name, key)
            insert_button = QPushButton("插入角色名")
            insert_button.setToolTip("把带颜色的角色名字插到光标处")
            insert_button.clicked.connect(self.insert_name)

            insert_row = QHBoxLayout()
            insert_row.addWidget(QLabel("插入"))
            insert_row.addWidget(self.name_combo, 1)
            insert_row.addWidget(insert_button)
            insert_row.addStretch(1)

            self.preview = QLabel()
            self.preview.setWordWrap(True)
            self.preview.setMinimumHeight(38)
            # Without a ceiling a one-line narration dialog stretches the preview
            # panel to fill the whole window.
            self.preview.setMaximumHeight(110)
            self.preview.setTextFormat(Qt.TextFormat.RichText)
            self.preview.setStyleSheet(
                "background:#111; color:#eee; padding:8px; border:1px solid #333;"
            )

            layout = QVBoxLayout(self)
            if not narrator:
                form = QFormLayout()
                form.addRow("角色", self.character_combo)
                layout.addLayout(form)
            layout.addWidget(QLabel("内容"))
            # A single-line editor has no business absorbing vertical space.
            if narrator:
                layout.addWidget(self.text_edit)
            else:
                layout.addWidget(self.text_edit, 1)
            layout.addLayout(insert_row)
            layout.addWidget(QLabel("预览（播放时的样子；上面框里的颜色代码不可见）"))
            layout.addWidget(self.preview)

            hint = QLabel(
                "标点和符号也会参与计时，只有空格是自动显示的。"
                + ("" if narrator else "　Ctrl+Enter 也可以完成。")
            )
            hint.setObjectName("hint")
            layout.addWidget(hint)
            if narrator:
                # Nothing above wants to grow, so push the buttons to the bottom.
                layout.addStretch(1)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
                "完成" if narrator else "确定"
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

            self.text_edit.textChanged.connect(self._refresh_preview)
            if not narrator:
                shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
                shortcut.activated.connect(self.accept)

            if event is not None:
                self._set_text(event.text)
                if event.character:
                    index = self.character_combo.findData(event.character)
                    if index >= 0:
                        self.character_combo.setCurrentIndex(index)
            self._refresh_preview()

        def text(self) -> str:
            return (
                self.text_edit.text() if self.narrator else self.text_edit.toPlainText()
            )

        def _set_text(self, value: str) -> None:
            if self.narrator:
                self.text_edit.setText(value)
            else:
                self.text_edit.setPlainText(value)

        def insert_name(self) -> None:
            key = self.name_combo.currentData()
            character = self.characters.get(key) if key else None
            if character is None:
                return
            snippet = (
                "%s%s\033[0m" % (character.style, character.name)
                if character.style
                else character.name
            )
            # A one-line editor has no plain-text API beyond insert().
            if self.narrator:
                self.text_edit.insert(snippet)
            else:
                self.text_edit.insertPlainText(snippet)
            self._refresh_preview()

        def _refresh_preview(self) -> None:
            text = self.text()
            if not text:
                self.preview.setText("<span style='color:#777'>（还没有内容）</span>")
                return
            self.preview.setText(_styled_html(text))

        def values(self) -> Optional[Dialogue]:
            text = self.text().strip()
            if not text:
                return None
            character = None if self.narrator else self.character_combo.currentData()
            return Dialogue(character, text)

        def accept(self) -> None:
            if self.values() is None:
                QMessageBox.warning(self, "旁白" if self.narrator else "对白", "内容不能为空")
                return
            super().accept()

    class NoteDialog(QDialog):
        """Write a supplement: an aside shown below the story, in its own colour."""

        def __init__(self, parent, event=None) -> None:
            super().__init__(parent)
            from tscp_player.format import NOTE_DEFAULT_SECONDS, make_note, note_parts

            self.setWindowTitle("补充内容")
            self.resize(620, 360)
            self._colour = ""

            self.text_edit = QLineEdit()
            self.text_edit.setPlaceholderText("显示在剧情下方的一行补充，按 Enter 完成")
            self.text_edit.returnPressed.connect(self.accept)

            self.seconds = QDoubleSpinBox()
            self.seconds.setRange(0.1, 600.0)
            self.seconds.setDecimals(1)
            self.seconds.setSingleStep(0.5)
            self.seconds.setValue(NOTE_DEFAULT_SECONDS)

            self.colour_button = QPushButton("选择颜色…")
            self.colour_button.clicked.connect(self.pick_colour)
            self.colour_swatch = QLabel("　")
            self.colour_swatch.setFixedWidth(28)
            self.default_button = QPushButton("默认颜色")
            self.default_button.clicked.connect(self.use_default_colour)

            form = QFormLayout()
            form.addRow("内容", self.text_edit)
            form.addRow("展示时长", self.seconds)
            row = QHBoxLayout()
            row.addWidget(self.colour_swatch)
            row.addWidget(self.colour_button)
            row.addWidget(self.default_button)
            row.addStretch(1)
            holder = QWidget()
            holder.setLayout(row)
            form.addRow("颜色", holder)

            self.preview = QLabel()
            self.preview.setWordWrap(True)
            self.preview.setMinimumHeight(38)
            self.preview.setMaximumHeight(110)
            self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)

            hint = QLabel(
                "补充内容显示在剧情主区域下方，不占用剧情时间；"
                "清屏也不会抹掉它——它按自己的时长消失。"
            )
            hint.setObjectName("hint")
            hint.setWordWrap(True)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

            layout = QVBoxLayout(self)
            layout.addLayout(form)
            layout.addWidget(QLabel("预览（播放时的样子）"))
            layout.addWidget(self.preview)
            layout.addWidget(hint)
            layout.addWidget(buttons)

            self.text_edit.textChanged.connect(self._refresh_preview)
            self.seconds.valueChanged.connect(self._refresh_preview)
            if event is not None:
                colour, seconds, text = note_parts(event)
                self.text_edit.setText(text)
                self.seconds.setValue(seconds)
                self._colour = colour
            self._paint_swatch()
            self._refresh_preview()

        def pick_colour(self) -> None:
            colour = QColorDialog.getColor(
                QColor(self._colour or "#ffd166"), self, "补充内容的颜色"
            )
            if colour.isValid():
                self._colour = colour.name()
                self._paint_swatch()
                self._refresh_preview()

        def use_default_colour(self) -> None:
            self._colour = ""
            self._paint_swatch()
            self._refresh_preview()

        def _paint_swatch(self) -> None:
            self.colour_swatch.setStyleSheet(
                "background:%s; border:1px solid #555;" % (self._colour or "#8a93a0")
            )

        def _refresh_preview(self) -> None:
            self.preview.setText(self.text_edit.text().strip() or "（补充内容）")
            self.preview.setStyleSheet(
                "background:#000; color:%s; padding:8px; border:1px solid #333;"
                % (self._colour or "#8a93a0")
            )

        def value(self):
            from tscp_player.format import make_note

            text = self.text_edit.text().strip()
            if not text:
                return None
            return make_note(text, color=self._colour, seconds=self.seconds.value())

        def accept(self) -> None:
            try:
                note = self.value()
            except Exception as exc:  # noqa: BLE001 - the format raises TSCPError
                QMessageBox.warning(self, "补充内容", str(exc))
                return
            if note is None:
                QMessageBox.warning(self, "补充内容", "内容不能为空")
                return
            super().accept()

    class ScriptNameDialog(QDialog):
        """Ask for a script name, showing the bare name and a fixed ``.tscp``.

        The extension is never editable: the dialog simply will not accept a name
        that would become a different file type.
        """

        def __init__(self, parent, title: str, name: str = "") -> None:
            super().__init__(parent)
            self.setWindowTitle(title)
            self.resize(430, 0)

            self.name_edit = QLineEdit(name)
            self.name_edit.selectAll()
            self.name_edit.returnPressed.connect(self.accept)
            suffix = QLabel(".tscp")
            suffix.setObjectName("hint")

            row = QHBoxLayout()
            row.addWidget(self.name_edit, 1)
            row.addWidget(suffix)

            self.hint = QLabel()
            self.hint.setObjectName("hint")
            self.hint.setWordWrap(True)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

            form = QFormLayout()
            form.addRow("剧本名", row)

            layout = QVBoxLayout(self)
            layout.addLayout(form)
            layout.addWidget(self.hint)
            layout.addStretch(1)
            layout.addWidget(buttons)

            self.name_edit.textChanged.connect(self._refresh_hint)
            self._refresh_hint()

        def _refresh_hint(self) -> None:
            try:
                base = studio.script_base_name(self.name_edit.text())
            except StudioError as exc:
                self.hint.setText(str(exc))
                return
            self.hint.setText("会保存为 %s.tscp" % base)

        def value(self) -> str:
            return studio.script_base_name(self.name_edit.text())

        def accept(self) -> None:
            try:
                self.value()
            except StudioError as exc:
                QMessageBox.warning(self, self.windowTitle(), str(exc))
                return
            super().accept()


    class SleepDialog(QDialog):
        """How long the player should hold before the next event."""

        def __init__(self, parent, seconds: str = "1.0") -> None:
            super().__init__(parent)
            self.setWindowTitle("暂停")
            self.spin = QDoubleSpinBox()
            self.spin.setRange(0.0, 600.0)
            self.spin.setDecimals(2)
            self.spin.setSingleStep(0.1)
            try:
                self.spin.setValue(float(seconds))
            except (TypeError, ValueError):
                self.spin.setValue(1.0)

            form = QFormLayout()
            form.addRow("暂停秒数", self.spin)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout = QVBoxLayout(self)
            layout.addLayout(form)
            layout.addWidget(buttons)

        def value(self) -> str:
            return "%g" % self.spin.value()

    class BatchCharactersDialog(QDialog):
        """Paste a whole cast at once.

        Both spellings are accepted: the JSON the character generator copies to
        the clipboard (``{"f": {"NAME": "FISH", "STYLE": "\\u001b[1;33m"}}``), and
        the tab-separated table.
        """

        def __init__(self, parent) -> None:
            super().__init__(parent)
            self.setWindowTitle("粘贴导入角色")
            self.resize(680, 480)

            hint = QLabel(
                "直接粘贴即可，两种格式都认：<br>"
                "• JSON —— 角色生成器复制出来的那种，"
                "<code>{ \"f\": { \"NAME\": \"FISH\", \"STYLE\": \"\\u001b[1;33m\" } }</code><br>"
                "• 表格 —— 每行 <b>缩写 &lt;TAB&gt; 全名 &lt;TAB&gt; ANSI 样式</b>"
                "（样式可省略），例如 <code>f&#9;FISH&#9;\\033[33m</code>"
            )
            hint.setWordWrap(True)
            hint.setObjectName("hint")

            self.editor = QPlainTextEdit()
            self.editor.setPlaceholderText(
                "{\n    \"f\": {\n        \"NAME\": \"FISH\",\n"
                "        \"STYLE\": \"\\u001b[1;33m\"\n    }\n}"
            )
            self.editor.setMinimumHeight(280)

            self.preview = QLabel()
            self.preview.setObjectName("hint")
            detect = QPushButton("识别")
            detect.clicked.connect(self._detect)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

            row = QHBoxLayout()
            row.addWidget(detect)
            row.addWidget(self.preview, 1)

            layout = QVBoxLayout(self)
            layout.addWidget(hint)
            layout.addWidget(self.editor, 1)
            layout.addLayout(row)
            layout.addWidget(buttons)

        def text(self) -> str:
            return self.editor.toPlainText()

        def _detect(self) -> None:
            """Show what the current text parses to, without committing it."""

            try:
                rows = studio.parse_characters(self.text())
            except StudioError as exc:
                self.preview.setText("读不出来：%s" % exc)
                return
            self.preview.setText(
                "识别到 %d 个角色：%s"
                % (len(rows), "、".join(key for key, _n, _s in rows[:8]))
                + ("…" if len(rows) > 8 else "")
            )

        def accept(self) -> None:
            try:
                rows = studio.parse_characters(self.text())
            except StudioError as exc:
                QMessageBox.warning(self, "角色", str(exc))
                return
            if not rows:
                QMessageBox.information(self, "角色", "没有识别到任何角色")
                return
            super().accept()

    class MusicDialog(QDialog):
        """Choose a track for a ``<p>`` event, or insert a brand new one.

        Tracks are listed by their audio file, never by the internal key.
        """

        def __init__(self, parent, studio_window, current: str = "") -> None:
            super().__init__(parent)
            self.setWindowTitle("播放音乐")
            self.studio = studio_window
            self.resize(540, 260)
            self.new_track: Optional[MusicDraft] = None

            self.combo = QComboBox()
            self.combo.addItem("（停止音乐）", "")
            for key, track in self.studio.project.tracks.items():
                self.combo.addItem("♪ %s" % studio.track_label(key, self.studio.project.tracks), key)
            index = self.combo.findData(current)
            if index >= 0:
                self.combo.setCurrentIndex(index)

            insert = QPushButton("插入新音乐…")
            insert.clicked.connect(self._insert_new)

            row = QHBoxLayout()
            row.addWidget(self.combo, 1)
            row.addWidget(insert)

            form = QFormLayout()
            form.addRow("曲目", row)

            hint = QLabel(
                "还没有的音乐可以先「插入新音乐」，它会直接存进剧情包；"
                "曲目在文件里的简称会自动生成。"
            )
            hint.setObjectName("hint")
            hint.setWordWrap(True)

            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)

            layout = QVBoxLayout(self)
            layout.addLayout(form)
            layout.addWidget(hint)
            layout.addStretch(1)
            layout.addWidget(buttons)

        def _insert_new(self) -> None:
            draft = collect_new_track(self, self.studio)
            if draft is None:
                return
            self.new_track = draft
            label = Path(draft.source).stem if draft.source else draft.abbreviation
            self.combo.addItem("♪ %s" % label, draft.abbreviation)
            self.combo.setCurrentIndex(self.combo.count() - 1)

        def value(self) -> str:
            return self.combo.currentData() or ""


    def collect_new_track(parent, studio_window) -> Optional[MusicDraft]:
        """Ask for audio, an abbreviation, its kind, lyrics and colour.

        Shared by the story step and the lyrics step so both offer the exact same
        questions the web app does.
        """

        filename, _ = QFileDialog.getOpenFileName(parent, "选择音乐文件", "", AUDIO_FILTER)
        if not filename:
            return None
        source = Path(filename)
        # The internal key is derived from the file, not asked for: nobody should
        # have to invent a short code just to drop a song into a scene.
        taken = set(studio_window.project.tracks)
        key = studio.suggest_key(source.stem, taken, prefix="m")

        answer = QMessageBox.question(
            parent,
            "音乐类型",
            "《%s》是纯音乐吗？\n\n是 —— 纯音乐\n否 —— 带歌词（接下来选歌词和颜色）"
            % source.name,
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return None

        draft = MusicDraft(abbreviation=key, source=source)
        if answer == QMessageBox.StandardButton.Yes:
            draft.kind = "instrumental"
            return draft

        draft.kind = "lyrics"
        choice = QMessageBox.question(
            parent,
            "歌词来源",
            "歌词从哪来？\n\n是 —— 选择现成的 .lrc 文件\n"
            "否 —— 选择 lyrics.txt，现在播放音乐并逐句录制出现时间",
            QMessageBox.StandardButton.Yes
            | QMessageBox.StandardButton.No
            | QMessageBox.StandardButton.Cancel,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return None

        if choice == QMessageBox.StandardButton.Yes:
            lrc, _ = QFileDialog.getOpenFileName(
                parent, "选择 LRC 歌词", "", "LRC 歌词 (*.lrc);;所有文件 (*)"
            )
            if not lrc:
                return None
            draft.lyrics_file = Path(lrc)
        else:
            text_file, _ = QFileDialog.getOpenFileName(
                parent, "选择歌词文本", "", "歌词文本 (*.txt);;所有文件 (*)"
            )
            if not text_file:
                return None
            try:
                lines = lyric_source_lines(Path(text_file).read_text(encoding="utf-8"))
            except (OSError, UnicodeError) as exc:
                QMessageBox.critical(parent, "音乐", "无法读取歌词：%s" % exc)
                return None
            if not lines:
                QMessageBox.warning(parent, "音乐", "这个文件里没有歌词行")
                return None
            recorded = LyricsRecorderDialog.record(parent, source, lines)
            if recorded is None:
                return None
            draft.lyrics_text = serialize_lrc(recorded)
            draft.lyrics_name = Path(text_file).with_suffix(".lrc").name

        colour = QColorDialog.getColor(
            QColor(LYRIC_COLOR_DEFAULT), parent, "选择歌词显示颜色"
        )
        if colour.isValid():
            draft.color = colour.name()
        return draft

    # ----------------------------------------------------------------------
    # steps
    # ----------------------------------------------------------------------

    class Step(QWidget):
        """Base class: a page that refreshes from the open project."""

        title = ""

        def __init__(self, studio_window) -> None:
            super().__init__()
            self.studio = studio_window
            self._build()

        def _build(self) -> None:  # pragma: no cover - overridden
            pass

        @property
        def project(self) -> Optional[StudioProject]:
            return self.studio.project

        def refresh(self) -> None:  # pragma: no cover - overridden
            pass

    class CharactersStep(Step):
        title = "① 角色"

        def _build(self) -> None:
            self.table = QTableWidget(0, 3)
            self.table.setHorizontalHeaderLabels(["名字", "样式", "标识"])
            compact_table(self.table)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemDoubleClicked.connect(lambda _item: self.edit_character())

            self.hint = QLabel(
                "名字就是播放时显示的样子，颜色决定它的样式。"
                "「标识」只是文件内部用的记号，会自动从名字生成，平时不用管。"
            )
            self.hint.setObjectName("hint")

            self.buttons: Dict[str, QPushButton] = {}
            buttons = QHBoxLayout()
            for label, slot, tip in (
                ("添加角色", self.add_character, "新增一个角色"),
                ("编辑", self.edit_character, "修改选中的角色"),
                ("删除", self.remove_character, "删除选中的角色"),
                ("批量添加…", self.batch_add,
                 "一个对话框里连续添加多个角色，最后一次性提交"),
                ("粘贴导入…", self.paste_add,
                 "粘贴多行：缩写<TAB>全名<TAB>样式"),
                ("从 JSON 导入…", self.import_json,
                 "从 Scripts/__init__.json 或角色 JSON 导入"),
                ("从剧情包导入…", self.import_from_plot,
                 "从已有 .tscpkg／.tscpkgs／剧情文件夹导入角色"),
                ("导出角色…", self.export_characters,
                 "把当前角色存成 .tscpc 文件，下次可直接导入"),
            ):
                button = QPushButton(label)
                button.setToolTip(tip)
                button.clicked.connect(slot)
                self.buttons[label] = button
                buttons.addWidget(button)
            buttons.addStretch(1)

            layout = QVBoxLayout(self)
            layout.addWidget(self.hint)
            layout.addWidget(self.table, 1)
            layout.addLayout(buttons)

        def refresh(self) -> None:
            self.table.setRowCount(0 if self.project is None else len(self.project.characters))
            if self.project is None:
                return
            for row, (key, name, style) in enumerate(self.project.character_rows()):
                item = QTableWidgetItem(name)
                # The key rides along invisibly; the column shows the name.
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.table.setItem(row, 0, item)

                style_item = QTableWidgetItem(self._style_text(style))
                css = _style_css(style)
                if css:
                    colour = re.search(r"color:(#[0-9a-fA-F]{6})", css)
                    if colour:
                        style_item.setForeground(QColor(colour.group(1)))
                self.table.setItem(row, 1, style_item)

                key_item = QTableWidgetItem(key)
                key_item.setForeground(QColor("#888888"))
                self.table.setItem(row, 2, key_item)
            self.table.resizeColumnsToContents()

        @staticmethod
        def _style_text(style: str) -> str:
            if not style:
                return "（默认）"
            match = re.search(r"\[([0-9;]*)m", str(style).replace("\x1b", r"\033"))
            return r"\033[%sm" % match.group(1) if match else str(style)

        def _selected_key(self) -> Optional[str]:
            rows = self.table.selectionModel().selectedRows()
            if not rows:
                return None
            item = self.table.item(rows[0].row(), 0)
            return item.data(Qt.ItemDataRole.UserRole) if item else None

        def add_character(self) -> None:
            if not self.studio.require_project():
                return
            dialog = CharacterDialog(self, taken=list(self.project.characters))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            key, name, style = dialog.values()
            self.studio.run(lambda: self.project.set_character(key, name, style), "已添加角色 %s" % name)

        def edit_character(self) -> None:
            if not self.studio.require_project():
                return
            key = self._selected_key()
            if key is None:
                QMessageBox.information(self, "角色", "请先选择一个角色")
                return
            current = self.project.characters[key]
            dialog = CharacterDialog(
                self, key, current.name, current.style, taken=list(self.project.characters)
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            new_key, name, style = dialog.values()
            def apply():
                if new_key != key:
                    self.project.remove_character(key)
                    self._rename_in_scripts(key, new_key)
                self.project.set_character(new_key, name, style)
            self.studio.run(apply, "已更新角色 %s" % name)

        def _rename_in_scripts(self, old: str, new: str) -> None:
            for filename, script in self.project.scripts.items():
                lines = []
                changed = False
                for item in script.lines:
                    if isinstance(item, Dialogue) and item.character == old:
                        lines.append(Dialogue(new, item.text, list(item.delays)))
                        changed = True
                    else:
                        lines.append(item)
                if changed:
                    self.project.set_script(filename, Script(lines))

        def remove_character(self) -> None:
            if not self.studio.require_project():
                return
            key = self._selected_key()
            if key is None:
                QMessageBox.information(self, "角色", "请先选择一个角色")
                return
            used = [
                filename
                for filename, script in self.project.scripts.items()
                if key in studio.script_characters(script)
            ]
            if used:
                QMessageBox.warning(
                    self, "角色", "还有剧本在用这个角色：%s" % "、".join(used)
                )
                return
            self.studio.run(lambda: self.project.remove_character(key), "已删除角色 %s" % key)

        # -- importing -------------------------------------------------
        def _merge(self, rows, source: str) -> None:
            before = set(self.project.characters)
            keys = self.studio.run(lambda: self.project.merge_characters(rows))
            if keys is None:
                return
            added = len([key for key in keys if key not in before])
            self.studio.status.setText(
                "%s：新增 %d 个、更新 %d 个角色" % (source, added, len(keys) - added)
            )

        def batch_add(self) -> None:
            """Add a whole cast without reopening a dialog for each name."""

            if not self.studio.require_project():
                return
            dialog = BatchAddDialog(self, self.project.characters)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self._merge(dialog.rows, "批量添加")

        def paste_add(self) -> None:
            if not self.studio.require_project():
                return
            dialog = BatchCharactersDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            try:
                rows = studio.parse_characters(dialog.text())
            except StudioError as exc:
                QMessageBox.warning(self, "角色", str(exc))
                return
            self._merge(rows, "粘贴导入")

        def import_json(self) -> None:
            if not self.studio.require_project():
                return
            filename, _ = QFileDialog.getOpenFileName(
                self, "导入角色 JSON", "",
                "角色配置 (*.json);;所有文件 (*)",
            )
            if not filename:
                return
            try:
                rows = studio.parse_character_json(
                    Path(filename).read_text(encoding="utf-8")
                )
            except (StudioError, OSError, UnicodeError) as exc:
                QMessageBox.warning(self, "角色", str(exc))
                return
            self._merge(rows, Path(filename).name)

        def import_from_plot(self) -> None:
            if not self.studio.require_project():
                return
            source = self.studio.pick_import_source()
            if source is None:
                return
            self._merge(
                studio.collect_source(source)["characters"], Path(source).name
            )

        def export_characters(self) -> None:
            """Save the cast as a ``.tscpc`` so it can be reused next time."""

            if not self.studio.require_project():
                return
            if not self.project.characters:
                QMessageBox.information(self, "角色", "还没有角色可以导出")
                return
            suggestion = self.project.path.with_suffix(studio.CHARACTERS_SUFFIX)
            filename, _ = QFileDialog.getSaveFileName(
                self,
                "导出角色",
                str(suggestion),
                "TSCP 角色文件 (*.tscpc);;JSON (*.json)",
            )
            if not filename:
                return
            try:
                target = studio.write_characters(
                    Path(filename), self.project.character_rows()
                )
            except StudioError as exc:
                QMessageBox.warning(self, "角色", str(exc))
                return
            self.studio.status.setText(
                "已导出 %d 个角色到 %s（下次用「从 JSON 导入…」或直接粘贴即可取回）"
                % (len(self.project.characters), target.name)
            )

    class StoryStep(Step):
        title = "② 剧情"

        def _build(self) -> None:
            self.script_combo = QComboBox()
            self.script_combo.currentIndexChanged.connect(lambda _i: self._reload_events())

            new_script = QPushButton("新建剧本")
            new_script.clicked.connect(self.new_script)
            rename_script = QPushButton("重命名")
            rename_script.clicked.connect(self.rename_script)
            delete_script = QPushButton("删除剧本")
            delete_script.clicked.connect(self.delete_script)
            import_script = QPushButton("导入剧本…")
            import_script.setToolTip("从已有的 .tscp（含时间）或 .tscps（原稿）导入")
            import_script.clicked.connect(self.import_script)

            top = QHBoxLayout()
            top.addWidget(QLabel("剧本"))
            top.addWidget(self.script_combo, 1)
            top.addWidget(new_script)
            top.addWidget(rename_script)
            top.addWidget(delete_script)
            top.addWidget(import_script)

            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels(["序号", "类型", "角色", "内容"])
            compact_table(self.table)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemDoubleClicked.connect(lambda _item: self.edit_event())
            self.table.itemSelectionChanged.connect(self._update_buttons)

            # label, slot, shortcut, tooltip
            self.actions = [
                ("添加对白", self.add_dialogue, "Ctrl+D", "新建一句角色对白"),
                ("添加旁白", self.add_narration, "Ctrl+A", "新建一句旁白"),
                ("补充内容", self.add_note, "Ctrl+B", "在剧情下方加一行带颜色、有时长的补充"),
                ("添加暂停", self.add_sleep, "Ctrl+T", "插入一段等待时间"),
                ("清空屏幕", self.add_clear, "Ctrl+E", "插入清屏指令"),
                ("插入音乐", self.add_music, "Ctrl+M", "选择已有曲目或插入新音乐"),
                ("停止音乐", self.add_stop_music, "Ctrl+P", "停止当前正在播放的音乐"),
                ("编辑", self.edit_event, "", "编辑选中的一行"),
                ("上移", lambda: self.move(-1), "", "把选中的一行往上挪"),
                ("下移", lambda: self.move(1), "", "把选中的一行往下挪"),
                ("删除", self.delete_event, "", "删除选中的一行"),
            ]
            self.action_buttons: Dict[str, QPushButton] = {}
            self.shortcuts: List[tuple] = []
            buttons = QHBoxLayout()
            for label, slot, key, tip in self.actions:
                button = QPushButton(label)
                button.setToolTip("%s（%s）" % (tip, key) if key else tip)
                button.clicked.connect(lambda _checked=False, s=slot: s())
                self.action_buttons[label] = button
                buttons.addWidget(button)
                if key:
                    shortcut = QShortcut(QKeySequence(key), self)
                    # Scoped to this page: the keys must not fire while another
                    # step, a dialog, or the player has focus.
                    shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                    shortcut.activated.connect(lambda s=slot: s())
                    self.shortcuts.append((key, label, shortcut))
            buttons.addStretch(1)

            self.hint = QLabel(
                "双击一行即可编辑；所有内容都用对话框填写，不用手写脚本格式。"
                "　快捷键：Ctrl+A 旁白、Ctrl+D 对白、Ctrl+B 补充、Ctrl+M 音乐、"
                "Ctrl+P 停止音乐、Ctrl+E 清屏、Ctrl+T 暂停。"
            )
            self.hint.setObjectName("hint")

            layout = QVBoxLayout(self)
            layout.addLayout(top)
            layout.addWidget(self.hint)
            layout.addWidget(self.table, 1)
            layout.addLayout(buttons)

        # -- script selection ------------------------------------------
        def refresh(self) -> None:
            names = [] if self.project is None else list(self.project.scripts)
            current = self.script_combo.currentText()
            self.script_combo.blockSignals(True)
            self.script_combo.clear()
            self.script_combo.addItems(names)
            if current in names:
                self.script_combo.setCurrentText(current)
            self.script_combo.blockSignals(False)
            self._reload_events()

        def current_script_name(self) -> Optional[str]:
            return self.script_combo.currentText() or None

        def _reload_events(self) -> None:
            name = self.current_script_name()
            if self.project is None or not name:
                self.table.setRowCount(0)
                self._update_buttons()
                return
            script = self.project.script(name)
            characters = self.project.characters
            tracks = self.project.tracks
            self.table.setRowCount(len(script.lines))
            for row, event in enumerate(script.lines):
                values = [
                    str(row + 1),
                    event_label(event),
                    studio.event_character(event, characters),
                    studio.describe_event(event, characters, tracks),
                ]
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.resizeColumnsToContents()
            self._update_buttons()

        def _selected_row(self) -> Optional[int]:
            rows = self.table.selectionModel().selectedRows()
            return rows[0].row() if rows else None

        def _update_buttons(self) -> None:
            has_script = self.project is not None and self.current_script_name() is not None
            has_row = has_script and self._selected_row() is not None
            for label in ("编辑", "上移", "下移", "删除"):
                self.action_buttons[label].setEnabled(bool(has_row))
            for label in (
                "添加对白", "添加旁白", "补充内容", "添加暂停",
                "清空屏幕", "插入音乐", "停止音乐",
            ):
                self.action_buttons[label].setEnabled(bool(has_script))

        def _selected_event(self):
            name = self.current_script_name()
            row = self._selected_row()
            if name is None or row is None:
                return None, None
            return name, self.project.script(name).lines[row]

        # -- script actions --------------------------------------------
        def new_script(self) -> None:
            if not self.studio.require_project():
                return
            suggestion = studio.script_base_name(
                next_script_name(self.project.scripts)
            )
            dialog = ScriptNameDialog(self, "新建剧本", suggestion)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            created = self.studio.run(
                lambda: self.project.new_script(dialog.value()), "已新建 %s.tscp" % dialog.value()
            )
            if created:
                self.refresh()
                self.script_combo.setCurrentText(created)

        def rename_script(self) -> None:
            if not self.studio.require_project():
                return
            name = self.current_script_name()
            if name is None:
                return
            dialog = ScriptNameDialog(self, "重命名剧本", studio.script_base_name(name))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self.studio.run(
                lambda: self.project.rename_script(name, dialog.value()), "已重命名"
            )
            self.refresh()

        def delete_script(self) -> None:
            if not self.studio.require_project():
                return
            name = self.current_script_name()
            if name is None:
                return
            if QMessageBox.question(self, "删除剧本", "删除 %s？" % name) != QMessageBox.StandardButton.Yes:
                return
            self.studio.run(lambda: self.project.delete_script(name), "已删除 %s" % name)
            self.refresh()

        def import_script(self) -> None:
            if not self.studio.require_project():
                return
            filename, _ = QFileDialog.getOpenFileName(
                self, "导入剧本", "",
                "TSCP 剧本 (*.tscp *.tscps);;编译稿 (*.tscp);;原稿 (*.tscps);;所有文件 (*)",
            )
            if not filename:
                return
            created = self.studio.run(
                lambda: self.project.import_script(Path(filename)),
                "已导入 %s" % Path(filename).name,
            )
            if created:
                self.refresh()
                self.script_combo.setCurrentText(created)

        # -- event actions ---------------------------------------------
        def _add(self, event) -> None:
            if not self.studio.require_project():
                return
            name = self.current_script_name()
            if name is None:
                QMessageBox.information(self, "剧情", "请先新建或选择一个剧本")
                return
            row = self._selected_row()
            index = None if row is None else row + 1
            self.studio.run(lambda: self.project.add_event(name, event, index))
            self._reload_events()

        def add_dialogue(self) -> None:
            if not self.studio.require_project():
                return
            if not self.project.characters:
                QMessageBox.information(self, "剧情", "先到「① 角色」添加至少一个角色")
                return
            dialog = DialogueDialog(self, self.studio, None, narrator=False)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._add(dialog.values())

        def add_narration(self) -> None:
            dialog = DialogueDialog(self, self.studio, None, narrator=True)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._add(dialog.values())

        def add_sleep(self) -> None:
            dialog = SleepDialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._add(Directive("s", dialog.value()))

        def add_clear(self) -> None:
            self._add(Directive("c"))

        def add_note(self) -> None:
            """Add an aside shown below the story, with its own time and colour."""

            if not self.studio.require_project():
                return
            dialog = NoteDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self._add(dialog.value())

        def add_stop_music(self) -> None:
            """The format's stop-the-music directive, as a first-class action.

            An empty ``P|`` value is what ``STOP_WORDS`` reads as "stop", so this
            is the same event the music dialog's first entry produces — it just
            does not make you go through that dialog to get it.
            """

            self._add(Directive("p", ""))

        def add_music(self) -> None:
            if not self.studio.require_project():
                return
            dialog = MusicDialog(self, self.studio)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if dialog.new_track is not None:
                label = Path(dialog.new_track.source).stem if dialog.new_track.source \
                    else dialog.new_track.abbreviation
                self.studio.run(
                    lambda: self.project.add_music([dialog.new_track]),
                    "已插入音乐 %s" % label,
                )
            self._add(Directive("p", dialog.value()))

        def edit_event(self) -> None:
            if not self.studio.require_project():
                return
            name, event = self._selected_event()
            if name is None or event is None:
                QMessageBox.information(self, "剧情", "请先选择一行")
                return
            row = self._selected_row()
            kind = event_kind(event)
            if kind in {DIALOGUE, NARRATION}:
                dialog = DialogueDialog(self, self.studio, event, narrator=kind == NARRATION)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                replacement = dialog.values()
            elif kind == SLEEP:
                dialog = SleepDialog(self, event.value)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                replacement = Directive("s", dialog.value())
            elif kind == studio.NOTE:
                dialog = NoteDialog(self, event)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                replacement = dialog.value()
            elif kind == MUSIC:
                dialog = MusicDialog(self, self.studio, event.value)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                if dialog.new_track is not None:
                    label = Path(dialog.new_track.source).stem if dialog.new_track.source \
                        else dialog.new_track.abbreviation
                    self.studio.run(
                        lambda: self.project.add_music([dialog.new_track]),
                        "已插入音乐 %s" % label,
                    )
                replacement = Directive("p", dialog.value())
            else:
                QMessageBox.information(self, "剧情", "清空屏幕没有可编辑的参数")
                return
            self.studio.run(lambda: self.project.replace_event(name, row, replacement))
            self._reload_events()

        def move(self, delta: int) -> None:
            if not self.studio.require_project():
                return
            name = self.current_script_name()
            row = self._selected_row()
            if name is None or row is None:
                return
            target = self.studio.run(lambda: self.project.move_event(name, row, delta))
            self._reload_events()
            if isinstance(target, int):
                self.table.selectRow(target)

        def delete_event(self) -> None:
            if not self.studio.require_project():
                return
            name = self.current_script_name()
            row = self._selected_row()
            if name is None or row is None:
                return
            self.studio.run(lambda: self.project.delete_event(name, row))
            self._reload_events()

    class TimingStep(Step):
        title = "③ 计时"

        def _build(self) -> None:
            self.script_combo = QComboBox()
            self.script_combo.currentIndexChanged.connect(lambda _i: self._reload_events())

            # How the timing gets edited: by pressing keys, by dragging blocks on
            # a timeline, or by typing durations. All three write the same delays.
            self.view_combo = QComboBox()
            self.view_combo.addItem("逐字录制（按计时键）", VIEW_RECORD)
            self.view_combo.addItem("时序图（拖动改语速）", VIEW_TIMELINE)
            self.view_combo.addItem("直接改时间（输入数值）", VIEW_NUMBERS)
            self.view_combo.currentIndexChanged.connect(self._view_changed)

            top = QHBoxLayout()
            top.addWidget(QLabel("剧本"))
            top.addWidget(self.script_combo, 1)
            top.addWidget(QLabel("方式"))
            top.addWidget(self.view_combo)

            # Built before the pages: they wire their status lines into it.
            self.status = QLabel(
                "选择一个剧本后点「开始 / 重新计时」；第一次按键才是计时起点。"
            )
            self.status.setObjectName("hint")

            self.pages = QStackedWidget()
            self.pages.addWidget(self._build_record_page())
            self.pages.addWidget(self._build_timeline_page())
            self.pages.addWidget(self._build_numbers_page())

            layout = QVBoxLayout(self)
            layout.addLayout(top)
            layout.addWidget(self.pages, 1)
            layout.addWidget(self.status)

            self.model: Optional[KeyboardTimingModel] = None
            self._wait_until = 0.0
            self._music: Optional[MusicPlayer] = None
            self._music_broken = False
            self._loading_numbers = False

        # -- the three pages -------------------------------------------
        def _build_record_page(self) -> QWidget:
            self.mode = QComboBox()
            self.mode.addItem("逐句模式（句间停顿不计入）", TimingMode.PER_SENTENCE)
            self.mode.addItem("连续模式（句间停顿计入下一句）", TimingMode.CONTINUOUS)

            self.key_edit = QLineEdit("Enter")
            self.key_edit.setMaximumWidth(110)

            self.start_button = QPushButton("开始 / 重新计时")
            self.start_button.clicked.connect(self.start_timing)
            self.selected_button = QPushButton("只录选中句")
            self.selected_button.clicked.connect(self.start_selected)
            self.import_button = QPushButton("导入时间…")
            self.import_button.setToolTip(
                "从已经录好的 .tscp 里把逐字时间搬过来；逐行按位置和文字匹配"
            )
            self.import_button.clicked.connect(self.import_timing)

            controls = QHBoxLayout()
            controls.addWidget(self.mode)
            controls.addWidget(QLabel("计时键"))
            controls.addWidget(self.key_edit)
            controls.addWidget(self.start_button)
            controls.addWidget(self.selected_button)
            controls.addWidget(self.import_button)
            controls.addStretch(1)

            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels(["序号", "类型", "角色", "进度"])
            compact_table(self.table)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)

            self.panel = QTextEdit()
            self.panel.setReadOnly(True)
            self.panel.setStyleSheet(
                "QTextEdit { background:#111; color:#eee; padding:14px; font-size:17px; }"
            )
            self.panel.setPlaceholderText("按计时键时这一句会逐字出现，当前字会被高亮")

            splitter = QSplitter(Qt.Orientation.Horizontal)
            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.addWidget(self.table)
            splitter.addWidget(left)
            splitter.addWidget(self.panel)
            splitter.setSizes([520, 560])

            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 6, 0, 0)
            layout.addLayout(controls)
            layout.addWidget(splitter, 1)
            return page

        def _build_timeline_page(self) -> QWidget:
            self.timeline = TimelineView()
            self.timeline.blockSelected.connect(self._timeline_selected)
            self.timeline.durationChanged.connect(self._timeline_dragged)
            self.timeline.statusMessage.connect(self.status.setText)

            self.zoom = QSlider(Qt.Orientation.Horizontal)
            self.zoom.setMinimum(int(MIN_ZOOM))
            self.zoom.setMaximum(int(MAX_ZOOM))
            self.zoom.setValue(int(DEFAULT_ZOOM))
            self.zoom.setMaximumWidth(240)
            self.zoom.valueChanged.connect(self._zoom_changed)

            fit = QPushButton("适应宽度")
            fit.clicked.connect(self._fit_timeline)
            reset = QPushButton("重置缩放")
            reset.clicked.connect(lambda: self.zoom.setValue(int(DEFAULT_ZOOM)))

            controls = QHBoxLayout()
            controls.addWidget(QLabel("缩放"))
            controls.addWidget(self.zoom)
            controls.addWidget(fit)
            controls.addWidget(reset)
            controls.addStretch(1)

            self.timeline_hint = QLabel(
                "拖动对话块的**右边缘**改变这一句的时长，后面的内容会顺延；"
                "滚轮缩放，中键拖动平移。音乐轨上的虚线是歌词的每一句。"
            )
            self.timeline_hint.setObjectName("hint")
            self.timeline_hint.setWordWrap(True)

            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 6, 0, 0)
            layout.addLayout(controls)
            # The lanes are a fixed height, so the widget should not stretch into
            # a big empty rectangle; the slack belongs below it.
            layout.addWidget(self.timeline)
            layout.addStretch(1)
            layout.addWidget(self.timeline_hint)
            return page

        def _build_numbers_page(self) -> QWidget:
            self.numbers = QTableWidget(0, 5)
            self.numbers.setHorizontalHeaderLabels(
                ["序号", "类型", "角色", "内容", "时长（秒）"]
            )
            compact_table(self.numbers)
            self.numbers.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.numbers.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.numbers.horizontalHeader().setStretchLastSection(False)
            self.numbers.itemChanged.connect(self._number_edited)

            self.numbers_hint = QLabel(
                "直接改「时长」那一列，回车生效；改一句话不会影响别的时间。"
            )
            self.numbers_hint.setObjectName("hint")
            self.numbers_hint.setWordWrap(True)

            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 6, 0, 0)
            layout.addWidget(self.numbers, 1)
            layout.addWidget(self.numbers_hint)
            return page

        # -- mode switching --------------------------------------------
        def current_view(self) -> str:
            return self.view_combo.currentData() or VIEW_RECORD

        def _view_changed(self) -> None:
            index = max(0, self.view_combo.currentIndex())
            self.pages.setCurrentIndex(index)
            if self.current_view() == VIEW_RECORD:
                self.status.setText(
                    "按「开始 / 重新计时」，然后按计时键；第一次按键才是计时起点。"
                )
            elif self.current_view() == VIEW_TIMELINE:
                self._load_timeline()
                self.status.setText("拖动对话块右边缘即可改语速。")
            else:
                self._reload_numbers()
                self.status.setText("双击「时长」单元格直接输入秒数。")

        def _zoom_changed(self, value: int) -> None:
            self.timeline.set_scale(float(value))

        def _fit_timeline(self) -> None:
            self.timeline.fit()
            self.zoom.blockSignals(True)
            self.zoom.setValue(int(max(MIN_ZOOM, min(MAX_ZOOM, self.timeline.scale))))
            self.zoom.blockSignals(False)

        def _timeline_selected(self, index: int) -> None:
            self.table.selectRow(index)
            self.numbers.selectRow(index)
            self.status.setText("已选中第 %d 行，拖动它的右边缘改时长。" % (index + 1))

        def _timeline_dragged(self, index: int, seconds: float) -> None:
            before = self._duration_of(index)
            self.set_duration(index, seconds)
            after = self._duration_of(index)
            self.status.setText(
                "第 %d 行：%.2f 秒 → %.2f 秒（后面的内容顺延）"
                % (index + 1, before, after)
            )

        # -- shared editing --------------------------------------------
        def _duration_of(self, index: int) -> float:
            name = self.current_script_name()
            if self.project is None or name is None:
                return 0.0
            lines = self.project.script(name).lines
            if not 0 <= index < len(lines):
                return 0.0
            item = lines[index]
            if not isinstance(item, Dialogue):
                return 0.0
            return sum(item.delays[: visible_text_length(item.text)])

        def set_duration(self, index: int, seconds: float) -> bool:
            """Give one dialogue a new total duration, scaling its delays.

            This is the single place the timeline and the numeric editor both
            write through, so the two views can never disagree.
            """

            name = self.current_script_name()
            if self.project is None or name is None:
                return False
            script = self.project.script(name)
            if not 0 <= index < len(script.lines):
                return False
            item = script.lines[index]
            if not isinstance(item, Dialogue):
                return False
            count = visible_text_length(item.text)
            if count <= 0:
                return False

            target = max(0.0, float(seconds))
            current = sum(item.delays[:count]) if item.delays else 0.0
            if current > 0.01:
                factor = target / current
                delays = [max(0.0, float(value)) * factor for value in item.delays[:count]]
            else:
                # Never timed: spread the new duration evenly.
                delays = [target / count] * count

            self.project.replace_event(
                name, index, Dialogue(item.character, item.text, delays)
            )
            self.studio.mark_dirty()
            self._reload_events()
            return True

        def _load_timeline(self) -> None:
            name = self.current_script_name()
            if self.project is None or name is None:
                self.timeline.set_script(None)
                return
            lyrics = {}
            for key, track in self.project.tracks.items():
                if not track.has_lyrics:
                    continue
                text = self.project.lyrics_text(key)
                if not text.strip():
                    continue
                try:
                    parsed = parse_lrc(text)
                except Exception:  # noqa: BLE001 - a broken .lrc must not break the view
                    continue
                if parsed.lines:
                    lyrics[key] = parsed.lines
            self.timeline.set_script(
                self.project.script(name),
                self.project.characters,
                self.project.tracks,
                lyrics,
            )
            self.timeline.set_scale(float(self.zoom.value()))

        def _reload_numbers(self) -> None:
            name = self.current_script_name()
            self._loading_numbers = True
            try:
                if self.project is None or name is None:
                    self.numbers.setRowCount(0)
                    return
                script = self.project.script(name)
                characters = self.project.characters
                tracks = self.project.tracks
                self.numbers.setRowCount(len(script.lines))
                for row, event in enumerate(script.lines):
                    values = [
                        str(row + 1),
                        event_label(event),
                        studio.event_character(event, characters),
                        studio.describe_event(event, characters, tracks),
                    ]
                    for column, value in enumerate(values):
                        item = QTableWidgetItem(value)
                        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        self.numbers.setItem(row, column, item)

                    cell = QTableWidgetItem()
                    if isinstance(event, Dialogue):
                        cell.setText("%.2f" % self._duration_of(row))
                        cell.setToolTip("总时长（秒）")
                    else:
                        cell.setText("—")
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self.numbers.setItem(row, 4, cell)
                self.numbers.resizeColumnsToContents()
            finally:
                self._loading_numbers = False

        def _number_edited(self, cell) -> None:
            if self._loading_numbers or cell.column() != 4:
                return
            try:
                seconds = float(cell.text())
            except ValueError:
                self.status.setText("时长要填一个数字")
                self._reload_numbers()
                return
            if seconds < 0:
                self.status.setText("时长不能是负数")
                self._reload_numbers()
                return
            if not self.set_duration(cell.row(), seconds):
                self._reload_numbers()
                return
            self.status.setText("第 %d 行改成 %.2f 秒" % (cell.row() + 1, seconds))
            self._reload_numbers()
            self._load_timeline()

        # -- helpers ---------------------------------------------------
        def current_script_name(self) -> Optional[str]:
            return self.script_combo.currentText() or None

        def refresh(self) -> None:
            names = [] if self.project is None else list(self.project.scripts)
            current = self.script_combo.currentText()
            self.script_combo.blockSignals(True)
            self.script_combo.clear()
            self.script_combo.addItems(names)
            if current in names:
                self.script_combo.setCurrentText(current)
            self.script_combo.blockSignals(False)
            self.model = None
            self._reload_events()
            self._load_timeline()
            self._reload_numbers()

        def _reload_events(self) -> None:
            name = self.current_script_name()
            if self.project is None or not name:
                self.table.setRowCount(0)
                self._load_timeline()
                self._reload_numbers()
                return
            script = self.project.script(name)
            characters = self.project.characters
            self.table.setRowCount(len(script.lines))
            for row, event in enumerate(script.lines):
                if isinstance(event, Dialogue):
                    needed = studio.timed_slots(event)
                    done = needed if len(event.delays) == visible_text_length(event.text) else 0
                    values = [
                        str(row + 1), event_label(event),
                        studio.character_label(event.character, characters) or "旁白",
                        "%d/%d" % (done, needed),
                    ]
                else:
                    values = [str(row + 1), event_label(event), "", "—"]
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.resizeColumnsToContents()
            self._load_timeline()
            self._reload_numbers()

        def _selected_row(self) -> Optional[int]:
            rows = self.table.selectionModel().selectedRows()
            return rows[0].row() if rows else None

        def _configured_keys(self):
            value = self.key_edit.text().strip()
            if not value:
                return {"Enter", "Return", "\r", "\n", "Space", " "}
            lowered = value.lower()
            if lowered in {"enter", "return"}:
                return {"Enter", "Return", "\r", "\n"}
            if lowered in {"space", "空格"}:
                return {"Space", " "}
            return {value, value.lower()}

        def _player(self) -> Optional[MusicPlayer]:
            if self._music is None and not self._music_broken:
                try:
                    self._music = MusicPlayer()
                except RuntimeError as exc:
                    self._music_broken = True
                    self.status.setText("音乐不可用：%s" % exc)
            return self._music

        def _apply_music(self, value: str) -> str:
            key = value.strip()
            player = self._player()
            if key.lower() in STOP_WORDS:
                if player is not None:
                    player.stop()
                return "音乐已停止"
            if player is None:
                return "音乐不可用"
            try:
                path = self.project.audio_path(key)
                reloaded = player.ensure(str(path))
            except (OSError, RuntimeError, StudioError) as exc:
                return "无法播放 %s：%s" % (key, exc)
            return "播放 %s（从头）" % key if reloaded else "续播 %s" % key

        def _begin_wait(self, value: str) -> str:
            try:
                seconds = max(0.0, float(str(value).strip()))
            except ValueError:
                seconds = 0.0
            self._wait_until = time.monotonic() + seconds
            delay = int(seconds * 1000)
            if delay > 0:
                QTimer.singleShot(delay, self._wait_finished)
            return "暂停 %.2f 秒（暂停期间按键不计时）" % seconds

        def _wait_finished(self) -> None:
            self._wait_until = 0.0

        # -- recording -------------------------------------------------
        def _start(self, targets, message: str) -> None:
            if not self.studio.require_project():
                return
            name = self.current_script_name()
            if name is None:
                QMessageBox.information(self, "计时", "请先新建或选择一个剧本")
                return
            script = self.project.script(name)
            last: List[object] = []

            def reached(index, event):
                if isinstance(event, Directive):
                    # A supplement is a control line, but it does not clear the
                    # screen; saying so would be actively misleading.
                    if is_note(event):
                        _colour, seconds, text = note_parts(event)
                        note = "补充内容，显示 %g 秒：%s" % (seconds, text)
                    elif event.command == "p":
                        note = self._apply_music(event.value)
                    elif event.command == "s":
                        note = self._begin_wait(event.value)
                    else:
                        note = "已清空屏幕"
                    self.status.setText("事件 %d：%s" % (index + 1, note))
                else:
                    last.append(event)
                    self._show_dialogue(event)
                self._reload_events()

            self.model = KeyboardTimingModel(
                script,
                self.mode.currentData(),
                accepted_keys=self._configured_keys(),
                event_callback=reached,
                targets=targets,
            )
            self.model.arm()
            self._reload_events()
            self.status.setText(message)

        def start_timing(self) -> None:
            self._start(
                None,
                "全程计时：按 %s 记录当前字；第一次按键即计时起点" % self.key_edit.text(),
            )

        def start_selected(self) -> None:
            row = self._selected_row()
            if row is None:
                QMessageBox.information(self, "计时", "请先在右边选择一行动白")
                return
            name = self.current_script_name()
            if name is None:
                return
            if not isinstance(self.project.script(name).lines[row], Dialogue):
                QMessageBox.information(self, "计时", "只能重新录制对白或旁白")
                return
            self._start([row], "单句录制第 %d 行：其余句保留已有时间" % (row + 1))

        def import_timing(self) -> None:
            """Copy timings out of an already recorded ``.tscp``."""

            if not self.studio.require_project():
                return
            name = self.current_script_name()
            if name is None:
                QMessageBox.information(self, "计时", "请先新建或选择一个剧本")
                return
            filename, _ = QFileDialog.getOpenFileName(
                self, "导入时间", "", "TSCP 编译稿 (*.tscp);;所有文件 (*)"
            )
            if not filename:
                return
            try:
                text = Path(filename).read_text(encoding="utf-8")
                incoming = studio.parse_script_text(text, ".tscp")
            except (StudioError, OSError, UnicodeError) as exc:
                QMessageBox.warning(self, "计时", str(exc))
                return
            outcome = self.studio.run(lambda: self.project.apply_timing(name, incoming))
            if outcome is None:
                return
            applied, skipped = outcome
            self._reload_events()
            self.status.setText(
                "%s：套用了 %d 句的时间%s"
                % (
                    Path(filename).name,
                    applied,
                    "" if not skipped else "；%d 句因为文字不同被跳过" % skipped,
                )
            )

        def _show_dialogue(self, event: Dialogue) -> None:
            """Show the line at the moment recording reaches it."""

            self._render_progress(event, None)

        def _dialogue_at(self, index: Optional[int]) -> Optional[Dialogue]:
            """The dialogue at a script line index, when there is one."""

            name = self.current_script_name()
            if self.project is None or name is None or index is None:
                return None
            lines = self.project.script(name).lines
            if not 0 <= index < len(lines):
                return None
            event = lines[index]
            return event if isinstance(event, Dialogue) else None

        def handle_key(self, key_name: str) -> bool:
            if self.model is None:
                return False
            if time.monotonic() < self._wait_until:
                self.status.setText("暂停中…")
                return True
            if not self.model.handle_key(key_name):
                return False
            index = self.model.current_event_index
            event = self._dialogue_at(index)
            if event is not None:
                self._render_progress(event, self.model.current_character_index)
            self._reload_events()
            if self.model.complete:
                self._commit()
            return True

        def _render_progress(self, event: Dialogue, cursor: Optional[int]) -> None:
            width = max(
                (len(value.name) for value in self.project.characters.values()), default=0
            )
            if event.character is None:
                prefix = "&nbsp;" * (width + 4)
            else:
                character = self.project.characters.get(event.character)
                name = character.name if character else event.character
                css = _style_css(character.style if character else "")
                shown = (
                    "<span style='%s'>%s</span>" % (css, html.escape(name))
                    if css
                    else html.escape(name)
                )
                prefix = "%s%s : " % ("&nbsp;" * max(0, width - len(name)), shown)
            self.panel.setHtml(
                "<div style='line-height:1.9'>%s%s</div>"
                % (prefix, _styled_html(event.text, cursor))
            )

        def _commit(self) -> None:
            name = self.current_script_name()
            if name is None or self.model is None:
                return
            previous = self.project.script(name)
            targets = set(self.model.targets)
            result = self.model.result()

            # The timing model fills untouched sentences with zeros so the result
            # is serialisable.  Put back the "no timing yet" state for sentences
            # this run did not record, otherwise the export check would report
            # them as done.
            lines = []
            for index, item in enumerate(result.lines):
                original = previous.lines[index] if index < len(previous.lines) else None
                if (
                    isinstance(item, Dialogue)
                    and isinstance(original, Dialogue)
                    and index not in targets
                    and not original.delays
                ):
                    lines.append(Dialogue(item.character, item.text, []))
                else:
                    lines.append(item)

            self.project.set_script(name, Script(lines))
            self.studio.mark_dirty()
            self._reload_events()
            summary = self.project.summary()
            self.status.setText(
                "本句已记录：全剧 %d/%d 个字有计时%s"
                % (
                    summary["timed"],
                    summary["timed_total"],
                    "" if not summary["untimed_scripts"]
                    else "；还没录完：" + "、".join(summary["untimed_scripts"]),
                )
            )

    class LyricsStep(Step):
        title = "④ 歌词"

        def _build(self) -> None:
            self.table = QTableWidget(0, 4)
            self.table.setHorizontalHeaderLabels(["曲目", "类型", "歌词文件", "颜色"])
            compact_table(self.table)
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self._load_selected)

            add_button = QPushButton("插入音乐…")
            add_button.clicked.connect(self.add_music)
            remove_button = QPushButton("删除曲目")
            remove_button.clicked.connect(self.remove_music)

            left_buttons = QHBoxLayout()
            left_buttons.addWidget(add_button)
            left_buttons.addWidget(remove_button)
            left_buttons.addStretch(1)

            left = QWidget()
            left_layout = QVBoxLayout(left)
            left_layout.setContentsMargins(0, 0, 0, 0)
            left_layout.addWidget(self.table)
            left_layout.addLayout(left_buttons)

            self.kind_instrumental = QRadioButton("纯音乐")
            self.kind_lyrics = QRadioButton("带歌词")
            kind_row = QHBoxLayout()
            kind_row.addWidget(self.kind_instrumental)
            kind_row.addWidget(self.kind_lyrics)
            kind_row.addStretch(1)

            self.lyrics_edit = QPlainTextEdit()
            self.lyrics_edit.setPlaceholderText("[00:01.00]第一句\n[00:04.50]Second line")
            self.lyrics_edit.setMinimumHeight(240)

            import_button = QPushButton("导入歌词文件…")
            import_button.setToolTip("读取 .lrc 或纯文本歌词，导入后可再录制时间")
            import_button.clicked.connect(self.import_lyrics)
            record_button = QPushButton("录制每句时间…")
            record_button.clicked.connect(self.record_from_text)
            colour_button = QPushButton("歌词颜色…")
            colour_button.clicked.connect(self.pick_colour)
            self.colour_swatch = QLabel("　")
            self.colour_swatch.setFixedWidth(28)
            self.colour_swatch.setStyleSheet("border:1px solid #555;")

            tools = QHBoxLayout()
            tools.addWidget(import_button)
            tools.addWidget(record_button)
            tools.addWidget(QLabel("颜色"))
            tools.addWidget(self.colour_swatch)
            tools.addWidget(colour_button)
            tools.addStretch(1)

            apply_button = QPushButton("保存到剧情包")
            apply_button.clicked.connect(self.apply_lyrics)

            right = QWidget()
            right_layout = QVBoxLayout(right)
            right_layout.setContentsMargins(0, 0, 0, 0)
            right_layout.addLayout(kind_row)
            right_layout.addWidget(QLabel("歌词（LRC，一行一句）"))
            right_layout.addWidget(self.lyrics_edit, 1)
            right_layout.addLayout(tools)
            right_layout.addWidget(apply_button)

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(left)
            splitter.addWidget(right)
            splitter.setSizes([430, 650])

            self.hint = QLabel(
                "带歌词的曲目在播放时会弹出歌词窗；汉字用宋体，英文用 Times New Roman 斜体。"
            )
            self.hint.setStyleSheet("color:#777;")

            layout = QVBoxLayout(self)
            layout.addWidget(self.hint)
            layout.addWidget(splitter, 1)

            self._colour = LYRIC_COLOR_DEFAULT

        def refresh(self) -> None:
            tracks = [] if self.project is None else sorted(self.project.tracks)
            current = self._selected_abbreviation()
            self.table.setRowCount(len(tracks))
            for row, key in enumerate(tracks):
                track = self.project.tracks[key]
                values = [
                    studio.track_label(key, self.project.tracks),
                    "带歌词" if track.has_lyrics else "纯音乐",
                    track.lyrics or track.filename,
                    track.color or "—",
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, key)
                    if column == 3 and track.color:
                        item.setForeground(QColor(track.color))
                    self.table.setItem(row, column, item)
            self.table.resizeColumnsToContents()
            if current in tracks:
                self.table.selectRow(tracks.index(current))

        def _selected_abbreviation(self) -> Optional[str]:
            rows = self.table.selectionModel().selectedRows()
            if not rows:
                return None
            item = self.table.item(rows[0].row(), 0)
            return item.data(Qt.ItemDataRole.UserRole) if item else None

        def _load_selected(self) -> None:
            key = self._selected_abbreviation()
            if self.project is None or key is None or key not in self.project.tracks:
                return
            track = self.project.tracks[key]
            self.kind_lyrics.setChecked(track.has_lyrics)
            self.kind_instrumental.setChecked(not track.has_lyrics)
            self.lyrics_edit.setPlainText(self.project.lyrics_text(key))
            self._colour = track.color or LYRIC_COLOR_DEFAULT
            self._paint_swatch()

        def _paint_swatch(self) -> None:
            self.colour_swatch.setStyleSheet(
                "background:%s; border:1px solid #555;" % self._colour
            )

        def add_music(self) -> None:
            if not self.studio.require_project():
                return
            draft = collect_new_track(self, self.studio)
            if draft is None:
                return
            label = Path(draft.source).stem if draft.source else draft.abbreviation
            self.studio.run(
                lambda: self.project.add_music([draft]), "已插入 %s" % label
            )
            self.refresh()

        def remove_music(self) -> None:
            key = self._selected_abbreviation()
            if key is None:
                QMessageBox.information(self, "歌词", "请先选择一首音乐")
                return
            if QMessageBox.question(self, "删除曲目", "删除 %s？" % key) != QMessageBox.StandardButton.Yes:
                return
            self.studio.run(lambda: self.project.remove_music(key), "已删除 %s" % key)
            self.refresh()

        def import_lyrics(self) -> None:
            """Load an ``.lrc`` or a plain lyric text into the editor."""

            key = self._selected_abbreviation()
            if key is None:
                QMessageBox.information(self, "歌词", "请先选择一首音乐")
                return
            filename, _ = QFileDialog.getOpenFileName(
                self, "导入歌词", "",
                "歌词 (*.lrc *.txt);;LRC 歌词 (*.lrc);;纯文本 (*.txt);;所有文件 (*)",
            )
            if not filename:
                return
            try:
                text = Path(filename).read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                QMessageBox.critical(self, "歌词", "无法读取：%s" % exc)
                return
            self.lyrics_edit.setPlainText(text)
            self.kind_lyrics.setChecked(True)
            if Path(filename).suffix.lower() == ".txt":
                self.studio.status.setText(
                    "已载入纯文本歌词；点「录制每句时间…」播放音乐逐句打点"
                )

        def record_from_text(self) -> None:
            """Turn the plain lines in the box into LRC by playing the track."""

            key = self._selected_abbreviation()
            if self.project is None or key is None:
                QMessageBox.information(self, "歌词", "请先选择一首音乐")
                return
            lines = lyric_source_lines(self.lyrics_edit.toPlainText())
            if not lines:
                QMessageBox.information(self, "歌词", "先在下面写好歌词句子")
                return
            try:
                audio = self.project.audio_path(key)
            except StudioError as exc:
                QMessageBox.critical(self, "歌词", str(exc))
                return
            recorded = LyricsRecorderDialog.record(self, audio, lines)
            if recorded is None:
                return
            if not recorded.lines:
                recorded = timed_from_marks(lines, [0.0] * len(lines))
            self.lyrics_edit.setPlainText(serialize_lrc(recorded))
            self.kind_lyrics.setChecked(True)

        def pick_colour(self) -> None:
            colour = QColorDialog.getColor(QColor(self._colour), self, "歌词颜色")
            if colour.isValid():
                self._colour = colour.name()
                self._paint_swatch()

        def apply_lyrics(self) -> None:
            key = self._selected_abbreviation()
            if key is None:
                QMessageBox.information(self, "歌词", "请先选择一首音乐")
                return
            kind = "lyrics" if self.kind_lyrics.isChecked() else "instrumental"
            text = self.lyrics_edit.toPlainText().strip()
            if kind == "lyrics" and not text:
                QMessageBox.warning(self, "歌词", "选择「带歌词」时要填歌词内容")
                return
            self.studio.run(
                lambda: self.project.update_music(
                    key,
                    kind=kind,
                    lyrics_text=text if kind == "lyrics" else None,
                    lyrics_name="%s.lrc" % key,
                    color=self._colour,
                ),
                "已更新 %s" % key,
            )
            self.refresh()

    class ExportStep(Step):
        title = "⑤ 导出"

        def _build(self) -> None:
            self.summary = QLabel()
            self.summary.setWordWrap(True)

            save_button = QPushButton("保存到剧情包")
            save_button.clicked.connect(lambda: self.studio.save())
            bookmark_button = QPushButton("保存为新版本…")
            bookmark_button.setToolTip("给当前状态起个名字，之后可以随时回溯")
            bookmark_button.clicked.connect(self.save_version)
            export_button = QPushButton("另存为 .tscpkg…")
            export_button.clicked.connect(self.studio.export_as)
            play_button = QPushButton("试播")
            play_button.clicked.connect(self.studio.preview_play)
            folder_button = QPushButton("打开所在文件夹")
            folder_button.clicked.connect(self.open_folder)
            import_button = QPushButton("从已有剧情导入…")
            import_button.setToolTip("把一个 .tscpkg／.tscpkgs 或旧式 Musics/Scripts 文件夹合并进来")
            import_button.clicked.connect(self.import_from_source)

            buttons = QHBoxLayout()
            for button in (
                save_button, bookmark_button, export_button,
                play_button, folder_button, import_button,
            ):
                buttons.addWidget(button)
            buttons.addStretch(1)

            self.issues = QLabel()
            self.issues.setWordWrap(True)
            self.issues.setObjectName("hint")

            # -- revision history ------------------------------------------
            self.history = QTableWidget(0, 4)
            self.history.setHorizontalHeaderLabels(["版本", "时间", "标签", "规模"])
            compact_table(self.history)
            self.history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.history.horizontalHeader().setStretchLastSection(True)
            self.history.itemDoubleClicked.connect(lambda _item: self.restore_version())

            restore_button = QPushButton("回溯到这个版本")
            restore_button.clicked.connect(self.restore_version)
            refresh_button = QPushButton("刷新历史")
            refresh_button.clicked.connect(self.refresh_history)

            history_buttons = QHBoxLayout()
            history_buttons.addWidget(restore_button)
            history_buttons.addWidget(refresh_button)
            history_buttons.addStretch(1)

            self.history_hint = QLabel()
            self.history_hint.setWordWrap(True)
            self.history_hint.setObjectName("hint")

            layout = QVBoxLayout(self)
            layout.addWidget(QLabel("检查"))
            layout.addWidget(self.summary)
            layout.addWidget(self.issues)
            layout.addWidget(QLabel("版本历史（保存在项目文件里，导出时不会带走）"))
            layout.addWidget(self.history_hint)
            layout.addWidget(self.history, 1)
            layout.addLayout(history_buttons)
            layout.addLayout(buttons)

        def refresh(self) -> None:
            if self.project is None:
                self.summary.setText("还没有打开剧情包。")
                self.issues.setText("")
                self.history.setRowCount(0)
                self.history_hint.setText("")
                return
            data = self.project.summary()
            lines = [
                "剧情包：%s" % self.project.path,
                "名称：%s        %s" % (self.project.name, self.project.description or "（没有简介）"),
                "角色：%d 个" % data["characters"],
                "剧本：%d 个" % data["scripts"],
                "音乐：%d 首（其中带歌词 %d 首）" % (data["tracks"], len(data["lyrics_tracks"])),
                "逐字计时：%d / %d" % (data["timed"], data["timed_total"]),
            ]
            self.summary.setText("\n".join(lines))

            problems: List[str] = []
            if data["missing_characters"]:
                problems.append(
                    "剧本用到了还没定义的角色：" + "、".join(data["missing_characters"])
                )
            if data["untimed_scripts"]:
                problems.append("还没录完逐字时间的剧本：" + "、".join(data["untimed_scripts"]))
            if not data["scripts"]:
                problems.append("还没有任何剧本")
            if not data["dirty"] and self.project.path.is_file():
                problems.append("所有改动都已经写进剧情包了")
            self.issues.setText("\n".join(problems))
            self.refresh_history()

        def refresh_history(self) -> None:
            if self.project is None:
                return
            if not self.project.has_history:
                self.history.setRowCount(0)
                self.history_hint.setText(
                    "这是一个普通 .tscpkg，没有版本历史。"
                    "新建时选 .tscpkgs，每次保存都会在同一个文件里记一版。"
                )
                return
            revisions = self.project.revisions()
            self.history.setRowCount(len(revisions))
            for row, item in enumerate(revisions):
                values = [
                    item.get("ID", ""),
                    str(item.get("TIME", "")).replace("T", " "),
                    item.get("LABEL", ""),
                    "%d 角色 / %d 剧本" % (item.get("CHARACTERS", 0), item.get("SCRIPTS", 0)),
                ]
                for column, value in enumerate(values):
                    self.history.setItem(row, column, QTableWidgetItem(str(value)))
            self.history.resizeColumnsToContents()
            self.history_hint.setText("共 %d 个版本，最新的在最上面。" % len(revisions))

        def save_version(self) -> None:
            if not self.studio.require_project():
                return
            if not self.project.has_history:
                QMessageBox.information(
                    self, "版本", "普通 .tscpkg 不记录历史，请用 .tscpkgs 新建项目"
                )
                return
            label, ok = QInputDialog.getText(self, "保存为新版本", "这一版叫什么？")
            if not ok or not label.strip():
                return
            if not self.studio.save(label=label.strip()):
                return
            self.studio.status.setText("已记录版本：%s" % label.strip())

        def restore_version(self) -> None:
            if not self.studio.require_project():
                return
            rows = self.history.selectionModel().selectedRows()
            if not rows:
                QMessageBox.information(self, "版本", "请先选择一个版本")
                return
            revision_id = self.history.item(rows[0].row(), 0).text()
            label = self.history.item(rows[0].row(), 2).text()
            if QMessageBox.question(
                self,
                "回溯版本",
                "把工程内容回到「%s」（%s）？\n\n"
                "当前内容不会被删除：先保存一次就又是一个新版本。" % (label, revision_id),
            ) != QMessageBox.StandardButton.Yes:
                return
            outcome = self.studio.run(lambda: self.project.restore(revision_id))
            if outcome is None:
                return
            note = "已回溯到 %s（%s）" % (revision_id, outcome["label"])
            if outcome["music_missing"]:
                note += "；这些音乐已经不在包里，没能恢复：" + "、".join(
                    outcome["music_missing"]
                )
            self.studio.status.setText(note)

        def open_folder(self) -> None:
            if self.project is None:
                return
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.project.path.parent)))

        def import_from_source(self) -> None:
            if not self.studio.require_project():
                return
            source = self.studio.pick_import_source()
            if source is None:
                return
            self.studio.import_everything(source)

    # ----------------------------------------------------------------------
    # the window
    # ----------------------------------------------------------------------

    class StudioWindow(QMainWindow):
        """One window, five steps, no hand-edited files."""

        def __init__(self, path: Optional[Path] = None) -> None:
            super().__init__()
            self.project: Optional[StudioProject] = None
            self._player = None
            self.theme_name = "light"
            self._theme_actions: Dict[str, QAction] = {}
            self._autosave_enabled = True
            self._backoff = 1
            self._autosave = QTimer(self)
            self._autosave.setInterval(AUTO_SNAPSHOT_SECONDS * 1000)
            self._autosave.timeout.connect(self._auto_snapshot)
            self.setWindowTitle("剧情工坊")
            self.resize(1240, 820)
            icon = QIcon(str(ICON_PATH))
            if not icon.isNull():
                self.setWindowIcon(icon)
            self._build_ui()
            QApplication.instance().installEventFilter(self)
            if path:
                self.open_path(path)
            else:
                self._update_title()

        def _build_ui(self) -> None:
            new_button = QPushButton("新建剧情包")
            new_button.setToolTip("Ctrl+N")
            new_button.clicked.connect(self.new_project)
            open_button = QPushButton("打开 .tscpkg")
            open_button.setToolTip("Ctrl+O")
            open_button.clicked.connect(self.open_project)
            self.save_button = QPushButton("保存")
            self.save_button.setToolTip("Ctrl+S —— 把角色、剧本和计时写进剧情包")
            self.save_button.clicked.connect(lambda: self.save())
            export_button = QPushButton("另存为 .tscpkg")
            export_button.setToolTip("Ctrl+Shift+S —— 导出独立副本，原包继续编辑")
            export_button.clicked.connect(self.export_as)
            play_button = QPushButton("试播")
            play_button.clicked.connect(self.preview_play)

            toolbar = QHBoxLayout()
            for button in (new_button, open_button, self.save_button, export_button, play_button):
                toolbar.addWidget(button)
            toolbar.addStretch(1)

            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("剧情名称")
            self.description_edit = QLineEdit()
            self.description_edit.setPlaceholderText("简介（会显示在播放器的介绍页）")
            apply_button = QPushButton("应用信息")
            apply_button.clicked.connect(self.apply_metadata)
            header = QHBoxLayout()
            header.addWidget(QLabel("名称"))
            header.addWidget(self.name_edit, 1)
            header.addWidget(QLabel("简介"))
            header.addWidget(self.description_edit, 2)
            header.addWidget(apply_button)

            self.step_list = QListWidget()
            self.step_list.setObjectName("steps")
            self.step_list.setMaximumWidth(178)
            self.step_list.setMinimumWidth(168)
            self.step_list.setSpacing(0)
            self.stack = QStackedWidget()

            self.steps = [
                CharactersStep(self),
                StoryStep(self),
                TimingStep(self),
                LyricsStep(self),
                ExportStep(self),
            ]
            self.timing: TimingStep = self.steps[2]
            for step in self.steps:
                self.step_list.addItem(step.title)
                self.stack.addWidget(step)
            self.step_list.currentRowChanged.connect(self._step_changed)

            rail_title = QLabel("制作流程")
            rail_title.setObjectName("railTitle")
            rail = QWidget()
            rail_layout = QVBoxLayout(rail)
            rail_layout.setContentsMargins(0, 0, 0, 0)
            rail_layout.setSpacing(0)
            rail_layout.addWidget(rail_title)
            rail_layout.addWidget(self.step_list, 1)

            body = QHBoxLayout()
            body.setSpacing(0)
            body.addWidget(rail)
            body.addWidget(self.stack, 1)

            self.status = QLabel()
            self.status.setStyleSheet("color:#888;")

            layout = QVBoxLayout()
            layout.addLayout(toolbar)
            layout.addLayout(header)
            layout.addLayout(body, 1)
            layout.addWidget(self.status)

            central = QWidget()
            central.setLayout(layout)
            self.setCentralWidget(central)

            self._build_menu()
            self._restore_theme()
            self.step_list.setCurrentRow(0)
            self._autosave.start(AUTO_SNAPSHOT_SECONDS * 1000)

        def _build_menu(self) -> None:
            """A file menu, so Ctrl+S and friends are discoverable."""

            menu = self.menuBar().addMenu("文件(&F)")
            entries = (
                ("新建剧情包", QKeySequence.StandardKey.New, self.new_project),
                ("打开 .tscpkg / .tscpkgs…", QKeySequence.StandardKey.Open, self.open_project),
                ("保存", QKeySequence.StandardKey.Save, self.save),
                ("另存为 .tscpkg…", QKeySequence.StandardKey.SaveAs, self.export_as),
            )
            for label, shortcut, slot in entries:
                action = QAction(label, self)
                action.setShortcut(shortcut)
                action.triggered.connect(lambda _checked=False, s=slot: s())
                menu.addAction(action)
            menu.addSeparator()
            quit_action = QAction("退出", self)
            quit_action.setShortcut(QKeySequence.StandardKey.Quit)
            quit_action.triggered.connect(lambda _checked=False: self.close())
            menu.addAction(quit_action)

            view = self.menuBar().addMenu("界面(&V)")
            for key, label in theme.THEMES:
                action = QAction("%s主题" % label, self)
                action.setCheckable(True)
                action.setChecked(key == self.theme_name)
                action.triggered.connect(
                    lambda _checked=False, k=key: self.set_theme(k)
                )
                view.addAction(action)
                self._theme_actions[key] = action

            view.addSeparator()
            self.autosave_action = QAction(AUTO_SNAPSHOT_MENU_LABEL, self)
            self.autosave_action.setCheckable(True)
            self.autosave_action.setChecked(True)
            self.autosave_action.toggled.connect(self.set_autosave)
            view.addAction(self.autosave_action)

        # -- automatic snapshots ---------------------------------------
        def set_autosave(self, enabled: bool) -> None:
            self._autosave_enabled = bool(enabled)
            if self._autosave_enabled:
                self._backoff = 1
                self._autosave.start(AUTO_SNAPSHOT_SECONDS * 1000)
                self.status.setText(
                    "自动快照已开启：每 %d 分钟一次，无改动会自动延长"
                    % (AUTO_SNAPSHOT_SECONDS // 60)
                )
            else:
                self._autosave.stop()
                self.status.setText("自动快照已关闭")

        def _auto_snapshot(self) -> None:
            """Take a lightweight snapshot, stretching the interval when idle."""

            if self.project is None or not self.project.has_history:
                # Nothing to snapshot into; try again at the slowest rate.
                self._backoff = AUTO_SNAPSHOT_MAX_BACKOFF
                self._reschedule_autosave()
                return
            try:
                revision = self.project.auto_snapshot()
            except StudioError as exc:
                self.status.setText("自动快照失败：%s" % exc)
                self._reschedule_autosave()
                return
            if revision is None:
                # No change since last time: wait longer before looking again.
                self._backoff = min(self._backoff * 2, AUTO_SNAPSHOT_MAX_BACKOFF)
                self.status.setText(
                    "自动快照：没有改动，下次 %d 分钟后再看"
                    % (AUTO_SNAPSHOT_SECONDS * self._backoff // 60)
                )
            else:
                self._backoff = 1
                self._refresh_all()
                self.status.setText("已记录自动快照 %s" % revision)
            self._reschedule_autosave()

        def _reschedule_autosave(self) -> None:
            if self._autosave_enabled:
                self._autosave.start(AUTO_SNAPSHOT_SECONDS * self._backoff * 1000)

        # -- theme -----------------------------------------------------
        def _restore_theme(self) -> None:
            settings = self._settings()
            stored = ""
            if settings is not None:
                stored = str(settings.value("theme", "") or "")
            self.set_theme(stored or "light", remember=False)

        def _settings(self):
            try:
                from PySide6.QtCore import QSettings

                return QSettings("TSCP", "Studio")
            except Exception:  # pragma: no cover - no settings backend
                return None

        def set_theme(self, name: str, remember: bool = True) -> None:
            """Swap the studio's own sheet; the player window is unaffected."""

            self.theme_name = name if name in {"light", "dark"} else "light"
            # Scoped to this window on purpose: the player is created with no Qt
            # parent, so restyling the application would bleed into it.
            theme.apply(self, self.theme_name)
            for key, action in self._theme_actions.items():
                action.setChecked(key == self.theme_name)
            if remember:
                settings = self._settings()
                if settings is not None:
                    settings.setValue("theme", self.theme_name)
            self.status.setText("已切换到%s主题" % ("深色" if self.theme_name == "dark" else "浅色"))

        # -- project plumbing ------------------------------------------
        def _step_changed(self, row: int) -> None:
            if 0 <= row < len(self.steps):
                # Switching the page is the whole point of the left-hand list;
                # forgetting this leaves every step showing the first one.
                self.stack.setCurrentIndex(row)
                self.steps[row].refresh()

        def _update_title(self) -> None:
            if self.project is None:
                self.setWindowTitle("剧情工坊")
                return
            mark = "*" if self.project.dirty else ""
            kind = "工程" if self.project.has_history else "剧情包"
            self.setWindowTitle("剧情工坊 - [%s] %s%s" % (kind, self.project.name, mark))
            self.name_edit.setText(self.project.name)
            self.description_edit.setText(self.project.description)
            self.save_button.setEnabled(True)

        def _refresh_all(self) -> None:
            self._update_title()
            for step in self.steps:
                step.refresh()

        def require_project(self) -> bool:
            if self.project is None:
                QMessageBox.information(self, "剧情工坊", "请先新建或打开一个剧情包")
                return False
            return True

        def run(self, action, success: str = ""):
            """Run one project operation, reporting failures instead of crashing."""

            try:
                result = action()
            except (StudioError, PackError, OSError, ValueError) as exc:
                QMessageBox.warning(self, "剧情工坊", str(exc))
                self.status.setText("失败：%s" % exc)
                return None
            if success:
                self.status.setText(success)
            self._refresh_all()
            return result

        def mark_dirty(self) -> None:
            if self.project is not None:
                self.project.dirty = True
                self._update_title()

        # -- file actions ----------------------------------------------
        def new_project(self) -> None:
            if not self._confirm_discard():
                return
            # Default into ``source/`` — that is the folder the player scans — so
            # a new plot does not land in whatever the process cwd happens to be.
            folder = Path(__file__).resolve().parent.parent / "source"
            if not folder.is_dir():
                folder = Path.home()
            filename, _ = QFileDialog.getSaveFileName(
                self,
                "选择新工程的位置",
                str(folder / ("新剧情" + studio.PROJECT_SUFFIX)),
                "TSCP 工程，含版本历史 (*.tscpkgs);;TSCP 剧情包，可直接播放 (*.tscpkg)",
            )
            if not filename:
                return
            target = Path(filename)
            if target.suffix.lower() not in {studio.PROJECT_SUFFIX, studio.PLOT_SUFFIX}:
                # No recognisable extension: default to the project format, which
                # is the one that keeps history.
                target = target.with_suffix(studio.PROJECT_SUFFIX)
            name, ok = QInputDialog.getText(self, "新建剧情包", "剧情名称", text=target.stem)
            if not ok or not name.strip():
                return
            description, ok = QInputDialog.getText(self, "新建剧情包", "简介（可留空）")
            if not ok:
                return
            try:
                self.project = StudioProject.create(
                    target, name=name.strip(), description=description.strip()
                )
            except StudioError as exc:
                QMessageBox.critical(self, "剧情工坊", str(exc))
                return
            self.step_list.setCurrentRow(0)
            self._refresh_all()
            self.status.setText("已创建 %s，从「① 角色」开始" % target.name)

        def open_project(self) -> None:
            if not self._confirm_discard():
                return
            folder = Path(__file__).resolve().parent.parent / "source"
            filename, _ = QFileDialog.getOpenFileName(
                self,
                "打开工程或剧情包",
                str(folder) if folder.is_dir() else "",
                "TSCP 工程与剧情包 (*.tscpkgs *.tscpkg);;"
                "TSCP 工程 (*.tscpkgs);;TSCP 剧情包 (*.tscpkg);;所有文件 (*)",
            )
            if filename:
                self.open_path(Path(filename))

        def open_path(self, path: Path) -> None:
            try:
                self.project = StudioProject.load(path)
            except StudioError as exc:
                QMessageBox.critical(self, "剧情工坊", "无法打开：%s" % exc)
                return
            self._refresh_all()
            self.status.setText("已打开 %s" % path.name)

        def save(self, label: Optional[str] = None) -> bool:
            if not self.require_project():
                return False
            text = str(label).strip() if label else None
            try:
                self.project.save(text)
            except StudioError as exc:
                QMessageBox.critical(self, "剧情工坊", "保存失败：%s" % exc)
                return False
            self._refresh_all()
            note = "（版本：%s）" % text if text else ""
            self.status.setText("已保存到 %s%s" % (self.project.path.name, note))
            return True

        def export_as(self) -> None:
            if not self.require_project():
                return
            filename, _ = QFileDialog.getSaveFileName(
                self, "另存为剧情包", self.project.path.name, "TSCP 剧情包 (*.tscpkg)"
            )
            if not filename:
                return
            if not self.save():
                return
            try:
                target = self.project.export(filename)
            except StudioError as exc:
                QMessageBox.critical(self, "剧情工坊", "导出失败：%s" % exc)
                return
            self.status.setText("已导出 %s（原包继续编辑）" % target)

        def apply_metadata(self) -> None:
            if not self.require_project():
                return
            name = self.name_edit.text().strip()
            if not name:
                QMessageBox.warning(self, "剧情工坊", "剧情名称不能为空")
                return
            self.project.name = name
            self.project.description = self.description_edit.text().strip()
            self.project.dirty = True
            self._update_title()
            self.status.setText("信息已更新，记得保存")

        def preview_play(self) -> None:
            """Save, then open the existing player on this package.

            A project file (``.tscpkgs``) is exported to a temporary ``.tscpkg``
            first, so the player never has to know about the history member.
            """

            if not self.require_project():
                return
            if not self.save():
                return
            target = self.project.path
            if target.suffix.lower() == studio.PROJECT_SUFFIX:
                temporary = Path(tempfile.gettempdir()) / (
                    target.stem + ".preview" + studio.PLOT_SUFFIX
                )
                try:
                    if temporary.exists():
                        temporary.unlink()
                    target = studio.export_playable(self.project.path, temporary)
                except (StudioError, OSError) as exc:
                    QMessageBox.critical(self, "剧情工坊", "无法准备试播：%s" % exc)
                    return
            try:
                import Main as player_module
            except ImportError as exc:  # pragma: no cover
                QMessageBox.critical(self, "剧情工坊", "无法加载播放器：%s" % exc)
                return
            if not getattr(player_module, "QT_AVAILABLE", False):  # pragma: no cover
                QMessageBox.critical(self, "剧情工坊", "播放器需要 PySide6")
                return
            try:
                core, problems = player_module.load_playlists(target)
            except Exception as exc:  # noqa: BLE001 - surfaced to the user
                QMessageBox.critical(self, "剧情工坊", "无法试播：%s" % exc)
                return
            self._player = player_module.PlayerWindow(core, target, problems)
            self._player.show()

        # -- importing -------------------------------------------------
        def pick_import_source(self) -> Optional[Path]:
            """Ask for a ``.tscpkg`` or a plot folder; ``None`` when cancelled."""

            box = QMessageBox(self)
            box.setWindowTitle("导入来源")
            box.setText("要从哪种来源导入？")
            file_button = box.addButton(
                "选择 .tscpkg 文件", QMessageBox.ButtonRole.AcceptRole
            )
            folder_button = box.addButton(
                "选择剧情文件夹", QMessageBox.ButtonRole.AcceptRole
            )
            box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is file_button:
                filename, _ = QFileDialog.getOpenFileName(
                    self, "选择剧情包", "", "TSCP 剧情包 (*.tscpkg);;所有文件 (*)"
                )
                return Path(filename) if filename else None
            if clicked is folder_button:
                folder = QFileDialog.getExistingDirectory(
                    self, "选择剧情文件夹（含 Musics 和 Scripts）"
                )
                return Path(folder) if folder else None
            return None

        def import_everything(self, source) -> None:
            """Merge characters, scripts and music from another plot or folder."""

            if not self.require_project():
                return
            result = self.run(lambda: self.project.import_from(source))
            if result is None:
                return
            message = "%s：导入 %d 个角色、%d 个剧本、%d 首音乐" % (
                Path(source).name,
                result["characters"],
                result["scripts"],
                result["music"],
            )
            notes = result.get("notes") or []
            if notes:
                message += "；" + "；".join(notes)
            self.status.setText(message)

        # -- lifecycle -------------------------------------------------
        def _confirm_discard(self) -> bool:
            if self.project is None or not self.project.dirty:
                return True
            answer = QMessageBox.question(
                self,
                "剧情工坊",
                "「%s」还有没保存的改动，先保存吗？" % self.project.name,
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.Save:
                return self.save()
            return True

        def eventFilter(self, watched, event) -> bool:
            """Route key presses to the timing step while it is recording."""

            timing = self.timing
            if (
                timing.model is not None
                and event.type() == QEvent.Type.KeyPress
                and watched is not timing.key_edit
                and self.stack.currentWidget() is timing
            ):
                key_event = event
                name = key_event.text() or ""
                if key_event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    name = "Enter"
                elif key_event.key() == Qt.Key.Key_Space:
                    name = "Space"
                if timing.handle_key(name):
                    return True
            return super().eventFilter(watched, event)

        def closeEvent(self, event) -> None:
            if not self._confirm_discard():
                event.ignore()
                return
            QApplication.instance().removeEventFilter(self)
            event.accept()

else:  # pragma: no cover
    StudioWindow = None  # type: ignore[assignment,misc]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="剧情工坊：一个窗口走完全流程")
    parser.add_argument("path", nargs="?", help="要打开的 .tscpkg")
    args = parser.parse_args(argv)
    if not QT_AVAILABLE:
        raise RuntimeError("剧情工坊需要安装可选依赖 PySide6")
    app = QApplication(sys.argv)
    window = StudioWindow(Path(args.path) if args.path else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
