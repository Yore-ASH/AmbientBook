"""Dependency-free operations for building and editing ``.tscpkg`` plots.

Everything here works on a container file directly, so the GUI stays a thin
shell and the same helpers are usable from scripts and tests.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from tscp_player import archive
from tscp_player.format import (
    Dialogue,
    Script,
    parse_tscp,
    parse_tscps,
    serialize_tscp,
    visible_text_length,
)
from tscp_player.plot import PROGRAM_VERSION_TEXT, PlotPackageError, load_plot_package

PathLike = Union[str, Path]

MUSIC_META = archive.MUSICS_DIR + "/" + archive.MANIFEST
SCRIPT_META = archive.SCRIPTS_DIR + "/" + archive.MANIFEST


class PackError(ValueError):
    """Raised when a container operation cannot be completed."""


@dataclass(frozen=True)
class MusicEntry:
    abbreviation: str
    filename: str
    size: int = 0
    present: bool = True


@dataclass(frozen=True)
class ScriptEntry:
    filename: str
    lines: int = 0
    characters: int = 0
    keys: Tuple[str, ...] = ()


@dataclass
class PackageInfo:
    location: Path
    name: str = ""
    description: str = ""
    version: str = "0.0.1"
    characters: Dict[str, str] = field(default_factory=dict)
    music: List[MusicEntry] = field(default_factory=list)
    scripts: List[ScriptEntry] = field(default_factory=list)


# --------------------------------------------------------------------------
# inspecting
# --------------------------------------------------------------------------

def _document(path: PathLike, member: str, fallback: dict) -> dict:
    data = archive.read_json(path, member, fallback)
    if not isinstance(data, dict):
        raise PackError("%s must be a JSON object" % member)
    return data


def music_document(path: PathLike) -> dict:
    return _document(path, MUSIC_META, {"VERSION": PROGRAM_VERSION_TEXT, "CONFIG": {}})


def script_document(path: PathLike) -> dict:
    return _document(path, SCRIPT_META, {"NAME": "", "VERSION": "0.0.1", "CHARACTERS": {}})


def music_config(path: PathLike) -> Dict[str, str]:
    document = music_document(path)
    config = document.get("CONFIG", {})
    if not isinstance(config, dict):
        raise PackError("Musics CONFIG must be an object")
    return {str(key): str(value) for key, value in config.items()}


def _write_json(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def inspect(path: PathLike) -> PackageInfo:
    """Read everything the manager shows for one container."""

    target = Path(path)
    if not archive.is_package(target):
        raise PackError("不是 .tscpkg 文件：%s" % target)
    manifest = _document(target, archive.MANIFEST, {})
    scripts_meta = script_document(target)
    members = set(archive.members(target))

    music: List[MusicEntry] = []
    for abbreviation, filename in sorted(music_config(target).items()):
        member = "%s/%s" % (archive.MUSICS_DIR, filename)
        music.append(
            MusicEntry(
                abbreviation,
                filename,
                archive.member_size(target, member),
                member in members,
            )
        )

    scripts: List[ScriptEntry] = []
    for filename in archive.list_dir(target, archive.SCRIPTS_DIR, ".tscp"):
        try:
            script = parse_tscp(archive.read_text(target, "%s/%s" % (archive.SCRIPTS_DIR, filename)))
        except ValueError:
            scripts.append(ScriptEntry(filename))
            continue
        keys: List[str] = []
        for item in script.lines:
            if isinstance(item, Dialogue) and item.character and item.character not in keys:
                keys.append(item.character)
        scripts.append(
            ScriptEntry(
                filename,
                lines=len(script.lines),
                characters=sum(
                    visible_text_length(item.text)
                    for item in script.lines
                    if isinstance(item, Dialogue)
                ),
                keys=tuple(keys),
            )
        )

    characters = scripts_meta.get("CHARACTERS", {})
    if not isinstance(characters, dict):
        raise PackError("CHARACTERS must be an object")
    return PackageInfo(
        location=target,
        name=str(manifest.get("NAME") or target.stem),
        description=str(manifest.get("DESCRIPTION", "")),
        version=str(manifest.get("VERSION", "0.0.1")),
        characters={str(k): str(v.get("NAME", k)) for k, v in characters.items()},
        music=music,
        scripts=scripts,
    )


# --------------------------------------------------------------------------
# creating
# --------------------------------------------------------------------------

def create_package(
    path: PathLike,
    *,
    name: str,
    description: str = "",
    version: str = "0.0.1",
) -> Path:
    label = str(name).strip()
    if not label:
        raise PackError("剧情名称不能为空")
    try:
        return archive.create(
            path,
            name=label,
            version=str(version).strip() or "0.0.1",
            description=str(description).strip(),
            dependency_version=PROGRAM_VERSION_TEXT,
            music_version=PROGRAM_VERSION_TEXT,
        )
    except archive.PackageError as exc:
        raise PackError(str(exc)) from exc


def update_metadata(
    path: PathLike,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    manifest = _document(path, archive.MANIFEST, {})
    if name is not None:
        label = str(name).strip()
        if not label:
            raise PackError("剧情名称不能为空")
        manifest["NAME"] = label
    if description is not None:
        manifest["DESCRIPTION"] = str(description).strip()
    manifest["FORMAT"] = archive.FORMAT_TEXT
    archive.update(path, text={archive.MANIFEST: _write_json(manifest)})


def pack_directory(
    root: PathLike,
    target: PathLike,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> Path:
    """Turn a legacy ``Musics`` + ``Scripts`` folder into one ``.tscpkg``."""

    source = Path(root)
    if not source.is_dir():
        raise PackError("目录不存在：%s" % source)
    load_plot_package(source)  # validates layout and versions

    package = create_package(
        target,
        name=name or source.name,
        description=description or "",
    )

    texts: Dict[str, str] = {}
    for relative in (archive.MANIFEST, SCRIPT_META, MUSIC_META):
        candidate = source / relative
        if candidate.is_file():
            texts[relative] = candidate.read_text(encoding="utf-8")

    manifest = json.loads(texts.get(archive.MANIFEST) or "{}")
    manifest["FORMAT"] = archive.FORMAT_TEXT
    if name:
        manifest["NAME"] = name
    if description is not None:
        manifest["DESCRIPTION"] = description
    texts[archive.MANIFEST] = _write_json(manifest)

    scripts_dir = source / archive.SCRIPTS_DIR
    if scripts_dir.is_dir():
        for pattern in ("*.tscp", "*.tscps"):
            for path in sorted(scripts_dir.glob(pattern)):
                texts["%s/%s" % (archive.SCRIPTS_DIR, path.name)] = path.read_text(
                    encoding="utf-8"
                )

    additions: Dict[str, Path] = {}
    music_dir = source / archive.MUSICS_DIR
    if music_dir.is_dir():
        for path in sorted(music_dir.iterdir()):
            if path.is_file() and path.name != archive.MANIFEST:
                additions["%s/%s" % (archive.MUSICS_DIR, path.name)] = path

    archive.update(package, add=additions, text=texts)
    return package


def save_copy(source: PathLike, target: PathLike) -> Path:
    """Write a copy of an existing container to *target*.

    Editing a container already writes straight into it, so this is the explicit
    "导出为 .tscpkg" step: keep the working file where it is and hand a copy to
    someone else.  The copy is fully independent - adding music afterwards does
    not touch the original.
    """

    origin = Path(source)
    if not archive.is_package(origin):
        raise PackError("不是 .tscpkg 文件：%s" % origin)
    try:
        archive.members(origin)
    except archive.PackageError as exc:
        raise PackError("不是有效的 .tscpkg：%s" % exc) from exc

    destination = Path(target)
    if destination.suffix.lower() != archive.SUFFIX:
        destination = destination.with_suffix(archive.SUFFIX)
    if destination.resolve() == origin.resolve():
        raise PackError("目标就是当前打开的剧情包，不需要另存")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(origin, destination)
    except OSError as exc:
        raise PackError("无法写入 %s：%s" % (destination, exc)) from exc
    return destination


# --------------------------------------------------------------------------
# music
# --------------------------------------------------------------------------


def _check_abbreviation(value: str) -> str:
    key = str(value).strip()
    if not key:
        raise PackError("音乐简称不能为空")
    if any(character in key for character in "|\r\n"):
        raise PackError("音乐简称不能包含竖线或换行")
    return key


def suggest_abbreviation(filename: PathLike) -> str:
    """A short default the GUI offers when a file is inserted."""

    # A dotfile has no ``.stem``, so trim leading dots before splitting.
    stem = Path(filename).stem.strip().lstrip(".").strip()
    first = stem.split()[0] if stem else ""
    return first[:12] or "track"


def add_music(path: PathLike, entries: Sequence[Tuple[PathLike, str]]) -> List[str]:
    """Embed audio files and bind them to abbreviations in one rewrite.

    ``entries`` is a sequence of ``(source_file, abbreviation)`` pairs.  The
    file keeps its own name inside ``Musics/``; a second abbreviation pointing at
    the same file is allowed and shares the embedded copy.
    """

    if not entries:
        return sorted(music_config(path))
    document = music_document(path)
    config = music_config(path)
    additions: Dict[str, Path] = {}
    for source_value, abbreviation in entries:
        source = Path(source_value)
        if not source.is_file():
            raise PackError("音乐文件不存在：%s" % source)
        key = _check_abbreviation(abbreviation)
        config[key] = source.name
        additions["%s/%s" % (archive.MUSICS_DIR, source.name)] = source
    document["CONFIG"] = config
    archive.update(
        path,
        add=additions,
        text={MUSIC_META: _write_json(document)},
    )
    return sorted(config)


def remove_music(path: PathLike, abbreviations: Iterable[str]) -> List[str]:
    """Unbind abbreviations and drop audio no other abbreviation still uses."""

    document = music_document(path)
    config = music_config(path)
    members = set(archive.members(path))
    removals: List[str] = []
    for key in abbreviations:
        filename = config.pop(str(key), None)
        if filename is None:
            continue
        member = "%s/%s" % (archive.MUSICS_DIR, filename)
        if member in members and filename not in config.values():
            removals.append(member)
    document["CONFIG"] = config
    archive.update(path, remove=removals, text={MUSIC_META: _write_json(document)})
    return sorted(config)


def bind_music(path: PathLike, abbreviation: str, filename: str) -> List[str]:
    """Point an abbreviation at a file already embedded in ``Musics/``."""

    key = _check_abbreviation(abbreviation)
    name = Path(filename).name
    if not archive.has_member(path, "%s/%s" % (archive.MUSICS_DIR, name)):
        raise PackError("包内没有这个音乐文件：%s" % name)
    document = music_document(path)
    config = music_config(path)
    config[key] = name
    document["CONFIG"] = config
    archive.update(path, text={MUSIC_META: _write_json(document)})
    return sorted(config)


# --------------------------------------------------------------------------
# scripts
# --------------------------------------------------------------------------

def script_member_name(source: PathLike) -> str:
    return "%s/%s" % (archive.SCRIPTS_DIR, Path(source).with_suffix(".tscp").name)


def add_script(path: PathLike, source: PathLike, compile_source: bool = False) -> str:
    """Import one script, optionally compiling a ``.tscps`` source on the way in.

    A compiled ``.tscp`` is validated before it is embedded.  A ``.tscps`` source
    has no per-character timing yet, so it is compiled with zero delays and can
    be timed later in Ts2Tp.
    """

    candidate = Path(source)
    if not candidate.is_file():
        raise PackError("剧本文件不存在：%s" % candidate)
    suffix = candidate.suffix.lower()
    if compile_source or suffix == ".tscps":
        if suffix != ".tscps":
            raise PackError("原稿编译只接受 .tscps 文件")
        try:
            script = parse_tscps(candidate.read_text(encoding="utf-8"))
            text = serialize_tscp(script)
        except (OSError, UnicodeError, ValueError) as exc:
            raise PackError("无法编译原稿：%s" % exc) from exc
    elif suffix == ".tscp":
        text = candidate.read_text(encoding="utf-8")
        try:
            parse_tscp(text)
        except ValueError as exc:
            raise PackError("不是有效的 .tscp：%s" % exc) from exc
    else:
        raise PackError("只接受 .tscp 或 .tscps 文件")
    member = script_member_name(candidate)
    archive.update(path, text={member: text})
    return member


def remove_script(path: PathLike, member: str) -> None:
    name = Path(member).name
    if Path(name).suffix.lower() != ".tscp":
        raise PackError("只能删除 .tscp 剧本")
    archive.update(path, remove=["%s/%s" % (archive.SCRIPTS_DIR, name)])


def script_member(path: PathLike, member: str) -> str:
    """Normalise *member* to its in-container path, rejecting anything else."""

    name = Path(member).name
    if Path(name).suffix.lower() != ".tscp":
        raise PackError("只能处理 .tscp 剧本")
    target = "%s/%s" % (archive.SCRIPTS_DIR, name)
    if not archive.has_member(path, target):
        raise PackError("包内没有这个剧本：%s" % name)
    return target


def script_text(path: PathLike, member: str) -> str:
    """Raw compiled text of one script inside the container."""

    return archive.read_text(path, script_member(path, member))


def read_script(path: PathLike, member: str) -> Script:
    """The parsed events of one script inside the container."""

    return parse_tscp(script_text(path, member))


def replace_script(path: PathLike, member: str, text: str) -> str:
    """Validate *text* as a compiled script and write it straight back."""

    target = script_member(path, member)
    try:
        parse_tscp(text)
    except ValueError as exc:
        raise PackError("不是有效的 .tscp：%s" % exc) from exc
    archive.update(path, text={target: text})
    return target


def export_script(path: PathLike, member: str, target: PathLike) -> Path:
    """Copy one script out of the container onto disk for external editing."""

    text = script_text(path, member)
    destination = Path(target)
    if destination.is_dir():
        destination = destination / Path(member).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination
