"""Shared test setup.

The GUI tests must never try to open a real window: a headless Ubuntu box (or a
CI runner) has no display, so Qt is pointed at its offscreen platform before any
``QApplication`` is created.

The ``qapp`` and ``messages`` fixtures live here rather than in one test module
because more than one module needs them.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole run; Qt allows only one."""

    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch, qapp):
    """Fail loudly instead of hanging when something pops a modal dialog.

    With no display attached a real ``QMessageBox`` waits for a click that never
    comes, so every prompt is stubbed out.  Only registered when Qt is actually
    installed, so the pure-Python tests stay runnable without it.
    """

    try:
        from PySide6.QtWidgets import QDialog, QMessageBox
    except ImportError:  # pragma: no cover - Qt is optional
        return

    def _ok(*_args, **_kwargs):
        return QMessageBox.StandardButton.Ok

    def _yes(*_args, **_kwargs):
        # "The user agreed" is the useful default; tests that need a refusal
        # stub their own.
        return QMessageBox.StandardButton.Yes

    for name, replacement in (
        ("information", _ok), ("warning", _ok), ("critical", _ok),
        ("question", _yes), ("about", _ok),
    ):
        try:
            monkeypatch.setattr(QMessageBox, name, staticmethod(replacement))
        except (AttributeError, TypeError):  # pragma: no cover - Qt build detail
            pass
    for name in ("exec",):
        try:
            monkeypatch.setattr(
                QMessageBox, name, lambda self: QMessageBox.StandardButton.Ok
            )
        except (AttributeError, TypeError):  # pragma: no cover
            pass
    # A real modal QDialog waits for a click that never comes offscreen, so any
    # dialog a shortcut happens to open is dismissed rather than hanging the run.
    try:
        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    except (AttributeError, TypeError):  # pragma: no cover
        pass


@pytest.fixture
def messages(monkeypatch, qapp, no_modal_dialogs):
    """Records the message boxes the code shows, so tests can assert on them."""

    from PySide6.QtWidgets import QMessageBox

    seen = []

    def _record(name):
        def _handler(*args, **_kwargs):
            seen.append((name, args[2] if len(args) > 2 else ""))
            return QMessageBox.StandardButton.Ok

        return staticmethod(_handler)

    for name in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, name, _record(name))
    return seen
