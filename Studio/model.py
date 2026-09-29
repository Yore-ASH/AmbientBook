"""One project that covers the whole authoring flow.

The studio keeps characters, scripts (with their per-character timings) and the
music table together, and flushes all of it into a single ``.tscpkg`` on save.
Nothing here imports Qt, so the flow can be tested without a display.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from CharacterCreator import model as characters
from PlotManager import model
from PlotManager.model import MusicDraft, PackError
from tscp_player import archive
from tscp_player.format import (
    Dialogue,
    Directive,
    Script,
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

EVENT_LABELS = {
    DIALOGUE: "角色对白",
    NARRATION: "旁白",
    SLEEP: "暂停",
    CLEAR: "清空屏幕",
    MUSIC: "播放音乐",
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
    """Which of the five row kinds this event is."""

    if isinstance(event, Dialogue):
        return DIALOGUE if event.character else NARRATION
    return {
        "s": SLEEP,
        "c": CLEAR,
        "p": MUSIC,
    }.get(getattr(event, "command", ""), getattr(event, "command", "?"))


def event_label(event) -> str:
    return EVENT_LABELS.get(event_kind(event), event_kind(event))


def describe_event(event) -> str:
    """The middle column of the story table."""

    if isinstance(event, Dialogue):
        return event.text
    if event.command == "s":
        return "%s 秒" % event.value
    if event.command == "p":
        return event.value or "（停止）"
    return "—"


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

    name = Path(str(value).strip()).name
    if not name:
        raise StudioError("剧本名不能为空")
    if not name.lower().endswith(".tscp"):
        name += ".tscp"
    if "/" in name or "\\" in name:
        raise StudioError("剧本名不能包含路径分隔符")
    return name


# --------------------------------------------------------------------------
# importing from existing sources
# --------------------------------------------------------------------------

COMPILED_HEADER = "TSCP "


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


def is_importable(source: PathLike) -> bool:
    """Whether :func:`collect_source` knows how to read *source*."""

    path = Path(source)
    return archive.is_package(path) or path.is_dir()


def collect_source(source: PathLike) -> Dict[str, Any]:
    """Gather characters, scripts and music from a ``.tscpkg`` or a folder."""

    path = Path(source)
    if archive.is_package(path):
        return _collect_from_package(path)
    if path.is_dir():
        return _collect_from_directory(path)
    raise StudioError(
        "只能从 .tscpkg 文件或包含 Musics/Scripts 的文件夹导入：%s" % path
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

    # -- creating and loading ------------------------------------------
    @classmethod
    def create(
        cls, path: PathLike, *, name: str, description: str = ""
    ) -> "StudioProject":
        target = Path(path)
        try:
            model.create_package(target, name=name, description=description)
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
    def save(self) -> Path:
        """Write characters, metadata and every script back into the container."""

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
        self.dirty = False
        return self.path

    def export(self, target: PathLike) -> Path:
        """Save, then write an independent copy to *target*."""

        self.save()
        try:
            return model.save_copy(self.path, target)
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc

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
    ) -> None:
        try:
            model.update_track(
                self.path,
                abbreviation,
                kind=kind,
                lyrics_text=lyrics_text,
                lyrics_name=lyrics_name,
                color=color,
            )
        except (PackError, archive.PackageError, OSError, ValueError) as exc:
            raise StudioError(str(exc)) from exc
        self._reload_tracks()

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
