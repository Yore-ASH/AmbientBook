from PlotWriter.model import character_keys_from_source, parse_delay_text
from tscp_player.format import Dialogue, Script, parse_tscp, serialize_tscp


def test_plotwriter_helpers_and_compiled_round_trip(tmp_path):
    source = tmp_path / "plot.tscps"
    source.write_text("<c>\n[f]你好\n旁白\n", encoding="utf-8")
    assert character_keys_from_source(source) == ["f"]

    delays = parse_delay_text("[0.1, 0.2]", 2)
    script = Script([Dialogue("f", "你好", delays)])
    assert parse_tscp(serialize_tscp(script)) == script


def test_blank_delays_use_default():
    assert parse_delay_text("", 3, 0.25) == [0.25, 0.25, 0.25]
