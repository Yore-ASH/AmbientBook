"""PyGame mixer integration with continuous-playback semantics.

A music track is only reloaded when the script switches to a different track.
Re-requesting the track that is already playing is a no-op, so a sentence is
always heard from the position where the previous sentence left off instead of
restarting the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class MusicPlayer:
    def __init__(self, mixer=None, loops: int = -1) -> None:
        self.loops = loops
        self.volume = 1.0
        self._current: Optional[str] = None
        self._paused = False
        if mixer is None:
            try:
                import pygame
                mixer = pygame.mixer
                mixer.init()
            except ImportError:
                mixer = None
            except pygame.error as exc:
                raise RuntimeError("PyGame audio initialization failed") from exc
        self._mixer = mixer

    @property
    def current(self) -> Optional[str]:
        """Path of the loaded track, or ``None`` when nothing is loaded."""

        return self._current

    @property
    def paused(self) -> bool:
        return self._paused

    def is_current(self, filename) -> bool:
        """Whether *filename* is the track that is already loaded."""

        try:
            return self._current == str(Path(filename))
        except TypeError:
            return False

    def ensure(self, filename, restart: bool = False) -> bool:
        """Play *filename*, keeping an already-loaded track running.

        Returns ``True`` when the stream was (re)loaded and ``False`` when the
        current track was deliberately left untouched.  This return value is
        what lets callers report "续播" instead of "重新播放".
        """

        if self._mixer is None:
            raise RuntimeError("PyGame is not installed; install requirements.txt")
        path = Path(filename)
        if not path.is_file():
            raise FileNotFoundError(path)
        key = str(path)
        if not restart and self._current == key:
            if self._paused:
                self.unpause()
            return False
        try:
            self._mixer.music.load(key)
            self._mixer.music.set_volume(max(0.0, min(1.0, self.volume)))
            self._mixer.music.play(loops=self.loops)
        except Exception as exc:
            raise RuntimeError("unable to play music: %s" % path) from exc
        self._current = key
        self._paused = False
        return True

    def play(self, filename, loops: Optional[int] = None, volume: float = 1.0) -> None:
        """Always (re)start *filename* from the beginning."""

        self.volume = volume
        previous = self.loops
        if loops is not None:
            self.loops = loops
        try:
            self.ensure(filename, restart=True)
        finally:
            self.loops = previous

    def pause(self) -> None:
        if self._mixer is None or self._current is None or self._paused:
            return
        try:
            self._mixer.music.pause()
        except Exception:
            return
        self._paused = True

    def unpause(self) -> None:
        if self._mixer is None or not self._paused:
            return
        try:
            self._mixer.music.unpause()
        except Exception:
            self._paused = False
            return
        self._paused = False

    def stop(self) -> None:
        if self._mixer is not None:
            self._mixer.music.stop()
        self._current = None
        self._paused = False

    def close(self) -> None:
        if self._mixer is not None:
            self._mixer.quit()
        self._current = None
        self._paused = False
