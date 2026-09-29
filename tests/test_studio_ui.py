"""Offscreen tests for the unified studio window.

These drive the real widgets, because the wiring between the steps is exactly
what the dependency-free project tests cannot cover.
"""

import pytest

from PlotManager.model import MusicDraft
from Studio import model as studio_model
from tscp_player.format import Dialogue, Directive
from tscp_player.plot import load_archive_package

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

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

    def _discard(*_args, **_kwargs):
        return QMessageBox.StandardButton.Discard

    def _no(*_args, **_kwargs):
        return QMessageBox.StandardButton.No

    for name, replacement in (
        ("information", _ok), ("warning", _ok), ("critical", _ok),
        ("question", _discard), ("about", _ok),
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
    assert [step.table.item(row, 0).text() for row in range(2)] == ["f", "t"]
    assert step.table.item(0, 1).text() == "FISH"
    # The style column shows the escaped spelling, not a real ESC byte.
    assert step.table.item(0, 2).text() == r"\033[1;33m"


def test_story_step_labels_every_row_kind(studio, tmp_path):
    window = studio
    name = _story(window, tmp_path)
    step = window.steps[1]

    assert step.script_combo.currentText() == name
    assert step.table.rowCount() == 4
    assert [step.table.item(row, 1).text() for row in range(4)] == [
        "播放音乐", "角色对白", "暂停", "旁白",
    ]
    assert step.table.item(1, 2).text() == "f"
    assert step.table.item(1, 3).text() == "你好，世界！"
    assert step.table.item(2, 3).text() == "0 秒"
    assert step.table.item(3, 2).text() == ""


def test_story_step_offers_every_editing_action(studio):
    step = studio.steps[1]
    assert set(step.action_buttons) == {
        "添加对白", "添加旁白", "添加暂停", "清空屏幕",
        "插入音乐", "编辑", "上移", "下移", "删除",
    }


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
