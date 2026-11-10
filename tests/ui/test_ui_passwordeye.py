from PySide6.QtWidgets import QLineEdit
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections
from chrono_ftp.core.connection import Connection
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.logindialog import LoginDialog
from chrono_ftp.ui.passwordeye import HIDE_TEXT, SHOW_TEXT
from tests.conftest import MemoryStore

# --- Version ------------------------------------------------------------
__version__ = "test_ui_passwordeye.py 0.0.21"

# --- Version history ----------------------------------------------------
# v0.0.21: Initial: the eye shows only with typed text, shows and hides it, and an emptied field is hidden again


def connection_window(qtbot: QtBot, *saved: Connection) -> ConnectionList:
    connections.save(saved)
    window = ConnectionList()
    qtbot.addWidget(window)
    return window


def test_an_empty_password_field_has_no_eye(qtbot: QtBot) -> None:
    window = connection_window(qtbot)
    assert not window.password_eye.action.isVisible()


def test_a_stored_password_shows_no_eye_and_is_never_in_the_field(qtbot: QtBot, memory_store: MemoryStore) -> None:
    window = connection_window(qtbot, Connection(name="Shop", host="shop.example.com"))
    memory_store.write("Shop", "very-secret")
    window.list.setCurrentRow(0)
    assert window.password.text() == ""
    assert window.password.placeholderText() == "Stored"
    assert not window.password_eye.action.isVisible()


def test_typing_brings_the_eye_and_a_click_shows_then_hides_the_password(qtbot: QtBot) -> None:
    window = connection_window(qtbot)
    window.password.setText("typed")
    eye = window.password_eye
    assert eye.action.isVisible()
    assert window.password.echoMode() == QLineEdit.EchoMode.Password
    assert eye.action.toolTip() == SHOW_TEXT
    eye.action.trigger()
    assert window.password.echoMode() == QLineEdit.EchoMode.Normal
    assert eye.action.toolTip() == HIDE_TEXT
    eye.action.trigger()
    assert window.password.echoMode() == QLineEdit.EchoMode.Password


def test_choosing_another_connection_hides_the_password_again(qtbot: QtBot) -> None:
    window = connection_window(qtbot, Connection(name="Shop", host="shop.example.com"))
    window.password.setText("typed")
    window.password_eye.action.trigger()
    window.list.setCurrentRow(0)
    assert window.password.echoMode() == QLineEdit.EchoMode.Password
    assert not window.password_eye.action.isVisible()
    window.password.setText("again")
    assert window.password.echoMode() == QLineEdit.EchoMode.Password


def test_the_login_window_has_the_eye_too(qtbot: QtBot) -> None:
    dialog = LoginDialog("Shop", "admin", True, True)
    qtbot.addWidget(dialog)
    assert not dialog.password_eye.action.isVisible()
    dialog.password.setText("typed")
    dialog.password_eye.action.trigger()
    assert dialog.password.echoMode() == QLineEdit.EchoMode.Normal
