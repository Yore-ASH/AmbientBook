import json
import zipfile

import pytest

from tscp_player import archive
from tscp_player.plot import (
    PlotPackageError,
    available_plots,
    discover_plot,
    discover_plots,
    load_archive_package,
    load_plot,
    load_plot_package,
)

SCRIPT = "TSCP 1\nD|f|SGk=|0.1,0.1\n"
CHARACTERS = {
    "NAME": "Demo",
    "VERSION": "0.0.1",
    "CHARACTERS": {"f": {"NAME": "FISH", "STYLE": ""}},
    "DEPENDECE": {"MAIN": {"VERSION": "0.0.1"}, "MUSIC_PACK": {"VERSION": "0.0.1"}},
}


def _new(tmp_path, name="Demo"):
    return archive.create(tmp_path / "demo", name=name)


def _populated(tmp_path):
    """A container holding one script, one track and a matching CONFIG."""

    path = _new(tmp_path)
    song = tmp_path / "song.flac"
    song.write_bytes(b"FLAC" * 64)
    archive.update(
        path,
        add={"Musics/song.flac": song},
        text={
            "Scripts/plot.tscp": SCRIPT,
            "Scripts/__init__.json": json.dumps(CHARACTERS),
            "Musics/__init__.json": json.dumps(
                {"VERSION": "0.0.1", "CONFIG": {"iw": "song.flac"}}
            ),
        },
    )
    return path


def test_create_writes_a_valid_container(tmp_path):
    path = _new(tmp_path)
    assert path.suffix == ".tscpkg"
    assert archive.members(path) == [
        "__init__.json", "Scripts/__init__.json", "Musics/__init__.json",
    ]
    manifest = archive.read_json(path, "__init__.json")
    assert manifest["FORMAT"] == "tscpkg 1"
    assert manifest["NAME"] == "Demo"


def test_create_refuses_to_overwrite(tmp_path):
    path = _new(tmp_path)
    with pytest.raises(archive.PackageError):
        archive.create(path, name="Other")


def test_audio_is_stored_and_text_is_deflated(tmp_path):
    path = _new(tmp_path)
    song = tmp_path / "song.ogg"
    song.write_bytes(b"\x00" * 5000)
    archive.update(path, add={"Musics/song.ogg": song}, text={"Scripts/a.tscp": SCRIPT})
    with zipfile.ZipFile(path) as handle:
        assert handle.getinfo("Musics/song.ogg").compress_type == zipfile.ZIP_STORED
        assert handle.getinfo("Scripts/a.tscp").compress_type == zipfile.ZIP_DEFLATED


def test_update_adds_and_removes_members(tmp_path):
    path = _new(tmp_path)
    archive.update(path, text={"Scripts/a.tscp": SCRIPT})
    assert archive.list_dir(path, "Scripts", ".tscp") == ["a.tscp"]
    archive.update(path, remove=["Scripts/a.tscp"])
    assert archive.list_dir(path, "Scripts", ".tscp") == []
    # Repacking must not drop the untouched required members.
    assert archive.has_member(path, "Scripts/__init__.json")
    assert archive.has_member(path, "Musics/__init__.json")


def test_extract_reuses_the_cache(tmp_path):
    path = _populated(tmp_path)
    cache = tmp_path / "cache"
    first = archive.extract(path, "Musics/song.flac", cache)
    assert first.read_bytes() == (tmp_path / "song.flac").read_bytes()
    stamp = first.stat().st_mtime_ns
    second = archive.extract(path, "Musics/song.flac", cache)
    assert second == first
    assert second.stat().st_mtime_ns == stamp


def test_missing_and_broken_members_are_reported(tmp_path):
    path = _new(tmp_path)
    assert archive.has_member(path, "Scripts/nope.tscp") is False
    with pytest.raises(archive.PackageError):
        archive.read_text(path, "Scripts/nope.tscp")
    with pytest.raises(archive.PackageError):
        archive.extract(path, "Musics/nope.flac", tmp_path / "cache")

    junk = tmp_path / "junk.tscpkg"
    junk.write_text("not a zip file", encoding="utf-8")
    with pytest.raises(archive.PackageError):
        archive.members(junk)


def test_load_a_container_plot(tmp_path):
    path = _populated(tmp_path)
    package = load_archive_package(path, cache_root=tmp_path / "cache")
    assert package.name == "Demo"
    assert package.characters["f"].name == "FISH"
    assert package.script_names() == ["plot.tscp"]
    assert package.load_script("plot.tscp").lines[0].text == "Hi"
    assert package.directory is None
    resolved = package.music_path("iw")
    assert resolved.is_file()
    assert resolved.name == "song.flac"
    assert resolved.read_bytes() == (tmp_path / "song.flac").read_bytes()


def test_discover_plot_accepts_a_container_file(tmp_path):
    path = _populated(tmp_path)
    assert discover_plot(path).name == "Demo"


def test_discover_plots_lists_containers_and_folders(tmp_path):
    container = _populated(tmp_path)
    legacy = tmp_path / "legacy"
    for name in ("Musics", "Scripts"):
        (legacy / name).mkdir(parents=True)
    (legacy / "__init__.json").write_text(
        json.dumps({"NAME": "Legacy"}), encoding="utf-8"
    )
    (legacy / "Musics" / "__init__.json").write_text(
        json.dumps({"VERSION": "0.0.1", "CONFIG": {}}), encoding="utf-8"
    )
    (legacy / "Scripts" / "__init__.json").write_text(
        json.dumps({**CHARACTERS, "NAME": "Legacy"}), encoding="utf-8"
    )
    (legacy / "Scripts" / "old.tscp").write_text(SCRIPT, encoding="utf-8")

    found = available_plots(tmp_path)
    assert container in found
    assert legacy in found

    names = sorted(package.name for package in discover_plots(tmp_path))
    assert names == ["Demo", "Legacy"]

    # Two plots in one folder is ambiguous, so the single-plot helper refuses.
    with pytest.raises(PlotPackageError):
        discover_plot(tmp_path)


def test_available_plots_finds_containers_in_subfolders(tmp_path):
    deep = tmp_path / "dist" / "2026"
    deep.mkdir(parents=True)
    inner = archive.create(deep / "inner", name="Inner")
    top = archive.create(tmp_path / "top", name="Top")

    assert set(available_plots(tmp_path)) == {inner, top}
    assert sorted(package.name for package in discover_plots(tmp_path)) == ["Inner", "Top"]


def test_load_plot_dispatches_on_the_carrier(tmp_path):
    container = _new(tmp_path)
    assert load_plot(container).name == "Demo"
    with pytest.raises(PlotPackageError):
        load_plot(tmp_path / "missing.tscpkg")


def test_available_plots_lists_a_broken_container_but_loading_still_raises(tmp_path):
    broken = tmp_path / "broken.tscpkg"
    broken.write_text("not a zip file", encoding="utf-8")
    # Discovery stays honest: the file is listed so the caller can report it.
    assert available_plots(tmp_path) == [broken]
    with pytest.raises(PlotPackageError):
        discover_plots(tmp_path)


def test_player_skips_a_broken_container_and_keeps_the_rest(tmp_path):
    import Main

    good = _populated(tmp_path)
    (tmp_path / "broken.tscpkg").write_text("not a zip file", encoding="utf-8")

    playlists, problems = Main.load_playlists(tmp_path)
    assert [package.location for package, _ in playlists] == [good]
    assert [where.name for where, _ in problems] == ["broken.tscpkg"]


def test_player_reports_when_nothing_is_playable(tmp_path):
    import Main

    (tmp_path / "broken.tscpkg").write_text("not a zip file", encoding="utf-8")
    with pytest.raises(PlotPackageError):
        Main.load_playlists(tmp_path)


def test_container_without_scripts_still_loads(tmp_path):
    path = _new(tmp_path)
    package = load_archive_package(path)
    assert package.script_names() == []
    assert package.music == {}


def test_directory_plot_still_works(tmp_path):
    root = tmp_path / "plain"
    for name in ("Musics", "Scripts"):
        (root / name).mkdir(parents=True)
    (root / "__init__.json").write_text("{}", encoding="utf-8")
    (root / "Musics" / "__init__.json").write_text(
        json.dumps({"VERSION": "0.0.1", "CONFIG": {"iw": "song.flac"}}), encoding="utf-8"
    )
    (root / "Scripts" / "__init__.json").write_text(json.dumps(CHARACTERS), encoding="utf-8")
    (root / "Scripts" / "plot.tscp").write_text(SCRIPT, encoding="utf-8")

    package = load_plot_package(root)
    assert package.directory == root
    assert package.script_names() == ["plot.tscp"]
    assert package.music_path("iw") == root / "Musics" / "song.flac"
