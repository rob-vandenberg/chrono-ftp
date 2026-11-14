"""The connection list as a tree (block FJ): folders, a connection dragged into one or out of one, the folders that stand open, Open
Every Connection In This Folder, the note, the settings a new connection starts with, and the typing that jumps to a connection."""

import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, Qt
from PySide6.QtGui import QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.config import bookmarks as saved_places
from chrono_ftp.config.bookmarks import REMOTE
from chrono_ftp.config import connections
from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config import workspaces as saved_workspaces
from chrono_ftp.connections import sessionlog
from chrono_ftp.core import credentials
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.errors import CredentialError
from chrono_ftp.ui.connectionlist import COPY_ADDRESS_TEXT, EMPTY_FOR_NEW_TEXT, EMPTY_FOR_NEW_TIP, FOLDER_OPEN_TEXT, IMPORT_SSH_TEXT, USE_FOR_NEW_TEXT, ConnectionList
from chrono_ftp.ui.connectiontree import CONNECTION, FOLDER, KIND, NAME
from chrono_ftp.ui.logindialog import LoginDialog
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.windowtabs import EDIT_TEXT
from chrono_ftp.ui.windownavigation import ADD_PROFILE_TEXT, ORGANIZE_PROFILES_TEXT, ORGANIZE_TEXT
from chrono_ftp.ui.plaintext import plain_of
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.ui.session import Session, may_keep
from tests.conftest import MemoryStore
from tests.fakeservers import PASSWORD, USER, ssh_server
from tests.ui.test_ui_sessions import connected, settled_tab, ssh_settings, trust_server, window

__all__ = ["window"]  # the fixture, used by name

# --- Version ------------------------------------------------------------
__version__ = "test_ui_connectionfolders.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 3: a move asks every file that has to follow the name first, and a connection saved under the name of an open
#          address tab never takes that tab (FJ-02, FJ-11)
# v0.0.47: Review round 2: typing a name with a space in it, a list that can't be saved, Duplicate Tab of an address tab, and every
#          action that would write under a tab's name greyed with its reason (FJ-02, FJ-07, FJ-11)
# v0.0.47: Review round 1: a move that can't take the password along moves nothing, a taken name in a folder says so in its own words,
#          a name with an empty part is refused and the spaces around each part are trimmed, and the new names follow the program's
#          own style (FJ-01, FJ-02, FJ-17)
# v0.0.47: Initial: block FJ, the tree with its folders, the note, the settings for a new connection and the typing (FJ-01 to FJ-07)

SHOP: Connection = Connection(name="Work/Shop", host="shop.example.com")
MAIL: Connection = Connection(name="Work/Mail server", host="mail.example.com")
HOME: Connection = Connection(name="Home", host="home.example.com")
DEEP: Connection = Connection(name="Work/Old/Archive", host="archive.example.com")


def window_with(qtbot: QtBot, *saved: Connection, open_folders: tuple[str, ...] = (), new_from: str = "") -> ConnectionList:
    connections.save(saved)
    window = ConnectionList(None, open_folders, new_from)
    qtbot.addWidget(window)
    return window


def answer_yes(*args: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def note_in(told: list[str]) -> Callable[..., None]:
    def note(*args: object) -> None:
        told.append(plain_of(str(args[2])))

    return note


def unreadable_places() -> saved_places.LoadedPlaces:
    """The bookmarks as they come back when the file is damaged and no copy of it could be made: writing it would lose what it holds."""
    return saved_places.LoadedPlaces(saved_places.Places(), None, False)


def refusing_write(path: object) -> str:
    """Every settings file answers that it can't be written: a read-only folder, a full disk."""
    return "Read-only file system"


def failing_after(times: int) -> Callable[..., None]:
    """A store that takes this many writes and then refuses: a forward write goes through, the putting back does not."""
    done: list[int] = []

    def written(*_ignored: object) -> None:
        done.append(1)
        if len(done) > times:
            raise OSError(28, "No space left on device")

    return written


def refusing_save(connections_to_save: object) -> None:
    """A connection list that can't be written: a full disk, a read-only folder."""
    raise OSError(28, "No space left on device")


def typed_login(dialog: LoginDialog) -> int:
    """The person types the user and the password for this one login."""
    dialog.user.setText(USER)
    dialog.password.setText(PASSWORD)
    return LoginDialog.DialogCode.Accepted.value


def typed_name(name: str) -> Callable[..., tuple[str, bool]]:
    """The name a person types in the little window that asks for one."""

    def typed(*_ignored: object, **_kept: object) -> tuple[str, bool]:
        return (name, True)

    return typed


class WriteRefusingStore:
    """A credential store that reads and removes, and cannot write: the copy of a password can't be made."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def read(self, name: str) -> str | None:
        return self.store.read(name)

    def write(self, name: str, password: str) -> None:
        raise CredentialError("the password store is locked")

    def remove(self, name: str) -> None:
        self.store.remove(name)


class KeepingStore:
    """A credential store that writes and reads, and cannot remove: a copy made under a name then stays behind."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    def read(self, name: str) -> str | None:
        return self.store.read(name)

    def write(self, name: str, password: str) -> None:
        self.store.write(name, password)

    def remove(self, name: str) -> None:
        raise CredentialError("the password store is locked")


class NotingStore:
    """The credential store of a test, noting every name it is asked for: nothing of a tab opened from an address may reach it (FJ-11)."""

    def __init__(self, store: MemoryStore, asked_for: list[str]) -> None:
        self.store = store
        self.asked_for = asked_for

    def read(self, name: str) -> str | None:
        self.asked_for.append(name)
        return self.store.read(name)

    def write(self, name: str, password: str) -> None:
        self.store.write(name, password)

    def remove(self, name: str) -> None:
        self.store.remove(name)


class LockedStore:
    """A credential store that can't be reached, as a locked password store is."""

    def read(self, name: str) -> str | None:
        raise CredentialError("the password store is locked")

    def write(self, name: str, password: str) -> None:
        raise CredentialError("the password store is locked")

    def remove(self, name: str) -> None:
        raise CredentialError("the password store is locked")


def renames_in(renamed: list[tuple[str, str]]) -> Callable[[str, Connection], None]:
    def noted(old: str, connection: Connection) -> None:
        renamed.append((old, connection.name))

    return noted


def folders_in(kept: list[tuple[str, ...]]) -> Callable[[object], None]:
    def noted(folders: object) -> None:
        kept.append(tuple(str(name) for name in folders) if isinstance(folders, tuple) else ())

    return noted


def connections_in(wanted: list[list[Connection]]) -> Callable[[object], None]:
    def noted(chosen: object) -> None:
        wanted.append([item for item in chosen if isinstance(item, Connection)] if isinstance(chosen, list) else [])

    return noted


def shown(window: ConnectionList) -> list[tuple[str, str]]:
    """What the tree holds, in its order: of each entry whether it is a folder or a connection, and its whole name."""
    return [(str(item.data(0, KIND)), str(item.data(0, NAME))) for item in window.tree._walk()]


# --- the folders of the list (FJ-01) ---------------------------------------------------


def test_a_name_with_a_slash_puts_the_connection_in_a_folder(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP, HOME)
    assert shown(window) == [(FOLDER, "Work"), (CONNECTION, "Work/Shop"), (CONNECTION, "Home")]


def test_a_folder_holds_folders(qtbot: QtBot) -> None:
    window = window_with(qtbot, DEEP, SHOP)
    assert shown(window) == [(FOLDER, "Work"), (FOLDER, "Work/Old"), (CONNECTION, "Work/Old/Archive"), (CONNECTION, "Work/Shop")]


def test_a_connection_shows_its_own_name_in_its_folder(qtbot: QtBot) -> None:
    window = window_with(qtbot, MAIL)
    item = window.tree.item_of("Work/Mail server")
    assert item is not None and item.text(0) == "Mail server"


def test_the_whole_name_stays_in_the_file(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    window.name.setText("Work/Shop")
    window.host.setText("shop.example.com")
    window.save()
    assert [connection.name for connection in connections.load().connections] == ["Work/Shop"]


def test_a_connection_in_a_folder_is_selected_with_its_folder_open(qtbot: QtBot) -> None:
    window = window_with(qtbot, MAIL)
    window.select_connection("Work/Mail server")
    item = window.tree.item_of("Work/Mail server")
    assert item is not None
    parent = item.parent()
    assert parent is not None and parent.isExpanded()
    assert window.selected() is not None and window.name.text() == "Work/Mail server"


# --- dragging a connection into a folder or out of one (FJ-02) --------------------------


def drop_on(window: ConnectionList, dragged: str, target: str) -> None:
    """The connection dragged and let go on this entry of the tree, as the person does it with the mouse."""
    window.show()
    window.tree.select_name(dragged)
    item = window.tree.item_of(target) or next(found for found in window.tree._walk() if found.data(0, NAME) == target)
    place = window.tree.visualItemRect(item).center()
    event = QDropEvent(QPoint(place.x(), place.y()), Qt.DropAction.MoveAction, QMimeData(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.tree.dropEvent(event)


def test_a_connection_dragged_onto_a_folder_moves_into_it(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP, HOME)
    drop_on(window, "Home", "Work")
    assert sorted(connection.name for connection in connections.load().connections) == ["Work/Home", "Work/Shop"]
    assert window.tree.item_of("Work/Home") is not None


def test_a_connection_dragged_out_of_its_folder_leaves_it(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP, HOME)
    window.show()
    window.tree.select_name("Work/Shop")
    event = QDropEvent(QPoint(5, window.tree.viewport().height() - 2), Qt.DropAction.MoveAction, QMimeData(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.tree.dropEvent(event)
    assert sorted(connection.name for connection in connections.load().connections) == ["Home", "Shop"]


def test_a_connection_moved_into_a_folder_takes_its_password_along(qtbot: QtBot, memory_store: MemoryStore) -> None:
    window = window_with(qtbot, SHOP, HOME)
    memory_store.write("Home", "very-secret")
    window.move_into_folder("Home", "Work")
    assert memory_store.read("Work/Home") == "very-secret"
    assert memory_store.read("Home") is None


def test_a_connection_moved_into_a_folder_takes_its_bookmarks_and_profiles_along(qtbot: QtBot) -> None:
    window = window_with(qtbot, HOME)
    places = saved_places.with_bookmark(saved_places.Places(), saved_places.Bookmark("Logs", "/var/log", saved_places.REMOTE, "Home"))
    places = saved_places.with_profile(places, saved_places.Profile("Nightly", "/tmp", "/var/log", connection="Home"))
    renamed: list[tuple[str, str]] = []
    window.saved.connect(renames_in(renamed))
    window.move_into_folder("Home", "Work")
    assert renamed == [("Home", "Work/Home")]  # the window moves them with the name, as a rename in the fields does
    moved = saved_places.with_connection_renamed(places, "Home", "Work/Home")
    assert [bookmark.connection for bookmark in moved.bookmarks] == ["Work/Home"]
    assert [profile.connection for profile in moved.profiles] == ["Work/Home"]


def test_a_connection_dragged_where_its_name_is_taken_says_so_and_moves_nothing(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Work/Home", host="h"), HOME)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    window.move_into_folder("Home", "Work")
    assert len(told) == 1 and "already" in told[0]
    assert sorted(connection.name for connection in connections.load().connections) == ["Home", "Work/Home"]


def test_a_connection_never_lands_in_a_folder_of_its_own_name(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Work", host="h"), DEEP)
    window.show()
    window.tree.select_name("Work")
    item = window.tree.item_of("Work/Old/Archive")
    assert item is not None
    assert window.tree._wanted_folder(window.tree.visualItemRect(item).center()) is None


# --- the folders that stand open (FJ-03) -----------------------------------------------


def test_the_folders_that_stood_open_stand_open_again(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP, DEEP, open_folders=("Work",))
    work = next(item for item in window.tree._walk() if item.data(0, NAME) == "Work")
    old = next(item for item in window.tree._walk() if item.data(0, NAME) == "Work/Old")
    assert work.isExpanded() and not old.isExpanded()


def test_opening_a_folder_is_kept_for_next_time(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP)
    kept: list[tuple[str, ...]] = []
    window.folders_kept.connect(folders_in(kept))
    work = next(item for item in window.tree._walk() if item.data(0, NAME) == "Work")
    work.setExpanded(True)
    assert kept[-1] == ("Work",)
    work.setExpanded(False)
    assert kept[-1] == ()


# --- Open Every Connection in This Folder (FJ-04) --------------------------------------


def test_the_menu_of_a_folder_opens_every_connection_in_it(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP, MAIL, HOME, DEEP)
    wanted: list[list[Connection]] = []
    window.folder_connect_requested.connect(connections_in(wanted))
    window.open_the_folder("Work")
    assert [connection.name for connection in wanted[0]] == ["Work/Old/Archive", "Work/Mail server", "Work/Shop"]
    assert window.isHidden()


def test_the_menu_of_a_folder_holds_that_one_item_with_its_count(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP, MAIL)
    menu = window.folder_menu_for("Work")
    actions = [action for action in menu.actions() if not action.isSeparator()]
    assert [action.text() for action in actions] == [FOLDER_OPEN_TEXT]
    assert actions[0].isEnabled() and "2 connections" in actions[0].toolTip() and "Work" in actions[0].toolTip()


def test_the_item_is_greyed_with_its_reason_in_an_empty_folder(qtbot: QtBot) -> None:
    window = window_with(qtbot, DEEP)
    window.move_into_folder("Work/Old/Archive", "")  # nothing is left in Work/Old, so the folder goes with it
    menu = window.folder_menu_for("Work/Old")
    action = next(found for found in menu.actions() if not found.isSeparator())
    assert not action.isEnabled() and action.toolTip()


# --- the note of a connection (FJ-05) --------------------------------------------------


def test_the_note_is_saved_and_shown_again(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    window.name.setText("Shop")
    window.host.setText("shop.example.com")
    window.note.setPlainText("The shop's own server. Anna knows the password.")
    window.save()
    assert connections.load().connections[0].note == "The shop's own server. Anna knows the password."
    window.clear_fields()
    window._select("Shop")
    assert window.note.toPlainText() == "The shop's own server. Anna knows the password."


def test_the_note_shows_under_the_mouse_in_the_list(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h", note="Anna knows the password."))
    item = window.tree.item_of("Shop")
    assert item is not None and item.toolTip(0) == "Anna knows the password."


def test_a_connection_without_a_note_has_no_hover_text(qtbot: QtBot) -> None:
    window = window_with(qtbot, HOME)
    item = window.tree.item_of("Home")
    assert item is not None and item.toolTip(0) == ""


def test_a_changed_note_counts_as_a_change(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h", note="before"))
    window._select("Shop")
    assert not window.has_changes()
    window.note.setPlainText("after")
    assert window.has_changes()


# --- the settings a new connection starts with (FJ-06) ---------------------------------


def test_new_starts_with_the_settings_of_the_chosen_connection_and_an_empty_name(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com", user="anna", port=2222, note="the usual", colour="#aa3355"))
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    window._select("Shop")
    window.use_for_a_new_one()
    window.clear_fields()
    assert window.name.text() == ""
    assert (window.host.text(), window.user.text(), window.port.value()) == ("shop.example.com", "anna", 2222)
    assert window.note.toPlainText() == "the usual" and window.use_colour.isChecked()


def test_the_chosen_connection_is_kept_for_next_time(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h"))
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    kept: list[str] = []
    window.new_from_kept.connect(kept.append)
    window._select("Shop")
    window.use_for_a_new_one()
    assert kept == ["Shop"]
    window.use_empty_for_a_new_one()
    assert kept == ["Shop", ""]


def test_empty_settings_for_a_new_one_makes_new_empty_again(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com", user="anna"), new_from="Shop")
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    window.clear_fields()
    assert window.host.text() == "shop.example.com"
    window.use_empty_for_a_new_one()
    window.clear_fields()
    assert (window.host.text(), window.user.text()) == ("", "")


def test_the_item_that_puts_it_back_is_greyed_with_its_reason_while_new_is_already_empty(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h"))
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    window._select("Shop")
    assert not window.empty_for_new_action.isEnabled()
    assert window.empty_for_new_action.toolTip() == EMPTY_FOR_NEW_TIP  # greyed with its reason (OR-20)
    window.use_for_a_new_one()
    assert window.empty_for_new_action.isEnabled()
    assert window.empty_for_new_action.toolTip() == EMPTY_FOR_NEW_TEXT  # no reason of its own: Qt shows the name


def test_a_renamed_connection_stays_the_one_a_new_one_starts_with(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h"), new_from="Shop")
    window._select("Shop")
    window.name.setText("Store")
    window.save()
    assert window.new_from == "Store"


def test_the_two_items_stand_in_the_menu_of_a_connection(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h"))
    window.show()
    item = window.tree.item_of("Shop")
    assert item is not None
    menu = window.menu_for(window.tree.visualItemRect(item).center())
    assert menu is not None
    assert {USE_FOR_NEW_TEXT, EMPTY_FOR_NEW_TEXT} <= {action.text() for action in menu.actions()}


# --- typing jumps to a connection (FJ-07) ----------------------------------------------


def test_typing_letters_jumps_to_the_connection_that_starts_with_them(qtbot: QtBot) -> None:
    window = window_with(qtbot, HOME, Connection(name="Shop", host="h"), Connection(name="Server", host="h"))
    window.show()
    window.tree.setFocus()
    QTest.keyClicks(window.tree, "se")
    assert window.tree.selected_name() == "Server"
    assert window.typing.text().startswith("Find: se")


def test_what_was_typed_is_shown_and_goes_again(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h"))
    window.show()
    window.tree.setFocus()
    QTest.keyClicks(window.tree, "sh")
    assert "sh" in window.typing.text()
    QTest.keyClick(window.tree, Qt.Key.Key_Escape)
    assert window.typing.text() == ""


def test_backspace_takes_the_last_letter_back(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h"), Connection(name="Sailing", host="h"))
    window.show()
    window.tree.setFocus()
    QTest.keyClicks(window.tree, "sh")
    QTest.keyClick(window.tree, Qt.Key.Key_Backspace)
    assert window.tree.typed == "s"


def test_typing_finds_a_connection_inside_a_closed_folder_and_opens_it(qtbot: QtBot) -> None:
    window = window_with(qtbot, MAIL, HOME)
    window.show()
    window.tree.setFocus()
    QTest.keyClicks(window.tree, "ma")
    assert window.tree.selected_name() == "Work/Mail server"
    item = window.tree.item_of("Work/Mail server")
    assert item is not None
    parent = item.parent()
    assert parent is not None and parent.isExpanded()


def test_typing_a_letter_no_connection_starts_with_keeps_the_selection(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h"))
    window.show()
    window._select("Shop")
    window.tree.setFocus()
    QTest.keyClicks(window.tree, "z")
    assert window.tree.selected_name() == "Shop" and window.typing.text() == ""


def test_a_folder_goes_when_its_last_connection_is_deleted(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, SHOP, HOME)
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    window._select("Work/Shop")
    window.delete()
    assert shown(window) == [(CONNECTION, "Home")]  # the folder goes with its last connection


# --- what the main window keeps and does with them (FJ-03, FJ-04, FJ-06, FJ-11) --------


def test_the_window_keeps_the_open_folders_and_the_settings_for_a_new_connection(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QMessageBox, "information", note_in([]))
    connections.save([SHOP, HOME])
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    work = next(item for item in view.tree._walk() if item.data(0, NAME) == "Work")
    work.setExpanded(True)
    assert saved_settings.load().open_connection_folders == ("Work",)
    view._select("Home")
    view.use_for_a_new_one()
    assert saved_settings.load().new_connection_from == "Home"
    view.close()


def test_the_connections_window_opens_with_the_folders_that_stood_open(qtbot: QtBot, window: MainWindow) -> None:
    connections.save([SHOP, HOME])
    saved_settings.save(replace(saved_settings.load(), open_connection_folders=("Work",), new_connection_from="Home"))
    window.settings = saved_settings.load()
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    work = next(item for item in view.tree._walk() if item.data(0, NAME) == "Work")
    assert work.isExpanded() and view.new_from == "Home"
    view.close()


def test_open_every_connection_in_this_folder_opens_a_tab_for_each(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    memory_store.write("Work/Shop", PASSWORD)
    memory_store.write("Work/Other", PASSWORD)
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        shop = replace(ssh_settings(one, "Work/Shop"), colour="")
        other = replace(ssh_settings(two, "Work/Other"), colour="")
        connections.save([shop, other, HOME])
        window.show_connection_list()
        view = window.connection_list
        assert view is not None
        view.open_the_folder("Work")
        for session in window.sessions:
            qtbot.waitUntil(lambda session=session: session.alive and session.remote_folder != "")
        assert [session.connection.name for session in window.sessions] == ["Work/Other", "Work/Shop"]
        for session in window.sessions:
            settled_tab(qtbot, session)


def test_a_tab_of_a_pasted_address_keeps_nothing_of_its_login(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()

    def typed(dialog: LoginDialog) -> int:
        dialog.user.setText(USER)
        dialog.password.setText(PASSWORD)
        return LoginDialog.DialogCode.Accepted.value

    monkeypatch.setattr(LoginDialog, "exec", typed)  # the person types the user and the password for this one login
    with ssh_server(root) as server:
        trust_server(server)
        pasted = Connection(name="127.0.0.1", host="127.0.0.1", port=server.port)
        window.open_an_address(pasted, "")
        session = window.sessions[-1]
        qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
        settled_tab(qtbot, session)
        assert session.connection.not_saved and session.typed_password  # the person typed it, and it is still not written (FJ-11, OR-19)
        assert memory_store.read("127.0.0.1") is None
        assert memory_store.passwords.get("127.0.0.1") is None
        assert connections.load().connections == ()  # and the address is in no list
        assert saved_settings.load().last_connection != "127.0.0.1"


def test_connect_duplicate_and_delete_grey_out_on_a_folder(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP)
    window._select("Work/Shop")
    assert window.connect_button.isEnabled()
    work = next(item for item in window.tree._walk() if item.data(0, NAME) == "Work")
    window.tree.setCurrentItem(work)
    assert not window.connect_button.isEnabled()  # a folder is no connection: nothing that can't act stays pressable (OR-20)
    assert not window.delete_action.isEnabled() and not window.duplicate_action.isEnabled()
    assert window.name.text() == "Work/Shop"  # the fields keep what they held: nothing is thrown away


# --- review round 1: the move carries everything, and the names (FJ-01, FJ-02) ----------


def test_a_move_that_cannot_take_the_password_along_moves_nothing(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, HOME)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(credentials, "store", LockedStore())
    window.move_into_folder("Home", "Work")
    assert len(told) == 1 and told[0] == "Nothing changed: Home is as it was. Your password store could not be read."
    assert [item.name for item in connections.load().connections] == ["Home"]  # the connection stands where it stood
    assert window.tree.item_of("Home") is not None


def test_the_refusal_when_a_name_is_taken_in_that_folder_says_so_in_its_own_words(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Work/Home", host="h"), HOME)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    window.move_into_folder("Home", "Work")
    assert told == ["The folder Work already holds a connection called Home, so nothing was moved."]
    window.move_into_folder("Work/Home", "")
    assert told[-1] == "Outside every folder there is already a connection called Home, so nothing was moved."
    assert sorted(item.name for item in connections.load().connections) == ["Home", "Work/Home"]


def test_a_name_with_an_empty_part_is_refused_and_the_spaces_are_trimmed(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    window.host.setText("h")
    for typed in ("Work/", "A//B", " / ", "/Shop"):
        window.name.setText(typed)
        window.save()
        assert connections.load().connections == (), typed
    assert len(told) == 4 and "empty part" in told[0]
    window.name.setText("Work / Mail server ")
    window.save()
    assert [item.name for item in connections.load().connections] == ["Work/Mail server"]
    assert window.tree.item_of("Work/Mail server") is not None


def test_the_new_names_are_in_the_style_of_the_programs_own_menus(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h"))
    names = [FOLDER_OPEN_TEXT, COPY_ADDRESS_TEXT, USE_FOR_NEW_TEXT, EMPTY_FOR_NEW_TEXT, window.address_button.text(), IMPORT_SSH_TEXT]
    small = {"The", "A", "An", "In", "On", "For", "Of", "And", "To"}
    assert not [word for name in names for word in name.split()[1:] if word.rstrip("…") in small]


def test_a_renamed_connection_takes_the_last_connection_the_tab_order_and_the_workspaces_with_it(qtbot: QtBot, window: MainWindow) -> None:
    connections.save([HOME])
    saved_settings.save(replace(saved_settings.load(), last_connection="Home", tab_order=("Home", "Other")))
    window.settings = saved_settings.load()
    saved_workspaces.save(saved_workspaces.Workspaces((saved_workspaces.Workspace("Daily", (saved_workspaces.WorkspaceTab("Home", "/tmp", "/var"),)),)))
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    view.move_into_folder("Home", "Work")
    assert saved_settings.load().last_connection == "Work/Home"
    assert saved_settings.load().tab_order == ("Work/Home", "Other")
    kept = saved_workspaces.named(saved_workspaces.load(), "Daily")
    assert kept is not None and [tab.connection for tab in kept.tabs] == ["Work/Home"]
    view.close()


def test_a_tab_opened_from_an_address_is_in_no_tab_order_and_in_no_workspace(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    monkeypatch.setattr(QInputDialog, "getText", typed_name("Daily"))  # the name the person types for the workspace
    with ssh_server(root) as server:
        trust_server(server)
        saved = replace(ssh_settings(server, "Shop"), colour="")
        memory_store.write("Shop", PASSWORD)
        connections.save([saved])
        connected(qtbot, window, saved)  # one ordinary tab, so the workspace has something to hold
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        session = window.sessions[-1]
        qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
        settled_tab(qtbot, session)
        window._keep_tab_order()
        assert saved_settings.load().tab_order == ("Shop",)  # the tab of an address keeps no place (FJ-11)
        window.save_workspace()
        kept = saved_workspaces.named(saved_workspaces.load(), "Daily")
        assert kept is not None and [tab.connection for tab in kept.tabs] == ["Shop"]  # a workspace holds nothing of an address tab
        window.tab_bar.setCurrentIndex(window.sessions.index(session))
        window.update_actions()
        bookmark = window.actions_by_name["remote_bookmark"]
        assert not bookmark.isEnabled() and "opened from an address" in bookmark.toolTip()


def test_the_same_address_pasted_twice_opens_two_tabs_of_its_own(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        pasted = Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER)
        window.open_an_address(pasted, PASSWORD)
        first = window.sessions[-1]
        qtbot.waitUntil(lambda: first.alive and first.remote_folder != "")
        settled_tab(qtbot, first)
        window.open_an_address(replace(pasted, start_folder="/"), PASSWORD)  # the same address again
        second = window.sessions[-1]
        qtbot.waitUntil(lambda: second.alive and second.remote_folder != "")
        settled_tab(qtbot, second)
        assert second is not first  # the second address never takes over the first one's tab, nor its login (FJ-11)
        assert [session.connection.name for session in window.sessions] == ["127.0.0.1", "127.0.0.1 (2)"]
        assert first.alive and first.password == PASSWORD and second.password == PASSWORD  # each tab keeps its own login
        assert first.connection.not_saved and second.connection.not_saved


def test_a_password_changed_on_the_server_is_not_kept_for_a_tab_opened_from_an_address(qtbot: QtBot, window: MainWindow, memory_store: MemoryStore) -> None:
    """The window that changes the password on the server asks the one question before it keeps anything here (FJ-11, OR-19)."""
    address = Session(Connection(name="server.example.com", host="server.example.com", not_saved=True), Worker(window.bridge.deliver), "/tmp")
    address.password = "typed-for-this-login"
    assert not may_keep(address)
    assert window.new_password_kept(address, "the-new-one")  # it did what it had to do: keep nothing
    assert memory_store.read("server.example.com") is None
    saved = Session(Connection(name="Shop", host="shop.example.com"), Worker(window.bridge.deliver), "/tmp")
    saved.password = "the-old-one"
    assert window.new_password_kept(saved, "the-new-one")
    assert memory_store.read("Shop") == "the-new-one"  # a saved connection keeps it, as it always did (OR-24)
    for session in (address, saved):
        session.worker.stop()


# --- review round 2 (FJ-02, FJ-07, FJ-11) ----------------------------------------------


def test_typing_a_name_with_a_space_in_it_finds_it(qtbot: QtBot) -> None:
    window = window_with(qtbot, MAIL, Connection(name="Mail box", host="h"))
    window.show()
    window.tree.setFocus()
    QTest.keyClicks(window.tree, "mail s")
    assert window.tree.typed == "mail s"
    assert window.tree.selected_name() == "Work/Mail server"  # a name is more than its first word (FJ-07)


def test_a_space_typed_first_is_no_name_to_find(qtbot: QtBot) -> None:
    window = window_with(qtbot, MAIL)
    window.show()
    window.tree.setFocus()
    QTest.keyClick(window.tree, Qt.Key.Key_Space)
    assert window.tree.typed == ""


def test_a_list_that_cannot_be_saved_puts_every_store_back_as_it_stood(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    """The bookmarks, the settings and the workspaces followed the new name and the list itself then failed: each goes back exactly as
    it stood, and the person reads the name the connection still has (FJ-02)."""
    connections.save([HOME])
    memory_store.write("Home", "very-secret")
    saved_settings.save(replace(saved_settings.load(), last_connection="Home", tab_order=("Home",)))
    window.settings = saved_settings.load()
    window._keep_places(saved_places.with_bookmark(saved_places.Places(), saved_places.Bookmark("Logs", "/var/log", REMOTE, "Home")))
    stale = saved_workspaces.WorkspaceTab("Work/Home", "/tmp", "/old")  # a left-over of a connection that is gone
    saved_workspaces.save(saved_workspaces.Workspaces((saved_workspaces.Workspace("Daily", (saved_workspaces.WorkspaceTab("Home", "/tmp", "/var"), stale)),)))
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(connections, "save", refusing_save)
    view.move_into_folder("Home", "Work")
    assert len(told) == 1 and told[0].startswith("Nothing changed: Home is as it was.")
    assert [mark.connection for mark in saved_places.load().places.bookmarks] == ["Home"]
    assert saved_settings.load().last_connection == "Home" and saved_settings.load().tab_order == ("Home",)
    kept = saved_workspaces.named(saved_workspaces.load(), "Daily")
    assert kept is not None and [tab.connection for tab in kept.tabs] == ["Home", "Work/Home"]  # the left-over as it stood, not renamed
    view.close()


def test_a_list_that_cannot_be_saved_moves_nothing_and_keeps_the_password(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    window = window_with(qtbot, HOME)
    memory_store.write("Home", "very-secret")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(connections, "save", refusing_save)
    window.move_into_folder("Home", "Work")
    assert len(told) == 1 and told[0].startswith("Nothing changed: Home is as it was.") and "connections.json could not be saved" in told[0]
    assert memory_store.read("Home") == "very-secret"  # the password is where the connection still is (FJ-02)
    assert memory_store.read("Work/Home") is None  # and no copy is left behind under the new name
    assert window.tree.item_of("Home") is not None


def test_duplicate_tab_of_an_address_tab_keeps_nothing_either(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        first = window.sessions[-1]
        qtbot.waitUntil(lambda: first.alive and first.remote_folder != "")
        settled_tab(qtbot, first)
        window.duplicate_tab(first)
        copy = window.sessions[-1]
        qtbot.waitUntil(lambda: copy.alive and copy.remote_folder != "")
        settled_tab(qtbot, copy)
        assert copy is not first and copy.connection.not_saved  # a copy of a tab that may not be kept may not be kept either (FJ-11)
        window._keep_tab_order()
        assert saved_settings.load().tab_order == ()
        assert saved_settings.load().last_connection != "127.0.0.1"
        assert memory_store.read("127.0.0.1") is None
        window.update_actions()
        assert not window.actions_by_name["remote_bookmark"].isEnabled()


def test_every_action_that_would_write_under_a_tabs_name_is_greyed_with_its_reason(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "www").mkdir(parents=True)
    with ssh_server(root) as server:
        trust_server(server)
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER, start_folder="/www"), PASSWORD)
        session = window.sessions[-1]
        qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
        settled_tab(qtbot, session)
        window.update_actions()
        bookmark = window.actions_by_name["remote_bookmark"]
        assert not bookmark.isEnabled() and "opened from an address" in bookmark.toolTip()
        marks = window.bookmark_menu_for(REMOTE)
        organize = next(action for action in marks.actions() if action.text() == ORGANIZE_TEXT)
        assert not organize.isEnabled() and "opened from an address" in organize.toolTip()
        profiles = window.profile_menu()
        for text in (ADD_PROFILE_TEXT, ORGANIZE_PROFILES_TEXT):
            action = next(found for found in profiles.actions() if found.text() == text)
            assert not action.isEnabled() and "opened from an address" in action.toolTip(), text
        places_before = saved_places.load().places
        window.add_bookmark(REMOTE)
        window.add_profile()
        window.organize_bookmarks(REMOTE)
        assert saved_places.load().places == places_before  # none of them wrote anything (FJ-11)
        marks.deleteLater()
        profiles.deleteLater()


# --- review round 3 (FJ-02, FJ-11) ------------------------------------------------------


def test_a_move_asks_every_file_that_has_to_follow_the_name_first(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore, tmp_path: Path) -> None:
    window = window_with(qtbot, HOME)
    memory_store.write("Home", "very-secret")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(connections, "can_write", refusing_write)
    window.move_into_folder("Home", "Work")
    assert len(told) == 1 and told[0] == f"Nothing changed: Home is as it was. {connections.list_path()} could not be saved: Read-only file system."
    assert [item.name for item in connections.load().connections] == ["Home"]
    assert memory_store.read("Home") == "very-secret" and memory_store.read("Work/Home") is None


def test_a_move_is_refused_while_the_bookmarks_cannot_be_read_in_full(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, HOME)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(saved_places, "load", unreadable_places)  # a damaged file that could not even be kept aside
    window.move_into_folder("Home", "Work")
    assert len(told) == 1 and told[0] == "Nothing changed: Home is as it was. Your bookmarks could not be read in full, and they would have to follow the new name."
    assert "copy" not in told[0]  # no copy is claimed where none was made (FJ-01)
    assert [item.name for item in connections.load().connections] == ["Home"]


def test_a_connection_saved_under_the_name_of_an_open_address_tab_never_takes_that_tab(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        address = window.sessions[-1]
        qtbot.waitUntil(lambda: address.alive and address.remote_folder != "")
        settled_tab(qtbot, address)
        saved = replace(ssh_settings(server, "127.0.0.1"), colour="")
        memory_store.write("127.0.0.1", PASSWORD)
        window._connection_saved("127.0.0.1", saved)  # a connection saved under that very name
        assert address.connection.colour == ""  # the address tab is no tab of that connection (FJ-11)
        assert address.connection.not_saved
        window.open_another_tab(saved)
        second = window.sessions[-1]
        qtbot.waitUntil(lambda: second.alive and second.remote_folder != "")
        settled_tab(qtbot, second)
        assert second is not address and not second.connection.not_saved  # a further tab of the saved one, never the address tab's twin


def test_a_part_that_cannot_follow_the_new_name_moves_nothing_and_says_which(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bookmarks, the settings and the workspaces follow a new name before the connection list itself; where one of them can't be
    written, the ones that did follow are put back and the connection keeps the name it had (FJ-02)."""
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    connections.save([HOME])
    saved_settings.save(replace(saved_settings.load(), last_connection="Home", tab_order=("Home",)))
    window.settings = saved_settings.load()
    window._keep_places(saved_places.with_bookmark(saved_places.Places(), saved_places.Bookmark("Logs", "/var/log", REMOTE, "Home")))
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(saved_settings, "save", refusing_save)
    view.move_into_folder("Home", "Work")
    assert len(told) == 1 and told[0] == f"Nothing changed: Home is as it was. {saved_settings.settings_path()} could not be saved: No space left on device."
    assert [item.name for item in connections.load().connections] == ["Home"]  # the list is written last of all
    assert [bookmark.connection for bookmark in saved_places.load().places.bookmarks] == ["Home"]  # and what followed is put back
    assert saved_settings.load().last_connection == "Home"
    view.close()


def test_reconnect_of_an_address_tab_never_logs_in_to_a_connection_of_that_name(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as mine, ssh_server(second) as other:
        trust_server(mine)
        trust_server(other)
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=mine.port, user=USER), PASSWORD)
        session = window.sessions[-1]
        qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
        settled_tab(qtbot, session)
        connections.save([Connection(name="127.0.0.1", host="127.0.0.1", port=other.port, user="someone-else", key_file="/no/key")])
        window.disconnect_tab(session)
        window.tab_bar.setCurrentIndex(window.sessions.index(session))
        window.reconnect()
        qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
        settled_tab(qtbot, session)
        assert session.connection.port == mine.port  # the address it was opened from, never the saved connection (FJ-11)
        assert session.connection.user == USER and session.connection.key_file == ""


def test_the_bookmark_menu_of_an_address_tab_shows_no_other_connections_bookmarks(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        window._keep_places(saved_places.with_bookmark(saved_places.Places(), saved_places.Bookmark("Logs", "/var/log", REMOTE, "127.0.0.1")))
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        session = window.sessions[-1]
        qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
        settled_tab(qtbot, session)
        menu = window.bookmark_menu_for(REMOTE)
        assert "Logs" not in [action.text() for action in menu.actions()]  # they belong to the saved connection, not to this tab
        menu.deleteLater()


def test_the_check_before_a_move_writes_real_bytes_and_flushes_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A disk with no room left takes an empty file and refuses the bytes, so the check writes as many bytes as the file holds and
    flushes them to the disk; otherwise it passes and the write fails halfway through a rename (FJ-02)."""
    flushed: list[int] = []
    real_fsync = os.fsync

    def noted(descriptor: int) -> None:
        flushed.append(os.fstat(descriptor).st_size)
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", noted)
    kept = tmp_path / "settings.json"
    kept.write_bytes(b"x" * 10)
    assert connections.can_write(kept) == ""
    assert flushed and flushed[0] >= connections.PROBE_BYTES  # really written, and on the disk before the answer
    assert sorted(path.name for path in tmp_path.iterdir()) == ["settings.json"]  # and nothing left behind
    unwritable = tmp_path / "closed"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    try:
        assert connections.can_write(unwritable / "settings.json")
    finally:
        unwritable.chmod(0o700)


def test_a_typed_password_that_cannot_be_kept_saves_nothing(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    window._select("Shop")
    window.host.setText("new.example.com")
    window.password.setText("typed")
    monkeypatch.setattr(credentials, "store", LockedStore())
    window.save()
    assert len(told) == 1 and told[0] == "Nothing changed: Shop is as it was. Your password store could not be written."
    assert connections.load().connections[0].host == "shop.example.com"  # nothing of it was saved (OR-24)


# --- review round 5 (FJ-02, FJ-11) ------------------------------------------------------


def test_the_words_follow_what_the_person_did_a_drag_or_a_typed_name(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    connections.save([HOME])
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(connections, "can_write", refusing_write)
    view.move_into_folder("Home", "Work")
    dragged = told[-1]
    view._select("Home")
    view.name.setText("Away")
    view.save()
    assert told[-1] == dragged  # one sentence of facts for both, in place of a pair of near-identical texts (the owner, round 6)
    assert told[-1].startswith("Nothing changed: Home is as it was.") and len(told[-1].split()) < 25
    assert [item.name for item in connections.load().connections] == ["Home"]
    view.close()


def test_a_store_that_cannot_be_put_back_says_what_stands_on_the_disk(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bookmarks followed the new name and the settings could not; where putting the bookmarks back fails too, the person reads
    what is really on the disk, never that nothing was moved (FJ-02)."""
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    connections.save([HOME])
    saved_settings.save(replace(saved_settings.load(), last_connection="Home"))
    window.settings = saved_settings.load()
    window._keep_places(saved_places.with_bookmark(saved_places.Places(), saved_places.Bookmark("Logs", "/var/log", REMOTE, "Home")))
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(saved_settings, "save", refusing_save)
    written: list[int] = []

    def once_then_never(places: saved_places.Places) -> None:
        written.append(1)
        if len(written) > 1:
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(saved_places, "save", once_then_never)
    view.move_into_folder("Home", "Work")
    assert len(told) == 1 and told[0].startswith("Home is as it was, but the name Work/Home now holds its bookmarks.")
    assert str(saved_places.places_path()) in told[0]  # the file that could not be saved to put them back (FJ-02)
    assert [item.name for item in connections.load().connections] == ["Home"]
    view.close()


def test_putting_back_restores_what_was_there_and_never_renames_the_other_way(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """A left-over bookmark of a connection called Work/Home, from one that was deleted, must still be that after a move was put back:
    a putting back writes what stood there, never a rename the other way, which would make it a bookmark of Home (FJ-02)."""
    connections.save([HOME])
    saved_settings.save(replace(saved_settings.load(), last_connection="Home"))  # so the settings really have to be written
    window.settings = saved_settings.load()
    places = saved_places.with_bookmark(saved_places.Places(), saved_places.Bookmark("Logs", "/var/log", REMOTE, "Home"))
    places = saved_places.with_bookmark(places, saved_places.Bookmark("Old", "/var/old", REMOTE, "Work/Home"))
    window._keep_places(places)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(saved_settings, "save", refusing_save)  # the settings fail after the bookmarks followed the new name
    view.move_into_folder("Home", "Work")
    assert len(told) == 1 and told[0].startswith("Nothing changed: Home is as it was.")
    assert sorted((mark.name, mark.connection) for mark in saved_places.load().places.bookmarks) == [("Logs", "Home"), ("Old", "Work/Home")]
    view.close()


def test_edit_the_tab_name_and_the_stored_password_never_reach_a_connection_of_that_name(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QInputDialog, "getText", typed_name("my own tab"))  # the name the person types for this tab
    monkeypatch.setattr(LoginDialog, "exec", typed_login)  # and the password they type for the login of this one tab
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        memory_store.write("127.0.0.1", "the-saved-ones-password")  # saved afterwards, so it carries the name the tab was given
        connections.save([replace(ssh_settings(server, "127.0.0.1"), colour="", tab_name="saved tab")])
        session = window.sessions[-1]
        qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
        settled_tab(qtbot, session)
        assert session.password == PASSWORD  # the one the address came with
        session.password = None  # as a reconnect starts: without one, a saved connection's stored password must not be taken (FJ-11)
        asked_for: list[str] = []
        monkeypatch.setattr(credentials, "store", NotingStore(memory_store, asked_for))
        window.disconnect_tab(session)
        window.tab_bar.setCurrentIndex(window.sessions.index(session))
        window.reconnect()
        qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
        settled_tab(qtbot, session)
        assert asked_for == []  # the store was never asked under the name this tab was given
        menu = window.tab_menu_for(window.tab_bar.tabRect(window.sessions.index(session)).center())
        assert menu is not None
        edits = [action for action in menu.actions() if action.text() == EDIT_TEXT]
        assert len(edits) == 1 and not edits[0].isEnabled()
        assert "opened from an address" in edits[0].toolTip()
        menu.deleteLater()
        window.rename_tab(session)  # the name it gets is kept with no connection at all
        assert saved_connections.load().connections[0].tab_name == "saved tab"


def test_an_address_tab_gets_its_own_server_information_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        address = window.sessions[-1]
        qtbot.waitUntil(lambda: address.alive and address.remote_folder != "")
        settled_tab(qtbot, address)
        saved = replace(ssh_settings(server, "127.0.0.1"), colour="")  # saved afterwards, so it carries the name the tab was given
        memory_store.write("127.0.0.1", PASSWORD)
        connections.save([saved])
        connected(qtbot, window, saved)
        kept = window.sessions[-1]
        assert kept is not address and kept.connection.name == address.connection.name
        window.show_server_info(address)
        window.show_server_info(kept)
        assert len(window.info_windows) == 2  # each tab its own window, however they are named (FJ-11)
        for open_window in list(window.info_windows.values()):
            open_window.close()


def test_a_copy_of_the_password_left_behind_after_a_failed_move_is_said(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    """The copy made for the new name could not be removed when the move fell through: the person is told what is left and where it is
    (FJ-02, OR-24)."""
    window = window_with(qtbot, HOME)
    memory_store.write("Home", "very-secret")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(credentials, "store", KeepingStore(memory_store))
    monkeypatch.setattr(connections, "save", refusing_save)
    window.move_into_folder("Home", "Work")
    assert any(said.startswith("Nothing changed: Home is as it was.") for said in told)
    assert "Home kept its password. A copy of it is left under the name Work/Home in your password store, where nothing uses it." in told
    assert memory_store.read("Home") == "very-secret"  # where the connection still is


def test_the_folder_tip_reads_right_for_one_connection(qtbot: QtBot) -> None:
    window = window_with(qtbot, SHOP)
    menu = window.folder_menu_for("Work")
    action = next(found for found in menu.actions() if not found.isSeparator())
    assert "for 1 connection in Work" in action.toolTip()  # never "each of the 1 connection" (OR-23)
    menu.deleteLater()


def test_an_address_tab_logs_apart_from_a_connection_of_that_name(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        address = window.sessions[-1]
        qtbot.waitUntil(lambda: address.alive and address.remote_folder != "")
        settled_tab(qtbot, address)
        saved = replace(ssh_settings(server, "127.0.0.1"), colour="")  # saved afterwards, carrying the name the tab was given
        memory_store.write("127.0.0.1", PASSWORD)
        connections.save([saved])
        connected(qtbot, window, saved)
        kept = window.sessions[-1]
        assert sessionlog.log_name(address.connection.name, address.connection.not_saved) != sessionlog.log_name(kept.connection.name, False)
        window.show_session_log(address)
        window.show_session_log(kept)
        assert len(window.log_windows) == 2  # each its own log and its own window (FJ-11)
        for open_window in list(window.log_windows.values()):
            open_window.close()


def test_a_deleted_connection_leaves_no_place_in_the_row_of_tabs(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    connections.save([HOME, SHOP])
    saved_settings.save(replace(saved_settings.load(), tab_order=("Home", "Work/Shop"), new_connection_from="Home"))
    window.settings = saved_settings.load()
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    view._select("Home")
    view.delete()
    assert saved_settings.load().tab_order == ("Work/Shop",)  # nothing of it is left to meet a new connection of that name (FJ-02)
    assert saved_settings.load().new_connection_from == ""
    view.close()


def test_a_rename_never_puts_the_same_name_twice_in_the_row_of_tabs(qtbot: QtBot, window: MainWindow) -> None:
    connections.save([HOME])
    saved_settings.save(replace(saved_settings.load(), tab_order=("Home", "Away")))  # a stale name of a connection that is gone
    window.settings = saved_settings.load()
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    view._select("Home")
    view.name.setText("Away")
    view.save()
    written = json.loads(saved_settings.settings_path().read_text(encoding="utf-8"))
    assert written["tab_order"] == ["Away"]  # one entry in the file itself, never (Away, Away)
    assert saved_settings.load().tab_order == ("Away",)
    view.close()


def test_a_tab_from_an_address_takes_no_place_from_a_connection_of_that_name(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        shop, other = replace(ssh_settings(one, "Shop"), colour=""), replace(ssh_settings(two, "Other"), colour="")
        for connection in (shop, other):
            memory_store.write(connection.name, PASSWORD)
        connections.save([shop, other])
        saved_settings.save(replace(saved_settings.load(), tab_order=("Shop", "Other (2)")))  # the name the address tab will be given
        window.settings = saved_settings.load()
        window.open_an_address(Connection(name="Other", host="127.0.0.1", port=one.port, user=USER), PASSWORD)
        address = window.sessions[-1]
        qtbot.waitUntil(lambda: address.alive and address.remote_folder != "")
        settled_tab(qtbot, address)
        connected(qtbot, window, shop)
        assert [session.connection.name for session in window.sessions] == ["Other (2)", "Shop"]  # a name of its own (FJ-11)
        assert window.sessions[0] is address  # its name in the order moves no other tab: Shop opens at the end


# --- review round 7 (FJ-02, FJ-06, FJ-11) ----------------------------------------------


def test_a_workspace_opens_its_tabs_beside_a_tab_that_was_opened_from_an_address(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        window.open_an_address(Connection(name="127.0.0.1", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        address = window.sessions[-1]
        qtbot.waitUntil(lambda: address.alive and address.remote_folder != "")
        settled_tab(qtbot, address)
        saved = replace(ssh_settings(server, "127.0.0.1"), colour="")  # saved afterwards, carrying the name the tab was given
        memory_store.write("127.0.0.1", PASSWORD)
        connections.save([saved])
        saved_workspaces.save(saved_workspaces.Workspaces((saved_workspaces.Workspace("Daily", (saved_workspaces.WorkspaceTab("127.0.0.1", str(tmp_path), "/"),)),)))
        opened = window.open_workspace("Daily")
        assert opened == 1  # the address tab is no tab of this connection, so the workspace opens its own (FJ-11)
        kept = window.sessions[-1]
        qtbot.waitUntil(lambda: kept.alive and kept.remote_folder != "")
        settled_tab(qtbot, kept)
        assert kept is not address and may_keep(kept)


def test_a_deleted_connection_is_no_longer_the_one_the_window_starts_on(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    connections.save([HOME])
    saved_settings.save(replace(saved_settings.load(), last_connection="Home"))
    window.settings = saved_settings.load()
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    view._select("Home")
    view.delete()
    assert saved_settings.load().last_connection == ""  # a new connection of that name is no connection that was last live (FJ-02)
    view.close()


def test_every_file_that_could_not_be_saved_is_named_and_the_cause_stands_first(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two stores followed the new name, the third stopped it, and neither of the two could be put back: both files are named, and why
    it stopped at all stands first (FJ-02)."""
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    connections.save([HOME])
    saved_settings.save(replace(saved_settings.load(), last_connection="Home"))
    window.settings = saved_settings.load()
    window._keep_places(saved_places.with_bookmark(saved_places.Places(), saved_places.Bookmark("Logs", "/var/log", REMOTE, "Home")))
    saved_workspaces.save(saved_workspaces.Workspaces((saved_workspaces.Workspace("Daily", (saved_workspaces.WorkspaceTab("Home", "/tmp", "/var"),)),)))
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(saved_places, "save", failing_after(1))  # the forward write goes through, the putting back does not
    monkeypatch.setattr(saved_settings, "save", failing_after(1))
    monkeypatch.setattr(saved_workspaces, "save", refusing_save)  # and the workspaces stop the move
    view.move_into_folder("Home", "Work")
    assert len(told) == 1
    assert told[0].startswith("Home is as it was, but the name Work/Home now holds its bookmarks and its place in the row of tabs.")
    assert str(saved_workspaces.workspaces_path()) in told[0]  # why it stopped
    assert str(saved_places.places_path()) in told[0] and str(saved_settings.settings_path()) in told[0]  # and every file left behind
    assert told[0].index(str(saved_workspaces.workspaces_path())) < told[0].index(str(saved_places.places_path()))  # the cause first
    view.close()


def test_a_new_connection_that_cannot_be_saved_says_it_was_not_saved(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot)
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(connections, "save", refusing_save)
    window.name.setText("Work")
    window.host.setText("work.example.com")
    window.save()
    assert len(told) == 1 and told[0].startswith("Work was not saved.")  # it never was there, so nothing is "as it was"
    assert "as it was" not in told[0] and "could not be saved" in told[0]


def test_the_copy_left_behind_is_said_after_what_happened(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    window = window_with(qtbot, HOME)
    memory_store.write("Home", "very-secret")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(credentials, "store", KeepingStore(memory_store))
    monkeypatch.setattr(connections, "save", refusing_save)
    window.move_into_folder("Home", "Work")
    assert len(told) == 2
    assert told[0].startswith("Nothing changed: Home is as it was.")  # what happened first
    assert told[1].startswith("Home kept its password.")  # the leftover copy after it


def test_the_password_store_says_which_call_failed(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    """Reading and writing are asked apart, so the sentence says the cause there is, never a guess at why (the owner, round 7)."""
    window = window_with(qtbot, HOME)
    memory_store.write("Home", "very-secret")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(credentials, "store", WriteRefusingStore(memory_store))
    window.move_into_folder("Home", "Work")
    assert told == ["Nothing changed: Home is as it was. Your password store could not be written."]
    monkeypatch.setattr(credentials, "store", LockedStore())
    window.move_into_folder("Home", "Work")
    assert told[-1] == "Nothing changed: Home is as it was. Your password store could not be read."


def test_a_list_that_cannot_be_saved_says_the_cause_beside_what_is_left_behind(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bookmarks followed, the connection list could not be saved, and the bookmarks could not be put back: the person reads both
    files, with why it stopped first (FJ-02)."""
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    connections.save([HOME])
    window._keep_places(saved_places.with_bookmark(saved_places.Places(), saved_places.Bookmark("Logs", "/var/log", REMOTE, "Home")))
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    monkeypatch.setattr(saved_places, "save", failing_after(1))  # the forward write goes through, the putting back does not
    monkeypatch.setattr(connections, "save", refusing_save)
    view.move_into_folder("Home", "Work")
    assert len(told) == 1
    assert told[0].startswith("Home is as it was, but the name Work/Home now holds its bookmarks.")
    assert str(connections.list_path()) in told[0] and str(saved_places.places_path()) in told[0]
    assert told[0].index(str(connections.list_path())) < told[0].index(str(saved_places.places_path()))  # the cause first
    view.close()


# --- review round 8 (FJ-06, OR-15) ------------------------------------------------------


def test_deleting_the_connection_new_starts_from_makes_new_empty_again(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com", user="anna"), new_from="Shop")
    kept: list[str] = []
    window.new_from_kept.connect(kept.append)
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    window._select("Shop")
    window.delete()
    assert window.new_from == "" and kept == [""]  # nothing of the name is left for a connection saved later (FJ-06)
    assert not window.empty_for_new_action.isEnabled()
    window.clear_fields()
    assert window.host.text() == ""


def test_deleting_or_duplicating_while_the_list_cannot_be_written_changes_nothing(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    """An ordinary event on a full disk: it says what happened and the list stays as it was, never a crash (OR-15)."""
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    memory_store.write("Shop", "very-secret")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    monkeypatch.setattr(connections, "save", refusing_save)
    window._select("Shop")
    window.delete()
    assert told[-1].startswith("Nothing changed: Shop is as it was.") and "could not be saved" in told[-1]
    assert [item.name for item in window.connections] == ["Shop"] and memory_store.read("Shop") == "very-secret"
    window._select("Shop")
    window.duplicate()
    assert told[-1].startswith("Nothing changed: Shop is as it was.")
    assert [item.name for item in window.connections] == ["Shop"]
    assert memory_store.read("Shop (copy)") is None  # and the password copied for it is gone again
    assert shown(window) == [(CONNECTION, "Shop")]
