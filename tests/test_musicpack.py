"""Tests for ``.tscpmc`` -- audio with its lyrics packed alongside.

The audio is the part that must not be damaged, so most of these check that the
bytes after the metadata come back bit-for-bit identical.
"""

import json
import zipfile
from pathlib import Path

import pytest

from tscp_player import archive, musicpack
from tscp_player.musicpack import MusicPackError

LRC = "[00:01.00]末班车驶过\n[00:03.50]Second line\n[00:06.25]第三句\n"
AUDIO_FRAMES = b"AUDIOFRAMES" * 8
REAL_FLAC = Path(__file__).resolve().parent.parent / "source" / "ExamplePlot" / "Musics" / "i want.flac"


def synthetic_flac(audio: bytes = AUDIO_FRAMES) -> bytes:
    """A FLAC with only a STREAMINFO block: no comment block to reuse."""

    streaminfo = bytes(34)
    out = bytearray(b"fLaC")
    out.append(0x80 | 0)                       # last block, type 0
    out += len(streaminfo).to_bytes(3, "big")
    out += streaminfo
    out += audio
    return bytes(out)


def flac_audio_offset() -> int:
    return len(synthetic_flac()) - len(AUDIO_FRAMES)


def synthetic_mp3(audio: bytes = b"\xff\xfb\x90\x00" * 32) -> bytes:
    return audio


def synthetic_flac_with_comment(comments=None) -> bytes:
    comments = list(comments or [b"ARTIST=Somebody"])
    payload = musicpack._vorbis_join(b"reference libFLAC", comments)
    streaminfo = bytes(34)
    out = bytearray(b"fLaC")
    out.append(0x00 | 0)                       # not last, type 0
    out += len(streaminfo).to_bytes(3, "big")
    out += streaminfo
    out.append(0x80 | musicpack.FLAC_COMMENT_BLOCK)
    out += len(payload).to_bytes(3, "big")
    out += payload
    out += AUDIO_FRAMES
    return bytes(out)


def write_audio(tmp_path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------
# FLAC
# --------------------------------------------------------------------------

def test_flac_embed_round_trips():
    tagged = musicpack._flac_write_lyrics(synthetic_flac(), LRC, title="末班车")
    assert musicpack._flac_read_lyrics(tagged) == LRC


def test_flac_audio_frames_are_untouched():
    """The whole point: only metadata changes, never the audio."""

    original = synthetic_flac()
    tagged = musicpack._flac_write_lyrics(original, LRC)
    _blocks, audio = musicpack._flac_split(tagged)
    assert audio == AUDIO_FRAMES
    assert tagged[:4] == b"fLaC"


def test_flac_gains_a_comment_block_after_streaminfo():
    tagged = musicpack._flac_write_lyrics(synthetic_flac(), LRC)
    blocks, _audio = musicpack._flac_split(tagged)
    assert [kind for kind, _payload in blocks] == [0, musicpack.FLAC_COMMENT_BLOCK]


def test_flac_keeps_the_other_comments():
    original = synthetic_flac_with_comment([b"ARTIST=Somebody", b"TITLE=Old"])
    tagged = musicpack._flac_write_lyrics(original, LRC, title="New")
    vendor, comments = musicpack._vorbis_split(
        [p for t, p in musicpack._flac_split(tagged)[0] if t == 4][0]
    )
    joined = b"|".join(comments)
    assert b"ARTIST=Somebody" in joined
    assert vendor == b"reference libFLAC"       # the original vendor survives
    assert b"LYRICS=" + LRC.encode("utf-8") in comments
    assert joined.count(b"TITLE=") == 1         # replaced, not duplicated


def test_flac_embedding_is_idempotent():
    once = musicpack._flac_write_lyrics(synthetic_flac(), LRC, title="X")
    twice = musicpack._flac_write_lyrics(once, LRC, title="X")
    assert twice == once
    assert once.count(b"LYRICS=") == 1


def test_flac_updating_replaces_the_old_lyrics():
    first = musicpack._flac_write_lyrics(synthetic_flac(), LRC)
    second = musicpack._flac_write_lyrics(first, "[00:09.00]换过了\n")
    assert musicpack._flac_read_lyrics(second) == "[00:09.00]换过了\n"
    assert second.count(b"LYRICS=") == 1
    assert musicpack._flac_split(second)[1] == AUDIO_FRAMES


def test_flac_last_block_flag_stays_correct():
    """A decoder walks the chain until the last-block flag; it must be on one."""

    tagged = musicpack._flac_write_lyrics(synthetic_flac_with_comment(), LRC)
    offset = 4
    seen_last = 0
    while True:
        header = tagged[offset]
        if header & 0x80:
            seen_last += 1
        length = int.from_bytes(tagged[offset + 1:offset + 4], "big")
        offset += 4 + length
        if header & 0x80:
            break
    assert seen_last == 1
    assert tagged[offset:] == AUDIO_FRAMES


def test_flac_rejects_junk():
    with pytest.raises(MusicPackError):
        musicpack._flac_split(b"not a flac file at all")
    with pytest.raises(MusicPackError):
        musicpack._flac_split(b"fLaC" + b"\x04\x00\x10short")


# --------------------------------------------------------------------------
# MP3
# --------------------------------------------------------------------------

def test_mp3_embed_round_trips():
    tagged = musicpack._mp3_write_lyrics(synthetic_mp3(), LRC, title="末班车")
    assert tagged[:3] == b"ID3"
    assert musicpack._mp3_read_lyrics(tagged) == LRC


def test_mp3_audio_is_untouched():
    original = synthetic_mp3()
    tagged = musicpack._mp3_write_lyrics(original, LRC)
    _tag, audio = musicpack._id3_split(tagged)
    assert audio == original


def test_mp3_uses_v24_because_the_lyrics_are_utf8():
    """UTF-8 text is only legal from ID3v2.4 on."""

    tagged = musicpack._mp3_write_lyrics(synthetic_mp3(), LRC)
    assert tagged[3] == 4
    assert tagged[4] == 0
    # The frame size is synchsafe: no byte may have its top bit set.
    frame_size = tagged[14:18]
    assert all(byte < 0x80 for byte in frame_size)


def test_mp3_embedding_is_idempotent():
    once = musicpack._mp3_write_lyrics(synthetic_mp3(), LRC, title="X")
    assert musicpack._mp3_write_lyrics(once, LRC, title="X") == once
    assert once.count(b"USLT") == 1


def test_mp3_keeps_the_other_frames():
    with_title = musicpack._mp3_write_lyrics(synthetic_mp3(), LRC, title="旧标题")
    rewritten = musicpack._mp3_write_lyrics(with_title, LRC, title="新标题")
    assert rewritten.count(b"TIT2") == 1
    assert "新标题".encode("utf-8") in rewritten


def test_mp3_reads_a_plain_file_without_a_tag():
    assert musicpack._mp3_read_lyrics(synthetic_mp3()) is None


def test_uslt_decoding_handles_every_encoding():
    # Single-byte encodings terminate the (empty) description with one 0x00;
    # UTF-16 uses two, and then carries a BOM.
    assert musicpack._uslt_text(b"\x03XXX\x00" + "歌词".encode("utf-8")) == "歌词"
    assert musicpack._uslt_text(b"\x00XXX\x00" + "abc".encode("latin-1")) == "abc"
    utf16 = "歌词".encode("utf-16")            # includes a BOM
    assert musicpack._uslt_text(b"\x01XXX" + b"\x00\x00" + utf16) == "歌词"
    utf16be = "歌词".encode("utf-16-be")
    assert musicpack._uslt_text(b"\x02XXX" + b"\x00\x00" + utf16be) == "歌词"


def test_uslt_with_a_description_is_decoded():
    """A non-empty description sits before the lyrics and must be skipped."""

    payload = b"\x03XXX" + "说明".encode("utf-8") + b"\x00" + "正文".encode("utf-8")
    assert musicpack._uslt_text(payload) == "正文"


# --------------------------------------------------------------------------
# tagging dispatch
# --------------------------------------------------------------------------

def test_supports_tagging_matches_what_we_implemented():
    assert musicpack.supports_tagging("a.flac")
    assert musicpack.supports_tagging("a.FLAC")
    assert musicpack.supports_tagging("a.mp3")
    for other in ("a.ogg", "a.opus", "a.wav", "a.m4a"):
        assert musicpack.supports_tagging(other) is False


def test_unsupported_formats_are_returned_untouched():
    raw = b"RIFF" + bytes(32)
    out, tagged = musicpack.embed_lyrics(raw, "a.wav", LRC)
    assert out is raw
    assert tagged is False


def test_read_embedded_on_a_plain_file_is_none():
    assert musicpack.read_embedded_lyrics(synthetic_mp3(), "a.mp3") is None
    assert musicpack.read_embedded_lyrics(b"RIFFxx", "a.wav") is None


# --------------------------------------------------------------------------
# the container
# --------------------------------------------------------------------------

def test_pack_round_trips(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(
        tmp_path / "out", source, LRC, title="末班车", color="#ffd166", kind="lyrics"
    )
    assert pack.suffix == musicpack.PACK_SUFFIX

    loaded = musicpack.read(pack)
    assert loaded.title == "末班车"
    assert loaded.color == "#ffd166"
    assert loaded.kind == "lyrics"
    assert loaded.lyrics == LRC
    assert loaded.audio_name == "track.flac"
    assert loaded.tagged is True
    # The audio inside is the tagged one, and still ends with the same frames.
    _blocks, audio = musicpack._flac_split(loaded.audio)
    assert audio == AUDIO_FRAMES


def test_the_audio_inside_really_carries_the_lyrics(tmp_path):
    """Open the packed audio with any player and the lyrics are there."""

    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, LRC, title="T")
    loaded = musicpack.read(pack)
    assert musicpack.read_embedded_lyrics(loaded.audio, loaded.audio_name) == LRC


def test_the_pack_stores_audio_uncompressed(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, LRC)
    with zipfile.ZipFile(pack) as archive_file:
        info = archive_file.getinfo("Audio/track.flac")
        assert info.compress_type == zipfile.ZIP_STORED


def test_the_manifest_is_readable_json(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, LRC, title="T", color="#abcdef")
    with zipfile.ZipFile(pack) as archive_file:
        document = json.loads(archive_file.read(musicpack.MANIFEST).decode("utf-8"))
    assert document["FORMAT"] == musicpack.PACK_FORMAT
    assert document["AUDIO"] == "Audio/track.flac"
    assert document["EMBEDDED"] is True
    assert document["COLOR"] == "#abcdef"


def test_a_format_without_tags_still_packs(tmp_path):
    source = write_audio(tmp_path, "plain.wav", b"RIFF" + bytes(64))
    pack = musicpack.write(tmp_path / "out", source, LRC)
    loaded = musicpack.read(pack)
    assert loaded.tagged is False
    assert loaded.lyrics == LRC                 # the container is the fallback
    assert loaded.audio == source.read_bytes()


def test_the_pack_refuses_nonsense(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    with pytest.raises(MusicPackError):
        musicpack.write(tmp_path / "out", source, "   ")
    with pytest.raises(MusicPackError):
        musicpack.write(tmp_path / "out", tmp_path / "missing.flac", LRC)
    with pytest.raises(MusicPackError):
        musicpack.write(source, source, LRC)     # never clobber the input


def test_reading_a_non_pack_fails_clearly(tmp_path):
    junk = tmp_path / "nope.tscpmc"
    junk.write_bytes(b"not a zip")
    with pytest.raises(MusicPackError):
        musicpack.read(junk)
    with pytest.raises(MusicPackError):
        musicpack.read(tmp_path / "missing.tscpmc")


def test_a_pack_that_lies_about_its_tags_is_flagged(tmp_path):
    """Trust the bytes, not the manifest."""

    source = write_audio(tmp_path, "track.wav", b"RIFF" + bytes(32))
    pack = musicpack.write(tmp_path / "out", source, LRC)
    # Rewrite the manifest to claim the audio is tagged.
    with zipfile.ZipFile(pack) as archive_file:
        members = {name: archive_file.read(name) for name in archive_file.namelist()}
    document = json.loads(members[musicpack.MANIFEST].decode("utf-8"))
    document["EMBEDDED"] = True
    document["AUDIO"] = "Audio/track.wav"
    members[musicpack.MANIFEST] = json.dumps(document).encode("utf-8")
    with zipfile.ZipFile(pack, "w") as archive_file:
        for name, data in members.items():
            archive_file.writestr(name, data)

    loaded = musicpack.read(pack)
    assert loaded.tagged is False
    assert loaded.notes


def test_extract_audio_and_cache(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, LRC)
    folder = tmp_path / "out-dir"

    plain = musicpack.extract_audio(pack, folder)
    assert plain.name == "track.flac"
    assert musicpack._flac_read_lyrics(plain.read_bytes()) == LRC

    cache = tmp_path / "cache"
    first = musicpack.extract_cached(pack, cache)
    second = musicpack.extract_cached(pack, cache)
    assert first == second
    assert first.is_file()


def test_is_pack(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, LRC)
    assert musicpack.is_pack(pack) is True
    assert musicpack.is_pack(source) is False
    assert musicpack.is_pack(tmp_path / "missing.tscpmc") is False


# --------------------------------------------------------------------------
# against a real recording
# --------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_FLAC.is_file(), reason="sample FLAC not present")
def test_a_real_flac_survives_tagging():
    """The synthetic cases are tidy; a real encoder's output is not."""

    data = REAL_FLAC.read_bytes()
    blocks, audio = musicpack._flac_split(data)
    assert blocks[0][0] == 0                    # STREAMINFO first
    assert len(audio) > 1000

    tagged = musicpack._flac_write_lyrics(data, LRC, title="i want")
    assert musicpack._flac_read_lyrics(tagged) == LRC
    assert musicpack._flac_split(tagged)[1] == audio
    # Only the metadata grew.
    assert len(tagged) > len(data)
    assert len(tagged) - len(data) < 4096


@pytest.mark.skipif(not REAL_FLAC.is_file(), reason="sample FLAC not present")
def test_a_real_flac_packs_and_unpacks(tmp_path):
    pack = musicpack.write(
        tmp_path / "real", REAL_FLAC, LRC, title="i want", color="#ffd166"
    )
    loaded = musicpack.read(pack)
    assert loaded.audio_name == REAL_FLAC.name
    assert loaded.lyrics == LRC
    assert loaded.tagged is True
    assert musicpack.read_embedded_lyrics(loaded.audio, loaded.audio_name) == LRC
    # The frames inside the pack match the original file's frames exactly.
    original_frames = musicpack._flac_split(REAL_FLAC.read_bytes())[1]
    assert musicpack._flac_split(loaded.audio)[1] == original_frames


# --------------------------------------------------------------------------
# dropping a pack into a plot
# --------------------------------------------------------------------------

@pytest.mark.skipif(not REAL_FLAC.is_file(), reason="sample FLAC not present")
def test_a_pack_can_be_inserted_as_a_track(tmp_path):
    from PlotManager import model as plot_model

    pack = musicpack.write(
        tmp_path / "track", REAL_FLAC, LRC, title="i want", color="#ffd166"
    )
    package = tmp_path / "demo.tscpkg"
    plot_model.create_package(package, name="包测试", description="")

    keys = plot_model.add_tracks(
        package, [plot_model.MusicDraft(abbreviation="iw", source=pack)]
    )
    assert keys == ["iw"]

    entry = plot_model.track(package, "iw")
    assert entry.kind == "lyrics"
    assert entry.color == "#ffd166"
    assert archive.read_text(package, "Musics/" + entry.lyrics) == LRC
    assert entry.filename == REAL_FLAC.name


def test_a_pack_without_lyrics_is_still_a_track(tmp_path):
    from PlotManager import model as plot_model

    source = write_audio(tmp_path, "plain.wav", b"RIFF" + bytes(64))
    pack = musicpack.write(tmp_path / "track", source, LRC, kind="instrumental")
    package = tmp_path / "demo.tscpkg"
    plot_model.create_package(package, name="包测试", description="")

    plot_model.add_tracks(package, [plot_model.MusicDraft(abbreviation="p", source=pack)])
    entry = plot_model.track(package, "p")
    assert entry.kind == "instrumental"


def test_resolve_music_source_leaves_ordinary_files_alone(tmp_path):
    from PlotManager import model as plot_model

    source = write_audio(tmp_path, "plain.flac", synthetic_flac())
    draft = plot_model.MusicDraft(abbreviation="x", source=source, kind="instrumental")
    assert plot_model.resolve_music_source(draft) is draft


# --------------------------------------------------------------------------
# byte-order marks
# --------------------------------------------------------------------------

def test_a_bom_in_the_lyrics_is_dropped(tmp_path):
    """Windows editors add one, and it would become an invisible first character."""

    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, "\ufeff" + LRC)
    assert musicpack.read(pack).lyrics == LRC


def test_a_bom_stored_in_a_pack_is_dropped_on_read(tmp_path):
    """Older packs, or ones written by another tool, may still carry one."""

    from tscp_player.lyrics import parse_lrc

    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, LRC)
    # Force a BOM into the stored lyrics, as a foreign tool might have.
    with zipfile.ZipFile(pack) as archive_file:
        members = {name: archive_file.read(name) for name in archive_file.namelist()}
    members[musicpack.LYRICS_MEMBER] = ("\ufeff" + LRC).encode("utf-8")
    with zipfile.ZipFile(pack, "w") as archive_file:
        for name, data in members.items():
            archive_file.writestr(name, data)

    loaded = musicpack.read(pack)
    assert loaded.lyrics.startswith("\ufeff")     # stored verbatim...
    assert [line.text for line in parse_lrc(loaded.lyrics).lines][0] == "末班车驶过"


# --------------------------------------------------------------------------
# the command line
# --------------------------------------------------------------------------

def test_cli_creates_a_pack(tmp_path, capsys):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    lrc_file = tmp_path / "song.lrc"
    lrc_file.write_text(LRC, encoding="utf-8")
    target = tmp_path / "out.tscpmc"

    code = musicpack.main(
        [str(source), "--lyrics", str(lrc_file), "--title", "歌", "-o", str(target)]
    )
    assert code == 0
    assert target.is_file()
    pack = musicpack.read(target)
    assert pack.title == "歌"
    assert pack.lyrics == LRC
    assert "音频标签" in capsys.readouterr().out


def test_cli_picks_up_lyrics_already_in_the_audio(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    tagged = musicpack.embed_lyrics(source.read_bytes(), source.name, LRC)[0]
    source.write_bytes(tagged)

    target = tmp_path / "out.tscpmc"
    assert musicpack.main([str(source), "-o", str(target)]) == 0
    assert musicpack.read(target).lyrics == LRC


def test_cli_refuses_to_pack_nothing(tmp_path, capsys):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    code = musicpack.main([str(source), "-o", str(tmp_path / "out.tscpmc")])
    assert code == 2
    assert "没有歌词" in capsys.readouterr().err


def test_cli_can_pack_instrumental(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    target = tmp_path / "out.tscpmc"
    assert musicpack.main([str(source), "--instrumental", "-o", str(target)]) == 0
    assert musicpack.read(target).kind == "instrumental"


def test_cli_shows_a_pack(tmp_path, capsys):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, LRC, title="歌", color="#ffd166")
    assert musicpack.main([str(pack), "--show"]) == 0
    output = capsys.readouterr().out
    assert "歌" in output
    assert "#ffd166" in output
    assert "有歌词" in output
    assert "末班车驶过" in output


def test_cli_extracts_a_pack(tmp_path):
    source = write_audio(tmp_path, "track.flac", synthetic_flac())
    pack = musicpack.write(tmp_path / "out", source, LRC, title="歌")
    folder = tmp_path / "unpacked"
    assert musicpack.main([str(pack), "--extract", "-o", str(folder)]) == 0
    assert (folder / "track.flac").is_file()
    assert (folder / musicpack.LYRICS_MEMBER).read_text(encoding="utf-8") == LRC
    # The extracted audio still carries its own lyrics.
    assert musicpack.read_embedded_lyrics(
        (folder / "track.flac").read_bytes(), "track.flac"
    ) == LRC


def test_cli_reports_a_missing_file(tmp_path, capsys):
    assert musicpack.main([str(tmp_path / "nope.flac")]) == 1
    assert "错误" in capsys.readouterr().err
