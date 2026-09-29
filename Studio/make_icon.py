"""Generate the studio's icon.

    python Studio/make_icon.py

Writes ``Studio/tscp-studio.ico`` (multi-size) and ``Studio/tscp-studio.png``
next to this file.  The drawing is pure geometry — no glyphs — so it does not
depend on which fonts a machine happens to have, and it stays legible at 16 px.

The caret and the third text line use the same amber the editor highlights the
current character with, so the icon matches what the app actually looks like.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from typing import Iterable, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

#: Windows picks whichever of these fits the place it is drawing.
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
MASTER = 256

BACKGROUND = "#14161a"
EDGE = "#333a44"
DIM = "#66707e"
ACCENT = "#f6d365"

ICON_NAME = "tscp-studio.ico"
PREVIEW_NAME = "tscp-studio.png"


def draw(size: int = MASTER) -> QImage:
    """Render the icon at *size* pixels square."""

    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    unit = size / 256.0

    def box(x, y, width, height, colour, radius=0.0):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colour))
        rect = QRectF(x * unit, y * unit, width * unit, height * unit)
        if radius:
            painter.drawRoundedRect(rect, radius * unit, radius * unit)
        else:
            painter.drawRect(rect)

    # The tile.
    box(10, 10, 236, 236, BACKGROUND, radius=54)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QColor(EDGE))
    painter.drawRoundedRect(
        QRectF(10 * unit, 10 * unit, 236 * unit, 236 * unit), 54 * unit, 54 * unit
    )

    # It is a text editor, so lead with the insertion point...
    box(54, 64, 24, 128, ACCENT, radius=11)
    # ...then the lines of a script, the last one lit up like the live character.
    box(100, 68, 104, 26, DIM, radius=13)
    box(100, 116, 136, 26, DIM, radius=13)
    box(100, 164, 88, 26, ACCENT, radius=13)

    painter.end()
    return image


def _png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def write_ico(path: Path, images: Sequence[QImage]) -> Path:
    """Write an ICO whose entries are PNG payloads.

    Vista and later read PNG entries natively, which keeps a nine-size icon
    small instead of storing nine uncompressed bitmaps.
    """

    payloads = [_png_bytes(image) for image in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)

    entries = []
    for image, payload in zip(images, payloads):
        side = image.width()
        # 256 is encoded as 0 in the directory, because the field is one byte.
        entries.append(
            struct.pack(
                "<BBBBHHII",
                0 if side >= 256 else side,
                0 if side >= 256 else side,
                0,                      # palette size: none
                0,                      # reserved
                1,                      # colour planes
                32,                     # bits per pixel
                len(payload),
                offset,
            )
        )
        offset += len(payload)

    path.write_bytes(header + b"".join(entries) + b"".join(payloads))
    return path


def build(folder: Path, sizes: Iterable[int] = SIZES) -> tuple:
    """Render every size and write both files; returns their paths."""

    master = draw(MASTER)
    images = [
        master.scaled(
            side,
            side,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        for side in sizes
    ]
    icon = write_ico(folder / ICON_NAME, images)
    preview = folder / PREVIEW_NAME
    master.save(str(preview))
    return icon, preview


def main(argv=None) -> int:
    if QGuiApplication.instance() is None:
        QGuiApplication(argv if argv is not None else sys.argv[:1])
    folder = Path(__file__).resolve().parent
    icon, preview = build(folder)
    print("wrote %s (%d bytes)" % (icon, icon.stat().st_size))
    print("wrote %s" % preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
