import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable, Generator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QByteArray, QItemSelectionModel, QMimeData, QPointF, Qt
from PySide6.QtGui import QColor, QDropEvent, QGuiApplication, QPalette
from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import RemoteConnection, UnknownServer
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core import colours
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.transfers.options import Answer, Choice
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.engine.rights import rights_of
from chrono_ftp.ui.actiondialogs import NewFolderDialog
from chrono_ftp.ui.propertiesdialog import PropertiesDialog
from chrono_ftp.ui.comparedialog import CompareDialog
from chrono_ftp.ui.filelist import DRAG_MARK, LOCAL_FILES, REMOTE_FILES, FileList
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.ui.logindialog import LoginDialog
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.overwritedialog import OverwriteDialog
from chrono_ftp.ui.session import Session, same_server
from chrono_ftp.ui.transferdialog import TransferDialog
from chrono_ftp.ui.windowbase import EDIT_WAIT_TRIES
from chrono_ftp.engine.cleanup import EDIT_FOLDER_PREFIX
from chrono_ftp.ui.plaintext import NAME_SHARE
from tests.conftest import MemoryStore
from tests.fakeservers import NOT_AS_ROOT, PASSWORD, USER, SshServer, ftp_server, ssh_server

# --- Version ------------------------------------------------------------
__version__ = "test_ui_sessions.py 0.0.44"

# --- Version history ----------------------------------------------------
# v0.0.44: settled_tab: a tab is ready only once its connection is up and the listing that connection started has answered, so a test
#          never ends while its fake server is still being asked something (AGENTS.md 4.4.4)
# v0.0.44: Synchronize starts from the Start button, not Apply (D-097)
# v0.0.43: The Synchronize window of a tab and its both-ways direction, as the helpers every window test uses (block FF)
# v0.0.38: The name of the edit folders comes from engine/cleanup.py
# v0.0.37: Owner and permissions are changed in the Properties window, its rights as a chmod that sets exactly the shown boxes, for
#          everything inside through find, files first and folders deepest first, only the touched boxes; a
#          copy on the server is no longer an action only for a shell (D-061); a new folder is named in the New folder window (FM-08)
# v0.0.34: A starting folder that is gone says so calmly (FH-06)
# v0.0.33: A dropped tab is NOT_CONNECTED_TAB
# v0.0.25: A connection that already has a tab switches to it
# v0.0.13: A job is put in the queue through add, as the list of jobs is a copy now
# v0.0.12: Uploads, downloads and drops answer the transfer window, which must really come with the right folder; a file that is there
#          already gets the overwrite question; Synchronize asks it too, with the default options; after a drop the window comes once
#          the drop has finished; the target folder is noted while the window is open, as it goes after use; the edit folders a test's
#          window made go with the test; tests that need a refusal by the rights of a folder skip as root; the path of a copy with unsent
#          changes is shown whole, and unbroken in the box's details
# v0.0.11: Imports from the split main window; a test with a transfer waits for its end, not for the file, so nothing runs on after it
# v0.0.9: The optional colour
# v0.0.8: The login window
# v0.0.6: Drag and drop, edit, odd names, the buttons of the sides, the status bar, synchronize
# v0.0.5: Initial: the real window against real servers in the test process

GREEN: str = "#00aa00"
MAGENTA: str = "#aa00aa"


def yes(*args: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def transfer_window_accepted(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The transfer window comes before every upload, download and drop: the person clicks its Upload or Download button. What it
    showed as the target folder is noted then: the window itself goes once it is used."""
    shown: list[str] = []

    def accept(dialog: TransferDialog) -> int:
        shown.append(dialog.folder())
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    return shown


def trust_server(server: SshServer) -> None:
    probe = SshConnection(Connection(name="T", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
    with pytest.raises(UnknownServer) as unknown:
        probe.connect()
    probe.close()
    trust.remember_host_key(unknown.value.to_remember)


def ssh_settings(server: SshServer, name: str = "Shop", colour: str = GREEN) -> Connection:
    return Connection(name=name, host="127.0.0.1", port=server.port, user=USER, colour=colour, start_folder="/")


@pytest.fixture
def window(qtbot: QtBot, memory_store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> Generator[MainWindow]:
    memory_store.passwords["Shop"] = PASSWORD
    memory_store.passwords["Other"] = PASSWORD
    made = MainWindow()
    folders: list[str] = []  # the edit folders this test's window made, and only those
    real_mkdtemp = tempfile.mkdtemp

    def noted(suffix: str | None = None, prefix: str | None = None, dir: str | None = None) -> str:
        folder = real_mkdtemp(suffix, prefix, dir)
        if prefix == EDIT_FOLDER_PREFIX:
            folders.append(folder)
        return folder

    monkeypatch.setattr(tempfile, "mkdtemp", noted)

    def let_go(_widget: QWidget) -> None:
        made._forget_edits(list(made.edited))  # the test is over: closing has no one to ask about them

    qtbot.addWidget(made, before_close_func=let_go)
    yield made
    made.close()
    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)  # a copy the program keeps on purpose (unsent changes) is the test's own here


def settled_tab(qtbot: QtBot, session: Session) -> None:
    """A tab is ready only once its connection is up AND the request that connection started has answered. _connected sets alive and
    asks for the first listing in one and the same step, so an idle worker after alive means nothing is in flight any more. A test
    that goes on at alive alone ends while its server is still being asked something: the fake server then stops under that request,
    the window says calmly that the connection was lost, and nobody answers that box (AGENTS.md 4.4.4)."""
    qtbot.waitUntil(lambda: session.alive and not session.connecting and session.worker.idle())


def connected(qtbot: QtBot, window: MainWindow, connection: Connection) -> None:
    window.open_connection(connection)
    session = window.sessions[-1]
    qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")
    settled_tab(qtbot, session)


def synchronize_window(qtbot: QtBot, window: MainWindow) -> CompareDialog:
    """The Synchronize window of the current tab, with its first scan done."""
    window.actions_by_name["compare"].trigger()
    found = [child for child in window.findChildren(CompareDialog) if child.isVisible()]
    assert found
    dialog = found[-1]  # the one just opened, never an earlier one that is still being deleted
    qtbot.waitUntil(lambda: not dialog.scanning)
    return dialog


def both_ways(qtbot: QtBot, dialog: CompareDialog, rows: int) -> None:
    """The window opens on the direction of the side with the focus (FF-11); these tests want both directions."""
    qtbot.waitUntil(lambda: not dialog.scanning)
    dialog.choices.both.setChecked(True)
    dialog.again_button.click()
    qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == rows)


def names_on_the_right(window: MainWindow) -> list[str]:
    return window.remote_side.file_list.names()


def select(window: MainWindow, *names: str) -> None:
    selection = window.remote_side.file_list.selectionModel()
    assert selection is not None
    selection.clear()
    for name in names:
        selection.select(window.remote_side.file_list.index_of(name), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def base_of(window: MainWindow) -> QColor:
    return window.remote_side.file_list.palette().color(QPalette.ColorRole.Base)


def test_a_connection_opens_in_a_tab_in_its_colour(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "site.html").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "site.html" in names_on_the_right(window))
        assert window.tab_bar.count() == 1
        assert window.tab_bar.background_of(0) == GREEN
        assert base_of(window) == QColor(GREEN)
        assert window.remote_side.path_line.text() == "/"


def test_text_and_selection_stay_readable_on_the_colour(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server, colour="#ffffff"))
        palette = window.remote_side.file_list.palette()
        assert palette.color(QPalette.ColorRole.Text) == QColor(colours.DARK_TEXT)
        assert palette.color(QPalette.ColorRole.Highlight) != palette.color(QPalette.ColorRole.HighlightedText)


def test_an_unknown_server_asks_and_no_means_no_connection(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def no(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    with ssh_server(tmp_path) as server:
        window.open_connection(ssh_settings(server))
        qtbot.waitUntil(lambda: bool(asked))
        assert "SHA256:" in asked[0]
        assert not window.sessions[0].alive
        assert window.tab_bar.background_of(0) == colours.NOT_CONNECTED_TAB


def test_an_unknown_server_yes_connects_and_is_remembered(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QMessageBox, "question", yes)
    with ssh_server(tmp_path) as server:
        connected(qtbot, window, ssh_settings(server))
        assert trust.known_hosts_path().exists()


def test_a_tab_keeps_its_own_local_and_remote_folder(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    one, two, left_one, left_two = tmp_path / "one", tmp_path / "two", tmp_path / "l1", tmp_path / "l2"
    for folder in (one, two, left_one, left_two):
        folder.mkdir()
    (one / "first.txt").write_text("x", encoding="utf-8")
    (two / "second.txt").write_text("x", encoding="utf-8")
    with ssh_server(one) as first, ssh_server(two) as second:
        trust_server(first)
        trust_server(second)
        connected(qtbot, window, ssh_settings(first, "Shop", GREEN))
        window.local_side.go_to(str(left_one))
        connected(qtbot, window, ssh_settings(second, "Other", MAGENTA))
        window.local_side.go_to(str(left_two))
        assert base_of(window) == QColor(MAGENTA)
        window.tab_bar.setCurrentIndex(0)
        assert window.local_side.folder == str(left_one)
        assert base_of(window) == QColor(GREEN)
        assert "first.txt" in names_on_the_right(window)
        assert window.tab_bar.background_of(1) == colours.dimmed(MAGENTA)
        window.tab_bar.setCurrentIndex(1)
        assert window.local_side.folder == str(left_two)
        assert "second.txt" in names_on_the_right(window)


def test_closing_the_last_tab_makes_the_right_side_grey(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.close_tab(0)
        assert base_of(window) == QColor(colours.NOT_CONNECTED)
        assert window.remote_side.path_line.text() == "Not connected"
        assert names_on_the_right(window) == []


def visible(window: MainWindow, name: str) -> bool:
    return window.actions_by_name[name].isVisible()


def enabled(window: MainWindow, name: str) -> bool:
    return window.actions_by_name[name].isEnabled()


def test_shell_actions_are_not_shown_on_ftp(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        for name in ("search", "terminal"):
            assert not visible(window, name), name
        for name in ("upload", "rename", "delete", "new_folder", "properties", "duplicate", "link"):
            assert visible(window, name), name
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        assert enabled(window, "rename") and enabled(window, "delete") and enabled(window, "properties")
        assert not enabled(window, "duplicate") and window.actions_by_name["duplicate"].toolTip()  # greyed with its reason: no SITE CPFR here
        assert not enabled(window, "link") and window.actions_by_name["link"].toolTip()


def test_shell_actions_are_greyed_on_sftp_without_a_shell(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        for name in ("search", "terminal"):
            assert visible(window, name), name
            assert not enabled(window, name), name
        assert enabled(window, "rename") and enabled(window, "properties")
        assert enabled(window, "duplicate")  # this server copies a file itself (copy-data), without a shell


def test_shell_actions_work_with_a_shell(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        assert enabled(window, "properties") and enabled(window, "search") and enabled(window, "duplicate")


def text_answer(text: str) -> Callable[..., tuple[str, bool]]:
    def answer(*args: object) -> tuple[str, bool]:
        return text, True

    return answer


def folder_named(name: str) -> Callable[[NewFolderDialog], int]:
    def named(dialog: NewFolderDialog) -> int:
        dialog.name.setText(name)
        return NewFolderDialog.DialogCode.Accepted.value

    return named


def test_rename_new_folder_and_delete_from_the_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        monkeypatch.setattr(QInputDialog, "getText", text_answer("b.txt"))
        window.actions_by_name["rename"].trigger()
        qtbot.waitUntil(lambda: "b.txt" in names_on_the_right(window))
        monkeypatch.setattr(NewFolderDialog, "exec", folder_named("made"))
        window.actions_by_name["new_folder"].trigger()
        qtbot.waitUntil(lambda: "made" in names_on_the_right(window))
        assert (tmp_path / "made").is_dir()
        select(window, "b.txt")
        monkeypatch.setattr(QMessageBox, "question", yes)
        window.actions_by_name["delete"].trigger()
        qtbot.waitUntil(lambda: "b.txt" not in names_on_the_right(window))
        assert not (tmp_path / "b.txt").exists()


def test_delete_asks_first(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        asked: list[str] = []

        def no(*args: object) -> QMessageBox.StandardButton:
            asked.append(str(args[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", no)
        window.actions_by_name["delete"].trigger()
        assert "a.txt" in asked[0]
        assert (tmp_path / "a.txt").exists()


def test_a_recursive_change_shows_the_exact_command_and_runs_it_on_the_whole_tree(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = tmp_path / "my tree"
    (tree / "deep").mkdir(parents=True)
    (tree / "deep" / "f.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        # The fake shell is not jailed like its SFTP, so the listing's path is the real one here.
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/"))
        qtbot.waitUntil(lambda: "my tree" in names_on_the_right(window))
        select(window, "my tree")
        window.remote_side.folder = str(tmp_path)

        def accepted(dialog: PropertiesDialog) -> int:
            dialog.rights.set_rights(rights_of(0o700))
            dialog.inside.setChecked(True)
            return PropertiesDialog.DialogCode.Accepted.value

        shown: list[str] = []

        def confirm(*args: object) -> QMessageBox.StandardButton:
            shown.append(str(args[2]))
            return QMessageBox.StandardButton.Yes

        def ignored(*args: object) -> None:
            pass  # the jailed SFTP can't list the real path afterwards: only the command is under test here

        monkeypatch.setattr(PropertiesDialog, "exec", accepted)
        monkeypatch.setattr(QMessageBox, "question", confirm)
        monkeypatch.setattr(QMessageBox, "information", ignored)
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: bool(shown))  # the names of users and groups come from the server first
        assert f"find '{tree}' ! -type d ! -type l -exec chmod -- " in shown[0]  # files first, with only the touched boxes
        assert f"find '{tree}' -depth -type d -exec chmod -- " in shown[0]  # folders deepest first
        assert ",-t " not in shown[0] and "u-s" not in shown[0] and "u+r" not in shown[0]  # boxes that did not change are never in it
        qtbot.waitUntil(lambda: stat.S_IMODE((tree / "deep" / "f.txt").stat().st_mode) & 0o077 == 0)


def test_no_means_no_command_runs(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    before = stat.S_IMODE((tmp_path / "a.txt").stat().st_mode)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")

        asked: list[str] = []

        def accepted(dialog: PropertiesDialog) -> int:
            dialog.rights.set_rights(rights_of(0o700))
            return PropertiesDialog.DialogCode.Accepted.value

        def no(*args: object) -> QMessageBox.StandardButton:
            asked.append(str(args[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(PropertiesDialog, "exec", accepted)
        monkeypatch.setattr(QMessageBox, "question", no)
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: bool(asked))
        assert stat.S_IMODE((tmp_path / "a.txt").stat().st_mode) == before


def test_a_download_lands_on_the_left_and_shows_in_the_queue(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shown = transfer_window_accepted(monkeypatch)
    server_side, local = tmp_path / "server", tmp_path / "local"
    server_side.mkdir()
    local.mkdir()
    (server_side / "report.txt").write_text("hello", encoding="utf-8")
    with ftp_server(server_side) as port:
        window.local_side.go_to(str(local))
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        qtbot.waitUntil(lambda: "report.txt" in names_on_the_right(window))
        select(window, "report.txt")
        window.actions_by_name["download"].trigger()
        qtbot.waitUntil(lambda: bool(window.queue.jobs) and window.queue.jobs[0].state == State.DONE)
        assert (local / "report.txt").read_text(encoding="utf-8") == "hello"
        assert len(shown) == 1 and shown[0] == str(local)
        qtbot.waitUntil(lambda: window.queue_view.rowCount() == 1)


def test_a_dropped_connection_turns_the_tab_grey_and_offers_reconnect(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[str] = []

    def note(*args: object) -> None:
        told.append(str(args[2]))

    monkeypatch.setattr(QMessageBox, "information", note)
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
    session = window.sessions[0]
    assert session.remote is not None
    window.refresh()
    qtbot.waitUntil(lambda: not session.alive, timeout=10000)
    assert window.tab_bar.background_of(0) == colours.NOT_CONNECTED_TAB
    assert base_of(window) == QColor(colours.NOT_CONNECTED)
    assert enabled(window, "reconnect")
    assert len(told) == 1


def test_the_tree_opens_down_to_the_starting_folder_and_keeps_its_path(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    deep = root / "usr" / "share"
    deep.mkdir(parents=True)
    (root / "etc").mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        with qtbot.waitExposed(window):
            window.show()
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, colour=GREEN, start_folder="/usr/share"))
        qtbot.waitUntil(lambda: window.remote_side.folder == "/usr/share")
        current = window.remote_side.tree.currentIndex()
        qtbot.waitUntil(lambda: window.remote_side.tree_model.rowCount() == 1 and window.remote_side.tree_model.item(0).rowCount() == 2)
        assert window.remote_side.tree.currentIndex().data() == "share"
        assert current.isValid()


def test_delete_acts_on_the_folder_shown_when_it_was_chosen(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "a" / "index.html").write_text("a", encoding="utf-8")
    (root / "b" / "index.html").write_text("b", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/a"))
        qtbot.waitUntil(lambda: "index.html" in names_on_the_right(window))
        select(window, "index.html")
        session = window.sessions[0]

        def other_folder_arrives_during_the_question(*args: object) -> QMessageBox.StandardButton:
            window.open_remote_folder("/b")
            qtbot.waitUntil(lambda: session.remote_folder == "/b")
            return QMessageBox.StandardButton.Yes

        monkeypatch.setattr(QMessageBox, "question", other_folder_arrives_during_the_question)
        window.actions_by_name["delete"].trigger()
        qtbot.waitUntil(lambda: not (root / "a" / "index.html").exists())
        assert (root / "b" / "index.html").exists()


def test_a_refresh_keeps_the_selection(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        (root / "new.txt").write_text("n", encoding="utf-8")
        window.refresh()
        qtbot.waitUntil(lambda: "new.txt" in names_on_the_right(window))
        assert [entry.name for entry in window.remote_side.selected_entries()] == ["a.txt"]


def test_closing_a_tab_while_it_connects_leaves_nothing_behind(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        window.open_connection(ssh_settings(server))
        session = window.sessions[0]
        window.close_tab(0)
        qtbot.waitUntil(lambda: not session.connecting)
        assert not session.alive
        assert window.tab_bar.count() == 0


def test_closing_another_tab_keeps_the_selection_of_this_one(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server, "Other", MAGENTA))
        connected(qtbot, window, ssh_settings(server, "Shop", GREEN))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.close_tab(0)
        assert window.current() is window.sessions[0]
        assert [entry.name for entry in window.remote_side.selected_entries()] == ["a.txt"]


def test_a_wrong_starting_folder_opens_the_home_folder_and_says_so_calmly(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[str] = []

    def note(*args: object) -> QMessageBox.StandardButton:
        told.append(str(args[2]))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/gone"))
        assert window.sessions[0].remote_folder == "/"
        qtbot.waitUntil(lambda: bool(told))
        assert "/gone" in told[0] and "home folder" in told[0]


def test_a_typed_password_is_stored_only_after_the_server_accepted_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords.clear()
    answers = ["wrong", PASSWORD]
    stored_at_second_ask: list[dict[str, str]] = []

    def typed(dialog: LoginDialog) -> int:
        stored_at_second_ask.append(dict(memory_store.passwords))
        dialog.password.setText(answers.pop(0))
        return LoginDialog.DialogCode.Accepted.value

    monkeypatch.setattr(LoginDialog, "exec", typed)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
    assert stored_at_second_ask == [{}, {}]
    assert memory_store.passwords == {"Shop": PASSWORD}


def test_a_connection_without_a_user_asks_first_and_keeps_the_name_only_after_the_login(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords.clear()
    with ssh_server(tmp_path) as server:
        trust_server(server)
        settings = replace(ssh_settings(server), user="")
        saved_connections.save([settings])
        users_at_ask: list[str] = []

        def typed(dialog: LoginDialog) -> int:
            users_at_ask.append(saved_connections.load().connections[0].user)
            dialog.user.setText(USER)
            dialog.password.setText(PASSWORD)
            return LoginDialog.DialogCode.Accepted.value

        monkeypatch.setattr(LoginDialog, "exec", typed)
        connected(qtbot, window, settings)
    assert users_at_ask == [""]
    assert saved_connections.load().connections[0].user == USER


def test_cancel_on_the_login_window_tries_nothing_and_changes_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path, memory_store: MemoryStore) -> None:
    memory_store.passwords.clear()
    cancelled: list[str] = []

    def cancel(dialog: LoginDialog) -> int:
        cancelled.append(dialog.user.text())
        return LoginDialog.DialogCode.Rejected.value

    with pytest.MonkeyPatch.context() as patch, ssh_server(tmp_path) as server:
        patch.setattr(LoginDialog, "exec", cancel)
        trust_server(server)
        window.open_connection(replace(ssh_settings(server), user=""))
        session = window.sessions[0]
        assert cancelled == [""]
        assert session.remote is None and not session.connecting
        assert session.connection.user == ""
        assert window.actions_by_name["reconnect"].isEnabled()


def test_cancel_after_a_refused_try_goes_back_to_the_user_of_before(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords.clear()
    answers = iter([("other", "wrong"), None])

    def answer(dialog: LoginDialog) -> int:
        given = next(answers)
        if given is None:
            return LoginDialog.DialogCode.Rejected.value
        dialog.user.setText(given[0])
        dialog.password.setText(given[1])
        return LoginDialog.DialogCode.Accepted.value

    monkeypatch.setattr(LoginDialog, "exec", answer)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        window.open_connection(ssh_settings(server))
        session = window.sessions[0]
        qtbot.waitUntil(lambda: not session.connecting and session.connection.user == USER and not session.typed_user, timeout=5000)
        assert session.password is None and not session.typed_password


def test_a_list_keeps_its_selection_when_a_login_keeps_a_user(qtbot: QtBot, window: MainWindow) -> None:
    saved_connections.save([Connection(name="Shop", host="h")])
    window.show_connection_list()
    view = window.connection_list
    assert view is not None
    view.list.setCurrentRow(0)
    view.user_accepted("Shop", "anna")
    assert view.selected() is not None and view.user.text() == "anna"
    view.close()


def test_a_refused_login_shows_the_known_user_and_can_change_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords.clear()
    seen: list[str] = []

    def typed(dialog: LoginDialog) -> int:
        seen.append(dialog.user.text())
        dialog.password.setText(PASSWORD)
        return LoginDialog.DialogCode.Accepted.value

    monkeypatch.setattr(LoginDialog, "exec", typed)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
    assert seen == [USER]


def test_a_server_that_is_down_asks_for_no_password(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords.clear()
    told: list[str] = []

    def note(*args: object) -> None:
        told.append(str(args[2]))

    monkeypatch.setattr(QMessageBox, "information", note)
    window.open_connection(Connection(name="Shop", host="127.0.0.1", port=1, user=USER))
    qtbot.waitUntil(lambda: bool(told))
    assert "could not be reached" in told[0]


def test_an_idle_connection_that_drops_goes_grey(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
    session = window.sessions[0]
    window.check_connections()
    qtbot.waitUntil(lambda: not session.alive, timeout=10000)
    assert window.tab_bar.background_of(0) == colours.NOT_CONNECTED_TAB


def test_closing_a_tab_with_a_running_transfer_asks_first(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        assert session.remote is not None
        job = Job(session.remote, session.connection, "/nothing", "/", True, "x")
        job.state = State.RUNNING
        window.queue.add(job)  # in this state the queue never runs it
        asked: list[str] = []

        def no(*args: object) -> QMessageBox.StandardButton:
            asked.append(str(args[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", no)
        window.close_tab(0)
        assert len(asked) == 1 and window.tab_bar.count() == 1
        job.state = State.DONE


def test_arrow_keys_in_the_remote_tree_open_the_folder(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "a").mkdir(parents=True)
    with ssh_server(root) as server:
        trust_server(server)
        with qtbot.waitExposed(window):
            window.show()
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a" in names_on_the_right(window))
        top = window.remote_side.tree_model.item(0)
        window.remote_side.tree.setCurrentIndex(top.child(0).index())
        qtbot.waitUntil(lambda: window.remote_side.folder == "/a")


def test_a_selected_link_can_not_get_a_recursive_change(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    (root / "real").mkdir(parents=True)
    (root / "link").symlink_to(root / "real")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "link" in names_on_the_right(window))
        seen: list[tuple[bool, bool, bool]] = []

        def looked(dialog: PropertiesDialog) -> int:
            seen.append((dialog.rights.isEnabled(), dialog.inside.isEnabled(), dialog.owner.isEnabled() or dialog.group.isEnabled()))
            return PropertiesDialog.DialogCode.Rejected.value

        monkeypatch.setattr(PropertiesDialog, "exec", looked)
        select(window, "real")
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: len(seen) == 1)
        assert seen[0][0] and seen[0][1]
        select(window, "link")
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: len(seen) == 2)
        assert seen[1] == (False, False, False)  # shown, but nothing can go through the link


def test_a_download_asks_before_it_replaces_a_local_file(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shown = transfer_window_accepted(monkeypatch)
    server_side, local = tmp_path / "server", tmp_path / "local"
    server_side.mkdir()
    local.mkdir()
    (server_side / "a.txt").write_text("new", encoding="utf-8")
    (local / "a.txt").write_text("old", encoding="utf-8")
    asked: list[str] = []

    def no(dialog: OverwriteDialog) -> int:
        asked.append(dialog.conflict.name)
        dialog.answer = Answer(Choice.NO)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(OverwriteDialog, "exec", no)
    with ftp_server(server_side) as port:
        window.local_side.go_to(str(local))
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["download"].trigger()
        qtbot.waitUntil(lambda: transfers_ended(window))
    assert asked == ["a.txt"]
    assert len(shown) == 1 and shown[0] == str(local)
    assert [job.state for job in window.queue.jobs] == [State.DONE]  # the one job the transfer window started; No adds none and fetches nothing
    assert sorted(path.name for path in local.iterdir()) == ["a.txt"]  # no part file either
    assert (local / "a.txt").read_text(encoding="utf-8") == "old"


def test_rename_refuses_a_name_that_is_taken(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    told: list[str] = []

    def note(*args: object) -> None:
        told.append(str(args[2]))

    monkeypatch.setattr(QMessageBox, "information", note)
    monkeypatch.setattr(QInputDialog, "getText", text_answer("b.txt"))
    with ftp_server(root) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["rename"].trigger()
    assert len(told) == 1
    assert (root / "a.txt").exists() and (root / "b.txt").read_text(encoding="utf-8") == "b"


def text_of(path: Path) -> str:
    """A replaced file is away for a moment during the swap: that is no answer yet."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def transfers_ended(window: MainWindow) -> bool:
    """A file is on the other side before its transfer has ended: a test waits for the end, so nothing still runs when it is over."""
    return bool(window.queue.jobs) and all(job.state not in (State.WAITING, State.RUNNING) for job in window.queue.jobs)


def mark(kind: str) -> QMimeData:
    mime = QMimeData()
    mime.setData(kind, QByteArray(DRAG_MARK))
    return mime


def dropped(view: FileList, kind: str, row_name: str | None = None) -> None:
    position = QPointF(view.visualRect(view.index_of(row_name)).center()) if row_name else QPointF(5, view.viewport().height() - 5)
    mime = mark(kind)  # the event does not keep it alive
    view.dropEvent(QDropEvent(position, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def pick_local(window: MainWindow, *names: str) -> None:
    selection = window.local_side.file_list.selectionModel()
    assert selection is not None
    for name in names:
        selection.select(window.local_side.file_list.index_of(name), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def test_a_file_dragged_to_the_right_lands_in_the_folder_it_fell_on(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shown = transfer_window_accepted(monkeypatch)
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "inner").mkdir(parents=True)
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "inner" in names_on_the_right(window))
        pick_local(window, "a.txt")
        dropped(window.remote_side.file_list, LOCAL_FILES, "inner")
        assert shown == []  # the window comes once the drop has finished, never inside it
        qtbot.waitUntil(lambda: transfers_ended(window))
        assert (root / "inner" / "a.txt").exists()
        assert len(shown) == 1 and shown[0] == "/inner"


def test_a_file_dragged_to_the_left_lands_in_the_folder_it_fell_on(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shown = transfer_window_accepted(monkeypatch)
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    (here / "inner").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        dropped(window.local_side.file_list, REMOTE_FILES, "inner")
        assert shown == []  # the window comes once the drop has finished, never inside it
        qtbot.waitUntil(lambda: transfers_ended(window))
        assert (here / "inner" / "a.txt").exists()
        assert len(shown) == 1 and shown[0] == str(here / "inner")


def test_a_drag_within_the_right_side_moves_on_the_server(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "inner").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        dropped(window.remote_side.file_list, REMOTE_FILES, "inner")
        qtbot.waitUntil(lambda: (root / "inner" / "a.txt").exists())
        assert not (root / "a.txt").exists()


def test_an_edited_file_goes_back_to_the_server_on_every_save(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        Path(opened[0]).write_text("new text", encoding="utf-8")
        qtbot.waitUntil(lambda: text_of(root / "a.txt") == "new text")
        qtbot.waitUntil(lambda: all(job.state == State.DONE for job in window.queue.jobs))


def test_a_name_that_is_not_utf8_can_be_copied_and_deleted_on_a_real_server(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    name = "b\udcffd.txt"
    Path(root, name).write_text("content", encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "question", yes)
    shown = transfer_window_accepted(monkeypatch)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "b\ufffdd.txt" in names_on_the_right(window))
        select(window, name)
        window.actions_by_name["download"].trigger()
        qtbot.waitUntil(lambda: transfers_ended(window))
        assert Path(here, name).read_text(encoding="utf-8") == "content"
        assert len(shown) == 1 and shown[0] == str(here)
        window.actions_by_name["delete"].trigger()
        qtbot.waitUntil(lambda: not Path(root, name).exists())


def test_a_folder_with_a_name_that_is_not_utf8_can_be_entered(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    inner = root / "f\udcffd"
    inner.mkdir(parents=True)
    (inner / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "f\ufffdd" in names_on_the_right(window))
        select(window, "f\udcffd")
        window.remote_side.file_list.opened.emit(window.remote_side.selected_entries()[0])
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))


def test_a_listing_with_a_name_that_is_not_utf8_keeps_an_ftp_connection_alive(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "plain.txt").write_text("a", encoding="utf-8")
    Path(root, "b\udcffd.txt").write_text("a", encoding="utf-8")
    with ftp_server(root) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        qtbot.waitUntil(lambda: "plain.txt" in names_on_the_right(window))
        assert window.sessions[0].alive


def test_the_remote_side_walks_with_its_buttons_and_home_is_where_the_login_starts(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "inner").mkdir(parents=True)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        side = window.remote_side
        qtbot.waitUntil(lambda: "inner" in names_on_the_right(window))
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: side.folder == "/inner")
        side.navigation.buttons["back"].trigger()
        qtbot.waitUntil(lambda: side.folder == "/")
        side.navigation.buttons["forward"].trigger()
        qtbot.waitUntil(lambda: side.folder == "/inner")
        side.navigation.buttons["home"].trigger()
        qtbot.waitUntil(lambda: side.folder == window.sessions[0].home_folder)
        side.path_line.setText("/inner/")
        side.path_line.returnPressed.emit()
        qtbot.waitUntil(lambda: side.folder == "/inner")


def test_the_status_bar_shows_the_protocol_the_lock_and_the_time(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window._show_connection_state()
        assert window.protocol_label.text() == "SFTP"
        assert window.lock_label.isVisibleTo(window)
        assert window.time_label.text().startswith("0:00:")


def test_plain_ftp_shows_no_lock(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ftp_server(root) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        window._show_connection_state()
        assert window.protocol_label.text() == "FTP"
        assert not window.lock_label.isVisibleTo(window)


def test_synchronize_copies_only_what_is_ticked_and_deletes_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "shared").mkdir(parents=True)
    (here / "shared").mkdir(parents=True)
    (here / "mine.txt").write_text("mine", encoding="utf-8")
    (root / "theirs.txt").write_text("theirs", encoding="utf-8")
    (here / "shared" / "deep.txt").write_text("deep", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 3)
        dialog.start_button.click()
        qtbot.waitUntil(lambda: (root / "mine.txt").exists() and (here / "theirs.txt").exists() and (root / "shared" / "deep.txt").exists())
        qtbot.waitUntil(lambda: all(job.state == State.DONE for job in window.queue.jobs))
        assert (root / "theirs.txt").exists() and (here / "mine.txt").exists()


def test_back_to_a_folder_that_is_refused_leaves_the_history_as_it_was(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    (root / "gone").mkdir(parents=True)
    (root / "next").mkdir()
    told: list[str] = []

    def note(_parent: object, _title: str, text: str) -> None:
        told.append(text)

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        side = window.remote_side
        window.open_remote_folder("/gone")
        qtbot.waitUntil(lambda: side.folder == "/gone")
        window.open_remote_folder("/")
        qtbot.waitUntil(lambda: side.folder == "/")
        (root / "gone").rmdir()
        side.navigation.buttons["back"].trigger()
        qtbot.waitUntil(lambda: bool(told))
        assert side.history.behind[-1] == "/gone" and side.history.ahead == []
        window.open_remote_folder("/next")
        qtbot.waitUntil(lambda: side.folder == "/next")
        assert side.history.behind[-1] == "/"


def test_refresh_after_a_refused_folder_shows_the_folder_on_the_screen(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    told: list[str] = []

    def note(_parent: object, _title: str, text: str) -> None:
        told.append(text)

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.open_remote_folder("/nowhere")
        qtbot.waitUntil(lambda: bool(told))
        (root / "new.txt").write_text("x", encoding="utf-8")
        window.refresh()
        qtbot.waitUntil(lambda: "new.txt" in names_on_the_right(window))
        assert len(told) == 1


def test_a_finished_upload_does_not_undo_the_move_the_person_asked_for(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "inner").mkdir(parents=True)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        window.open_remote_folder("/inner")
        window._reload(session)
        qtbot.waitUntil(lambda: window.remote_side.folder == "/inner")


def test_opening_a_file_in_the_list_edits_that_file_not_the_selection(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "b.txt" in names_on_the_right(window))
        select(window, "a.txt")
        view = window.remote_side.file_list
        view.opened.emit(view.files.rows[view.index_of("b.txt").row()])
        qtbot.waitUntil(lambda: bool(opened))
        assert Path(opened[0]).name == "b.txt"


def test_a_save_while_the_connection_is_gone_goes_after_reconnect(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        session = window.sessions[0]
        session.alive = False
        Path(opened[0]).write_text("new", encoding="utf-8")
        qtbot.waitUntil(lambda: window.edited[opened[0]].unsent and not window.edit_settle.isActive())
        assert (root / "a.txt").read_text(encoding="utf-8") == "old"
        session.alive = True
        window.edit_settle.start()
        qtbot.waitUntil(lambda: transfers_ended(window))
        assert text_of(root / "a.txt") == "new"


def test_closing_a_tab_with_a_file_open_for_editing_asks_first(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    opened: list[str] = []
    asked: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)

    def no(_parent: object, _title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.No

    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        monkeypatch.setattr(QMessageBox, "question", no)
        window.close_tab(0)
        assert "a.txt" in asked[0] and len(window.sessions) == 1
        assert "What you save after this" in asked[0]
        monkeypatch.setattr(QMessageBox, "question", yes)
        window.close_tab(0)
        assert window.sessions == [] and window.edited == {}
        assert not Path(opened[0]).exists()


def test_each_tab_keeps_its_own_history_and_filter(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "inner").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        side = window.remote_side
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: side.folder == "/inner")
        connected(qtbot, window, ssh_settings(server, "Other", MAGENTA))
        qtbot.waitUntil(lambda: side.folder == "/")
        assert side.history.behind == []
        window.sessions[1].name_mask = "*.png"
        window.tab_bar.setCurrentIndex(0)
        qtbot.waitUntil(lambda: side.folder == "/inner")
        assert side.history.behind == ["/"]
        assert side.file_list.name_mask == ""
        window.tab_bar.setCurrentIndex(1)
        assert side.file_list.name_mask == "*.png"


def test_a_name_with_a_slash_is_refused_on_the_server(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    told: list[str] = []

    def note(_parent: object, _title: str, text: str) -> None:
        told.append(text)

    monkeypatch.setattr(QMessageBox, "information", note)
    monkeypatch.setattr(NewFolderDialog, "exec", folder_named("../outside"))
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.actions_by_name["new_folder"].trigger()
        assert told


def editing(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch, name: str = "a.txt") -> str:
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    qtbot.waitUntil(lambda: name in names_on_the_right(window))
    select(window, name)
    window.actions_by_name["edit"].trigger()
    qtbot.waitUntil(lambda: bool(opened))
    return opened[0]


@NOT_AS_ROOT
def test_a_refused_send_asks_and_try_again_sends_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    asked: list[str] = []

    def again(box: QMessageBox) -> int:
        asked.append(box.detailedText())  # the copy's path, whole, to copy
        root.chmod(0o755)  # the person put the server right
        return QMessageBox.StandardButton.Yes.value

    monkeypatch.setattr(QMessageBox, "exec", again)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        root.chmod(0o555)
        try:
            Path(copy).write_text("new", encoding="utf-8")
            qtbot.waitUntil(lambda: text_of(root / "a.txt") == "new")
        finally:
            root.chmod(0o755)
        assert asked[0] == copy
        qtbot.waitUntil(lambda: not window.edited[copy].pending)


def test_a_copy_that_never_reached_the_server_is_kept_when_its_edit_ends(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        Path(copy).write_text("new", encoding="utf-8")
        window.edited[copy].unsent = True
        window._forget_edits([copy])
        assert Path(copy).read_text(encoding="utf-8") == "new"
        shutil.rmtree(Path(copy).parent)


@NOT_AS_ROOT
def test_a_refused_rename_leaves_the_edit_going(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    told: list[str] = []

    def note(_parent: object, _title: str, text: str) -> None:
        told.append(text)

    def answer(*_args: object) -> str:
        return "b.txt"

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        monkeypatch.setattr(window, "_ask_text", answer)
        root.chmod(0o555)
        try:
            window.actions_by_name["rename"].trigger()
            qtbot.waitUntil(lambda: bool(told))
        finally:
            root.chmod(0o755)
        assert copy in window.edited


def test_a_rename_in_another_tab_of_the_same_server_ends_the_edit_too(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    told: list[str] = []

    def note(_parent: object, _title: str, text: str) -> None:
        told.append(text)

    def answer(*_args: object) -> str:
        return "b.txt"

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        editing(qtbot, window, monkeypatch)
        connected(qtbot, window, ssh_settings(server, "Other", MAGENTA))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        monkeypatch.setattr(window, "_ask_text", answer)
        window.actions_by_name["rename"].trigger()
        qtbot.waitUntil(lambda: window.edited == {})


def test_a_stopped_send_goes_only_with_a_new_save(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        edited = window.edited[copy]
        edited.job = Job(cast(RemoteConnection, window.sessions[0].remote), window.sessions[0].connection, copy, "/", True, "a.txt", state=State.STOPPED)
        window._edit_job_ended(edited.job)
        assert edited.unsent and edited.held
        window._send_edited_files()
        assert edited.job is None and edited.unsent
        window._edited_file_changed(copy)
        assert not edited.held


def test_closing_a_tab_says_when_a_save_is_not_on_the_server_yet(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    asked: list[str] = []

    def no(box: QMessageBox) -> int:
        asked.append(box.detailedText())
        return QMessageBox.StandardButton.No.value

    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        window.edited[copy].unsent = True
        monkeypatch.setattr(QMessageBox, "exec", no)
        window.close_tab(0)
        assert asked[0] == copy
        window.edited[copy].unsent = False


def test_a_tab_closed_during_the_download_of_an_edit_opens_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        worker = window.sessions[0].worker
        window.close_tab(0)
        assert window.sessions == []
        finished = threading.Event()
        worker.submit(finished.set, lambda _nothing: None, lambda _error: None)
        qtbot.wait(300)
        assert opened == [] and window.edited == {}


def test_no_on_the_question_about_edits_leaves_the_transfers_running(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    answers = iter([QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])

    def answer(*_args: object) -> QMessageBox.StandardButton:
        return next(answers)

    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        editing(qtbot, window, monkeypatch)
        job = Job(cast(RemoteConnection, window.sessions[0].remote), window.sessions[0].connection, "/nowhere", "/", True, "x", state=State.RUNNING)
        window.queue.add(job)  # in this state the queue never runs it
        monkeypatch.setattr(QMessageBox, "question", answer)
        window.close_tab(0)
        assert not job.stop_asked.is_set()
        job.state = State.DONE


def test_closing_the_window_with_a_file_open_for_editing_asks_first(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    asked: list[str] = []

    def no(_parent: object, _title: str, text: str) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.No

    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        monkeypatch.setattr(QMessageBox, "question", no)
        assert not window.close()
        assert "a.txt" in asked[0]
        assert Path(copy).exists()


def test_a_copy_that_stays_away_ends_the_waiting(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        Path(copy).unlink()
        window.edited[copy].unsent = True
        window.edited[copy].waited = EDIT_WAIT_TRIES - 1
        window._send_edited_files()
        assert window.edited == {}
        assert not os.path.lexists(os.path.dirname(copy))  # nothing is left in its folder, so the folder goes too


def test_a_save_never_makes_a_renamed_file_again(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")

    def note(*_args: object) -> None:
        pass

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        editing(qtbot, window, monkeypatch)

        def answer(*_args: object) -> str:
            return "b.txt"

        monkeypatch.setattr(window, "_ask_text", answer)
        window.actions_by_name["rename"].trigger()
        qtbot.waitUntil(lambda: window.edited == {})
        assert (root / "b.txt").exists()


def test_a_move_refused_halfway_ends_the_edit_of_the_file_that_did_move(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    (root / "t").mkdir(parents=True)
    (root / "a.txt").write_text("old", encoding="utf-8")
    (root / "z.txt").write_text("z", encoding="utf-8")
    (root / "t" / "z.txt").write_text("there", encoding="utf-8")

    def note(*_args: object) -> None:
        pass

    def answer(*_args: object) -> str:
        return "/t"

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        editing(qtbot, window, monkeypatch)
        select(window, "a.txt", "z.txt")
        monkeypatch.setattr(window, "_ask_text", answer)
        window.actions_by_name["move"].trigger()
        qtbot.waitUntil(lambda: window.edited == {})
        assert (root / "t" / "a.txt").exists()


def test_a_renamed_parent_folder_ends_the_edit_of_what_is_inside(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_text("old", encoding="utf-8")

    def note(*_args: object) -> None:
        pass

    def answer(*_args: object) -> str:
        return "live"

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "sub" in names_on_the_right(window))
        window.open_remote_folder("/sub")
        editing(qtbot, window, monkeypatch)
        window.open_remote_folder("/")
        qtbot.waitUntil(lambda: "sub" in names_on_the_right(window))
        select(window, "sub")
        monkeypatch.setattr(window, "_ask_text", answer)
        window.actions_by_name["rename"].trigger()
        qtbot.waitUntil(lambda: window.edited == {})


def test_no_to_try_again_waits_for_its_own_save_and_other_saves_do_not_bring_it_back(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        edited = window.edited[copy]
        edited.job = Job(cast(RemoteConnection, window.sessions[0].remote), window.sessions[0].connection, copy, "/", True, "a.txt", state=State.FAILED, message="refused")
        asked: list[str] = []

        def no(box: QMessageBox) -> int:
            asked.append(box.detailedText())
            return QMessageBox.StandardButton.No.value

        monkeypatch.setattr(QMessageBox, "exec", no)
        window._edit_job_ended(edited.job)
        window._send_edited_files()
        assert len(asked) == 1 and edited.job is None and edited.unsent
        window._connected(window.sessions[0], cast(RemoteConnection, window.sessions[0].remote), "/")
        assert not edited.held
        edited.unsent = False


def test_a_save_during_a_send_that_is_stopped_still_goes(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        edited = window.edited[copy]
        edited.unsent = True
        Path(copy).write_text("newer", encoding="utf-8")
        edited.job = Job(cast(RemoteConnection, window.sessions[0].remote), window.sessions[0].connection, copy, "/", True, "a.txt", state=State.STOPPED)
        window._edit_job_ended(edited.job)
        assert not edited.held
        qtbot.waitUntil(lambda: transfers_ended(window))
        assert text_of(root / "a.txt") == "newer"


def test_a_file_with_changes_on_their_way_opens_the_same_copy_and_otherwise_a_fresh_one(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        window.edited[opened[0]].unsent = True
        window.edited[opened[0]].held = True
        window.actions_by_name["edit"].trigger()
        assert opened == [opened[0], opened[0]]
        window.edited[opened[0]].unsent = False
        (root / "a.txt").write_text("newer on the server", encoding="utf-8")
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: len(opened) == 3)
        assert Path(opened[2]).read_text(encoding="utf-8") == "newer on the server"
        assert len(window.edited) == 1


def test_a_fresh_copy_that_can_not_be_fetched_leaves_the_edit_as_it_was(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    told: list[str] = []

    def note(_parent: object, _title: str, text: str) -> None:
        told.append(text)

    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        (root / "a.txt").unlink()
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(told))
        assert copy in window.edited and Path(copy).exists()


def test_a_save_that_went_through_during_the_download_of_a_fresh_copy_wins(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch)
        window.actions_by_name["edit"].trigger()
        window.edited[copy].saves += 1  # saved and sent while the download ran
        qtbot.waitUntil(lambda: window.fetching == set())
        assert list(window.edited) == [copy]


def test_two_presses_on_edit_give_one_copy(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        qtbot.wait(200)
        assert len(opened) == 1 and len(window.edited) == 1


def test_another_login_on_the_same_server_is_another_place(qtbot: QtBot, window: MainWindow) -> None:
    first = Session(Connection(name="A", host="h", user="alice"), Worker(window.bridge.deliver), "/")
    second = Session(Connection(name="B", host="h", user="bob"), Worker(window.bridge.deliver), "/")
    assert not same_server(first, second)
    first.worker.stop()
    second.worker.stop()


def test_a_live_connection_without_a_colour_looks_like_the_left_side_and_drops_to_grey(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, replace(ssh_settings(server), colour=""))
        assert base_of(window) == QColor(colours.PANEL)
        assert window.tab_bar.background_of(0) == colours.NO_COLOUR_TAB
        window._mark_lost(window.sessions[0])
        assert base_of(window) == QColor(colours.NOT_CONNECTED)
        assert window.tab_bar.background_of(0) == colours.NOT_CONNECTED_TAB


def test_the_path_of_a_copy_with_unsent_changes_is_shown_whole_and_fits_the_screen(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """It may be the only copy of the changes: every character of its path is there, broken over lines, none too long for the screen."""
    long_name = "x" * 250  # no space anywhere to break at
    root = tmp_path / "server"
    root.mkdir()
    (root / long_name).write_text("old", encoding="utf-8")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        copy = editing(qtbot, window, monkeypatch, long_name)
        window.edited[copy].unsent = True
        text = window._edits_text([copy])
    assert copy in text.replace("\n", "")  # whole
    screen = QGuiApplication.primaryScreen()
    room = (screen.availableGeometry().width() if screen is not None else 1024) * NAME_SHARE
    path_lines = [line for line in text.split("\n") if line and " " not in line]
    assert len(path_lines) > 1 and all(window.fontMetrics().horizontalAdvance(line) <= room for line in path_lines)


def test_opening_a_connection_that_has_a_tab_switches_to_it(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    for folder in (one, two):
        folder.mkdir()
    with ssh_server(one) as first, ssh_server(two) as second:
        trust_server(first)
        trust_server(second)
        connected(qtbot, window, ssh_settings(first, "Shop", GREEN))
        connected(qtbot, window, ssh_settings(second, "Other", MAGENTA))
        assert window.tab_bar.currentIndex() == 1
        window.open_connection(ssh_settings(first, "Shop", GREEN))
        assert window.tab_bar.count() == 2
        assert len(window.sessions) == 2
        assert window.tab_bar.currentIndex() == 0
