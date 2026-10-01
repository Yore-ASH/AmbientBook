"""Per-line lyric styling, translations, and the layout they need.

Two things are being defended here:

* ``原文|翻译`` and the ``{"t":...,"c":[...]}`` rows that some exporters emit
  must both survive parsing -- silently dropping a row loses lyrics;
* a line's own font and colour cannot live in LRC, so they ride in the JSON
  document, and the wrap/column maths has to work without Qt so it can be tested
  at all.
"""

import zipfile
from pathlib import Path

import pytest

from tscp_player import musicpack
from tscp_player.lyrics import (
    LYRIC_DOCUMENT_FORMAT,
    LyricError,
    LyricLine,
    Lyrics,
    format_time,
    has_line_styles,
    looks_like_lyric_document,
    parse_lyric_document,
    parse_lyrics,
    parse_lrc,
    plan_lyric_rows,
    serialize_lrc,
    serialize_lyric_document,
    split_translation,
    to_html,
    wrap_text,
    wrap_units,
)

LRC = "[00:01.00]Hello|你好\n[00:03.00]Just a line\n"


# --------------------------------------------------------------------------
# translations
# --------------------------------------------------------------------------

def test_split_translation():
    assert split_translation("原文|翻译") == ("原文", "翻译")
    assert split_translation("no pipe here") == ("no pipe here", "")
    # Only the first separator counts, so a translation may contain one.
    assert split_translation("a|b|c") == ("a", "b|c")
    # Surrounding spaces around the separator are not part of either half.
    assert split_translation("原文 | 翻译") == ("原文", "翻译")


def test_a_translation_is_parsed_out_of_the_text():
    lyrics = parse_lrc(LRC)
    assert [line.text for line in lyrics.lines] == ["Hello", "Just a line"]
    assert [line.translation for line in lyrics.lines] == ["你好", ""]
    assert [line.has_translation for line in lyrics.lines] == [True, False]


def test_a_translation_survives_the_lrc_round_trip():
    assert serialize_lrc(parse_lrc(LRC)) == LRC


def test_a_lrc_without_translations_is_unchanged():
    plain = "[00:01.00]one\n[00:02.00]two\n"
    assert serialize_lrc(parse_lrc(plain)) == plain


# --------------------------------------------------------------------------
# the karaoke rows some exporters emit
# --------------------------------------------------------------------------

KARAOKE = (
    '{"t":0,"c":[{"tx":"词 Lyricist: "},{"tx":"Jules"},{"tx":"/Melqui"}]}\n'
    "[00:18.77]Through the ashes\n"
    '{"t":18025,"c":[{"tx":"音乐录制管理: "},{"tx":"TuningPop"}]}\n'
)


def test_karaoke_rows_are_read_rather_than_dropped():
    lyrics = parse_lrc(KARAOKE)
    assert len(lyrics.lines) == 3
    # Lines come back sorted by time, not in file order.
    assert [line.time for line in lyrics.lines] == [0.0, 18.025, 18.77]
    assert lyrics.lines[0].text == "词 Lyricist: Jules/Melqui"
    assert lyrics.lines[1].text == "音乐录制管理: TuningPop"
    assert lyrics.lines[2].text == "Through the ashes"


def test_a_karaoke_row_may_carry_a_translation():
    row = '{"t":1000,"c":[{"tx":"Hello"},{"tx":"|"},{"tx":"你好"}]}'
    lyrics = parse_lrc(row)
    assert lyrics.lines[0].text == "Hello"
    assert lyrics.lines[0].translation == "你好"


def test_junk_braces_are_still_skipped():
    lyrics = parse_lrc("{not json at all\n[00:01.00]real\n")
    assert [line.text for line in lyrics.lines] == ["real"]

    # Well-formed JSON that is not a karaoke row is ignored too.
    assert parse_lrc('{"hello": "world"}\n[00:01.00]real\n').lines[0].text == "real"


def test_a_karaoke_row_never_invents_a_timestamp():
    assert parse_lrc('{"t":"x","c":[{"tx":"a"}]}').lines == []
    assert parse_lrc('{"t":100}').lines == []


# --------------------------------------------------------------------------
# the JSON document
# --------------------------------------------------------------------------

STYLED = Lyrics([
    LyricLine(1.0, "Hello", translation="你好", font="SimSun", color="#ffd166"),
    LyricLine(2.0, "Plain"),
])


def test_the_document_round_trips_every_field():
    again = parse_lyric_document(serialize_lyric_document(STYLED))
    assert again.lines == STYLED.lines


def test_the_document_omits_empty_keys():
    text = serialize_lyric_document(STYLED)
    assert LYRIC_DOCUMENT_FORMAT in text
    # The plain line carries nothing beyond its time and text.
    assert '"translation"' not in text.split("Plain")[0].split("{")[-1]
    assert text.count('"font"') == 1
    assert text.count('"color"') == 1
    assert text.count('"translation"') == 1


def test_has_line_styles():
    assert has_line_styles(STYLED) is True
    assert has_line_styles(Lyrics([LyricLine(1.0, "x")])) is False
    assert has_line_styles(Lyrics([LyricLine(1.0, "x", color="#fff")])) is True


def test_looks_like_lyric_document():
    assert looks_like_lyric_document(serialize_lyric_document(STYLED)) is True
    assert looks_like_lyric_document(LRC) is False
    assert looks_like_lyric_document('{"t":0,"c":[]}') is False


def test_parse_lyrics_picks_the_right_form():
    assert parse_lyrics(LRC).lines[0].text == "Hello"
    assert parse_lyrics(serialize_lyric_document(STYLED)).lines == STYLED.lines


def test_a_broken_document_says_so():
    for broken in ('{"LINES": "nope"}', "[1,2,3]", "{not json", '{"LINES":[{"text":"x"}]}'):
        with pytest.raises(LyricError):
            parse_lyric_document(broken)


def test_line_at_returns_the_whole_line():
    lyrics = parse_lrc(LRC)
    assert lyrics.line_at(0.5) is None
    assert lyrics.line_at(1.0).translation == "你好"
    assert lyrics.line_at(99.0).text == "Just a line"
    assert lyrics.at(1.0) == "Hello"


# --------------------------------------------------------------------------
# wrapping and the two-column layout
# --------------------------------------------------------------------------

def width(text: str) -> int:
    """Stand-in for real text measurement: one unit per character."""

    return len(text)


def test_wrap_units_breaks_latin_on_spaces_and_cjk_anywhere():
    assert wrap_units("hello world") == ["hello", " ", "world"]
    assert wrap_units("你好世界") == ["你", "好", "世", "界"]
    assert wrap_units("hi 你好 there") == ["hi", " ", "你", "好", " ", "there"]


def test_wrap_text_keeps_words_whole():
    assert wrap_text("hello world again", width, 10) == ["hello", "world", "again"]
    assert wrap_text("short", width, 10) == ["short"]
    assert wrap_text("", width, 10) == [""]


def test_wrap_text_breaks_cjk_between_characters():
    assert wrap_text("你好世界你好", width, 4) == ["你好世界", "你好"]


def test_wrap_text_never_exceeds_the_limit_when_it_can_avoid_it():
    for line in wrap_text("the quick brown fox jumps over the lazy dog", width, 12):
        assert len(line) <= 12


def test_wrap_text_handles_a_word_longer_than_the_limit():
    lines = wrap_text("supercalifragilistic", width, 5)
    assert "".join(lines) == "supercalifragilistic"


def test_no_translation_is_a_single_column():
    assert plan_lyric_rows("abc", "", width, 40) == [("abc", "")]
    assert plan_lyric_rows("aaaa bbbb cccc", "", width, 9) == [
        ("aaaa bbbb", ""),
        ("cccc", ""),
    ]


def test_a_short_pair_sits_on_one_row():
    assert plan_lyric_rows("hello", "你好", width, 40, gap=2) == [("hello", "你好")]


def test_a_long_pair_wraps_into_two_columns():
    rows = plan_lyric_rows("one two three four", "一二三四五六", width, 12, gap=2)
    assert len(rows) > 1
    assert all(isinstance(left, str) and isinstance(right, str) for left, right in rows)
    # Nothing is lost from either half.
    assert "".join(left for left, _ in rows).replace(" ", "") == "onetwothreefour"
    assert "".join(right for _, right in rows) == "一二三四五六"


def test_the_shorter_column_is_centred():
    """A one-line translation should not be pinned to the top."""

    rows = plan_lyric_rows("a b c d e f", "短", width, 10, gap=2)
    assert len(rows) >= 3
    filled = [index for index, (_l, r) in enumerate(rows) if r]
    middle = len(rows) // 2
    assert filled == [middle] or abs(filled[0] - middle) <= 1


def test_the_pair_is_never_wider_than_the_limit():
    rows = plan_lyric_rows("a very long original", "一条很长很长的翻译", width, 20, gap=2)
    for left, right in rows:
        assert width(left) <= 10
        assert width(right) <= 10


# --------------------------------------------------------------------------
# typography
# --------------------------------------------------------------------------

def test_a_per_line_font_overrides_the_script_choice():
    html = to_html("Hello 你好", "#fff", ["宋体", "Times New Roman"], family="SimSun")
    assert html.count("SimSun") == 2
    assert "Times New Roman" not in html
    # A forced family applies to the whole line, so nothing is italicised.
    assert "italic" not in html


def test_without_a_font_the_script_still_decides():
    html = to_html("Hello", "#fff", ["Times New Roman"])
    assert "Times New Roman" in html
    assert "italic" in html


# --------------------------------------------------------------------------
# storing the document in a .tscpmc
# --------------------------------------------------------------------------

def _flac(audio: bytes = b"AUDIOFRAMES" * 8) -> bytes:
    streaminfo = bytes(34)
    out = bytearray(b"fLaC")
    out.append(0x80 | 0)
    out += len(streaminfo).to_bytes(3, "big")
    out += streaminfo
    out += audio
    return bytes(out)


@pytest.fixture()
def pack(tmp_path):
    source = tmp_path / "track.flac"
    source.write_bytes(_flac())
    return musicpack.write(
        tmp_path / "out", source, serialize_lrc(STYLED), title="t", lines=STYLED
    )


def test_a_styled_pack_carries_the_document(pack):
    loaded = musicpack.read(pack)
    assert loaded.document
    assert loaded.lines().lines == STYLED.lines
    with zipfile.ZipFile(pack) as archive:
        assert musicpack.LYRIC_DOCUMENT_MEMBER in archive.namelist()
        import json

        manifest = json.loads(archive.read(musicpack.MANIFEST).decode("utf-8"))
    assert manifest["LYRICS_JSON"] == musicpack.LYRIC_DOCUMENT_MEMBER


def test_an_unstyled_pack_does_not_grow_a_document(tmp_path):
    source = tmp_path / "track.flac"
    source.write_bytes(_flac())
    plain = Lyrics([LyricLine(1.0, "x")])
    pack = musicpack.write(tmp_path / "out", source, serialize_lrc(plain), lines=plain)
    loaded = musicpack.read(pack)
    assert loaded.document == ""
    with zipfile.ZipFile(pack) as archive:
        assert musicpack.LYRIC_DOCUMENT_MEMBER not in archive.namelist()
    assert loaded.lines().lines == plain.lines


def test_rewrite_lyrics_keeps_the_audio(pack, tmp_path):
    before = musicpack.read(pack)
    new_lines = Lyrics([LyricLine(1.0, "changed", font="Arial")])
    musicpack.rewrite_lyrics(pack, lines=new_lines)
    after = musicpack.read(pack)
    assert after.lines().lines == new_lines.lines
    assert after.audio_name == before.audio_name
    # Same recording: only the metadata differs.
    assert (
        musicpack._flac_split(after.audio)[1] == musicpack._flac_split(before.audio)[1]
    )
    assert after.color == before.color
    assert after.title == before.title


def test_rewrite_lyrics_can_change_the_colour(pack):
    musicpack.rewrite_lyrics(pack, color="#123456")
    assert musicpack.read(pack).color == "#123456"


def test_rewrite_lyrics_clears_stale_styles(pack):
    assert musicpack.read(pack).document
    musicpack.rewrite_lyrics(pack, lines=Lyrics([LyricLine(1.0, "x")]))
    assert musicpack.read(pack).document == ""


def test_rewrite_lyrics_updates_the_embedded_tag(pack):
    lines = Lyrics([LyricLine(1.0, "brand new")])
    musicpack.rewrite_lyrics(pack, lines=lines)
    loaded = musicpack.read(pack)
    assert musicpack.read_embedded_lyrics(loaded.audio, loaded.audio_name) == loaded.lyrics


def test_rewrite_lyrics_leaves_no_temp_file(pack):
    musicpack.rewrite_lyrics(pack, lyrics="[00:01.00]x\n")
    leftovers = list(Path(pack).parent.glob("*.rewriting"))
    assert leftovers == []


# --------------------------------------------------------------------------
# the normalise command
# --------------------------------------------------------------------------

MIXED = (
    '{"t":0,"c":[{"tx":"词 Lyricist: "},{"tx":"Jules"}]}\n'
    '{"t":721,"c":[{"tx":"曲 Composer: "},{"tx":"Melqui"}]}\n'
    "[00:18.77]Through the ashes\n"
    "[00:22.77]Where the trail fades away\n"
)


@pytest.fixture()
def mixed_pack(tmp_path):
    source = tmp_path / "track.flac"
    source.write_bytes(_flac())
    return musicpack.write(tmp_path / "mixed", source, MIXED, title="mixed")


def test_normalise_rewrites_the_lyrics_as_plain_lrc(mixed_pack, capsys):
    before = musicpack.read(mixed_pack)
    assert musicpack.main([str(mixed_pack), "--normalise"]) == 0

    after = musicpack.read(mixed_pack)
    rows = [row for row in after.lyrics.splitlines() if row.strip()]
    assert len(rows) == 4
    assert all(row.startswith("[") for row in rows)
    assert not any(row.startswith("{") for row in rows)
    assert "词 Lyricist: Jules" in after.lyrics
    assert "已重写" in capsys.readouterr().out

    # Times are preserved to LRC's own precision.
    before_times = [round(line.time, 2) for line in before.lines().lines]
    after_times = [round(line.time, 2) for line in after.lines().lines]
    assert before_times == after_times
    # Nothing else moves.
    assert after.color == before.color
    assert after.title == before.title
    assert musicpack._flac_split(after.audio)[1] == musicpack._flac_split(before.audio)[1]


def test_normalise_keeps_the_old_text_beside_the_file(mixed_pack):
    musicpack.main([str(mixed_pack), "--normalise"])
    backup = Path(str(mixed_pack) + ".lrc.bak")
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == MIXED


def test_normalise_refuses_a_pack_with_no_lyrics(tmp_path, capsys):
    source = tmp_path / "track.flac"
    source.write_bytes(_flac())
    pack = musicpack.write(tmp_path / "quiet", source, "", kind="instrumental")
    assert musicpack.main([str(pack), "--normalise"]) == 2
    assert "没有可解析的歌词" in capsys.readouterr().err


def test_normalise_can_write_somewhere_else(mixed_pack, tmp_path):
    target = tmp_path / "clean.tscpmc"
    assert musicpack.main([str(mixed_pack), "--normalise", "-o", str(target)]) == 0
    assert target.is_file()
    # The source is left alone, and no backup is made for it.
    assert "{" in musicpack.read(mixed_pack).lyrics
    assert not Path(str(mixed_pack) + ".lrc.bak").exists()
