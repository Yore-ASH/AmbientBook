import pytest

from tscp_player.format import Dialogue, Directive, Script, parse_tscp, parse_tscps, serialize_tscp, serialize_tscps, visible_text_length


def test_source_and_compiled_round_trip():
    source = parse_tscps("<c>\n[f]你好\n旁白\n<p>theme\n<s>0.5\n")
    compiled = Script([
        source.lines[0],
        Dialogue("f", "你好", [0.1, 0.2]),
        Dialogue(None, "旁白", [0.3, 0.4]),
        source.lines[3],
        source.lines[4],
    ])
    assert parse_tscp(serialize_tscp(compiled)) == compiled


def test_ansi_sequences_do_not_require_timing_entries():
    text = r"\033[1;91m警告\033[0m"
    assert visible_text_length(text) == 2
    compiled = Script([Dialogue(None, text, [0.1, 0.2])])
    assert parse_tscp(serialize_tscp(compiled)) == compiled


# --------------------------------------------------------------------------
# supplements: the aside shown below the story
# --------------------------------------------------------------------------

def test_supplement_survives_both_formats():
    from tscp_player.format import make_note, note_parts

    script = Script([
        Dialogue("f", "你好"),
        make_note("这是一句补充", color="#ffd166", seconds=4.5),
    ])
    source = serialize_tscps(script)
    assert "<a>#ffd166,4.5 这是一句补充" in source
    from_source = parse_tscps(source)
    assert note_parts(from_source.lines[1]) == ("#ffd166", 4.5, "这是一句补充")

    compiled = serialize_tscp(script)
    assert "A|#ffd166|4.500000|" in compiled
    from_compiled = parse_tscp(compiled)
    assert note_parts(from_compiled.lines[1]) == ("#ffd166", 4.5, "这是一句补充")
    # Both routes agree, and re-serialising is stable.
    assert serialize_tscp(from_source) == compiled


def test_every_source_spelling_of_a_supplement():
    from tscp_player.format import note_parts

    cases = {
        "<a>只是补充": ("", 3.0, "只是补充"),
        "<a>4.5 四秒半": ("", 4.5, "四秒半"),
        "<a>#ffd166,4.5 都写": ("#ffd166", 4.5, "都写"),
        "<a>#ffd166 只有颜色": ("#ffd166", 3.0, "只有颜色"),
        "<a>,2.0 只有时长": ("", 2.0, "只有时长"),
    }
    for line, expected in cases.items():
        script = parse_tscps(line + "\n")
        assert note_parts(script.lines[0]) == expected, line


def test_a_supplement_can_contain_pipes_and_spaces():
    """The text is the tail of the value, so separators inside it are safe."""

    from tscp_player.format import note_parts

    text = "前半 | 后半 还有 空格"
    script = parse_tscps("<a>#ffd166,4 " + text + "\n")
    assert note_parts(script.lines[0])[2] == text
    compiled = serialize_tscp(script)
    # Base64 keeps the pipe out of the compiled line.
    assert compiled.strip().splitlines()[-1].count("|") == 3
    assert note_parts(parse_tscp(compiled).lines[0])[2] == text


def test_supplements_reject_nonsense():
    from tscp_player.format import TSCPError, make_note

    with pytest.raises(TSCPError):
        make_note("   ")
    with pytest.raises(TSCPError):
        make_note("换\n行")
    with pytest.raises(TSCPError):
        make_note("颜色不对", color="red")
    with pytest.raises(TSCPError):
        make_note("负时长", seconds=-1)

    for bad in ("<a>\n", "<a>#ffd166,abc 时长不对\n"):
        with pytest.raises(TSCPError):
            parse_tscps(bad)


def test_old_scripts_are_untouched_by_the_new_directive():
    """A plot with no supplements must serialise exactly as it always did."""

    script = Script([
        Directive("p", "night"),
        Dialogue("f", "你好", [0.1, 0.2]),
        Directive("s", "2"),
        Directive("c"),
    ])
    assert serialize_tscp(script) == (
        "TSCP 1\nP|night\nD|f|5L2g5aW9|0.100000,0.200000\nS|2\nC\n"
    )
    assert serialize_tscps(script) == "<p>night\n[f]你好\n<s>2\n<c>\n"


def test_unknown_commands_are_still_rejected():
    from tscp_player.format import TSCPError

    with pytest.raises(TSCPError):
        parse_tscps("<x>1\n")
    with pytest.raises(TSCPError):
        parse_tscp("TSCP 1\nZ|nope\n")
