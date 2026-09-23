import json
from pathlib import Path

import pytest

from PlotManager import model
from tscp_player import archive

SCRIPT = "TSCP 1\nD|f|SGk=|0.1,0.1\n"
SOURCE = "<p>iw\n<c>\n[f]你好\n旁白\n"
CHARACTERS = {
    "NAME": "Demo",
    "VERSION": "0.0.1",
    "CHARACTERS": {"f": {"NAME": "FISH", "STYLE": ""}},
    "DEPENDECE": {"MAIN": {"VERSION": "0.0.1"}, "MUSIC_PACK": {"VERSION": "0.0.1"}},
}


def _song(tmp_path, name="i want.flac", size=4096):
    path = tmp_path / name
    path.write_bytes(b"FLAC" * (size // 4))
    return path


def _package(tmp_path, name="Demo"):
    return model.create_package(tmp_path / "demo", name=name, description="简介")


def test_create_and_inspect(tmp_path):
    path = _package(tmp_path)
    info = model.inspect(path)
    assert info.name == "Demo"
    assert info.description == "简介"
    assert info.music == []
    assert info.scripts == []


def test_create_rejects_an_empty_name(tmp_path):
    with pytest.raises(model.PackError):
        model.create_package(tmp_path / "demo", name="   ")


def test_add_music_embeds_the_file_and_binds_the_abbreviation(tmp_path):
    path = _package(tmp_path)
    song = _song(tmp_path)
    assert model.add_music(path, [(song, "iw")]) == ["iw"]

    info = model.inspect(path)
    assert len(info.music) == 1
    entry = info.music[0]
    assert entry.abbreviation == "iw"
    assert entry.filename == "i want.flac"
    assert entry.present is True
    assert entry.size == song.stat().st_size
    # The audio member keeps the original bytes, stored rather than deflated.
    assert archive.extract(path, "Musics/i want.flac", tmp_path / "cache").read_bytes() == song.read_bytes()


def test_add_music_rejects_a_bad_abbreviation(tmp_path):
    path = _package(tmp_path)
    song = _song(tmp_path)
    with pytest.raises(model.PackError):
        model.add_music(path, [(song, "  ")])
    with pytest.raises(model.PackError):
        model.add_music(path, [(song, "bad|key")])


def test_missing_music_file_is_flagged(tmp_path):
    path = _package(tmp_path)
    song = _song(tmp_path)
    model.add_music(path, [(song, "iw")])
    # Simulate a config that points at a file nobody embedded.
    document = model.music_document(path)
    document["CONFIG"]["ghost"] = "ghost.flac"
    archive.update(
        path,
        text={"Musics/__init__.json": json.dumps(document, ensure_ascii=False)},
    )
    flags = {entry.abbreviation: entry.present for entry in model.inspect(path).music}
    assert flags == {"iw": True, "ghost": False}


def test_remove_music_keeps_a_file_still_used_by_another_key(tmp_path):
    path = _package(tmp_path)
    song = _song(tmp_path)
    model.add_music(path, [(song, "iw")])
    model.bind_music(path, "theme", "i want.flac")

    model.remove_music(path, ["iw"])
    assert model.music_config(path) == {"theme": "i want.flac"}
    assert archive.has_member(path, "Musics/i want.flac")

    model.remove_music(path, ["theme"])
    assert model.music_config(path) == {}
    assert archive.has_member(path, "Musics/i want.flac") is False


def test_bind_music_requires_an_embedded_file(tmp_path):
    path = _package(tmp_path)
    with pytest.raises(model.PackError):
        model.bind_music(path, "iw", "nope.flac")


def test_add_script_validates_and_embeds(tmp_path):
    path = _package(tmp_path)
    compiled = tmp_path / "plot.tscp"
    compiled.write_text(SCRIPT, encoding="utf-8")
    assert model.add_script(path, compiled) == "Scripts/plot.tscp"

    info = model.inspect(path)
    assert [entry.filename for entry in info.scripts] == ["plot.tscp"]
    assert info.scripts[0].lines == 1
    assert info.scripts[0].characters == 2
    assert info.scripts[0].keys == ("f",)


def test_add_script_rejects_a_broken_file(tmp_path):
    path = _package(tmp_path)
    broken = tmp_path / "broken.tscp"
    broken.write_text("not a compiled script\n", encoding="utf-8")
    with pytest.raises(model.PackError):
        model.add_script(path, broken)

    wrong = tmp_path / "notes.txt"
    wrong.write_text("hello", encoding="utf-8")
    with pytest.raises(model.PackError):
        model.add_script(path, wrong)


def test_compile_source_imports_a_tscps(tmp_path):
    path = _package(tmp_path)
    source = tmp_path / "plot.tscps"
    source.write_text(SOURCE, encoding="utf-8")
    assert model.add_script(path, source, compile_source=True) == "Scripts/plot.tscp"
    embedded = archive.read_text(path, "Scripts/plot.tscp")
    assert embedded.startswith("TSCP 1")
    assert "P|iw" in embedded


def test_remove_script(tmp_path):
    path = _package(tmp_path)
    compiled = tmp_path / "plot.tscp"
    compiled.write_text(SCRIPT, encoding="utf-8")
    model.add_script(path, compiled)
    model.remove_script(path, "plot.tscp")
    assert model.inspect(path).scripts == []
    # The required metadata member survives.
    assert archive.has_member(path, "Scripts/__init__.json")


def test_update_metadata(tmp_path):
    path = _package(tmp_path)
    model.update_metadata(path, name="Renamed", description="新简介")
    info = model.inspect(path)
    assert info.name == "Renamed"
    assert info.description == "新简介"
    with pytest.raises(model.PackError):
        model.update_metadata(path, name="  ")


def test_pack_directory_migrates_a_folder_plot(tmp_path):
    root = tmp_path / "legacy"
    for name in ("Musics", "Scripts"):
        (root / name).mkdir(parents=True)
    (root / "__init__.json").write_text(
        json.dumps({"NAME": "Legacy", "DESCRIPTION": "旧剧情"}), encoding="utf-8"
    )
    (root / "Musics" / "__init__.json").write_text(
        json.dumps({"VERSION": "0.0.1", "CONFIG": {"iw": "i want.flac"}}), encoding="utf-8"
    )
    song = root / "Musics" / "i want.flac"
    song.write_bytes(b"FLAC" * 256)
    (root / "Scripts" / "__init__.json").write_text(json.dumps(CHARACTERS), encoding="utf-8")
    (root / "Scripts" / "plot.tscp").write_text(SCRIPT, encoding="utf-8")

    target = model.pack_directory(root, tmp_path / "packed", description="打包")
    info = model.inspect(target)
    assert info.name == "Legacy"
    assert info.description == "打包"
    assert info.music[0].filename == "i want.flac"
    assert info.music[0].present is True
    assert [entry.filename for entry in info.scripts] == ["plot.tscp"]
    assert archive.read_json(target, "__init__.json")["FORMAT"] == archive.FORMAT_TEXT


def test_script_text_and_replace_round_trip(tmp_path):
    path = _package(tmp_path)
    compiled = tmp_path / "plot.tscp"
    compiled.write_text(SCRIPT, encoding="utf-8")
    model.add_script(path, compiled)

    assert model.script_text(path, "plot.tscp") == SCRIPT
    assert model.read_script(path, "plot.tscp").lines[0].text == "Hi"

    edited = "TSCP 1\nD|f|SGVsbG8=|0.1,0.2,0.3,0.4,0.5\n"
    model.replace_script(path, "plot.tscp", edited)
    assert model.script_text(path, "plot.tscp") == edited
    assert model.read_script(path, "plot.tscp").lines[0].text == "Hello"


def test_replace_script_rejects_invalid_text(tmp_path):
    path = _package(tmp_path)
    compiled = tmp_path / "plot.tscp"
    compiled.write_text(SCRIPT, encoding="utf-8")
    model.add_script(path, compiled)
    with pytest.raises(model.PackError):
        model.replace_script(path, "plot.tscp", "not a compiled script\n")
    # A rejected edit must leave the stored script untouched.
    assert model.script_text(path, "plot.tscp") == SCRIPT


def test_script_member_rejects_missing_or_wrong_type(tmp_path):
    path = _package(tmp_path)
    with pytest.raises(model.PackError):
        model.script_text(path, "ghost.tscp")
    with pytest.raises(model.PackError):
        model.script_text(path, "plot.tscps")


def test_export_script_writes_a_file(tmp_path):
    path = _package(tmp_path)
    compiled = tmp_path / "plot.tscp"
    compiled.write_text(SCRIPT, encoding="utf-8")
    model.add_script(path, compiled)

    out = model.export_script(path, "plot.tscp", tmp_path / "out" / "plot.tscp")
    assert out == tmp_path / "out" / "plot.tscp"
    assert out.read_text(encoding="utf-8") == SCRIPT

    # An existing directory receives the script under its own name.
    folder = tmp_path / "folder"
    folder.mkdir()
    inside = model.export_script(path, "plot.tscp", folder)
    assert inside == folder / "plot.tscp"
    assert inside.read_text(encoding="utf-8") == SCRIPT


def test_save_copy_exports_everything_inserted(tmp_path):
    path = _package(tmp_path)
    song = _song(tmp_path)
    model.add_music(path, [(song, "iw")])
    compiled = tmp_path / "plot.tscp"
    compiled.write_text(SCRIPT, encoding="utf-8")
    model.add_script(path, compiled)

    exported = model.save_copy(path, tmp_path / "handout")
    assert exported.suffix == ".tscpkg"
    assert exported != path

    info = model.inspect(exported)
    assert info.name == "Demo"
    assert [entry.abbreviation for entry in info.music] == ["iw"]
    assert info.music[0].present is True
    assert [entry.filename for entry in info.scripts] == ["plot.tscp"]
    extracted = archive.extract(exported, "Musics/i want.flac", tmp_path / "cache")
    assert extracted.read_bytes() == song.read_bytes()


def test_save_copy_is_independent_from_the_original(tmp_path):
    path = _package(tmp_path)
    exported = model.save_copy(path, tmp_path / "handout.tscpkg")

    model.update_metadata(exported, name="Handout")
    model.add_music(exported, [(_song(tmp_path, "extra.flac"), "ex")])

    assert model.inspect(path).name == "Demo"
    assert model.inspect(path).music == []
    assert model.inspect(exported).name == "Handout"


def test_save_copy_rejects_bad_targets(tmp_path):
    path = _package(tmp_path)
    with pytest.raises(model.PackError):
        model.save_copy(path, path)

    broken = tmp_path / "broken.tscpkg"
    broken.write_text("not a zip", encoding="utf-8")
    with pytest.raises(model.PackError):
        model.save_copy(broken, tmp_path / "out.tscpkg")

    with pytest.raises(model.PackError):
        model.save_copy(tmp_path / "missing.tscpkg", tmp_path / "out.tscpkg")


def test_suggest_abbreviation(tmp_path):
    assert model.suggest_abbreviation(tmp_path / "i want.flac") == "i"
    assert model.suggest_abbreviation(tmp_path / "theme.ogg") == "theme"
    assert model.suggest_abbreviation(tmp_path / ".flac") == "flac"
    assert model.suggest_abbreviation(tmp_path / "   .ogg") == "track"
