"""Tests for pause / rewind in the recorder and batch styling in the dialog."""

from pathlib import Path

import pytest

from PySide6.QtWidgets import QApplication  # noqa: F401  (created by the qapp fixture)

from PlotManager.recorder import LyricsRecorderDialog
from tscp_player.audio import SEEKABLE_SUFFIXES, SeekUnsupported, supports_seek


def _recorder(tmp_path, lines=("一", "二", "三", "四"), name="song.ogg"):
    import time

    dialog = LyricsRecorderDialog(None, tmp_path / name, list(lines))
    dialog.start = lambda: setattr(dialog, "_started_at", time.monotonic())
    # The clock is measured against monotonic(), so anchoring it at 0 would make
    # every elapsed reading enormous.
    dialog._started_at = time.monotonic()
    dialog._cursor = 0
    return dialog


# --------------------------------------------------------------------------
# what can be positioned at all
# --------------------------------------------------------------------------

def test_supports_seek_matches_sdl_mixer():
    for name in ("a.ogg", "a.oga", "a.opus", "a.mp3", "a.MP3"):
        assert supports_seek(name) is True
    # SDL_mixer's set_pos does nothing for these, which is why they are out.
    for name in ("a.flac", "a.wav", "a.m4a", ""):
        assert supports_seek(name) is False


def test_the_seekable_set_is_the_documented_one():
    assert SEEKABLE_SUFFIXES == frozenset({".ogg", ".oga", ".opus", ".mp3"})


def test_seek_unsupported_names_the_format():
    error = SeekUnsupported("/music/song.flac")
    assert error.suffix == ".flac"
    assert "flac" in str(error)


class _Mixer:
    """Records what the player asks of the mixer."""

    def __init__(self) -> None:
        self.calls = []
        self.music = self

    def load(self, key):
        self.calls.append(("load", key))

    def set_volume(self, value):
        pass

    def play(self, loops=None):
        self.calls.append(("play", loops))

    def get_pos(self):
        return 1234

    def set_pos(self, seconds):
        self.calls.append(("set_pos", seconds))

    def pause(self):
        self.calls.append(("pause",))

    def unpause(self):
        self.calls.append(("unpause",))

    def stop(self):
        self.calls.append(("stop",))


def test_position_reports_the_mixers_clock(tmp_path):
    from tscp_player.audio import MusicPlayer

    song = tmp_path / "song.ogg"
    song.write_bytes(b"x")
    player = MusicPlayer(mixer=_Mixer())
    assert player.position() is None          # nothing loaded yet
    player.play(str(song))
    assert player.position() == 1.234


def test_seek_reaches_the_mixer_for_a_seekable_file(tmp_path):
    from tscp_player.audio import MusicPlayer

    song = tmp_path / "song.ogg"
    song.write_bytes(b"x")
    mixer = _Mixer()
    player = MusicPlayer(mixer=mixer)
    player.play(str(song))

    player.seek(42.5)
    assert ("set_pos", 42.5) in mixer.calls


def test_seek_refuses_a_format_the_mixer_cannot_position(tmp_path):
    """A FLAC must fail loudly rather than quietly keep playing."""

    from tscp_player.audio import MusicPlayer

    song = tmp_path / "song.flac"
    song.write_bytes(b"x")
    mixer = _Mixer()
    player = MusicPlayer(mixer=mixer)
    player.play(str(song))

    with pytest.raises(SeekUnsupported):
        player.seek(42.5)
    assert ("set_pos", 42.5) not in mixer.calls


# --------------------------------------------------------------------------
# pausing freezes the clock
# --------------------------------------------------------------------------

def test_pausing_stops_the_clock(qapp, tmp_path):
    dialog = _recorder(tmp_path)
    dialog.toggle_pause()

    frozen = dialog._elapsed()
    assert dialog._paused_at is not None
    assert dialog.pause_button.text() == "▶ 继续"
    import time

    time.sleep(0.05)
    assert dialog._elapsed() == frozen


def test_resuming_does_not_count_the_pause(qapp, tmp_path):
    import time

    dialog = _recorder(tmp_path)
    dialog.toggle_pause()
    time.sleep(0.08)
    dialog.toggle_pause()

    assert dialog._paused_at is None
    assert dialog.pause_button.text() == "⏸ 暂停"
    # The pause must not show up as elapsed audio.
    assert dialog._elapsed() < 0.08


def test_marks_taken_after_a_pause_are_not_late(qapp, tmp_path):
    import time

    dialog = _recorder(tmp_path)
    dialog._mark()
    first = dialog._marks[0]

    dialog.toggle_pause()
    time.sleep(0.08)
    dialog.toggle_pause()
    dialog._mark()
    second = dialog._marks[1]

    assert first is not None and second is not None
    assert second - first < 0.08


def test_the_clock_label_says_when_it_is_paused(qapp, tmp_path):
    dialog = _recorder(tmp_path)
    dialog.toggle_pause()
    assert "已暂停" in dialog.clock_label.text()
    dialog.toggle_pause()
    assert "已暂停" not in dialog.clock_label.text()


def test_the_pause_button_starts_disabled(qapp, tmp_path):
    """Nothing to pause before recording begins."""

    dialog = _recorder(tmp_path)
    assert dialog.pause_button.isEnabled() is False
    assert dialog.rewind_button.isEnabled() is False


# --------------------------------------------------------------------------
# rewinding to the previous line
# --------------------------------------------------------------------------

def test_rewind_is_refused_for_a_flac_with_an_explanation(qapp, tmp_path, messages):
    dialog = _recorder(tmp_path, name="song.flac")
    dialog._mark()
    dialog._mark()
    dialog.rewind_to_previous()

    assert any("无法定位播放" in text for _kind, text in messages)
    assert any("OGG" in text for _kind, text in messages)


def test_rewind_goes_to_the_line_before_the_cursor(qapp, tmp_path, monkeypatch):
    dialog = _recorder(tmp_path, name="song.ogg")
    # Pretend the first two lines were marked at 10s and 20s.
    dialog._marks[0] = 10.0
    dialog._marks[1] = 20.0
    dialog.table.item(0, 1).setText("10.00")
    dialog.table.item(1, 1).setText("20.00")
    dialog._cursor = 2

    seeks = []

    class FakePlayer:
        def ensure(self, path, restart=False):
            return True

        def seek(self, seconds):
            seeks.append(seconds)

        def pause(self):
            pass

    dialog._music = FakePlayer()
    dialog.rewind_to_previous()

    # The cursor sits on line 3, so "the previous line" is line 2 (20s).
    assert seeks == [20.0]
    assert dialog._cursor == 1
    assert "20.00" in dialog.progress_label.text()


def test_rewind_lines_the_clock_up_with_the_audio(qapp, tmp_path):
    dialog = _recorder(tmp_path, name="song.ogg")
    dialog._marks[0] = 30.0
    dialog.table.item(0, 1).setText("30.00")
    dialog._cursor = 1

    class FakePlayer:
        def ensure(self, path, restart=False):
            return True

        def seek(self, seconds):
            pass

        def pause(self):
            pass

    dialog._music = FakePlayer()
    dialog.rewind_to_previous()

    # After seeking to 30s the clock has to read 30s, not 0.
    assert abs(dialog._elapsed() - 30.0) < 0.1
    assert "30.00" in dialog.clock_label.text()


def test_rewind_at_the_first_line_stays_at_the_start(qapp, tmp_path):
    dialog = _recorder(tmp_path, name="song.ogg")
    dialog._cursor = 0

    class FakePlayer:
        def ensure(self, path, restart=False):
            return True

        def seek(self, seconds):
            self.seen = seconds

        def pause(self):
            pass

    player = FakePlayer()
    dialog._music = player
    dialog.rewind_to_previous()

    assert dialog._cursor == 0
    assert getattr(player, "seen", None) == 0.0


def test_rewind_resumes_first_when_paused(qapp, tmp_path):
    dialog = _recorder(tmp_path, name="song.ogg")
    dialog._marks[0] = 5.0
    dialog._cursor = 1
    dialog.toggle_pause()
    assert dialog._paused_at is not None

    unpaused = []

    class FakePlayer:
        def ensure(self, path, restart=False):
            return True

        def seek(self, seconds):
            pass

        def pause(self):
            pass

        def unpause(self):
            unpaused.append(True)

    dialog._music = FakePlayer()
    dialog.rewind_to_previous()

    assert unpaused == [True]
    assert dialog._paused_at is None


def test_rewind_does_nothing_before_recording_starts(qapp, tmp_path):
    from PlotManager.recorder import LyricsRecorderDialog

    dialog = LyricsRecorderDialog(None, tmp_path / "song.ogg", ["一", "二"])
    dialog.rewind_to_previous()          # must not raise
    assert dialog._cursor == 0


# --------------------------------------------------------------------------
# batch styling
# --------------------------------------------------------------------------

def _dialog(lines=None):
    from Studio.Main import LineStyleDialog
    from tscp_player.lyrics import LyricLine, Lyrics

    lyrics = Lyrics([LyricLine(float(i), "line %d" % i) for i in range(1, 5)])
    return LineStyleDialog(None, lyrics, {})


def test_the_batch_box_offers_every_line(qapp):
    dialog = _dialog()
    assert dialog.apply_all.isChecked() is False
    assert len(dialog._target_rows()) == 1          # the focused row by default

    dialog.apply_all.setChecked(True)
    assert dialog._target_rows() == [0, 1, 2, 3]


def test_selecting_several_lines_targets_all_of_them(qapp):
    from PySide6.QtCore import QItemSelectionModel

    dialog = _dialog()
    dialog.table.selectionModel().select(
        dialog.table.model().index(1, 0),
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    dialog.table.selectionModel().select(
        dialog.table.model().index(2, 0),
        QItemSelectionModel.SelectionFlag.Select
        | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert dialog._target_rows() == [1, 2]


def test_applying_a_colour_to_every_line(qapp):
    dialog = _dialog()
    dialog.apply_all.setChecked(True)
    for row in dialog._target_rows():
        dialog._entry(row)["color"] = "#abcdef"

    styles = dialog.styles()
    assert len(styles) == 4
    assert all(entry["color"] == "#abcdef" for entry in styles.values())


def test_clearing_every_line_is_a_batch_too(qapp):
    dialog = _dialog()
    dialog.apply_all.setChecked(True)
    for row in dialog._target_rows():
        dialog._entry(row)["font"] = "Arial"

    assert len(dialog.styles()) == 4
    dialog.clear_row()
    assert dialog.styles() == {}


def test_clearing_a_selection_leaves_the_rest_alone(qapp):
    dialog = _dialog()
    dialog.apply_all.setChecked(True)
    for row in dialog._target_rows():
        dialog._entry(row)["color"] = "#111111"

    # Now target a single row.
    dialog.apply_all.setChecked(False)
    dialog.table.selectRow(0)
    dialog.clear_row()

    remaining = dialog.styles()
    assert 1.0 not in remaining
    assert len(remaining) == 3


def test_the_table_allows_multi_selection(qapp):
    from PySide6.QtWidgets import QAbstractItemView

    dialog = _dialog()
    assert (
        dialog.table.selectionMode()
        == QAbstractItemView.SelectionMode.ExtendedSelection
    )


# --------------------------------------------------------------------------
# knowing when the music has finished
# --------------------------------------------------------------------------

def test_is_busy_reports_the_mixers_state(tmp_path):
    from tscp_player.audio import MusicPlayer

    song = tmp_path / "song.ogg"
    song.write_bytes(b"x")

    class Mixer:
        def __init__(self):
            self.music = self
            self.busy = True

        def load(self, key):
            pass

        def set_volume(self, value):
            pass

        def play(self, loops=None):
            pass

        def get_busy(self):
            return self.busy

        def get_pos(self):
            return 0

    mixer = Mixer()
    player = MusicPlayer(mixer=mixer)
    assert player.is_busy() is False          # nothing loaded
    player.play(str(song))
    assert player.is_busy() is True
    mixer.busy = False
    assert player.is_busy() is False


# The two preview-playback tests live in test_studio_ui.py, which owns the
# studio fixture and the _timed_story helper they need.


def test_the_dialog_styles_the_translation_separately(qapp):
    from PySide6.QtWidgets import QFontDialog

    from Studio.Main import LineStyleDialog
    from tscp_player.lyrics import LyricLine, Lyrics

    dialog = LineStyleDialog(
        None, Lyrics([LyricLine(1.0, "a", "b")]), {}
    )
    dialog.apply_all.setChecked(True)

    class FakeFont:
        @staticmethod
        def family():
            return "Arial"

    # QFontDialog.getFont returns (font, accepted).
    original = QFontDialog.getFont
    QFontDialog.getFont = staticmethod(lambda *a, **k: (FakeFont(), True))
    try:
        dialog.pick_font("translation_font")
    finally:
        QFontDialog.getFont = original

    styles = dialog.styles()
    assert styles[1.0] == {"translation_font": "Arial"}
    # The original half is untouched.
    assert "font" not in styles[1.0]


def _tiny_flac() -> bytes:
    """A structurally valid FLAC: STREAMINFO then some audio frames."""

    streaminfo = bytes(34)
    out = bytearray(b"fLaC")
    out.append(0x80 | 0)                      # last metadata block, STREAMINFO
    out += len(streaminfo).to_bytes(3, "big")
    out += streaminfo
    out += b"AUDIOFRAMES" * 4
    return bytes(out)


# --------------------------------------------------------------------------
# the player reads the lyric document, not just the .lrc
# --------------------------------------------------------------------------

def test_per_line_styles_reach_the_player(tmp_path):
    """Reading only the .lrc made every line come out in the track colour."""

    from PlotManager import model as plot_model
    from tscp_player import plot
    from tscp_player.lyrics import (
        Lyrics,
        LyricLine,
        serialize_lyric_document,
    )

    song = tmp_path / "song.flac"
    song.write_bytes(_tiny_flac())

    package_path = tmp_path / "demo.tscpkg"
    plot_model.create_package(package_path, name="x", description="")
    plot_model.add_tracks(
        package_path,
        [plot_model.MusicDraft(
            abbreviation="iw", source=song, kind="lyrics",
            lyrics_text="[00:01.00]a\n", color="#ff0000",
        )],
    )
    plot_model.update_track(
        package_path,
        "iw",
        lyric_document=serialize_lyric_document(
            Lyrics([LyricLine(1.0, "a", color="#00ff00")])
        ),
    )

    package = plot.load_plot(package_path)
    assert package.track("iw").document
    lines = package.lyrics("iw").lines
    assert lines[0].color == "#00ff00"
    # The track colour is still the fallback for unstyled lines.
    assert package.track("iw").color == "#ff0000"


def test_a_track_without_a_document_still_reads_its_lrc(tmp_path):
    from PlotManager import model as plot_model
    from tscp_player import plot

    song = tmp_path / "song.flac"
    song.write_bytes(_tiny_flac())

    package_path = tmp_path / "demo.tscpkg"
    plot_model.create_package(package_path, name="x", description="")
    plot_model.add_tracks(
        package_path,
        [plot_model.MusicDraft(
            abbreviation="iw", source=song, kind="lyrics",
            lyrics_text="[00:01.00]only\n",
        )],
    )

    package = plot.load_plot(package_path)
    assert package.track("iw").document is None
    assert package.lyrics("iw").lines[0].text == "only"


def test_a_broken_document_falls_back_to_the_lrc(tmp_path):
    from PlotManager import model as plot_model
    from tscp_player import plot

    song = tmp_path / "song.flac"
    song.write_bytes(_tiny_flac())

    package_path = tmp_path / "demo.tscpkg"
    plot_model.create_package(package_path, name="x", description="")
    plot_model.add_tracks(
        package_path,
        [plot_model.MusicDraft(
            abbreviation="iw", source=song, kind="lyrics",
            lyrics_text="[00:01.00]fallback\n",
        )],
    )
    # Something that claims to be a document but is not.
    plot_model.update_track(package_path, "iw", lyric_document="{ not json")

    package = plot.load_plot(package_path)
    assert package.lyrics("iw").lines[0].text == "fallback"


def test_the_document_wins_over_the_lrc(tmp_path):
    """Both are present; the richer one is the one that should be shown."""

    from PlotManager import model as plot_model
    from tscp_player import plot
    from tscp_player.lyrics import Lyrics, LyricLine, serialize_lyric_document

    song = tmp_path / "song.flac"
    song.write_bytes(_tiny_flac())

    package_path = tmp_path / "demo.tscpkg"
    plot_model.create_package(package_path, name="x", description="")
    plot_model.add_tracks(
        package_path,
        [plot_model.MusicDraft(
            abbreviation="iw", source=song, kind="lyrics",
            lyrics_text="[00:01.00]from lrc\n",
        )],
    )
    plot_model.update_track(
        package_path,
        "iw",
        lyric_document=serialize_lyric_document(
            Lyrics([LyricLine(1.0, "from document", font="Arial")])
        ),
    )

    package = plot.load_plot(package_path)
    line = package.lyrics("iw").lines[0]
    assert line.text == "from document"
    assert line.font == "Arial"


# --------------------------------------------------------------------------
# the overlay keeps itself in front
# --------------------------------------------------------------------------

def test_the_overlay_asks_to_stay_on_top(qapp):
    from PySide6.QtCore import Qt

    from tscp_player.lyricview import LyricsWindow

    window = LyricsWindow()
    flags = window.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.FramelessWindowHint


def test_the_overlay_re_asserts_itself_while_showing(qapp):
    """The hint is only a request; another app can still end up in front."""

    from tscp_player.lyrics import Lyrics, LyricLine
    from tscp_player.lyricview import LyricsWindow

    window = LyricsWindow()
    assert not window._top_timer.isActive()

    window.show_track(Lyrics([LyricLine(1.0, "hi")]), "#fff", 1.0)
    assert window._top_timer.isActive()
    # And the timer is wired to something that re-raises rather than raising.
    window._keep_on_top()

    window.clear()
    assert not window._top_timer.isActive()


def test_the_overlay_stops_re_asserting_when_hidden(qapp):
    from tscp_player.lyrics import Lyrics, LyricLine
    from tscp_player.lyricview import LyricsWindow

    window = LyricsWindow()
    window.show_track(Lyrics([LyricLine(1.0, "hi")]), "#fff", 1.0)
    window.hide()
    window._keep_on_top()
    assert not window._top_timer.isActive()


