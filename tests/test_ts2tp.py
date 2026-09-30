from pathlib import Path

import pytest

from Ts2Tp.model import (
    KeyboardTimingModel,
    TimingMode,
    compiled_name,
    compile_script,
    split_pages,
    timed_character_positions,
    visible_characters,
)
from tscp_player.format import Dialogue, Directive, Script, parse_tscps, serialize_tscp


def test_ansi_sequences_and_compiled_naming():
    text = r"\033[31m甲\033[0m乙"
    assert visible_characters(text) == ["甲", "乙"]
    assert compiled_name(Path("scene.tscps")) == "scene.tscp"


def test_keyboard_model_ignores_non_enter_and_records_characters():
    model = KeyboardTimingModel(Script([Dialogue("f", "甲乙")]), clock=iter([1.0, 1.4]).__next__)
    model.start(0.0)
    assert not model.handle_key("x")
    assert model.handle_key("Enter")
    assert model.handle_key("Return")
    assert model.result().lines[0].delays == pytest.approx([1.0, 0.4])


def test_continuous_mode_carries_time_between_sentences():
    source = Script([Dialogue(None, "甲"), Dialogue(None, "乙")])
    result = compile_script(
        source,
        TimingMode.CONTINUOUS,
        input_fn=lambda _: None,
        output_fn=lambda _: None,
        clock=iter([0.0, 0.5, 1.5]).__next__,
    )
    assert [line.delays for line in result.lines] == [[0.5], [1.0]]


def test_source_pages_split_at_clear():
    pages = split_pages(parse_tscps("一\n<c>\n二\n"))
    assert [[line.text for line in page.lines] for page in pages] == [["一"], ["二"]]


def test_punctuation_now_consumes_a_key():
    assert visible_characters("A,B!") == ["A", ",", "B", "!"]
    assert timed_character_positions("A,B!") == [0, 1, 2, 3]
    model = KeyboardTimingModel(Script([Dialogue(None, "A,B!")]))
    model.start(0.0)
    assert model.handle_key("Enter", 0.5)
    assert model.handle_key("Enter", 0.6)
    assert model.handle_key("Enter", 1.0)
    assert model.handle_key("Enter", 1.1)
    result = model.result()
    assert result.lines[0].delays == pytest.approx([0.5, 0.1, 0.4, 0.1])
    assert "0.500000,0.100000,0.400000,0.100000" in serialize_tscp(result)


def test_whitespace_is_still_automatic():
    assert visible_characters("A B") == ["A", " ", "B"]
    assert timed_character_positions("A B") == [0, 2]
    model = KeyboardTimingModel(Script([Dialogue(None, "A B")]))
    model.start(0.0)
    assert model.handle_key("Enter", 0.5)
    assert model.handle_key("Enter", 1.0)
    # 空格仍然立刻显示（0 秒），它没有自己的长度需要表演。
    assert model.result().lines[0].delays == pytest.approx([0.5, 0.0, 0.5])


def test_control_events_are_not_timed_and_are_reported():
    reached = []
    source = Script([Directive("s", "0.2"), Dialogue(None, "A"), Directive("c")])
    model = KeyboardTimingModel(
        source,
        event_callback=lambda index, event: reached.append(index),
        clock=iter([0.0, 0.5]).__next__,
    )
    model.start(0.0)
    assert reached[:2] == [0, 1]
    assert model.handle_key("Enter")
    assert model.complete
    assert reached == [0, 1, 2]


def test_arm_makes_the_first_key_the_baseline():
    model = KeyboardTimingModel(
        Script([Dialogue(None, "甲乙")]), clock=iter([10.0, 10.0, 10.4]).__next__
    )
    model.arm()
    assert model.armed
    assert not model.complete
    assert model.handle_key("Enter")
    assert model.handle_key("Enter")
    # 点按钮到第一次 Enter 之间的时间不会落到第一个字上。
    assert model.result().lines[0].delays == pytest.approx([0.0, 0.4])


def test_start_still_records_from_the_given_baseline():
    model = KeyboardTimingModel(Script([Dialogue(None, "甲乙")]))
    model.start(0.0)
    assert model.handle_key("Enter", 0.7)
    assert model.handle_key("Enter", 1.0)
    assert model.result().lines[0].delays == pytest.approx([0.7, 0.3])


def test_sentence_scope_keeps_the_other_lines():
    script = Script([Dialogue(None, "甲"), Dialogue(None, "乙"), Dialogue(None, "丙")])
    model = KeyboardTimingModel(
        script,
        base_delays={0: [1.0], 2: [3.0]},
        targets=[1],
        clock=iter([0.7]).__next__,
    )
    model.start(0.0)
    assert model.targets == [1]
    assert model.run_total_count == 1
    assert model.handle_key("Enter")
    assert model.complete
    assert [line.delays for line in model.result().lines] == [[1.0], [0.7], [3.0]]


def test_source_delays_are_reused_as_the_baseline():
    script = Script([Dialogue(None, "甲", [1.0]), Dialogue(None, "乙", [2.0])])
    model = KeyboardTimingModel(script, targets=[1], clock=iter([0.9]).__next__)
    model.start(0.0)
    assert model.progress_for_event(0) == (1, 1)
    assert model.handle_key("Enter")
    assert [line.delays for line in model.result().lines] == [[1.0], [0.9]]


def test_scoped_run_only_visits_its_own_directives():
    reached = []
    source = Script([
        Directive("p", "iw"),
        Dialogue(None, "甲", [1.0]),
        Directive("s", "1"),
        Dialogue(None, "乙", [1.0]),
    ])
    model = KeyboardTimingModel(
        source,
        targets=[3],
        event_callback=lambda index, event: reached.append(index),
        clock=iter([0.5]).__next__,
    )
    model.start(0.0)
    assert model.handle_key("Enter")
    assert model.complete
    assert reached == [0, 2, 3]


def test_music_timeline_binds_sentences_to_offsets():
    script = Script([
        Directive("p", "iw"),
        Dialogue(None, "甲乙", [1.0, 1.0]),
        Dialogue(None, "乙", [0.5]),
    ])
    model = KeyboardTimingModel(script, targets=[])
    model.start(0.0)
    assert model.complete
    timeline = model.music_timeline()
    assert timeline.at(1).track == "iw"
    assert timeline.at(1).offset == pytest.approx(0.0)
    assert timeline.at(2).offset == pytest.approx(2.0)


def test_parse_script_file_detects_compiled_scripts():
    from Ts2Tp.Main import parse_script_file

    compiled = "TSCP 1\nD|f|SGk=|0.1,0.2\n"
    # 编译稿带着已有时间，重新打开才能只改一句。
    assert parse_script_file(compiled).lines[0].delays == pytest.approx([0.1, 0.2])
    assert parse_script_file(compiled, ".tscp").lines[0].delays == pytest.approx([0.1, 0.2])
    # 原稿仍然按原稿解析，没有逐字时间。
    assert parse_script_file("[f]Hi\n", ".tscps").lines[0].delays == []


# --------------------------------------------------------------------------
# supplements in the timing tool
# --------------------------------------------------------------------------

def test_a_supplement_is_shown_friendly_not_packed():
    """The panel must not leak the internal colour|seconds|text packing."""

    from Ts2Tp.Main import _display_line
    from tscp_player.format import make_note, parse_tscps

    assert _display_line(make_note("只是一句补充", color="#ffd166", seconds=4.5)) == (
        "<a>#ffd166,4.5 只是一句补充"
    )
    assert _display_line(make_note("没颜色", seconds=3)) == "<a>没颜色"
    # The other controls are unchanged.
    assert _display_line(parse_tscps("<s>1.5").lines[0]) == "<s>1.5"
    assert _display_line(parse_tscps("<c>").lines[0]) == "<c>"
    assert _display_line(parse_tscps("<p>night").lines[0]) == "<p>night"


def test_a_supplement_is_reported_as_a_supplement_not_a_clear():
    """It is a Directive, but it does not clear anything."""

    from Ts2Tp.Main import ConverterWindow
    from tscp_player.format import make_note

    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")

    # _execute_directive only reads state it does not have to build, so call it
    # unbound against a stand-in rather than spinning up the whole window.
    class StandIn:
        _execute_directive = ConverterWindow._execute_directive

        def _apply_music(self, value):        # pragma: no cover - not reached
            return "music"

        def _begin_wait(self, value):         # pragma: no cover - not reached
            return "wait"

    stand_in = StandIn()
    assert stand_in._execute_directive(make_note("旁注", color="#ffd166", seconds=4.0)) == (
        "补充内容会显示 4 秒：旁注"
    )
    assert stand_in._execute_directive(Directive("p", "iw")) == "music"
    assert stand_in._execute_directive(Directive("s", "1")) == "wait"
    assert stand_in._execute_directive(Directive("c")) == "已清空屏幕"


def test_timing_keeps_supplements_and_walks_past_them():
    """A supplement costs no time, so recording must not stop on it."""

    from tscp_player.format import make_note

    script = Script([
        Dialogue("f", "甲"),
        make_note("一句旁注", color="#ffd166", seconds=4.0),
        Dialogue("f", "乙"),
    ])
    reached = []
    model = KeyboardTimingModel(
        script,
        TimingMode.PER_SENTENCE,
        clock=iter([1.0, 2.0, 3.0, 4.0]).__next__,
        event_callback=lambda index, event: reached.append((index, type(event).__name__)),
    )
    model.arm()
    while not model.complete:
        if not model.handle_key("Enter"):
            break

    # The supplement was announced on the way past...
    assert (1, "Directive") in reached
    # ...and it survived into the result, unchanged.
    result = model.result()
    assert result.lines[1] == script.lines[1]
    assert len(result.lines) == 3
    # Only the two dialogues carry timing.
    assert result.lines[0].delays and result.lines[2].delays
    assert result.lines[1].value == script.lines[1].value
