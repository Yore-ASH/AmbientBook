"""Tests for the upload-and-deploy ZIP.

The archive is a real deployment artefact, so its shape matters: a wrong file
mode or a missing package turns into a confusing failure on somebody's server.
"""

import stat
import zipfile
from pathlib import Path

import pytest

from deploy import make_release


@pytest.fixture(scope="module")
def archive(tmp_path_factory):
    target = tmp_path_factory.mktemp("release") / "tscp-web-deploy.zip"
    make_release.build(target)
    return target


def _modes(target: Path):
    with zipfile.ZipFile(target) as opened:
        return {
            info.filename: ((info.external_attr >> 16) & 0o777, info.create_system)
            for info in opened.infolist()
        }


def test_the_archive_has_one_top_level_directory(archive):
    with zipfile.ZipFile(archive) as opened:
        names = opened.namelist()
    assert names
    assert all(name.startswith("tscp-web/") for name in names)
    assert "tscp-web/DEPLOY.txt" in names


def test_shell_scripts_are_stored_executable(archive):
    """Without this, `./deploy/install-ubuntu.sh` is Permission denied."""

    modes = _modes(archive)
    script = "tscp-web/deploy/install-ubuntu.sh"
    assert script in modes
    mode, system = modes[script]
    assert mode == 0o755, oct(mode)
    # MS-DOS entries make unzip ignore the mode entirely.
    assert system == make_release._UNIX


def test_plain_files_are_not_executable(archive):
    modes = _modes(archive)
    mode, _system = modes["tscp-web/webapp/__init__.py"]
    assert mode == 0o644


def test_the_archive_carries_what_the_site_needs(archive):
    with zipfile.ZipFile(archive) as opened:
        names = set(opened.namelist())

    for needed in (
        "webapp/__init__.py", "webapp/__main__.py", "webapp/app.py",
        "webapp/templates/base.html", "webapp/static/app.js",
        "PlotManager/__init__.py", "PlotManager/model.py",
        "tscp_player/__init__.py", "tscp_player/format.py",
        "requirements-web.txt", "pyproject.toml", "LICENSE",
        "deploy/install-ubuntu.sh", "deploy/tscp-web.service",
        "deploy/nginx-8888.conf", "deploy/tscp-web.env.example",
        "deploy/README.md",
    ):
        assert "tscp-web/" + needed in names, needed


def test_the_desktop_tools_and_demo_assets_stay_out(archive):
    """They need PySide6 and cannot run headless; the sample plot is 94 MB."""

    with zipfile.ZipFile(archive) as opened:
        names = opened.namelist()

    for unwanted in ("Studio/", "Ts2Tp/", "TSCPEditor/", "CharacterCreator/",
                     "source/", "Main.py", "requirements.txt"):
        assert not any(name.startswith("tscp-web/" + unwanted) for name in names), unwanted
    assert not [n for n in names if n.endswith((".tscpkg", ".flac", ".pyc"))]


def test_the_web_dependencies_exclude_the_desktop_stack(archive):
    with zipfile.ZipFile(archive) as opened:
        text = opened.read("tscp-web/requirements-web.txt").decode("utf-8")
    code = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    assert "PySide6" not in code
    assert "pygame" not in code
    assert "Flask" in code and "gunicorn" in code


def test_the_readme_tells_you_how_to_deploy(archive):
    with zipfile.ZipFile(archive) as opened:
        text = opened.read("tscp-web/DEPLOY.txt").decode("utf-8")
    assert "sudo ./deploy/install-ubuntu.sh" in text
    assert "8888" in text
    # The 8080 warning the user asked about has to be answered in the box.
    assert "8080" in text
    assert "systemctl restart tscp-web" in text


def test_nothing_local_leaks_in(archive):
    with zipfile.ZipFile(archive) as opened:
        names = opened.namelist()
    assert not [n for n in names if "/.git/" in n or "__pycache__" in n]
    assert not [n for n in names if n.endswith((".log", ".zip", ".swp"))]


def test_collect_only_walks_the_expected_places():
    files = make_release.collect()
    assert files
    tops = {path.relative_to(make_release.ROOT).parts[0] for path in files}
    assert tops <= set(make_release.INCLUDE_DIRS) | set(make_release.INCLUDE_FILES)
