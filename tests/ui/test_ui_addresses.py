"""Addresses in the window (block FJ): Open an Address… in the connections window, and Copy the Address of a connection and of a file on
the server. A password is never in an address, and an address that was pasted is saved nowhere."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections
from chrono_ftp.core.addresses import NO_USER_WITH_AT_SAID, PASSWORD_SAID, PORT_PROBLEM, UNDERSTOOD_HEADING
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.ui.addressdialog import ASK_TEXT, OpenAddressDialog, show_address
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.mainwindow import MainWindow
from tests.fakeservers import USER, ssh_server
from tests.ui.test_ui_sessions import connected, ssh_settings, trust_server, window

__all__ = ["window"]  # the fixture, used by name

# --- Version ------------------------------------------------------------
__version__ = "test_ui_addresses.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 10: the line about no user name with an @ in the folder, and the help line (FJ-11)
# v0.0.47: Review round 9: what the window has read from the address stands under the field, and Open is greyed with its reason while
#          the address can not be read (FJ-11)
# v0.0.47: Initial: Open an Address… and Copy the Address (FJ-11, FJ-12)


def window_with(qtbot: QtBot, *saved: Connection) -> ConnectionList:
    connections.save(saved)
    window = ConnectionList()
    qtbot.addWidget(window)
    return window


def dialog_with(qtbot: QtBot, typed: str) -> OpenAddressDialog:
    dialog = OpenAddressDialog()
    qtbot.addWidget(dialog)
    dialog.field.setText(typed)
    return dialog


def note_in(told: list[str]) -> Callable[..., None]:
    def note(*args: object) -> None:
        told.append(str(args[2]))

    return note


def paste(typed: str) -> Callable[[OpenAddressDialog], int]:
    """The person pastes this address in the window and presses Open."""

    def opened(dialog: OpenAddressDialog) -> int:
        dialog.field.setText(typed)
        dialog._read()
        return QDialog.DialogCode.Accepted.value if dialog.address is not None else QDialog.DialogCode.Rejected.value

    return opened


def box_text_in(told: list[str], detailed: bool = False) -> Callable[[QMessageBox], int]:
    """A message box the test answers with Ok, noting what it showed (AGENTS.md 4.4.4)."""

    def answered(box: QMessageBox) -> int:
        told.append(box.detailedText() if detailed else box.text())
        return QMessageBox.StandardButton.Ok.value

    return answered


def took(handed: list[tuple[Connection, str]]) -> Callable[[Connection, str], None]:
    def take(connection: Connection, password: str) -> None:
        handed.append((connection, password))

    return take


# --- Open an Address… (FJ-11) ----------------------------------------------------------


def test_a_pasted_address_is_read_and_the_window_closes(qtbot: QtBot) -> None:
    dialog = dialog_with(qtbot, "sftp://anna@server.example.com:2222/var/log")
    dialog.open_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted.value
    assert dialog.address is not None
    assert (dialog.address.connection.host, dialog.address.connection.port, dialog.address.folder) == ("server.example.com", 2222, "/var/log")


def test_an_address_that_is_no_address_says_why_and_changes_nothing(qtbot: QtBot) -> None:
    dialog = dialog_with(qtbot, "shop.example.com/var")
    dialog.open_button.click()
    assert dialog.address is None
    assert dialog.result() != QDialog.DialogCode.Accepted.value  # the window stays, so what was typed is not lost
    assert "sftp://" in dialog.said.text()
    dialog.field.setText("sftp://shop.example.com")
    assert "Server: shop.example.com" in dialog.said.text()  # what was said goes once the address reads


def test_the_window_shows_what_it_has_read_from_the_address_while_the_person_types(qtbot: QtBot) -> None:
    """A person who meant a password with a slash in it reads which server and port it says before pressing Open (FJ-11, G-2)."""
    dialog = dialog_with(qtbot, "")
    assert dialog.said.text() == ""
    dialog.field.setText("sftp://user:12/ss@server/x")
    assert dialog.said.text().splitlines() == [UNDERSTOOD_HEADING, "Server: user", "Port: 12", "User name: none", "Folder: /ss@server/x", NO_USER_WITH_AT_SAID]
    assert dialog.open_button.isEnabled()  # it is a proper address, and the window says what it will open
    assert "%2F" in ASK_TEXT and "%40" not in ASK_TEXT  # an @ in a folder needs no writing out, so nothing asks for it
    dialog.field.setText("sftp://anna:secret@shop.example.com:2222/var/log")
    assert dialog.said.text().splitlines()[1:4] == ["Server: shop.example.com", "Port: 2222", "User name: anna"]
    assert PASSWORD_SAID in dialog.said.text() and "secret" not in dialog.said.text().replace(PASSWORD_SAID, "")


def test_open_is_greyed_with_its_reason_while_the_address_cannot_be_read(qtbot: QtBot) -> None:
    dialog = dialog_with(qtbot, "sftp://h:nothing/x")
    assert not dialog.open_button.isEnabled()
    assert dialog.open_button.toolTip() == PORT_PROBLEM and dialog.said.text() == PORT_PROBLEM
    dialog.field.setText("sftp://h:22/x")
    assert dialog.open_button.isEnabled() and not dialog.open_button.toolTip()


def test_open_is_greyed_with_its_reason_while_nothing_is_pasted(qtbot: QtBot) -> None:
    dialog = dialog_with(qtbot, "")
    assert not dialog.open_button.isEnabled() and dialog.open_button.toolTip()
    dialog.field.setText("sftp://shop.example.com")
    assert dialog.open_button.isEnabled()


def test_the_connections_window_hands_the_address_over_and_saves_nothing(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    monkeypatch.setattr(OpenAddressDialog, "exec", paste("ftp://anna:very-secret@other.example.com/pub"))
    handed: list[tuple[Connection, str]] = []
    window.address_requested.connect(took(handed))
    window.open_an_address()
    assert len(handed) == 1
    connection, password = handed[0]
    assert (connection.name, connection.host, connection.protocol, connection.start_folder) == ("other.example.com", "other.example.com", FTP, "/pub")
    assert password == "very-secret"
    assert [saved.name for saved in connections.load().connections] == ["Shop"]  # the list is untouched (FJ-11)
    assert "very-secret" not in connections.list_path().read_text(encoding="utf-8")


def test_a_pasted_address_of_a_name_already_in_the_list_gets_a_name_of_its_own(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="shop.example.com", host="shop.example.com"))
    monkeypatch.setattr(OpenAddressDialog, "exec", paste("sftp://shop.example.com/var"))
    handed: list[tuple[Connection, str]] = []
    window.address_requested.connect(took(handed))
    window.open_an_address()
    assert handed[0][0].name == "shop.example.com (2)"


def test_the_button_stands_in_the_connections_window_with_a_hover_text(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    assert window.address_button.text() == "Open an Address…"
    assert "sftp://" in window.address_button.toolTip()


# --- Copy the Address (FJ-12) ----------------------------------------------------------


def test_copy_the_address_of_a_connection_puts_it_on_the_clipboard_and_shows_it(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com", user="anna", start_folder="/var/www"))
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "exec", box_text_in(told))
    window._select("Shop")
    window.copy_the_address()
    assert QGuiApplication.clipboard().text() == "sftp://anna@shop.example.com/var/www"
    assert len(told) == 1 and "sftp://anna@shop.example.com/var/www" in told[0]


def test_the_address_of_a_connection_never_holds_its_password(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: object) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com", user="anna"))
    monkeypatch.setattr(QMessageBox, "exec", box_text_in([]))
    window._select("Shop")
    window.password.setText("very-secret")
    window.copy_the_address()
    assert "very-secret" not in QGuiApplication.clipboard().text()


def test_the_address_shown_can_be_copied_whole_from_the_box(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot)
    details: list[str] = []
    monkeypatch.setattr(QMessageBox, "exec", box_text_in(details, True))
    show_address(window, "sftp://anna@shop.example.com/a/very/long/path/that/would/break/over/lines")
    assert details == ["sftp://anna@shop.example.com/a/very/long/path/that/would/break/over/lines"]


# --- the address of a file on the server (FJ-12) ---------------------------------------


def test_copy_the_address_of_a_file_on_the_server(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    (root / "www").mkdir(parents=True)
    (root / "www" / "page.html").write_text("x", encoding="utf-8")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "exec", box_text_in(told))
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, replace(ssh_settings(server), start_folder="/www"))
        view = window.remote_side.file_list
        view.setCurrentIndex(view.index_of("page.html"))
        window.update_actions()
        action = window.list_actions["remote_copy_address"]
        assert action.isEnabled()
        action.trigger()
    address = f"sftp://{USER}@127.0.0.1:{server.port}/www/page.html"
    assert QGuiApplication.clipboard().text() == address
    assert len(told) == 1 and address in told[0]


def test_the_address_of_a_file_is_greyed_with_its_reason_without_a_connection(qtbot: QtBot, window: MainWindow) -> None:
    window.update_actions()
    action = window.list_actions["remote_copy_address"]
    assert not action.isEnabled() and action.toolTip()


def test_the_left_side_has_no_address_to_copy(qtbot: QtBot, window: MainWindow) -> None:
    assert "local_copy_address" not in window.list_actions  # a file on this computer has no address (FJ-12)
