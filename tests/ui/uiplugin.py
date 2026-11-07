from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QDialog, QFileDialog, QInputDialog, QMessageBox, QWidget

# --- Version ------------------------------------------------------------
__version__ = "uiplugin.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial: the general parts of Chrono Mail's plugin (AGENTS.md 4.4.4): nothing waits for a click, every test cleans up

MODIFIER_KEYS: tuple[Qt.Key, ...] = (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Meta)
_unanswered: list[str] = []
_first_look: list[tuple[str, QPalette]] = []

_PICKERS: tuple[tuple[type[QWidget], str, object], ...] = (
    (QMessageBox, "about", None),  # made and run inside Qt itself, so the stand-in exec never sees them
    (QMessageBox, "aboutQt", None),
    (QInputDialog, "getText", ("", False)),
    (QInputDialog, "getItem", ("", False)),
    (QInputDialog, "getInt", (0, False)),
    (QFileDialog, "getOpenFileName", ("", "")),
    (QFileDialog, "getOpenFileNames", ([], "")),
    (QFileDialog, "getSaveFileName", ("", "")),
    (QFileDialog, "getExistingDirectory", ""),
    (QColorDialog, "getColor", QColor()),
)


def let_go_of_keys() -> None:
    """A key pressed with a modifier through QTest leaves Qt sure that the modifier is still down: every modifier is let go."""
    if QGuiApplication.keyboardModifiers() == Qt.KeyboardModifier.NoModifier:
        return
    stand_in = QWidget()  # never shown: it takes no window, focus or activation from the next test
    for key in MODIFIER_KEYS:
        QTest.keyRelease(stand_in, key, Qt.KeyboardModifier.NoModifier)
    stand_in.deleteLater()


def _unanswered_box(kind: str) -> object:
    """A message box that no test answers: noted (the test then fails) and answered with the safe button, so the run goes on."""

    def show(parent: object, title: str, text: str, *args: object, **kwargs: object) -> QMessageBox.StandardButton:
        _unanswered.append(f"{kind} '{title}': {text}")
        return QMessageBox.StandardButton.No if kind == "question" else QMessageBox.StandardButton.Ok

    return staticmethod(show)


def _unanswered_exec(self: QDialog) -> int:
    """A window that waits for a click (exec) and that no test answers: noted (the test then fails) and answered with Cancel."""
    _unanswered.append(f"window '{self.windowTitle()}' ({type(self).__name__})")
    return QDialog.DialogCode.Rejected.value


def _unanswered_picker(name: str, cancelled: object) -> object:
    """A ready-made picker that no test answers: noted, and answered as cancelled."""

    def show(*args: object, **kwargs: object) -> object:
        _unanswered.append(f"picker {name}")
        return cancelled

    return staticmethod(show)


@pytest.fixture(autouse=True, scope="session")
def no_box_waits_for_a_click() -> Generator[None]:
    """For the whole run: a message box, a window that waits for a click or a picker is never shown for real."""
    real = {name: QMessageBox.__dict__[name] for name in ("question", "warning", "information", "critical")}
    for name in real:
        setattr(QMessageBox, name, _unanswered_box(name))
    real_exec = QDialog.__dict__["exec"]
    setattr(QDialog, "exec", _unanswered_exec)
    real_pickers = [(owner, name, owner.__dict__[name]) for owner, name, _ in _PICKERS]
    for owner, name, cancelled in _PICKERS:
        setattr(owner, name, _unanswered_picker(f"{owner.__name__}.{name}", cancelled))
    yield
    for name, function in real.items():
        setattr(QMessageBox, name, function)
    setattr(QDialog, "exec", real_exec)
    for owner, name, function in real_pickers:
        setattr(owner, name, function)


@pytest.fixture(autouse=True)
def every_test_starts_clean(request: pytest.FixtureRequest) -> Generator[None]:
    """Every window test starts as the first test of a run does, whatever ran before it, and leaves nothing behind."""
    if not Path(str(request.node.path)).is_relative_to(Path(__file__).parent):  # a plugin sees every test; only the window tests are ours
        yield
        return
    app = cast(QApplication, request.getfixturevalue("qapp"))
    if not _first_look:
        _first_look.append((app.style().objectName(), QPalette(app.palette())))
    _unanswered.clear()
    yield
    let_go_of_keys()
    for window in app.topLevelWidgets():
        window.hide()
        window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    app.setStyle(_first_look[0][0])
    app.setPalette(_first_look[0][1])
    if _unanswered:
        shown = list(_unanswered)
        _unanswered.clear()
        pytest.fail(f"A message box or window came up that the test did not answer: {shown}", pytrace=False)
