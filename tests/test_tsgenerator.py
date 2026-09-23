import pytest
import json

from TSGenerator.model import (
    character_keys,
    character_keys_from_source,
    load_character_config,
    parse_sleep_text,
    split_pages,
    validate_character_key,
    validate_directive,
)
from tscp_player.format import Dialogue, Directive, Script, parse_tscps, serialize_tscps


def test_source_round_trip_has_no_timing_data():
    source = "<c>\n[f]你好\n旁白\n<s>0.5\n<p>theme\n"
    parsed = parse_tscps(source)

    assert serialize_tscps(parsed) == source
    assert parse_tscps(serialize_tscps(parsed)) == parsed
    assert all(isinstance(line, (Dialogue, Directive)) for line in parsed.lines)
    assert all(not isinstance(line, Dialogue) or line.delays == [] for line in parsed.lines)


def test_source_helpers_validate_keys_and_directives():
    assert validate_character_key("  f  ") == "f"
    assert parse_sleep_text(" 0.5 ") == "0.5"
    assert validate_directive("clear") == Directive("c")
    assert validate_directive("music", " theme ") == Directive("p", "theme")
    assert character_keys(Script([Dialogue("f", "a"), Dialogue("f", "b")])) == ["f"]

    with pytest.raises(ValueError):
        validate_character_key("bad]")
    with pytest.raises(ValueError):
        parse_sleep_text("nan")
    with pytest.raises(ValueError):
        parse_sleep_text("-1")
    with pytest.raises(ValueError):
        validate_directive("music", "")


def test_source_character_keys_include_plot_metadata(tmp_path):
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    (scripts / "__init__.json").write_text(
        json.dumps({
            "CHARACTERS": {
                "f": {"NAME": "FISH", "STYLE": "\u001b[33m"},
                "m": {"NAME": "MAGE"},
            }
        }),
        encoding="utf-8",
    )
    source = scripts / "scene.tscps"
    source.write_text("[f]你好\n[x]新角色\n", encoding="utf-8")

    characters = load_character_config(source)
    assert characters["f"].name == "FISH"
    assert characters["f"].style == "\u001b[33m"
    assert character_keys_from_source(source) == ["f", "m", "x"]


def test_split_pages_uses_clear_directives_as_boundaries():
    script = parse_tscps("第一页\n<c>\n[f]第二页\n<c>\n旁白\n")

    pages = split_pages(script)

    assert [[line.text for line in page.lines] for page in pages] == [
        ["第一页"],
        ["第二页"],
        ["旁白"],
    ]
