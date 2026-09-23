import pytest

from tscp_player.format import Dialogue, Directive, Script
from tscp_player.music import MusicCue, build_timeline, line_durations


def test_same_track_continues_instead_of_restarting():
    script = Script([
        Directive("p", "iw"),
        Dialogue(None, "AB", [1.0, 1.0]),
        Directive("p", "iw"),
        Dialogue(None, "B", [0.5]),
    ])
    timeline = build_timeline(script)
    assert timeline.at(0) == MusicCue("iw", 0.0, True)
    assert timeline.at(1) == MusicCue("iw", 0.0, True)
    # 重新声明同一曲目不会重置：第二句从第一句结束处继续。
    assert timeline.at(2) == MusicCue("iw", 2.0, True)
    assert timeline.at(3) == MusicCue("iw", 2.0, True)


def test_different_track_restarts_and_stop_resets():
    script = Script([
        Directive("p", "iw"),
        Dialogue(None, "A", [3.0]),
        Directive("p", "ot"),
        Dialogue(None, "B", [1.0]),
        Directive("p", "stop"),
        Dialogue(None, "C", [1.0]),
    ])
    timeline = build_timeline(script)
    assert timeline.at(1) == MusicCue("iw", 0.0, True)
    # <p> 行的 cue 描述的是切换之后的状态：换曲从 0 重新开始。
    assert timeline.at(2) == MusicCue("ot", 0.0, True)
    assert timeline.at(3) == MusicCue("ot", 0.0, True)
    assert timeline.at(5) == MusicCue(None, 0.0, False)
    assert timeline.at(5).silent


def test_sleep_advances_the_offset_while_playing():
    script = Script([
        Directive("p", "iw"),
        Directive("s", "2.5"),
        Dialogue(None, "A", [0.5]),
        Directive("p", "iw"),
    ])
    timeline = build_timeline(script)
    assert timeline.track_changed(0) is True
    assert timeline.track_changed(1) is False
    assert timeline.track_changed(3) is False
    assert timeline.at(2).offset == pytest.approx(2.5)
    assert timeline.at(3).offset == pytest.approx(3.0)


def test_sleep_does_not_advance_while_stopped():
    script = Script([Directive("s", "5"), Directive("p", "iw"), Directive("s", "1")])
    timeline = build_timeline(script)
    assert timeline.at(2).offset == pytest.approx(0.0)


def test_untimed_script_reports_zero_offsets():
    script = Script([Directive("p", "iw"), Dialogue(None, "A"), Dialogue(None, "B")])
    timeline = build_timeline(script)
    assert all(cue.offset == 0.0 for cue in timeline.cues)
    assert [cue.track for cue in timeline.cues] == ["iw", "iw", "iw"]


def test_line_durations_uses_delays_and_sleep():
    script = Script([
        Dialogue(None, "AB", [0.5, 1.5]),
        Directive("s", "2"),
        Directive("c"),
        Directive("p", "x"),
    ])
    assert line_durations(script) == [2.0, 2.0, 0.0, 0.0]


def test_line_durations_ignores_broken_values():
    script = Script([Directive("s", "abc"), Dialogue(None, "A", [-1.0])])
    assert line_durations(script) == [0.0, 0.0]


def test_durations_length_is_validated():
    with pytest.raises(ValueError):
        build_timeline(Script([Dialogue(None, "A")]), durations=[1.0, 2.0])


def test_explicit_durations_override_the_script():
    script = Script([Directive("p", "iw"), Dialogue(None, "A"), Dialogue(None, "B")])
    timeline = build_timeline(script, durations=[0.0, 4.0, 0.0])
    assert timeline.at(2).offset == pytest.approx(4.0)


def test_first_offset_of_reports_where_a_track_starts():
    script = Script([
        Directive("p", "iw"),
        Dialogue(None, "A", [2.0]),
        Directive("p", "ot"),
        Directive("p", "iw"),
    ])
    timeline = build_timeline(script)
    assert timeline.first_offset_of("iw") == pytest.approx(0.0)
    assert timeline.first_offset_of("ot") == pytest.approx(0.0)
    assert timeline.first_offset_of("missing") is None
