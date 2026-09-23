import json

import pytest

from tscp_player.plot import (
    PlotPackageError,
    discover_plot,
    find_music_directory,
    load_music_files,
    load_plot_package,
)


def test_load_folder_and_validate_versions(tmp_path):
    (tmp_path / "Musics").mkdir()
    (tmp_path / "Scripts").mkdir()
    (tmp_path / "__init__.json").write_text("{}", encoding="utf-8")
    (tmp_path / "Musics" / "__init__.json").write_text(
        json.dumps({"VERSION": "0.0.2", "CONFIG": {"theme": "theme.flac"}}), encoding="utf-8")
    (tmp_path / "Scripts" / "__init__.json").write_text(json.dumps({
        "NAME": "Demo", "VERSION": "0.0.0", "CHARACTERS": {"f": {"NAME": "FISH", "STYLE": "\033[33m"}},
        "DEPENDECE": {"MAIN": {"VERSION": "0.0.1"}, "MUSIC_PACK": {"VERSION": "0.0.1"}},
    }), encoding="utf-8")
    (tmp_path / "Scripts" / "plot.tscp").write_text("TSCP 1\nD|f|SGk=|0.1,0.1\n", encoding="utf-8")
    package = load_plot_package(str(tmp_path))
    assert package.characters["f"].name == "FISH"
    assert package.load_script("plot.tscp").lines[0].text == "Hi"


def test_reject_incompatible_music(tmp_path):
    for folder in ("Musics", "Scripts"):
        (tmp_path / folder).mkdir()
    (tmp_path / "__init__.json").write_text("{}", encoding="utf-8")
    (tmp_path / "Musics" / "__init__.json").write_text(json.dumps({"VERSION": "1.0.0", "CONFIG": {}}), encoding="utf-8")
    (tmp_path / "Scripts" / "__init__.json").write_text(json.dumps({
        "NAME": "x", "VERSION": "0.0.0", "CHARACTERS": {},
        "DEPENDECE": {"MAIN": {"VERSION": "0.0.1"}, "MUSIC_PACK": {"VERSION": "0.0.0"}},
    }), encoding="utf-8")
    with __import__("pytest").raises(PlotPackageError):
        load_plot_package(str(tmp_path))


def _music_pack(root):
    (root / "Musics").mkdir(parents=True, exist_ok=True)
    (root / "Musics" / "__init__.json").write_text(
        json.dumps({"VERSION": "0.0.2", "CONFIG": {"iw": "i want.flac"}}),
        encoding="utf-8",
    )


def test_find_music_directory_accepts_every_entry_point(tmp_path):
    _music_pack(tmp_path)
    (tmp_path / "Scripts").mkdir()
    script = tmp_path / "Scripts" / "plot.tscps"
    script.write_text("[f]你好\n", encoding="utf-8")

    for entry in (script, tmp_path / "Scripts", tmp_path):
        assert find_music_directory(entry) == tmp_path / "Musics"


def test_find_music_directory_returns_none_without_a_pack(tmp_path):
    (tmp_path / "Scripts").mkdir()
    script = tmp_path / "Scripts" / "plot.tscps"
    script.write_text("[f]你好\n", encoding="utf-8")
    assert find_music_directory(script) is None


def test_load_music_files_maps_abbreviations(tmp_path):
    _music_pack(tmp_path)
    assert load_music_files(tmp_path / "Musics") == {"iw": "i want.flac"}


def test_load_music_files_rejects_a_non_object_config(tmp_path):
    (tmp_path / "Musics").mkdir()
    (tmp_path / "Musics" / "__init__.json").write_text(
        json.dumps({"VERSION": "0.0.2", "CONFIG": []}), encoding="utf-8"
    )
    with pytest.raises(PlotPackageError):
        load_music_files(tmp_path / "Musics")


def test_discover_direct_source_and_one_script(tmp_path):
    plot = tmp_path / "source"
    (plot / "Musics").mkdir(parents=True)
    (plot / "Scripts").mkdir()
    (plot / "__init__.json").write_text(
        json.dumps({"NAME": "Demo", "DESCRIPTION": "Intro"}), encoding="utf-8")
    (plot / "Musics" / "__init__.json").write_text(
        json.dumps({"VERSION": "0.0.2", "CONFIG": {}}), encoding="utf-8")
    (plot / "Scripts" / "__init__.json").write_text(json.dumps({
        "NAME": "Demo", "VERSION": "0.0.0", "CHARACTERS": {},
        "DEPENDECE": {"MAIN": {"VERSION": "0.0.1"}},
    }), encoding="utf-8")
    (plot / "Scripts" / "plot.tscp").write_text("TSCP 1\n", encoding="utf-8")
    package = discover_plot(tmp_path / "source")
    assert package.metadata["NAME"] == "Demo"
    assert package.script_names() == ["plot.tscp"]
