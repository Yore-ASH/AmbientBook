"""One project that covers the whole authoring flow.

The studio keeps characters, scripts (with their per-character timings) and the
music table together, and flushes all of it into a single ``.tscpkg`` on save.
Nothing here imports Qt, so the flow can be tested without a display.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from CharacterCreator import model as characters
from PlotManager import model
from PlotManager.model import MusicDraft, PackError
from tscp_player import archive, lyrics
from tscp_player.format import (
    Dialogue,
    Directive,
    Script,
    is_note,
    make_note,
    note_parts,
    parse_tscp,
    parse_tscps,
    serialize_tscp,
    visible_text_length,
)
from tscp_player.plot import (
    Character,
    MusicTrack,
    PlotPackageError,
    load_archive_package,
    parse_tracks,
)

PathLike = Union[str, Path]

#: Event kinds used by the story table.
DIALOGUE = "dialogue"
NARRATION = "narration"
SLEEP = "sleep"
CLEAR = "clear"
MUSIC = "music"
NOTE = "note"

EVENT_LABELS = {
    DIALOGUE: "角色对白",
    NARRATION: "旁白",
    SLEEP: "暂停",
    CLEAR: "清空屏幕",
    MUSIC: "播放音乐",
    NOTE: "补充内容",
}


class StudioError(ValueError):
    """Raised when a project operation cannot be completed."""


# --------------------------------------------------------------------------
# pure helpers (also used by the tests)
# --------------------------------------------------------------------------

def normalise_script(script: Script) -> Script:
    """Give every dialogue exactly one delay per visible character.

    The serializer refuses a mismatch, so the studio repairs the list here
    rather than failing to save because a text was edited after it was timed.
    """

    lines = []
    for item in script.lines:
        if not isinstance(item, Dialogue):
            lines.append(item)
            continue
        expected = visible_text_length(item.text)
        delays = [max(0.0, float(value)) for value in item.delays][:expected]
        if len(delays) < expected:
            delays.extend([0.0] * (expected - len(delays)))
        lines.append(Dialogue(item.character, item.text, delays))
    return Script(lines)


def event_kind(event) -> str:
    """Which of the six row kinds this event is."""

    if isinstance(event, Dialogue):
        return DIALOGUE if event.character else NARRATION
    if is_note(event):
        return NOTE
    return {
        "s": SLEEP,
        "c": CLEAR,
        "p": MUSIC,
    }.get(getattr(event, "command", ""), getattr(event, "command", "?"))


def event_label(event) -> str:
    return EVENT_LABELS.get(event_kind(event), event_kind(event))


def describe_event(
    event,
    characters: Optional[Dict[str, Character]] = None,
    tracks: Optional[Dict[str, MusicTrack]] = None,
) -> str:
    """The middle column of the story table, in human terms."""

    if isinstance(event, Dialogue):
        return event.text
    if is_note(event):
        _color, seconds, text = note_parts(event)
        return "%s（%g 秒）" % (text, seconds)
    if event.command == "s":
        return "%s 秒" % event.value
    if event.command == "p":
        if not event.value:
            return "（停止音乐）"
        return "♪ " + track_label(event.value, tracks)
    return "—"


def event_character(event, characters: Optional[Dict[str, Character]] = None) -> str:
    """The character column: the display name, never the file key."""

    if not isinstance(event, Dialogue):
        return ""
    return character_label(event.character, characters)


def dialogue_count(script: Script) -> int:
    return sum(1 for item in script.lines if isinstance(item, Dialogue))


def timed_slots(event: Dialogue) -> int:
    """How many key presses this line needs (every visible char but spaces)."""

    from Ts2Tp.model import timed_character_positions

    return len(timed_character_positions(event.text))


def timing_progress(script: Script) -> Tuple[int, int]:
    """``(recorded, total)`` key presses across the whole script."""

    recorded = total = 0
    for item in script.lines:
        if not isinstance(item, Dialogue):
            continue
        needed = timed_slots(item)
        total += needed
        has_delays = bool(item.delays) and len(item.delays) == visible_text_length(item.text)
        if has_delays:
            recorded += needed
    return recorded, total


def is_script_timed(script: Script) -> bool:
    """True when every dialogue already carries a usable delay list."""

    for item in script.lines:
        if not isinstance(item, Dialogue):
            continue
        if len(item.delays) != visible_text_length(item.text):
            return False
    return True


def script_characters(script: Script) -> List[str]:
    """Abbreviations used by a script, in first-seen order."""

    seen: List[str] = []
    for item in script.lines:
        if isinstance(item, Dialogue) and item.character and item.character not in seen:
            seen.append(item.character)
    return seen


def missing_characters(script: Script, characters: Dict[str, Character]) -> List[str]:
    """Abbreviations the script uses but the project does not define."""

    return [key for key in script_characters(script) if key not in characters]


def next_script_name(existing: Iterable[str], base: str = "plot") -> str:
    """``plot.tscp``, then ``plot2.tscp``, ... avoiding names already taken."""

    taken = {str(name) for name in existing}
    candidate = "%s.tscp" % base
    index = 2
    while candidate in taken:
        candidate = "%s%d.tscp" % (base, index)
        index += 1
    return candidate


def safe_script_name(value: str) -> str:
    """Normalise a user-typed script name into ``something.tscp``."""

    return script_base_name(value) + ".tscp"


#: Characters that are illegal in a file name on Windows, plus control codes.
_ILLEGAL_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = (
    {"con", "prn", "aux", "nul"}
    | {"com%d" % index for index in range(1, 10)}
    | {"lpt%d" % index for index in range(1, 10)}
)


def script_base_name(value: str) -> str:
    """Validate a typed script name and return it *without* the extension.

    The dialog only ever asks for the bare name, so a stray ``.tscp`` (or an
    outright wrong extension) is stripped here rather than becoming a member
    name nobody expected.
    """

    name = str(value).strip()
    if name.lower().endswith(".tscp"):
        name = name[: -len(".tscp")].strip()
    if not name:
        raise StudioError("剧本名不能为空")
    if name in {".", ".."}:
        raise StudioError("这个名字不能用")
    if name.lower() in _RESERVED_NAMES:
        raise StudioError("「%s」是系统保留名，换一个" % name)
    illegal = _ILLEGAL_NAME.search(name)
    if illegal:
        raise StudioError("剧本名不能包含 %s" % illegal.group(0))
    if name != name.rstrip(" ."):
        raise StudioError("剧本名不能以句点或空格结尾")
    return name


#: A character/track key only ever lives inside the file; it must survive
#: ``[key]``, ``D|key|`` and ``P|key``, so keep it to plain ASCII word chars.
_KEY_UNSAFE = re.compile(r"[^0-9A-Za-z_]+")


def suggest_key(name: str, taken: Iterable[str] = (), prefix: str = "c") -> str:
    """Derive a short, unique, format-safe key from a display name.

    With a GUI nobody needs to type or read the abbreviation, but the format
    still needs one, so it is generated here instead of being asked for.
    """

    used = {str(item) for item in taken}
    cleaned = _KEY_UNSAFE.sub("", str(name))
    base = (cleaned[:4] or prefix).lower()
    if base[0].isdigit():
        base = prefix + base
    candidate = base
    index = 2
    while candidate in used:
        candidate = "%s%d" % (base, index)
        index += 1
    return candidate


def character_label(key: Optional[str], characters: Optional[Dict[str, Character]] = None) -> str:
    """What to show for a character: its name, never its key."""

    if not key:
        return ""
    character = (characters or {}).get(key)
    return character.name if character is not None else key


def track_label(key: Optional[str], tracks: Optional[Dict[str, MusicTrack]] = None) -> str:
    """What to show for a music track: the audio file, not its key."""

    if not key:
        return ""
    track = (tracks or {}).get(key)
    if track is None:
        return key
    return Path(track.filename).stem or track.filename or key


# --------------------------------------------------------------------------
# importing from existing sources
# --------------------------------------------------------------------------

COMPILED_HEADER = "TSCP "

#: A studio *project* is a ``.tscpkg`` container plus a ``History/`` folder, so
#: the playable part stays byte-for-byte what the player expects and exporting a
#: clean ``.tscpkg`` is just "copy everything except History/".
PROJECT_SUFFIX = archive.PROJECT_SUFFIX
PROJECT_FORMAT = archive.PROJECT_FORMAT
PLOT_SUFFIX = archive.SUFFIX
HISTORY_DIR = "History"
HISTORY_INDEX = HISTORY_DIR + "/__init__.json"
HISTORY_FORMAT = "tscpks-history 1"
MAX_REVISIONS = 200
#: Automatic snapshots are capped separately from the named checkpoints, so a
#: long session cannot push out the versions the author actually cares about.
MAX_AUTO_SNAPSHOTS = 5

#: ``.tscpc`` — a small, portable character file.
CHARACTERS_SUFFIX = ".tscpc"
CHARACTERS_FORMAT = "tscpc 1"


def is_project(path: PathLike) -> bool:
    """Whether *path* is a studio project (``.tscpkgs``) rather than a plot."""

    candidate = Path(path)
    if not candidate.is_file() or candidate.suffix.lower() != PROJECT_SUFFIX:
        return False
    try:
        manifest = archive.read_json(candidate, archive.MANIFEST, {}) or {}
    except (archive.PackageError, OSError, ValueError):
        return False
    return str(manifest.get("FORMAT", "")) == PROJECT_FORMAT


def parse_script_text(text: str, suffix: str = "") -> Script:
    """Parse a compiled ``.tscp`` or a source ``.tscps`` from text."""

    try:
        if suffix.lower() == ".tscp" or text.lstrip().startswith(COMPILED_HEADER):
            return parse_tscp(text)
        return parse_tscps(text)
    except ValueError as exc:
        raise StudioError(str(exc)) from exc


def _normalise_style(key: str, style: str) -> str:
    if not style or not str(style).strip():
        return ""
    try:
        return characters.normalize_ansi(style)
    except ValueError as exc:
        raise StudioError("角色 %s 的样式无效：%s" % (key, exc)) from exc


def parse_character_text(text: str) -> List[Tuple[str, str, str]]:
    """Parse the batch format ``缩写<TAB>全名<TAB>ANSI样式``.

    This is the same parser the standalone character generator uses, so anything
    pasted there keeps working here.
    """

    try:
        rows = characters.parse_batch_text(text)
    except ValueError as exc:
        raise StudioError(str(exc)) from exc
    return [
        (key.strip(), name.strip(), _normalise_style(key.strip(), style))
        for key, name, style in rows
    ]


def parse_character_json(text: str) -> List[Tuple[str, str, str]]:
    """Read character rows from JSON.

    Accepts either a whole ``Scripts/__init__.json`` (with a ``CHARACTERS``
    object) or a bare ``{"f": {"NAME": ..., "STYLE": ...}}`` mapping, which is
    what the character generator outputs.
    """

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StudioError("不是有效的 JSON：%s" % exc) from exc
    if not isinstance(document, dict):
        raise StudioError("角色文件必须是一个 JSON 对象")

    table = document.get("CHARACTERS")
    if table is None:
        # A bare mapping is fine, but a plot manifest is not.
        table = document
        if any(not isinstance(value, dict) for value in table.values()):
            raise StudioError("这个 JSON 里没有 CHARACTERS 对象")
    if not isinstance(table, dict):
        raise StudioError("CHARACTERS 必须是对象")

    rows: List[Tuple[str, str, str]] = []
    for key, value in table.items():
        if not isinstance(value, dict) or "NAME" not in value:
            raise StudioError("角色 %s 缺少 NAME" % key)
        rows.append((str(key), str(value["NAME"]), _normalise_style(str(key), value.get("STYLE", ""))))
    if not rows:
        raise StudioError("这个文件里没有任何角色")
    return rows


def parse_characters(text: str) -> List[Tuple[str, str, str]]:
    """Read characters from either supported spelling.

    A pasted blob may be JSON (what the character generator writes) or the
    tab-separated batch table, so the paste dialog simply hands it over here and
    the shape is worked out from the content.
    """

    stripped = text.lstrip()
    if stripped.startswith("{"):
        return parse_character_json(text)
    return parse_character_text(text)


def characters_document(rows: Iterable[Tuple[str, str, str]]) -> Dict[str, Any]:
    """The ``.tscpc`` document for *rows*."""

    table: Dict[str, Dict[str, str]] = {}
    for key, name, style in rows:
        abbreviation = str(key).strip()
        label = str(name).strip()
        if not abbreviation or not label:
            raise StudioError("角色的缩写和名字都不能为空")
        table[abbreviation] = {
            "NAME": label,
            "STYLE": _normalise_style(abbreviation, style),
        }
    if not table:
        raise StudioError("没有可导出的角色")
    return {"FORMAT": CHARACTERS_FORMAT, "CHARACTERS": table}


def characters_text(rows: Iterable[Tuple[str, str, str]]) -> str:
    """Pretty JSON for a ``.tscpc`` file; the same shape can be pasted back."""

    return json.dumps(characters_document(rows), ensure_ascii=False, indent=2) + "\n"


def write_characters(path: PathLike, rows: Iterable[Tuple[str, str, str]]) -> Path:
    """Save characters next to the project so they can be reused later."""

    target = Path(path)
    if target.suffix.lower() != CHARACTERS_SUFFIX:
        target = target.with_suffix(CHARACTERS_SUFFIX)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(characters_text(rows), encoding="utf-8")
    except OSError as exc:
        raise StudioError("无法写入 %s：%s" % (target, exc)) from exc
    return target


def is_importable(source: PathLike) -> bool:
    """Whether :func:`collect_source` knows how to read *source*."""

    path = Path(source)
    return archive.is_package(path) or is_project(path) or path.is_dir()


def collect_source(source: PathLike) -> Dict[str, Any]:
    """Gather characters, scripts and music from a plot, project or folder."""

    path = Path(source)
    if archive.is_package(path) or is_project(path):
        return _collect_from_package(path)
    if path.is_dir():
        return _collect_from_directory(path)
    raise StudioError(
        "只能从 .tscpkg / .tscpkgs 文件或包含 Musics/Scripts 的文件夹导入：%s" % path
    )


def _collect_from_package(path: Path) -> Dict[str, Any]:
    try:
        package = load_archive_package(path)
    except (PlotPackageError, archive.PackageError, OSError, ValueError) as exc:
        raise StudioError(str(exc)) from exc

    music: List[Dict[str, Any]] = []
    for key, track in package.tracks.items():
        try:
            audio = package.music_path(key)
        except (PlotPackageError, archive.PackageError, OSError, ValueError):
            continue
        music.append(
            {
                "abbreviation": key,
                "audio": audio,
                "kind": track.kind,
                "lyrics_file": package.lyrics_path(key),
                "color": track.color,
            }
        )

    return {
        "characters": [
            (key, value.name, value.style) for key, value in package.characters.items()
        ],
        "scripts": {
            filename: model.read_script(path, filename)
            for filename in package.script_names()
        },
        "music": music,
    }


def _collect_from_directory(root: Path) -> Dict[str, Any]:
    meta = root / "Scripts" / "__init__.json"
    rows: List[Tuple[str, str, str]] = []
    if meta.is_file():
        try:
            rows = parse_character_json(meta.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            rows = []

    scripts: Dict[str, Script] = {}
    scripts_dir = root / "Scripts"
    if scripts_dir.is_dir():
        for path in sorted(scripts_dir.iterdir()):
            if path.suffix.lower() not in {".tscp", ".tscps"}:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            try:
                scripts[path.with_suffix(".tscp").name] = parse_script_text(text, path.suffix)
            except StudioError:
                continue

    music: List[Dict[str, Any]] = []
    music_meta = root / "Musics" / "__init__.json"
    if music_meta.is_file():
        try:
            document = json.loads(music_meta.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            document = {}
        config = document.get("CONFIG") or {}
        table = document.get("TRACKS") or {}
        for key, filename in config.items():
            entry = table.get(key) or {}
            lyrics_name = entry.get("LYRICS")
            music.append(
                {
                    "abbreviation": str(key),
                    "audio": root / "Musics" / str(filename),
                    "kind": str(entry.get("KIND", "instrumental")).strip().lower(),
                    "lyrics_file": (
                        root / "Musics" / str(lyrics_name) if lyrics_name else None
                    ),
                    "color": str(entry.get("COLOR") or ""),
                }
            )

    return {"characters": rows, "scripts": scripts, "music": music}


def export_playable(source: PathLike, target: PathLike) -> Path:
    """Write a clean ``.tscpkg``: current state only, no ``History/``.

    The project file and the playable file share every other member, so this is
    a straight copy with the history stripped and the manifest relabelled.
    """

    origin = Path(source)
    if not origin.is_file():
        raise StudioError("找不到项目文件：%s" % origin)
    requested = Path(target)
    # Check "same file" before any suffix fixup, so exporting a project onto its
    # own path is refused instead of quietly writing a sibling .tscpkg.
    if requested.resolve() == origin.resolve():
        raise StudioError("导出目标和项目文件是同一个文件")
    destination = requested
    if destination.suffix.lower() != archive.SUFFIX:
        destination = destination.with_suffix(archive.SUFFIX)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise StudioError("目标已存在：%s" % destination)

    try:
        with zipfile.ZipFile(origin) as reader, zipfile.ZipFile(destination, "w") as writer:
            for info in reader.infolist():
                name = info.filename
                if info.is_dir() or name.startswith(HISTORY_DIR + "/"):
                    continue
                data = reader.read(name)
                if name == archive.MANIFEST:
                    manifest = json.loads(data.decode("utf-8"))
                    manifest["FORMAT"] = archive.FORMAT_TEXT
                    data = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
                writer.writestr(
                    name,
                    data,
                    compress_type=(
                        zipfile.ZIP_STORED
                        if archive.is_audio(name)
                        else zipfile.ZIP_DEFLATED
                    ),
                )
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        if destination.exists():
            destination.unlink()
        raise StudioError("导出失败：%s" % exc) from exc
    return destination


def _read_history(path: PathLike) -> List[Dict[str, Any]]:
    try:
        document = archive.read_json(path, HISTORY_INDEX, None)
    except (archive.PackageError, OSError, ValueError):
        return []
    if not isinstance(document, dict):
        return []
    entries = document.get("REVISIONS")
    return list(entries) if isinstance(entries, list) else []


def _write_history(path: PathLike, revisions: List[Dict[str, Any]], snapshot: Optional[Tuple[str, Dict[str, Any]]] = None) -> None:
    texts = {
        HISTORY_INDEX: json.dumps(
            {"FORMAT": HISTORY_FORMAT, "REVISIONS": revisions}, ensure_ascii=False, indent=2
        )
    }
    if snapshot is not None:
        revision_id, document = snapshot
        texts["%s/%s.json" % (HISTORY_DIR, revision_id)] = (
            json.dumps(document, ensure_ascii=False, indent=2) + "\n"
        )
    archive.update(path, text=texts)


def _digest(document: Dict[str, Any], keys: Optional[Iterable[str]] = None) -> str:
    """Hash the parts of a snapshot that mean 'the content changed'.

    ``keys`` restricts the comparison to a subset, which is what lets a
    writing-only snapshot be compared against a full one on equal terms.
    """

    import hashlib

    chosen = sorted(
        set(keys) if keys is not None else set(document) - {"ID", "TIME", "LABEL"}
    )
    payload = {key: document.get(key) for key in chosen}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


# --------------------------------------------------------------------------
# the project
# --------------------------------------------------------------------------

@dataclass
class StudioProject:
    """Characters, scripts and music of one ``.tscpkg``, held in memory."""

    path: Path
    name: str = ""
    description: str = ""
    characters: Dict[str, Character] = field(default_factory=dict)
    scripts: Dict[str, Script] = field(default_factory=dict)
    tracks: Dict[str, MusicTrack] = field(default_factory=dict)
    dirty: bool = False
    extra_notes: List[str] = field(default_factory=list)
    #: Set by :meth:`save` so callers can tell whether a revision was written.
    last_revision_id: Optional[str] = None

    # -- creating and loading ------------------------------------------
    @classmethod
    def create(
        cls, path: PathLike, *, name: str, description: str = ""
    ) -> "StudioProject":
        target = Path(path)
        try:
            if target.suffix.lower() == PROJECT_SUFFIX:
                # A project carries its own revision history alongside the very
                # same playable members.
                archive.create(
                    target,
                    name=name,
                    description=description,
                    suffix=PROJECT_SUFFIX,
                    format_text=PROJECT_FORMAT,
                )
                _write_history(target, [])
            else:
                model.create_package(target, name=name, description=description)
                target = target.with_suffix(archive.SUFFIX)
        except (PackError, archive.PackageError, OSError) as exc:
            raise StudioError(str(exc)) from exc
        project = cls(path=target, name=name.strip(), description=description)
        project.dirty = True
        return project

    @classmethod
    def load(cls, path: PathLike) -> "StudioProject":
        target = Path(path)
        try:
            info = model.inspect(target)
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc
        project = cls(path=target, name=info.name, description=info.description)
        project.refresh()
        return project

    def refresh(self) -> None:
        """Re-read everything from the container on disk."""

        try:
            table = model.character_table(self.path)
            config = model.music_config(self.path)
            document = model.music_document(self.path)
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc
        self.characters = {
            key: Character(value["NAME"], value["STYLE"]) for key, value in table.items()
        }
        self.tracks = parse_tracks(document, config)
        self.scripts = {
            filename: model.read_script(self.path, filename)
            for filename in archive.list_dir(self.path, archive.SCRIPTS_DIR, ".tscp")
        }
        self.dirty = False

    # -- saving --------------------------------------------------------
    @property
    def has_history(self) -> bool:
        return self.path.suffix.lower() == PROJECT_SUFFIX or bool(_read_history(self.path))

    def save(
        self,
        label: Optional[str] = None,
        *,
        include_music: bool = True,
        auto: bool = False,
    ) -> Path:
        """Write characters, metadata and every script back into the container.

        On a project file this also records a revision, so the history lives in
        the same file as the work itself.
        """

        try:
            model.update_metadata(
                self.path, name=self.name.strip() or "未命名剧情", description=self.description
            )
            model.update_characters(
                self.path,
                {
                    key: {"NAME": character.name, "STYLE": character.style}
                    for key, character in self.characters.items()
                },
            )
            for filename, script in self.scripts.items():
                model.write_script(self.path, filename, serialize_tscp(normalise_script(script)))
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc
        self.last_revision_id = None
        if self.has_history:
            try:
                self.last_revision_id = self._record_revision(
                    label, include_music=include_music, auto=auto
                )
            except (archive.PackageError, OSError, ValueError) as exc:
                raise StudioError("保存历史版本失败：%s" % exc) from exc
        self.dirty = False
        return self.path

    def auto_snapshot(self) -> Optional[str]:
        """Flush the current work and record a scripts-only snapshot.

        Returns the new revision id, or ``None`` when nothing changed since the
        last one — the caller uses that to stretch the next interval.
        """

        if not self.has_history:
            return None
        # Music is left out on purpose: a snapshot only needs to capture the
        # writing, and audio would make every snapshot enormous.
        self.save(include_music=False, auto=True)
        return self.last_revision_id

    def export(self, target: PathLike) -> Path:
        """Write a clean ``.tscpkg``: current state, no history."""

        self.save()
        try:
            return export_playable(self.path, target)
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc

    # -- revision history ----------------------------------------------
    def snapshot(self, include_music: bool = True) -> Dict[str, Any]:
        """The full editable state, as JSON."""

        document: Dict[str, Any] = {
            "NAME": self.name,
            "DESCRIPTION": self.description,
            "CHARACTERS": {
                key: {"NAME": character.name, "STYLE": character.style}
                for key, character in self.characters.items()
            },
            "SCRIPTS": {
                filename: serialize_tscp(normalise_script(script))
                for filename, script in self.scripts.items()
            },
        }
        if include_music:
            document["MUSIC"] = {
                key: {
                    "KIND": track.kind,
                    "COLOR": track.color,
                    "FILENAME": track.filename,
                    "LYRICS": track.lyrics,
                    "LYRICS_TEXT": self.lyrics_text(key),
                }
                for key, track in self.tracks.items()
            }
        return document

    def revisions(self) -> List[Dict[str, Any]]:
        """Newest first."""

        return list(reversed(_read_history(self.path)))

    def _read_snapshot(self, revision_id: str) -> Optional[Dict[str, Any]]:
        member = "%s/%s.json" % (HISTORY_DIR, revision_id)
        try:
            document = archive.read_json(self.path, member, None)
        except (archive.PackageError, OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def revision(self, revision_id: str) -> Dict[str, Any]:
        document = self._read_snapshot(revision_id)
        if document is None:
            raise StudioError("找不到版本 %s" % revision_id)
        return document

    def _record_revision(
        self,
        label: Optional[str],
        *,
        include_music: bool = True,
        auto: bool = False,
    ) -> Optional[str]:
        document = self.snapshot(include_music=include_music)
        keys = set(document)
        digest = _digest(document, keys)
        history = _read_history(self.path)
        if history and not label:
            # Compare like with like: a snapshot without music is checked against
            # the previous one on the same fields, so "nothing changed" is still
            # detected instead of every automatic pass looking like an edit.
            previous = self._read_snapshot(history[-1]["ID"])
            if previous is not None and _digest(previous, keys) == digest:
                return None                  # nothing actually changed
        revision_id = "R%06d" % (len(history) + 1)
        document["ID"] = revision_id
        document["TIME"] = datetime.now().isoformat(timespec="seconds")
        document["LABEL"] = label or ("自动快照" if auto else "自动保存")
        entry = {
            "ID": revision_id,
            "TIME": document["TIME"],
            "LABEL": document["LABEL"],
            "HASH": digest,
            "AUTO": bool(auto),
            "CHARACTERS": len(self.characters),
            "SCRIPTS": len(self.scripts),
        }
        history.append(entry)

        # Drop the oldest automatic snapshots first, keeping the named ones, then
        # trim the overall cap so a long-lived project cannot grow forever.
        drop: List[str] = []
        if auto:
            autos = [item for item in history if item.get("AUTO")]
            drop.extend(item["ID"] for item in autos[:-MAX_AUTO_SNAPSHOTS])
        remaining = [item for item in history if item["ID"] not in drop]
        if len(remaining) > MAX_REVISIONS:
            drop.extend(
                item["ID"] for item in remaining[: len(remaining) - MAX_REVISIONS]
            )
        if drop:
            archive.update(
                self.path,
                remove=["%s/%s.json" % (HISTORY_DIR, key) for key in drop],
            )
            history = [item for item in history if item["ID"] not in drop]

        _write_history(self.path, history, (revision_id, document))
        return revision_id

    def restore(self, revision_id: str) -> Dict[str, Any]:
        """Load a revision back into memory (the file is untouched until save)."""

        document = self.revision(revision_id)
        self.name = str(document.get("NAME", self.name))
        self.description = str(document.get("DESCRIPTION", self.description))
        self.characters = {
            str(key): Character(str(value.get("NAME", key)), str(value.get("STYLE", "")))
            for key, value in (document.get("CHARACTERS") or {}).items()
        }
        scripts: Dict[str, Script] = {}
        for filename, text in (document.get("SCRIPTS") or {}).items():
            try:
                scripts[str(filename)] = parse_script_text(str(text), ".tscp")
            except StudioError:
                continue
        self.scripts = scripts
        self.dirty = True

        restored, missing = 0, []
        for key, entry in (document.get("MUSIC") or {}).items():
            if key not in self.tracks:
                missing.append(str(key))
                continue
            try:
                model.update_track(
                    self.path,
                    key,
                    kind=str(entry.get("KIND") or "instrumental"),
                    lyrics_text=entry.get("LYRICS_TEXT") or None,
                    color=str(entry.get("COLOR") or ""),
                )
                restored += 1
            except (PackError, archive.PackageError, OSError, ValueError):
                missing.append(str(key))
        if restored:
            self._reload_tracks()
        return {
            "id": revision_id,
            "label": str(document.get("LABEL", "")),
            "music_restored": restored,
            "music_missing": missing,
        }

    # -- characters ----------------------------------------------------
    def character_rows(self) -> List[Tuple[str, str, str]]:
        return [
            (key, character.name, character.style)
            for key, character in self.characters.items()
        ]

    def set_character(self, key: str, name: str, style: str = "") -> str:
        abbreviation = str(key).strip()
        label = str(name).strip()
        if not abbreviation:
            raise StudioError("角色缩写不能为空")
        if any(character in abbreviation for character in "[]\r\n"):
            raise StudioError("角色缩写不能包含 [ ] 或换行")
        if not label:
            raise StudioError("角色名字不能为空")
        self.characters[abbreviation] = Character(label, str(style).strip())
        self.dirty = True
        return abbreviation

    def remove_character(self, key: str) -> None:
        if key not in self.characters:
            raise StudioError("没有这个角色：%s" % key)
        del self.characters[key]
        self.dirty = True

    def merge_characters(self, rows: Iterable[Tuple[str, str, str]]) -> List[str]:
        """Add or overwrite characters from ``(缩写, 名字, 样式)`` rows.

        Existing abbreviations are updated rather than rejected, so re-importing
        a character list is idempotent.
        """

        keys: List[str] = []
        for key, name, style in rows:
            keys.append(self.set_character(key, name, style))
        return keys

    # -- scripts -------------------------------------------------------
    def script(self, filename: str) -> Script:
        if filename not in self.scripts:
            raise StudioError("没有这个剧本：%s" % filename)
        return self.scripts[filename]

    def new_script(self, name: Optional[str] = None) -> str:
        filename = (
            safe_script_name(name)
            if name
            else next_script_name(self.scripts)
        )
        if filename in self.scripts:
            raise StudioError("剧本已存在：%s" % filename)
        self.scripts[filename] = Script()
        self.dirty = True
        return filename

    def delete_script(self, filename: str) -> None:
        if filename not in self.scripts:
            raise StudioError("没有这个剧本：%s" % filename)
        del self.scripts[filename]
        self.dirty = True

    def rename_script(self, filename: str, new_name: str) -> str:
        if filename not in self.scripts:
            raise StudioError("没有这个剧本：%s" % filename)
        target = safe_script_name(new_name)
        if target != filename and target in self.scripts:
            raise StudioError("剧本已存在：%s" % target)
        script = self.scripts.pop(filename)
        self.scripts[target] = script
        self.dirty = True
        return target

    # -- events --------------------------------------------------------
    def add_event(self, filename: str, event, index: Optional[int] = None) -> int:
        script = self.script(filename)
        position = len(script.lines) if index is None else max(0, min(index, len(script.lines)))
        script.lines.insert(position, event)
        self.dirty = True
        return position

    def replace_event(self, filename: str, index: int, event) -> None:
        script = self.script(filename)
        if not 0 <= index < len(script.lines):
            raise StudioError("事件序号超出范围")
        script.lines[index] = event
        self.dirty = True

    def delete_event(self, filename: str, index: int) -> None:
        script = self.script(filename)
        if not 0 <= index < len(script.lines):
            raise StudioError("事件序号超出范围")
        del script.lines[index]
        self.dirty = True

    def move_event(self, filename: str, index: int, delta: int) -> int:
        script = self.script(filename)
        target = index + delta
        if not (0 <= index < len(script.lines)) or not (0 <= target < len(script.lines)):
            return index
        script.lines[index], script.lines[target] = script.lines[target], script.lines[index]
        self.dirty = True
        return target

    def set_script(self, filename: str, script: Script) -> None:
        self.scripts[filename] = script
        self.dirty = True

    # -- importing -----------------------------------------------------
    def import_script(self, source: PathLike, name: Optional[str] = None) -> str:
        """Add a ``.tscp`` or ``.tscps`` file as a new script in this project."""

        path = Path(source)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise StudioError("无法读取 %s：%s" % (path, exc)) from exc
        script = parse_script_text(text, path.suffix)
        filename = safe_script_name(name or path.with_suffix(".tscp").name)
        if filename in self.scripts:
            raise StudioError("剧本已存在：%s" % filename)
        self.scripts[filename] = script
        self.dirty = True
        return filename

    def apply_timing(self, filename: str, source: Script) -> Tuple[int, int]:
        """Copy delays from *source* onto the matching lines of *filename*.

        Lines are matched by position **and** text.  A line that does not match
        is left untouched rather than shifting every later timing, and the
        counts of applied and skipped lines are returned so the caller can say
        what actually happened.
        """

        target = self.script(filename)
        applied = skipped = 0
        lines = []
        for index, item in enumerate(target.lines):
            other = source.lines[index] if index < len(source.lines) else None
            if (
                isinstance(item, Dialogue)
                and isinstance(other, Dialogue)
                and item.text == other.text
                and len(other.delays) == visible_text_length(other.text)
            ):
                lines.append(Dialogue(item.character, item.text, list(other.delays)))
                applied += 1
            else:
                if isinstance(item, Dialogue):
                    skipped += 1
                lines.append(item)
        self.set_script(filename, Script(lines))
        return applied, skipped

    def import_from(
        self,
        source: PathLike,
        *,
        characters_wanted: bool = True,
        scripts_wanted: bool = True,
        music_wanted: bool = True,
    ) -> Dict[str, Any]:
        """Merge a ``.tscpkg`` or a legacy folder into this project.

        Characters are overwritten by abbreviation, scripts that would collide
        get a numeric suffix instead of clobbering what is already here, and a
        music track that cannot be embedded is reported in ``notes`` rather than
        failing the whole import.
        """

        collected = collect_source(source)

        result: Dict[str, Any] = {
            "characters": 0,
            "scripts": 0,
            "music": 0,
            "notes": [],
        }
        if characters_wanted and collected["characters"]:
            result["characters"] = len(self.merge_characters(collected["characters"]))

        if scripts_wanted and collected["scripts"]:
            for filename, script in collected["scripts"].items():
                target = filename
                index = 2
                while target in self.scripts:
                    target = "%s%d.tscp" % (Path(filename).stem, index)
                    index += 1
                self.scripts[target] = script
                result["scripts"] += 1
            self.dirty = True

        if music_wanted and collected["music"]:
            drafts: List[MusicDraft] = []
            for item in collected["music"]:
                audio = Path(item["audio"])
                if not audio.is_file():
                    result["notes"].append("跳过缺失的音频：%s" % audio.name)
                    continue
                kind = item.get("kind") or "instrumental"
                lyrics_file = item.get("lyrics_file")
                if kind == "lyrics" and not (lyrics_file and Path(lyrics_file).is_file()):
                    # Declared as lyrics but the .lrc is missing: bring the audio
                    # in as instrumental and say so, rather than failing.
                    result["notes"].append(
                        "%s 原本标为带歌词，但没有歌词文件，已按纯音乐导入"
                        % item["abbreviation"]
                    )
                    kind, lyrics_file = "instrumental", None
                drafts.append(
                    MusicDraft(
                        abbreviation=item["abbreviation"],
                        source=audio,
                        kind=kind,
                        lyrics_file=lyrics_file,
                        color=item.get("color", ""),
                    )
                )
            if drafts:
                self.add_music(drafts)
                result["music"] = len(drafts)

        return result

    # -- music ---------------------------------------------------------
    def add_music(self, drafts: Iterable[MusicDraft]) -> List[str]:
        """Embed tracks straight away; audio lives in the container, not memory."""

        try:
            keys = model.add_tracks(self.path, list(drafts))
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc
        self._reload_tracks()
        return keys

    def update_music(
        self,
        abbreviation: str,
        *,
        kind: Optional[str] = None,
        lyrics_text: Optional[str] = None,
        lyrics_name: Optional[str] = None,
        color: Optional[str] = None,
        lyric_document: Optional[str] = None,
    ) -> None:
        try:
            model.update_track(
                self.path,
                abbreviation,
                kind=kind,
                lyrics_text=lyrics_text,
                lyrics_name=lyrics_name,
                color=color,
                lyric_document=lyric_document,
            )
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc
        self._reload_tracks()

    def track_document(self, abbreviation: str) -> str:
        """The JSON lyric document for one track, or ``""``."""

        try:
            return model.track_document(self.path, abbreviation)
        except (PackError, archive.PackageError, OSError, ValueError):
            return ""

    def track_styles(self, abbreviation: str) -> Dict[float, Dict[str, str]]:
        """Per-line font and colour overrides, keyed by rounded time.

        Keyed by time rather than row number so the styling survives a lyric
        text edit that only touches the words.
        """

        document = self.track_document(abbreviation)
        if not document:
            return {}
        try:
            lines = lyrics.parse_lyric_document(document).lines
        except (lyrics.LyricError, ValueError):
            return {}
        return {
            round(line.time, 2): {
                key: value
                for key, value in (
                    ("font", line.font),
                    ("color", line.color),
                    ("translation_font", line.translation_font),
                    ("translation_color", line.translation_color),
                )
                if value
            }
            for line in lines
            if line.has_style
        }

    def remove_music(self, abbreviation: str) -> None:
        try:
            model.remove_music(self.path, [abbreviation])
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc
        self._reload_tracks()

    def lyrics_text(self, abbreviation: str) -> str:
        track = self.tracks.get(abbreviation)
        if track is None or not track.has_lyrics:
            return ""
        member = "%s/%s" % (archive.MUSICS_DIR, track.lyrics)
        if not archive.has_member(self.path, member):
            return ""
        try:
            return archive.read_text(self.path, member)
        except (PackError, archive.PackageError, OSError, ValueError):
            return ""

    def audio_path(self, abbreviation: str) -> Path:
        """Extract one track and return a real filename for playback."""

        track = self.tracks.get(abbreviation)
        if track is None:
            raise StudioError("没有这个音乐简称：%s" % abbreviation)
        return archive.extract(
            self.path, "%s/%s" % (archive.MUSICS_DIR, track.filename)
        )

    def _reload_tracks(self) -> None:
        document = model.music_document(self.path)
        config = model.music_config(self.path)
        self.tracks = parse_tracks(document, config)

    # -- reporting -----------------------------------------------------
    def summary(self) -> Dict[str, object]:
        recorded = total = 0
        untimed: List[str] = []
        unknown: List[str] = []
        for filename, script in self.scripts.items():
            done, needed = timing_progress(script)
            recorded += done
            total += needed
            if needed and not is_script_timed(script):
                untimed.append(filename)
            for key in missing_characters(script, self.characters):
                if key not in unknown:
                    unknown.append(key)
        lyric_tracks = [key for key, track in self.tracks.items() if track.has_lyrics]
        return {
            "characters": len(self.characters),
            "scripts": len(self.scripts),
            "tracks": len(self.tracks),
            "lyrics_tracks": lyric_tracks,
            "timed": recorded,
            "timed_total": total,
            "untimed_scripts": untimed,
            "missing_characters": unknown,
            "dirty": self.dirty,
        }
