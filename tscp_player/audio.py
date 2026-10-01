"""PyGame mixer integration with continuous-playback semantics.

A music track is only reloaded when the script switches to a different track.
Re-requesting the track that is already playing is a no-op, so a sentence is
always heard from the position where the previous sentence left off instead of
restarting the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

#: SDL_mixer only implements SetMusicPosition for these.  For FLAC and WAV
#: ``set_pos`` returns without moving, so the format is what decides whether a
#: track can be started from the middle -- not a runtime check.
SEEKABLE_SUFFIXES = frozenset({".ogg", ".oga", ".opus", ".mp3"})


class SeekUnsupported(RuntimeError):
    """Raised when the loaded format cannot be positioned."""

    def __init__(self, name: str) -> None:
        suffix = Path(str(name)).suffix.lower()
        self.suffix = suffix
        super().__init__(
            "这种格式（%s）无法定位播放，只能从头开始" % (suffix.lstrip(".") or "未知")
        )


def supports_seek(filename) -> bool:
    """Whether a track of this format can be started from the middle."""

    try:
        return Path(filename).suffix.lower() in SEEKABLE_SUFFIXES
    except TypeError:
        return False


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

    def is_busy(self) -> bool:
        """Whether the mixer is still playing something.

        pygame loops by default (``loops=-1``), so without asking the mixer
        there is no way to notice that a one-shot track has finished.
        """

        if self._mixer is None or self._current is None:
            return False
        try:
            return bool(self._mixer.music.get_busy())
        except Exception:
            return False

    def position(self) -> Optional[float]:
        """Seconds since playback started, or ``None`` when nothing is playing.

        This is the mixer's own clock, which is what a seek has to be measured
        against -- asking the file is not possible through pygame.
        """

        if self._mixer is None or self._current is None:
            return None
        try:
            return self._mixer.music.get_pos() / 1000.0
        except Exception:
            return None

    def seek(self, seconds: float) -> None:
        """Start the loaded track from *seconds*.

        Raises :class:`SeekUnsupported` for a format SDL_mixer cannot position,
        rather than silently continuing from where it was -- which is exactly
        what ``set_pos`` does on a FLAC.
        """

        if self._mixer is None or self._current is None:
            raise RuntimeError("没有正在播放的音乐")
        if not supports_seek(self._current):
            raise SeekUnsupported(self._current)
        try:
            self._mixer.music.set_pos(max(0.0, float(seconds)))
        except Exception as exc:  # noqa: BLE001
            raise SeekUnsupported(self._current) from exc

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
