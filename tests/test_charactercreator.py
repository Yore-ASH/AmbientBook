import json

import pytest

from CharacterCreator.model import (
    build_ansi_style,
    generate_json_entry,
    generate_json_entries,
    normalize_ansi,
    parse_batch_text,
    validate_abbreviation,
    validate_full_name,
)


def test_build_styles_and_normalize_escape_spellings():
    assert build_ansi_style(33, bold=True, underline=True) == "\033[1;4;33m"
    assert normalize_ansi(r"\x1b[33;1m") == "\033[33;1m"
    assert normalize_ansi("\x1b[4m") == "\033[4m"
    assert build_ansi_style() == ""


def test_custom_style_rejects_italic():
    with pytest.raises(ValueError, match="斜体"):
        normalize_ansi(r"\033[3;31m")
    with pytest.raises(ValueError):
        normalize_ansi("not ansi")


def test_generated_entry_is_valid_json_and_has_expected_shape():
    text = generate_json_entry(" f ", "鱼", r"\033[33;4m")
    data = json.loads("{" + text + "}")
    assert data == {"f": {"NAME": "鱼", "STYLE": "\033[33;4m"}}
    assert not text.endswith(",")


def test_names_are_validated():
    assert validate_abbreviation("  mage ") == "mage"
    assert validate_full_name("  MAGE ") == "MAGE"
    with pytest.raises(ValueError):
        validate_abbreviation("bad]")
    with pytest.raises(ValueError):
        validate_full_name("two\nlines")


def test_batch_generation():
    rows = parse_batch_text("f\tFISH\t\\033[33m\nm\tMAGE\t")
    result = json.loads(generate_json_entries(rows))
    assert result["f"]["NAME"] == "FISH"
    assert result["m"]["STYLE"] == ""
    with pytest.raises(ValueError, match="重复"):
        generate_json_entries([("f", "FISH", ""), ("f", "Other", "")])
