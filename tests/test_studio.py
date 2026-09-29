"""Tests for the unified studio project layer (no Qt needed)."""

import io

import pytest

from PlotManager import model
from Studio import model as studio_model
from Studio.model import (
    CLEAR,
    DIALOGUE,
    MUSIC,
    NARRATION,
    SLEEP,
    StudioError,
    StudioProject,
    describe_event,
    event_kind,
    is_script_timed,
    missing_characters,
    next_script_name,
    normalise_script,
    safe_script_name,
    script_characters,
    timing_progress,
)
from tscp_player.format import Dialogue, Directive, Script, visible_text_length
from tscp_player.plot import load_archive_package


def _project(tmp_path, name="工坊"):
    return StudioProject.create(tmp_path / "demo.tscpkg", name=name, description="简介")


def _song(tmp_path, name="song.flac"):
    path = tmp_path / name
    path.write_bytes(b"FLAC" * 64)
    return path


# --------------------------------------------------------------------------
# pure helpers
# --------------------------------------------------------------------------

def test_event_kind_and_labels_cover_all_five_rows():
    assert event_kind(Dialogue("f", "你好")) == DIALOGUE
    assert event_kind(Dialogue(None, "旁白")) == NARRATION
    assert event_kind(Directive("s", "1.5")) == SLEEP
    assert event_kind(Directive("c")) == CLEAR
    assert event_kind(Directive("p", "iw")) == MUSIC
    assert [studio_model.event_label(item) for item in (
        Dialogue("f", "a"), Dialogue(None, "b"), Directive("s", "1"),
        Directive("c"), Directive("p", "iw"),
    )] == ["角色对白", "旁白", "暂停", "清空屏幕", "播放音乐"]


def test_describe_event():
    assert describe_event(Dialogue("f", "你好")) == "你好"
    assert describe_event(Directive("s", "1.5")) == "1.5 秒"
    assert describe_event(Directive("p", "iw")) == "iw"
    assert describe_event(Directive("p", "")) == "（停止）"
    assert describe_event(Directive("c")) == "—"


def test_normalise_script_pads_and_truncates_delays():
    script = Script([
        Dialogue("f", "你好", [0.5]),            # too few
        Dialogue("f", "ab", [0.1, 0.2, 0.3]),    # too many
        Directive("s", "1"),
    ])
    fixed = normalise_script(script)
    assert fixed.lines[0].delays == [0.5, 0.0]
    assert fixed.lines[1].delays == [0.1, 0.2]
    assert fixed.lines[2] == Directive("s", "1")


def test_normalise_script_clamps_negative_delays():
    fixed = normalise_script(Script([Dialogue(None, "甲", [-2.0])]))
    assert fixed.lines[0].delays == [0.0]


def test_timing_progress_counts_only_recorded_scripts():
    untimed = Script([Dialogue("f", "甲乙")])
    assert timing_progress(untimed) == (0, 2)
    assert is_script_timed(untimed) is False

    timed = Script([Dialogue("f", "甲乙", [0.1, 0.2])])
    assert timing_progress(timed) == (2, 2)
    assert is_script_timed(timed) is True


def test_timing_progress_treats_a_wrong_length_as_untimed():
    broken = Script([Dialogue("f", "甲乙", [0.1])])
    assert is_script_timed(broken) is False


def test_script_characters_and_missing_characters():
    script = Script([Dialogue("f", "A"), Dialogue(None, "旁白"), Dialogue("t", "B")])
    assert script_characters(script) == ["f", "t"]
    from tscp_player.plot import Character

    assert missing_characters(script, {"f": Character("F", "")}) == ["t"]
    assert missing_characters(script, {"f": Character("F", ""), "t": Character("T", "")}) == []


def test_next_script_name_avoids_collisions():
    assert next_script_name([]) == "plot.tscp"
    assert next_script_name(["plot.tscp"]) == "plot2.tscp"
    assert next_script_name(["plot.tscp", "plot2.tscp"]) == "plot3.tscp"
    assert next_script_name([], base="序章") == "序章.tscp"


def test_safe_script_name():
    assert safe_script_name("第一幕") == "第一幕.tscp"
    assert safe_script_name("act.tscp") == "act.tscp"
    assert safe_script_name("  spaced  ") == "spaced.tscp"
    with pytest.raises(StudioError):
        safe_script_name("   ")


# --------------------------------------------------------------------------
# project: create / load / save round trip
# --------------------------------------------------------------------------

def test_create_writes_a_container_and_marks_it_dirty(tmp_path):
    project = _project(tmp_path)
    assert project.path.is_file()
    assert project.dirty is True
    assert project.name == "工坊"


def test_save_and_reload_round_trips_everything(tmp_path):
    project = _project(tmp_path)
    project.set_character("f", "FISH", "\033[1;33m")
    project.set_character("t", "Teiresias", "")
    filename = project.new_script("序章")
    project.add_event(filename, Dialogue("f", "你好", [0.1, 0.2]))
    project.add_event(filename, Directive("s", "1"))
    project.add_event(filename, Dialogue(None, "旁白", [0.3, 0.4]))
    project.description = "改过的简介"
    project.save()
    assert project.dirty is False

    reloaded = StudioProject.load(project.path)
    assert reloaded.name == "工坊"
    assert reloaded.description == "改过的简介"
    assert {key: item.name for key, item in reloaded.characters.items()} == {
        "f": "FISH", "t": "Teiresias",
    }
    assert reloaded.characters["f"].style == "\033[1;33m"
    assert list(reloaded.scripts) == ["序章.tscp"]
    lines = reloaded.scripts["序章.tscp"].lines
    assert [event_kind(item) for item in lines] == [DIALOGUE, SLEEP, NARRATION]
    assert lines[0].delays == pytest.approx([0.1, 0.2])


def test_saved_container_loads_with_the_desktop_loader(tmp_path):
    project = _project(tmp_path)
    project.set_character("f", "FISH", "")
    filename = project.new_script()
    project.add_event(filename, Dialogue("f", "你好", [0.5, 0.5]))
    project.add_music([model.MusicDraft(abbreviation="iw", source=_song(tmp_path))])
    project.save()

    package = load_archive_package(project.path, cache_root=tmp_path / "cache")
    assert package.name == "工坊"
    assert package.characters["f"].name == "FISH"
    assert package.script_names() == ["plot.tscp"]
    assert package.music_path("iw").read_bytes() == b"FLAC" * 64


def test_save_repairs_a_delay_list_that_no_longer_matches(tmp_path):
    """Editing a line after timing it must not make the whole save fail."""

    project = _project(tmp_path)
    filename = project.new_script()
    project.add_event(filename, Dialogue(None, "甲", [0.2]))
    project.save()
    assert StudioProject.load(project.path).scripts["plot.tscp"].lines[0].delays == [0.2]


def test_export_leaves_the_working_file_alone(tmp_path):
    project = _project(tmp_path)
    project.set_character("f", "FISH", "")
    project.new_script()
    project.save()

    copy = project.export(tmp_path / "handout")
    assert copy.suffix == ".tscpkg"
    assert copy != project.path
    assert load_archive_package(copy).characters["f"].name == "FISH"

    # Editing the copy afterwards does not disturb the original.
    model.update_metadata(copy, name="Handout")
    assert StudioProject.load(project.path).name == "工坊"


def test_load_rejects_a_non_container(tmp_path):
    junk = tmp_path / "junk.tscpkg"
    junk.write_text("not a zip", encoding="utf-8")
    with pytest.raises(StudioError):
        StudioProject.load(junk)


def test_create_refuses_to_overwrite(tmp_path):
    _project(tmp_path)
    with pytest.raises(StudioError):
        _project(tmp_path)


# --------------------------------------------------------------------------
# characters
# --------------------------------------------------------------------------

def test_character_editing(tmp_path):
    project = _project(tmp_path)
    assert project.character_rows() == []

    project.set_character("f", "FISH", "\033[33m")
    project.set_character("f", "FISH 改", "\033[33m")
    assert project.character_rows() == [("f", "FISH 改", "\033[33m")]

    project.remove_character("f")
    assert project.characters == {}

    for bad in (("", "名字"), ("f", ""), ("a]b", "名字")):
        with pytest.raises(StudioError):
            project.set_character(*bad)
    with pytest.raises(StudioError):
        project.remove_character("ghost")


def test_characters_survive_a_save_with_no_scripts(tmp_path):
    project = _project(tmp_path)
    project.set_character("f", "FISH", "\033[1;31m")
    project.save()
    assert StudioProject.load(project.path).characters["f"].style == "\033[1;31m"


# --------------------------------------------------------------------------
# scripts and events
# --------------------------------------------------------------------------

def test_script_management(tmp_path):
    project = _project(tmp_path)
    first = project.new_script()
    second = project.new_script()
    assert (first, second) == ("plot.tscp", "plot2.tscp")

    with pytest.raises(StudioError):
        project.new_script("plot.tscp")

    renamed = project.rename_script(second, "第二幕")
    assert renamed == "第二幕.tscp"
    assert set(project.scripts) == {"plot.tscp", "第二幕.tscp"}

    with pytest.raises(StudioError):
        project.rename_script(second, "plot.tscp")

    project.delete_script(first)
    assert list(project.scripts) == ["第二幕.tscp"]
    with pytest.raises(StudioError):
        project.delete_script(first)


def test_event_editing(tmp_path):
    project = _project(tmp_path)
    name = project.new_script()

    project.add_event(name, Dialogue("f", "一"))
    project.add_event(name, Dialogue("f", "三"))
    project.add_event(name, Dialogue("f", "二"), index=1)
    assert [item.text for item in project.script(name).lines] == ["一", "二", "三"]

    project.replace_event(name, 1, Directive("s", "2"))
    assert isinstance(project.script(name).lines[1], Directive)

    assert project.move_event(name, 0, 1) == 1
    assert project.move_event(name, 0, -1) == 0        # already at the top
    assert project.move_event(name, 2, 1) == 2         # already at the bottom

    project.delete_event(name, 0)
    assert len(project.script(name).lines) == 2

    with pytest.raises(StudioError):
        project.replace_event(name, 99, Directive("c"))
    with pytest.raises(StudioError):
        project.delete_event(name, 99)


def test_operations_on_a_missing_script_are_rejected(tmp_path):
    project = _project(tmp_path)
    for call in (
        lambda: project.script("nope.tscp"),
        lambda: project.add_event("nope.tscp", Directive("c")),
        lambda: project.delete_script("nope.tscp"),
        lambda: project.rename_script("nope.tscp", "x"),
    ):
        with pytest.raises(StudioError):
            call()


# --------------------------------------------------------------------------
# music and lyrics
# --------------------------------------------------------------------------

def test_add_and_remove_music(tmp_path):
    project = _project(tmp_path)
    project.add_music([model.MusicDraft(abbreviation="iw", source=_song(tmp_path))])
    assert project.tracks["iw"].instrumental is True
    project.remove_music("iw")
    assert project.tracks == {}


def test_lyrics_live_in_the_container(tmp_path):
    project = _project(tmp_path)
    project.add_music([model.MusicDraft(
        abbreviation="iw",
        source=_song(tmp_path),
        kind="lyrics",
        lyrics_text="[00:01.00]第一句\n",
        color="#ffd166",
    )])
    track = project.tracks["iw"]
    assert track.has_lyrics is True
    assert track.color == "#ffd166"
    assert "第一句" in project.lyrics_text("iw")

    project.update_music("iw", lyrics_text="[00:02.00]换过了\n")
    assert "换过了" in project.lyrics_text("iw")
    # The colour survives a lyrics-only update.
    assert project.tracks["iw"].color == "#ffd166"

    assert project.lyrics_text("ghost") == ""


def test_audio_path_extracts_for_playback(tmp_path):
    project = _project(tmp_path)
    project.add_music([model.MusicDraft(abbreviation="iw", source=_song(tmp_path))])
    extracted = project.audio_path("iw")
    assert extracted.is_file()
    assert extracted.read_bytes() == b"FLAC" * 64
    with pytest.raises(StudioError):
        project.audio_path("ghost")


# --------------------------------------------------------------------------
# summary
# --------------------------------------------------------------------------

def test_summary_reports_what_is_still_missing(tmp_path):
    project = _project(tmp_path)
    project.set_character("f", "FISH", "")
    empty = project.new_script("空的")
    assert project.summary()["characters"] == 1
    assert project.summary()["untimed_scripts"] == []

    name = project.new_script("有内容")
    project.add_event(name, Dialogue("f", "甲乙"))
    project.add_event(name, Dialogue("ghost", "丙"))
    project.add_music([model.MusicDraft(
        abbreviation="iw", source=_song(tmp_path), kind="lyrics",
        lyrics_text="[00:00.00]甲\n",
    )])

    summary = project.summary()
    assert summary["scripts"] == 2
    assert summary["tracks"] == 1
    assert summary["lyrics_tracks"] == ["iw"]
    assert summary["timed"] == 0
    assert summary["timed_total"] == 3
    assert summary["untimed_scripts"] == ["有内容.tscp"]
    assert summary["missing_characters"] == ["ghost"]
    assert empty in project.scripts


def test_summary_marks_timing_complete(tmp_path):
    project = _project(tmp_path)
    name = project.new_script()
    project.add_event(name, Dialogue(None, "甲乙", [0.1, 0.2]))
    summary = project.summary()
    assert summary["timed"] == summary["timed_total"] == 2
    assert summary["untimed_scripts"] == []


def test_empty_script_is_not_reported_as_untimed(tmp_path):
    project = _project(tmp_path)
    project.new_script()
    assert project.summary()["untimed_scripts"] == []
