"""PySide6 asset manager for single-file ``.tscpkg`` plots.

Inserting a track or a script is a two-click job: pick the file, confirm the
abbreviation, and the container is rewritten once with the audio embedded and
``Musics/__init__.json`` updated together.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tscp_player import archive

try:
    from .model import (
        MusicDraft,
        PackError,
        PackageInfo,
        add_music,
        add_script,
        add_tracks,
        create_package,
        export_script,
        inspect,
        pack_directory,
        remove_music,
        remove_script,
        replace_script,
        save_copy,
        script_text,
        suggest_abbreviation,
        update_metadata,
        update_track,
    )
except ImportError:  # Support ``python PlotManager/Main.py``.
    from PlotManager.model import (  # type: ignore
        MusicDraft,
        PackError,
        PackageInfo,
        add_music,
        add_script,
        add_tracks,
        create_package,
        export_script,
        inspect,
        pack_directory,
        remove_music,
        remove_script,
        replace_script,
        save_copy,
        script_text,
        suggest_abbreviation,
        update_metadata,
        update_track,
    )

try:
    from .recorder import LyricsRecorderDialog
except ImportError:  # Support ``python PlotManager/Main.py``.
    from PlotManager.recorder import LyricsRecorderDialog  # type: ignore

from tscp_player.lyrics import lyric_source_lines, serialize_lrc

try:  # pragma: no cover - depends on the optional GUI package
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QColorDialog,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


AUDIO_FILTER = (
    "音频 (*.flac *.mp3 *.ogg *.oga *.opus *.wav *.m4a *.aac);;所有文件 (*)"
)

#: Default colour of the on-screen lyric text.
LYRIC_COLOR_DEFAULT = "#ffffff"


def _size_text(size: int) -> str:
    if size <= 0:
        return "—"
    if size < 1024:
        return "%d B" % size
    if size < 1024 * 1024:
        return "%.1f KB" % (size / 1024)
    return "%.1f MB" % (size / (1024 * 1024))


if QT_AVAILABLE:

    class ScriptTextDialog(QDialog):
        """Edit the compiled text of one script without leaving the package."""

        def __init__(self, parent, title: str, text: str) -> None:
            super().__init__(parent)
            self.setWindowTitle(title)
            self.resize(940, 660)
            hint = QLabel("直接编辑编译后的 .tscp 文本；确定时会校验格式，非法内容不会写回。")
            hint.setStyleSheet("color:#666;")
            self.editor = QPlainTextEdit(text)
            self.editor.setFont(QFont("Consolas", 11))
            self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout = QVBoxLayout(self)
            layout.addWidget(hint)
            layout.addWidget(self.editor, 1)
            layout.addWidget(buttons)

        @classmethod
        def edit(cls, parent, title: str, text: str):
            dialog = cls(parent, title, text)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return dialog.editor.toPlainText(), True
            return text, False


    class ManagerWindow(QMainWindow):
        """Create a container, then insert music and scripts into it."""

        def __init__(self, path: Optional[Path] = None) -> None:
            super().__init__()
            self.info: Optional[PackageInfo] = None
            self.setWindowTitle("剧情素材管理器")
            self.resize(1080, 700)
            self._build_ui()
            if path:
                self._open(path)

        # -- construction --------------------------------------------------
        def _build_ui(self) -> None:
            new_button = QPushButton("新建剧情包")
            new_button.clicked.connect(self.new_package)
            open_button = QPushButton("打开 .tscpkg")
            open_button.clicked.connect(self.open_package)
            save_as_button = QPushButton("导出副本 .tscpkg")
            save_as_button.setToolTip(
                "把当前剧情包（含已插入的音乐和剧本）导出成另一个 .tscpkg 文件，\n"
                "导出后仍继续编辑原来那个包。"
            )
            save_as_button.clicked.connect(self.save_as_package)
            pack_button = QPushButton("文件夹导出为 .tscpkg")
            pack_button.setToolTip(
                "把包含 Musics 和 Scripts 的整个文件夹打包导出成一个 .tscpkg，\n"
                "音乐和 Scripts 里的 .tscp/.tscps 都会一起装进去。"
            )
            pack_button.clicked.connect(self.pack_directory)
            refresh_button = QPushButton("刷新")
            refresh_button.clicked.connect(self.refresh)

            toolbar = QHBoxLayout()
            for button in (
                new_button, open_button, save_as_button, pack_button, refresh_button,
            ):
                toolbar.addWidget(button)
            toolbar.addStretch(1)

            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("剧情名称")
            self.description_edit = QLineEdit()
            self.description_edit.setPlaceholderText("简介")
            apply_button = QPushButton("应用信息")
            apply_button.clicked.connect(self.apply_metadata)
            meta_row = QHBoxLayout()
            meta_row.addWidget(QLabel("名称"))
            meta_row.addWidget(self.name_edit, 1)
            meta_row.addWidget(QLabel("简介"))
            meta_row.addWidget(self.description_edit, 2)
            meta_row.addWidget(apply_button)

            self.music_table = QTableWidget(0, 6)
            self.music_table.setHorizontalHeaderLabels(
                ["简称", "类型", "文件", "大小", "歌词", "颜色"]
            )
            self.script_table = QTableWidget(0, 4)
            self.script_table.setHorizontalHeaderLabels(["剧本", "事件行", "可见字数", "角色"])
            for table in (self.music_table, self.script_table):
                table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
                table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
                table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
                table.horizontalHeader().setStretchLastSection(True)

            music_panel = self._panel(
                "音乐",
                self.music_table,
                [
                    ("插入音乐", "把一个或多个音频嵌进包并绑定简称；\n每个音频都会先问是纯音乐还是带歌词", self.insert_music),
                    ("设置歌词", "修改选中曲目的类型、歌词来源与显示颜色", self.configure_lyrics),
                    ("删除选中", "解除简称；不再被引用的音频会一并删掉", self.delete_music),
                ],
            )
            script_panel = self._panel(
                "剧本",
                self.script_table,
                [
                    ("导入 .tscp", "校验后直接放进 Scripts/", self.import_script),
                    ("原稿编译导入", "把 .tscps 编译成 .tscp 再放进 Scripts/", self.compile_source),
                    ("编辑内容", "在包内直接修改选中剧本并写回", self.edit_script),
                    ("导出选中", "把选中剧本复制到磁盘，改完再导入回来", self.export_scripts),
                    ("删除选中", "从包里移除选中的剧本", self.delete_script),
                ],
            )

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(music_panel)
            splitter.addWidget(script_panel)
            splitter.setSizes([520, 520])

            self.status = QLabel("新建一个 .tscpkg，或打开已有的包")

            layout = QVBoxLayout()
            layout.addLayout(toolbar)
            layout.addLayout(meta_row)
            layout.addWidget(splitter, 1)
            layout.addWidget(self.status)
            central = QWidget()
            central.setLayout(layout)
            self.setCentralWidget(central)

        @staticmethod
        def _panel(title: str, table: QTableWidget, actions) -> QWidget:
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.addWidget(QLabel(title))
            layout.addWidget(table, 1)
            buttons = QHBoxLayout()
            for label, tip, handler in actions:
                button = QPushButton(label)
                button.setToolTip(tip)
                button.clicked.connect(handler)
                buttons.addWidget(button)
            buttons.addStretch(1)
            layout.addLayout(buttons)
            return panel

        # -- helpers -------------------------------------------------------
        def _run(self, action: Callable[[], str], done: str) -> bool:
            """Run one container operation, reporting failures in the status bar."""

            try:
                note = action()
            except (PackError, archive.PackageError, OSError, ValueError) as exc:
                self.status.setText("失败：%s" % exc)
                QMessageBox.warning(self, "剧情素材管理器", str(exc))
                return False
            self.refresh()
            self.status.setText(done % (note or "") if "%s" in done else done)
            return True

        def _require(self) -> Optional[Path]:
            if self.info is None:
                QMessageBox.information(self, "剧情素材管理器", "请先新建或打开一个 .tscpkg")
                return None
            return self.info.location

        def _selected(self, table: QTableWidget) -> list:
            rows = sorted({index.row() for index in table.selectedIndexes()})
            return [table.item(row, 0).text() for row in rows]

        # -- package level -------------------------------------------------
        def new_package(self) -> None:
            """Pick where the container lives first, then describe it.

            The file dialog comes first because that is where the user is already
            thinking about the new plot, and the name then defaults to whatever
            they typed there instead of the other way round.
            """

            filename, _ = QFileDialog.getSaveFileName(
                self, "选择新剧情包的位置", "新剧情.tscpkg",
                "TSCP 剧情包 (*.tscpkg)",
            )
            if not filename:
                return
            target = Path(filename)
            if target.suffix.lower() != archive.SUFFIX:
                target = target.with_suffix(archive.SUFFIX)

            name, ok = QInputDialog.getText(
                self, "新建剧情包", "剧情名称", text=target.stem
            )
            if not ok or not name.strip():
                return
            description, ok = QInputDialog.getText(self, "新建剧情包", "简介（可留空）")
            if not ok:
                return
            if self._run(
                lambda: str(create_package(target, name=name, description=description)),
                "已创建：%s",
            ):
                self._open(target)

        def open_package(self) -> None:
            filename, _ = QFileDialog.getOpenFileName(
                self, "打开剧情包", "", "TSCP 剧情包 (*.tscpkg);;所有文件 (*)"
            )
            if filename:
                self._open(Path(filename))

        def _open(self, path: Path) -> None:
            try:
                self.info = inspect(path)
            except (PackError, archive.PackageError, OSError, ValueError) as exc:
                QMessageBox.critical(self, "剧情素材管理器", "无法打开：%s" % exc)
                return
            self.setWindowTitle("剧情素材管理器 - %s" % path.name)
            self.name_edit.setText(self.info.name)
            self.description_edit.setText(self.info.description)
            self._fill_tables()
            self.status.setText(
                "已打开 %s：%d 个音乐简称、%d 个剧本、%d 个角色"
                % (path.name, len(self.info.music), len(self.info.scripts),
                   len(self.info.characters))
            )

        def save_as_package(self) -> None:
            """Export the open container, with everything inserted, to a new file."""

            path = self._require()
            if path is None:
                return
            filename, _ = QFileDialog.getSaveFileName(
                self, "导出剧情包副本", path.name, "TSCP 剧情包 (*.tscpkg)"
            )
            if not filename:
                return
            try:
                target = save_copy(path, filename)
            except (PackError, archive.PackageError, OSError, ValueError) as exc:
                QMessageBox.critical(self, "剧情素材管理器", "导出失败：%s" % exc)
                return
            self.status.setText(
                "已导出 %s：%d 个音乐、%d 个剧本（仍在本包里继续编辑）"
                % (target, len(self.info.music), len(self.info.scripts))
            )

        def pack_directory(self) -> None:
            directory = QFileDialog.getExistingDirectory(self, "选择旧目录剧情（含 Musics 和 Scripts）")
            if not directory:
                return
            source = Path(directory)
            filename, _ = QFileDialog.getSaveFileName(
                self, "另存为剧情包", "%s.tscpkg" % source.name,
                "TSCP 剧情包 (*.tscpkg)",
            )
            if not filename:
                return
            target = Path(filename)
            if self._run(
                lambda: str(pack_directory(source, target)),
                "已打包：%s",
            ):
                self._open(target.with_suffix(".tscpkg"))

        def apply_metadata(self) -> None:
            path = self._require()
            if path is None:
                return
            self._run(
                lambda: update_metadata(
                    path,
                    name=self.name_edit.text(),
                    description=self.description_edit.text(),
                ),
                "剧情信息已更新",
            )

        def refresh(self) -> None:
            if self.info is None:
                return
            self._open(self.info.location)

        # -- music ---------------------------------------------------------
        def insert_music(self) -> None:
            path = self._require()
            if path is None:
                return
            filenames, _ = QFileDialog.getOpenFileNames(
                self, "选择要插入的音乐", "", AUDIO_FILTER
            )
            if not filenames:
                return
            drafts = []
            for filename in filenames:
                source = Path(filename)
                abbreviation, ok = QInputDialog.getText(
                    self, "音乐简称",
                    "为 %s 指定 <p> 使用的简称：" % source.name,
                    text=suggest_abbreviation(source),
                )
                if not ok:
                    return
                draft = MusicDraft(source=source, abbreviation=abbreviation)
                if not self._ask_track_kind(draft):
                    return
                drafts.append(draft)
            self._run(
                lambda: "、".join(add_tracks(path, drafts)),
                "已插入音乐并更新简称：%s",
            )

        def _ask_track_kind(self, draft: MusicDraft) -> bool:
            """Ask whether the track is instrumental, then collect its lyrics."""

            name = Path(draft.source).name
            answer = QMessageBox.question(
                self,
                "音乐类型",
                "《%s》是纯音乐吗？\n\n"
                "是 —— 纯音乐，播放时只放声音\n"
                "否 —— 带歌词，接下来选择歌词来源和显示颜色"
                % name,
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.Yes:
                draft.kind = "instrumental"
                return True
            draft.kind = "lyrics"
            return self._collect_lyrics(draft)

        def _collect_lyrics(self, draft: MusicDraft) -> bool:
            """Pick an existing ``.lrc`` or record timings for a ``lyrics.txt``."""

            source = QMessageBox.question(
                self,
                "歌词来源",
                "歌词从哪来？\n\n"
                "是 —— 选择现成的 .lrc 文件\n"
                "否 —— 选择 lyrics.txt，现在播放音乐并逐句录制出现时间",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if source == QMessageBox.StandardButton.Cancel:
                return False

            if source == QMessageBox.StandardButton.Yes:
                filename, _ = QFileDialog.getOpenFileName(
                    self, "选择 LRC 歌词", "", "LRC 歌词 (*.lrc);;所有文件 (*)"
                )
                if not filename:
                    return False
                draft.lyrics_file = Path(filename)
            else:
                filename, _ = QFileDialog.getOpenFileName(
                    self, "选择歌词文本", "", "歌词文本 (*.txt);;所有文件 (*)"
                )
                if not filename:
                    return False
                text_file = Path(filename)
                try:
                    lines = lyric_source_lines(text_file.read_text(encoding="utf-8"))
                except (OSError, UnicodeError) as exc:
                    QMessageBox.critical(self, "剧情素材管理器", "无法读取歌词：%s" % exc)
                    return False
                if not lines:
                    QMessageBox.warning(self, "剧情素材管理器", "这个文件里没有歌词行")
                    return False
                recorded = LyricsRecorderDialog.record(
                    self, Path(draft.source), lines
                )
                if recorded is None:
                    return False
                draft.lyrics_text = serialize_lrc(recorded)
                draft.lyrics_name = text_file.with_suffix(".lrc").name

            colour = QColorDialog.getColor(
                QColor(LYRIC_COLOR_DEFAULT), self, "选择歌词显示颜色"
            )
            if colour.isValid():
                draft.color = colour.name()
            return True

        def configure_lyrics(self) -> None:
            """Change the selected track's kind, lyrics source and colour."""

            path = self._require()
            if path is None:
                return
            selected = self._selected(self.music_table)
            if len(selected) != 1:
                QMessageBox.information(self, "剧情素材管理器", "请先选择恰好一个音乐简称")
                return
            abbreviation = selected[0]
            entry = next(
                (item for item in self.info.music if item.abbreviation == abbreviation),
                None,
            )
            if entry is None:
                return

            draft = MusicDraft(
                abbreviation=abbreviation, kind=entry.kind, color=entry.color
            )
            if not self._ask_track_kind(draft):
                return
            try:
                if draft.kind == "lyrics":
                    update_track(
                        path,
                        abbreviation,
                        kind="lyrics",
                        lyrics_file=draft.lyrics_file,
                        lyrics_text=draft.lyrics_text,
                        lyrics_name=draft.lyrics_name,
                        color=draft.color or None,
                    )
                else:
                    update_track(
                        path, abbreviation, kind="instrumental", color=draft.color or None
                    )
            except (PackError, archive.PackageError, OSError, ValueError) as exc:
                QMessageBox.critical(self, "剧情素材管理器", "设置失败：%s" % exc)
                return
            self.refresh()
            self.status.setText("已更新 %s 的歌词设置" % abbreviation)

        def delete_music(self) -> None:
            path = self._require()
            if path is None:
                return
            selected = self._selected(self.music_table)
            if not selected:
                QMessageBox.information(self, "剧情素材管理器", "请先在音乐表中选择简称")
                return
            self._run(
                lambda: "、".join(remove_music(path, selected)),
                "已删除：%s",
            )

        # -- scripts -------------------------------------------------------
        def import_script(self) -> None:
            path = self._require()
            if path is None:
                return
            filename, _ = QFileDialog.getOpenFileName(
                self, "导入编译剧本", "", "TSCP 剧本 (*.tscp);;所有文件 (*)"
            )
            if not filename:
                return
            source = Path(filename)
            self._run(lambda: add_script(path, source), "已导入：%s")

        def compile_source(self) -> None:
            path = self._require()
            if path is None:
                return
            filename, _ = QFileDialog.getOpenFileName(
                self, "编译原稿并导入", "", "TSCP 原稿 (*.tscps);;所有文件 (*)"
            )
            if not filename:
                return
            source = Path(filename)
            answer = QMessageBox.question(
                self, "原稿编译导入",
                "原稿还没有逐字时间，导入后会以 0 秒生成 %s。\n"
                "建议先在 Ts2Tp 里录好时间再导入。仍要继续吗？"
                % source.with_suffix(".tscp").name,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._run(
                lambda: add_script(path, source, compile_source=True),
                "已编译并导入：%s",
            )

        def edit_script(self) -> None:
            path = self._require()
            if path is None:
                return
            selected = self._selected(self.script_table)
            if len(selected) != 1:
                QMessageBox.information(self, "剧情素材管理器", "请先选择恰好一个剧本")
                return
            member = selected[0]
            try:
                original = script_text(path, member)
            except (PackError, archive.PackageError, OSError, ValueError) as exc:
                QMessageBox.critical(self, "剧情素材管理器", "无法读取：%s" % exc)
                return
            edited, accepted = ScriptTextDialog.edit(self, "编辑 %s" % member, original)
            if not accepted or edited == original:
                return
            self._run(lambda: replace_script(path, member, edited), "已写回：%s")

        def export_scripts(self) -> None:
            path = self._require()
            if path is None:
                return
            selected = self._selected(self.script_table)
            if not selected:
                QMessageBox.information(self, "剧情素材管理器", "请先在剧本表中选择一行")
                return
            directory = QFileDialog.getExistingDirectory(self, "选择导出目录")
            if not directory:
                return
            try:
                written = [
                    export_script(path, member, Path(directory) / member).name
                    for member in selected
                ]
            except (PackError, archive.PackageError, OSError, ValueError) as exc:
                QMessageBox.critical(self, "剧情素材管理器", "导出失败：%s" % exc)
                return
            self.status.setText("已导出到 %s：%s" % (directory, "、".join(written)))

        def delete_script(self) -> None:
            path = self._require()
            if path is None:
                return
            selected = self._selected(self.script_table)
            if not selected:
                QMessageBox.information(self, "剧情素材管理器", "请先在剧本表中选择一行")
                return
            for name in selected:
                if not self._run(lambda member=name: remove_script(path, member), "已删除：%s"):
                    return
            self.refresh()

        # -- tables --------------------------------------------------------
        def _fill_tables(self) -> None:
            assert self.info is not None
            self.music_table.setRowCount(len(self.info.music))
            for row, entry in enumerate(self.info.music):
                if entry.has_lyrics:
                    kind = "带歌词"
                    lyrics = entry.lyrics or ""
                    if not entry.lyrics_present:
                        lyrics += "（缺少文件）"
                else:
                    kind = "纯音乐"
                    lyrics = "—"
                values = [
                    entry.abbreviation,
                    kind,
                    entry.filename,
                    _size_text(entry.size),
                    lyrics,
                    entry.color or "—",
                ]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if column == 5 and entry.color:
                        item.setForeground(QColor(entry.color))
                        item.setBackground(QColor("#202124"))
                    self.music_table.setItem(row, column, item)
            self.music_table.resizeColumnsToContents()

            self.script_table.setRowCount(len(self.info.scripts))
            for row, entry in enumerate(self.info.scripts):
                values = [
                    entry.filename,
                    str(entry.lines),
                    str(entry.characters),
                    " ".join(entry.keys),
                ]
                for column, value in enumerate(values):
                    self.script_table.setItem(row, column, QTableWidgetItem(value))
            self.script_table.resizeColumnsToContents()

else:
    ManagerWindow = None  # type: ignore[assignment,misc]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="管理 .tscpkg 剧情包中的音乐与剧本")
    parser.add_argument("path", nargs="?", help="要打开的 .tscpkg")
    args = parser.parse_args(argv)
    if not QT_AVAILABLE:
        raise RuntimeError("PlotManager 需要安装可选依赖 PySide6")
    app = QApplication(sys.argv)
    window = ManagerWindow(Path(args.path) if args.path else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
