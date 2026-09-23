"""Console renderer for compiled TSCP events."""

from __future__ import annotations

from dataclasses import dataclass
import shutil
import re
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from .audio import MusicPlayer
from .format import Dialogue, Directive, Script
from .music import STOP_WORDS
from .plot import Character


RESET = "\033[0m"
ANSI_TOKEN = re.compile(
    r"(?:\\033|\\x1b|\x1b)\[[0-?]*[ -/]*[@-~]"
    r"|\x9b[0-?]*[ -/]*[@-~]"
)


def expand_ansi(text: str) -> str:
    """Convert source spellings such as ``\\033[91m`` to terminal escapes."""
    return text.replace("\\033", "\033").replace("\\x1b", "\033")


@dataclass(frozen=True)
class RenderOptions:
    color: bool = True
    vertical_center: bool = True
    quote: str = ""


class TerminalRenderer:
    def __init__(self, characters: dict[str, Character], output=sys.stdout,
                 options: Optional[RenderOptions] = None,
                 music: Optional[MusicPlayer] = None,
                 sleeper: Callable[[float], None] = time.sleep,
                 music_files: Optional[dict[str, str]] = None,
                 music_root: Optional[Path] = None,
                 music_resolver: Optional[Callable[[str], object]] = None) -> None:
        self.characters = characters
        self.output = output
        self.options = options or RenderOptions()
        self.music = music or MusicPlayer()
        self.sleeper = sleeper
        self.music_files = music_files or {}
        self.music_root = music_root
        self.music_resolver = music_resolver
        self.history: list[str] = []

    def _music_path(self, abbreviation: str) -> Path:
        """Resolve one ``<p>`` abbreviation to a real filename."""

        if self.music_resolver is not None:
            return Path(self.music_resolver(abbreviation))
        filename = self.music_files.get(abbreviation, abbreviation)
        path = Path(filename)
        if self.music_root and not path.is_absolute():
            path = self.music_root / filename
        return path

    def clear(self) -> None:
        self.output.write("\033[2J\033[H")
        self.output.flush()
        self.history.clear()

    def _visible_width(self, text: str) -> int:
        return len(text)

    def _layout(self, item: Dialogue) -> str:
        width = max((len(value.name) for value in self.characters.values()), default=0)
        if item.character is None:
            return " " * (width + 4) + item.text
        character = self.characters.get(item.character)
        name = character.name if character else item.character
        return name.ljust(width) + " : " + self.options.quote + item.text

    def _styled_name(self, item: Dialogue) -> str:
        if item.character is None:
            return ""
        character = self.characters.get(item.character)
        name = character.name if character else item.character
        if self.options.color and character and character.style:
            return character.style + name + RESET
        return name

    def _draw(self) -> None:
        if self.options.vertical_center:
            rows = shutil.get_terminal_size((80, 24)).lines
            padding = max(0, (rows - len(self.history)) // 2)
            self.output.write("\033[2J\033[H" + "\n" * padding)
        self.output.write("\n".join(self.history) + ("\n" if self.history else ""))
        self.output.flush()

    def _display(self, item: Dialogue) -> None:
        width = max((len(value.name) for value in self.characters.values()), default=0)
        if item.character is None:
            prefix = " " * (width + 4)
        else:
            character = self.characters.get(item.character)
            raw_name = character.name if character else item.character
            prefix = self._styled_name(item) + " " * (width - len(raw_name)) + " : " + self.options.quote
        line = prefix
        self.history.append(line)
        self._draw()
        visible_index = 0
        expanded = expand_ansi(item.text)
        for token in re.split("(" + ANSI_TOKEN.pattern + ")", expanded):
            if not token:
                continue
            if ANSI_TOKEN.fullmatch(token):
                line += expand_ansi(token)
                self.history[-1] = line
                self._draw()
                continue
            for character in token:
                line += character
                self.history[-1] = line
                self._draw()
                delay = item.delays[visible_index] if visible_index < len(item.delays) else 0.0
                visible_index += 1
                if delay:
                    self.sleeper(delay)

    def render(self, script: Script) -> None:
        for item in script.lines:
            if isinstance(item, Dialogue):
                self._display(item)
            elif item.command == "c":
                self.clear()
            elif item.command == "s":
                self.sleeper(float(item.value))
            elif item.command == "p":
                if item.value.strip().lower() in STOP_WORDS:
                    self.music.stop()
                else:
                    self.music.ensure(str(self._music_path(item.value)))
