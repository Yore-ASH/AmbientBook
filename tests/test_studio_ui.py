"""Offscreen tests for the unified studio window.

These drive the real widgets, because the wiring between the steps is exactly
what the dependency-free project tests cannot cover.
"""

import json

import pytest

from PlotManager.model import MusicDraft, read_script
from Studio import model as studio_model
from Studio.model import StudioProject
from tscp_player import archive
from tscp_player.format import Dialogue, Directive, Script, make_note
from tscp_player.plot import load_archive_package

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from Studio.Main import StudioWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch, qapp):
    """Fail loudly instead of hanging when something pops a modal dialog.

    With no display attached a real ``QMessageBox`` waits for a click that never
    comes, so every prompt is stubbed out for the whole module.
    """

    from PySide6.QtWidgets import QMessageBox

    def _ok(*_args, **_kwargs):
        return QMessageBox.StandardButton.Ok

    def _yes(*_args, **_kwargs):
        # "The user agreed to the prompt" is the useful default here; tests that
        # need a refusal stub their own.
        return QMessageBox.StandardButton.Yes

    def _no(*_args, **_kwargs):
        return QMessageBox.StandardButton.No

    for name, replacement in (
        ("information", _ok), ("warning", _ok), ("critical", _ok),
        ("question", _yes), ("about", _ok),
    ):
        try:
            monkeypatch.setattr(QMessageBox, name, staticmethod(replacement))
        except (AttributeError, TypeError):  # pragma: no cover - Qt build detail
            pass
    try:
        monkeypatch.setattr(
            QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok
        )
    except (AttributeError, TypeError):  # pragma: no cover
        pass
    # A real modal QDialog waits for a click that never comes offscreen, so any
    # dialog a shortcut happens to open is dismissed rather than hanging the run.
    from PySide6.QtWidgets import QDialog

    try:
        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    except (AttributeError, TypeError):  # pragma: no cover
        pass


@pytest.fixture
def messages(monkeypatch, qapp, no_modal_dialogs):
    """Records the message boxes the code shows, so tests can assert on them."""

    from PySide6.QtWidgets import QMessageBox

    seen = []

    def _record(name):
        def _handler(*args, **_kwargs):
            seen.append((name, args[2] if len(args) > 2 else ""))
            return QMessageBox.StandardButton.Ok

        return staticmethod(_handler)

    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, _record(name))
    return seen


@pytest.fixture
def studio(tmp_path, qapp, no_modal_dialogs):
    """A window with a freshly created project already loaded."""

    window = StudioWindow()
    project = studio_model.StudioProject.create(
        tmp_path / "demo.tscpkg", name="工坊", description="从角色到 tscpkg"
    )
    window.project = project
    window._refresh_all()
    yield window
    # Closing a dirty window asks to save; make the teardown deterministic.
    if window.project is not None:
        window.project.dirty = False
    window.close()


def _song(tmp_path, name="song.flac"):
    path = tmp_path / name
    path.write_bytes(b"FLAC" * 64)
    return path


def _story(window, tmp_path):
    """Fill characters and one script with the four row kinds."""

    project = window.project
    project.set_character("f", "FISH", "\033[1;33m")
    project.set_character("t", "Teiresias", "")
    project.add_music([MusicDraft(abbreviation="iw", source=_song(tmp_path))])

    name = project.new_script("序章")
    project.add_event(name, Directive("p", "iw"))
    project.add_event(name, Dialogue("f", "你好，世界！"))
    project.add_event(name, Directive("s", "0"))
    project.add_event(name, Dialogue(None, "旁白一句话。"))
    window._refresh_all()
    return name


# --------------------------------------------------------------------------

def test_window_has_all_five_steps(studio):
    titles = [studio.step_list.item(i).text() for i in range(studio.step_list.count())]
    assert len(titles) == 5
    assert titles[0].startswith("①")
    assert titles[-1].startswith("⑤")


def test_characters_step_lists_the_project(studio, tmp_path):
    window = studio
    window.project.set_character("f", "FISH", "\033[1;33m")
    window.project.set_character("t", "Teiresias", "")
    window.steps[0].refresh()

    step = window.steps[0]
    assert step.table.rowCount() == 2
    # The name comes first; the file key is tucked into the last column.
    assert [step.table.item(row, 0).text() for row in range(2)] == ["FISH", "Teiresias"]
    assert step.table.item(0, 2).text() == "f"
    # The style column shows the escaped spelling, not a real ESC byte.
    assert step.table.item(0, 1).text() == r"\033[1;33m"
    # The row still knows which character it is.
    from PySide6.QtCore import Qt

    assert step.table.item(0, 0).data(Qt.ItemDataRole.UserRole) == "f"
    step.table.selectRow(1)
    assert step._selected_key() == "t"


def test_story_step_labels_every_row_kind(studio, tmp_path):
    window = studio
    name = _story(window, tmp_path)
    step = window.steps[1]

    assert step.script_combo.currentText() == name
    assert step.table.rowCount() == 4
    assert [step.table.item(row, 1).text() for row in range(4)] == [
        "播放音乐", "角色对白", "暂停", "旁白",
    ]
    # Characters appear by name, music by its audio file — never by file key.
    assert step.table.item(1, 2).text() == "FISH"
    assert step.table.item(1, 3).text() == "你好，世界！"
    assert step.table.item(0, 3).text() == "♪ song"
    assert step.table.item(2, 3).text() == "0 秒"
    assert step.table.item(3, 2).text() == ""


def test_story_step_offers_every_editing_action(studio):
    step = studio.steps[1]
    assert set(step.action_buttons) == {
        "添加对白", "添加旁白", "补充内容", "添加暂停", "清空屏幕", "插入音乐",
        "停止音乐", "编辑", "上移", "下移", "删除",
    }


# --------------------------------------------------------------------------
# story-step shortcuts
# --------------------------------------------------------------------------

EXPECTED_SHORTCUTS = {
    "Ctrl+A": "添加旁白",
    "Ctrl+D": "添加对白",
    "Ctrl+B": "补充内容",
    "Ctrl+M": "插入音乐",
    "Ctrl+P": "停止音乐",
    "Ctrl+E": "清空屏幕",
    "Ctrl+T": "添加暂停",
}


def test_story_shortcuts_are_bound_to_the_right_actions(studio):
    from PySide6.QtGui import QKeySequence

    step = studio.steps[1]
    bound = {key: label for key, label, _shortcut in step.shortcuts}
    assert bound == EXPECTED_SHORTCUTS
    # ...and they really are Qt shortcuts, not just labels in a dict.
    for key, _label, shortcut in step.shortcuts:
        assert shortcut.key() == QKeySequence(key)
        assert shortcut.isEnabled() is True


def test_story_shortcuts_cannot_leak_off_the_page(studio):
    """Six keys on one page must not fire while another step has focus."""

    from PySide6.QtCore import Qt

    for _key, _label, shortcut in studio.steps[1].shortcuts:
        assert shortcut.context() == Qt.ShortcutContext.WidgetWithChildrenShortcut
        assert shortcut.parent() is studio.steps[1]


def test_story_shortcuts_do_not_clash_with_the_window_menu(studio):
    from PySide6.QtGui import QAction, QKeySequence

    step = studio.steps[1]
    page = {QKeySequence(key).toString() for key, _l, _s in step.shortcuts}
    window = {
        action.shortcut().toString()
        for action in studio.findChildren(QAction)
        if not action.shortcut().isEmpty()
    }
    assert page.isdisjoint(window), page & window


def _focus_story(window):
    """Show the story page and give it focus so shortcuts can actually fire.

    Qt only routes a key press through the shortcut map when a window is visible
    and active, so a hidden window silently swallows everything.
    """

    app = QApplication.instance()
    window.show()
    window.activateWindow()
    window.setFocus()
    app.processEvents()
    window.step_list.setCurrentRow(1)
    step = window.steps[1]
    step.setFocus()
    step.table.setFocus()
    app.processEvents()
    return step


def test_ctrl_a_really_adds_narration(studio, tmp_path, monkeypatch):
    """Drive a real key press, not just the signal."""

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    window = studio
    name = _story(window, tmp_path)

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return Dialogue(None, "快捷键写的旁白")

    monkeypatch.setattr("Studio.Main.DialogueDialog", FakeDialog)
    _focus_story(window)
    app = QApplication.instance()

    before = len(window.project.script(name).lines)
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()

    lines = window.project.script(name).lines
    assert len(lines) == before + 1
    assert lines[-1] == Dialogue(None, "快捷键写的旁白")


def test_ctrl_d_really_adds_dialogue(studio, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    window = studio
    name = _story(window, tmp_path)

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return Dialogue("f", "快捷键写的对白")

    monkeypatch.setattr("Studio.Main.DialogueDialog", FakeDialog)
    _focus_story(window)
    app = QApplication.instance()

    QTest.keyClick(window, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()

    lines = window.project.script(name).lines
    assert lines[-1] == Dialogue("f", "快捷键写的对白")


def test_the_instant_shortcuts_do_their_thing(studio, tmp_path, monkeypatch):
    """Ctrl+E and Ctrl+T need no dialog, so they can be driven directly."""

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    window = studio
    name = _story(window, tmp_path)

    class FakeSleep:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def value(self):
            return "2.5"

    monkeypatch.setattr("Studio.Main.SleepDialog", FakeSleep)
    _focus_story(window)
    app = QApplication.instance()

    QTest.keyClick(window, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.project.script(name).lines[-1] == Directive("c")

    QTest.keyClick(window, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert window.project.script(name).lines[-1] == Directive("s", "2.5")


def test_buttons_advertise_their_shortcut(studio):
    step = studio.steps[1]
    assert "Ctrl+A" in step.action_buttons["添加旁白"].toolTip()
    assert "Ctrl+P" in step.action_buttons["停止音乐"].toolTip()
    assert "Ctrl+A" in step.hint.text() and "Ctrl+P" in step.hint.text()


# --------------------------------------------------------------------------
# stopping the music is a control directive, and now a button
# --------------------------------------------------------------------------

def test_stop_music_button_inserts_the_directive(studio, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window = studio
    name = _story(window, tmp_path)
    step = _focus_story(window)
    app = QApplication.instance()

    QTest.keyClick(window, Qt.Key.Key_P, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()

    inserted = window.project.script(name).lines[-1]
    assert inserted == Directive("p", "")
    assert step.table.item(step.table.rowCount() - 1, 1).text() == "播放音乐"
    assert step.table.item(step.table.rowCount() - 1, 3).text() == "（停止音乐）"


def test_stop_music_survives_a_save_and_the_player_reads_it_as_a_stop(tmp_path):
    """The command the author inserts must actually stop playback."""

    from tscp_player.format import parse_tscp, serialize_tscp
    from tscp_player.music import STOP_WORDS, build_timeline

    script = Script([
        Directive("p", "night"),
        Dialogue("f", "音乐还在响", [0.1, 0.1, 0.1, 0.1, 0.1]),
        Directive("p", ""),
        Dialogue(None, "音乐停了", [0.1, 0.1, 0.1, 0.1]),
    ])
    text = serialize_tscp(script)
    assert "P|night" in text
    assert "P|\n" in text                      # an empty value is the stop

    back = parse_tscp(text)
    assert back.lines[2] == Directive("p", "")
    assert back.lines[2].value in STOP_WORDS

    timeline = build_timeline(back)
    assert timeline.at(0).track == "night"
    assert timeline.at(0).playing is True
    # From the stop line onwards nothing is playing.
    assert timeline.at(2).playing is False
    assert timeline.at(3).playing is False

    # And a project round trip keeps it intact.
    project = studio_model.StudioProject.create(tmp_path / "x.tscpkg", name="X", description="")
    name = project.new_script()
    for event in script.lines:
        project.add_event(name, event)
    project.save()
    reloaded = StudioProject.load(project.path)
    assert reloaded.scripts["plot.tscp"].lines[2] == Directive("p", "")


# --------------------------------------------------------------------------
# menu bar and window chrome
# --------------------------------------------------------------------------

def test_the_menu_bar_follows_the_theme(studio):
    from Studio import theme

    dark = theme.stylesheet("dark")
    # Both the bar and its items need their own rule; the platform style
    # otherwise paints the strip beside the last menu from the window role.
    assert "QMenuBar {" in dark
    assert "QMenuBar::item" in dark
    assert theme.DARK["panel"] in dark
    assert "QMenu::item:selected" in dark


def test_the_menu_bar_renders_the_themed_colour(studio, tmp_path):
    """Sample the pixels: the reported palette can look right while paint is not."""

    from Studio import theme

    studio.set_theme("dark", remember=False)
    _story(studio, tmp_path)
    studio.show()
    QApplication.instance().processEvents()
    image = studio.grab().toImage()
    bar = studio.menuBar()
    y = max(1, bar.height() // 2)
    # Far right of the bar: the empty strip that used to stay light.
    sample = image.pixelColor(image.width() - 8, y).name()
    assert sample == theme.DARK["panel"], sample


def test_the_titlebar_helper_is_safe_everywhere(studio):
    """It is a Windows nicety; on anything else it must quietly do nothing."""

    import sys

    from Studio import theme

    result = theme.apply_titlebar(studio, True)
    if sys.platform == "win32":
        assert isinstance(result, bool)
    else:
        assert result is False
    # Either way it must not disturb the widget.
    assert studio.windowTitle() != ""


def test_setting_the_theme_asks_for_a_matching_titlebar(studio, monkeypatch):
    from Studio import theme

    calls = []
    monkeypatch.setattr(
        theme, "apply_titlebar", lambda widget, dark: calls.append(dark) or True
    )
    studio.set_theme("dark", remember=False)
    studio.set_theme("light", remember=False)
    assert calls == [True, False]


# --------------------------------------------------------------------------
# the icon
# --------------------------------------------------------------------------

def test_the_window_carries_the_shipped_icon(studio):
    from Studio.Main import ICON_PATH

    assert ICON_PATH.is_file(), "the .ico must be committed next to Studio/Main.py"
    icon = studio.windowIcon()
    assert icon.isNull() is False
    # Multi-size, so Windows can pick the right one for the taskbar.
    sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
    assert (16, 16) in sizes
    assert (256, 256) in sizes


def test_the_icon_generator_reproduces_the_shipped_file(tmp_path):
    """The icon is a build artefact; its generator must still make the same one."""

    from Studio import make_icon
    from Studio.Main import ICON_PATH

    icon, _preview = make_icon.build(tmp_path)
    assert icon.read_bytes() == ICON_PATH.read_bytes()


def test_story_buttons_follow_the_selection(studio, tmp_path):
    window = studio
    _story(window, tmp_path)
    step = window.steps[1]
    step.table.clearSelection()
    step._update_buttons()
    assert step.action_buttons["删除"].isEnabled() is False

    step.table.selectRow(0)
    step._update_buttons()
    assert step.action_buttons["删除"].isEnabled() is True
    assert step.action_buttons["插入音乐"].isEnabled() is True


def test_timing_step_records_every_character(studio, tmp_path):
    window = studio
    name = _story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)
    step._reload_events()

    before = [step.table.item(row, 3).text() for row in range(step.table.rowCount())]
    assert before == ["—", "0/6", "—", "0/6"]

    step.start_timing()
    guard = 0
    while step.model is not None and not step.model.complete and guard < 80:
        guard += 1
        step._wait_until = 0.0        # do not really sit through <s>
        step.handle_key("Enter")

    delays = [
        list(item.delays)
        for item in window.project.script(name).lines
        if isinstance(item, Dialogue)
    ]
    # Every visible character got a slot, punctuation included.
    assert [len(item) for item in delays] == [6, 6]
    after = [step.table.item(row, 3).text() for row in range(step.table.rowCount())]
    assert after == ["—", "6/6", "—", "6/6"]


def test_timing_step_only_records_the_selected_sentence(studio, tmp_path):
    window = studio
    name = _story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)
    step._reload_events()

    step.table.selectRow(3)             # the narration row
    step.start_selected()
    assert step.model.targets == [3]

    guard = 0
    while step.model is not None and not step.model.complete and guard < 60:
        guard += 1
        step._wait_until = 0.0
        step.handle_key("Enter")

    lines = window.project.script(name).lines
    assert lines[1].delays == []        # untouched
    assert len(lines[3].delays) == 6


def test_lyrics_step_switches_a_track_to_lyrics(studio, tmp_path):
    window = studio
    _story(window, tmp_path)
    step = window.steps[3]
    step.refresh()

    assert step.table.rowCount() == 1
    assert step.table.item(0, 1).text() == "纯音乐"

    step.table.selectRow(0)
    step.kind_lyrics.setChecked(True)
    step.lyrics_edit.setPlainText("[00:01.00]第一句\n[00:03.50]Second line\n")
    step._colour = "#ffd166"
    step.apply_lyrics()

    track = window.project.tracks["iw"]
    assert track.has_lyrics is True
    assert track.color == "#ffd166"
    assert step.table.item(0, 1).text() == "带歌词"
    assert "第一句" in window.project.lyrics_text("iw")


def test_export_step_reports_and_writes_a_playable_package(studio, tmp_path):
    window = studio
    name = _story(window, tmp_path)
    project = window.project

    # Time both lines so the summary has nothing to complain about.
    for index, item in enumerate(project.script(name).lines):
        if isinstance(item, Dialogue):
            project.replace_event(
                name, index, Dialogue(item.character, item.text, [0.1] * 6)
            )
    step = window.steps[4]
    step.refresh()
    assert "逐字计时：12 / 12" in step.summary.text()
    assert step.issues.text() == ""

    window.save()
    assert project.dirty is False

    handout = project.export(tmp_path / "handout")
    package = load_archive_package(handout, cache_root=tmp_path / "cache")
    assert package.name == "工坊"
    assert package.script_names() == ["序章.tscp"]
    assert package.characters["f"].name == "FISH"
    assert package.track("iw").instrumental is True


def test_export_step_flags_missing_characters(studio, tmp_path):
    window = studio
    name = _story(window, tmp_path)
    window.project.add_event(name, Dialogue("ghost", "谁？"))
    step = window.steps[4]
    step.refresh()
    assert "ghost" in step.issues.text()


# --------------------------------------------------------------------------
# Ctrl+S and the other shortcuts
# --------------------------------------------------------------------------

def test_save_has_a_keyboard_shortcut(studio):
    from PySide6.QtGui import QAction

    shortcuts = {
        action.shortcut().toString()
        for action in studio.findChildren(QAction)
        if not action.shortcut().isEmpty()
    }
    assert any(value.endswith("+S") for value in shortcuts), shortcuts
    assert any(value.endswith("+O") for value in shortcuts), shortcuts
    assert any(value.endswith("+N") for value in shortcuts), shortcuts


def test_the_save_action_actually_saves(studio, tmp_path):
    from PySide6.QtGui import QAction

    window = studio
    window.project.set_character("f", "FISH", "")
    window.project.dirty = True
    save_action = next(
        action
        for action in window.findChildren(QAction)
        if action.shortcut().toString().endswith("+S")
    )
    save_action.trigger()
    assert window.project.dirty is False
    assert StudioProject.load(window.project.path).characters["f"].name == "FISH"


def test_the_save_button_explains_the_shortcut(studio):
    assert "Ctrl+S" in studio.save_button.toolTip()


# --------------------------------------------------------------------------
# importing from source files, step by step
# --------------------------------------------------------------------------

def _patch_open_file(monkeypatch, path):
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(path), ""))
    )


def _b64(text: str) -> str:
    """Compiled scripts store dialogue text as base64."""

    import base64

    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_characters_step_offers_batch_and_import(studio):
    step = studio.steps[0]
    assert {
        "添加角色", "编辑", "删除",
        "批量添加…", "粘贴导入…", "从 JSON 导入…", "从剧情包导入…", "导出角色…",
    } == set(step.buttons)


# --------------------------------------------------------------------------
# the left-hand rail
# --------------------------------------------------------------------------

def test_the_left_rail_switches_the_page(studio):
    """Regression: the rail refreshed the step but never actually showed it.

    Every step kept rendering the first page, so the right-hand buttons looked
    dead.  Driving the real list is the only way to catch that.
    """

    for index in range(studio.step_list.count()):
        studio.step_list.setCurrentRow(index)
        assert studio.stack.currentIndex() == index
        assert studio.stack.currentWidget() is studio.steps[index]


def test_each_page_shows_its_own_buttons(studio, tmp_path):
    """A symptom of the rail bug: every step reported the same button set."""

    _story(studio, tmp_path)
    seen = {}
    for index in range(studio.step_list.count()):
        studio.step_list.setCurrentRow(index)
        page = studio.stack.currentWidget()
        seen[index] = {button.text() for button in page.findChildren(QPushButton)}
    assert "添加角色" in seen[0]
    assert "插入音乐" in seen[1]
    assert "开始 / 重新计时" in seen[2]
    assert "导出角色…" in seen[0]
    # The pages are genuinely different.
    assert seen[0] != seen[1] != seen[2]


def test_switching_pages_refreshes_the_target(studio):
    window = studio
    window.step_list.setCurrentRow(1)
    window.project.set_character("f", "FISH", "")
    window.step_list.setCurrentRow(0)
    assert window.steps[0].table.rowCount() == 1


# --------------------------------------------------------------------------
# .tscpc character files
# --------------------------------------------------------------------------

def test_export_characters_writes_a_reusable_file(studio, tmp_path, monkeypatch):
    window = studio
    window.project.set_character("f", "FISH", "\033[1;33m")
    window.project.set_character("t", "Teiresias", "")
    window.steps[0].refresh()

    target = tmp_path / "cast.tscpc"
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), ""))
    )
    window.steps[0].export_characters()

    assert target.is_file()
    # ...and it goes straight back into a brand new project.
    assert "已导出 2 个角色" in window.status.text()
    fresh = studio_model.StudioProject.create(tmp_path / "fresh.tscpkg", name="新", description="")
    fresh.merge_characters(
        studio_model.parse_character_json(target.read_text(encoding="utf-8"))
    )
    assert fresh.character_rows() == window.project.character_rows()


def test_export_characters_needs_a_cast(studio, messages):
    studio.steps[0].export_characters()
    assert any("还没有角色" in text for _kind, text in messages)


# --------------------------------------------------------------------------
# pasting the generator's JSON
# --------------------------------------------------------------------------

PASTED_JSON = """{
    "f": {"NAME": "FISH", "STYLE": "\\u001b[1;33m"},
    "b": {"NAME": "Bister", "STYLE": "\\u001b[1;97m"},
    "I": {"NAME": "Iris", "STYLE": "\\u001b[1;36m"}
}"""


def test_paste_dialog_accepts_the_generator_json(studio):
    from Studio.Main import BatchCharactersDialog

    window = studio
    dialog = BatchCharactersDialog(window)
    dialog.editor.setPlainText(PASTED_JSON)
    dialog._detect()
    assert "识别到 3 个角色" in dialog.preview.text()

    window.project.merge_characters(studio_model.parse_characters(dialog.text()))
    assert window.project.characters["f"].name == "FISH"
    assert window.project.characters["f"].style == "\033[1;33m"
    assert window.project.characters["b"].style == "\033[1;97m"


def test_paste_dialog_still_accepts_tab_rows(studio):
    from Studio.Main import BatchCharactersDialog

    dialog = BatchCharactersDialog(studio)
    dialog.editor.setPlainText("f\tFISH\t\\033[33m\nt\tTeiresias\n")
    dialog._detect()
    assert "识别到 2 个角色" in dialog.preview.text()
    assert studio_model.parse_characters(dialog.text()) == [
        ("f", "FISH", "\033[33m"),
        ("t", "Teiresias", ""),
    ]


def test_paste_dialog_explains_a_bad_blob(studio):
    from Studio.Main import BatchCharactersDialog

    dialog = BatchCharactersDialog(studio)
    dialog.editor.setPlainText("{ broken")
    dialog._detect()
    assert "读不出来" in dialog.preview.text()


# --------------------------------------------------------------------------
# theme and density
# --------------------------------------------------------------------------

def test_dark_theme_applies_to_the_studio_only(studio):
    from Studio import theme

    studio.set_theme("dark", remember=False)
    assert studio.theme_name == "dark"
    assert studio.styleSheet() == theme.stylesheet("dark")
    assert studio.styleSheet() != theme.stylesheet("light")

    studio.set_theme("light", remember=False)
    assert studio.styleSheet() == theme.stylesheet("light")


def test_the_theme_never_touches_the_shared_application(studio):
    """The player is opened from here; restyling the app would bleed into it."""

    from PySide6.QtWidgets import QApplication

    before = QApplication.instance().styleSheet()
    studio.set_theme("dark", remember=False)
    assert QApplication.instance().styleSheet() == before
    studio.set_theme("light", remember=False)


def test_the_dark_theme_sets_a_matching_palette(studio):
    """A stylesheet alone left the item-view header painted Qt-grey.

    The stretched last header section is drawn by the header widget itself, so
    the header background needs its own rule; the palette carries the rest.
    """

    from PySide6.QtGui import QPalette

    from Studio import theme

    studio.set_theme("dark", remember=False)
    assert studio.palette().color(QPalette.ColorRole.Window).name() == theme.DARK["window"]
    # The header widget needs its own background, not just ::section.
    assert "QHeaderView { background:" in theme.stylesheet("dark")
    assert "item:selected" in theme.stylesheet("dark")


def test_the_stretched_header_section_is_themed(studio, tmp_path):
    """Regression: the last header section stayed Qt-grey (#efefef).

    Sampling the rendered pixels is the only way to catch this — the widget's
    reported palette looked correct while the paint did not.
    """

    from Studio import theme

    studio.set_theme("dark", remember=False)
    _story(studio, tmp_path)
    table = studio.steps[1].table
    table.resize(420, 200)
    header = table.horizontalHeader()
    image = table.grab().toImage()
    assert image.width() > 100 and header.height() > 2

    # The far right of the header band is inside the stretched last section.
    sample = image.pixelColor(image.width() - 15, header.height() // 2).name()
    assert sample == theme.DARK["head"], (
        "the stretched header section ignored the theme: got %s" % sample
    )


def test_theme_menu_marks_the_active_choice(studio):
    studio.set_theme("dark", remember=False)
    assert studio._theme_actions["dark"].isChecked() is True
    assert studio._theme_actions["light"].isChecked() is False


def test_tables_are_compact(studio, tmp_path):
    from Studio.Main import COMPACT_ROW_HEIGHT

    _story(studio, tmp_path)
    for index in (0, 1, 2, 3, 4):
        table = studio.steps[index].table if hasattr(studio.steps[index], "table") \
            else studio.steps[index].history
        assert table.verticalHeader().isVisible() is False
        assert table.verticalHeader().defaultSectionSize() == COMPACT_ROW_HEIGHT


# --------------------------------------------------------------------------
# revision history
# --------------------------------------------------------------------------

def _project_with_history(tmp_path, qapp, no_modal_dialogs):
    window = StudioWindow()
    project = studio_model.StudioProject.create(
        tmp_path / "work.tscpkgs", name="带历史", description=""
    )
    window.project = project
    window._refresh_all()
    return window


def test_history_panel_lists_versions(tmp_path, qapp, no_modal_dialogs):
    window = _project_with_history(tmp_path, qapp, no_modal_dialogs)
    try:
        window.project.set_character("f", "FISH", "")
        window.save("第一版")
        window.project.set_character("t", "Teiresias", "")
        window.save("第二版")

        step = window.steps[4]
        step.refresh()
        assert step.history.rowCount() == 2
        assert step.history.item(0, 2).text() == "第二版"       # newest first
        assert step.history.item(1, 2).text() == "第一版"
        assert "共 2 个版本" in step.history_hint.text()
    finally:
        window.project.dirty = False
        window.close()


def test_history_panel_explains_a_plain_plot(studio, tmp_path):
    step = studio.steps[4]
    step.refresh()
    assert step.history.rowCount() == 0
    assert ".tscpkgs" in step.history_hint.text()


def test_restoring_from_the_panel(tmp_path, qapp, no_modal_dialogs):
    window = _project_with_history(tmp_path, qapp, no_modal_dialogs)
    try:
        window.project.set_character("f", "FISH", "")
        window.save("v1")
        window.project.set_character("t", "Teiresias", "")
        window.save("v2")

        step = window.steps[4]
        step.refresh()
        step.history.selectRow(1)          # v1
        step.restore_version()

        assert list(window.project.characters) == ["f"]
        assert "R000001" in window.status.text()
    finally:
        window.project.dirty = False
        window.close()


def test_export_strips_history_and_the_player_still_reads_it(tmp_path, qapp, no_modal_dialogs):
    window = _project_with_history(tmp_path, qapp, no_modal_dialogs)
    try:
        window.project.set_character("f", "FISH", "")
        name = window.project.new_script()
        window.project.add_event(name, Dialogue("f", "你好", [0.1, 0.2]))
        window.save("v1")

        from tscp_player import archive

        handout = window.project.export(tmp_path / "out")
        members = archive.members(handout)
        assert not any(m.startswith("History/") for m in members)
        package = load_archive_package(handout, cache_root=tmp_path / "cache")
        assert package.characters["f"].name == "FISH"
    finally:
        window.project.dirty = False
        window.close()


def test_window_title_shows_whether_history_is_on(studio):
    assert "[剧情包]" in studio.windowTitle()
    assert studio.project.has_history is False


# --------------------------------------------------------------------------
# supplements in the studio
# --------------------------------------------------------------------------

def test_note_button_adds_a_supplement(studio, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialog

    from tscp_player.format import note_parts

    window = studio
    name = _story(window, tmp_path)
    step = window.steps[1]

    class FakeNote:
        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def value(self):
            return make_note("一句补充说明", color="#ffd166", seconds=4.0)

    monkeypatch.setattr("Studio.Main.NoteDialog", FakeNote)
    step.add_note()

    inserted = window.project.script(name).lines[-1]
    assert note_parts(inserted) == ("#ffd166", 4.0, "一句补充说明")
    assert step.table.item(step.table.rowCount() - 1, 1).text() == "补充内容"
    assert step.table.item(step.table.rowCount() - 1, 3).text() == "一句补充说明（4 秒）"


def test_note_dialog_round_trips_an_existing_note(studio):
    from Studio.Main import NoteDialog
    from tscp_player.format import note_parts

    event = make_note("改之前", color="#33cccc", seconds=2.5)
    dialog = NoteDialog(studio, event)
    assert dialog.text_edit.text() == "改之前"
    assert dialog.seconds.value() == pytest.approx(2.5)
    assert dialog._colour == "#33cccc"
    assert note_parts(dialog.value()) == ("#33cccc", 2.5, "改之前")

    dialog.text_edit.setText("改之后")
    dialog.use_default_colour()
    assert note_parts(dialog.value()) == ("", 2.5, "改之后")


def test_note_dialog_needs_some_text(studio, messages):
    from Studio.Main import NoteDialog

    dialog = NoteDialog(studio)
    dialog.accept()
    assert any("不能为空" in text for _kind, text in messages)
    assert dialog.result() != dialog.DialogCode.Accepted


def test_supplements_do_not_count_as_untimed(studio, tmp_path):
    """An aside carries no per-character timing, so it must not skew the check."""

    window = studio
    name = _story(window, tmp_path)
    window.project.add_event(name, make_note("旁注", seconds=3.0))
    summary = window.project.summary()
    assert summary["timed"] == 0
    assert summary["timed_total"] == 12          # 6 + 6 from the two lines
    assert summary["untimed_scripts"] == ["序章.tscp"]


def test_a_timed_script_with_a_supplement_is_still_complete(studio):
    window = studio
    window.project.set_character("f", "FISH", "")
    name = window.project.new_script()
    window.project.add_event(name, Dialogue("f", "甲乙", [0.1, 0.2]))
    window.project.add_event(name, make_note("旁注", seconds=3.0))
    summary = window.project.summary()
    assert summary["timed"] == summary["timed_total"] == 2
    assert summary["untimed_scripts"] == []


def test_supplements_survive_save_and_export(studio, tmp_path):
    from tscp_player.format import note_parts

    window = studio
    name = _story(window, tmp_path)
    window.project.add_event(name, make_note("存得住", color="#ffd166", seconds=3.5))
    window.save()

    reloaded = StudioProject.load(window.project.path)
    assert note_parts(reloaded.scripts[name].lines[-1]) == ("#ffd166", 3.5, "存得住")

    handout = window.project.export(tmp_path / "handout")
    package = load_archive_package(handout, cache_root=tmp_path / "cache")
    assert package.script_names() == [name]
    exported = read_script(handout, name)
    assert note_parts(exported.lines[-1]) == ("#ffd166", 3.5, "存得住")


# --------------------------------------------------------------------------
# the timing step: record / timeline / numbers
# --------------------------------------------------------------------------

def _timed_story(window, tmp_path):
    """A script with real timings, a music track and lyrics."""

    project = window.project
    project.set_character("f", "FISH", "\033[1;33m")
    project.add_music([MusicDraft(
        abbreviation="iw",
        source=_song(tmp_path),
        kind="lyrics",
        lyrics_text="[00:00.50]第一句\n[00:01.50]Second line\n[00:02.50]第三句\n",
        color="#ffd166",
    )])
    name = project.new_script("序章")
    project.add_event(name, Directive("p", "iw"))
    project.add_event(name, Dialogue("f", "你好世界", [0.5] * 4))
    project.add_event(name, Directive("s", "1"))
    project.add_event(name, Dialogue(None, "旁白一句", [0.25] * 4))
    project.add_event(name, make_note("旁注", color="#ffd166", seconds=3.0))
    window._refresh_all()
    return name


def test_the_timing_step_offers_three_ways_to_edit(studio):
    from Studio.Main import VIEW_NUMBERS, VIEW_RECORD, VIEW_TIMELINE

    step = studio.steps[2]
    modes = [step.view_combo.itemData(i) for i in range(step.view_combo.count())]
    assert modes == [VIEW_RECORD, VIEW_TIMELINE, VIEW_NUMBERS]
    assert step.pages.count() == 3
    # Selecting a mode shows its page.
    for index in range(3):
        step.view_combo.setCurrentIndex(index)
        assert step.pages.currentIndex() == index


def test_the_timeline_loads_the_script_and_its_lyrics(studio, tmp_path):
    window = studio
    name = _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.view_combo.setCurrentIndex(1)

    view = step.timeline
    assert view.script is not None and len(view.script.lines) == 5
    assert view.characters["f"].name == "FISH"
    assert "iw" in view.tracks
    # Every lyric line is loaded, so the music lane can mark them.
    assert [line.text for line in view.lyrics["iw"]] == ["第一句", "Second line", "第三句"]
    # 4 * 0.5 + 1 + 4 * 0.25 = 4.0 seconds of story.
    assert view.total_seconds == pytest.approx(4.0)
    assert view._starts == pytest.approx([0.0, 0.0, 2.0, 3.0, 4.0])


def test_the_timeline_paints_without_complaining(studio, tmp_path):
    window = studio
    _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.view_combo.setCurrentIndex(1)
    step.timeline.resize(900, 260)
    step.timeline.show()
    QApplication.instance().processEvents()
    image = step.timeline.grab().toImage()
    assert image.width() > 100
    # Something was actually drawn, not just the backdrop.
    from Studio.timeline import BACKDROP

    colours = {
        image.pixelColor(x, y).name()
        for x in range(0, image.width(), 17)
        for y in range(0, image.height(), 11)
    }
    assert len(colours) > 1
    assert colours != {BACKDROP}


def test_set_duration_scales_the_existing_timing(studio, tmp_path):
    window = studio
    name = _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)

    assert step._duration_of(1) == pytest.approx(2.0)
    assert step.set_duration(1, 4.0) is True

    line = window.project.script(name).lines[1]
    # Doubling the total doubles every character's share.
    assert sum(line.delays) == pytest.approx(4.0)
    assert line.delays == pytest.approx([1.0] * 4)
    assert window.project.dirty is True
    # Its neighbours are untouched.
    assert step._duration_of(3) == pytest.approx(1.0)


def test_set_duration_on_an_untimed_line_spreads_evenly(studio, tmp_path):
    window = studio
    window.project.set_character("f", "FISH", "")
    name = window.project.new_script()
    window.project.add_event(name, Dialogue("f", "四个字啊"))
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)

    assert step._duration_of(0) == 0.0
    assert step.set_duration(0, 2.0) is True
    assert window.project.script(name).lines[0].delays == pytest.approx([0.5] * 4)


def test_set_duration_ignores_things_that_are_not_dialogue(studio, tmp_path):
    window = studio
    name = _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)
    assert step.set_duration(0, 5.0) is False        # a music directive
    assert step.set_duration(2, 5.0) is False        # a sleep
    assert step.set_duration(99, 5.0) is False       # out of range
    assert window.project.script(name).lines[2] == Directive("s", "1")


def test_dragging_a_block_changes_the_speed(studio, tmp_path):
    """Drive the real drag: press on the edge, move, release."""

    from PySide6.QtCore import QPointF, Qt

    window = studio
    name = _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)
    step.view_combo.setCurrentIndex(1)

    view = step.timeline
    view.resize(900, 260)
    view.show()
    QApplication.instance().processEvents()
    view.fit()

    # Grab the right edge of line 1, which ends at 2.0 s.
    rect = view._block_rect(1)
    assert view._handle_at(rect.right(), rect.center().y()) == 1

    target = view.x_for(3.5)
    view.set_scale(view.scale)
    press = _mouse(view, QPointF(rect.right(), rect.center().y()),
                   Qt.MouseButton.LeftButton, "press")
    move = _mouse(view, QPointF(target, rect.center().y()),
                  Qt.MouseButton.LeftButton, "move")
    release = _mouse(view, QPointF(target, rect.center().y()),
                     Qt.MouseButton.LeftButton, "release")
    assert press and move and release

    assert step._duration_of(1) == pytest.approx(3.5, abs=0.05)
    # Everything after it shifts, so the total grows.
    assert step.timeline.total_seconds == pytest.approx(5.5, abs=0.05)


def _mouse(widget, position, button, kind):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    types = {
        "press": QEvent.Type.MouseButtonPress,
        "move": QEvent.Type.MouseMove,
        "release": QEvent.Type.MouseButtonRelease,
    }
    event = QMouseEvent(
        types[kind], QPointF(position), widget.mapToGlobal(QPointF(position)),
        button, button, Qt.KeyboardModifier.NoModifier,
    )
    if kind == "press":
        widget.mousePressEvent(event)
    elif kind == "move":
        widget.mouseMoveEvent(event)
    else:
        widget.mouseReleaseEvent(event)
    return True


def test_clicking_a_block_selects_the_row(studio, tmp_path):
    from PySide6.QtCore import QPointF, Qt

    window = studio
    name = _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)
    step.view_combo.setCurrentIndex(1)
    view = step.timeline
    view.resize(900, 260)
    view.show()
    QApplication.instance().processEvents()

    middle = view._block_rect(1).center()
    _mouse(view, QPointF(middle), Qt.MouseButton.LeftButton, "press")
    assert view.selected == 1
    assert step.table.selectionModel().selectedRows()[0].row() == 1
    assert step.numbers.selectionModel().selectedRows()[0].row() == 1


def test_the_numbers_editor_shows_and_writes_durations(studio, tmp_path):
    from Studio.Main import VIEW_NUMBERS

    window = studio
    name = _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)
    step.view_combo.setCurrentIndex(2)
    assert step.view_combo.currentData() == VIEW_NUMBERS

    assert step.numbers.rowCount() == 5
    assert step.numbers.item(1, 4).text() == "2.00"
    assert step.numbers.item(3, 4).text() == "1.00"
    # Non-dialogue rows cannot be edited.
    assert not (step.numbers.item(0, 4).flags() & Qt.ItemFlag.ItemIsEditable)

    step.numbers.item(1, 4).setText("3.00")
    assert step._duration_of(1) == pytest.approx(3.0)
    assert step.numbers.item(1, 4).text() == "3.00"


def test_the_numbers_editor_rejects_nonsense(studio, tmp_path):
    window = studio
    name = _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)
    step.view_combo.setCurrentIndex(2)

    before = step._duration_of(1)
    step.numbers.item(1, 4).setText("不是数字")
    assert step._duration_of(1) == pytest.approx(before)
    assert "数字" in step.status.text()

    step.numbers.item(1, 4).setText("-4")
    assert step._duration_of(1) == pytest.approx(before)
    assert "负数" in step.status.text()


def test_zoom_and_fit_keep_the_timeline_usable(studio, tmp_path):
    from Studio.timeline import MAX_SCALE, MIN_SCALE

    window = studio
    _timed_story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.view_combo.setCurrentIndex(1)
    step.timeline.resize(900, 260)

    step.zoom.setValue(200)
    assert step.timeline.scale == pytest.approx(200.0)
    step._fit_timeline()
    assert MIN_SCALE <= step.timeline.scale <= MAX_SCALE
    # Fitting a four second story into ~900px lands around 220 px/s.
    assert step.timeline.scale > 100


def test_the_timeline_survives_an_empty_project(studio):
    step = studio.steps[2]
    step.view_combo.setCurrentIndex(1)
    assert step.timeline.script is None
    assert step.timeline.total_seconds == 0.0
    step.timeline.resize(400, 200)
    step.timeline.grab()
    step.view_combo.setCurrentIndex(2)
    assert step.numbers.rowCount() == 0


def test_a_broken_lrc_does_not_break_the_timeline(studio, tmp_path):
    window = studio
    project = window.project
    project.set_character("f", "FISH", "")
    project.add_music([MusicDraft(
        abbreviation="iw", source=_song(tmp_path), kind="lyrics",
        lyrics_text="这一行根本不是 LRC\n", color="#ffd166",
    )])
    name = project.new_script()
    project.add_event(name, Dialogue("f", "你好", [0.1, 0.1]))
    window._refresh_all()
    step = window.steps[2]
    step.refresh()
    step.view_combo.setCurrentIndex(1)
    assert step.timeline.script is not None
    assert step.timeline.lyrics.get("iw", []) == []


# --------------------------------------------------------------------------
# script names never expose the extension
# --------------------------------------------------------------------------

def test_script_name_dialog_shows_the_bare_name(studio):
    from Studio.Main import ScriptNameDialog

    dialog = ScriptNameDialog(studio, "新建剧本", "序章")
    assert dialog.name_edit.text() == "序章"
    assert dialog.value() == "序章"
    assert "序章.tscp" in dialog.hint.text()
    # A typed extension is simply understood, not doubled up.
    dialog.name_edit.setText("第一章.tscp")
    assert dialog.value() == "第一章"


def test_script_name_dialog_refuses_an_illegal_name(studio, messages):
    from Studio.Main import ScriptNameDialog

    dialog = ScriptNameDialog(studio, "新建剧本", "序章")
    dialog.name_edit.setText("a/b")
    dialog.accept()
    assert any("不能包含" in text for _kind, text in messages)
    # Refused, so the dialog is still open (never accepted).
    assert dialog.result() != dialog.DialogCode.Accepted


def test_new_script_uses_the_name_dialog(studio, monkeypatch):
    from Studio.Main import ScriptNameDialog

    window = studio

    class AutoAccept(ScriptNameDialog):
        def exec(self):
            self.name_edit.setText("第二幕")
            return self.DialogCode.Accepted

    monkeypatch.setattr("Studio.Main.ScriptNameDialog", AutoAccept)
    window.steps[1].new_script()
    assert "第二幕.tscp" in window.project.scripts


# --------------------------------------------------------------------------
# characters are picked by name
# --------------------------------------------------------------------------

def test_dialogue_dialog_lists_characters_by_name(studio, tmp_path):
    from Studio.Main import DialogueDialog

    window = studio
    _story(window, tmp_path)
    dialog = DialogueDialog(window, window, None, narrator=False)
    labels = [dialog.character_combo.itemText(i) for i in range(dialog.character_combo.count())]
    assert labels == ["FISH", "Teiresias"]
    # ...while each entry still carries the file key.
    assert dialog.character_combo.itemData(0) == "f"
    dialog.text_edit.setPlainText("你好")
    assert dialog.values() == Dialogue("f", "你好")


def test_dialogue_dialog_preselects_the_existing_character(studio, tmp_path):
    from Studio.Main import DialogueDialog

    window = studio
    _story(window, tmp_path)
    dialog = DialogueDialog(window, window, Dialogue("t", "旧台词"), narrator=False)
    assert dialog.values().character == "t"
    assert dialog.text() == "旧台词"


def test_narration_is_a_single_line_and_enter_commits(studio, tmp_path):
    from PySide6.QtWidgets import QLineEdit

    from Studio.Main import DialogueDialog

    window = studio
    _story(window, tmp_path)
    dialog = DialogueDialog(window, window, None, narrator=True)
    # One line of narration, and Enter finishes it.
    assert isinstance(dialog.text_edit, QLineEdit)
    dialog.text_edit.setText("雨声盖过了广播。")
    dialog.text_edit.returnPressed.emit()
    assert dialog.result() == dialog.DialogCode.Accepted
    assert dialog.values() == Dialogue(None, "雨声盖过了广播。")


def test_dialogue_inserts_a_coloured_character_name(studio, tmp_path):
    from PySide6.QtGui import QTextCursor

    from Studio.Main import DialogueDialog

    window = studio
    _story(window, tmp_path)
    dialog = DialogueDialog(window, window, None, narrator=False)
    dialog.text_edit.setPlainText("说：")
    dialog.text_edit.moveCursor(QTextCursor.MoveOperation.End)
    dialog.name_combo.setCurrentIndex(1)          # FISH
    dialog.insert_name()

    text = dialog.text_edit.toPlainText()
    assert text == "说：\033[1;33mFISH\033[0m"
    # The preview renders the colour rather than the escape codes.
    assert "#cccc33" in dialog.preview.text() or "font-weight:bold" in dialog.preview.text()
    assert "\\033" not in dialog.preview.text()


def test_narration_can_also_take_a_coloured_name(studio, tmp_path):
    from Studio.Main import DialogueDialog

    window = studio
    _story(window, tmp_path)
    dialog = DialogueDialog(window, window, None, narrator=True)
    dialog.name_combo.setCurrentIndex(1)
    dialog.insert_name()
    assert dialog.text() == "\033[1;33mFISH\033[0m"


def test_music_dialog_lists_tracks_by_file(studio, tmp_path):
    from Studio.Main import MusicDialog

    window = studio
    _story(window, tmp_path)                       # adds song.flac under key "iw"
    dialog = MusicDialog(window, window, "iw")
    labels = [dialog.combo.itemText(i) for i in range(dialog.combo.count())]
    assert labels[0] == "（停止音乐）"
    assert labels[1] == "♪ song"
    assert dialog.value() == "iw"


# --------------------------------------------------------------------------
# automatic snapshots
# --------------------------------------------------------------------------

def test_auto_snapshot_records_then_backs_off(tmp_path, qapp, no_modal_dialogs):
    window = _project_with_history(tmp_path, qapp, no_modal_dialogs)
    try:
        window.project.set_character("f", "FISH", "")
        window.save("起点")

        # A real change is snapshotted and the interval resets.
        window.project.set_character("t", "Teiresias", "")
        window._auto_snapshot()
        assert window._backoff == 1
        assert window.project.revisions()[0]["AUTO"] is True

        # Nothing changed this time, so the next look is further away.
        before = len(window.project.revisions())
        window._auto_snapshot()
        assert window._backoff == 2
        assert len(window.project.revisions()) == before
        assert "没有改动" in window.status.text()
        assert window._autosave.interval() == 300 * 2 * 1000
    finally:
        window.project.dirty = False
        window.close()


def test_auto_snapshot_backs_off_all_the_way_up_to_the_cap(tmp_path, qapp, no_modal_dialogs):
    from Studio.Main import AUTO_SNAPSHOT_MAX_BACKOFF

    window = _project_with_history(tmp_path, qapp, no_modal_dialogs)
    try:
        window.project.set_character("f", "FISH", "")
        window.save("起点")
        for _ in range(10):
            window._auto_snapshot()
        assert window._backoff == AUTO_SNAPSHOT_MAX_BACKOFF
    finally:
        window.project.dirty = False
        window.close()


def test_auto_snapshot_survives_having_no_project(studio):
    """No project open: the timer must idle quietly, not raise."""

    from Studio.Main import AUTO_SNAPSHOT_MAX_BACKOFF

    studio.project = None
    studio._auto_snapshot()
    assert studio._backoff == AUTO_SNAPSHOT_MAX_BACKOFF


def test_autosave_can_be_switched_off(studio):
    assert studio._autosave.isActive() is True
    studio.set_autosave(False)
    assert studio._autosave.isActive() is False
    assert "已关闭" in studio.status.text()
    studio.set_autosave(True)
    assert studio._autosave.isActive() is True


def test_the_autosave_menu_item_reflects_the_state(studio):
    assert studio.autosave_action.isChecked() is True
    studio.autosave_action.setChecked(False)
    assert studio._autosave_enabled is False


def test_batch_add_dialog_accumulates_before_committing(studio):
    """The old generator's batch flow: keep filling fields, commit once.

    Only the name is typed now — the file key is derived and kept unique.
    """

    from Studio.Main import BatchAddDialog

    window = studio
    window.project.set_character("t", "Teiresias", "")     # already in the project
    dialog = BatchAddDialog(window, window.project.characters)

    for name in ("FISH", "Teiresias", "Guide"):
        dialog.fields.name_edit.setText(name)
        dialog.add_row()

    assert [row[1] for row in dialog.rows] == ["FISH", "Teiresias", "Guide"]
    keys = [row[0] for row in dialog.rows]
    assert len(set(keys)) == 3, keys
    # A name the project already knows keeps its existing key.
    assert keys[1] == "t"
    assert keys[0] != "t"
    assert dialog.table.rowCount() == 3
    # The known one is flagged as an update of what is already there.
    assert "（更新）" in dialog.table.item(1, 0).text()

    # Adding a staged name again updates that row instead of duplicating it.
    dialog.fields.name_edit.setText("FISH")
    dialog.add_row()
    assert [row[1] for row in dialog.rows] == ["FISH", "Teiresias", "Guide"]
    assert dialog.fields.name_edit.text() == ""      # fields cleared, ready again

    window.project.merge_characters(dialog.rows)
    names = {key: value.name for key, value in window.project.characters.items()}
    assert names["t"] == "Teiresias"
    assert names[keys[2]] == "Guide"


def test_batch_add_dialog_can_remove_a_row(studio):
    from Studio.Main import BatchAddDialog

    dialog = BatchAddDialog(studio)
    for name in ("FISH", "Teiresias"):
        dialog.fields.name_edit.setText(name)
        dialog.add_row()
    dialog.table.selectRow(0)
    dialog.remove_row()
    assert [row[1] for row in dialog.rows] == ["Teiresias"]


def test_batch_add_dialog_derives_the_key_from_the_name(studio):
    from Studio.Main import BatchAddDialog

    dialog = BatchAddDialog(studio)
    dialog.fields.name_edit.setText("Bister")
    dialog.fields.custom_edit.setText(r"\033[33m")
    dialog.add_row()
    key, name, style = dialog.rows[0]
    assert name == "Bister"
    assert key == "bist"
    assert style == "\033[33m"
    # The same name twice is the same character, not a second one.
    dialog.fields.name_edit.setText("Bister")
    dialog.add_row()
    assert [row[0] for row in dialog.rows] == ["bist"]


def test_editing_keeps_the_existing_key(studio):
    """Renaming a character must not silently rewrite every reference."""

    from Studio.Main import CharacterDialog

    window = studio
    window.project.set_character("f", "FISH", "")
    dialog = CharacterDialog(window, "f", "FISH", "", taken=["f"])
    assert dialog.fields.manual_key.isChecked() is True
    assert dialog.fields.resolved_key() == "f"
    dialog.fields.name_edit.setText("FISH 改")
    assert dialog.fields.resolved_key() == "f"


def test_characters_step_imports_a_json_file(studio, tmp_path, monkeypatch):
    import json as json_module

    window = studio
    payload = tmp_path / "characters.json"
    payload.write_text(
        json_module.dumps({"f": {"NAME": "FISH", "STYLE": "\u001b[33m"}}),
        encoding="utf-8",
    )
    _patch_open_file(monkeypatch, payload)

    window.steps[0].import_json()
    assert window.project.characters["f"].name == "FISH"
    assert window.project.characters["f"].style == "\033[33m"
    assert "新增 1 个" in window.status.text()


def test_characters_batch_dialog_parses_pasted_rows(studio):
    from Studio.Main import BatchCharactersDialog

    window = studio
    dialog = BatchCharactersDialog(window)
    dialog.editor.setPlainText("f\tFISH\t\\033[33m\nt\tTeiresias\n")
    rows = studio_model.parse_character_text(dialog.text())
    window.project.merge_characters(rows)
    assert window.project.character_rows() == [
        ("f", "FISH", "\033[33m"),
        ("t", "Teiresias", ""),
    ]


def test_story_step_imports_a_script_file(studio, tmp_path, monkeypatch):
    window = studio
    draft = tmp_path / "chapter.tscps"
    draft.write_text("[f]第一章\n旁白\n", encoding="utf-8")
    _patch_open_file(monkeypatch, draft)

    window.steps[1].import_script()
    assert "chapter.tscp" in window.project.scripts
    assert window.steps[1].script_combo.currentText() == "chapter.tscp"
    assert window.project.scripts["chapter.tscp"].lines[0].text == "第一章"


def test_timing_step_imports_timing_from_a_tscp(studio, tmp_path, monkeypatch):
    window = studio
    name = _story(window, tmp_path)
    step = window.steps[2]
    step.refresh()
    step.script_combo.setCurrentText(name)
    step._reload_events()

    recorded = tmp_path / "recorded.tscp"
    recorded.write_text(
        "TSCP 1\nP|iw\nD|f|%s|%s\nS|0\nN|%s|%s\n"
        % (
            _b64("Hold"), ",".join(["0.1"] * 4),
            _b64("Aside"), ",".join(["0.2"] * 5),
        ),
        encoding="utf-8",
    )
    # The project's line differs, so nothing should be copied over silently.
    _patch_open_file(monkeypatch, recorded)
    step.import_timing()
    # The timing step reports into its own status label, next to the table.
    assert "被跳过" in step.status.text()

    # Now make a script whose text matches the file, and import again.
    matching = window.project.new_script("matching")
    window.project.add_event(matching, Directive("p", "iw"))
    window.project.add_event(matching, Dialogue("f", "Hold", []))
    window.project.add_event(matching, Directive("s", "0"))
    window.project.add_event(matching, Dialogue(None, "Aside", []))
    step.refresh()
    step.script_combo.setCurrentText(matching)
    step._reload_events()
    step.import_timing()

    lines = window.project.script(matching).lines
    assert lines[1].delays == pytest.approx([0.1] * 4)
    assert lines[3].delays == pytest.approx([0.2] * 5)
    assert "套用了 2 句" in step.status.text()


def test_lyrics_step_imports_a_plain_text_file(studio, tmp_path, monkeypatch):
    window = studio
    _story(window, tmp_path)
    step = window.steps[3]
    step.refresh()
    step.table.selectRow(0)

    lyrics = tmp_path / "lyrics.txt"
    lyrics.write_text("第一句\n# 注释\n第二句\n", encoding="utf-8")
    _patch_open_file(monkeypatch, lyrics)

    step.import_lyrics()
    assert step.kind_lyrics.isChecked() is True
    assert "第一句" in step.lyrics_edit.toPlainText()
    assert "录制" in window.status.text()


def test_lyrics_step_imports_an_lrc(studio, tmp_path, monkeypatch):
    window = studio
    _story(window, tmp_path)
    step = window.steps[3]
    step.refresh()
    step.table.selectRow(0)

    lrc = tmp_path / "song.lrc"
    lrc.write_text("[00:01.00]第一句\n[00:03.50]Second line\n", encoding="utf-8")
    _patch_open_file(monkeypatch, lrc)

    step.import_lyrics()
    step.apply_lyrics()
    assert "第一句" in window.project.lyrics_text("iw")


def test_export_step_imports_a_whole_plot(studio, tmp_path, monkeypatch):
    window = studio
    _story(window, tmp_path)

    donor = studio_model.StudioProject.create(
        tmp_path / "donor.tscpkg", name="Donor", description=""
    )
    donor.set_character("t", "Teiresias", "\033[36m")
    donor_script = donor.new_script("act")
    donor.add_event(donor_script, Dialogue("t", "外来台词", [0.1, 0.2, 0.3, 0.4]))
    donor.save()

    monkeypatch.setattr(window, "pick_import_source", lambda: donor.path)
    window.steps[4].import_from_source()

    assert "t" in window.project.characters
    assert "act.tscp" in window.project.scripts
    assert "导入 1 个角色" in window.status.text()


def test_import_helper_reports_notes(studio, tmp_path, monkeypatch):
    """A track declared as lyrics without a .lrc must not fail the import."""

    window = studio
    root = tmp_path / "legacy"
    (root / "Scripts").mkdir(parents=True)
    (root / "Musics").mkdir(parents=True)
    (root / "Scripts" / "__init__.json").write_text(
        json.dumps({"CHARACTERS": {"f": {"NAME": "FISH"}}}), encoding="utf-8"
    )
    (root / "Musics" / "song.flac").write_bytes(b"FLAC" * 16)
    (root / "Musics" / "__init__.json").write_text(
        json.dumps({
            "VERSION": "0.0.1",
            "CONFIG": {"iw": "song.flac"},
            "TRACKS": {"iw": {"KIND": "lyrics", "LYRICS": "gone.lrc"}},
        }),
        encoding="utf-8",
    )

    window.import_everything(root)
    assert "纯音乐" in window.status.text()
    assert window.project.tracks["iw"].instrumental is True
