from tscp_player.format import Dialogue, Directive, Script, parse_tscp, parse_tscps, serialize_tscp, visible_text_length


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
