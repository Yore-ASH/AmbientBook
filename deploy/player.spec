# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone story player.

    pyinstaller deploy/player.spec --noconfirm

--onedir rather than --onefile on purpose: onefile unpacks the whole Qt runtime
into a temp folder on every launch, which is slow and makes the "source/" folder
next to the .exe ambiguous. Onedir starts immediately and keeps the layout the
player expects.

The studio, the web app and the build tooling are excluded: a player has no use
for any of them, and leaving them in roughly doubles the size.
"""

from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

a = Analysis(
    [str(ROOT / "Main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Nothing here is reachable from the player, and some of it would drag in
    # dependencies the player must not need.
    excludes=[
        "Studio",
        "webapp",
        "PlotManager.recorder",
        "Ts2Tp",
        "TSCPEditor",
        "CharacterCreator",
        "PlotWriter",
        "TSGenerator",
        "Editor",
        "flask",
        "werkzeug",
        "jinja2",
        "PySide6.QtWebEngineCore",
        "PySide6.QtQuick",
        "PySide6.Qt3DCore",
        "PySide6.QtMultimedia",
        "PySide6.QtNetwork",
        "PySide6.QtQml",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtCharts",
        "tkinter",
        "unittest",
        "pydoc",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TSCP剧情播放器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # a windowed app: no console box behind the player
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TSCP剧情播放器",
)
