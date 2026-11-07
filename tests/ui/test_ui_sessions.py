import stat
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QInputDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.connections import trust
from chrono_ftp.connections.base import UnknownServer
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core import colours
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.changedialog import ChangeDialog
from chrono_ftp.ui.mainwindow import MainWindow
from tests.conftest import MemoryStore
from tests.fakeservers import PASSWORD, USER, SshServer, ftp_server, ssh_server

# --- Version ------------------------------------------------------------
__version__ = "test_ui_sessions.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: the real window against real servers in the test process

GREEN: str = "#00aa00"
MAGENTA: str = "#aa00aa"


def yes(*args: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def trust_server(server: SshServer) -> None:
    probe = SshConnection(Connection(name="T", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
    with pytest.raises(UnknownServer) as unknown:
        probe.connect()
    probe.close()
    trust.remember_host_key(unknown.value.to_remember)


def ssh_settings(server: SshServer, name: str = "Shop", colour: str = GREEN) -> Connection:
    return Connection(name=name, host="127.0.0.1", port=server.port, user=USER, colour=colour, start_folder="/")


@pytest.fixture
def window(qtbot: QtBot, memory_store: MemoryStore) -> Generator[MainWindow]:
    memory_store.passwords["Shop"] = PASSWORD
    memory_store.passwords["Other"] = PASSWORD
    made = MainWindow()
    qtbot.addWidget(made)
    yield made
    made.close()


def connected(qtbot: QtBot, window: MainWindow, connection: Connection) -> None:
    window.open_connection(connection)
    session = window.sessions[-1]
    qtbot.waitUntil(lambda: session.alive and session.remote_folder != "")


def names_on_the_right(window: MainWindow) -> list[str]:
    return [str(window.remote_side.files.item(row, 0).text()) for row in range(window.remote_side.files.rowCount())]


def select(window: MainWindow, *names: str) -> None:
    selection = window.remote_side.file_list.selectionModel()
    assert selection is not None
    selection.clear()
    for row in range(window.remote_side.files.rowCount()):
        item = window.remote_side.files.item(row, 0)
        if item.text() in names:
            selection.select(item.index(), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


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
        assert window.remote_side.path_label.text() == "/"


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
        assert window.tab_bar.background_of(0) == colours.NOT_CONNECTED


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
        assert window.remote_side.path_label.text() == "Not connected"
        assert names_on_the_right(window) == []


def visible(window: MainWindow, name: str) -> bool:
    return window.actions_by_name[name].isVisible()


def enabled(window: MainWindow, name: str) -> bool:
    return window.actions_by_name[name].isEnabled()


def test_shell_actions_are_not_shown_on_ftp(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        for name in ("copy", "search", "change", "terminal"):
            assert not visible(window, name), name
        for name in ("upload", "rename", "delete", "new_folder"):
            assert visible(window, name), name
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        assert enabled(window, "rename") and enabled(window, "delete")


def test_shell_actions_are_greyed_on_sftp_without_a_shell(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        for name in ("copy", "search", "change", "terminal"):
            assert visible(window, name), name
            assert not enabled(window, name), name
        assert enabled(window, "rename")


def test_shell_actions_work_with_a_shell(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        assert enabled(window, "change") and enabled(window, "search") and enabled(window, "copy")


def text_answer(text: str) -> Callable[..., tuple[str, bool]]:
    def answer(*args: object) -> tuple[str, bool]:
        return text, True

    return answer


def test_rename_new_folder_and_delete_from_the_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        monkeypatch.setattr(QInputDialog, "getText", text_answer("b.txt"))
        window.actions_by_name["rename"].trigger()
        qtbot.waitUntil(lambda: "b.txt" in names_on_the_right(window))
        monkeypatch.setattr(QInputDialog, "getText", text_answer("made"))
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

        def accepted(dialog: ChangeDialog) -> int:
            dialog.mode.setText("700")
            return ChangeDialog.DialogCode.Accepted.value

        shown: list[str] = []

        def confirm(*args: object) -> QMessageBox.StandardButton:
            shown.append(str(args[2]))
            return QMessageBox.StandardButton.Yes

        def ignored(*args: object) -> None:
            pass  # the jailed SFTP can't list the real path afterwards: only the command is under test here

        monkeypatch.setattr(ChangeDialog, "exec", accepted)
        monkeypatch.setattr(QMessageBox, "question", confirm)
        monkeypatch.setattr(QMessageBox, "information", ignored)
        window.actions_by_name["change"].trigger()
        assert f"chmod -R -- 700 '{tree}'" in shown[0]
        qtbot.waitUntil(lambda: stat.S_IMODE((tree / "deep" / "f.txt").stat().st_mode) == 0o700)


def test_no_means_no_command_runs(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    before = stat.S_IMODE((tmp_path / "a.txt").stat().st_mode)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")

        def accepted(dialog: ChangeDialog) -> int:
            dialog.mode.setText("700")
            return ChangeDialog.DialogCode.Accepted.value

        def no(*args: object) -> QMessageBox.StandardButton:
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(ChangeDialog, "exec", accepted)
        monkeypatch.setattr(QMessageBox, "question", no)
        window.actions_by_name["change"].trigger()
        assert stat.S_IMODE((tmp_path / "a.txt").stat().st_mode) == before


def test_a_download_lands_on_the_left_and_shows_in_the_queue(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
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
    assert window.tab_bar.background_of(0) == colours.NOT_CONNECTED
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


def test_a_wrong_starting_folder_opens_the_home_folder(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=server.port, user=USER, start_folder="/gone"))
        assert window.sessions[0].remote_folder == "/"


def test_a_typed_password_is_stored_only_after_the_server_accepted_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords.clear()
    answers = ["wrong", PASSWORD]
    stored_at_second_ask: list[dict[str, str]] = []

    def typed(*args: object) -> tuple[str, bool]:
        stored_at_second_ask.append(dict(memory_store.passwords))
        return answers.pop(0), True

    monkeypatch.setattr(QInputDialog, "getText", typed)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
    assert stored_at_second_ask == [{}, {}]
    assert memory_store.passwords == {"Shop": PASSWORD}


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
    assert window.tab_bar.background_of(0) == colours.NOT_CONNECTED


def test_closing_a_tab_with_a_running_transfer_asks_first(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        assert session.remote is not None
        job = Job(session.remote, session.connection, "/nothing", "/", True, "x")
        job.state = State.RUNNING
        window.queue.jobs.append(job)
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


def test_a_selected_link_can_not_get_a_recursive_change(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "real").mkdir(parents=True)
    (root / "link").symlink_to(root / "real")
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "link" in names_on_the_right(window))
        select(window, "real")
        assert enabled(window, "change")
        select(window, "link")
        assert not enabled(window, "change")


def test_a_download_asks_before_it_replaces_a_local_file(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server_side, local = tmp_path / "server", tmp_path / "local"
    server_side.mkdir()
    local.mkdir()
    (server_side / "a.txt").write_text("new", encoding="utf-8")
    (local / "a.txt").write_text("old", encoding="utf-8")
    asked: list[str] = []

    def no(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    with ftp_server(server_side) as port:
        window.local_side.go_to(str(local))
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["download"].trigger()
    assert "a.txt" in asked[0]
    assert window.queue.jobs == []
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
