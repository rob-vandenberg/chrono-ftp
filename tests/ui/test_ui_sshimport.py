"""Reading the SSH settings of this computer into the connection list (block FJ): the window with its ticks, what it refuses and why,
and that nothing in ~/.ssh is ever written. Every test builds its own settings folder: the person's own ~/.ssh is never read."""

from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections
from chrono_ftp.config.sessionimport import IMPORT_BUTTON_TEXT
from chrono_ftp.core import sshsettings
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.sshsettings import FoundServer, read_settings
from chrono_ftp.ui.connectionlist import ALL_THERE_TEXT, IMPORT_SSH_TEXT, NONE_IN_FILE_TEXT, NO_SSH_FILE_TEXT, NO_SSH_SERVER_TEXT, SSH_UNREADABLE_TEXT, ConnectionList
from chrono_ftp.ui.plaintext import plain_of
from chrono_ftp.ui.sshimportdialog import NONE_CAN_TEXTS, SshImportDialog

# --- Version ------------------------------------------------------------
__version__ = "test_ui_sshimport.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 3: the three reasons nothing was added, and an import that can't be written (FJ-08, FJ-14)
# v0.0.47: Review round 1: Add says the true reason where no server can come over, the three answers of the settings file are said
#          apart, and an imported name with a slash in it is no folder of the list (FJ-08, FJ-13, FJ-14)
# v0.0.47: Initial: the import of this computer's own SSH settings (FJ-08, FJ-09, FJ-10)

SETTINGS: str = """
Host shop
    HostName shop.example.com
    User anna
    Port 2222

Host inside
    HostName inside.example.com
    ProxyJump jump.example.com
"""


def settings_folder(tmp_path: Path, text: str = SETTINGS) -> Path:
    folder = tmp_path / "ssh-settings" / ".ssh"
    folder.mkdir(parents=True)
    (folder / "config").write_text(text, encoding="utf-8")
    return folder


def window_with(qtbot: QtBot, *saved: Connection) -> ConnectionList:
    connections.save(saved)
    window = ConnectionList()
    qtbot.addWidget(window)
    return window


def note_in(told: list[str]) -> Callable[..., None]:
    def note(*args: object) -> None:
        told.append(plain_of(str(args[2])))

    return note


def refusing_save(connections_to_save: object) -> None:
    """A connection list that can't be written: a full disk, a read-only folder."""
    raise OSError(28, "No space left on device")


def picked_file(path: Path) -> Callable[..., tuple[str, str]]:
    def picked(*_ignored: object, **_kept: object) -> tuple[str, str]:
        return (str(path), "")

    return picked


def folder_itself(folder: Path) -> Callable[[], Path]:
    """The SSH settings the test built, in place of the person's own ~/.ssh (test rule 1)."""

    def found() -> Path:
        return folder

    return found


def accepted(dialog: SshImportDialog) -> int:
    """The person presses Add with the ticks as they stand."""
    return QDialog.DialogCode.Accepted.value


def rejected(dialog: SshImportDialog) -> int:
    return QDialog.DialogCode.Rejected.value


def never_opens(dialog: SshImportDialog) -> int:
    pytest.fail("no window should open")


def dialog_for(qtbot: QtBot, found: tuple[FoundServer, ...], taken: set[str] | None = None) -> SshImportDialog:
    dialog = SshImportDialog(found, taken or set())
    qtbot.addWidget(dialog)
    return dialog


# --- the window of the servers found (FJ-08) -------------------------------------------


def test_every_server_found_stands_in_the_window_with_its_settings(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = dialog_for(qtbot, read_settings(settings_folder(tmp_path)).servers)
    first = dialog.list.topLevelItem(0)
    assert first is not None
    assert [first.text(column) for column in range(3)] == ["shop", "shop.example.com port 2222", "anna"]
    assert [dialog.list.headerItem().text(column) for column in range(4)] == ["Name", "Server", "User", "Key file"]
    assert dialog.list.topLevelItemCount() == 2


def test_a_server_that_can_come_over_is_ticked_and_one_that_cannot_is_not(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = dialog_for(qtbot, read_settings(settings_folder(tmp_path)).servers)
    assert [item.checkState(0) == Qt.CheckState.Checked for item in dialog.items] == [True, False]
    assert [connection.name for connection in dialog.chosen()] == ["shop"]


def test_a_server_that_cannot_come_over_says_why_under_it_and_cannot_be_ticked(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = dialog_for(qtbot, read_settings(settings_folder(tmp_path)).servers)
    refused = dialog.items[1]
    assert refused.childCount() == 1
    child = refused.child(0)
    assert child is not None and "through another server" in child.text(0)
    assert not refused.flags() & Qt.ItemFlag.ItemIsUserCheckable


def test_add_is_greyed_with_its_reason_while_nothing_is_ticked(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = dialog_for(qtbot, read_settings(settings_folder(tmp_path)).servers)
    assert dialog.add_button.isEnabled()
    dialog.items[0].setCheckState(0, Qt.CheckState.Unchecked)
    assert not dialog.add_button.isEnabled() and dialog.add_button.toolTip()


def test_the_name_port_user_and_key_file_reach_the_connection_list(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = settings_folder(tmp_path, "Host shop\n  HostName shop.example.com\n  User anna\n  Port 2222\n  IdentityFile /home/anna/.ssh/id_shop\n")
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(folder))
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    monkeypatch.setattr(SshImportDialog, "exec", accepted)
    window = window_with(qtbot)
    window.import_ssh_settings()
    added = connections.load().connections
    assert [(item.name, item.host, item.port, item.user, item.key_file) for item in added] == [("shop", "shop.example.com", 2222, "anna", "/home/anna/.ssh/id_shop")]


def test_only_the_ticked_servers_are_added(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = settings_folder(tmp_path, "Host one\nHost two\n")
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(folder))
    monkeypatch.setattr(QMessageBox, "information", note_in([]))

    def tick_the_first(dialog: SshImportDialog) -> int:
        dialog.items[1].setCheckState(0, Qt.CheckState.Unchecked)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SshImportDialog, "exec", tick_the_first)
    window = window_with(qtbot)
    window.import_ssh_settings()
    assert [item.name for item in connections.load().connections] == ["one"]


def test_a_server_whose_name_is_taken_comes_over_under_a_name_of_its_own(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = settings_folder(tmp_path, "Host shop\n  HostName shop.example.com\n")
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(folder))
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    monkeypatch.setattr(SshImportDialog, "exec", accepted)
    window = window_with(qtbot, Connection(name="shop", host="other.example.com"))
    window.import_ssh_settings()
    assert sorted(item.name for item in connections.load().connections) == ["shop", "shop (2)"]
    assert next(item for item in connections.load().connections if item.name == "shop").host == "other.example.com"


def test_cancel_adds_nothing(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = settings_folder(tmp_path)
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(folder))
    monkeypatch.setattr(SshImportDialog, "exec", rejected)
    window = window_with(qtbot)
    window.import_ssh_settings()
    assert connections.load().connections == ()


def test_no_settings_file_no_server_and_a_file_that_cannot_be_read_each_say_their_own_thing(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(SshImportDialog, "exec", never_opens)
    window = window_with(qtbot)
    nothing = tmp_path / "none" / ".ssh"
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(nothing))
    window.import_ssh_settings()
    assert told[-1] == NO_SSH_FILE_TEXT.format(path=nothing / "config") and "yet" not in NO_SSH_FILE_TEXT  # nothing is promised
    empty = settings_folder(tmp_path, "# nothing here\n")
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(empty))
    window.import_ssh_settings()
    assert told[-1] == NO_SSH_SERVER_TEXT
    broken = tmp_path / "broken" / ".ssh"
    (broken / "config").mkdir(parents=True)  # a folder where the file should be: it is there and can't be read
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(broken))
    window.import_ssh_settings()
    assert told[-1] == SSH_UNREADABLE_TEXT.format(path=broken / "config")
    assert len(told) == 3


def test_nothing_is_written_into_the_ssh_settings(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = settings_folder(tmp_path)
    (folder / "known_hosts").write_text("shop.example.com ssh-ed25519 AAAA\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in sorted(folder.iterdir())}
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(folder))
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    monkeypatch.setattr(SshImportDialog, "exec", accepted)
    window = window_with(qtbot)
    window.import_ssh_settings()
    assert {path.name: path.read_bytes() for path in sorted(folder.iterdir())} == before
    assert sorted(path.name for path in folder.iterdir()) == ["config", "known_hosts"]


def test_add_says_the_true_reason_where_no_server_can_come_over(qtbot: QtBot, tmp_path: Path) -> None:
    one = dialog_for(qtbot, read_settings(settings_folder(tmp_path, "Include config.d/*\nHost shop\n  HostName shop.example.com\n")).servers)
    assert not one.add_button.isEnabled()
    assert one.add_button.toolTip() == NONE_CAN_TEXTS[0] and "This server" in NONE_CAN_TEXTS[0]  # it reads right for one server too
    more = tmp_path / "more" / ".ssh"
    more.mkdir(parents=True)
    (more / "config").write_text("Include config.d/*\nHost shop\n  HostName shop.example.com\nHost other\n", encoding="utf-8")
    many = dialog_for(qtbot, read_settings(more).servers)
    assert many.add_button.toolTip() == NONE_CAN_TEXTS[1] and "None of these servers" in NONE_CAN_TEXTS[1]
    for said in NONE_CAN_TEXTS:  # the word a person uses for bringing connections in, and no promise of a version (OR-23)
        assert "added" in said and "come over" not in said and "yet" not in said


def test_a_name_with_a_slash_in_the_settings_never_becomes_a_folder(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = settings_folder(tmp_path, "Host work/mail\n  HostName mail.example.com\n")
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(folder))
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    monkeypatch.setattr(SshImportDialog, "exec", accepted)
    window = window_with(qtbot)
    window.import_ssh_settings()
    assert [item.name for item in connections.load().connections] == ["work mail"]
    assert window.tree.item_of("work mail") is not None


def test_the_import_button_offers_both_ways_in(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    assert [action.text() for action in window.import_menu.actions()] == [IMPORT_BUTTON_TEXT, IMPORT_SSH_TEXT]


# --- review round 3 (FJ-08, FJ-14) ------------------------------------------------------


def test_nothing_added_says_which_of_the_three_it_was(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that can't be read, a file without a connection, and a file whose connections the list already holds (FJ-14)."""
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    picked = tmp_path / "sessions.ini"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", picked_file(picked))
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    picked.mkdir()  # a folder where the file should be: it can't be read
    window.import_sessions()
    assert "could not be read" in told[-1]
    picked.rmdir()
    picked.write_text("[Configuration]\n", encoding="utf-8")
    window.import_sessions()
    assert told[-1] == NONE_IN_FILE_TEXT
    picked.write_text("[Sessions\\Shop]\nHostName=shop.example.com\n", encoding="utf-8")
    window.import_sessions()
    assert told[-1] == ALL_THERE_TEXT
    assert [item.name for item in connections.load().connections] == ["Shop"]
    picked.write_text("[Sessions\\Other]\nHostName=other.example.com\n", encoding="utf-8")
    window.import_sessions()
    assert "1 connection was added" in told[-1]
    assert sorted(item.name for item in connections.load().connections) == ["Other", "Shop"]


def test_an_import_that_cannot_be_written_adds_nothing(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = settings_folder(tmp_path, "Host shop\n  HostName shop.example.com\n")
    monkeypatch.setattr(sshsettings, "ssh_folder", folder_itself(folder))
    monkeypatch.setattr(SshImportDialog, "exec", accepted)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    monkeypatch.setattr(connections, "save", refusing_save)
    window.import_ssh_settings()
    assert "could not be saved" in told[-1]
    assert [item.name for item in window.connections] == ["Shop"]  # the list in memory stays as it was (G-2)
    assert [item.name for item in connections.load().connections] == ["Shop"]
    assert window.tree.item_of("shop") is None
