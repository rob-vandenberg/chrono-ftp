"""Block FI on the screen: workspaces (FI-02). Saving every open tab under a name, opening them all again, renaming, deleting, and the
one that opens by itself at start; all from the menu of an empty part of the tab row."""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import workspaces as saved_workspaces
from chrono_ftp.config.workspaces import LONGEST_NAME
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.windowworkspaces import AT_START_MENU_TEXT, DELETE_MENU_TEXT, NO_TAB_TEXT, NO_WORKSPACE_TEXT, OPEN_MENU_TEXT, RENAME_MENU_TEXT, SAVE_TEXT
from tests.fakeservers import ssh_server
from tests.ui.test_ui_sessions import GREEN, MAGENTA, connected, settled_tab, ssh_settings, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_workspaces.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Initial: the menu beside the tabs; saving, opening, renaming and deleting a workspace, and the one that opens at start (FI-02)

__all__ = ["window"]  # the fixture, used by name


def beside_the_tabs(window: MainWindow) -> QPoint:
    return QPoint(window.tab_bar.width() - 1, window.tab_bar.height() - 1)


def item(menu: QMenu, text: str) -> QAction:
    found = [action for action in menu.actions() if action.text() == text]
    assert found, f"{text} is not in the menu: {[action.text() for action in menu.actions()]}"
    return found[0]


def submenu(menu: QMenu, text: str) -> QMenu:
    found = [action.menu() for action in menu.actions() if action.text() == text]
    assert found
    opened = found[0]
    assert isinstance(opened, QMenu)
    return opened


def types(monkeypatch: pytest.MonkeyPatch, answer: str | None) -> None:
    """What the person types in the window that asks for a name."""

    def given(window: MainWindow, title: str, label: str, start: str = "") -> str | None:
        return answer

    monkeypatch.setattr(MainWindow, "_ask_text", given)


def says_yes(monkeypatch: pytest.MonkeyPatch, yes: bool = True) -> None:
    """What the person answers the question box."""

    def answer(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(answer))


def test_the_menu_beside_the_tabs_is_the_workspaces_menu(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        menu = window.tab_menu_for(beside_the_tabs(window))
        assert menu is None  # beside the tabs there is no tab menu
        menu = window.workspaces_menu()
        for text in (SAVE_TEXT, OPEN_MENU_TEXT, RENAME_MENU_TEXT, DELETE_MENU_TEXT, AT_START_MENU_TEXT):
            item(menu, text)
        menu.deleteLater()


def test_with_no_tab_open_saving_is_greyed_with_its_reason(qtbot: QtBot, window: MainWindow) -> None:
    menu = window.workspaces_menu()
    save = item(menu, SAVE_TEXT)
    assert not save.isEnabled() and save.toolTip() == NO_TAB_TEXT  # greyed with its reason (OR-20)
    assert not submenu(menu, OPEN_MENU_TEXT).isEnabled()
    menu.deleteLater()


def test_the_open_tabs_are_saved_with_the_folders_they_are_in(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here = tmp_path / "here"
    here.mkdir()
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        types(monkeypatch, "Morning")
        window.save_workspace()
        kept = saved_workspaces.load()
        assert [workspace.name for workspace in kept.items] == ["Morning"]
        assert kept.items[0].tabs[0].connection == "Shop"
        assert kept.items[0].tabs[0].local_folder == str(here)
        assert kept.items[0].tabs[0].remote_folder == "/"
        assert "Morning" in window.status.currentMessage()


def test_saving_under_a_name_that_is_taken_asks_first_and_no_keeps_what_was_there(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        types(monkeypatch, "Morning")
        says_yes(monkeypatch, True)
        window.save_workspace()
        window.close_tab(0)
        says_yes(monkeypatch, False)
        window.save_workspace()  # nothing is open now: No keeps the workspace as it was
        assert len(saved_workspaces.load().items[0].tabs) == 1


def test_a_workspace_opens_every_tab_it_holds_in_the_folders_it_saved(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inner, here = tmp_path / "inner", tmp_path / "here"
    for folder in (inner, here):
        folder.mkdir()
    (inner / "deep.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        window.local_side.go_to(str(here))
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: "deep.txt" in window.remote_side.file_list.names())
        types(monkeypatch, "Morning")
        window.save_workspace()
        window.close_tab(0)
        assert window.sessions == []
        window.open_workspace("Morning")
        qtbot.waitUntil(lambda: bool(window.sessions))
        session = window.sessions[0]
        settled_tab(qtbot, session)
        qtbot.waitUntil(lambda: session.remote_folder == "/inner")
        assert window.local_side.folder == str(here)


def test_a_workspace_whose_connection_is_gone_says_so_and_opens_the_rest(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        types(monkeypatch, "Morning")
        window.save_workspace()
        window.close_tab(0)
        saved_connections.save([])  # the person removed the connection since
        window.open_workspace("Morning")
        assert window.sessions == []
        assert "Shop" in window.status.currentMessage()


def test_a_tab_that_is_already_open_is_not_opened_a_second_time(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        types(monkeypatch, "Morning")
        window.save_workspace()
        window.open_workspace("Morning")
        assert len(window.sessions) == 1  # one connection, one tab (D-051)
        assert "already" in window.status.currentMessage()


def test_a_workspace_is_renamed_and_a_name_that_is_taken_changes_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        types(monkeypatch, "Morning")
        window.save_workspace()
        types(monkeypatch, "Evening")
        window.save_workspace()
        types(monkeypatch, "Evening")
        window.rename_workspace("Morning")
        assert sorted(workspace.name for workspace in saved_workspaces.load().items) == ["Evening", "Morning"]
        types(monkeypatch, "Night")
        window.rename_workspace("Morning")
        assert sorted(workspace.name for workspace in saved_workspaces.load().items) == ["Evening", "Night"]


def test_deleting_a_workspace_asks_first_and_leaves_the_tabs_open(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        types(monkeypatch, "Morning")
        says_yes(monkeypatch, True)
        window.save_workspace()
        says_yes(monkeypatch, False)
        window.delete_workspace("Morning")
        assert len(saved_workspaces.load().items) == 1
        says_yes(monkeypatch, True)
        window.delete_workspace("Morning")
        assert saved_workspaces.load().items == ()
        assert len(window.sessions) == 1  # the tabs it held stay open


def test_a_workspace_can_open_by_itself_at_start_and_the_choice_is_ticked(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        types(monkeypatch, "Morning")
        window.save_workspace()
        assert window.open_start_workspace() is False  # none is chosen yet
        window.open_at_start("Morning")
        assert saved_workspaces.load().at_start == "Morning"
        menu = window.workspaces_menu()
        chosen = [action for action in submenu(menu, AT_START_MENU_TEXT).actions() if action.isChecked()]
        assert [action.text() for action in chosen] == ["Morning"]
        menu.deleteLater()
        window.close_tab(0)  # at a real start no tab is open yet
        assert window.open_start_workspace() is True
        qtbot.waitUntil(lambda: len(window.sessions) == 1)
        settled_tab(qtbot, window.sessions[0])
        window.open_at_start("")
        menu = window.workspaces_menu()
        assert item(submenu(menu, AT_START_MENU_TEXT), NO_WORKSPACE_TEXT).isChecked()
        menu.deleteLater()


def test_two_tabs_are_saved_and_opened_in_the_order_they_are_in(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        shop, other = ssh_settings(one, "Shop", GREEN), ssh_settings(two, "Other", MAGENTA)
        saved_connections.save([shop, other])
        connected(qtbot, window, shop)
        connected(qtbot, window, other)
        types(monkeypatch, "Morning")
        window.save_workspace()
        assert [tab.connection for tab in saved_workspaces.load().items[0].tabs] == ["Shop", "Other"]
        window.tab_bar.moveTab(1, 0)  # dragged the other way round after saving: the workspace keeps its own order
        window.close_tab(1)
        window.close_tab(0)
        window.open_workspace("Morning")
        qtbot.waitUntil(lambda: len(window.sessions) == 2)
        for session in window.sessions:
            settled_tab(qtbot, session)
        assert [session.connection.name for session in window.sessions] == ["Shop", "Other"]


def test_a_workspace_opens_the_two_tabs_the_person_made_on_one_connection(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "deep.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        window.duplicate_tab(window.sessions[0])
        settled_tab(qtbot, window.sessions[1])
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: window.sessions[1].remote_folder == "/inner")
        types(monkeypatch, "Morning")
        window.save_workspace()
        window.close_tab(1)
        window.close_tab(0)
        assert window.open_workspace("Morning") == 2  # the person made two on purpose and gets two back
        said = window.status.currentMessage()  # read before the connection says it is up, which the status bar says next
        qtbot.waitUntil(lambda: len(window.sessions) == 2)
        for session in window.sessions:
            settled_tab(qtbot, session)
        assert [session.connection.name for session in window.sessions] == ["Shop", "Shop"]
        qtbot.waitUntil(lambda: window.sessions[1].remote_folder == "/inner")
        assert window.sessions[0].remote_folder == "/"
        assert "2 tabs opened" in said
        assert "was open already" not in said and "one tab" not in said  # it says only what happened (G-2)


def test_a_workspace_opens_only_what_is_not_open_yet_and_says_so(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        window.duplicate_tab(window.sessions[0])
        settled_tab(qtbot, window.sessions[1])
        types(monkeypatch, "Morning")
        window.save_workspace()
        window.close_tab(1)  # one of the two stays open
        assert window.open_workspace("Morning") == 1
        said = window.status.currentMessage()
        qtbot.waitUntil(lambda: len(window.sessions) == 2)
        for session in window.sessions:
            settled_tab(qtbot, session)
        assert "1 tab opened" in said and "1 was open already" in said


def test_a_workspace_name_that_is_too_long_is_refused_when_it_is_typed(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        types(monkeypatch, "x" * (LONGEST_NAME + 1))
        window.save_workspace()
        assert saved_workspaces.load().items == ()  # never accept what would be thrown away when it is read back
        assert str(LONGEST_NAME) in window.status.currentMessage()


def test_a_workspace_at_start_whose_connections_are_gone_opens_the_connections_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = ssh_settings(server)
        saved_connections.save([settings])
        connected(qtbot, window, settings)
        types(monkeypatch, "Morning")
        window.save_workspace()
        window.open_at_start("Morning")
        window.close_tab(0)
        saved_connections.save([])  # the person removed the connection since
        assert window.open_start_workspace() is False  # nothing opened: the person gets what they would have had without it
        assert window.sessions == []
        assert "connections window" in window.status.currentMessage()
