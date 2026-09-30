"""Build the upload-and-deploy ZIP.

    python deploy/make_release.py

Produces ``tscp-web-deploy.zip`` in the repository root.  It contains **only
what a server needs**: the three packages the site imports (verified by
``tests/test_webapp.py::test_webapp_runs_without_the_desktop_dependencies``),
the deployment files, and the licence.  The desktop tools are left out because
they need PySide6 and cannot run on a headless box anyway, and the 94 MB sample
plot under ``source/`` is demo material that would be the entire archive.

Shell scripts are stored with their executable bit set, and the entries are
marked as coming from Unix -- without both of those, `unzip` on Linux produces a
``install-ubuntu.sh`` that answers "Permission denied" to ``./install-ubuntu.sh``.
"""

from __future__ import annotations

import argparse
import stat
import time
import zipfile
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parent.parent

#: The three packages the web app imports, plus the deployment material.
INCLUDE_DIRS = ("webapp", "PlotManager", "tscp_player", "deploy")
INCLUDE_FILES = (
    "requirements-web.txt",
    "pyproject.toml",
    "README.md",
    "LICENSE",
)
#: Skipped wherever they appear inside an included directory.
SKIP_DIRS = {"__pycache__", ".pytest_cache", "data", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pyo", ".pyd", ".tscpkg", ".zip"}

ARCHIVE_PREFIX = "tscp-web"
DEFAULT_NAME = "tscp-web-deploy.zip"
DEPLOY_TEXT_NAME = "DEPLOY.txt"

_WINDOWS = 0
_UNIX = 3


def executable(member: str) -> bool:
    return member.endswith(".sh")


def collect() -> List[Path]:
    """Every file that belongs in the archive, in a stable order."""

    found: List[Path] = []
    for name in INCLUDE_DIRS:
        folder = ROOT / name
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if any(part in SKIP_DIRS for part in relative.parts):
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            found.append(path)
    for name in INCLUDE_FILES:
        candidate = ROOT / name
        if candidate.is_file():
            found.append(candidate)
    return found


def deploy_text(archive: str, prefix: str) -> str:
    return """\
TSCP 剧情工坊 · 服务器部署
============================================================

这个包里是**网页版**，不需要 git，也不需要 PySide6 / pygame。
桌面工具（剧情工坊、播放器）不在包里 —— 它们在服务器上跑不起来。

包含的目录：
  webapp/       网站本身
  PlotManager/  剧情包读写
  tscp_player/  剧本格式、音乐、歌词
  deploy/       部署脚本与说明


一、上传并解压

    unzip {archive}
    cd {prefix}


二、一条命令部署

    sudo ./deploy/install-ubuntu.sh

默认监听 8888，开机自启。脚本会自己建系统用户、虚拟环境、
数据目录和 systemd 服务，并生成随机密钥。


三、访问

    http://<服务器地址>:8888

第一个注册的账号会自动成为管理员。


关于已经占用 8080 的那个 Flask

  不冲突。这个服务用 8888，不会碰你的 8080。
  如果 8888 也被占用，脚本会在启动前直接报出来。换端口：

    sudo PORT=9999 ./deploy/install-ubuntu.sh


打不开怎么办

    sudo ufw allow 8888/tcp       # 本机防火墙
    云控制台的安全组也要放行       # 这一条最容易漏

    sudo systemctl status tscp-web
    sudo journalctl -u tscp-web -n 50 --no-pager


常用命令

    sudo systemctl status tscp-web      看状态
    sudo systemctl restart tscp-web     重启（改完代码必须重启）
    sudo systemctl stop tscp-web        停止
    sudo journalctl -u tscp-web -f      实时日志


详细说明（nginx 反代、更新、备份、排错表）见 deploy/README.md
""".format(archive=archive, prefix=prefix)


def build(target: Path, prefix: str = ARCHIVE_PREFIX) -> Path:
    """Write the archive and return its path."""

    files = collect()
    if not files:
        raise SystemExit("nothing to archive -- is this the repository root?")

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        # A short read-me first, so it is the obvious thing to open.
        readme = zipfile.ZipInfo(
            "%s/%s" % (prefix, DEPLOY_TEXT_NAME), time.localtime()[:6]
        )
        readme.compress_type = zipfile.ZIP_DEFLATED
        readme.create_system = _UNIX
        readme.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(readme, deploy_text(target.name, prefix))

        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            member = "%s/%s" % (prefix, relative)
            info = zipfile.ZipInfo(member, time.localtime(path.stat().st_mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            # Without create_system=Unix and a mode in the high bits, Linux
            # unzip writes every file as 0644 and the install script is not
            # runnable.
            info.create_system = _UNIX
            mode = 0o755 if executable(relative) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, path.read_bytes())
    return target


def report(target: Path, prefix: str) -> None:
    with zipfile.ZipFile(target) as archive:
        infos = archive.infolist()
    unpacked = sum(info.file_size for info in infos)
    scripts = [info for info in infos if executable(info.filename)]

    print("wrote %s" % target)
    print(
        "  %.2f MB on disk, %.2f MB unpacked, %d files"
        % (target.stat().st_size / (1024 * 1024), unpacked / (1024 * 1024), len(infos))
    )
    print("  top-level directory: %s/" % prefix)
    for info in scripts:
        mode = (info.external_attr >> 16) & 0o777
        print("  %s  mode %s" % (info.filename, oct(mode)))
    tops = sorted({i.filename.split("/")[1] for i in infos if i.filename.count("/") >= 1})
    print("  contains: %s" % ", ".join(tops))


def main(argv: Iterable[str] = None) -> int:
    parser = argparse.ArgumentParser(description="生成可上传的部署 zip")
    parser.add_argument(
        "target", nargs="?", default=str(ROOT / DEFAULT_NAME),
        help="输出路径，默认 %s" % DEFAULT_NAME,
    )
    parser.add_argument("--prefix", default=ARCHIVE_PREFIX, help="解压后的目录名")
    args = parser.parse_args(list(argv) if argv is not None else None)

    target = Path(args.target)
    if target.exists():
        target.unlink()          # never ship a stale archive

    build(target, args.prefix)
    report(target, args.prefix)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
