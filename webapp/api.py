"""The authoring API: plots, scripts, music, lyrics and downloads.

Every operation goes through :mod:`webapp.storage`, which in turn calls the same
``PlotManager.model`` helpers the desktop tools use, so a package built in the
browser is identical to one built locally.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from flask import Blueprint, current_app, jsonify, request, send_file

from PlotManager.model import MusicDraft
from tscp_player.lyrics import lyric_source_lines, serialize_lrc, timed_from_marks
from tscp_player.plot import KIND_INSTRUMENTAL, KIND_LYRICS

from . import db, storage
from .auth import admin_required, current_user, login_required

bp = Blueprint("api", __name__, url_prefix="/api")

AUDIO_SUFFIXES = {".flac", ".mp3", ".ogg", ".oga", ".opus", ".wav", ".m4a", ".aac"}


class ApiError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def data_dir() -> Path:
    return Path(current_app.config["DATA_DIR"])


def owned_plot(plot_id: str) -> Dict[str, Any]:
    """The plot row, if the caller may touch it.

    Administrators can reach every plot; a normal user only their own.  A plot
    that exists but belongs to somebody else reads as "not found" so the API
    cannot be used to enumerate other people's work.
    """

    row = db.get_plot(db.get_db(), plot_id)
    user = current_user()
    if row is None or (row["owner_id"] != user["id"] and not user["is_admin"]):
        raise ApiError("剧情包不存在", 404)
    return row


def _json_error(exc: Exception, status: int = 400):
    return jsonify({"error": str(exc)}), status


# --------------------------------------------------------------------------
# plots
# --------------------------------------------------------------------------

@bp.get("/plots")
@login_required
def list_plots():
    user = current_user()
    connection = db.get_db()
    if user["is_admin"] and request.args.get("all") == "1":
        rows = db.list_plots(connection)
    else:
        rows = db.list_plots(connection, owner_id=user["id"])
    for row in rows:
        _decorate(row)
    return jsonify({"plots": rows})


def _decorate(row: Dict[str, Any]) -> Dict[str, Any]:
    """Add the counts and size the list view shows.

    A package that cannot be opened still appears, with ``null`` counts, so one
    broken file never hides the rest of somebody's work.
    """

    path = storage.plot_path(data_dir(), row["id"])
    row["bytes"] = path.stat().st_size if path.is_file() else 0
    row["script_count"] = None
    row["music_count"] = None
    try:
        detail = storage.describe(storage.load(data_dir(), row["id"]))
        row["script_count"] = len(detail["scripts"])
        row["music_count"] = len(detail["music"])
    except (storage.StorageError, OSError, ValueError):
        pass
    return row


@bp.post("/plots")
@login_required
def create_plot():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip() or "未命名剧情"
    description = str(payload.get("description", "")).strip()
    user = current_user()
    row = db.create_plot_row(db.get_db(), user["id"], name)
    try:
        storage.create_plot_file(data_dir(), row["id"], name=name, description=description)
    except storage.StorageError as exc:
        db.delete_plot_row(db.get_db(), row["id"])
        return _json_error(exc, 500)
    return jsonify({"plot": row, "detail": storage.describe(storage.load(data_dir(), row["id"]))}), 201


@bp.get("/plots/<plot_id>")
@login_required
def get_plot(plot_id: str):
    row = owned_plot(plot_id)
    try:
        package = storage.load(data_dir(), plot_id)
    except storage.StorageError as exc:
        return _json_error(exc, 500)
    return jsonify({"plot": row, "detail": storage.describe(package)})


@bp.patch("/plots/<plot_id>")
@login_required
def update_plot(plot_id: str):
    row = owned_plot(plot_id)
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    description = payload.get("description")
    name = str(name).strip() if name is not None else None
    description = str(description).strip() if description is not None else None
    if name == "":
        return _json_error(ApiError("剧情名称不能为空"))
    try:
        storage.update_metadata(data_dir(), plot_id, name=name, description=description)
    except storage.StorageError as exc:
        return _json_error(exc)
    if name is not None:
        db.rename_plot(db.get_db(), plot_id, name)
    else:
        db.touch_plot(db.get_db(), plot_id)
    return jsonify({"plot": db.get_plot(db.get_db(), plot_id)})


@bp.delete("/plots/<plot_id>")
@login_required
def delete_plot(plot_id: str):
    owned_plot(plot_id)
    try:
        storage.delete_plot_file(data_dir(), plot_id)
    except storage.StorageError as exc:
        return _json_error(exc, 500)
    db.delete_plot_row(db.get_db(), plot_id)
    return jsonify({"ok": True})


@bp.get("/plots/<plot_id>/download")
@login_required
def download_plot(plot_id: str):
    row = owned_plot(plot_id)
    path = storage.plot_path(data_dir(), plot_id)
    if not path.is_file():
        return _json_error(ApiError("剧情包文件不存在", 404))
    db.touch_plot(db.get_db(), plot_id)
    return send_file(
        path,
        as_attachment=True,
        download_name="%s.tscpkg" % (row["name"] or plot_id),
        mimetype="application/zip",
    )


# --------------------------------------------------------------------------
# scripts
# --------------------------------------------------------------------------

@bp.get("/plots/<plot_id>/scripts/<path:filename>")
@login_required
def read_script(plot_id: str, filename: str):
    owned_plot(plot_id)
    try:
        text = storage.script_text(data_dir(), plot_id, filename)
    except storage.StorageError as exc:
        return _json_error(exc, 404)
    return jsonify({"filename": filename, "text": text})


@bp.put("/plots/<plot_id>/scripts/<path:filename>")
@login_required
def write_script(plot_id: str, filename: str):
    owned_plot(plot_id)
    payload = request.get_json(silent=True) or {}
    text = payload.get("text")
    if not isinstance(text, str):
        return _json_error(ApiError("缺少 text 字段"))
    try:
        storage.save_script(data_dir(), plot_id, filename, text)
    except storage.StorageError as exc:
        return _json_error(exc)
    db.touch_plot(db.get_db(), plot_id)
    return jsonify({"ok": True})


@bp.post("/plots/<plot_id>/scripts")
@login_required
def import_script(plot_id: str):
    """Import a ``.tscp`` as-is, or compile a ``.tscps`` source on the way in."""

    owned_plot(plot_id)
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _json_error(ApiError("请选择要导入的剧本文件"))
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in {".tscp", ".tscps"}:
        return _json_error(ApiError("只接受 .tscp 或 .tscps 文件"))
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / Path(upload.filename).name
        upload.save(target)
        try:
            member = storage.import_script(
                data_dir(), plot_id, target, compile_source=suffix == ".tscps"
            )
        except storage.StorageError as exc:
            return _json_error(exc)
    db.touch_plot(db.get_db(), plot_id)
    return jsonify({"member": member}), 201


@bp.delete("/plots/<plot_id>/scripts/<path:filename>")
@login_required
def delete_script(plot_id: str, filename: str):
    owned_plot(plot_id)
    try:
        storage.delete_script(data_dir(), plot_id, filename)
    except storage.StorageError as exc:
        return _json_error(exc, 404)
    db.touch_plot(db.get_db(), plot_id)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# music
# --------------------------------------------------------------------------

@bp.post("/plots/<plot_id>/music")
@login_required
def add_music(plot_id: str):
    """Upload one track, plus either a finished ``.lrc`` or recorded timings."""

    owned_plot(plot_id)
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _json_error(ApiError("请选择要插入的音乐文件"))
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in AUDIO_SUFFIXES:
        return _json_error(ApiError("不支持的音频格式：%s" % suffix))
    abbreviation = str(request.form.get("abbreviation", "")).strip()
    if not abbreviation:
        return _json_error(ApiError("音乐简称不能为空"))
    kind = str(request.form.get("kind") or KIND_INSTRUMENTAL).strip().lower()
    if kind not in {KIND_INSTRUMENTAL, KIND_LYRICS}:
        return _json_error(ApiError("未知的音乐类型：%s" % kind))
    color = str(request.form.get("color") or "").strip()

    lyrics_text: Optional[str] = None
    lyrics_name: Optional[str] = None
    if kind == KIND_LYRICS:
        lyrics_upload = request.files.get("lyrics")
        if lyrics_upload is not None and lyrics_upload.filename:
            lyrics_text = lyrics_upload.read().decode("utf-8", errors="replace")
            lyrics_name = Path(lyrics_upload.filename).name
        else:
            raw = request.form.get("lyrics_text")
            if not raw:
                return _json_error(ApiError("带歌词的音乐需要 .lrc 文件或歌词内容"))
            lyrics_text = raw
            lyrics_name = "%s.lrc" % abbreviation
        # A plain lyrics.txt becomes LRC using the times recorded in the browser.
        if not lyrics_text.lstrip().startswith("["):
            lines = lyric_source_lines(lyrics_text)
            marks = [float(value) for value in request.form.getlist("mark")]
            lyrics_text = serialize_lrc(timed_from_marks(lines, marks))

    draft = MusicDraft(
        abbreviation=abbreviation,
        kind=kind,
        lyrics_text=lyrics_text,
        lyrics_name=lyrics_name or ("%s.lrc" % abbreviation),
        color=color,
    )
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / Path(upload.filename).name
        upload.save(target)
        draft.source = target
        try:
            storage.add_music(data_dir(), plot_id, [draft])
        except storage.StorageError as exc:
            return _json_error(exc)
    db.touch_plot(db.get_db(), plot_id)
    return jsonify({"detail": storage.describe(storage.load(data_dir(), plot_id))}), 201


@bp.patch("/plots/<plot_id>/music/<abbr>")
@login_required
def update_music(plot_id: str, abbr: str):
    owned_plot(plot_id)
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind")
    color = payload.get("color")
    lyrics_text = payload.get("lyrics_text")
    try:
        storage.update_music(
            data_dir(),
            plot_id,
            abbr,
            kind=str(kind).strip().lower() if kind is not None else None,
            lyrics_text=lyrics_text if isinstance(lyrics_text, str) else None,
            lyrics_name="%s.lrc" % abbr,
            color=str(color) if color is not None else None,
        )
    except storage.StorageError as exc:
        return _json_error(exc)
    db.touch_plot(db.get_db(), plot_id)
    return jsonify({"detail": storage.describe(storage.load(data_dir(), plot_id))})


@bp.delete("/plots/<plot_id>/music/<abbr>")
@login_required
def delete_music(plot_id: str, abbr: str):
    owned_plot(plot_id)
    try:
        storage.delete_music(data_dir(), plot_id, abbr)
    except storage.StorageError as exc:
        return _json_error(exc, 404)
    db.touch_plot(db.get_db(), plot_id)
    return jsonify({"detail": storage.describe(storage.load(data_dir(), plot_id))})


@bp.get("/plots/<plot_id>/audio/<abbr>")
@login_required
def stream_audio(plot_id: str, abbr: str):
    """Stream one track so the browser can play it.

    ``conditional=True`` makes Flask answer Range requests, which is what lets
    the player seek - something the desktop player cannot do with FLAC.
    """

    owned_plot(plot_id)
    try:
        package = storage.load(data_dir(), plot_id)
        path = package.music_path(abbr)
    except (storage.StorageError, OSError, ValueError) as exc:
        return _json_error(exc, 404)
    if not Path(path).is_file():
        return _json_error(ApiError("音频文件缺失", 404))
    return send_file(Path(path), conditional=True)


@bp.get("/plots/<plot_id>/lyrics/<abbr>")
@login_required
def read_lyrics(plot_id: str, abbr: str):
    owned_plot(plot_id)
    try:
        text = storage.lyrics_text(data_dir(), plot_id, abbr)
    except storage.StorageError as exc:
        return _json_error(exc, 404)
    return jsonify({"abbreviation": abbr, "lrc": text})
