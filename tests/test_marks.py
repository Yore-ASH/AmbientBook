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


BACKSLASH = chr(92)

# --------------------------------------------------------------------------
# escapes turn back into marks when writing a source file
# --------------------------------------------------------------------------

def test_contract_undoes_expand():
    for original in (
        BACKSLASH + "co?00ffaa上色" + BACKSLASH + "co",
        "前" + BACKSLASH + "ge整体" + BACKSLASH + "ge后",
        BACKSLASH + "co?ff0000红" + BACKSLASH + "co和" + BACKSLASH + "co?00ff00绿" + BACKSLASH + "co",
        "普通文本",
    ):
        assert marks.contract_colours(marks.expand_colours(original)) == original


def test_contract_leaves_foreign_escapes_alone():
    """A character's own style has no mark, so it must survive verbatim."""

    styled = "\x1b[1;33mFISH\x1b[0m"
    out = marks.contract_colours(styled)
    assert out.startswith("\x1b[1;33mFISH")
    # Only the reset, which \co means exactly, is rewritten.
    assert out.endswith(BACKSLASH + "co")


def test_contract_is_a_no_op_without_escapes():
    assert marks.contract_colours("普通文本") == "普通文本"
    assert marks.contract_colours("") == ""


def test_contract_pads_short_components():
    assert marks.contract_colours("\x1b[38;2;1;2;3m") == BACKSLASH + "co?010203"


# --------------------------------------------------------------------------
# tokenising: the editor has to see the markup, not only its effect
# --------------------------------------------------------------------------

def test_a_colour_mark_becomes_a_labelled_token():
    tokens = marks.tokenize(BACKSLASH + "co?00ffaa绿灯" + BACKSLASH + "co")

    assert [t.kind for t in tokens] == [marks.COLOUR, marks.TEXT, marks.RESET]
    assert tokens[0].label == "#00ffaa"
    assert tokens[0].colour == "#00ffaa"
    # The text run knows what colour it is in, so it can be drawn directly.
    assert tokens[1].colour == "#00ffaa"
    assert tokens[1].source == "绿灯"
    assert tokens[2].colour == ""


def test_a_group_mark_is_labelled_by_which_end_it_is():
    tokens = marks.tokenize("前" + BACKSLASH + "ge中间" + BACKSLASH + "ge后")
    groups = [t for t in tokens if t.kind == marks.GROUP]
    assert [t.label for t in groups] == ["整体开始", "整体结束"]


def test_a_real_escape_is_recognised_and_tinted():
    tokens = marks.tokenize("\x1b[1;33mFISH\x1b[0m")
    kinds = [t.kind for t in tokens]
    assert kinds == [marks.ANSI, marks.TEXT, marks.ANSI]
    assert tokens[0].colour == "#cccc33"      # 33 is yellow in the palette
    assert tokens[1].colour == "#cccc33"
    assert tokens[2].colour == ""


def test_the_literal_escape_spelling_is_recognised_too():
    """Older files write two characters rather than a real escape."""

    tokens = marks.tokenize(BACKSLASH + "033[36mBIG" + BACKSLASH + "033[0m")
    assert tokens[0].kind == marks.ANSI
    assert tokens[0].colour == "#33cccc"


def test_truecolour_escapes_report_their_colour():
    tokens = marks.tokenize("\x1b[38;2;0;255;170mX")
    assert tokens[0].colour == "#00ffaa"


def test_plain_text_is_one_run():
    tokens = marks.tokenize("普通的一句话")
    assert len(tokens) == 1
    assert tokens[0].kind == marks.TEXT
    assert tokens[0].colour == ""


def test_tokens_rebuild_the_original_text():
    """Nothing may be lost: the editor saves what it was given."""

    for line in (
        BACKSLASH + "co?00ffaa绿灯" + BACKSLASH + "co 后面",
        "前" + BACKSLASH + "ge整个名字" + BACKSLASH + "ge后",
        "\x1b[1;33mFISH\x1b[0m 说话",
        BACKSLASH + "033[36mBIG" + BACKSLASH + "033[0m 收",
        "普通文本",
        "",
    ):
        assert "".join(t.source for t in marks.tokenize(line)) == line


# --------------------------------------------------------------------------
# blocks: what gets its own frame
# --------------------------------------------------------------------------

def test_a_colour_run_is_one_block():
    grouped = marks.blocks(BACKSLASH + "co?00ffaa绿灯" + BACKSLASH + "co 后面")
    assert len(grouped) == 2
    assert [t.kind for t in grouped[0]] == [marks.COLOUR, marks.TEXT, marks.RESET]
    assert [t.kind for t in grouped[1]] == [marks.TEXT]


def test_a_group_is_one_block():
    grouped = marks.blocks("前" + BACKSLASH + "ge整体" + BACKSLASH + "ge后")
    assert [[t.kind for t in block] for block in grouped] == [
        [marks.TEXT],
        [marks.GROUP, marks.TEXT, marks.GROUP],
        [marks.TEXT],
    ]


def test_an_unclosed_colour_still_gets_a_block():
    grouped = marks.blocks(BACKSLASH + "co?ff0000一直红")
    assert [t.kind for t in grouped[0]] == [marks.COLOUR, marks.TEXT]


def test_blocks_cover_every_token():
    line = BACKSLASH + "co?00ffaa甲" + BACKSLASH + "co乙" + BACKSLASH + "ge丙" + BACKSLASH + "ge丁"
    flat = [t.source for block in marks.blocks(line) for t in block]
    assert "".join(flat) == line


# --------------------------------------------------------------------------
# the editor html
# --------------------------------------------------------------------------

def test_the_editor_html_frames_and_colours():
    html = marks.to_editor_html(BACKSLASH + "co?00ffaa绿灯" + BACKSLASH + "co")

    assert "border:1px solid" in html          # the block has a frame
    assert "color:#00ffaa" in html             # the text is drawn in its colour
    assert "绿灯" in html


def test_a_group_block_looks_different_from_a_colour_block():
    colour = marks.to_editor_html(BACKSLASH + "co?00ffaa甲" + BACKSLASH + "co")
    group = marks.to_editor_html(BACKSLASH + "ge甲" + BACKSLASH + "ge")

    assert marks.GROUP_BORDER in group
    assert marks.GROUP_BORDER not in colour
    assert marks.CHIP_BORDER in colour


def test_the_editor_html_escapes_markup():
    html = marks.to_editor_html("<b>不是标签</b>")
    assert "&lt;b&gt;" in html
    assert "<b>" not in html


def test_the_editor_html_handles_a_plain_line():
    html = marks.to_editor_html("普通的一句话")
    assert "普通的一句话" in html
    assert "border:1px solid" not in html
