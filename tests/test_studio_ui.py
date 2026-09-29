"""Offscreen tests for the unified studio window.

These drive the real widgets, because the wiring between the steps is exactly
what the dependency-free project tests cannot cover.
"""

import json

import pytest

from PlotManager.model import MusicDraft
from Studio import model as studio_model
from Studio.model import StudioProject
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
        "批量添加…", "粘贴导入…", "从 JSON 导入…", "从剧情包导入…",
    } == set(step.buttons)


def test_batch_add_dialog_accumulates_before_committing(studio):
    """The old generator's batch flow: keep filling fields, commit once."""

    from Studio.Main import BatchAddDialog

    window = studio
    window.project.set_character("t", "旧名字", "")
    dialog = BatchAddDialog(window, window.project.characters)

    for key, name in (("f", "FISH"), ("t", "Teiresias"), ("g", "Guide")):
        dialog.fields.key_edit.setText(key)
        dialog.fields.name_edit.setText(name)
        dialog.add_row()

    # Adding a second time updates in place rather than duplicating.
    dialog.fields.key_edit.setText("f")
    dialog.fields.name_edit.setText("FISH 改")
    dialog.add_row()

    assert [row[0] for row in dialog.rows] == ["f", "t", "g"]
    assert dialog.rows[0][1] == "FISH 改"
    assert dialog.table.rowCount() == 3
    assert "（更新）" in dialog.table.item(1, 1).text()
    # The fields are emptied, ready for the next name.
    assert dialog.fields.key_edit.text() == ""

    window.project.merge_characters(dialog.rows)
    assert window.project.characters["g"].name == "Guide"
    assert window.project.characters["t"].name == "Teiresias"
    assert window.project.characters["f"].name == "FISH 改"


def test_batch_add_dialog_can_remove_a_row(studio):
    from Studio.Main import BatchAddDialog

    dialog = BatchAddDialog(studio)
    for key, name in (("f", "FISH"), ("t", "Teiresias")):
        dialog.fields.key_edit.setText(key)
        dialog.fields.name_edit.setText(name)
        dialog.add_row()
    dialog.table.selectRow(0)
    dialog.remove_row()
    assert [row[0] for row in dialog.rows] == ["t"]


def test_batch_add_dialog_derives_a_style_from_the_colour(studio):
    from Studio.Main import BatchAddDialog

    dialog = BatchAddDialog(studio)
    dialog.fields.key_edit.setText("f")
    dialog.fields.name_edit.setText("FISH")
    dialog.fields.custom_edit.setText(r"\033[33m")
    dialog.add_row()
    assert dialog.rows[0][2] == "\033[33m"


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
