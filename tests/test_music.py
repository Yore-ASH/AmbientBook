import pytest

from tscp_player.format import Dialogue, Directive, Script, make_note
from tscp_player.music import MusicCue, build_timeline, line_durations, note_spans


def test_supplements_cost_no_main_timeline_time():
    """An aside runs alongside the story, it does not hold it up."""

    script = Script([
        Dialogue("f", "你好", [0.5, 0.5]),
        make_note("旁注", color="#ffd166", seconds=4.0),
        Dialogue(None, "继续", [0.25, 0.25]),
    ])
    assert line_durations(script) == [1.0, 0.0, 0.5]
    # Pacing is exactly what it would be without the aside.
    without = Script([script.lines[0], script.lines[2]])
    assert sum(line_durations(script)) == sum(line_durations(without))


def test_note_spans_sit_where_the_player_reaches_them():
    script = Script([
        Directive("p", "night"),
        Dialogue("f", "你好", [0.5, 0.5]),      # 0.0 -> 1.0
        make_note("四秒旁注", color="#ffd166", seconds=4.0),
        Directive("s", "2"),
        make_note("默认时长", seconds=3.0),
    ])
    spans = note_spans(script)
    assert [(s.index, s.start, s.end, s.color, s.text) for s in spans] == [
        (2, 1.0, 5.0, "#ffd166", "四秒旁注"),
        (4, 3.0, 6.0, "", "默认时长"),
    ]
    assert spans[0].seconds == pytest.approx(4.0)


def test_note_spans_accept_an_explicit_timeline():
    script = Script([Dialogue(None, "甲", [0.1]), make_note("注", seconds=2.0)])
    spans = note_spans(script, [10.0, 0.0])
    assert spans[0].start == pytest.approx(10.0)
    with pytest.raises(ValueError):
        note_spans(script, [1.0])


def test_a_script_without_supplements_has_no_spans():
    script = Script([Dialogue(None, "甲", [0.1]), Directive("c")])
    assert note_spans(script) == []


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
