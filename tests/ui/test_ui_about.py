"""The About window and F1 (D-059)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_ftp.core.appinfo import program_version
from chrono_ftp.ui.aboutdialog import TITLE, AboutWindow
from chrono_ftp.ui.mainwindow import MainWindow

# --- Version ------------------------------------------------------------
__version__ = "test_ui_about.py 0.0.35"

# --- Version history ----------------------------------------------------
# v0.0.35: Initial: F1 opens the About window, which shows the version and closes with Close, Enter and Escape


def about_opened_by_f1(qtbot: QtBot) -> tuple[MainWindow, AboutWindow]:
    window = MainWindow()
    qtbot.addWidget(window)
    with qtbot.waitExposed(window):
        window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)  # a key reaches only the active window
    window.local_side.file_list.setFocus()
    QTest.keySequence(window.local_side.file_list, QKeySequence("F1"))  # as a person presses it, on the part that has the focus
    qtbot.waitUntil(lambda: any(isinstance(widget, AboutWindow) and widget.isVisible() for widget in QApplication.topLevelWidgets()))
    about = next(widget for widget in QApplication.topLevelWidgets() if isinstance(widget, AboutWindow) and widget.isVisible())
    return window, about


def test_f1_opens_the_about_window_with_the_version_and_the_license(qtbot: QtBot) -> None:
    _window, about = about_opened_by_f1(qtbot)
    assert about.windowTitle() == TITLE
    text = about.text.text()
    assert f"version {program_version()}" in text
    assert "An open-source file transfer program for Linux." in text
    assert "License: AGPL-3.0" in text
    about.close_button.click()
    qtbot.waitUntil(lambda: not about.isVisible())


def test_enter_closes_the_about_window(qtbot: QtBot) -> None:
    _window, about = about_opened_by_f1(qtbot)
    QTest.keyClick(about, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not about.isVisible())


def test_escape_closes_the_about_window(qtbot: QtBot) -> None:
    _window, about = about_opened_by_f1(qtbot)
    QTest.keyClick(about, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not about.isVisible())
