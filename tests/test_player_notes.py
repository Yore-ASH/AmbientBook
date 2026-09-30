"""Offscreen tests for the desktop player's supplement area.

The aside lives below the story, has its own duration and colour, and — the bit
that is easy to get wrong — survives a clear without restarting its clock.
"""

from pathlib import Path

import pytest

from PlotManager import model as plot_model
from tscp_player.format import Dialogue, Directive, Script, make_note, note_parts, serialize_tscp

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import Main as player  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modals(monkeypatch, qapp):
    """A real QMessageBox would wait for a click that never comes."""

    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(
            QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
        )
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )


@pytest.fixture
def window(tmp_path, qapp, no_modals):
    """A player with one script that already contains a supplement."""

    script = Script([
        Directive("p", "night"),
        Dialogue("f", "这是剧情正文。", [0.1] * 7),
        make_note("这是一句补充", color="#ffd166", seconds=30.0),
        Directive("c"),
        Dialogue(None, "清屏之后。", [0.1] * 5),
    ])
    source = tmp_path / "plot.tscpkg"
    plot_model.create_package(source, name="补充测", description="")
    plot_model.write_script(source, "plot.tscp", serialize_tscp(script))
    song = tmp_path / "song.flac"
    song.write_bytes(b"FLAC" * 64)
    plot_model.add_music(source, [(song, "night")])

    core, problems = player.load_playlists(source)
    widget = player.PlayerWindow(core, source, problems)
    widget.package = core[0][0]
    widget.script = core[0][1]["plot.tscp"]
    # Stop _next_event from running the whole script behind our backs.
    widget.line_index = len(widget.script.lines) + 10
    widget.resize(800, 560)
    yield widget
    widget._closed = True
    widget.close()


def test_nothing_is_shown_before_a_supplement_arrives(window):
    assert window.supplement.isHidden() is True
    assert window.supplement.text() == ""


def test_a_supplement_appears_below_the_story(window):
    window._show_note(make_note("答案在下面", color="#33cccc", seconds=5.0))
    assert window.supplement.isHidden() is False
    assert window.supplement.text() == "答案在下面"
    # Its colour is applied, and it sits below the story area in the layout.
    assert "#33cccc" in window.supplement.styleSheet()
    layout = window.play_page.layout()
    assert layout.indexOf(window.output) < layout.indexOf(window.supplement)


def test_a_supplement_uses_the_default_colour_when_none_is_given(window):
    window._show_note(make_note("没指定颜色", seconds=5.0))
    assert "#8a93a0" in window.supplement.styleSheet()


def test_clearing_reloads_the_supplement_without_restarting_it(window):
    """The story goes away; the aside comes back with the time it had left."""

    window._show_note(make_note("别把我清掉", color="#ffd166", seconds=30.0))
    deadline = window._note_until
    assert window.supplement.isHidden() is False

    window.output.setPlainText("之前写下的正文")
    window._handle_directive(Directive("c"))

    # The story was cleared...
    assert window.output.toPlainText() == ""
    # ...but the aside is still there, with exactly the same deadline.
    assert window.supplement.isHidden() is False
    assert window.supplement.text() == "别把我清掉"
    assert window._note_until == deadline


def test_a_supplement_expires_on_its_own(window):
    window._show_note(make_note("马上消失", seconds=0.01))
    assert window.supplement.isHidden() is False
    window._note_until = 0.0
    window._expire_note()
    assert window.supplement.isHidden() is True
    assert window.supplement.text() == ""
    assert window._note_text == ""


def test_painting_after_expiry_does_not_resurrect_it(window):
    window._show_note(make_note("过期了", seconds=1.0))
    window._note_until = 0.0
    window._paint_note()
    assert window.supplement.isHidden() is True


def test_a_second_supplement_replaces_the_first(window):
    window._show_note(make_note("第一条", seconds=30.0))
    window._show_note(make_note("第二条", color="#ff6666", seconds=30.0))
    assert window.supplement.text() == "第二条"
    assert "#ff6666" in window.supplement.styleSheet()


def test_handling_the_note_directive_goes_through_the_dispatcher(window):
    """It must be reached by the normal event walk, not only by a direct call."""

    note = make_note("走正常路径", color="#f6d365", seconds=30.0)
    window._handle_directive(note)
    assert window.supplement.text() == "走正常路径"
    assert note_parts(window.script.lines[2]) == ("#ffd166", 30.0, "这是一句补充")


def test_supplements_do_not_hold_up_the_story(window):
    """The line after an aside starts immediately: it costs no time."""

    from tscp_player.music import line_durations

    durations = line_durations(window.script)
    assert durations[2] == 0.0
    # The story total is unaffected by the aside.
    without = Script([window.script.lines[0], window.script.lines[1],
                      window.script.lines[3], window.script.lines[4]])
    assert sum(durations) == pytest.approx(sum(line_durations(without)))
