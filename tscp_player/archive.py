"""Single-file ``.tscpkg`` plot containers.

A ``.tscpkg`` is an ordinary ZIP archive, so any zip tool can open it and a new
music file can be dropped straight in.  Text members are deflated; audio members
are *stored* because FLAC/OGG/MP3 are already compressed and deflating them only
costs CPU time for no size gain.

Layout::

    __init__.json          manifest  (FORMAT / NAME / VERSION / DESCRIPTION)
    Scripts/__init__.json  CHARACTERS and DEPENDECE
    Scripts/*.tscp         compiled scripts
    Musics/__init__.json   VERSION and the abbreviation -> filename CONFIG
    Musics/*               the audio files themselves

Rewriting a container copies every other member through, so changes are applied
in a single pass: read the current member, then call :func:`update` once with the
new file *and* the updated JSON together.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Union

PathLike = Union[str, Path]

SUFFIX = ".tscpkg"
MANIFEST = "__init__.json"
SCRIPTS_DIR = "Scripts"
MUSICS_DIR = "Musics"
FORMAT_NAME = "tscpkg"
FORMAT_VERSION = 1
FORMAT_TEXT = "%s %d" % (FORMAT_NAME, FORMAT_VERSION)

AUDIO_SUFFIXES = frozenset(
    {".flac", ".mp3", ".ogg", ".oga", ".opus", ".wav", ".m4a", ".aac", ".wma"}
)

_CHUNK = 1024 * 1024


class PackageError(ValueError):
    """Raised when a ``.tscpkg`` container is missing or malformed."""


def is_audio(name: PathLike) -> bool:
    """Whether *name* looks like an audio file that should be stored verbatim."""

    return Path(name).suffix.lower() in AUDIO_SUFFIXES


def is_package(path: PathLike) -> bool:
    """Whether *path* is an existing ``.tscpkg`` file."""

    candidate = Path(path)
    return candidate.is_file() and candidate.suffix.lower() == SUFFIX


def _compression_for(member: str) -> int:
    return zipfile.ZIP_STORED if is_audio(member) else zipfile.ZIP_DEFLATED


def _normalise(member: str) -> str:
    return member.replace("\\", "/").strip("/")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------

def members(path: PathLike) -> List[str]:
    """Every file member of the container, in archive order."""

    try:
        with zipfile.ZipFile(path) as archive:
            return [info.filename for info in archive.infolist() if not info.is_dir()]
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError("not a readable .tscpkg container: %s" % path) from exc


def has_member(path: PathLike, member: str) -> bool:
    name = _normalise(member)
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.getinfo(name) is not None
    except (KeyError, OSError, zipfile.BadZipFile):
        return False


def member_size(path: PathLike, member: str) -> int:
    """Uncompressed size of *member*, or ``0`` when it is absent."""

    name = _normalise(member)
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.getinfo(name).file_size
    except (KeyError, OSError, zipfile.BadZipFile):
        return 0


def list_dir(path: PathLike, directory: str, suffix: str = "") -> List[str]:
    """Names directly inside *directory*, optionally filtered by *suffix*."""

    prefix = _normalise(directory) + "/"
    wanted = suffix.lower()
    result = []
    for name in members(path):
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        if not rest or "/" in rest:
            continue
        if wanted and not rest.lower().endswith(wanted):
            continue
        result.append(rest)
    return sorted(result)


def read_text(path: PathLike, member: str, encoding: str = "utf-8") -> str:
    name = _normalise(member)
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.read(name).decode(encoding)
    except (KeyError, OSError, UnicodeError, zipfile.BadZipFile) as exc:
        raise PackageError("cannot read %s from %s" % (name, path)) from exc


def read_json(path: PathLike, member: str, default=None):
    if not has_member(path, member):
        if default is None:
            raise PackageError("missing %s in %s" % (_normalise(member), path))
        return json.loads(json.dumps(default))
    try:
        return json.loads(read_text(path, member))
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise PackageError("invalid JSON in %s of %s" % (_normalise(member), path)) from exc


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def _blank(path: PathLike) -> Path:
    target = Path(path)
    if target.suffix.lower() != SUFFIX:
        target = target.with_suffix(SUFFIX)
    return target


def create(
    path: PathLike,
    *,
    name: str,
    version: str = "0.0.1",
    description: str = "",
    dependency_version: str = "0.0.1",
    music_version: str = "0.0.1",
    characters: Optional[Mapping[str, object]] = None,
) -> Path:
    """Create an empty, valid ``.tscpkg`` and return its path."""

    target = _blank(path)
    if target.exists():
        raise PackageError("refusing to overwrite an existing container: %s" % target)
    manifest = {
        "FORMAT": FORMAT_TEXT,
        "NAME": str(name),
        "VERSION": str(version),
    }
    if description:
        manifest["DESCRIPTION"] = str(description)
    scripts = {
        "NAME": str(name),
        "VERSION": str(version),
        "CHARACTERS": dict(characters or {}),
        "DEPENDECE": {
            "MAIN": {"VERSION": str(dependency_version)},
            "MUSIC_PACK": {"VERSION": str(music_version)},
        },
    }
    music = {"VERSION": str(music_version), "CONFIG": {}}
    with zipfile.ZipFile(target, "w") as archive:
        for member, data in (
            (MANIFEST, manifest),
            (SCRIPTS_DIR + "/" + MANIFEST, scripts),
            (MUSICS_DIR + "/" + MANIFEST, music),
        ):
            archive.writestr(
                member,
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return target


def update(
    path: PathLike,
    *,
    add: Optional[Mapping[str, PathLike]] = None,
    text: Optional[Mapping[str, str]] = None,
    remove: Iterable[str] = (),
) -> None:
    """Apply every change in one rewrite so large audio is copied only once.

    ``add`` embeds files, ``text`` writes UTF-8 strings, and ``remove`` deletes
    members.  Anything not mentioned is copied through unchanged, streamed a
    megabyte at a time so a big FLAC never has to fit in memory.
    """

    target = Path(path)
    pending: Dict[str, Optional[object]] = {}
    for member in remove:
        pending[_normalise(member)] = None
    for member, value in (text or {}).items():
        pending[_normalise(member)] = str(value)
    for member, value in (add or {}).items():
        source = Path(value)
        if not source.is_file():
            raise PackageError("cannot embed a missing file: %s" % source)
        pending[_normalise(member)] = source

    if not pending:
        return

    handle, temporary_name = tempfile.mkstemp(suffix=SUFFIX, dir=str(target.parent or Path(".")))
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(target) as source, zipfile.ZipFile(temporary, "w") as sink:
            for info in source.infolist():
                if info.is_dir() or info.filename in pending:
                    continue
                with source.open(info) as reader, sink.open(info, "w") as writer:
                    shutil.copyfileobj(reader, writer, _CHUNK)
            for member, value in pending.items():
                if value is None:
                    continue
                if isinstance(value, Path):
                    sink.write(value, member, compress_type=_compression_for(member))
                else:
                    sink.writestr(
                        member,
                        value.encode("utf-8"),
                        compress_type=_compression_for(member),
                    )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


# --------------------------------------------------------------------------
# extracting audio for playback
# --------------------------------------------------------------------------

def default_cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
    root = Path(base) if base else Path(tempfile.gettempdir())
    return root / "tscpkg-cache"


def _cache_key(archive: Path) -> str:
    stat = archive.stat()
    digest = hashlib.sha1(str(archive.resolve()).encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^0-9A-Za-z_.-]", "_", archive.stem)[:40]
    return "%s-%d-%d-%s" % (stem, stat.st_mtime_ns, stat.st_size, digest)


def extract(path: PathLike, member: str, cache_root: Optional[PathLike] = None) -> Path:
    """Extract *member* to a cache directory and return a real filesystem path.

    PyGame needs a filename, not an archive member, so audio is extracted once
    and reused until the container changes (tracked by size and mtime).
    """

    archive = Path(path)
    name = _normalise(member)
    try:
        with zipfile.ZipFile(archive) as handle:
            try:
                info = handle.getinfo(name)
            except KeyError as exc:
                raise PackageError("missing member %s in %s" % (name, archive)) from exc
            root = Path(cache_root) if cache_root else default_cache_root()
            target = root / _cache_key(archive) / Path(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and target.stat().st_size == info.file_size:
                return target
            temporary = target.with_name(target.name + ".part")
            with handle.open(info) as reader, open(temporary, "wb") as writer:
                shutil.copyfileobj(reader, writer, _CHUNK)
            os.replace(temporary, target)
            return target
    except (OSError, zipfile.BadZipFile) as exc:
        raise PackageError("cannot extract %s from %s" % (name, archive)) from exc
