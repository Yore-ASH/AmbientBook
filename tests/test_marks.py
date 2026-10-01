"""Tests for the author-facing inline marks."""

import pytest

from tscp_player import marks


# --------------------------------------------------------------------------
# colour
# --------------------------------------------------------------------------

def test_a_colour_becomes_a_real_escape():
    result = marks.parse(r"\co?00ffaa绿灯\co")
    assert result.visible == "绿灯"
    assert result.rendered.startswith("\033[38;2;0;255;170m")
    assert result.rendered.endswith("\033[0m")
    assert "绿灯" in result.rendered


def test_colour_is_case_insensitive():
    upper = marks.parse(r"\co?00FFAAx\co")
    lower = marks.parse(r"\co?00ffaax\co")
    assert upper.rendered == lower.rendered


def test_several_coloured_runs():
    text = r"\co?ff0000红\co 和 \co?00ff00绿\co"
    result = marks.parse(text)
    assert result.visible == "红 和 绿"
    assert "\033[38;2;255;0;0m" in result.rendered
    assert "\033[38;2;0;255;0m" in result.rendered
    assert result.rendered.count("\033[0m") == 2


def test_an_unclosed_colour_runs_to_the_end():
    result = marks.parse(r"\co?ff0000一直红下去")
    assert result.visible == "一直红下去"
    assert "\033[38;2;255;0;0m" in result.rendered


def test_a_colour_without_hex_is_left_alone():
    """Not our syntax, so it must survive verbatim rather than vanish."""

    result = marks.parse(r"\co?xyz 不是颜色")
    assert "不是颜色" in result.visible


# --------------------------------------------------------------------------
# grouping
# --------------------------------------------------------------------------

def test_a_group_is_removed_from_the_visible_text():
    result = marks.parse(r"\ge系统提示\ge")
    assert result.visible == "系统提示"
    assert "\\ge" not in result.rendered
    assert result.rendered == "系统提示"


def test_a_group_reports_its_range_in_visible_space():
    result = marks.parse(r"前\ge中间\ge后")
    assert result.visible == "前中间后"
    assert result.groups == ((1, 3),)


def test_two_groups():
    result = marks.parse(r"\ge甲\ge\ge乙\ge")
    assert result.visible == "甲乙"
    assert result.groups == ((0, 1), (1, 2))


def test_an_unclosed_group_runs_to_the_end():
    result = marks.parse(r"前\ge后面全部")
    assert result.visible == "前后面全部"
    assert result.groups == ((1, 5),)


def test_groups_and_colours_together():
    text = r"前\ge\co?00ffaa整体上色\co\ge后"
    result = marks.parse(text)
    assert result.visible == "前整体上色后"
    assert result.groups == ((1, 5),)
    assert "\033[38;2;0;255;170m" in result.rendered
    assert "\\ge" not in result.rendered


# --------------------------------------------------------------------------
# plain text is untouched
# --------------------------------------------------------------------------

def test_ordinary_text_passes_through():
    for text in ("普通一句话", "with spaces and 标点，。！", "a\\b is fine"):
        result = marks.parse(text)
        assert result.visible == text
        assert result.rendered == text
        assert result.groups == ()


def test_an_empty_string():
    result = marks.parse("")
    assert result.visible == ""
    assert result.rendered == ""
    assert not result


def test_has_marks():
    assert marks.has_marks(r"\gex\ge")
    assert marks.has_marks(r"\co?ffffffx\co")
    assert not marks.has_marks("plain")
    assert not marks.has_marks("a backslash \\ alone")


# --------------------------------------------------------------------------
# the helpers agree with parse()
# --------------------------------------------------------------------------

def test_the_helpers_match_parse():
    text = r"前\ge\co?00ffaa整体\co\ge后"
    parsed = marks.parse(text)
    assert marks.strip(text) == parsed.visible
    assert marks.render(text) == parsed.rendered
    assert marks.groups(text) == parsed.groups
