from tscp_player.format import Dialogue, Script
from tscp_player.timing import compile_script


def test_interactive_converter_records_each_character(monkeypatch):
    answers = iter(["", ""])
    clock = iter([1.0, 1.25, 1.5])
    monkeypatch.setattr("tscp_player.timing.time.monotonic", lambda: next(clock))
    result = compile_script(Script([Dialogue("f", "AB")]), input_fn=lambda _: next(answers), output_fn=lambda _: None)
    assert result.lines[0].delays == [0.25, 0.25]
