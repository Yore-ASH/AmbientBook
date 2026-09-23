"""Load and validate a plot, from a plain directory or a single ``.tscpkg``.

The same validation and the same :class:`PlotPackage` API cover both carriers,
so the player, the timing tools and the packer all work with either one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from . import archive
from .archive import PackageError
from .format import Script, parse_tscp


# The player and every bundled package manifest use this same release version.
PROGRAM_VERSION = (0, 0, 1)
PROGRAM_VERSION_TEXT = ".".join(map(str, PROGRAM_VERSION))

MUSICS = archive.MUSICS_DIR
SCRIPTS = archive.SCRIPTS_DIR


class PlotPackageError(ValueError):
    pass


@dataclass(frozen=True)
class Character:
    name: str
    style: str


class PlotSource:
    """Where a plot's members live."""

    kind = "abstract"

    def __init__(self, location: Union[str, Path]) -> None:
        self.location = Path(location)

    def read_text(self, member: str) -> str:
        raise NotImplementedError

    def exists(self, member: str) -> bool:
        raise NotImplementedError

    def list(self, directory: str, suffix: str = "") -> List[str]:
        raise NotImplementedError

    def materialize(self, member: str) -> Path:
        """Return a real filesystem path for a member (audio needs one)."""

        raise NotImplementedError


class DirectorySource(PlotSource):
    """A plot stored as ``Musics/`` and ``Scripts/`` inside a folder."""

    kind = "directory"

    def _path(self, member: str) -> Path:
        return self.location / Path(member)

    def read_text(self, member: str) -> str:
        try:
            return self._path(member).read_text(encoding="utf-8")
        except OSError as exc:
            raise PlotPackageError("cannot read %s: %s" % (member, exc)) from exc

    def exists(self, member: str) -> bool:
        return self._path(member).is_file()

    def list(self, directory: str, suffix: str = "") -> List[str]:
        folder = self._path(directory)
        if not folder.is_dir():
            return []
        return sorted(path.name for path in folder.glob("*" + suffix) if path.is_file())

    def materialize(self, member: str) -> Path:
        return self._path(member)


class ArchiveSource(PlotSource):
    """A plot stored as one ``.tscpkg`` file."""

    kind = "archive"

    def __init__(self, location: Union[str, Path], cache_root: Optional[Union[str, Path]] = None) -> None:
        super().__init__(location)
        self.cache_root = Path(cache_root) if cache_root else None

    def read_text(self, member: str) -> str:
        try:
            return archive.read_text(self.location, member)
        except PackageError as exc:
            raise PlotPackageError(str(exc)) from exc

    def exists(self, member: str) -> bool:
        return archive.has_member(self.location, member)

    def list(self, directory: str, suffix: str = "") -> List[str]:
        return archive.list_dir(self.location, directory, suffix)

    def materialize(self, member: str) -> Path:
        try:
            return archive.extract(self.location, member, self.cache_root)
        except PackageError as exc:
            raise PlotPackageError(str(exc)) from exc


class PlotPackage:
    def __init__(
        self,
        source: PlotSource,
        metadata: dict,
        characters: Dict[str, Character],
        music: Dict[str, str],
    ) -> None:
        self.source = source
        self.metadata = metadata
        self.characters = characters
        self.music = music

    @property
    def location(self) -> Path:
        """The plot directory or the ``.tscpkg`` file."""

        return self.source.location

    @property
    def directory(self) -> Optional[Path]:
        """The plot folder, or ``None`` when the plot is a ``.tscpkg``."""

        return self.location if self.source.kind == "directory" else None

    @property
    def name(self) -> str:
        return str(self.metadata.get("NAME") or self.location.stem)

    def load_script(self, filename: str) -> Script:
        member = "%s/%s" % (SCRIPTS, filename)
        if Path(filename).suffix.lower() != ".tscp" or not self.source.exists(member):
            raise PlotPackageError("script does not exist in Scripts: " + filename)
        try:
            return parse_tscp(self.source.read_text(member))
        except ValueError as exc:
            raise PlotPackageError("invalid script %s: %s" % (filename, exc)) from exc

    def script_names(self) -> List[str]:
        return self.source.list(SCRIPTS, ".tscp")

    def music_path(self, abbreviation: str) -> Path:
        """A real filename for one music abbreviation, extracting if needed."""

        filename = self.music.get(abbreviation, abbreviation)
        return self.source.materialize("%s/%s" % (MUSICS, filename))

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return "PlotPackage(%s, %r)" % (self.location, self.name)


def _version(value: object, source: str) -> tuple:
    try:
        parts = tuple(int(part) for part in str(value).split("."))
    except ValueError as exc:
        raise PlotPackageError("invalid version in " + source) from exc
    if len(parts) != 3:
        raise PlotPackageError("version must be a.b.c in " + source)
    return parts


def _read_json(source: PlotSource, member: str) -> dict:
    if not source.exists(member):
        raise PlotPackageError("invalid metadata: missing %s in %s" % (member, source.location))
    try:
        return json.loads(source.read_text(member))
    except json.JSONDecodeError as exc:
        raise PlotPackageError("invalid metadata: %s" % source.location) from exc


def _load(source: PlotSource) -> PlotPackage:
    metadata = _read_json(source, archive.MANIFEST)
    music_meta = _read_json(source, "%s/%s" % (MUSICS, archive.MANIFEST))
    scripts_meta = _read_json(source, "%s/%s" % (SCRIPTS, archive.MANIFEST))
    required = {"NAME", "VERSION", "CHARACTERS"}
    if not required.issubset(scripts_meta):
        raise PlotPackageError("Scripts/__init__.json is missing required fields")
    characters = {
        key: Character(str(value["NAME"]), str(value.get("STYLE", "")))
        for key, value in scripts_meta["CHARACTERS"].items()
    }
    music = load_music_files_from(music_meta)
    dependency = scripts_meta.get("DEPENDECE", {}).get("MAIN", {}).get("VERSION", "0.0.0")
    if _version(dependency, "Scripts dependency") != PROGRAM_VERSION:
        raise PlotPackageError("script requires main version %s, current is %s"
                               % (dependency, PROGRAM_VERSION_TEXT))
    actual_version = _version(music_meta.get("VERSION"), "Musics/__init__.json")
    if (actual_version[0] != PROGRAM_VERSION[0]
            or actual_version[1] != PROGRAM_VERSION[1]
            or actual_version[2] < PROGRAM_VERSION[2]):
        raise PlotPackageError("music pack version %s does not satisfy %s"
                               % (music_meta.get("VERSION"), PROGRAM_VERSION_TEXT))
    music_dependency = scripts_meta.get("DEPENDECE", {}).get("MUSIC_PACK", {}).get("VERSION")
    if music_dependency is not None:
        required_version = _version(music_dependency, "music dependency")
        if actual_version < required_version:
            raise PlotPackageError("music pack is older than script requirement")
    return PlotPackage(source, metadata, characters, music)


def load_music_files_from(metadata: dict) -> Dict[str, str]:
    """Turn a parsed ``Musics/__init__.json`` document into the CONFIG mapping."""

    config = metadata.get("CONFIG", {})
    if not isinstance(config, dict):
        raise PlotPackageError("Musics CONFIG must be an object")
    return {str(key): str(value) for key, value in config.items()}


def load_music_files(directory: Union[str, Path]) -> Dict[str, str]:
    """Read ``Musics/__init__.json`` and return the abbreviation -> filename map."""

    path = Path(directory) / archive.MANIFEST
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlotPackageError("invalid metadata: " + str(path)) from exc
    return load_music_files_from(metadata)


def find_music_directory(source: Union[str, Path]) -> Optional[Path]:
    """Locate the ``Musics`` directory belonging to *source*.

    *source* may be a ``.tscps``/``.tscp`` file, a ``Scripts`` directory, or the
    plot package root.  ``None`` means the script has no sibling music pack, in
    which case music directives cannot be resolved to files.
    """

    path = Path(source)
    if path.is_file():
        path = path.parent
    if path.name == SCRIPTS:
        path = path.parent
    candidate = path / MUSICS
    return candidate if candidate.is_dir() else None


def load_plot_package(root: Union[str, Path]) -> PlotPackage:
    """Load a plot stored as a directory containing ``Musics`` and ``Scripts``."""

    base = Path(root)
    if not base.is_dir():
        raise PlotPackageError("plot must be a directory: " + str(root))
    return _load(DirectorySource(base))


def load_archive_package(
    path: Union[str, Path], cache_root: Optional[Union[str, Path]] = None
) -> PlotPackage:
    """Load a plot stored as a single ``.tscpkg`` container."""

    candidate = Path(path)
    if not archive.is_package(candidate):
        raise PlotPackageError("plot must be a .tscpkg file: " + str(path))
    return _load(ArchiveSource(candidate, cache_root))


#: Directories never worth walking into while looking for plots.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".idea", ".vscode", "node_modules",
})


def _walk(root: Path):
    """Yield every entry under *root*, skipping obvious noise directories."""

    for child in sorted(root.iterdir()):
        if child.name in _SKIP_DIRS:
            continue
        yield child
        if child.is_dir() and not child.is_symlink():
            yield from _walk(child)


def _is_plot_directory(path: Path) -> bool:
    return (path / MUSICS).is_dir() and (path / SCRIPTS).is_dir()


def load_plot(path: Union[str, Path]) -> PlotPackage:
    """Load a plot from either a ``.tscpkg`` container or a plot directory."""

    candidate = Path(path)
    if archive.is_package(candidate):
        return load_archive_package(candidate)
    return load_plot_package(candidate)


def available_plots(source: Union[str, Path]) -> List[Path]:
    """Every plot found at *source*, searched recursively.

    A folder that is itself a plot is returned as-is.  Otherwise every
    ``.tscpkg`` container and every plot-shaped directory underneath it is
    returned, so a build-output folder such as ``source/dist/`` is picked up as
    well.  Containers come first, each group sorted, so the listing is stable.
    """

    path = Path(source)
    if archive.is_package(path):
        return [path]
    if not path.is_dir():
        return []
    if _is_plot_directory(path):
        return [path]

    containers: List[Path] = []
    folders: List[Path] = []
    for entry in _walk(path):
        if entry.is_file() and entry.suffix.lower() == archive.SUFFIX:
            containers.append(entry)
        elif entry.is_dir() and _is_plot_directory(entry):
            folders.append(entry)
    return sorted(containers) + sorted(folders)


def discover_plots(source: Union[str, Path]) -> List[PlotPackage]:
    """Load every plot found at *source*.

    An unreadable or invalid plot raises, rather than being silently skipped, so
    a broken container is reported instead of quietly disappearing from the
    player's list.  Use :func:`available_plots` plus :func:`load_plot` when a
    single bad file should not stop the others from loading.
    """

    return [load_plot(candidate) for candidate in available_plots(source)]


def discover_plot(source: Union[str, Path]) -> PlotPackage:
    """Load the single plot at *source*.

    Raises when the folder holds several plots, because the caller then needs to
    ask which one to use; use :func:`discover_plots` to list them.
    """

    path = Path(source)
    if archive.is_package(path):
        return load_archive_package(path)
    if not path.is_dir():
        raise PlotPackageError("source directory does not exist: " + str(path))
    if (path / MUSICS).is_dir() and (path / SCRIPTS).is_dir():
        return load_plot_package(path)
    candidates = available_plots(path)
    if len(candidates) == 1:
        return load_plot(candidates[0])
    raise PlotPackageError(
        "source must contain Musics and Scripts, or exactly one plot (found %d)"
        % len(candidates)
    )
