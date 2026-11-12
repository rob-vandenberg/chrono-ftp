import os
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMenu, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_ftp.config import bookmarks as saved_places
from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config.bookmarks import LOCAL, REMOTE, SHARED, Bookmark, Places, Profile
from chrono_ftp.core.connection import Connection
from chrono_ftp.ui.bookmarksdialog import BookmarksDialog
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.localside import NO_ACCESS_TEXT, LocalSide, local_parts
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.openfolder import OpenFolderDialog
from chrono_ftp.ui.pathbar import ELLIPSIS
from chrono_ftp.ui.remoteside import remote_parts
from chrono_ftp.ui.windownavigation import PROGRAM_KEYS
from tests.conftest import MemoryStore
from tests.fakeservers import NOT_AS_ROOT, PASSWORD, USER, SshServer, ssh_server
from tests.ui.test_ui_sessions import connected, ssh_settings, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_navigation.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial: the clickable path, Ctrl+L, Ctrl+O, Ctrl+B, the bookmarks window and keys, location profiles, Ctrl+., synchronized
#          browsing, and starting folders (FH-01 to FH-06); the faults of review rounds 1 and 2, each with its own test

__all__ = ["window"]  # the fixture, used by name


class Told:
    """The message boxes and questions of a test: noted, and answered as the test says."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes) -> None:
        self.texts: list[str] = []
        self.questions: list[str] = []
        self.answer = answer
        monkeypatch.setattr(QMessageBox, "information", self.information)
        monkeypatch.setattr(QMessageBox, "question", self.question)

    def information(self, *args: object) -> QMessageBox.StandardButton:
        self.texts.append(str(args[2]))
        return QMessageBox.StandardButton.Ok

    def question(self, *args: object) -> QMessageBox.StandardButton:
        self.questions.append(str(args[2]))
        return self.answer


def shown_window(qtbot: QtBot, window: MainWindow) -> MainWindow:
    with qtbot.waitExposed(window):
        window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    return window


def press(qtbot: QtBot, widget: QWidget, key: str) -> None:
    """The key as a person presses it, on the widget that has the focus."""
    QTest.keySequence(widget, QKeySequence(key))


def server_folders(tmp_path: Path) -> Path:
    root = tmp_path / "server"
    for name in ("css", "js", "deep/inner"):
        (root / name).mkdir(parents=True)
    return root


def local_folders(tmp_path: Path) -> Path:
    local = tmp_path / "local"
    for name in ("css", "img"):
        (local / name).mkdir(parents=True)
    return local


def live(qtbot: QtBot, window: MainWindow, server: SshServer, name: str = "Shop") -> None:
    trust_server(server)
    connected(qtbot, window, ssh_settings(server, name))


# --- the clickable path (FH-01) -----------------------------------------------


def test_the_path_is_a_row_of_folder_parts_and_a_click_on_one_opens_it(qtbot: QtBot, tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    side = LocalSide()
    qtbot.addWidget(side)
    side.resize(2000, 400)
    with qtbot.waitExposed(side):
        side.show()
    side.go_to(str(deep))
    assert side.path_bar.parts.parts == local_parts(str(deep))
    assert side.path_bar.parts.parts[-1] == ("b", str(deep))
    side.path_bar.parts.lay_out()
    rect = next(rect for rect, number in side.path_bar.parts.places if side.path_bar.parts.parts[number][1] == str(tmp_path / "a"))
    QTest.mouseClick(side.path_bar.parts, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
    assert side.folder == str(tmp_path / "a")


def test_a_click_on_the_empty_part_types_a_path_enter_goes_there_and_escape_returns(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "inner").mkdir()
    side = LocalSide()
    qtbot.addWidget(side)
    side.resize(2000, 400)
    with qtbot.waitExposed(side):
        side.show()
    side.go_to(str(tmp_path))
    bar = side.path_bar
    QTest.mouseClick(bar.parts, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(bar.parts.width() - 5, bar.parts.height() // 2))
    assert bar.typing
    bar.line.setText(str(tmp_path / "inner"))
    QTest.keyClick(bar.line, Qt.Key.Key_Return)
    assert side.folder == str(tmp_path / "inner")
    assert not bar.typing
    bar.start_typing()
    bar.line.setText("/somewhere/else")
    QTest.keyClick(bar.line, Qt.Key.Key_Escape)
    assert not bar.typing
    assert bar.line.text() == str(tmp_path / "inner")
    assert side.folder == str(tmp_path / "inner")


def test_a_long_path_shortens_from_the_left_and_keeps_the_last_folder(qtbot: QtBot, tmp_path: Path) -> None:
    deep = tmp_path.joinpath(*[f"folder{number}" for number in range(12)], "last")
    deep.mkdir(parents=True)
    side = LocalSide()
    qtbot.addWidget(side)
    side.resize(260, 400)
    with qtbot.waitExposed(side):
        side.show()
    side.go_to(str(deep))
    shown = side.path_bar.parts._shown_texts()
    assert shown[0][0] == ELLIPSIS
    assert shown[-1][0] == "last"
    assert sum(side.path_bar.fontMetrics().horizontalAdvance(text) for text, _number in shown) <= side.path_bar.parts.width()


def test_the_path_of_the_side_with_the_focus_is_highlighted(qtbot: QtBot, window: MainWindow) -> None:
    shown_window(qtbot, window)
    window.remote_side.file_list.setFocus()
    qtbot.waitUntil(lambda: window.remote_side.path_bar.parts.focused)
    assert not window.local_side.path_bar.parts.focused
    window.local_side.file_list.setFocus()
    qtbot.waitUntil(lambda: window.local_side.path_bar.parts.focused)
    assert not window.remote_side.path_bar.parts.focused


def test_ctrl_l_types_a_path_on_the_side_with_the_focus(qtbot: QtBot, window: MainWindow) -> None:
    shown_window(qtbot, window)
    window.local_side.file_list.setFocus()
    press(qtbot, window.local_side.file_list, "Ctrl+L")
    assert window.local_side.path_bar.typing
    assert not window.remote_side.path_bar.typing  # not connected: nothing to type there


def test_the_right_path_names_user_and_host_on_hover_and_its_parts_open_folders(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        assert window.remote_side.path_bar.toolTip().startswith(f"{USER}@127.0.0.1")
        window.open_remote_folder("/deep/inner")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/deep/inner")
        assert window.remote_side.path_bar.parts.parts == remote_parts("/deep/inner") == [("/", "/"), ("deep", "/deep"), ("inner", "/deep/inner")]
        window.remote_side.path_bar.folder_chosen.emit("/deep")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/deep")
        window.close_tab(0)
        assert "@" not in window.remote_side.path_bar.toolTip()


# --- Open folder and bookmarks (FH-02) ------------------------------------------


def chosen_in_open_folder(monkeypatch: pytest.MonkeyPatch, path: str) -> list[OpenFolderDialog]:
    seen: list[OpenFolderDialog] = []

    def accept(dialog: OpenFolderDialog) -> int:
        seen.append(dialog)
        dialog.path.setEditText(path)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(OpenFolderDialog, "exec", accept)
    return seen


def test_ctrl_o_opens_a_typed_folder_and_keeps_it_in_the_history(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    seen = chosen_in_open_folder(monkeypatch, str(target))
    shown_window(qtbot, window)
    window.local_side.file_list.setFocus()
    press(qtbot, window.local_side.file_list, "Ctrl+O")
    assert len(seen) == 1
    assert window.local_side.folder == str(target)
    assert saved_places.typed_of(saved_places.load().places, LOCAL, "") == [str(target)]


def test_ctrl_o_on_a_folder_that_is_not_there_says_so_and_nothing_moves(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    chosen_in_open_folder(monkeypatch, str(tmp_path / "gone"))
    window.local_side.go_to(str(tmp_path))
    window.open_folder(LOCAL)
    assert window.local_side.folder == str(tmp_path)
    assert len(told.texts) == 1 and "gone" in told.texts[0] and "stays where it was" in told.texts[0]


def test_the_open_folder_window_offers_the_history_and_the_bookmarks_of_its_side(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window._keep_places(Places((Bookmark("Here", str(tmp_path), LOCAL), Bookmark("There", "/srv", REMOTE)), (), ()))
    window._keep_places(saved_places.with_typed(window.places, LOCAL, "", "/usr"))
    seen: list[OpenFolderDialog] = []

    def look(dialog: OpenFolderDialog) -> int:
        seen.append(dialog)
        item = dialog.marks.item(0)
        assert item is not None
        dialog._mark_taken(item)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(OpenFolderDialog, "exec", look)
    window.open_folder(LOCAL)
    assert [seen[0].path.itemText(row) for row in range(seen[0].path.count())] == ["/usr"]
    assert seen[0].marks.count() == 1
    assert window.local_side.folder == str(tmp_path)


def test_ctrl_b_adds_the_folder_once_and_its_menu_opens_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "site"
    folder.mkdir()
    shown_window(qtbot, window)
    window.local_side.go_to(str(folder))
    window.local_side.file_list.setFocus()
    press(qtbot, window.local_side.file_list, "Ctrl+B")
    press(qtbot, window.local_side.file_list, "Ctrl+B")
    assert saved_places.load().places.bookmarks == (Bookmark("site", str(folder), LOCAL),)
    window.local_side.go_to(str(tmp_path))
    menu = window.bookmark_menu_for(LOCAL)
    entry = next(action for action in menu.actions() if action.text() == "site")
    entry.trigger()
    assert window.local_side.folder == str(folder)


def test_a_remote_bookmark_belongs_to_its_connection_and_opens_there(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        window.open_remote_folder("/css")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        window.actions_by_name["remote_bookmark"].trigger()
        assert saved_places.load().places.bookmarks == (Bookmark("css", "/css", REMOTE, "Shop"),)
        window.open_remote_folder("/")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/")
        menu = window.bookmark_menu_for(REMOTE)
        next(action for action in menu.actions() if action.text() == "css").trigger()
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")


def test_the_remote_bookmark_actions_are_greyed_while_not_connected(qtbot: QtBot, window: MainWindow) -> None:
    for name in ("remote_open", "remote_bookmark", "remote_other_side", "sync_browsing", "profiles"):
        assert not window.actions_by_name[name].isEnabled(), name
    assert window.actions_by_name["local_bookmark"].isEnabled()
    assert not window.remote_side.navigation.buttons["bookmarks"].isEnabled()


def test_the_bookmarks_window_renames_deletes_and_reorders(qtbot: QtBot) -> None:
    places = Places((Bookmark("one", "/1", LOCAL), Bookmark("two", "/2", LOCAL), Bookmark("srv", "/srv", REMOTE, "Shop"), Bookmark("three", "/3", LOCAL)))
    dialog = BookmarksDialog(places, "Shop", PROGRAM_KEYS)
    qtbot.addWidget(dialog)
    page = dialog.local_page
    page.list.setCurrentRow(0)
    dialog.mark_fields[LOCAL][0].setText("first")
    dialog.mark_fields[LOCAL][0].textEdited.emit("first")
    moved = page.list.takeItem(2)  # "three" dragged to the top
    page.list.insertItem(0, moved)
    page.list.setCurrentRow(2)  # "two"
    page.delete_button.click()
    # the local ones in their new order, in the places local ones had; the remote one keeps its place
    assert dialog.places().bookmarks == (Bookmark("three", "/3", LOCAL), Bookmark("srv", "/srv", REMOTE, "Shop"), Bookmark("first", "/1", LOCAL))


def test_a_remote_bookmark_can_be_shown_on_every_connection(qtbot: QtBot) -> None:
    dialog = BookmarksDialog(Places((Bookmark("srv", "/srv", REMOTE, "Shop"),)), "Shop", PROGRAM_KEYS, REMOTE)
    qtbot.addWidget(dialog)
    dialog.mark_shared.click()
    assert dialog.places().bookmarks == (Bookmark("srv", "/srv", REMOTE, SHARED),)


def test_a_key_the_program_uses_is_refused_calmly_and_a_free_one_is_kept(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    dialog = BookmarksDialog(Places((Bookmark("one", "/1", LOCAL), Bookmark("two", "/2", LOCAL, key="Ctrl+2"))), "", PROGRAM_KEYS)
    qtbot.addWidget(dialog)
    key = dialog.mark_fields[LOCAL][2]
    dialog.local_page.list.setCurrentRow(0)
    for taken in ("Ctrl+O", "Ctrl+2"):
        key.setKeySequence(QKeySequence(taken))
        key.editingFinished.emit()
    assert len(told.texts) == 2 and "Open folder" in told.texts[0] and "two" in told.texts[1]
    key.setKeySequence(QKeySequence("Ctrl+1"))
    key.editingFinished.emit()
    assert dialog.places().bookmarks[0].key == "Ctrl+1"


def test_the_key_of_a_bookmark_opens_it_on_its_side(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "site"
    folder.mkdir()

    def organized(dialog: BookmarksDialog) -> int:
        dialog.local_page.list.setCurrentRow(0)
        dialog.mark_fields[LOCAL][2].setKeySequence(QKeySequence("Ctrl+1"))
        dialog.mark_fields[LOCAL][2].editingFinished.emit()
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(BookmarksDialog, "exec", organized)
    window._keep_places(Places((Bookmark("site", str(folder), LOCAL),)))
    window.organize_bookmarks(LOCAL)
    assert saved_places.load().places.bookmarks[0].key == "Ctrl+1"
    shown_window(qtbot, window)
    window.local_side.go_to(str(tmp_path))
    window.local_side.file_list.setFocus()
    press(qtbot, window.local_side.file_list, "Ctrl+1")
    assert window.local_side.folder == str(folder)


def test_a_damaged_bookmarks_file_never_stops_the_window(qtbot: QtBot) -> None:
    path = saved_places.places_path()
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    made = MainWindow()
    qtbot.addWidget(made)
    assert made.places == Places()
    assert "nothing is lost" in made.status.currentMessage()
    assert made.bookmark_menu_for(LOCAL).actions()[-1].text() == "No bookmarks yet"


# --- location profiles (FH-03) and the same folder on the other side (FH-04) ----------------


def test_a_location_profile_opens_both_folders_and_a_gone_one_leaves_its_side(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    local = local_folders(tmp_path)

    def named(*args: object) -> tuple[str, bool]:
        return "Styles", True

    monkeypatch.setattr(QInputDialog, "getText", named)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        window.local_side.go_to(str(local / "css"))
        window.open_remote_folder("/css")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        window.add_profile()
        assert saved_places.load().places.profiles == (Profile("Styles", str(local / "css"), "/css", "", "Shop"),)
        window.local_side.go_to(str(local))
        window.open_remote_folder("/")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/")
        menu = window.profile_menu()
        next(action for action in menu.actions() if action.text() == "Styles").trigger()
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        assert window.local_side.folder == str(local / "css")
        window.open_profile(Profile("Gone", str(local / "gone"), "/js"))
        qtbot.waitUntil(lambda: window.remote_side.folder == "/js")
        assert window.local_side.folder == str(local / "css")
        assert len(told.texts) == 1 and "gone" in told.texts[0]


def test_profiles_in_a_folder_are_in_a_menu_of_their_own(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    window._keep_places(Places((), (Profile("A", "/a", "/a", "Work"), Profile("B", "/b", "/b"), Profile("C", "/c", "/c", "", "Other"))))
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        texts = [action.text() for action in window.profile_menu().actions()]
        assert "Work" in texts and "B" in texts and "C" not in texts


def test_ctrl_dot_opens_the_same_folder_on_the_other_side(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    root = server_folders(tmp_path)
    local = local_folders(tmp_path)
    (root / str(local / "css").lstrip("/")).mkdir(parents=True)  # the same path on the server
    with ssh_server(root) as server:
        live(qtbot, window, server)
        shown_window(qtbot, window)
        window.local_side.go_to(str(local / "css"))
        window.local_side.file_list.setFocus()
        press(qtbot, window.local_side.file_list, "Ctrl+.")
        qtbot.waitUntil(lambda: window.remote_side.folder == str(local / "css"))
        window.local_side.go_to(str(local / "img"))  # not on the server
        window.same_folder_on_the_other_side(LOCAL)
        qtbot.waitUntil(lambda: bool(told.texts))
        assert window.remote_side.folder == str(local / "css") and "stays where it was" in told.texts[0]
        window.open_remote_folder("/deep")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/deep")
        window.same_folder_on_the_other_side(REMOTE)  # no /deep on this computer
        assert window.local_side.folder == str(local / "img") and len(told.texts) == 2
        window.open_remote_folder(str(local / "css"))
        qtbot.waitUntil(lambda: window.remote_side.folder == str(local / "css"))
        window.same_folder_on_the_other_side(REMOTE)
        assert window.local_side.folder == str(local / "css")


# --- synchronized browsing (FH-05) ---------------------------------------------


def synced(qtbot: QtBot, window: MainWindow, local: Path) -> None:
    window.local_side.go_to(str(local))
    qtbot.waitUntil(lambda: window.remote_side.folder == "/")
    window.actions_by_name["sync_browsing"].trigger()
    assert window.sessions[0].sync_browsing


def test_a_step_on_one_side_is_made_on_the_other(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        window.local_side.go_to(str(local / "css"))
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        window.local_side.go_to(str(local))
        qtbot.waitUntil(lambda: window.remote_side.folder == "/")
        window.open_remote_folder("/css")
        qtbot.waitUntil(lambda: window.local_side.folder == str(local / "css"))
        window.open_remote_folder("/")
        qtbot.waitUntil(lambda: window.local_side.folder == str(local))
        assert window.remote_side.folder == "/" and told.texts == [] and told.questions == []
        assert window.sessions[0].sync_browsing


def test_a_folder_missing_on_the_other_side_is_made_after_a_question(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch, QMessageBox.StandardButton.Yes)
    local = local_folders(tmp_path)
    root = server_folders(tmp_path)
    with ssh_server(root) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        window.local_side.go_to(str(local / "img"))  # not on the server
        qtbot.waitUntil(lambda: window.remote_side.folder == "/img")
        assert (root / "img").is_dir() and len(told.questions) == 1 and "img" in told.questions[0]
        window.open_remote_folder("/")
        qtbot.waitUntil(lambda: window.local_side.folder == str(local))
        window.open_remote_folder("/js")  # not on this computer
        qtbot.waitUntil(lambda: window.local_side.folder == str(local / "js"))
        assert (local / "js").is_dir() and len(told.questions) == 2
        assert window.sessions[0].sync_browsing


def test_no_to_making_the_folder_turns_synchronized_browsing_off(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch, QMessageBox.StandardButton.No)
    local = local_folders(tmp_path)
    root = server_folders(tmp_path)
    with ssh_server(root) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        window.local_side.go_to(str(local / "img"))
        qtbot.waitUntil(lambda: not window.sessions[0].sync_browsing)
        assert not (root / "img").exists()
        assert not window.actions_by_name["sync_browsing"].isChecked()
        assert "off" in window.status.currentMessage() and len(told.questions) == 1
        assert window.remote_side.folder == "/"


def test_a_step_the_other_side_can_not_match_turns_it_off_and_says_why(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        window.local_side.go_to(str(tmp_path))  # up from local, while the server is at its root
        assert not window.sessions[0].sync_browsing
        assert not window.actions_by_name["sync_browsing"].isChecked()
        assert len(told.texts) == 1 and "can't go up that far" in told.texts[0]
        assert window.remote_side.folder == "/"


def test_each_tab_keeps_its_own_synchronized_browsing(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    assert not window.actions_by_name["sync_browsing"].isEnabled()
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server, "Shop")
        connected(qtbot, window, ssh_settings(server, "Other"))
        window.tab_bar.setCurrentIndex(0)
        window.actions_by_name["sync_browsing"].trigger()
        assert window.actions_by_name["sync_browsing"].isChecked()
        window.tab_bar.setCurrentIndex(1)
        assert not window.actions_by_name["sync_browsing"].isChecked()
        window.tab_bar.setCurrentIndex(0)
        assert window.actions_by_name["sync_browsing"].isChecked()
        assert window.sessions[0].sync_browsing and not window.sessions[1].sync_browsing


# --- starting folders (FH-06) ---------------------------------------------------


def test_a_connection_opens_its_local_starting_folder(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        trust_server(server)
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/css", local_start_folder=str(local / "img")))
        assert window.local_side.folder == str(local / "img")
        assert window.remote_side.folder == "/css"


def test_a_local_starting_folder_that_is_gone_says_so_and_opens_the_home_folder(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    with ssh_server(server_folders(tmp_path)) as server:
        trust_server(server)
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/", local_start_folder=str(tmp_path / "gone")))
        assert window.local_side.folder == str(home)
        assert len(told.texts) == 1 and "gone" in told.texts[0] and "home folder" in told.texts[0]


def test_the_last_folders_are_kept_when_the_tab_closes_and_the_connection_remembers_them(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        settings = Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/", remember_folders=True)
        other = Connection(name="Other", host="127.0.0.1", port=server.port, user=USER, start_folder="/")
        saved_connections.save([settings, other])
        trust_server(server)
        connected(qtbot, window, settings)
        window.local_side.go_to(str(local / "css"))
        window.open_remote_folder("/js")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/js")
        window.close_tab(0)
        connected(qtbot, window, other)
        window.local_side.go_to(str(local / "img"))
        window.close_tab(0)
    kept = {connection.name: connection for connection in saved_connections.load().connections}
    assert (kept["Shop"].local_start_folder, kept["Shop"].start_folder) == (str(local / "css"), "/js")
    assert (kept["Other"].local_start_folder, kept["Other"].start_folder) == ("", "/")


def test_closing_the_program_keeps_the_last_folders_too(qtbot: QtBot, tmp_path: Path, memory_store: MemoryStore) -> None:
    memory_store.passwords["Shop"] = PASSWORD
    local = local_folders(tmp_path)
    made = MainWindow()
    qtbot.addWidget(made)
    with ssh_server(server_folders(tmp_path)) as server:
        settings = Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/", remember_folders=True)
        saved_connections.save([settings])
        trust_server(server)
        connected(qtbot, made, settings)
        made.local_side.go_to(str(local / "img"))
        made.open_remote_folder("/css")
        qtbot.waitUntil(lambda: made.remote_side.folder == "/css")
        made.close()
    kept = saved_connections.load().connections[0]
    assert (kept.local_start_folder, kept.start_folder) == (str(local / "img"), "/css")


def test_the_connections_window_has_the_local_starting_folder_and_remembering(qtbot: QtBot) -> None:
    saved_connections.save([Connection(name="Shop", host="shop.example.com", local_start_folder="/home/anna", remember_folders=True)])
    dialog = ConnectionList()
    qtbot.addWidget(dialog)
    dialog.select_connection("Shop")
    assert dialog.local_start_folder.text() == "/home/anna" and dialog.remember_folders.isChecked()
    assert not dialog.has_changes()
    dialog.clear_fields()
    assert not dialog.remember_folders.isChecked() and dialog.local_start_folder.text() == ""


def test_a_renamed_connection_keeps_its_bookmarks(qtbot: QtBot, window: MainWindow) -> None:
    saved_connections.save([Connection(name="Shop", host="shop.example.com")])
    window._keep_places(Places((Bookmark("srv", "/srv", REMOTE, "Shop"),)))
    window.show_connection_list()
    dialog = window.connection_list
    assert dialog is not None
    dialog.select_connection("Shop")
    dialog.name.setText("Store")
    dialog.save()
    assert saved_places.load().places.bookmarks == (Bookmark("srv", "/srv", REMOTE, "Store"),)
    assert os.path.exists(saved_places.places_path())


# --- review round 1 -------------------------------------------------------------


def test_ctrl_l_on_the_left_reads_a_path_from_the_folder_shown_and_tells_a_wrong_one(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    local = local_folders(tmp_path)
    monkeypatch.setenv("HOME", str(local / "img"))
    side = window.local_side
    side.go_to(str(local))
    for typed, expected in (("css", local / "css"), ("..", local), ("css/..", local), ("~", local / "img")):
        side.path_bar.start_typing()
        side.path_line.setText(typed)
        QTest.keyClick(side.path_line, Qt.Key.Key_Return)
        assert side.folder == str(expected), typed
    side.path_line.setText("nothere")
    QTest.keyClick(side.path_line, Qt.Key.Key_Return)
    assert side.folder == str(local / "img")
    assert len(told.texts) == 1 and str(local / "img" / "nothere") in told.texts[0] and "stays where it was" in told.texts[0]


def test_ctrl_l_on_the_right_reads_a_path_from_the_folder_shown_and_tells_a_wrong_one(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        side = window.remote_side
        for typed, expected in (("deep", "/deep"), ("inner", "/deep/inner"), ("..", "/deep"), ("~/js", "/js")):
            side.path_line.setText(typed)
            side.path_line.returnPressed.emit()
            qtbot.waitUntil(lambda expected=expected: side.folder == expected)
        side.path_line.setText("nothere")
        side.path_line.returnPressed.emit()
        qtbot.waitUntil(lambda: bool(told.texts))
        assert side.folder == "/js" and "/js/nothere" in told.texts[0] and "stays where it was" in told.texts[0]


def test_a_connection_renamed_with_its_tab_open_keeps_its_tab_bookmarks_and_last_folders(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        settings = ssh_settings(server)
        saved_connections.save([settings])
        live(qtbot, window, server)
        session = window.sessions[0]
        login = session.remote
        window.open_remote_folder("/css")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        window.add_bookmark(REMOTE)
        window.show_connection_list()
        dialog = window.connection_list
        assert dialog is not None
        dialog.select_connection("Shop")
        dialog.name.setText("Store")
        dialog.remember_folders.setChecked(True)
        dialog.save()
        assert session.connection.name == "Store" and session.connection.remember_folders
        assert window.tab_bar.tabText(0) == "Store"
        assert session.remote is login and session.alive  # no new login
        assert [action.text() for action in window.bookmark_menu_for(REMOTE).actions()][-1] == "css"
        window.open_remote_folder("/js")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/js")
        window.add_bookmark(REMOTE)
        assert [(mark.name, mark.connection) for mark in saved_places.load().places.bookmarks] == [("css", "Store"), ("js", "Store")]
        window.local_side.go_to(str(local / "img"))
        window.close_tab(0)
    kept = saved_connections.load().connections[0]
    assert (kept.name, kept.local_start_folder, kept.start_folder) == ("Store", str(local / "img"), "/js")


@pytest.mark.parametrize(
    "key",
    [
        "A", "Space", "Esc", "Return", "Del", "Left", "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+F", "Shift+F3", "Shift+A", "Meta+1",
        "Ctrl+Left", "Ctrl+Right", "Ctrl+Up", "Ctrl+Down", "Ctrl+Home", "Ctrl+End", "Ctrl+PgUp", "Ctrl+PgDown", "Ctrl+Backspace", "Ctrl+Del",
        "Ctrl+Space", "Ctrl+Tab", "Ctrl+Shift+Left", "Ctrl+Shift+End", "Alt+Left", "F1", "F3", "F11",
    ],
)  # fmt: skip
def test_a_key_lists_or_fields_need_is_never_a_bookmark_key(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    told = Told(monkeypatch)
    dialog = BookmarksDialog(Places((Bookmark("one", "/1", LOCAL),)), "", PROGRAM_KEYS)
    qtbot.addWidget(dialog)
    dialog.local_page.list.setCurrentRow(0)
    field = dialog.mark_fields[LOCAL][2]
    field.setKeySequence(QKeySequence(key))
    field.editingFinished.emit()
    assert dialog.places().bookmarks[0].key == ""
    assert len(told.texts) == 1
    assert ("Ctrl or Alt" in told.texts[0] and "F1 to F12" in told.texts[0]) or (key in PROGRAM_KEYS and "already opens" in told.texts[0])


@pytest.mark.parametrize("key", ["Ctrl+1", "Alt+1", "Ctrl+Shift+J", "Alt+Shift+Left", "F6", "F12"])
def test_a_key_with_ctrl_or_alt_or_a_free_function_key_is_a_bookmark_key(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    told = Told(monkeypatch)
    dialog = BookmarksDialog(Places((Bookmark("one", "/1", LOCAL),)), "", PROGRAM_KEYS)
    qtbot.addWidget(dialog)
    dialog.local_page.list.setCurrentRow(0)
    field = dialog.mark_fields[LOCAL][2]
    field.setKeySequence(QKeySequence(key))
    field.editingFinished.emit()
    assert dialog.places().bookmarks[0].key == key and told.texts == []


def test_a_key_from_the_file_that_lists_need_is_never_taken(qtbot: QtBot, window: MainWindow) -> None:
    window._keep_places(Places((Bookmark("one", "/1", LOCAL, key="A"), Bookmark("two", "/2", LOCAL, key="Ctrl+2"))))
    assert [action.shortcut().toString(QKeySequence.SequenceFormat.PortableText) for action in window.bookmark_keys] == ["Ctrl+2"]


def test_a_step_into_a_folder_whose_parent_is_missing_on_the_server_turns_sync_off_at_once(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    local = local_folders(tmp_path)
    (local / "img" / "small").mkdir()
    root = server_folders(tmp_path)
    with ssh_server(root) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        window.local_side.go_to(str(local / "img" / "small"))  # two levels down: the server has no /img
        qtbot.waitUntil(lambda: not window.sessions[0].sync_browsing)
        assert told.questions == []
        assert len(told.texts) == 1 and "no folder /" in told.texts[0] and "img" in told.texts[0]
        assert not (root / "img").exists() and window.remote_side.folder == "/"


def test_a_step_into_a_folder_whose_parent_is_missing_here_turns_sync_off_at_once(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        window.open_remote_folder("/deep/inner")  # this computer has no deep
        qtbot.waitUntil(lambda: not window.sessions[0].sync_browsing)
        assert told.questions == []
        assert len(told.texts) == 1 and f"no folder {local}" in told.texts[0] and "inner" in told.texts[0]
        assert not (local / "deep").exists() and window.local_side.folder == str(local)


@NOT_AS_ROOT
def test_a_folder_the_server_refuses_to_make_gives_the_servers_reason(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch, QMessageBox.StandardButton.Yes)
    local = local_folders(tmp_path)
    root = server_folders(tmp_path)
    with ssh_server(root) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        root.chmod(0o555)
        try:
            window.local_side.go_to(str(local / "img"))
            qtbot.waitUntil(lambda: not window.sessions[0].sync_browsing)
        finally:
            root.chmod(0o755)
        assert len(told.questions) == 1 and len(told.texts) == 1
        reason = told.texts[0].split("could not be opened or made.")[1].replace("</p>", "").strip()
        assert reason  # the server's own reason, never an empty one


def test_a_path_typed_in_ctrl_o_on_the_right_is_kept_only_once_it_opened(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        chosen_in_open_folder(monkeypatch, "/gone")
        window.open_folder(REMOTE)
        qtbot.waitUntil(lambda: bool(told.texts))
        assert saved_places.typed_of(window.places, REMOTE, "Shop") == []
        chosen_in_open_folder(monkeypatch, "css")
        window.open_folder(REMOTE)
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        assert saved_places.typed_of(saved_places.load().places, REMOTE, "Shop") == ["/css"]


def test_a_lost_connection_turns_synchronized_browsing_off_for_good(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        session = window.sessions[0]
        window._mark_lost(session)
        assert not session.sync_browsing and not window.actions_by_name["sync_browsing"].isChecked()
        assert "connection ended" in window.status.currentMessage()
        window.reconnect()
        qtbot.waitUntil(lambda: session.alive and not session.connecting)
        assert not session.sync_browsing and not window.actions_by_name["sync_browsing"].isChecked()


def test_a_new_login_that_falls_back_to_the_home_folder_is_no_step(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    local = local_folders(tmp_path)
    root = server_folders(tmp_path)
    with ssh_server(root) as server:
        live(qtbot, window, server)
        session = window.sessions[0]
        window.local_side.go_to(str(local / "css"))
        window.open_remote_folder("/css")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        window.actions_by_name["sync_browsing"].trigger()
        (root / "css").rmdir()
        window._connect(session)  # a new login, as after changed settings: /css is gone, so the home folder opens
        qtbot.waitUntil(lambda: session.alive and not session.connecting and window.remote_side.folder == "/")
        assert not session.sync_browsing and not window.actions_by_name["sync_browsing"].isChecked()
        assert window.local_side.folder == str(local / "css")


def test_a_click_on_a_path_part_gives_that_side_the_focus(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    local = local_folders(tmp_path)
    shown_window(qtbot, window)
    window.local_side.go_to(str(local / "css"))
    window.remote_side.file_list.setFocus()
    qtbot.waitUntil(lambda: window.remote_side.path_bar.parts.focused)
    parts = window.local_side.path_bar.parts
    parts.lay_out()
    rect = next(rect for rect, number in parts.places if parts.parts[number][1] == str(local))
    QTest.mouseClick(parts, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, rect.center())
    assert window.local_side.folder == str(local)
    qtbot.waitUntil(lambda: window.local_side.path_bar.parts.focused)
    assert window.local_side.file_list.hasFocus() and window.focused_side == LOCAL


def test_an_ampersand_in_a_name_stays_in_the_menus(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    window._keep_places(Places((Bookmark("R&D", "/rd", LOCAL),), (Profile("A&B", "/a", "/b", "X&Y"),)))
    assert [action.text() for action in window.bookmark_menu_for(LOCAL).actions()][-1] == "R&&D"
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        menu = window.profile_menu()
        folder = next(action for action in menu.actions() if action.menu() is not None)
        assert folder.text() == "X&&Y"
        submenu = folder.menu()
        assert isinstance(submenu, QMenu) and [action.text() for action in submenu.actions()] == ["A&&B"]


def test_another_window_taking_the_focus_keeps_the_typing(qtbot: QtBot, window: MainWindow) -> None:
    bar = window.local_side.path_bar
    bar.start_typing()
    bar.line.setText("half typed")
    for reason in (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason):
        QApplication.sendEvent(bar.line, QFocusEvent(QEvent.Type.FocusOut, reason))
        assert bar.typing and bar.line.text() == "half typed"
    QApplication.sendEvent(bar.line, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.MouseFocusReason))
    assert not bar.typing


def test_a_step_of_synchronized_browsing_the_person_moved_on_from_is_forgotten(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        session = window.sessions[0]
        window._sync_remote_to(session, "/css")
        window.open_remote_folder("/js")  # the person moves on before the step arrives
        qtbot.waitUntil(lambda: window.remote_side.folder == "/js")
        window.sessions[0].worker.submit(lambda: None, lambda _nothing: None, lambda _error: None)
        qtbot.waitUntil(lambda: session.sync_asked == "")
        window._sync_remote_to(session, "/gone")  # a step that fails
        qtbot.waitUntil(lambda: session.sync_asked == "" and session.wanted_folder == "/js")


# --- review round 2 -------------------------------------------------------------

ODD_NAME: str = "b\udcffd"  # a folder name that is not valid UTF-8


def test_a_folder_that_is_not_valid_utf8_is_kept_as_the_last_folder_when_the_tab_closes(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    odd = tmp_path / ODD_NAME
    odd.mkdir()
    with ssh_server(server_folders(tmp_path)) as server:
        settings = Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/", remember_folders=True)
        saved_connections.save([settings])
        trust_server(server)
        connected(qtbot, window, settings)
        window.local_side.go_to(str(odd))
        window.close_tab(0)
    assert saved_connections.load().connections[0].local_start_folder == str(odd)
    assert not saved_connections.list_path().with_name("connections.json.tmp").exists()


def test_typing_keeps_the_real_path_of_a_folder_that_is_not_valid_utf8(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    odd = tmp_path / ODD_NAME
    (odd / "sub").mkdir(parents=True)
    side = window.local_side
    side.go_to(str(odd))
    side.path_bar.start_typing()
    QTest.keyClick(side.path_line, Qt.Key.Key_Return)
    assert side.folder == str(odd)
    side.path_bar.start_typing()
    side.path_line.setText(side.path_line.text() + "/sub")
    QTest.keyClick(side.path_line, Qt.Key.Key_Return)
    assert side.folder == str(odd / "sub") and told.texts == []


@NOT_AS_ROOT
def test_the_field_to_type_in_holds_the_path_only_never_the_no_access_note(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    closed = tmp_path / "closed"
    closed.mkdir()
    closed.chmod(0)
    try:
        side = window.local_side
        assert side.go_to(str(closed))
        assert NO_ACCESS_TEXT in side.path_bar.parts.note
        side.path_bar.start_typing()
        assert side.path_line.text() == str(closed)
    finally:
        closed.chmod(0o755)


def test_a_refresh_while_typing_leaves_what_is_typed_alone(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        for side in (window.local_side, window.remote_side):
            side.path_bar.start_typing()
            side.path_line.setText("half typed")
        window.local_side.go_to(str(local))
        window.local_side.refresh()
        window.refresh()
        session = window.sessions[0]
        session.worker.submit(lambda: None, lambda _nothing: None, lambda _error: None)
        window.open_remote_folder("/css")  # a folder that arrives while the person types
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        for side in (window.local_side, window.remote_side):
            assert side.path_bar.typing and side.path_line.text() == "half typed"
        assert window.remote_side.path_bar.parts.parts[-1] == ("css", "/css")  # the parts follow at once
        QTest.keyClick(window.remote_side.path_line, Qt.Key.Key_Escape)
        assert window.remote_side.path_line.text() == "/css"


def test_enter_and_escape_in_the_path_field_give_the_keys_back_to_the_list(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    local = local_folders(tmp_path)
    shown_window(qtbot, window)
    side = window.local_side
    side.go_to(str(local))
    side.file_list.setFocus()
    press(qtbot, side.file_list, "Ctrl+L")
    qtbot.waitUntil(side.path_line.hasFocus)
    QTest.keyClick(side.path_line, Qt.Key.Key_Escape)
    qtbot.waitUntil(side.file_list.hasFocus)
    press(qtbot, side.file_list, "Ctrl+L")
    qtbot.waitUntil(side.path_line.hasFocus)
    side.path_line.setText("css")
    QTest.keyClick(side.path_line, Qt.Key.Key_Return)
    assert side.folder == str(local / "css")
    qtbot.waitUntil(side.file_list.hasFocus)


def test_a_folder_typed_in_the_bookmarks_window_is_read_as_in_the_path_field(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/anna")
    places = Places((Bookmark("here", "/1", LOCAL), Bookmark("there", "/srv", REMOTE, "Shop")), (Profile("P", "/a", "/b", "", "Shop"),))
    dialog = BookmarksDialog(places, "Shop", PROGRAM_KEYS, LOCAL, None, "/srv/www", "", "/home/web")
    qtbot.addWidget(dialog)
    for side, typed in ((LOCAL, "~/Documents"), (REMOTE, "site/./logs")):
        page = dialog.page_of(side)
        page.list.setCurrentRow(0)
        field = dialog.mark_fields[side][1]
        field.setText(typed)
        field.textEdited.emit(typed)
    dialog.profile_page.list.setCurrentRow(0)
    for field, typed in ((dialog.profile_local, "css"), (dialog.profile_remote, "~/../public")):
        field.setText(typed)
        field.textEdited.emit(typed)
    result = dialog.places()
    assert [mark.path for mark in result.bookmarks] == ["/home/anna/Documents", "/home/web/site/logs"]
    assert (result.profiles[0].local, result.profiles[0].remote) == ("/srv/www/css", "/home/public")


def test_a_deleted_connection_takes_its_own_bookmarks_and_profiles_along(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    Told(monkeypatch, QMessageBox.StandardButton.Yes)
    saved_connections.save([Connection(name="Shop", host="shop.example.com"), Connection(name="Other", host="other.example.com")])
    window._keep_places(
        Places(
            (Bookmark("own", "/srv", REMOTE, "Shop"), Bookmark("all", "/var", REMOTE), Bookmark("other", "/o", REMOTE, "Other"), Bookmark("here", "/h", LOCAL)),
            (Profile("own", "/a", "/b", "", "Shop"), Profile("all", "/a", "/b")),
            (saved_places.Typed(REMOTE, "Shop", "/etc"), saved_places.Typed(REMOTE, "Other", "/usr")),
        )
    )
    window.show_connection_list()
    dialog = window.connection_list
    assert dialog is not None
    dialog.select_connection("Shop")
    dialog.delete()
    kept = saved_places.load().places
    assert [mark.name for mark in kept.bookmarks] == ["all", "other", "here"]
    assert [profile.name for profile in kept.profiles] == ["all"]
    assert [entry.connection for entry in kept.typed] == ["Other"]


def test_reconnect_of_a_dropped_tab_logs_in_as_the_connection_is_saved_now(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    (first / "old").mkdir(parents=True)
    (second / "new").mkdir(parents=True)
    with ssh_server(first) as old_server, ssh_server(second) as new_server:
        trust_server(old_server)
        trust_server(new_server)
        settings = ssh_settings(old_server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        qtbot.waitUntil(lambda: "old" in window.remote_side.file_list.names())
        session = window.sessions[0]
        saved_connections.save([replace(settings, port=new_server.port)])
        window._mark_lost(session)
        window.reconnect()
        qtbot.waitUntil(lambda: session.alive and not session.connecting and "new" in window.remote_side.file_list.names())
        assert session.connection.port == new_server.port


def test_a_move_up_because_the_folder_went_away_is_no_step(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    local = local_folders(tmp_path)
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        synced(qtbot, window, local)
        window.local_side.go_to(str(local / "css"))
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        (local / "css").rmdir()
        qtbot.waitUntil(lambda: window.local_side.folder == str(local))
        session = window.sessions[0]
        assert session.sync_asked == "" and session.wanted_folder == "/css"
        assert window.remote_side.folder == "/css" and session.sync_browsing and told.texts == told.questions == []


def test_without_the_home_folder_only_a_full_server_path_is_taken(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    told = Told(monkeypatch)
    places = Places((Bookmark("there", "/srv", REMOTE),), (Profile("P", "/a", "/b"),))
    dialog = BookmarksDialog(places, "", PROGRAM_KEYS, REMOTE)  # no tab: the home folder of the server is not known
    qtbot.addWidget(dialog)
    dialog.remote_page.list.setCurrentRow(0)
    dialog.profile_page.list.setCurrentRow(0)
    for field in (dialog.mark_fields[REMOTE][1], dialog.profile_remote):
        for typed in ("~/x", "site"):
            field.setText(typed)
            field.textEdited.emit(typed)
            field.editingFinished.emit()
    assert dialog.places().bookmarks[0].path == "/srv" and dialog.places().profiles[0].remote == "/b"
    assert len(told.texts) == 4 and all("full path, starting with /" in text for text in told.texts)
    assert dialog.mark_fields[REMOTE][1].text() == "/srv"
    for field in (dialog.mark_fields[REMOTE][1], dialog.profile_remote):
        field.setText("/var/www/")
        field.textEdited.emit("/var/www/")
        field.editingFinished.emit()
    assert dialog.places().bookmarks[0].path == "/var/www" and dialog.places().profiles[0].remote == "/var/www" and len(told.texts) == 4


# --- review round 3 -------------------------------------------------------------


def test_a_relative_local_starting_folder_is_read_from_the_home_folder(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)  # the working folder of the program: never where a starting folder is read from
    saved_connections.save([Connection(name="Shop", host="shop.example.com")])
    dialog = ConnectionList()
    qtbot.addWidget(dialog)
    dialog.select_connection("Shop")
    dialog.local_start_folder.setText("Documents/../Documents/")
    assert dialog.from_fields().local_start_folder == str(home / "Documents")
    with ssh_server(server_folders(tmp_path)) as server:
        trust_server(server)
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/", local_start_folder="Documents"))
        assert window.local_side.folder == str(home / "Documents")


def test_on_a_dropped_tab_the_remote_path_can_not_be_typed_or_clicked(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(server_folders(tmp_path)) as server:
        live(qtbot, window, server)
        window.open_remote_folder("/deep")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/deep")
        session = window.sessions[0]
        bar = window.remote_side.path_bar
        bar.start_typing()
        window._mark_lost(session)
        assert not bar.typing
        bar.start_typing()
        assert not bar.typing and "Reconnect first" in bar.toolTip()
        chosen: list[object] = []
        bar.folder_chosen.connect(chosen.append)
        bar.parts.part_clicked.emit("/")
        assert chosen == []
        window.reconnect()
        qtbot.waitUntil(lambda: session.alive and not session.connecting)
        bar.start_typing()
        assert bar.typing and "Reconnect" not in bar.toolTip()


def test_ctrl_l_while_typing_selects_what_is_typed_and_keeps_it(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    shown_window(qtbot, window)
    side = window.local_side
    side.go_to(str(tmp_path))
    side.file_list.setFocus()
    press(qtbot, side.file_list, "Ctrl+L")
    qtbot.waitUntil(side.path_line.hasFocus)
    side.path_line.setText("half typed")
    side.path_line.deselect()
    press(qtbot, side.path_line, "Ctrl+L")
    assert side.path_bar.typing and side.path_line.text() == "half typed" and side.path_line.selectedText() == "half typed"


# --- review round 4 -------------------------------------------------------------


def test_a_folder_a_gone_starting_folder_fell_back_to_is_not_remembered(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Told(monkeypatch)
    local = local_folders(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    with ssh_server(server_folders(tmp_path)) as server:
        settings = Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/gone", local_start_folder=str(tmp_path / "away"), remember_folders=True)
        moved = replace(settings, name="Other")
        saved_connections.save([settings, moved])
        trust_server(server)
        connected(qtbot, window, settings)  # both sides fall back to the home folders, and stay there
        assert window.local_side.folder == str(home) and window.remote_side.folder == "/"
        window.close_tab(0)
        connected(qtbot, window, moved)  # both sides fall back, and the person moves them
        window.local_side.go_to(str(local))
        window.open_remote_folder("/css")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/css")
        window.close_tab(0)
    kept = {connection.name: connection for connection in saved_connections.load().connections}
    assert (kept["Shop"].local_start_folder, kept["Shop"].start_folder) == (str(tmp_path / "away"), "/gone")
    assert (kept["Other"].local_start_folder, kept["Other"].start_folder) == (str(local), "/css")


def test_a_fallback_folder_that_goes_away_moves_the_side_up_and_is_still_not_remembered(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Told(monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    with ssh_server(server_folders(tmp_path)) as server:
        settings = Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/", local_start_folder=str(tmp_path / "away"), remember_folders=True)
        saved_connections.save([settings])
        trust_server(server)
        connected(qtbot, window, settings)
        assert window.local_side.folder == str(home)
        home.rmdir()  # the side moves up by itself: no step of the person
        qtbot.waitUntil(lambda: window.local_side.folder == str(tmp_path))
        window.close_tab(0)
    assert saved_connections.load().connections[0].local_start_folder == str(tmp_path / "away")
