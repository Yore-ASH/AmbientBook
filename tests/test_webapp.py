import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from werkzeug.datastructures import MultiDict

from tscp_player.plot import load_archive_package
from webapp import TestConfig, create_app


@pytest.fixture
def app(tmp_path):
    return create_app(TestConfig, DATA_DIR=tmp_path / "data")


@pytest.fixture
def client(app):
    return app.test_client()


def register(client, username="alice", password="secret1"):
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": password, "display_name": username},
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["user"]


def new_client(app, username, password="secret1"):
    client = app.test_client()
    register(client, username, password)
    return client


def make_plot(client, name="Demo"):
    response = client.post("/api/plots", json={"name": name, "description": "简介"})
    assert response.status_code == 201, response.get_json()
    return response.get_json()["plot"]["id"]


def upload(client, url, fields, files):
    """POST multipart form data.

    ``fields`` may be a dict or a list of pairs; the list form matters because a
    repeated key (one ``mark`` per recorded lyric line) must survive, which is
    exactly what a MultiDict preserves.
    """

    data = MultiDict(fields if not isinstance(fields, dict) else list(fields.items()))
    for name, value in files.items():
        data.add(name, value)
    return client.post(url, data=data, content_type="multipart/form-data")


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------

def test_first_account_becomes_admin(client):
    user = register(client)
    assert user["is_admin"] is True
    assert user["role"] == "admin"


def test_later_accounts_are_plain_users(app):
    register(app.test_client(), "first")
    second = register(app.test_client(), "second")
    assert second["is_admin"] is False


def test_me_reports_setup_state(client):
    assert client.get("/api/auth/me").get_json()["needs_setup"] is True
    register(client)
    payload = client.get("/api/auth/me").get_json()
    assert payload["needs_setup"] is False
    assert payload["user"]["username"] == "alice"


def test_registration_validates_input(client):
    bad = client.post("/api/auth/register", json={"username": "ab", "password": "secret1"})
    assert bad.status_code == 400
    short = client.post("/api/auth/register", json={"username": "alice", "password": "123"})
    assert short.status_code == 400
    weird = client.post("/api/auth/register", json={"username": "a b", "password": "secret1"})
    assert weird.status_code == 400


def test_duplicate_username_is_rejected(app):
    register(app.test_client(), "alice")
    again = app.test_client().post(
        "/api/auth/register", json={"username": "alice", "password": "another1"}
    )
    assert again.status_code == 400


def test_login_logout_cycle(app):
    register(app.test_client(), "alice")
    fresh = app.test_client()
    assert fresh.get("/api/auth/me").get_json()["user"] is None
    assert fresh.post(
        "/api/auth/login", json={"username": "alice", "password": "wrong"}
    ).status_code == 401
    assert fresh.post(
        "/api/auth/login", json={"username": "alice", "password": "secret1"}
    ).status_code == 200
    assert fresh.get("/api/auth/me").get_json()["user"]["username"] == "alice"
    fresh.post("/api/auth/logout")
    assert fresh.get("/api/auth/me").get_json()["user"] is None


def test_password_change(client):
    register(client)
    assert client.post(
        "/api/auth/password", json={"current": "nope", "new": "brandnew1"}
    ).status_code == 400
    assert client.post(
        "/api/auth/password", json={"current": "secret1", "new": "brandnew1"}
    ).status_code == 200
    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login", json={"username": "alice", "password": "brandnew1"}
    ).status_code == 200


def test_api_needs_login(client):
    assert client.get("/api/plots").status_code == 401


# --------------------------------------------------------------------------
# plots
# --------------------------------------------------------------------------

def test_create_and_list_plots(client):
    register(client)
    plot_id = make_plot(client, "第一幕")
    payload = client.get("/api/plots").get_json()
    assert [row["id"] for row in payload["plots"]] == [plot_id]
    row = payload["plots"][0]
    assert row["name"] == "第一幕"
    assert row["script_count"] == 0
    assert row["music_count"] == 0
    assert row["bytes"] > 0


def test_plot_detail_carries_description(client):
    register(client)
    plot_id = make_plot(client)
    detail = client.get("/api/plots/" + plot_id).get_json()["detail"]
    assert detail["name"] == "Demo"
    assert detail["description"] == "简介"
    assert detail["music"] == []
    assert detail["scripts"] == []


def test_rename_plot(client):
    register(client)
    plot_id = make_plot(client)
    response = client.patch("/api/plots/" + plot_id, json={"name": "改名了"})
    assert response.status_code == 200
    assert client.get("/api/plots/" + plot_id).get_json()["detail"]["name"] == "改名了"


def test_delete_plot(client):
    register(client)
    plot_id = make_plot(client)
    assert client.delete("/api/plots/" + plot_id).status_code == 200
    assert client.get("/api/plots/" + plot_id).status_code == 404


def test_plots_are_private_to_their_owner(app):
    alice = new_client(app, "alice")
    plot_id = make_plot(alice, "Alice 的剧情")

    bob = new_client(app, "bob")
    assert bob.get("/api/plots").get_json()["plots"] == []
    # Somebody else's plot reads as missing rather than forbidden, so the API
    # cannot be used to enumerate other people's ids.
    assert bob.get("/api/plots/" + plot_id).status_code == 404
    assert bob.delete("/api/plots/" + plot_id).status_code == 404
    assert bob.get("/api/plots/" + plot_id + "/download").status_code == 404


# --------------------------------------------------------------------------
# scripts
# --------------------------------------------------------------------------

SCRIPT = "TSCP 1\nP|iw\nD|f|SGVsbG8=|0.1,0.2,0.3,0.4,0.5\n"


def test_import_read_save_script(client):
    register(client)
    plot_id = make_plot(client)
    response = upload(
        client,
        "/api/plots/%s/scripts" % plot_id,
        {},
        {"file": (io.BytesIO(SCRIPT.encode("utf-8")), "plot.tscp")},
    )
    assert response.status_code == 201, response.get_json()
    assert response.get_json()["member"] == "Scripts/plot.tscp"

    read = client.get("/api/plots/%s/scripts/plot.tscp" % plot_id).get_json()
    assert read["text"] == SCRIPT

    edited = SCRIPT.replace("Hello", "World")
    edited = "TSCP 1\nP|iw\nD|f|V29ybGQ=|0.1,0.2,0.3,0.4,0.5\n"
    assert client.put(
        "/api/plots/%s/scripts/plot.tscp" % plot_id, json={"text": edited}
    ).status_code == 200
    assert client.get(
        "/api/plots/%s/scripts/plot.tscp" % plot_id
    ).get_json()["text"] == edited


def test_saving_a_broken_script_is_rejected(client):
    register(client)
    plot_id = make_plot(client)
    upload(
        client,
        "/api/plots/%s/scripts" % plot_id,
        {},
        {"file": (io.BytesIO(SCRIPT.encode("utf-8")), "plot.tscp")},
    )
    response = client.put(
        "/api/plots/%s/scripts/plot.tscp" % plot_id, json={"text": "rubbish\n"}
    )
    assert response.status_code == 400
    assert client.get(
        "/api/plots/%s/scripts/plot.tscp" % plot_id
    ).get_json()["text"] == SCRIPT


def test_import_compiles_a_source_script(client):
    register(client)
    plot_id = make_plot(client)
    response = upload(
        client,
        "/api/plots/%s/scripts" % plot_id,
        {},
        {"file": (io.BytesIO("P|iw\n[f]你好\n".encode("utf-8")), "plot.tscps")},
    )
    assert response.status_code == 201
    text = client.get("/api/plots/%s/scripts/plot.tscp" % plot_id).get_json()["text"]
    assert text.startswith("TSCP 1")


def test_import_rejects_other_files(client):
    register(client)
    plot_id = make_plot(client)
    response = upload(
        client,
        "/api/plots/%s/scripts" % plot_id,
        {},
        {"file": (io.BytesIO(b"hello"), "notes.txt")},
    )
    assert response.status_code == 400


def test_delete_script(client):
    register(client)
    plot_id = make_plot(client)
    upload(
        client,
        "/api/plots/%s/scripts" % plot_id,
        {},
        {"file": (io.BytesIO(SCRIPT.encode("utf-8")), "plot.tscp")},
    )
    assert client.delete("/api/plots/%s/scripts/plot.tscp" % plot_id).status_code == 200
    assert client.get("/api/plots/" + plot_id).get_json()["detail"]["scripts"] == []


# --------------------------------------------------------------------------
# music and lyrics
# --------------------------------------------------------------------------

def _song():
    return io.BytesIO(b"FLAC" * 64)


def test_insert_instrumental_music(client):
    register(client)
    plot_id = make_plot(client)
    response = upload(
        client,
        "/api/plots/%s/music" % plot_id,
        {"abbreviation": "iw", "kind": "instrumental"},
        {"file": (_song(), "i want.flac")},
    )
    assert response.status_code == 201, response.get_json()
    tracks = response.get_json()["detail"]["music"]
    assert tracks[0]["abbreviation"] == "iw"
    assert tracks[0]["kind"] == "instrumental"
    assert tracks[0]["has_lyrics"] is False
    assert tracks[0]["size"] == 256


def test_insert_music_with_a_lrc(client):
    register(client)
    plot_id = make_plot(client)
    lrc = "[00:01.00]第一句\n[00:03.50]Second line\n".encode("utf-8")
    response = upload(
        client,
        "/api/plots/%s/music" % plot_id,
        {"abbreviation": "iw", "kind": "lyrics", "color": "#ffd166"},
        {"file": (_song(), "song.flac"), "lyrics": (io.BytesIO(lrc), "song.lrc")},
    )
    assert response.status_code == 201, response.get_json()
    track = response.get_json()["detail"]["music"][0]
    assert track["kind"] == "lyrics"
    assert track["has_lyrics"] is True
    assert track["lyrics"] == "song.lrc"
    assert track["color"] == "#ffd166"

    lyrics = client.get("/api/plots/%s/lyrics/iw" % plot_id).get_json()["lrc"]
    assert "第一句" in lyrics and "Second line" in lyrics


def test_recorded_lyric_times_become_lrc(client):
    register(client)
    plot_id = make_plot(client)
    response = upload(
        client,
        "/api/plots/%s/music" % plot_id,
        [
            ("abbreviation", "iw"),
            ("kind", "lyrics"),
            ("lyrics_text", "甲\n乙\n"),
            ("mark", "0.0"),
            ("mark", "2.5"),
        ],
        {"file": (_song(), "song.flac")},
    )
    assert response.status_code == 201, response.get_json()
    lyrics = client.get("/api/plots/%s/lyrics/iw" % plot_id).get_json()["lrc"]
    assert "[00:00.00]甲" in lyrics
    assert "[00:02.50]乙" in lyrics


def test_lyrics_track_needs_some_lyrics(client):
    register(client)
    plot_id = make_plot(client)
    response = upload(
        client,
        "/api/plots/%s/music" % plot_id,
        {"abbreviation": "iw", "kind": "lyrics"},
        {"file": (_song(), "song.flac")},
    )
    assert response.status_code == 400


def test_audio_is_streamed_with_range_support(client):
    register(client)
    plot_id = make_plot(client)
    upload(
        client,
        "/api/plots/%s/music" % plot_id,
        {"abbreviation": "iw", "kind": "instrumental"},
        {"file": (_song(), "song.flac")},
    )
    whole = client.get("/api/plots/%s/audio/iw" % plot_id)
    assert whole.status_code == 200
    assert whole.data == b"FLAC" * 64

    # Browsers seek by asking for a byte range; Flask answers with 206.
    partial = client.get(
        "/api/plots/%s/audio/iw" % plot_id, headers={"Range": "bytes=0-3"}
    )
    assert partial.status_code == 206
    assert partial.data == b"FLAC"


def test_update_and_delete_music(client):
    register(client)
    plot_id = make_plot(client)
    upload(
        client,
        "/api/plots/%s/music" % plot_id,
        {"abbreviation": "iw", "kind": "instrumental"},
        {"file": (_song(), "song.flac")},
    )
    response = client.patch(
        "/api/plots/%s/music/iw" % plot_id,
        json={"kind": "lyrics", "lyrics_text": "[00:00.00]甲\n", "color": "#00ff00"},
    )
    assert response.status_code == 200, response.get_json()
    track = response.get_json()["detail"]["music"][0]
    assert track["has_lyrics"] is True
    assert track["color"] == "#00ff00"

    response = client.delete("/api/plots/%s/music/iw" % plot_id)
    assert response.status_code == 200
    assert response.get_json()["detail"]["music"] == []


def test_download_round_trips_through_the_desktop_loader(client, tmp_path):
    register(client)
    plot_id = make_plot(client)
    upload(
        client,
        "/api/plots/%s/scripts" % plot_id,
        {},
        {"file": (io.BytesIO(SCRIPT.encode("utf-8")), "plot.tscp")},
    )
    upload(
        client,
        "/api/plots/%s/music" % plot_id,
        {"abbreviation": "iw", "kind": "lyrics", "color": "#abcdef"},
        {
            "file": (_song(), "song.flac"),
            "lyrics": (io.BytesIO("[00:00.00]甲\n".encode("utf-8")), "song.lrc"),
        },
    )

    response = client.get("/api/plots/%s/download" % plot_id)
    assert response.status_code == 200
    target = tmp_path / "downloaded.tscpkg"
    target.write_bytes(response.data)
    assert response.data[:2] == b"PK"

    package = load_archive_package(target, cache_root=tmp_path / "cache")
    assert package.name == "Demo"
    assert package.script_names() == ["plot.tscp"]
    track = package.track("iw")
    assert track.kind == "lyrics"
    assert track.color == "#abcdef"
    assert [line.text for line in package.lyrics("iw").lines] == ["甲"]
    assert package.music_path("iw").read_bytes() == b"FLAC" * 64


# --------------------------------------------------------------------------
# admin
# --------------------------------------------------------------------------

def test_admin_overview_and_user_list(app):
    admin = new_client(app, "admin")
    new_client(app, "bob")
    overview = admin.get("/api/admin/overview").get_json()
    assert overview["users"] == 2
    assert overview["admins"] == 1
    users = admin.get("/api/admin/users").get_json()["users"]
    assert [user["username"] for user in users] == ["admin", "bob"]
    assert users[1]["plot_count"] == 0


def test_admin_can_create_and_edit_users(app):
    admin = new_client(app, "admin")
    created = admin.post(
        "/api/admin/users",
        json={"username": "carol", "password": "secret1", "role": "admin"},
    )
    assert created.status_code == 201, created.get_json()
    user_id = created.get_json()["user"]["id"]
    assert created.get_json()["user"]["is_admin"] is True

    updated = admin.patch(
        "/api/admin/users/%d" % user_id, json={"display_name": "Carol"}
    )
    assert updated.get_json()["user"]["display_name"] == "Carol"

    assert admin.patch(
        "/api/admin/users/%d" % user_id, json={"is_active": False}
    ).status_code == 200
    blocked = app.test_client()
    assert blocked.post(
        "/api/auth/login", json={"username": "carol", "password": "secret1"}
    ).status_code == 403


def test_admin_cannot_lock_itself_or_the_last_admin(app):
    admin = new_client(app, "admin")
    me = admin.get("/api/auth/me").get_json()["user"]
    assert admin.patch(
        "/api/admin/users/%d" % me["id"], json={"is_active": False}
    ).status_code == 400
    assert admin.patch(
        "/api/admin/users/%d" % me["id"], json={"role": "user"}
    ).status_code == 400
    assert admin.delete("/api/admin/users/%d" % me["id"]).status_code == 400


def test_admin_deleting_a_user_removes_their_plots(app):
    admin = new_client(app, "admin")
    bob = new_client(app, "bob")
    plot_id = make_plot(bob)
    bob_id = bob.get("/api/auth/me").get_json()["user"]["id"]

    assert sorted(row["id"] for row in admin.get("/api/admin/plots").get_json()["plots"]) == [plot_id]
    assert admin.delete("/api/admin/users/%d" % bob_id).status_code == 200
    assert admin.get("/api/admin/plots").get_json()["plots"] == []
    assert admin.get("/api/admin/plots/" + plot_id).status_code == 404


def test_plain_users_cannot_reach_the_admin_api(app):
    new_client(app, "admin")
    bob = new_client(app, "bob")
    assert bob.get("/api/admin/users").status_code == 403
    assert bob.get("/api/admin/plots").status_code == 403


def test_admin_can_inspect_and_delete_any_plot(app):
    admin = new_client(app, "admin")
    bob = new_client(app, "bob")
    plot_id = make_plot(bob, "Bob 的剧情")

    detail = admin.get("/api/admin/plots/" + plot_id).get_json()
    assert detail["detail"]["name"] == "Bob 的剧情"
    # An admin can also open it through the normal API.
    assert admin.get("/api/plots/" + plot_id).status_code == 200
    assert admin.delete("/api/admin/plots/" + plot_id).status_code == 200
    assert bob.get("/api/plots").get_json()["plots"] == []


# --------------------------------------------------------------------------
# pages
# --------------------------------------------------------------------------

def test_pages_render(client, app):
    assert b"TSCP" in client.get("/").data
    register(client)
    assert b"TSCP" in client.get("/editor").data
    assert b"TSCP" in client.get("/play").data
    assert b"TSCP" in client.get("/admin").data


def test_editor_redirects_anonymous_visitors_to_the_sign_in_page(app):
    anonymous = app.test_client()
    response = anonymous.get("/editor")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")


def test_admin_page_is_hidden_from_plain_users(app):
    new_client(app, "admin")
    bob = new_client(app, "bob")
    assert bob.get("/admin").status_code == 302


def test_unknown_api_returns_json(client):
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.get_json()["error"]


def test_create_app_without_a_config_object_keeps_its_defaults(tmp_path):
    # ``from_object(None)`` loads nothing, so the factory has to fall back to the
    # shipped Config or every setting (including SECRET_KEY) goes missing.
    app = create_app(None, DATA_DIR=tmp_path / "data")
    assert app.config["SECRET_KEY"]
    assert app.config["MAX_CONTENT_LENGTH"] > 0
    assert app.config["DATA_DIR"] == tmp_path / "data"
    assert app.test_client().get("/").status_code == 200


# --------------------------------------------------------------------------
# deployment defaults (Ubuntu server, port 8888)
# --------------------------------------------------------------------------

def test_the_shipped_default_listens_on_8888():
    """Pin the documented default so a stray edit cannot quietly move it."""

    if os.environ.get("TSCP_PORT") or os.environ.get("TSCP_HOST"):
        pytest.skip("TSCP_PORT/TSCP_HOST is set in this environment")
    from webapp import config as config_module

    assert config_module.Config.PORT == 8888
    assert config_module.Config.HOST == "0.0.0.0"


def test_dev_runner_passes_the_configured_host_and_port(monkeypatch):
    from webapp import app as runner

    seen = {}

    class FakeApp:
        def run(self, host=None, port=None, debug=None):
            seen.update(host=host, port=port)

    monkeypatch.setattr(runner, "create_app", lambda *args, **kwargs: FakeApp())
    monkeypatch.setattr(runner.Config, "HOST", "0.0.0.0", raising=False)
    monkeypatch.setattr(runner.Config, "PORT", 8888, raising=False)
    assert runner.main([]) == 0
    assert seen == {"host": "0.0.0.0", "port": 8888}


def test_wal_mode_keeps_concurrent_workers_happy(tmp_path):
    """Several gunicorn workers share one SQLite file, so WAL is not optional."""

    from webapp import db as db_module

    path = db_module.init_db(tmp_path / "data")
    connection = db_module.open_db(path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


NO_DESKTOP_DEPS = r'''
import sys
from importlib.abc import MetaPathFinder

BLOCKED = {"pygame", "PySide6", "shiboken6", "pyside6"}


class Blocker(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED:
            raise ImportError("not installed on this headless server: " + fullname)
        return None


sys.meta_path.insert(0, Blocker())

from webapp import create_app

app = create_app(DATA_DIR=__DATA_DIR__)
client = app.test_client()
assert client.get("/").status_code == 200
created = client.post(
    "/api/auth/register", json={"username": "smoke", "password": "secret1"}
)
assert created.status_code == 201, created.get_data(as_text=True)
plot = client.post("/api/plots", json={"name": "Smoke"})
assert plot.status_code == 201, plot.get_data(as_text=True)
print("OK")
'''


def test_webapp_runs_without_the_desktop_dependencies(tmp_path):
    """A headless Ubuntu box must not need PySide6 or pygame to serve the site."""

    # Plain token replacement: the script is full of braces that ``format`` would
    # try to interpret as fields.
    script = NO_DESKTOP_DEPS.replace("__DATA_DIR__", repr(str(tmp_path / "data")))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_the_webapp_does_not_pull_in_qt_or_audio():
    script = (
        "import sys\n"
        "from webapp import create_app\n"
        "heavy = sorted("
        "n for n in sys.modules if n.split('.')[0] in "
        "{'pygame', 'PySide6', 'shiboken6'})\n"
        "print('HEAVY:' + ','.join(heavy))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    assert "HEAVY:\n" in result.stdout, result.stdout
