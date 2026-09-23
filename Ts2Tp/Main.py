"""PySide6 timing workspace for converting ``.tscps`` source files."""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tscp_player.audio import MusicPlayer
from tscp_player.format import (
    ANSI_SEQUENCE_RE,
    Dialogue,
    Directive,
    Script,
    parse_tscp,
    parse_tscps,
    serialize_tscp,
)
from tscp_player.music import STOP_WORDS, MusicCue, build_timeline
from tscp_player.plot import (
    PlotPackageError,
    find_music_directory,
    load_music_files,
)
from TSGenerator.model import load_character_config, split_pages

try:
    from .model import (
        KeyboardTimingModel,
        TimingMode,
        compiled_name,
        timed_character_positions,
        visible_characters,
    )
except ImportError:  # Support ``python Ts2Tp/Main.py``.
    from Ts2Tp.model import (  # type: ignore
        KeyboardTimingModel,
        TimingMode,
        compiled_name,
        timed_character_positions,
        visible_characters,
    )

try:  # pragma: no cover - depends on optional GUI package
    from PySide6.QtCore import QEvent, Qt, QTimer
    from PySide6.QtGui import QColor, QKeyEvent
    from PySide6.QtWidgets import (
        QApplication,
        QAbstractItemView,
        QComboBox,
        QDoubleSpinBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


def _display_line(line: object) -> str:
    """Return a readable source representation for previews and diagnostics."""

    if isinstance(line, Dialogue):
        return ("[%s]" % line.character if line.character else "[旁白] ") + line.text
    return "<%s>%s" % (line.command, line.value)


def parse_script_file(text: str, suffix: str = "") -> Script:
    """Parse either a compiled ``.tscp`` or a source ``.tscps`` document.

    A compiled script keeps its existing delays, so re-opening one lets a single
    sentence be re-timed without losing the rest of the recording.
    """

    if suffix.lower() == ".tscp" or text.lstrip().startswith("TSCP "):
        return parse_tscp(text)
    return parse_tscps(text)


def _seconds(value: object) -> float:
    """Parse a ``<s>`` duration, treating anything broken as zero."""

    try:
        return max(0.0, float(str(value).strip()))
    except (TypeError, ValueError):
        return 0.0


def _key_name(event: "QKeyEvent") -> str:
    if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
        return "Enter"
    if event.key() == Qt.Key.Key_Space:
        return "Space"
    return event.text() or str(event.key())


if QT_AVAILABLE:

    def _ansi_css(sequence: str) -> str:
        """Translate common SGR sequences to small inline CSS fragments."""

        normalized = sequence.replace(r"\033", "\x1b").replace(r"\x1b", "\x1b")
        match = re.search(r"\x1b\[([0-9;]*)m", normalized)
        if not match:
            return ""
        colors = {
            30: "#000000", 31: "#cc3333", 32: "#33aa55", 33: "#bb9911",
            34: "#3366cc", 35: "#aa55aa", 36: "#229999", 37: "#dddddd",
            90: "#777777", 91: "#ff6666", 92: "#66dd88", 93: "#ffff66",
            94: "#6699ff", 95: "#ee88ee", 96: "#66eeee", 97: "#ffffff",
        }
        css = []
        for raw_code in match.group(1).split(";"):
            code = int(raw_code or 0)
            if code == 0:
                css = []
            elif code in colors:
                css.append("color:%s" % colors[code])
            elif code == 1:
                css.append("font-weight:bold")
            elif code == 3:
                css.append("font-style:italic")
            elif code == 4:
                css.append("text-decoration:underline")
        return ";".join(css)


    def _styled_html(text: str, current_index: Optional[int] = None) -> str:
        """Render text with ANSI styling and a clear current-character marker."""

        parts = re.split("(" + ANSI_SEQUENCE_RE.pattern + ")", text)
        visible_index = 0
        style = ""
        output = []
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
                output.append(
                    "<span style='%s'>%s</span>" % (css, safe) if css else safe
                )
                visible_index += 1
        return "".join(output)


    def _char_fragments(text: str):
        """Yield one HTML fragment per visible character, ANSI styles applied."""

        style = ""
        for part in re.split("(" + ANSI_SEQUENCE_RE.pattern + ")", text):
            if not part:
                continue
            if ANSI_SEQUENCE_RE.fullmatch(part):
                style = _ansi_css(part)
                continue
            for character in part:
                safe = html.escape(character).replace(" ", "&nbsp;")
                yield "<span style='%s'>%s</span>" % (style, safe) if style else safe


    class ConverterWindow(QMainWindow):
        """Table-driven timing interface; source controls never consume a key."""

        def __init__(self, path: Optional[Path] = None) -> None:
            super().__init__()
            self.script = Script()
            self.source_path: Optional[Path] = None
            self.model: Optional[KeyboardTimingModel] = None
            self.pages = []
            self.page_index = 0
            self._handled_directives = set()
            self.music_root: Optional[Path] = None
            self.music_files: dict = {}
            self.characters: dict = {}
            self._music: Optional[MusicPlayer] = None
            self._music_unavailable = False
            self._wait_until = 0.0
            self._preview_running = False
            self._preview_index = 0
            self._preview_parts: list = []
            self._preview_fragments: list = []
            self._preview_delays: list = []
            self._preview_char = 0
            self.setWindowTitle("Ts2Tp - TSCP 逐字计时")
            self.resize(1180, 720)
            self._build_ui()
            QApplication.instance().installEventFilter(self)
            if path:
                self._open_path(path)

        def _build_ui(self) -> None:
            open_button = QPushButton("打开 .tscps")
            open_button.clicked.connect(self.open_file)
            start_button = QPushButton("开始/重新计时")
            start_button.setToolTip(
                "从第一句开始全程计时。第一次按计时键才是计时起点，\n"
                "点按钮到第一次按键之间的时间不会被记录。"
            )
            start_button.clicked.connect(self.start_timing)
            selected_button = QPushButton("只录选中句")
            selected_button.setToolTip(
                "只重新录制表格中选中的那一句，其余句保留已有时间。\n"
                "配合表格里的「音乐」列可以确认这一句该从音乐的哪个位置开始。"
            )
            selected_button.clicked.connect(self.start_selected)
            save_button = QPushButton("保存 .tscp")
            save_button.clicked.connect(self.save_file)
            self.full_button = QPushButton("完全预览")
            self.full_button.setToolTip(
                "按设定好的程序流程完整播放一次整个剧本：\n"
                "逐字节奏、<p> 音乐和 <s> 暂停都会真的执行。播放中按 Esc 停止。"
            )
            self.full_button.clicked.connect(self.full_preview)
            page_button = QPushButton("内容单页展示")
            page_button.clicked.connect(self.next_page)
            music_button = QPushButton("音乐文件夹")
            music_button.setToolTip("选择带 __init__.json 的音乐目录，把 <p> 简称解析成实际文件")
            music_button.clicked.connect(self.choose_music_folder)

            self.mode = QComboBox()
            self.mode.addItem("逐句模式（句间停顿不计入）", TimingMode.PER_SENTENCE)
            self.mode.addItem("连续模式（句间停顿计入下一句）", TimingMode.CONTINUOUS)
            self.mode.setToolTip(
                "逐句：每句独立计时，句子之间的停顿被丢弃。\n"
                "连续：整条时间轴连续，句间停顿记进下一句的第一个字。\n"
                "想只改一句话请用「只录选中句」。"
            )
            self.key_edit = QLineEdit("Enter")
            self.key_edit.setMaximumWidth(130)
            self.key_edit.setToolTip("Enter、Space 或任意自定义键名；自定义键可填一个字符")
            key_label = QLabel("计时键:")
            self.preview_delay = QDoubleSpinBox()
            self.preview_delay.setRange(0.0, 2.0)
            self.preview_delay.setDecimals(2)
            self.preview_delay.setSingleStep(0.01)
            self.preview_delay.setValue(0.06)
            self.preview_delay.setMaximumWidth(90)
            self.preview_delay.setToolTip("剧本还没有逐字时间时，完全预览使用的间隔（秒）")
            preview_label = QLabel("预览逐字:")

            toolbar = QHBoxLayout()
            for button in (
                open_button, start_button, selected_button, save_button,
                self.full_button, page_button, music_button,
            ):
                toolbar.addWidget(button)
            toolbar.addWidget(self.mode)
            toolbar.addWidget(key_label)
            toolbar.addWidget(self.key_edit)
            toolbar.addWidget(preview_label)
            toolbar.addWidget(self.preview_delay)
            toolbar.addStretch(1)

            self.table = QTableWidget(0, 6)
            self.table.setHorizontalHeaderLabels(
                ["序号", "类型", "角色", "内容/参数", "进度", "音乐"]
            )
            self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self._selection_changed)

            self.character_panel = QTextEdit()
            self.character_panel.setReadOnly(True)
            self.character_panel.setPlaceholderText("选择或到达对白后，这里会高亮当前字")
            self.character_panel.setStyleSheet(
                "QTextEdit { background:#202124; color:#f0f0f0; padding:12px; "
                "font-size:18px; }"
            )
            self.event_label = QLabel("当前事件：—")
            self.status = QLabel("就绪。打开原稿后按 Enter 记录计时。")

            layout = QVBoxLayout()
            layout.addLayout(toolbar)
            layout.addWidget(self.table, 3)
            layout.addWidget(self.event_label)
            layout.addWidget(self.character_panel, 2)
            layout.addWidget(self.status)
            central = QWidget()
            central.setLayout(layout)
            self.setCentralWidget(central)

        def open_file(self) -> None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "打开剧本",
                "",
                "TSCP 剧本 (*.tscps *.tscp);;原稿 (*.tscps);;编译稿 (*.tscp);;所有文件 (*)",
            )
            if filename:
                self._open_path(Path(filename))

        def _open_path(self, path: Path) -> None:
            try:
                text = path.read_text(encoding="utf-8")
                self.script = parse_script_file(text, path.suffix)
            except (OSError, ValueError, UnicodeError) as exc:
                QMessageBox.critical(self, "Ts2Tp", "无法打开：%s" % exc)
                return
            self.source_path = path
            self.pages = split_pages(self.script)
            self.page_index = 0
            self.model = None
            self._handled_directives.clear()
            self._wait_until = 0.0
            self._stop_preview()
            self._stop_music()
            try:
                self.characters = load_character_config(path)
            except ValueError:
                self.characters = {}
            self._load_music_pack(find_music_directory(path))
            self._render_table()
            self.setWindowTitle("Ts2Tp - %s" % path.name)
            music_note = (
                "%d 个音乐简称" % len(self.music_files)
                if self.music_files else "未解析音乐简称"
            )
            self.status.setText(
                "已打开：%s（%s）；「开始/重新计时」全程录，或选一行后「只录选中句」"
                % (path.name, music_note)
            )

        def _load_music_pack(self, directory: Optional[Path]) -> None:
            self.music_root = directory
            self.music_files = {}
            if directory is None:
                self.status.setText("未找到 Musics 目录，<p> 简称将按文件名直接查找")
                return
            try:
                self.music_files = load_music_files(directory)
            except (PlotPackageError, OSError) as exc:
                self.status.setText("音乐配置不可用（%s）；<p> 简称将按文件名直接查找" % exc)
                return
            self.status.setText(
                "音乐目录：%s（%d 个简称）" % (directory, len(self.music_files))
            )

        def choose_music_folder(self) -> None:
            directory = QFileDialog.getExistingDirectory(self, "选择音乐文件夹")
            if directory:
                self._load_music_pack(Path(directory))

        def _player(self) -> Optional[MusicPlayer]:
            """Return the shared music player, or ``None`` when audio is unavailable."""

            if self._music is None and not self._music_unavailable:
                try:
                    self._music = MusicPlayer()
                except RuntimeError as exc:
                    self._music_unavailable = True
                    self.status.setText("音乐不可用：%s" % exc)
            return self._music

        def _stop_music(self) -> None:
            if self._music is not None:
                self._music.stop()

        def _apply_music(self, value: str) -> str:
            """Execute one ``<p>`` directive and describe what happened."""

            key = value.strip()
            if key.lower() in STOP_WORDS:
                self._stop_music()
                return "音乐已停止"
            filename = self.music_files.get(key, key)
            path = Path(filename)
            if self.music_root is not None and not path.is_absolute():
                path = self.music_root / filename
            player = self._player()
            if player is None:
                return "音乐不可用（未安装 pygame 或音频设备不可用）"
            try:
                reloaded = player.ensure(str(path))
            except (OSError, RuntimeError) as exc:
                return "无法播放 %s：%s" % (key, exc)
            if reloaded:
                return "播放音乐 %s（从头开始）—— %s" % (key, path.name)
            # 同一曲目不会重启，所以下一句从上一句结束的位置继续。
            return "续播音乐 %s（不重启）—— %s" % (key, path.name)

        def _begin_wait(self, value: str) -> str:
            """Execute one ``<s>`` directive by really waiting it out."""

            try:
                seconds = max(0.0, float(str(value).strip()))
            except ValueError:
                seconds = 0.0
            self._wait_until = time.monotonic() + seconds
            delay_ms = int(seconds * 1000)
            if delay_ms > 0:
                QTimer.singleShot(delay_ms, self._wait_finished)
                return "暂停 %.2f 秒（暂停期间按键不计时）" % seconds
            return "暂停 0 秒"

        def _wait_finished(self) -> None:
            self._wait_until = 0.0
            if self.model is not None and not self.model.complete:
                self.status.setText("暂停结束：继续按 %s 记录" % self.key_edit.text())

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

        def _selected_row(self) -> Optional[int]:
            rows = self.table.selectionModel().selectedRows()
            return rows[0].row() if rows else None

        def _start_run(self, targets: Optional[List[int]], message: str) -> None:
            if self.source_path is None:
                QMessageBox.information(self, "Ts2Tp", "请先打开 .tscps 原稿")
                return
            self._handled_directives.clear()
            self._wait_until = 0.0
            self._stop_preview()
            self._stop_music()
            self.model = KeyboardTimingModel(
                self.script,
                self.mode.currentData(),
                accepted_keys=self._configured_keys(),
                event_callback=self._event_reached,
                targets=targets,
            )
            # arm() 而不是 start()：第一次按计时键才是计时基准点。
            self.model.arm()
            self._refresh_progress()
            self.status.setText(message)

        def start_timing(self) -> None:
            self._start_run(
                None,
                "全程计时：按 %s 记录当前字；第一次按键即计时起点（空格自动显示）"
                % self.key_edit.text(),
            )

        def start_selected(self) -> None:
            row = self._selected_row()
            if row is None:
                QMessageBox.information(self, "Ts2Tp", "请先在表格中选择一行")
                return
            if not isinstance(self.script.lines[row], Dialogue):
                QMessageBox.information(self, "Ts2Tp", "只能重新录制对白或旁白行")
                return
            cue = self._music_timeline().at(row)
            self._start_run(
                [row],
                "单句录制第 %d 行（音乐 %s）：其余句保留已有时间"
                % (row + 1, cue.describe()),
            )

        def _untimed_lines(self) -> List[int]:
            return [
                index
                for index, item in enumerate(self.script.lines)
                if isinstance(item, Dialogue) and not item.delays
            ]

        def _finish_run(self) -> None:
            """Write a completed run back into ``self.script`` so it accumulates."""

            if self.model is None or not self.model.complete:
                return
            self.script = self.model.result()
            self.pages = split_pages(self.script)
            untimed = self._untimed_lines()
            if untimed:
                shown = ", ".join(str(index + 1) for index in untimed[:6])
                more = "…" if len(untimed) > 6 else ""
                self.status.setText(
                    "本句已更新；还有 %d 句未计时（第 %s%s 行），可以继续用「只录选中句」补录"
                    % (len(untimed), shown, more)
                )
            else:
                self.status.setText("计时完成：%d 句全部就绪，可保存 .tscp" % (
                    len([item for item in self.script.lines if isinstance(item, Dialogue)])
                ))

        def _event_reached(self, index: int, event: object) -> None:
            if isinstance(event, Directive):
                self._handled_directives.add(index)
            self._refresh_progress(select_index=index)
            if isinstance(event, Directive):
                note = self._execute_directive(event)
                self.character_panel.setHtml(
                    "<h3>控制语句已执行</h3><p>%s</p><p>%s</p>"
                    % (html.escape(_display_line(event)), html.escape(note))
                )

        def _execute_directive(self, event: Directive) -> str:
            if event.command == "p":
                return self._apply_music(event.value)
            if event.command == "s":
                return self._begin_wait(event.value)
            return "已清空屏幕"

        def eventFilter(self, watched: object, event: object) -> bool:
            if event.type() == QEvent.Type.KeyPress and watched is not self.key_edit:
                key_event = event  # type: ignore[assignment]
                if self._preview_running:
                    # 完全预览期间按键只用来停止，不参与计时。
                    if key_event.key() == Qt.Key.Key_Escape:
                        self._stop_preview()
                        self.status.setText("完全预览已停止")
                    return True
                if self.model is not None:
                    remaining = self._wait_until - time.monotonic()
                    if remaining > 0:
                        # <s> 暂停真的生效：暂停期间的按键不计时。
                        self.status.setText("暂停中… 还需 %.1f 秒" % remaining)
                        return True
                    if self.model.handle_key(_key_name(key_event)):
                        self._refresh_progress()
                        if self.model.complete:
                            self._finish_run()
                        return True
            return super().eventFilter(watched, event)

        def _render_table(self) -> None:
            self.table.blockSignals(True)
            self.table.setRowCount(len(self.script.lines))
            timeline = self._music_timeline()
            for row, event in enumerate(self.script.lines):
                if isinstance(event, Dialogue):
                    kind = "角色对白" if event.character else "旁白"
                    character = event.character or ""
                    content = event.text
                    total = len(timed_character_positions(event.text))
                else:
                    kind = {"s": "暂停", "c": "清空屏幕", "p": "播放音乐"}.get(
                        event.command, event.command
                    )
                    character = ""
                    content = event.value
                    total = 0
                values = [
                    str(row + 1), kind, character, content,
                    "0/%d" % total, timeline.at(row).describe(),
                ]
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))
                if isinstance(event, Directive):
                    for column in range(6):
                        self.table.item(row, column).setBackground(QColor("#fff1c2"))
            self.table.blockSignals(False)

        def _music_timeline(self):
            """Music bound to every line, from the delays known so far."""

            if self.model is not None:
                return self.model.music_timeline()
            return build_timeline(self.script)

        def _refresh_progress(self, select_index: Optional[int] = None) -> None:
            if self.model is not None:
                timeline = self._music_timeline()
                for row, event in enumerate(self.script.lines):
                    item = self.table.item(row, 4)
                    if isinstance(event, Dialogue):
                        recorded, total = self.model.progress_for_event(row)
                        item.setText("%d/%d" % (recorded, total))
                    elif row in self._handled_directives:
                        item.setText("已处理")
                    self.table.item(row, 5).setText(timeline.at(row).describe())
                index = self.model.current_event_index if select_index is None else select_index
                if index is not None and 0 <= index < self.table.rowCount():
                    self.table.selectRow(index)
                self._show_event(index)

        def _selection_changed(self) -> None:
            rows = self.table.selectionModel().selectedRows()
            if rows:
                self._show_event(rows[0].row())

        def _show_event(self, index: Optional[int]) -> None:
            if index is None or not (0 <= index < len(self.script.lines)):
                return
            event = self.script.lines[index]
            if isinstance(event, Dialogue):
                current = self.model.current_character_index if self.model and self.model.current_event_index == index else None
                self.character_panel.setHtml(
                    "<div style='color:#9aa0a6'>%s</div><div style='line-height:1.8'>%s</div>"
                    % (
                        html.escape("[%s]" % event.character if event.character else "[旁白]"),
                        _styled_html(event.text, current),
                    )
                )
                recorded, total = (
                    self.model.progress_for_event(index)
                    if self.model else (0, len(timed_character_positions(event.text)))
                )
                cue: MusicCue = self._music_timeline().at(index)
                self.event_label.setText(
                    "当前事件：%d  句：%s  %s    字符：%s    计时：%d/%d    音乐：%s"
                    % (
                        index + 1,
                        str(self.model.current_sentence_index)
                        if self.model and self.model.current_sentence_index is not None else "—",
                        "对白" if event.character else "旁白",
                        "—" if current is None else str(current + 1),
                        recorded,
                        total,
                        cue.describe(),
                    )
                )
            else:
                self.character_panel.setHtml(
                    "<h3>控制语句（不计时）</h3><p>%s</p>" % html.escape(_display_line(event))
                )
                self.event_label.setText(
                    "当前事件：%d  控制语句 <%s>    音乐：%s"
                    % (index + 1, event.command, self._music_timeline().at(index).describe())
                )

        def save_file(self) -> None:
            if self.source_path is None:
                QMessageBox.information(self, "Ts2Tp", "请先打开 .tscps 原稿")
                return
            dialogues = [
                item for item in self.script.lines if isinstance(item, Dialogue)
            ]
            untimed = self._untimed_lines()
            if dialogues and len(untimed) == len(dialogues):
                QMessageBox.warning(self, "Ts2Tp", "还没有任何计时数据，请先录制至少一句")
                return
            if untimed:
                answer = QMessageBox.question(
                    self,
                    "Ts2Tp",
                    "还有 %d 句没有计时，这些句子会以 0 秒写入。仍要保存吗？\n未计时行：%s"
                    % (
                        len(untimed),
                        ", ".join(str(index + 1) for index in untimed[:10]),
                    ),
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            # Default next to the script that was opened, not to the process
            # working directory, so saving never drops a stray file at the root.
            default = Path(compiled_name(self.source_path or "plot.tscps"))
            if self.source_path is not None:
                default = self.source_path.with_name(default.name)
            filename, _ = QFileDialog.getSaveFileName(
                self, "保存编译文件", str(default),
                "TSCP 文件 (*.tscp);;所有文件 (*)",
            )
            if not filename:
                return
            path = Path(filename)
            if path.suffix.lower() != ".tscp":
                path = path.with_suffix(".tscp")
            try:
                path.write_text(serialize_tscp(self.script), encoding="utf-8")
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Ts2Tp", "无法保存：%s" % exc)
                return
            self.status.setText("已保存：%s" % path.name)

        def full_preview(self) -> None:
            """Play the whole script once, following the designed program flow.

            Every event is performed for real: characters appear at their
            recorded per-character delays, ``<p>`` switches the music and ``<s>``
            really waits.  A script that has not been timed yet uses the
            「预览逐字」 interval so the preview is still watchable.
            """

            if self._preview_running:
                self._stop_preview()
                self.status.setText("完全预览已停止")
                return
            if not self.script.lines:
                QMessageBox.information(self, "Ts2Tp", "还没有可播放的剧本")
                return
            self._wait_until = 0.0
            self._stop_music()
            self._preview_running = True
            self._preview_index = 0
            self._preview_parts = []
            self._preview_fragments = []
            self._preview_delays = []
            self._preview_char = 0
            self.character_panel.clear()
            self.full_button.setText("停止预览")
            self.status.setText("完全预览：按剧本流程完整播放一次（Esc 停止）")
            self._preview_next_event()

        def _stop_preview(self) -> None:
            self._preview_running = False
            self._stop_music()
            button = getattr(self, "full_button", None)
            if button is not None:
                button.setText("完全预览")

        def _preview_next_event(self) -> None:
            if not self._preview_running:
                return
            if self._preview_index >= len(self.script.lines):
                self._preview_running = False
                self.full_button.setText("完全预览")
                self._stop_music()
                self.status.setText("完全预览结束：整段剧情已按时间轴播完")
                return
            event = self.script.lines[self._preview_index]
            self._preview_index += 1
            if isinstance(event, Dialogue):
                self._preview_begin_dialogue(event)
            else:
                self._preview_directive(event)

        def _preview_begin_dialogue(self, event: Dialogue) -> None:
            width = max(
                (len(entry.name) for entry in self.characters.values()), default=0
            )
            if event.character is None:
                self._preview_parts.append("&nbsp;" * (width + 4))
            else:
                character = self.characters.get(event.character)
                name = character.name if character else event.character
                css = _ansi_css(character.style) if character else ""
                styled = (
                    "<span style='%s'>%s</span>" % (css, html.escape(name))
                    if css
                    else html.escape(name)
                )
                self._preview_parts.append(
                    "%s%s : " % ("&nbsp;" * max(0, width - len(name)), styled)
                )
            self._preview_fragments = list(_char_fragments(event.text))
            self._preview_delays = list(event.delays)
            self._preview_char = 0
            self._preview_write()

        def _preview_write(self) -> None:
            if not self._preview_running:
                return
            if self._preview_char >= len(self._preview_fragments):
                self._preview_parts.append("<br>")
                self._render_preview()
                QTimer.singleShot(0, self._preview_next_event)
                return
            self._preview_parts.append(self._preview_fragments[self._preview_char])
            if self._preview_delays:
                index = min(self._preview_char, len(self._preview_delays) - 1)
                delay = self._preview_delays[index]
            else:
                delay = self.preview_delay.value()
            self._preview_char += 1
            self._render_preview()
            QTimer.singleShot(max(0, round(delay * 1000)), self._preview_write)

        def _render_preview(self) -> None:
            self.character_panel.setHtml("".join(self._preview_parts))
            bar = self.character_panel.verticalScrollBar()
            bar.setValue(bar.maximum())

        def _preview_directive(self, event: Directive) -> None:
            if event.command == "c":
                self._preview_parts = []
                self.character_panel.clear()
                QTimer.singleShot(0, self._preview_next_event)
            elif event.command == "p":
                self.status.setText("完全预览：" + self._apply_music(event.value))
                QTimer.singleShot(0, self._preview_next_event)
            elif event.command == "s":
                seconds = _seconds(event.value)
                self.status.setText("完全预览：暂停 %.2f 秒" % seconds)
                QTimer.singleShot(max(0, round(seconds * 1000)), self._preview_next_event)
            else:
                QTimer.singleShot(0, self._preview_next_event)

        def next_page(self) -> None:
            if not self.pages:
                return
            page = self.pages[self.page_index]
            self.character_panel.setPlainText("\n".join(_display_line(line) for line in page.lines))
            self.status.setText("内容单页展示：第 %d/%d 页" % (self.page_index + 1, len(self.pages)))
            self.page_index = (self.page_index + 1) % len(self.pages)

        def closeEvent(self, event: object) -> None:
            QApplication.instance().removeEventFilter(self)
            if self._music is not None:
                self._music.close()
            super().closeEvent(event)

else:
    ConverterWindow = None  # type: ignore[assignment,misc]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="将 .tscps 转换为带逐字时间的 .tscp")
    parser.add_argument("path", nargs="?", help="要打开的 .tscps")
    args = parser.parse_args(argv)
    if not QT_AVAILABLE:
        raise RuntimeError("Ts2Tp 需要安装可选依赖 PySide6")
    app = QApplication(sys.argv)
    window = ConverterWindow(Path(args.path) if args.path else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
