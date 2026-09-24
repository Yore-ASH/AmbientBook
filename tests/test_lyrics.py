import pytest

from tscp_player.lyrics import (
    HAN,
    LATIN,
    NEUTRAL,
    OTHER,
    Lyrics,
    format_time,
    parse_lrc,
    parse_time,
    lyric_source_lines,
    pick_family,
    serialize_lrc,
    script_of,
    text_runs,
    timed_from_marks,
    to_html,
    wants_italic,
)

LRC = """[ti:Demo]
[ar:Nobody]
[00:01.00]第一句
[00:03.50]Second line
[00:06]只有秒的写法
"""


def test_parse_lrc_reads_times_and_skips_metadata():
    lyrics = parse_lrc(LRC)
    assert [line.text for line in lyrics.lines] == [
        "第一句",
        "Second line",
        "只有秒的写法",
    ]
    assert [round(line.time, 2) for line in lyrics.lines] == [1.0, 3.5, 6.0]


def test_parse_lrc_supports_several_stamps_on_one_line():
    lyrics = parse_lrc("[00:10.00][00:20.00]重复副歌")
    assert [(round(line.time, 2), line.text) for line in lyrics.lines] == [
        (10.0, "重复副歌"),
        (20.0, "重复副歌"),
    ]


def test_parse_lrc_applies_the_offset_tag():
    lyrics = parse_lrc("[offset:+500]\n[00:10.00]晚半秒")
    assert lyrics.lines[0].time == pytest.approx(10.5)
    early = parse_lrc("[offset:-1500]\n[00:10.00]早一秒半")
    assert early.lines[0].time == pytest.approx(8.5)


def test_parse_lrc_supports_hours():
    lyrics = parse_lrc("[01:02:03.50]很长的曲子")
    assert lyrics.lines[0].time == pytest.approx(3723.5)


def test_parse_lrc_ignores_a_file_without_timestamps():
    assert parse_lrc("只是普通文本\n第二行\n").lines == []
    assert not parse_lrc("只是普通文本")


def test_lyrics_lookup_returns_the_current_line():
    lyrics = parse_lrc(LRC)
    assert lyrics.at(0.5) is None
    assert lyrics.at(1.0) == "第一句"
    assert lyrics.at(3.4) == "第一句"
    assert lyrics.at(3.5) == "Second line"
    assert lyrics.at(99.0) == "只有秒的写法"
    assert lyrics.index_at(0.0) == -1
    assert lyrics.next_after(1.0).text == "Second line"
    assert lyrics.duration == pytest.approx(6.0)


def test_lrc_round_trip_keeps_times_and_text():
    original = parse_lrc(LRC)
    again = parse_lrc(serialize_lrc(original))
    assert [line.text for line in again.lines] == [line.text for line in original.lines]
    for left, right in zip(original.lines, again.lines):
        assert left.time == pytest.approx(right.time, abs=0.01)


def test_lyric_source_lines_accepts_plain_text_and_lrc():
    assert lyric_source_lines("第一句\n# 注释\n\n第二句\n") == ["第一句", "第二句"]
    # A file that already has timestamps is treated as LRC.
    assert lyric_source_lines(LRC) == ["第一句", "Second line", "只有秒的写法"]


def test_timed_from_marks_pairs_lines_with_recorded_times():
    lyrics = timed_from_marks(["甲", "乙", "丙"], [0.0, 2.0])
    assert [(line.text, line.time) for line in lyrics.lines] == [("甲", 0.0), ("乙", 2.0)]


def test_timed_from_marks_sorts_and_rejects_negatives():
    lyrics = timed_from_marks(["晚", "早"], [5.0, -3.0])
    assert [(line.text, line.time) for line in lyrics.lines] == [("早", 0.0), ("晚", 5.0)]


def test_script_classification():
    assert script_of("汉") == HAN
    assert script_of("字") == HAN
    assert script_of("A") == LATIN
    assert script_of("é") == LATIN
    assert script_of("あ") == "japanese"
    assert script_of("한") == "korean"
    assert script_of("Д") == "cyrillic"
    assert script_of("α") == "greek"
    # Script-specific punctuation belongs to its own script so it uses the
    # matching font; ASCII punctuation stays neutral.
    assert script_of("،") == "arabic"
    assert script_of("。") == HAN
    assert script_of(",") == NEUTRAL
    assert script_of("7") == NEUTRAL
    assert script_of(" ") == NEUTRAL


def test_text_runs_keeps_punctuation_with_its_script():
    runs = text_runs("你好，world!")
    assert runs == [("你好，", HAN), ("world!", LATIN)]


def test_text_runs_lets_leading_neutral_text_borrow_the_next_script():
    # Leading neutrals join the following script, so this stays a single run.
    assert text_runs("  ...你好") == [("  ...你好", HAN)]


def test_text_runs_handles_mixed_and_empty_input():
    assert text_runs("") == []
    assert text_runs("abc") == [("abc", LATIN)]
    runs = text_runs("A汉")
    assert [script for _, script in runs] == [LATIN, HAN]


def test_pick_family_prefers_the_first_available_candidate():
    assert pick_family(HAN, ["SimSun", "Times New Roman"]) == "SimSun"
    assert pick_family(HAN, ["宋体"]) == "宋体"
    assert pick_family(LATIN, ["Times New Roman"]) == "Times New Roman"
    # Nothing suitable installed: let Qt fall back rather than force a wrong font.
    assert pick_family(HAN, ["Arial"]) == ""
    assert pick_family("klingon", ["Noto Sans"]) == "Noto Sans"


def test_only_latin_is_italic():
    assert wants_italic(LATIN) is True
    assert wants_italic(HAN) is False
    assert wants_italic("klingon") is False


def test_parse_time_accepts_several_spellings():
    assert parse_time("12.34") == pytest.approx(12.34)
    assert parse_time("0:12.34") == pytest.approx(12.34)
    assert parse_time("1:02:03.5") == pytest.approx(3723.5)
    assert parse_time(" 5 ") == pytest.approx(5.0)
    assert parse_time("-3") == pytest.approx(0.0)
    assert parse_time("abc") is None
    assert parse_time("1:2:3:4") is None
    assert parse_time("") is None


def test_format_time_round_trips_through_parse_time():
    for value in (0.0, 1.5, 61.25, 3723.5):
        assert parse_time(format_time(value)) == pytest.approx(value, abs=0.01)
    assert format_time(0.0) == "00:00.00"
    assert format_time(61.25) == "01:01.25"


def test_to_html_uses_a_font_per_script():
    markup = to_html("你好world", "#ffd166", ["SimSun", "Times New Roman"])
    assert "font-family:'SimSun'" in markup
    assert "font-family:'Times New Roman'" in markup
    assert "color:#ffd166" in markup
    # Only the Latin run is italic, so 你好 keeps its upright face.
    assert markup.count("font-style:italic") == 1
    assert markup.index("SimSun") < markup.index("Times New Roman")


def test_to_html_escapes_markup_and_keeps_spaces():
    markup = to_html("a <b> c", "#fff", [])
    assert "&lt;b&gt;" in markup
    assert "&nbsp;" in markup
    assert "<b>" not in markup


def test_to_html_falls_back_to_the_widget_default():
    markup = to_html("你好", "#123456", [])
    assert "color:#123456" in markup
    assert "font-family" not in markup
    assert to_html("", "#fff", []) == ""
