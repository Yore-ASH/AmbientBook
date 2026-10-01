"""``.tscpmc`` -- an audio file with its lyrics packed alongside.

Two things go on at once, on purpose:

* the lyrics are **written into the audio's own tags** (FLAC ``LYRICS`` Vorbis
  comment, MP3 ``USLT`` ID3 frame), so the audio is genuinely self-describing
  and any ordinary player will show them;
* the pair is then wrapped in one container together with a plain ``.lrc`` and a
  little manifest, so formats that cannot carry tags still work, the exact
  timing text survives untouched, and TSCP can keep its own extras (colour).

Nothing here depends on a tag library: FLAC metadata blocks and ID3v2 frames are
simple enough to read and write directly, which keeps the project's
zero-runtime-dependency promise intact.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

PathLike = Union[str, Path]

#: Extension for the packed file.
PACK_SUFFIX = ".tscpmc"
PACK_FORMAT = "tscpmc 1"
MANIFEST = "__init__.json"
AUDIO_DIR = "Audio"
LYRICS_MEMBER = "lyrics.lrc"
#: Written only when lines carry a font or a colour, which LRC cannot express.
LYRIC_DOCUMENT_MEMBER = "lyrics.json"

#: Where the lyrics are kept inside the audio itself.
FLAC_COMMENT_BLOCK = 4
VORBIS_LYRICS_KEYS = (b"LYRICS", b"UNSYNCEDLYRICS", b"UNSYNCED LYRICS")
ID3_LYRICS_FRAME = b"USLT"

#: Formats whose tags we can write.  Ogg/Opus would mean re-paginating the
#: container, which is not something to do casually, and WAV has no standard
#: lyrics field at all -- those ride in the container only.
TAGGABLE_SUFFIXES = frozenset({".flac", ".mp3"})


class MusicPackError(ValueError):
    """Raised when an audio file cannot be tagged or a pack cannot be read."""


# --------------------------------------------------------------------------
# FLAC: metadata blocks with a Vorbis comment
# --------------------------------------------------------------------------

def _flac_split(data: bytes) -> Tuple[List[Tuple[int, bytes]], bytes]:
    """Split a FLAC stream into ``[(block_type, payload)]`` and its audio frames."""

    if data[:4] != b"fLaC":
        raise MusicPackError("不是 FLAC 文件（缺少 fLaC 标记）")
    offset = 4
    blocks: List[Tuple[int, bytes]] = []
    while True:
        if offset + 4 > len(data):
            raise MusicPackError("FLAC 元数据块被截断")
        header = data[offset]
        is_last = bool(header & 0x80)
        block_type = header & 0x7F
        length = int.from_bytes(data[offset + 1:offset + 4], "big")
        payload = data[offset + 4:offset + 4 + length]
        if len(payload) != length:
            raise MusicPackError("FLAC 元数据块长度与内容不符")
        blocks.append((block_type, payload))
        offset += 4 + length
        if is_last:
            break
    return blocks, data[offset:]


def _flac_join(blocks: List[Tuple[int, bytes]], audio: bytes) -> bytes:
    out = bytearray(b"fLaC")
    last = len(blocks) - 1
    for index, (block_type, payload) in enumerate(blocks):
        out.append((0x80 if index == last else 0x00) | block_type)
        out += len(payload).to_bytes(3, "big")
        out += payload
    out += audio
    return bytes(out)


def _vorbis_split(payload: bytes) -> Tuple[bytes, List[bytes]]:
    """``(vendor, [b"KEY=value", ...])``; every length is little-endian."""

    try:
        position = 0
        vendor_length = int.from_bytes(payload[position:position + 4], "little")
        position += 4
        vendor = payload[position:position + vendor_length]
        position += vendor_length
        count = int.from_bytes(payload[position:position + 4], "little")
        position += 4
        comments: List[bytes] = []
        for _ in range(count):
            length = int.from_bytes(payload[position:position + 4], "little")
            position += 4
            comments.append(payload[position:position + length])
            position += length
    except (IndexError, ValueError) as exc:
        raise MusicPackError("Vorbis 注释块格式不对") from exc
    return vendor, comments


def _vorbis_join(vendor: bytes, comments: List[bytes]) -> bytes:
    out = bytearray()
    out += len(vendor).to_bytes(4, "little") + vendor
    out += len(comments).to_bytes(4, "little")
    for comment in comments:
        out += len(comment).to_bytes(4, "little") + comment
    return bytes(out)


def _is_lyrics_comment(comment: bytes) -> bool:
    key = comment.split(b"=", 1)[0].strip().upper()
    return key in VORBIS_LYRICS_KEYS


def _flac_write_lyrics(data: bytes, lrc: str, title: str = "") -> bytes:
    blocks, audio = _flac_split(data)
    entry = b"LYRICS=" + lrc.encode("utf-8")
    title_entry = b"TITLE=" + title.encode("utf-8") if title else None

    rebuilt: List[Tuple[int, bytes]] = []
    comment_index: Optional[int] = None
    for block_type, payload in blocks:
        if block_type != FLAC_COMMENT_BLOCK:
            rebuilt.append((block_type, payload))
            continue
        vendor, comments = _vorbis_split(payload)
        kept = [item for item in comments if not _is_lyrics_comment(item)]
        if title_entry is not None:
            kept = [item for item in kept if not item.upper().startswith(b"TITLE=")]
            kept.append(title_entry)
        kept.append(entry)
        comment_index = len(rebuilt)
        rebuilt.append((block_type, _vorbis_join(vendor, kept)))

    if comment_index is None:
        # No comment block yet: put ours right after STREAMINFO, which is where
        # every other FLAC writer puts it.
        comments = [title_entry] if title_entry is not None else []
        comments.append(entry)
        insert_at = 1 if rebuilt and rebuilt[0][0] == 0 else 0
        rebuilt.insert(insert_at, (FLAC_COMMENT_BLOCK, _vorbis_join(b"TSCP", comments)))

    return _flac_join(rebuilt, audio)


def _flac_read_lyrics(data: bytes) -> Optional[str]:
    blocks, _audio = _flac_split(data)
    for block_type, payload in blocks:
        if block_type != FLAC_COMMENT_BLOCK:
            continue
        _vendor, comments = _vorbis_split(payload)
        for comment in comments:
            if _is_lyrics_comment(comment):
                _key, _sep, value = comment.partition(b"=")
                return value.decode("utf-8", "replace")
    return None


# --------------------------------------------------------------------------
# MP3: an ID3v2 tag with a USLT (unsynchronised lyrics) frame
# --------------------------------------------------------------------------

def _synchsafe(value: int) -> bytes:
    return bytes(
        (
            (value >> 21) & 0x7F,
            (value >> 14) & 0x7F,
            (value >> 7) & 0x7F,
            value & 0x7F,
        )
    )


def _unsynchsafe(raw: bytes) -> int:
    return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]


def _id3_split(data: bytes) -> Tuple[Optional[bytes], bytes]:
    """``(tag_bytes_or_None, audio_bytes)``."""

    if data[:3] != b"ID3" or len(data) < 10:
        return None, data
    size = _unsynchsafe(data[6:10])
    total = 10 + size
    if data[5] & 0x10:              # a v2.4 footer follows the tag
        total += 10
    if total > len(data):
        raise MusicPackError("ID3 标签长度超过了文件本身")
    return data[:total], data[total:]


def _id3_frames(tag: bytes) -> List[Tuple[bytes, int, int]]:
    """``[(frame_id, data_offset, size)]`` for a v2.3/v2.4 tag."""

    major = tag[3]
    offset = 10
    found: List[Tuple[bytes, int, int]] = []
    while offset + 10 <= len(tag):
        frame_id = tag[offset:offset + 4]
        if not frame_id.strip(b"\x00"):
            break
        raw = tag[offset + 4:offset + 8]
        size = _unsynchsafe(raw) if major >= 4 else int.from_bytes(raw, "big")
        if size <= 0 or offset + 10 + size > len(tag):
            break
        found.append((frame_id, offset + 10, size))
        offset += 10 + size
    return found


def _decode_text(encoding: int, raw: bytes) -> str:
    try:
        if encoding == 0:
            return raw.decode("latin-1")
        if encoding == 1:
            return raw.decode("utf-16")
        if encoding == 2:
            return raw.decode("utf-16-be")
        return raw.decode("utf-8")
    except (UnicodeDecodeError, LookupError):
        return raw.decode("utf-8", "replace")


def _uslt_payload(lrc: str) -> bytes:
    # 3 = UTF-8, "XXX" = language undefined, then an empty description.
    return b"\x03" + b"XXX" + b"\x00" + lrc.encode("utf-8")


def _uslt_text(payload: bytes) -> str:
    if len(payload) < 4:
        return ""
    encoding = payload[0]
    body = payload[4:]                      # skip encoding byte + language
    if encoding in (1, 2):
        end = -1
        for index in range(0, len(body) - 1, 2):
            if body[index:index + 2] == b"\x00\x00":
                end = index
                break
        body = body[end + 2:] if end >= 0 else body
    else:
        end = body.find(b"\x00")
        body = body[end + 1:] if end >= 0 else body
    return _decode_text(encoding, body)


def _mp3_write_lyrics(data: bytes, lrc: str, title: str = "") -> bytes:
    tag, audio = _id3_split(data)

    frames: List[Tuple[bytes, bytes]] = []
    if tag is not None:
        for frame_id, offset, size in _id3_frames(tag):
            if frame_id == ID3_LYRICS_FRAME:
                continue                    # replaced below
            if frame_id == b"TIT2" and title:
                continue                    # rewritten below
            frames.append((frame_id, tag[offset:offset + size]))
    if title:
        frames.append((b"TIT2", b"\x03" + title.encode("utf-8")))
    frames.append((ID3_LYRICS_FRAME, _uslt_payload(lrc)))

    body = bytearray()
    for frame_id, payload in frames:
        # v2.4 frame sizes are synchsafe, and UTF-8 needs v2.4 to be legal.
        body += frame_id + _synchsafe(len(payload)) + b"\x00\x00" + payload
    header = b"ID3\x04\x00\x00" + _synchsafe(len(body))
    return bytes(header) + bytes(body) + audio


def _mp3_read_lyrics(data: bytes) -> Optional[str]:
    tag, _audio = _id3_split(data)
    if tag is None:
        return None
    for frame_id, offset, size in _id3_frames(tag):
        if frame_id == ID3_LYRICS_FRAME:
            return _uslt_text(tag[offset:offset + size])
    return None


# --------------------------------------------------------------------------
# tagging, format-agnostic
# --------------------------------------------------------------------------

def supports_tagging(name: PathLike) -> bool:
    """Whether the lyrics can be written into this file's own metadata."""

    return Path(name).suffix.lower() in TAGGABLE_SUFFIXES


def embed_lyrics(
    data: bytes, name: PathLike, lrc: str, *, title: str = ""
) -> Tuple[bytes, bool]:
    """Write *lrc* into the audio's tags.

    Returns ``(new_bytes, tagged)``.  An unsupported format comes back untouched
    with ``tagged=False`` rather than raising -- the container still carries the
    lyrics, so nothing is lost.
    """

    suffix = Path(name).suffix.lower()
    if suffix == ".flac":
        return _flac_write_lyrics(data, lrc, title), True
    if suffix == ".mp3":
        return _mp3_write_lyrics(data, lrc, title), True
    return data, False


def read_embedded_lyrics(data: bytes, name: PathLike) -> Optional[str]:
    """The lyrics already inside the audio, if any."""

    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".flac":
            return _flac_read_lyrics(data)
        if suffix == ".mp3":
            return _mp3_read_lyrics(data)
    except MusicPackError:
        return None
    return None


# --------------------------------------------------------------------------
# the container
# --------------------------------------------------------------------------

@dataclass
class MusicPack:
    """One ``.tscpmc``, read back into memory."""

    path: Optional[Path] = None
    audio_name: str = ""
    audio: bytes = b""
    lyrics: str = ""
    title: str = ""
    color: str = ""
    kind: str = "lyrics"
    tagged: bool = False
    notes: List[str] = field(default_factory=list)
    #: The richer lyric document, when the pack carries one.
    document: str = ""

    @property
    def audio_suffix(self) -> str:
        return Path(self.audio_name).suffix.lower()

    def lines(self):
        """The lyrics as timed lines, in whichever form the pack stored."""

        from .lyrics import parse_lyrics

        if self.document:
            return parse_lyrics(self.document)
        return parse_lyrics(self.lyrics)


def _manifest(pack: MusicPack) -> Dict[str, object]:
    document = {
        "FORMAT": PACK_FORMAT,
        "TITLE": pack.title,
        "KIND": pack.kind,
        "COLOR": pack.color,
        "AUDIO": "%s/%s" % (AUDIO_DIR, pack.audio_name),
        "LYRICS": LYRICS_MEMBER,
        "EMBEDDED": pack.tagged,
    }
    if pack.document:
        document["LYRICS_JSON"] = LYRIC_DOCUMENT_MEMBER
    return document


def write(
    target: PathLike,
    audio: PathLike,
    lyrics: str,
    *,
    title: str = "",
    color: str = "",
    kind: str = "lyrics",
    lines=None,
) -> Path:
    """Pack *audio* and *lyrics* into one ``.tscpmc``.

    Pass *lines* (a :class:`~tscp_player.lyrics.Lyrics`) when any line carries
    its own font or colour: those cannot be written into an ``.lrc``, so the
    richer JSON document rides along beside it.
    """

    source = Path(audio)
    if not source.is_file():
        raise MusicPackError("找不到音频文件：%s" % source)
    lyrics = lyrics[1:] if lyrics.startswith("\ufeff") else lyrics
    # An instrumental pack legitimately has nothing to sing along to.
    if kind != "instrumental" and not str(lyrics).strip():
        raise MusicPackError("歌词不能为空")

    destination = Path(target)
    if destination.suffix.lower() != PACK_SUFFIX:
        destination = destination.with_suffix(PACK_SUFFIX)
    # Pointing the output at the input is a mistake worth stopping: the suffix
    # fixup above would otherwise quietly write a sibling file instead.
    if Path(target).resolve() == source.resolve() or destination.resolve() == source.resolve():
        raise MusicPackError("输出文件不能覆盖原始音频")
    destination.parent.mkdir(parents=True, exist_ok=True)

    raw = source.read_bytes()
    label = title.strip() or source.stem
    try:
        tagged_bytes, tagged = embed_lyrics(raw, source.name, lyrics, title=label)
    except MusicPackError:
        tagged_bytes, tagged = raw, False

    # Only pay for the JSON document when a line actually needs it.
    document = ""
    if lines is not None:
        from .lyrics import has_line_styles, serialize_lyric_document

        if has_line_styles(lines):
            document = serialize_lyric_document(lines)

    pack = MusicPack(
        path=destination,
        audio_name=source.name,
        audio=tagged_bytes,
        lyrics=lyrics,
        title=label,
        color=color,
        kind=kind,
        tagged=tagged,
        document=document,
    )
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr(
            MANIFEST, json.dumps(_manifest(pack), ensure_ascii=False, indent=2) + "\n"
        )
        if pack.lyrics:
            archive.writestr(LYRICS_MEMBER, pack.lyrics)
        if document:
            archive.writestr(LYRIC_DOCUMENT_MEMBER, document)
        # Audio is already compressed; storing it keeps the pack streamable and
        # makes the bytes inside identical to the original.
        archive.writestr(
            "%s/%s" % (AUDIO_DIR, source.name),
            tagged_bytes,
            compress_type=zipfile.ZIP_STORED,
        )
    return destination


def is_pack(path: PathLike) -> bool:
    candidate = Path(path)
    return candidate.is_file() and candidate.suffix.lower() == PACK_SUFFIX


def read(path: PathLike) -> MusicPack:
    """Load a ``.tscpmc`` into memory."""

    source = Path(path)
    if not source.is_file():
        raise MusicPackError("找不到文件：%s" % source)
    try:
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            if MANIFEST not in names:
                raise MusicPackError("不是 .tscpmc 文件（缺少 %s）" % MANIFEST)
            document = json.loads(archive.read(MANIFEST).decode("utf-8"))
            audio_member = str(document.get("AUDIO", ""))
            if not audio_member or audio_member not in names:
                raise MusicPackError("包里的音频成员不对：%r" % audio_member)
            lyrics_member = str(document.get("LYRICS", LYRICS_MEMBER))
            lyrics = ""
            if lyrics_member in names:
                lyrics = archive.read(lyrics_member).decode("utf-8")
            document_member = str(document.get("LYRICS_JSON", "") or "")
            rich = ""
            if document_member and document_member in names:
                rich = archive.read(document_member).decode("utf-8")
            audio = archive.read(audio_member)
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeError) as exc:
        raise MusicPackError("无法读取 %s：%s" % (source, exc)) from exc

    pack = MusicPack(
        path=source,
        audio_name=Path(audio_member).name,
        audio=audio,
        lyrics=lyrics,
        title=str(document.get("TITLE", "")),
        color=str(document.get("COLOR", "")),
        kind=str(document.get("KIND", "lyrics")),
        tagged=bool(document.get("EMBEDDED", False)),
        document=rich,
    )

    # A pack whose audio claims to carry tags but does not is worth saying out
    # loud rather than silently trusting the manifest.
    if pack.tagged:
        embedded = read_embedded_lyrics(pack.audio, pack.audio_name)
        if embedded is None:
            pack.tagged = False
            pack.notes.append("音频里其实没有找到歌词标签")
    return pack


def extract_audio(path: PathLike, folder: PathLike) -> Path:
    """Write the pack's audio out as a real file and return its path."""

    pack = read(path)
    destination = Path(folder) / pack.audio_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(pack.audio)
    return destination


def default_cache_root() -> Path:
    """Where extracted audio is parked, matching the plot container's cache."""

    from . import archive

    return archive.default_cache_root() / "tscpmc"


def extract_cached(path: PathLike, cache_root: Optional[PathLike] = None) -> Path:
    """Extract the pack's audio once, keyed by size and mtime.

    Every tool that wants to play or embed a ``.tscpmc`` needs a real file on
    disk; doing it through a cache means dropping the same pack into several
    plots only unpacks it once.
    """

    source = Path(path)
    if not source.is_file():
        raise MusicPackError("找不到文件：%s" % source)

    stat = source.stat()
    fingerprint = "%s-%d-%d" % (source.stem, stat.st_size, int(stat.st_mtime))
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
    root = Path(cache_root) if cache_root is not None else default_cache_root()
    folder = root / digest
    folder.mkdir(parents=True, exist_ok=True)

    pack = read(source)
    destination = folder / pack.audio_name
    if not destination.is_file() or destination.stat().st_size != len(pack.audio):
        destination.write_bytes(pack.audio)
    return destination


def copy_into(pack_path: PathLike, folder: PathLike) -> Path:
    """Copy the whole pack, uncompressed, to *folder*."""

    source = Path(pack_path)
    destination = Path(folder) / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return destination


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------

def rewrite_lyrics(
    path: PathLike,
    *,
    lyrics: Optional[str] = None,
    lines=None,
    color: Optional[str] = None,
    title: Optional[str] = None,
    target: Optional[PathLike] = None,
) -> Path:
    """Rewrite one pack's lyrics (and optionally its colour), keeping its audio.

    Reads *path* and writes *target* (default: the same file).  The audio member
    is copied byte for byte and only re-tagged, so a rewrite never degrades the
    recording.  The new pack is built beside the destination and swapped in with
    an atomic replace, so a failure leaves the original intact.
    """

    import os

    from .lyrics import has_line_styles, parse_lyrics, serialize_lrc, serialize_lyric_document

    source = Path(path)
    pack = read(source)
    if lines is None and lyrics is not None:
        lines = parse_lyrics(lyrics)

    if lyrics is None and lines is not None:
        lyrics = serialize_lrc(lines)
    text = pack.lyrics if lyrics is None else lyrics
    if not text.strip() and pack.kind != "instrumental":
        raise MusicPackError("歌词不能为空")

    document = ""
    if lines is not None:
        document = serialize_lyric_document(lines) if has_line_styles(lines) else ""
    else:
        document = pack.document

    label = pack.title if title is None else title
    tagged, tagged_ok = embed_lyrics(pack.audio, pack.audio_name, text, title=label)

    manifest = {
        "FORMAT": PACK_FORMAT,
        "TITLE": label,
        "KIND": pack.kind,
        "COLOR": pack.color if color is None else color,
        "AUDIO": "%s/%s" % (AUDIO_DIR, pack.audio_name),
        "LYRICS": LYRICS_MEMBER,
        "EMBEDDED": tagged_ok,
    }
    if document:
        manifest["LYRICS_JSON"] = LYRIC_DOCUMENT_MEMBER

    destination = source if target is None else Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)
    helper = destination.with_name(destination.name + ".rewriting")
    try:
        with zipfile.ZipFile(helper, "w") as archive:
            archive.writestr(
                MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            )
            if text:
                archive.writestr(LYRICS_MEMBER, text)
            if document:
                archive.writestr(LYRIC_DOCUMENT_MEMBER, document)
            archive.writestr(
                "%s/%s" % (AUDIO_DIR, pack.audio_name),
                tagged,
                compress_type=zipfile.ZIP_STORED,
            )
        os.replace(helper, destination)
    except BaseException:
        helper.unlink(missing_ok=True)
        raise
    return destination


def main(argv: Optional[List[str]] = None) -> int:
    """``python -m tscp_player.musicpack`` -- make, inspect or unpack a pack."""

    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="python -m tscp_player.musicpack",
        description="把歌词嵌入音频，合成一个 .tscpmc；或者反过来取出来",
    )
    parser.add_argument("path", help="音频文件，或要读取/解开的 .tscpmc")
    parser.add_argument("-o", "--output", help="输出文件或目录")
    parser.add_argument("--lyrics", help="歌词 .lrc；省略则用音频里已有的标签")
    parser.add_argument("--title", default="", help="曲名（写进音频标签）")
    parser.add_argument("--color", default="", help="歌词颜色 #rrggbb")
    parser.add_argument("--instrumental", action="store_true", help="标为纯音乐")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--show", action="store_true", help="只显示包里的信息")
    action.add_argument("--extract", action="store_true", help="解出音频和歌词")
    action.add_argument(
        "--normalise",
        action="store_true",
        help="把包里的歌词重写成标准 LRC（时间不变），音频原样保留",
    )
    args = parser.parse_args(argv)

    try:
        if args.normalise:
            from .lyrics import serialize_lrc

            source = Path(args.path)
            pack = read(source)
            lines = pack.lines()
            if not lines.lines:
                print("这个包里没有可解析的歌词。", file=sys.stderr)
                return 2
            target = Path(args.output) if args.output else source
            if target == source:
                # Keep the old text beside the file: it is tiny, and it is the
                # only copy of whatever the original mixed formats held.
                source.with_suffix(source.suffix + ".lrc.bak").write_text(
                    pack.lyrics, encoding="utf-8"
                )
            written = rewrite_lyrics(
                source, lines=lines, lyrics=serialize_lrc(lines), target=target
            )
            print(
                "已重写 %s：%d 行标准 LRC（时间不变，音频原样保留）"
                % (written, len(lines.lines))
            )
            return 0

        if args.show or args.extract:
            pack = read(args.path)
            if args.show:
                print("曲名    :", pack.title)
                print("音频    :", pack.audio_name, "(%.1f MB)" % (len(pack.audio) / 1048576))
                print("类型    :", pack.kind)
                print("颜色    :", pack.color or "（默认）")
                print("音频标签:", "有歌词" if pack.tagged else "没有")
                for note in pack.notes:
                    print("注意    :", note)
                print("歌词    :")
                for line in pack.lyrics.splitlines():
                    print("   ", line)
                return 0
            folder = Path(args.output) if args.output else Path(args.path).parent
            audio = extract_audio(args.path, folder)
            print("已取出音频:", audio)
            if pack.lyrics:
                lyrics = Path(folder) / LYRICS_MEMBER
                lyrics.write_text(pack.lyrics, encoding="utf-8")
                print("已取出歌词:", lyrics)
            return 0

        source = Path(args.path)
        if args.lyrics:
            lyrics = Path(args.lyrics).read_text(encoding="utf-8")
        else:
            # No .lrc given: pick up whatever the audio already carries.
            lyrics = read_embedded_lyrics(source.read_bytes(), source.name) or ""
        if not lyrics.strip() and not args.instrumental:
            print(
                "没有歌词。用 --lyrics 指定 .lrc，或者加 --instrumental 只打包音频。",
                file=sys.stderr,
            )
            return 2
        target = args.output or source.with_suffix(PACK_SUFFIX)
        written = write(
            target,
            source,
            lyrics or "",
            title=args.title,
            color=args.color,
            kind="instrumental" if args.instrumental else "lyrics",
        )
        tagged = "（歌词已写进音频标签）" if supports_tagging(source.name) else \
            "（这种格式没有歌词标签，歌词只存在包里）"
        print("已生成 %s  %.1f MB  %s" % (
            written, written.stat().st_size / 1048576, tagged
        ))
        return 0
    except (MusicPackError, OSError, UnicodeError) as exc:
        print("错误:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
