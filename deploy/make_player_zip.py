"""Build the giveaway zip: a frozen player plus one or more story packages.

    python deploy/make_player_zip.py source/ExamplePlot.tscpkg -o out/

Produces ``TSCP剧情播放器.zip``. Unzipped, it is a folder holding the player and
a ``source/`` directory with the plots in it -- the player looks for ``source/``
next to its own .exe, so this is all a Windows user needs. No Python, no PySide6,
no configuration.

Requires PyInstaller: ``pip install pyinstaller``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NAME = "TSCP剧情播放器"
ARCHIVE = "TSCP剧情播放器.zip"

READ_ME = """TSCP 剧情播放器
============================================================

双击「{name}.exe」即可播放，**不需要安装 Python 或任何环境**。


怎么用
------

1. 把整个文件夹解压到任意位置（不要只把 exe 拖出来，
   旁边的 _internal 文件夹是程序的一部分）
2. 双击「{name}.exe」
3. 开局页面会列出 source 文件夹里的剧情包，选一个开始


想换成别的剧情
--------------

把 .tscpkg 文件放进 source 文件夹即可，重新打开就会出现。
也可以直接把 .tscpkg 拖到 exe 上打开。


操作
----

    鼠标左键 / 空格 / Enter     继续
    Esc                        退出
    拖动歌词窗                  调整位置


常见问题
--------

* **打不开 / 闪退**：先看有没有被杀毒软件拦下，把整个文件夹加入白名单
* **没有声音**：本播放器用 SDL 播放音频，请确认系统音量与默认输出设备
* **歌词被别的窗口挡住**：歌词窗会每秒自动置顶一次，若仍被遮挡可点一下它
* **移动了文件夹**：请整体移动，source 文件夹要和 exe 在一起
"""


def build_player(*, clean: bool = True) -> Path:
    """Run PyInstaller and return the folder holding the built player."""

    dist = ROOT / "build" / "player-dist"
    work = ROOT / "build" / "player-work"
    if clean:
        shutil.rmtree(dist, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    dist.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable, "-m", "PyInstaller",
        str(ROOT / "deploy" / "player.spec"),
        "--noconfirm",
        "--distpath", str(dist),
        "--workpath", str(work),
        "--log-level", "WARN",
    ]
    print("running:", " ".join(command))
    result = subprocess.run(command, cwd=str(ROOT))
    if result.returncode != 0:
        raise SystemExit("PyInstaller failed (exit %d)" % result.returncode)

    folder = dist / DEFAULT_NAME
    if not folder.is_dir():
        raise SystemExit("expected %s to exist after the build" % folder)
    return folder


def stage(folder: Path, plots, *, out: Path) -> Path:
    """Copy the player and the plots into one folder ready to be zipped."""

    staging = out / DEFAULT_NAME
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(folder, staging)

    source = staging / "source"
    source.mkdir(parents=True, exist_ok=True)
    for plot in plots:
        target = source / plot.name
        if plot.is_dir():
            shutil.copytree(plot, target)
        else:
            shutil.copyfile(plot, target)

    (staging / "使用说明.txt").write_text(
        READ_ME.format(name=DEFAULT_NAME), encoding="utf-8"
    )
    return staging


def zip_folder(folder: Path, target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder.parent).as_posix())
    return target


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="打包 Windows 免安装播放器")
    parser.add_argument("plots", nargs="*", help="要一起打包的 .tscpkg 或剧情目录")
    parser.add_argument("-o", "--output", default=str(ROOT / "dist"))
    parser.add_argument("--into-source", action="store_true",
                        help="把传入的目录当成 source，打包它下面所有 .tscpkg")
    parser.add_argument("--skip-build", action="store_true",
                        help="复用上一次的构建结果")
    parser.add_argument("--no-zip", action="store_true", help="只准备文件夹")
    args = parser.parse_args(list(argv) if argv is not None else None)

    out = Path(args.output)
    plots = [Path(item) for item in args.plots]
    if args.into_source:
        expanded = []
        for item in plots:
            expanded.extend(sorted(item.glob("*.tscpkg")))
            expanded.extend(sorted(p for p in item.iterdir() if p.is_dir()))
        plots = expanded
    for plot in plots:
        if not plot.exists():
            raise SystemExit("找不到：%s" % plot)

    folder = (out / "player" / DEFAULT_NAME) if args.skip_build else build_player()
    if args.skip_build and not folder.is_dir():
        raise SystemExit("没有可复用的构建结果：%s" % folder)

    staging = stage(folder, plots, out=out)
    print("staged:", staging)
    print("plots :", [p.name for p in plots] or "（没有剧情包）")

    if args.no_zip:
        return 0
    archive = zip_folder(staging, out / ARCHIVE)
    size = archive.stat().st_size / (1024 * 1024)
    print("wrote %s (%.1f MB)" % (archive, size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
