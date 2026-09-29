"""Compact stylesheets for the studio.

The sheet is applied to the :class:`StudioWindow` itself, **never** to the
``QApplication``.  The plot player opened from the studio has no Qt parent, so it
keeps its own carefully tuned look instead of inheriting the editor chrome.
"""

from __future__ import annotations

LIGHT = {
    "window": "#f4f5f7",
    "panel": "#ffffff",
    "field": "#ffffff",
    "line": "#d3d8e0",
    "fg": "#1f2328",
    "dim": "#6b7280",
    "head": "#e9ecf1",
    "headfg": "#3c4450",
    "btn": "#ffffff",
    "btnhover": "#eef1f6",
    "disabledbg": "#f0f1f3",
    "navbg": "#e7eaf0",
    "navfg": "#39404b",
    "navhover": "#dde2ea",
    "accent": "#f6d365",
    "accentfg": "#1f2328",
    "sel": "#dce8ff",
}

DARK = {
    "window": "#14161a",
    "panel": "#1a1d22",
    "field": "#0f1114",
    "line": "#2a2f36",
    "fg": "#d8dde3",
    "dim": "#7d8794",
    "head": "#22262c",
    "headfg": "#aab3bf",
    "btn": "#1f242a",
    "btnhover": "#272d34",
    "disabledbg": "#1a1d21",
    "navbg": "#101216",
    "navfg": "#98a2b0",
    "navhover": "#1c2026",
    "accent": "#f6d365",
    "accentfg": "#14161a",
    "sel": "#2b3a52",
}

#: ``%(name)s`` placeholders are filled from the palette above.
_QSS = """
QWidget { color: %(fg)s; }
QMainWindow, QDialog { background: %(window)s; }

/* --- compact controls ------------------------------------------------- */
QPushButton {
    padding: 4px 12px;
    border: 1px solid %(line)s;
    border-radius: 3px;
    background: %(btn)s;
}
QPushButton:hover:!disabled { background: %(btnhover)s; }
QPushButton:disabled { color: %(dim)s; background: %(disabledbg)s; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {
    padding: 3px 6px;
    border: 1px solid %(line)s;
    border-radius: 3px;
    background: %(field)s;
    selection-background-color: %(sel)s;
}
QComboBox::drop-down { width: 18px; border: none; }
QComboBox QAbstractItemView {
    border: 1px solid %(line)s;
    background: %(panel)s;
    selection-background-color: %(sel)s;
}

/* --- compact tables --------------------------------------------------- */
QTableWidget, QTableView {
    border: 1px solid %(line)s;
    gridline-color: %(line)s;
    background: %(panel)s;
    alternate-background-color: %(window)s;
    selection-background-color: %(sel)s;
}
QTableWidget::item, QTableView::item { padding: 0px 6px; border: none; }
QTableWidget::item:selected, QTableView::item:selected {
    background: %(sel)s;
    color: %(fg)s;
}
QTableWidget::item:focus, QTableView::item:focus { outline: none; }
/* The stretched last section is painted by the header widget itself, not by
   ::section, so the header needs its own background too. */
QHeaderView { background: %(head)s; }
QHeaderView::section {
    padding: 2px 6px;
    border: none;
    border-right: 1px solid %(line)s;
    border-bottom: 1px solid %(line)s;
    background: %(head)s;
    color: %(headfg)s;
}

/* --- the left-hand step rail: deliberately heavier -------------------- */
QListWidget#steps {
    border: none;
    border-right: 1px solid %(line)s;
    background: %(navbg)s;
    outline: none;
}
QListWidget#steps::item {
    padding: 16px 14px;
    margin: 3px 7px;
    border-radius: 5px;
    color: %(navfg)s;
    font-size: 15px;
    font-weight: 700;
}
QListWidget#steps::item:hover:!selected { background: %(navhover)s; }
QListWidget#steps::item:selected { background: %(accent)s; color: %(accentfg)s; }

QLabel#railTitle {
    padding: 12px 14px 8px 14px;
    color: %(dim)s;
    font-size: 12px;
    font-weight: 700;
}

/* --- misc ------------------------------------------------------------- */
QSplitter::handle { background: %(line)s; }
QSplitter::handle:horizontal { width: 3px; }
QLabel#hint { color: %(dim)s; }
QMenuBar, QMenu { background: %(panel)s; }
QMenuBar::item:selected, QMenu::item:selected { background: %(sel)s; }
QMenu { border: 1px solid %(line)s; }
QCheckBox, QRadioButton { spacing: 6px; }
"""


def stylesheet(name: str = "light") -> str:
    """The compact sheet for ``"light"`` or ``"dark"``."""

    palette = DARK if str(name).lower() == "dark" else LIGHT
    return _QSS % palette


def palette(name: str = "light"):
    """A ``QPalette`` matching the sheet.

    A stylesheet alone does not reach every role -- the header and selection
    colours of an item view in particular kept their light defaults, which showed
    up as bright bars in the dark theme.  Setting the palette as well fixes them.
    """

    from PySide6.QtGui import QColor, QPalette

    source = DARK if str(name).lower() == "dark" else LIGHT
    result = QPalette()
    roles = (
        (QPalette.ColorRole.Window, "window"),
        (QPalette.ColorRole.WindowText, "fg"),
        (QPalette.ColorRole.Base, "panel"),
        (QPalette.ColorRole.AlternateBase, "window"),
        (QPalette.ColorRole.Text, "fg"),
        (QPalette.ColorRole.Button, "btn"),
        (QPalette.ColorRole.ButtonText, "fg"),
        (QPalette.ColorRole.Highlight, "sel"),
        (QPalette.ColorRole.HighlightedText, "fg"),
        (QPalette.ColorRole.ToolTipBase, "panel"),
        (QPalette.ColorRole.ToolTipText, "fg"),
        (QPalette.ColorRole.PlaceholderText, "dim"),
    )
    for role, key in roles:
        result.setColor(role, QColor(source[key]))
    return result


def apply(widget, name: str = "light") -> None:
    """Style one window.  Scoped to *widget* so other windows are untouched."""

    # Order matters: the stylesheet is what wins where both apply.
    widget.setPalette(palette(name))
    widget.setStyleSheet(stylesheet(name))


THEMES = (("light", "浅色"), ("dark", "深色"))
