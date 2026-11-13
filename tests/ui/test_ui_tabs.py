"""Block FI on the screen: the tabs (FI-01). Dragging a tab to another place, the middle mouse button, files dropped on a tab, the
tab keys, renaming, duplicating, the list of all tabs, the menu of a tab and the folder a tab names."""

from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QItemSelectionModel, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QDragEnterEvent, QDragMoveEvent, QDropEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QInputDialog, QMenu
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.core import colours
from chrono_ftp.core.connection import LONGEST_TAB_NAME
from chrono_ftp.transfers.queue import ENDED
from chrono_ftp.ui.filelist import DRAG_MARK, LOCAL_FILES
from chrono_ftp.ui.mainwindow import NO_TAB_OPEN_TEXT, MainWindow
from chrono_ftp.ui.tabbar import NO_TAB
from chrono_ftp.ui.transferdialog import TransferDialog
from chrono_ftp.ui.windowtabs import CLOSE_TEXT, DISCONNECT_TEXT, DUPLICATE_TEXT, FOLDER_SEPARATOR, RECONNECT_TEXT, RENAME_TEXT, TAB_NOT_LIVE_TEXT
from tests.fakeservers import ssh_server
from tests.ui.test_ui_sessions import GREEN, MAGENTA, connected, settled_tab, ssh_settings, transfer_window_accepted, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_tabs.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Initial: a tab dragged to another place and that order kept; the middle mouse button; files dropped on a tab; Ctrl+Tab,
#          Ctrl+Shift+Tab and Alt+1 to Alt+9; Rename Tab, Duplicate Tab, Reconnect, Disconnect and Close Tab; the list of all tabs;
#          the folder a tab shows next to its name (FI-01)

__all__ = ["window"]  # the fixture, used by name


def names_of(window: MainWindow) -> list[str]:
    return [session.connection.name for session in window.sessions]


def labels(window: MainWindow) -> list[str]:
    return [window.tab_bar.tabText(index) for index in range(window.tab_bar.count())]


def tab_centre(window: MainWindow, index: int) -> QPoint:
    return window.tab_bar.tabRect(index).center()


def local_drag() -> QMimeData:
    mime = QMimeData()
    mime.setData(LOCAL_FILES, QByteArray(DRAG_MARK))
    return mime


def drag_over_tab(window: MainWindow, index: int, mime: QMimeData) -> None:
    place = QPointF(tab_centre(window, index))
    window.tab_bar.dragEnterEvent(QDragEnterEvent(place, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    window.tab_bar.dragMoveEvent(QDragMoveEvent(place, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def drop_on_tab(window: MainWindow, index: int, mime: QMimeData) -> None:
    place = QPointF(tab_centre(window, index))
    window.tab_bar.dropEvent(QDropEvent(place, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def pick_local(window: MainWindow, name: str) -> None:
    view = window.local_side.file_list
    selection = view.selectionModel()
    assert selection is not None
    selection.select(view.index_of(name), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def typed(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    """What the person types in the rename window."""

    def given(*args: object, **kwargs: object) -> tuple[str, bool]:
        return answer, True

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(given))


def tab_menu(window: MainWindow, index: int) -> QMenu:
    menu = window.tab_menu_for(tab_centre(window, index))
    assert menu is not None
    return menu


def entry(menu: QMenu, text: str) -> QAction:
    found = [action for action in menu.actions() if action.text() == text]
    assert found, f"{text} is not in the menu: {[action.text() for action in menu.actions()]}"
    return found[0]


# --- 1. a tab dragged to another place (FI-01) -----------------------------------------


def test_a_tab_dragged_to_another_place_takes_its_connection_with_it(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        shown = window.current()
        window.tab_bar.moveTab(1, 0)  # as the person drags it
        assert names_of(window) == ["Other", "Shop"]
        assert window.current() is shown  # the same tab is still shown, in another place
        assert window.tab_bar.background_of(0) == MAGENTA  # a tab keeps its connection's colour (OR-2)


def test_the_order_of_the_tabs_is_kept_and_a_tab_opens_again_in_its_place(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        window.tab_bar.moveTab(1, 0)  # Other before Shop, as the person wants them
        assert saved_settings.load().tab_order[:2] == ("Other", "Shop")
        window.close_tab(1)
        window.close_tab(0)
        assert names_of(window) == []
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))  # opened the other way round this time
        window.open_connection(ssh_settings(two, "Other", MAGENTA))  # its tab goes in front of Shop, so it is not the last one
        assert names_of(window) == ["Other", "Shop"]  # each tab back in the place it was dragged to
        for session in window.sessions:
            settled_tab(qtbot, session)


# --- 2. the middle mouse button (FI-01) ------------------------------------------------


def test_the_middle_mouse_button_closes_the_tab_it_is_clicked_on_and_only_that_one(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        QTest.mouseClick(window.tab_bar, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, tab_centre(window, 0))
        assert names_of(window) == ["Other"]
        assert window.tab_bar.count() == 1


def test_the_middle_mouse_button_beside_the_tabs_closes_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        beside = QPoint(window.tab_bar.width() - 1, window.tab_bar.height() - 1)
        QTest.mouseClick(window.tab_bar, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, beside)
        assert len(window.sessions) == 1


# --- 3. files dropped on a tab (FI-01) -------------------------------------------------


def test_files_dropped_on_another_tab_go_to_that_connection_and_the_tab_shown_stays(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second, here = tmp_path / "one", tmp_path / "two", tmp_path / "here"
    for folder in (first, second, here):
        folder.mkdir()
    (here / "note.txt").write_text("x", encoding="utf-8")
    shown = transfer_window_accepted(monkeypatch)
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        pick_local(window, "note.txt")
        on_screen = window.current()
        drop_on_tab(window, 0, local_drag())  # on Shop, while Other is shown
        qtbot.waitUntil(lambda: bool(shown))
        assert window.current() is on_screen  # the tab shown did not change
        qtbot.waitUntil(lambda: bool(window.queue.jobs))
        assert window.queue.jobs[0].connection.name == "Shop"
        qtbot.waitUntil(lambda: (first / "note.txt").exists())
        qtbot.waitUntil(lambda: all(job.state in ENDED for job in window.queue.jobs))
        for session in window.sessions:
            settled_tab(qtbot, session)  # the refresh after the upload has answered: nothing is in flight when the server stops


def test_a_tab_is_marked_while_it_would_take_the_files_and_a_dead_tab_never_is(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        drag_over_tab(window, 0, local_drag())
        assert window.tab_bar.taking == 0
        assert "Shop" in window.status.currentMessage()
        window.disconnect_tab(window.sessions[0])
        drag_over_tab(window, 0, local_drag())
        assert window.tab_bar.taking == NO_TAB  # it can't take them: it is never marked (G-2)
        assert window.tab_takes_files(0) == TAB_NOT_LIVE_TEXT.format(name="Shop")


def test_a_drag_the_tab_row_can_do_nothing_with_is_refused(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        empty = QMimeData()
        empty.setText("nothing to drop")
        drag_over_tab(window, 0, empty)
        assert window.tab_bar.taking == NO_TAB


# --- 4. the tab keys (FI-01) -----------------------------------------------------------


def test_ctrl_tab_goes_round_the_tabs_and_alt_with_a_number_jumps_to_one(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        with qtbot.waitExposed(window):
            window.show()
        window.activateWindow()
        qtbot.waitUntil(window.isActiveWindow)  # a key reaches only the active window
        view = window.remote_side.file_list
        view.setFocus()
        QTest.keySequence(view, QKeySequence("Ctrl+Tab"))
        assert window.tab_bar.currentIndex() == 0  # round, from the last to the first
        QTest.keySequence(view, QKeySequence("Ctrl+Shift+Tab"))
        assert window.tab_bar.currentIndex() == 1
        QTest.keySequence(view, QKeySequence("Alt+1"))
        assert window.tab_bar.currentIndex() == 0
        QTest.keySequence(view, QKeySequence("Alt+9"))
        assert window.tab_bar.currentIndex() == 0  # no ninth tab: nothing happens


# --- 5. renaming a tab (FI-01) ---------------------------------------------------------


def test_a_renamed_tab_shows_that_name_and_keeps_it_with_its_connection(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        typed(monkeypatch, "Live site")
        window.rename_tab(window.sessions[0])
        assert labels(window)[0].startswith("Live site")
        kept = [connection for connection in saved_connections.load().connections if connection.name == "Shop"]
        assert kept[0].tab_name == "Live site"


def test_an_empty_name_brings_the_name_of_the_connection_back(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        typed(monkeypatch, "Live site")
        window.rename_tab(window.sessions[0])
        typed(monkeypatch, "   ")
        window.rename_tab(window.sessions[0])
        assert labels(window)[0].startswith("Shop")


def test_a_renamed_tab_keeps_its_name_when_the_connection_is_saved_again(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        window.show_connection_list()
        dialog = window.connection_list
        assert dialog is not None
        dialog.select_connection("Shop")
        typed(monkeypatch, "Live site")
        window.rename_tab(window.sessions[0])
        assert not dialog.has_changes()  # the name of a tab is no field of that window
        dialog.save()
        kept = [connection for connection in saved_connections.load().connections if connection.name == "Shop"]
        assert kept[0].tab_name == "Live site"
        assert labels(window)[0].startswith("Live site")


# --- 6. duplicating a tab (FI-01) ------------------------------------------------------


def test_a_duplicated_tab_is_a_second_tab_to_the_same_server_with_its_own_folders(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "deep.txt").write_text("x", encoding="utf-8")
    left_one, left_two = tmp_path / "l1", tmp_path / "l2"
    for folder in (left_one, left_two):
        folder.mkdir()
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(left_one))
        window.duplicate_tab(window.sessions[0])
        assert len(window.sessions) == 2
        copy = window.sessions[1]
        settled_tab(qtbot, copy)
        assert copy.connection.name == "Shop"
        assert window.tab_bar.background_of(1) == GREEN  # the colour of its connection (OR-2)
        window.local_side.go_to(str(left_two))
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: "deep.txt" in window.remote_side.file_list.names())
        window.tab_bar.setCurrentIndex(0)
        assert window.local_side.folder == str(left_one)  # each tab keeps its own two folders (OR-4)
        assert window.sessions[0].remote_folder == "/"


def test_connect_on_a_connection_with_two_tabs_still_opens_no_third_one(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        connected(qtbot, window, settings)
        window.duplicate_tab(window.sessions[0])
        settled_tab(qtbot, window.sessions[1])
        window.open_connection(settings)  # D-051
        assert len(window.sessions) == 2
        assert window.tab_bar.currentIndex() == 0


# --- 7. the list of all tabs (FI-01) ---------------------------------------------------


def test_the_list_of_all_tabs_names_every_tab_and_goes_to_the_one_clicked(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        assert window.all_tabs_button.isEnabled()
        menu = window.tabs_menu()
        texts = [action.text() for action in menu.actions()]
        assert len(texts) == 2 and texts[0].startswith("Shop") and texts[1].startswith("Other")
        assert menu.actions()[1].isChecked()  # the tab shown now
        menu.actions()[0].trigger()
        assert window.tab_bar.currentIndex() == 0
        menu.deleteLater()
        window.close_tab(1)
        window.close_tab(0)
        assert not window.all_tabs_button.isEnabled() and window.all_tabs_button.toolTip() == NO_TAB_OPEN_TEXT  # greyed with its reason (OR-20)


# --- 8. the menu of a tab (FI-01) ------------------------------------------------------


def test_the_menu_of_a_tab_reconnects_disconnects_and_closes_with_its_reasons(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        menu = tab_menu(window, 0)
        assert not entry(menu, RECONNECT_TEXT).isEnabled() and entry(menu, RECONNECT_TEXT).toolTip()  # greyed with its reason (OR-20)
        assert entry(menu, DISCONNECT_TEXT).isEnabled()
        assert entry(menu, CLOSE_TEXT).isEnabled()
        for text in ("Edit settings…", "Pause Automatic Refresh", "Clear Cached Listings", RENAME_TEXT, DUPLICATE_TEXT):
            entry(menu, text)
        menu.deleteLater()
        entry(tab_menu(window, 0), DISCONNECT_TEXT).trigger()
        qtbot.waitUntil(lambda: not window.sessions[0].alive)
        assert window.tab_bar.background_of(0) == colours.NOT_CONNECTED_TAB  # grey, as any dropped connection (OR-3)
        menu = tab_menu(window, 0)
        assert entry(menu, RECONNECT_TEXT).isEnabled()
        assert not entry(menu, DISCONNECT_TEXT).isEnabled()
        entry(menu, RECONNECT_TEXT).trigger()
        settled_tab(qtbot, window.sessions[0])
        assert window.tab_bar.background_of(0) == GREEN
        menu.deleteLater()


def test_close_tab_in_the_menu_closes_that_tab(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        entry(tab_menu(window, 0), CLOSE_TEXT).trigger()
        assert window.sessions == []


# --- 9. the folder a tab names (FI-01) -------------------------------------------------


def test_a_tab_names_the_folder_it_is_in_so_two_tabs_on_one_server_differ(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "deep.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        assert labels(window)[0] == "Shop" + FOLDER_SEPARATOR + "/"
        window.duplicate_tab(window.sessions[0])
        settled_tab(qtbot, window.sessions[1])
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: "deep.txt" in window.remote_side.file_list.names())
        assert labels(window)[1] == "Shop" + FOLDER_SEPARATOR + "inner"
        assert labels(window)[0] == "Shop" + FOLDER_SEPARATOR + "/"
        assert "inner" in window.tab_bar.tabToolTip(1)


def test_a_tab_name_that_is_too_long_is_refused_when_it_is_typed(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        typed(monkeypatch, "x" * (LONGEST_TAB_NAME + 1))
        window.rename_tab(window.sessions[0])
        assert labels(window)[0].startswith("Shop")  # nothing a tab can't hold is ever accepted
        assert str(LONGEST_TAB_NAME) in window.status.currentMessage()


def test_a_tab_that_cannot_take_dropped_files_says_why_while_they_hang_over_it(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        window.disconnect_tab(window.sessions[0])
        window.status.clearMessage()
        drag_over_tab(window, 0, local_drag())
        assert window.status.currentMessage() == TAB_NOT_LIVE_TEXT.format(name="Shop")  # never silent (OR-7)
        assert window.tab_bar.taking == NO_TAB
        window.status.clearMessage()
        drop_on_tab(window, 0, local_drag())
        assert window.status.currentMessage() == TAB_NOT_LIVE_TEXT.format(name="Shop")  # letting go says it too
        assert window.queue.jobs == []
