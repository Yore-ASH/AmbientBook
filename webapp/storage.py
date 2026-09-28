"""Per-user ``.tscpkg`` files on disk.

This is the bridge to the existing desktop logic: every operation delegates to
``PlotManager.model`` / ``tscp_player`` so the website and the desktop tools
produce byte-for-byte compatible packages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PlotManager import model
from PlotManager.model import MusicDraft, PackError
from tscp_player import archive
from tscp_player.plot import PlotPackage, PlotPackageError, load_archive_package


class StorageError(ValueError):
    """Raised when a plot file operation cannot be completed."""


def plots_dir(data_dir) -> Path:
    path = Path(data_dir) / "plots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir(data_dir) -> Path:
    path = Path(data_dir) / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def plot_path(data_dir, plot_id: str) -> Path:
    return plots_dir(data_dir) / ("%s.tscpkg" % plot_id)


def create_plot_file(
    data_dir, plot_id: str, *, name: str, description: str = ""
) -> Path:
    path = plot_path(data_dir, plot_id)
    try:
        return model.create_package(path, name=name, description=description)
    except (PackError, archive.PackageError, OSError, OSError) as exc:
        raise StorageError(str(exc)) from exc


def delete_plot_file(data_dir, plot_id: str) -> None:
    path = plot_path(data_dir, plot_id)
    if path.is_file():
        try:
            path.unlink()
        except OSError as exc:
            raise StorageError("无法删除剧情包：%s" % exc) from exc


def load(data_dir, plot_id: str) -> PlotPackage:
    """Open one plot package, extracting audio into the app's cache."""

    path = plot_path(data_dir, plot_id)
    if not path.is_file():
        raise StorageError("剧情包不存在")
    try:
        return load_archive_package(path, cache_root=cache_dir(data_dir))
    except (PlotPackageError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def describe(package: PlotPackage) -> Dict[str, Any]:
    """Everything the browser needs to render one plot."""

    return {
        "name": package.name,
        "description": str(package.metadata.get("DESCRIPTION") or ""),
        "version": str(package.metadata.get("VERSION") or ""),
        "characters": {
            key: {"name": value.name, "style": value.style}
            for key, value in sorted(package.characters.items())
        },
        "music": [
            {
                "abbreviation": track.abbreviation,
                "filename": track.filename,
                "kind": track.kind,
                "lyrics": track.lyrics,
                "has_lyrics": track.has_lyrics,
                "color": track.color,
                "size": archive.member_size(
                    package.location, "%s/%s" % (archive.MUSICS_DIR, track.filename)
                ),
            }
            for track in sorted(package.tracks.values(), key=lambda item: item.abbreviation)
        ],
        "scripts": package.script_names(),
    }


def script_text(data_dir, plot_id: str, filename: str) -> str:
    try:
        return model.script_text(plot_path(data_dir, plot_id), filename)
    except (PackError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def save_script(data_dir, plot_id: str, filename: str, text: str) -> None:
    try:
        model.replace_script(plot_path(data_dir, plot_id), filename, text)
    except (PackError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def import_script(data_dir, plot_id: str, source: Path, *, compile_source: bool) -> str:
    try:
        return model.add_script(
            plot_path(data_dir, plot_id), source, compile_source=compile_source
        )
    except (PackError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def delete_script(data_dir, plot_id: str, filename: str) -> None:
    try:
        model.remove_script(plot_path(data_dir, plot_id), filename)
    except (PackError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def add_music(data_dir, plot_id: str, drafts: List[MusicDraft]) -> List[str]:
    try:
        return model.add_tracks(plot_path(data_dir, plot_id), drafts)
    except (PackError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def update_music(
    data_dir,
    plot_id: str,
    abbreviation: str,
    *,
    kind: Optional[str] = None,
    lyrics_text: Optional[str] = None,
    lyrics_name: Optional[str] = None,
    color: Optional[str] = None,
) -> None:
    try:
        model.update_track(
            plot_path(data_dir, plot_id),
            abbreviation,
            kind=kind,
            lyrics_text=lyrics_text,
            lyrics_name=lyrics_name,
            color=color,
        )
    except (PackError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def delete_music(data_dir, plot_id: str, abbreviation: str) -> List[str]:
    try:
        return model.remove_music(plot_path(data_dir, plot_id), [abbreviation])
    except (PackError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def update_metadata(
    data_dir, plot_id: str, *, name: Optional[str] = None, description: Optional[str] = None
) -> None:
    try:
        model.update_metadata(
            plot_path(data_dir, plot_id), name=name, description=description
        )
    except (PackError, archive.PackageError, OSError, ValueError) as exc:
        raise StorageError(str(exc)) from exc


def lyrics_text(data_dir, plot_id: str, abbreviation: str) -> str:
    """The raw LRC of one track, or an empty string when it has none."""

    package = load(data_dir, plot_id)
    path = package.lyrics_path(abbreviation)
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
