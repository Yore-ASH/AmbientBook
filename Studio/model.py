"""One project that covers the whole authoring flow.

The studio keeps characters, scripts (with their per-character timings) and the
music table together, and flushes all of it into a single ``.tscpkg`` on save.
Nothing here imports Qt, so the flow can be tested without a display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from PlotManager import model
from PlotManager.model import MusicDraft, PackError
from tscp_player import archive
from tscp_player.format import (
    Dialogue,
    Directive,
    Script,
    serialize_tscp,
    visible_text_length,
)
from tscp_player.plot import Character, MusicTrack, parse_tracks

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
