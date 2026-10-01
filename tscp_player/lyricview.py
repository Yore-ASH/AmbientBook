"""The floating lyric overlay, shared by the player and the studio preview.

Frameless and backgroundless: only the text is drawn, so it floats over whatever
is behind it.  A line with a translation is laid out as two columns -- original
on the left, translation on the right -- with the pair centred as a group, and
either half wraps when the pair is wider than the screen.

The wrapping and column planning live in :mod:`tscp_player.lyrics` and know
nothing about Qt; this module is only the widget around them.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .lyrics import plan_lyric_rows, to_html

try:  # pragma: no cover - depends on the optional GUI package
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics
    from PySide6.QtWidgets import (
        QApplication,
        QGraphicsDropShadowEffect,
        QLabel,
        QVBoxLayout,
        QWidget,
    )

    QT_AVAILABLE = True
except ImportError:  # pragma: no cover
    QT_AVAILABLE = False


#: The pair never gets more than this share of the screen before it wraps.
MAX_SCREEN_SHARE = 0.90
#: Taken off the wrapping limit on top of the share above. Measured text and
#: rendered text are never quite the same width, and the difference has to go
#: somewhere other than past the edge of the window.
WRAP_SLACK = 24
#: Space between the original column and the translation column.
COLUMN_GAP = "　　"
#: Point size of a lyric line.
LYRIC_POINT_SIZE = 26


if QT_AVAILABLE:

    class LyricsWindow(QWidget):
        """A frameless overlay that follows the current lyric line."""

        def __init__(self, parent=None) -> None:
            super().__init__(
                parent,
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool,
            )
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.setWindowTitle("歌词")

            self.label = QLabel(self)
            self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.label.setTextFormat(Qt.TextFormat.RichText)
            self.label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.label.setFont(QFont("", LYRIC_POINT_SIZE))
            self.label.setWordWrap(True)
            # A drop shadow keeps the text readable over any wallpaper without
            # painting a background box behind it.
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(14)
            shadow.setColor(QColor(0, 0, 0, 210))
            shadow.setOffset(0, 2)
            self.label.setGraphicsEffect(shadow)

            layout = QVBoxLayout(self)
            layout.setContentsMargins(28, 14, 28, 14)
            layout.addWidget(self.label)

            self._families = QFontDatabase.families()
            self._lyrics = None
            self._color = "#ffffff"
            self._dragging = None
            self.resize(960, 130)

            # WindowStaysOnTopHint is a request, not a guarantee: on Windows
            # another app taking focus can still end up in front. Re-asserting
            # periodically is what actually keeps the lyrics visible.
            self._top_timer = QTimer(self)
            self._top_timer.setInterval(1000)
            self._top_timer.timeout.connect(self._keep_on_top)

        # -- content -------------------------------------------------------
        def show_track(self, lyrics, color: str = "", seconds: float = 0.0) -> None:
            """Start showing *lyrics* in *color*, positioned once per track."""

            self._lyrics = lyrics
            self._color = color or "#ffffff"
            if not self.isVisible():
                self._place()
                self.show()
            self.raise_()
            self._top_timer.start()
            self.update_line(seconds)

        def update_line(self, seconds: float) -> None:
            if self._lyrics is None:
                return
            line = self._lyrics.line_at(seconds)
            if line is None:
                self._set_html("")
                return
            self._set_html(self._html_for(line))

        def clear(self) -> None:
            self._lyrics = None
            self.label.clear()
            self._top_timer.stop()
            self.resize(960, 130)

        def _keep_on_top(self) -> None:
            """Put the overlay back in front without stealing focus."""

            if not self.isVisible():
                self._top_timer.stop()
                return
            self.raise_()

        # -- rendering -----------------------------------------------------
        def _columns_width(self) -> float:
            """How wide the pair may get before it has to wrap."""

            screen = QApplication.primaryScreen()
            if screen is None:
                return 900.0
            return max(240.0, screen.availableGeometry().width() * MAX_SCREEN_SHARE)

        def _html_for(self, line) -> str:
            colour = line.color or self._color
            translated_colour = line.translation_color or colour
            metrics = QFontMetrics(self.label.font())
            gap = metrics.horizontalAdvance(COLUMN_GAP)
            rows = plan_lyric_rows(
                line.text,
                line.translation,
                metrics.horizontalAdvance,
                max(80.0, self._columns_width() - WRAP_SLACK),
                gap,
            )

            def render_original(text: str) -> str:
                return to_html(text, colour, self._families, family=line.text_font)

            def render_translation(text: str) -> str:
                return to_html(
                    text,
                    translated_colour,
                    self._families,
                    family=line.translated_font,
                )

            if len(rows) == 1 and not rows[0][1]:
                return render_original(rows[0][0])

            left_cell = (
                '<td align="right" valign="middle" '
                'style="word-wrap:break-word;overflow-wrap:break-word;">%s</td>'
            )
            right_cell = (
                '<td align="left" valign="middle" '
                'style="padding-left:%dpx;'
                'word-wrap:break-word;overflow-wrap:break-word;">%s</td>'
            )
            open_table = '<table width="100%" cellspacing="0" cellpadding="0">'
            if len(rows) == 1:
                left, right = rows[0]
                return (
                    open_table
                    + "<tr>"
                    + left_cell % render_original(left)
                    + right_cell % (int(gap), render_translation(right))
                    + "</tr></table>"
                )
            body = "".join(
                "<tr>"
                + left_cell % render_original(left)
                + right_cell % (int(gap), render_translation(right))
                + "</tr>"
                for left, right in rows
            )
            return open_table + body + "</table>"

        def _set_html(self, html: str) -> None:
            self.label.setText(html)
            self._fit()

        def _fit(self) -> None:
            """Grow or shrink the window to whatever the line needs."""

            screen = QApplication.primaryScreen()
            width = int(self._columns_width()) + 56
            if screen is not None:
                width = min(width, screen.availableGeometry().width())
            self.setMaximumWidth(width)
            self.setFixedWidth(width)
            self.label.setFixedWidth(width - 56)
            height = max(130, self.label.sizeHint().height() + 28)
            if screen is not None:
                height = min(height, int(screen.availableGeometry().height() * 0.6))
            self.setFixedHeight(height)

        # -- placement -----------------------------------------------------
        def _place(self) -> None:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            area = screen.availableGeometry()
            self.move(
                area.x() + (area.width() - self.width()) // 2,
                area.y() + int(area.height() * 0.72),
            )

        def mousePressEvent(self, event) -> None:
            if event.button() == Qt.MouseButton.LeftButton:
                self._dragging = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                event.accept()

        def mouseMoveEvent(self, event) -> None:
            if self._dragging is not None:
                self.move(event.globalPosition().toPoint() - self._dragging)
                event.accept()

        def mouseReleaseEvent(self, event) -> None:
            self._dragging = None
            event.accept()
