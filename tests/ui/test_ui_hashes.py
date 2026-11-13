"""Block FE in the window: the Size and Checksum tabs of Properties on both sides, Shift+Space for the folder sizes of the side with the
focus, and Compare Contents in Synchronize, on the fake servers only (OR-26)."""

import hashlib
import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_ftp.config.bookmarks import REMOTE
from chrono_ftp.connections.base import KeepGoing, RemoteConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.engine import checksums, sizes
from chrono_ftp.engine.sizes import Count
from chrono_ftp.ui import propertiesdialog
from chrono_ftp.ui.comparedialog import CONTENTS_STOPPED_TEXT, CONTENTS_TEXT, STOP_TEXT, CompareDialog
from chrono_ftp.ui.filelist import AT_LEAST_TEXT
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.propertiesdialog import PropertiesDialog
from chrono_ftp.ui.windownavigation import PROGRAM_KEYS
from chrono_ftp.ui.windowsizes import COUNT_STOPPED_TEXT, FOLDER_SIZES_KEY, NO_FOLDER_SELECTED_TEXT
from tests.fakeservers import NOT_AS_ROOT, SLOW_HASHING, USER, ftp_server, ssh_server
from tests.ui.test_ui_properties import pick_local
from tests.ui.test_ui_selection import with_settings
from tests.ui.test_ui_sessions import connected, names_on_the_right, select, ssh_settings, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_hashes.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Initial: block FE, hashes and sizes without a shell (FE-01 to FE-03); review round 1: calculations that never wait for
#          each other, counts that stop with their tab, Shift+Space typing a space into a name being searched; review round 2: a
#          stopped Compare Contents says so

_ = window  # the window of the session tests, with a password in the test's store
CONTENT: bytes = b"some content\n"


def sha(data: bytes, algorithm: str = "sha256") -> str:
    return hashlib.new(algorithm, data).hexdigest()


def ftp_settings(port: int) -> Connection:
    return Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/")


def size_cell(window: MainWindow, local: bool, name: str) -> str:
    view = (window.local_side if local else window.remote_side).file_list
    return str(view.index_of(name).siblingAtColumn(1).data())


def shift_space(window: MainWindow, local: bool) -> None:
    view = (window.local_side if local else window.remote_side).file_list
    view.setFocus()
    QTest.keyClick(view, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)


def tree(root: Path) -> None:
    (root / "photos" / "2025").mkdir(parents=True)
    (root / "photos" / "a.jpg").write_bytes(b"1" * 2000)
    (root / "photos" / "2025" / "b.jpg").write_bytes(b"2" * 48)
    (root / "notes.txt").write_bytes(CONTENT)


# --- the Size and Checksum tabs of Properties (FE-02) -------------------------------------


def test_the_size_tab_here_counts_files_and_folders(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree(tmp_path)
    pick_local(window, tmp_path, "photos", "notes.txt")
    seen: list[tuple[str, str, str]] = []

    def count(dialog: PropertiesDialog) -> int:
        tab = dialog.size_tab
        assert tab is not None
        dialog.tabs.setCurrentWidget(tab)
        tab.calculate.click()
        qtbot.waitUntil(lambda: tab.stop is None)
        seen.append((tab.size_value.text(), tab.files.text(), tab.folders.text()))
        return PropertiesDialog.DialogCode.Rejected.value

    monkeypatch.setattr(PropertiesDialog, "exec", count)
    window.actions_by_name["local_properties"].trigger()
    qtbot.waitUntil(lambda: bool(seen))
    total = 2000 + 48 + len(CONTENT)
    assert seen == [(propertiesdialog.count_text(total), "3", "2")]


def test_the_size_tab_stops_when_asked(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree(tmp_path)
    pick_local(window, tmp_path, "photos")
    started = threading.Event()

    def endless(_path: str, keep_going: KeepGoing) -> Count | None:
        started.set()
        limit = time.monotonic() + 10
        while keep_going() and time.monotonic() < limit:
            time.sleep(0.01)
        return None if not keep_going() else Count()

    monkeypatch.setattr(sizes, "local_count", endless)
    notes: list[str] = []

    def stop_it(dialog: PropertiesDialog) -> int:
        tab = dialog.size_tab
        assert tab is not None
        tab.calculate.click()
        qtbot.waitUntil(started.is_set)
        assert tab.calculate.text() == propertiesdialog.STOP_TEXT
        tab.calculate.click()
        qtbot.waitUntil(lambda: tab.stop is None)
        notes.append(tab.note.text())
        return PropertiesDialog.DialogCode.Rejected.value

    monkeypatch.setattr(PropertiesDialog, "exec", stop_it)
    window.actions_by_name["local_properties"].trigger()
    qtbot.waitUntil(lambda: bool(notes))
    assert notes == [propertiesdialog.STOPPED_TEXT]


def test_the_checksum_tab_here_lists_names_with_checksums_copies_them_and_remembers_the_algorithm(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree(tmp_path)
    pick_local(window, tmp_path, "photos", "notes.txt")
    seen: list[list[tuple[str, str]]] = []
    starts: list[str] = []

    def calculate(dialog: PropertiesDialog) -> int:
        tab = dialog.checksum_tab
        assert tab is not None
        starts.append(tab.algorithm.currentText())
        if len(starts) == 1:
            tab.algorithm.setCurrentText("md5")
            tab.calculate.click()
            qtbot.waitUntil(lambda: tab.stop is None)
            rows = [item for number in range(tab.rows.topLevelItemCount()) if (item := tab.rows.topLevelItem(number)) is not None]
            seen.append([(item.text(0), item.text(1)) for item in rows])
            tab.copy.click()
        return PropertiesDialog.DialogCode.Rejected.value

    monkeypatch.setattr(PropertiesDialog, "exec", calculate)
    window.actions_by_name["local_properties"].trigger()
    qtbot.waitUntil(lambda: bool(seen))
    expected = [(os.path.join("photos", "2025", "b.jpg"), sha(b"2" * 48, "md5")), (os.path.join("photos", "a.jpg"), sha(b"1" * 2000, "md5")), ("notes.txt", sha(CONTENT, "md5"))]
    assert seen == [expected]
    assert QGuiApplication.clipboard().text() == "".join(f"{value}  {name}\n" for name, value in expected)  # as the *sum tools write them
    assert window.settings.checksum_algorithm == "md5"
    window.actions_by_name["local_properties"].trigger()
    qtbot.waitUntil(lambda: len(starts) == 2)
    assert starts == [checksums.PREFERRED[0], "md5"]  # the last choice comes first next time


def test_the_checksum_tab_on_the_server_uses_check_file_without_a_shell(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    tree(root)
    seen: list[list[tuple[str, str]]] = []
    sizes_seen: list[str] = []

    def calculate(dialog: PropertiesDialog) -> int:
        tab, size_tab = dialog.checksum_tab, dialog.size_tab
        assert tab is not None and size_tab is not None
        assert tab.calculate.isEnabled()
        tab.algorithm.setCurrentText("sha256")
        tab.calculate.click()
        qtbot.waitUntil(lambda: tab.stop is None)
        rows = [item for number in range(tab.rows.topLevelItemCount()) if (item := tab.rows.topLevelItem(number)) is not None]
        seen.append([(item.text(0), item.text(1)) for item in rows])
        size_tab.calculate.click()
        qtbot.waitUntil(lambda: size_tab.stop is None)
        sizes_seen.append(size_tab.files.text())
        return PropertiesDialog.DialogCode.Rejected.value

    monkeypatch.setattr(PropertiesDialog, "exec", calculate)
    with ssh_server(root, shell=False, check_file=True) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "photos" in names_on_the_right(window))
        select(window, "photos")
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: bool(seen))
    assert seen == [[("photos/2025/b.jpg", sha(b"2" * 48)), ("photos/a.jpg", sha(b"1" * 2000))]]
    assert sizes_seen == ["2"]


def test_on_ftp_without_a_hash_the_checksum_is_greyed_with_its_reason(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_bytes(CONTENT)
    seen: list[tuple[bool, str, bool]] = []

    def look(dialog: PropertiesDialog) -> int:
        tab, size_tab = dialog.checksum_tab, dialog.size_tab
        assert tab is not None and size_tab is not None
        seen.append((tab.calculate.isEnabled(), tab.calculate.toolTip(), size_tab.calculate.isEnabled()))
        return PropertiesDialog.DialogCode.Rejected.value

    monkeypatch.setattr(PropertiesDialog, "exec", look)
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, ftp_settings(port))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: bool(seen))
    assert seen == [(False, checksums.NO_WAY_ON_FTP_TEXT, True)]  # greyed, never hidden (OR-20); the size works everywhere


# --- folder sizes in the list (FE-03) --------------------------------------------------------


def test_shift_space_here_fills_the_size_of_the_selected_folders(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    tree(tmp_path)
    (tmp_path / "empty").mkdir()
    with qtbot.waitExposed(window):
        window.show()
    pick_local(window, tmp_path, "photos", "empty", "notes.txt")
    assert size_cell(window, True, "photos") == ""
    shift_space(window, True)
    qtbot.waitUntil(lambda: size_cell(window, True, "empty") != "")
    qtbot.waitUntil(lambda: size_cell(window, True, "photos") != "")
    assert size_cell(window, True, "photos") == "2.0 KB" and size_cell(window, True, "empty") == "0 bytes"
    assert size_cell(window, True, "notes.txt") == f"{len(CONTENT)} bytes"
    assert FOLDER_SIZES_KEY in PROGRAM_KEYS  # no bookmark can take it
    window.local_side.go_to(str(tmp_path / "photos"))
    window.local_side.go_to(str(tmp_path))
    assert size_cell(window, True, "photos") == ""  # counted for the folder it was in: gone once another folder showed


@NOT_AS_ROOT
def test_a_folder_that_can_not_be_read_in_full_shows_at_least(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    tree(tmp_path)
    (tmp_path / "photos" / "2025").chmod(0)
    try:
        with qtbot.waitExposed(window):
            window.show()
        pick_local(window, tmp_path, "photos")
        shift_space(window, True)
        qtbot.waitUntil(lambda: size_cell(window, True, "photos") != "")
        assert size_cell(window, True, "photos") == AT_LEAST_TEXT.format(size="2.0 KB")
    finally:
        (tmp_path / "photos" / "2025").chmod(0o755)


def test_shift_space_without_a_folder_never_types_nor_marks_and_says_why(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    tree(tmp_path)
    with qtbot.waitExposed(window):
        window.show()
    with_settings(window, two_pane_selection=True)
    view = window.local_side.file_list
    window.local_side.go_to(str(tmp_path))
    selection = view.selectionModel()
    assert selection is not None
    selection.setCurrentIndex(view.index_of("notes.txt"), QItemSelectionModel.SelectionFlag.NoUpdate)
    assert not window.list_actions["local_folder_sizes"].isEnabled()
    shift_space(window, True)
    assert view.selected_entries() == [] and view.typed == ""  # not marked as Space would, nothing typed
    assert window.status.currentMessage() == NO_FOLDER_SELECTED_TEXT


def test_folder_sizes_on_the_server_are_counted_away_from_the_persons_own_actions(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    tree(root)
    may_go_on = threading.Event()
    real_count = sizes.remote_count_by_listing

    def held(connection: RemoteConnection, path: str, keep_going: KeepGoing) -> Count | None:
        may_go_on.wait(10)  # the count takes long; meanwhile the person browses
        return real_count(connection, path, keep_going)

    monkeypatch.setattr(sizes, "remote_count_by_listing", held)
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "photos" in names_on_the_right(window))
        with qtbot.waitExposed(window):
            window.show()
        select(window, "photos")
        shift_space(window, False)
        qtbot.waitUntil(window.stop_counting.isVisible)
        (root / "new.txt").write_bytes(b"n")
        window.refresh()
        qtbot.waitUntil(lambda: "new.txt" in names_on_the_right(window))  # the person's own refresh never waited for the count
        assert size_cell(window, False, "photos") == ""
        may_go_on.set()
        qtbot.waitUntil(lambda: size_cell(window, False, "photos") == "2.0 KB")
        qtbot.waitUntil(lambda: not window.stop_counting.isVisible())


def test_stop_counting_ends_the_count_and_leaves_the_column_as_it_was(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree(tmp_path)
    started = threading.Event()

    def endless(_path: str, keep_going: KeepGoing) -> Count | None:
        started.set()
        limit = time.monotonic() + 10
        while keep_going() and time.monotonic() < limit:
            time.sleep(0.01)
        return None

    monkeypatch.setattr(sizes, "local_count", endless)
    with qtbot.waitExposed(window):
        window.show()
    pick_local(window, tmp_path, "photos")
    shift_space(window, True)
    qtbot.waitUntil(started.is_set)
    window.stop_counting.click()
    qtbot.waitUntil(lambda: not window.stop_counting.isVisible())
    assert size_cell(window, True, "photos") == ""


# --- Compare Contents (FE-01, D-027) ----------------------------------------------------------


def test_compare_contents_without_a_shell_leaves_out_what_has_the_same_content(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for side, other in ((here, b"AAAA"), (root, b"BBBB")):
        (side / "same.txt").write_bytes(CONTENT)
        (side / "other.txt").write_bytes(other)
        os.utime(side / "same.txt", (1_600_000_000 if side == here else 1_650_000_000,) * 2)
        os.utime(side / "other.txt", (1_600_000_000 if side == here else 1_650_000_000,) * 2)
    with ssh_server(root, shell=False, check_file=True) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        window.actions_by_name["compare"].trigger()
        dialog = window.findChild(CompareDialog)
        assert dialog is not None
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 2)
        qtbot.waitUntil(dialog.contents_button.isEnabled)
        dialog.contents_button.click()
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1 and dialog.contents_button.text() == CONTENTS_TEXT)
        name, difference = dialog.table.item(0, 0), dialog.table.item(0, 1)
        assert name is not None and difference is not None
        assert (name.text(), difference.text()) == ("other.txt", "Other content, newer on the server")
        assert name.checkState() == Qt.CheckState.Checked
        dialog.close()


def test_compare_contents_on_ftp_without_a_hash_is_greyed_with_its_reason(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for side in (here, root):
        (side / "same.txt").write_bytes(CONTENT)
    os.utime(here / "same.txt", (1_600_000_000, 1_600_000_000))
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        window.local_side.go_to(str(here))
        window.actions_by_name["compare"].trigger()
        dialog = window.findChild(CompareDialog)
        assert dialog is not None
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        assert not dialog.contents_button.isEnabled() and dialog.contents_button.toolTip() == checksums.NO_WAY_ON_FTP_TEXT
        dialog.close()


# --- review round 1 --------------------------------------------------------------------------------


def test_a_second_calculation_never_waits_for_the_first(qtbot: QtBot, window: MainWindow) -> None:
    first_may_end = threading.Event()
    answers: list[str] = []
    first, second = threading.Event(), threading.Event()
    window.run_alone(lambda: first_may_end.wait(10), lambda _result: answers.append("first"), lambda _error: None, first)
    window.run_alone(lambda: "done", lambda _result: answers.append("second"), lambda _error: None, second)
    qtbot.waitUntil(lambda: answers == ["second"])  # while the first still runs
    first_may_end.set()
    qtbot.waitUntil(lambda: answers == ["second", "first"])
    assert window.calculations == set()


def held_count(started: threading.Event, ended: list[bool]) -> object:
    """A count on the server that runs until it is told to stop (at most 10 s), and notes that it ended."""

    def count(_connection: RemoteConnection, _path: str, keep_going: KeepGoing) -> Count | None:
        started.set()
        limit = time.monotonic() + 10
        while keep_going() and time.monotonic() < limit:
            time.sleep(0.01)
        ended.append(True)
        return None

    return count


def test_closing_a_tab_stops_its_count_silently(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    tree(root)
    started, ended = threading.Event(), list[bool]()
    monkeypatch.setattr(sizes, "remote_count_by_listing", held_count(started, ended))
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "photos" in names_on_the_right(window))
        with qtbot.waitExposed(window):
            window.show()
        select(window, "photos")
        shift_space(window, False)
        qtbot.waitUntil(started.is_set)
        window.close_tab(0)
        assert not window.stop_counting.isVisible()
        qtbot.waitUntil(lambda: ended == [True] and window.size_workers[REMOTE].idle())
        QApplication.processEvents()  # the count's answer, handed to the window, is taken in now: it may say nothing
        assert "could not be counted" not in window.status.currentMessage() and window.status.currentMessage() != COUNT_STOPPED_TEXT


def test_a_count_belongs_to_its_tab_and_showing_another_tab_stops_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    for root in (first, second):
        root.mkdir()
        tree(root)
    started, ended = threading.Event(), list[bool]()
    monkeypatch.setattr(sizes, "remote_count_by_listing", held_count(started, ended))
    with ssh_server(first, shell=False) as one, ssh_server(second, shell=False) as other:
        trust_server(one)
        trust_server(other)
        connected(qtbot, window, ssh_settings(one))
        qtbot.waitUntil(lambda: "photos" in names_on_the_right(window))
        with qtbot.waitExposed(window):
            window.show()
        select(window, "photos")
        shift_space(window, False)
        qtbot.waitUntil(started.is_set)
        connected(qtbot, window, ssh_settings(other, name="Other"))  # the same folder, /, on another server
        qtbot.waitUntil(lambda: window.current() is window.sessions[1] and "photos" in names_on_the_right(window))
        assert not window.stop_counting.isVisible()  # the button belongs to the tab shown
        qtbot.waitUntil(lambda: ended == [True])
        assert size_cell(window, False, "photos") == ""


def test_while_a_name_is_typed_shift_space_types_a_space(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    tree(tmp_path)
    (tmp_path / "my photos").mkdir()
    with qtbot.waitExposed(window):
        window.show()
    pick_local(window, tmp_path, "photos")
    view = window.local_side.file_list
    view.setFocus()
    qtbot.waitUntil(view.hasFocus)
    QTest.keyClicks(view, "my")
    assert view.typed == "my"
    QTest.keyClick(view, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
    assert view.typed == "my " and not window.stop_counting.isVisible()
    entry = view.current_entry()
    assert entry is not None and entry.name == "my photos"


def test_a_stopped_compare_contents_says_so_and_keeps_its_rows(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for side in (here, root):
        (side / "same.txt").write_bytes(CONTENT)
    os.utime(here / "same.txt", (1_600_000_000, 1_600_000_000))
    SLOW_HASHING.clear()
    with ssh_server(root, shell=False, check_file="slow") as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        window.actions_by_name["compare"].trigger()
        dialog = window.findChild(CompareDialog)
        assert dialog is not None
        qtbot.waitUntil(dialog.contents_button.isEnabled)
        dialog.contents_button.click()
        assert SLOW_HASHING.wait(10)
        assert dialog.contents_button.text() == STOP_TEXT
        dialog.contents_button.click()
        qtbot.waitUntil(lambda: dialog.contents_button.text() == CONTENTS_TEXT)
        assert dialog.status.text() == CONTENTS_STOPPED_TEXT and dialog.table.rowCount() == 1
        dialog.close()

