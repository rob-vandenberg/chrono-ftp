"""Block FI on the screen: copy and paste files between tabs (FI-03). Ctrl+C and Ctrl+V on the list with the focus, the server's own
copy within one server, the way through this computer between two servers, and a pasted path."""

import threading
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.config.bookmarks import LOCAL, REMOTE
from chrono_ftp.connections.base import RemoteEntry
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.transfers.options import Answer, Choice, Conflict
from chrono_ftp.transfers.sidework import SideWork, Step
from chrono_ftp.core.errors import ConnectionLost
from chrono_ftp.engine import commands
from chrono_ftp.ui import windowpaste
from chrono_ftp.ui.copydialog import CopyDialog
from chrono_ftp.ui.plaintext import plain_of
from chrono_ftp.ui.overwritedialog import OverwriteDialog
from chrono_ftp.ui.startabort import ABORT_TEXT, START_TEXT
from chrono_ftp.ui.windowpaste import NO_PATH_HERE_TEXT, NO_PATH_THERE_TEXT, SOURCE_GONE_TEXT
from tests.fakeservers import ssh_server
from tests.ui.test_ui_sessions import GREEN, MAGENTA, connected, settled_tab, ssh_settings, transfer_window_accepted, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_paste.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Initial: Ctrl+C and Ctrl+V between the tabs: upload, download, the server's own copy, the way through this computer, a
#          pasted path, and what is greyed with its reason (FI-03)

__all__ = ["window"]  # the fixture, used by name


def pick(window: MainWindow, side_name: str, *names: str) -> None:
    view = (window.local_side if side_name == LOCAL else window.remote_side).file_list
    selection = view.selectionModel()
    assert selection is not None
    selection.clear()
    for name in names:
        selection.select(view.index_of(name), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def ended(window: MainWindow) -> bool:
    return bool(window.queue.jobs) and all(job.state.name in ("DONE", "FAILED", "STOPPED") for job in window.queue.jobs)


def test_files_of_this_computer_are_copied_and_pasted_on_the_server(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    there, here = tmp_path / "there", tmp_path / "here"
    for folder in (there, here):
        folder.mkdir()
    (here / "note.txt").write_text("hello", encoding="utf-8")
    shown = transfer_window_accepted(monkeypatch)
    with ssh_server(there) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        pick(window, LOCAL, "note.txt")
        window.copy_files(LOCAL)
        assert QGuiApplication.clipboard().text().endswith("note.txt")
        window.paste_files(REMOTE)
        qtbot.waitUntil(lambda: bool(shown))
        qtbot.waitUntil(lambda: (there / "note.txt").exists())
        qtbot.waitUntil(lambda: ended(window))


def test_files_of_a_server_are_copied_and_pasted_on_this_computer(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    there, here = tmp_path / "there", tmp_path / "here"
    for folder in (there, here):
        folder.mkdir()
    (there / "note.txt").write_text("hello", encoding="utf-8")
    transfer_window_accepted(monkeypatch)
    with ssh_server(there) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.remote_side.file_list.names())
        pick(window, REMOTE, "note.txt")
        window.copy_files(REMOTE)
        window.paste_files(LOCAL)
        qtbot.waitUntil(lambda: (here / "note.txt").exists())
        qtbot.waitUntil(lambda: ended(window))


def test_within_one_server_the_server_makes_every_copy_itself_from_a_start_button(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The command on the server is never run against the fake server, whose shell would act on this computer's own paths: what is
    checked here is the route, the window and what the server is asked to copy."""
    inner = tmp_path / "inner"
    inner.mkdir()
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    copies: list[tuple[str, str, bool]] = []

    def copied(remote: object, source: str, target: str, replace: bool = False) -> None:
        copies.append((source, target, replace))

    monkeypatch.setattr(commands, "copy_on_server", copied)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "note.txt" in window.remote_side.file_list.names())
        pick(window, REMOTE, "note.txt")
        window.copy_files(REMOTE)
        window.duplicate_tab(window.sessions[0])
        settled_tab(qtbot, window.sessions[1])
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: window.sessions[1].remote_folder == "/inner")
        window.paste_files(REMOTE)
        dialog = copy_window(window)
        assert "1 thing goes to /inner." in dialog.status.text() and "Nothing happens until you press Start." in dialog.status.text()
        assert dialog.start_button.text() == START_TEXT
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)
        assert copies == [("/note.txt", "/inner/note.txt", False)]
        assert dialog.status.text() == "Finished: note.txt was copied."
        assert window.queue.jobs == []  # nothing travelled through this computer
        dialog.close()


def test_a_paste_within_one_server_can_be_aborted_from_its_own_button(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    for number in range(5):
        (tmp_path / f"file{number}.txt").write_text("x", encoding="utf-8")
    held, busy = threading.Event(), threading.Event()
    copies: list[str] = []

    def slowly(remote: object, source: str, target: str, replace: bool = False) -> None:
        busy.set()
        held.wait(5)
        copies.append(source)

    monkeypatch.setattr(commands, "copy_on_server", slowly)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "file0.txt" in window.remote_side.file_list.names())
        pick(window, REMOTE, *[f"file{number}.txt" for number in range(5)])
        window.copy_files(REMOTE)
        window.duplicate_tab(window.sessions[0])
        settled_tab(qtbot, window.sessions[1])
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: window.sessions[1].remote_folder == "/inner")
        window.paste_files(REMOTE)
        dialog = copy_window(window)
        dialog.start_button.click()
        qtbot.waitUntil(busy.is_set)
        assert dialog.start_button.text() == ABORT_TEXT  # the one button of D-097, on the server side too
        assert dialog.status.text().startswith("Copying 1 of 5: file0.txt")  # the file it is busy with, from the moment it starts
        dialog.start_button.click()  # Abort
        held.set()
        qtbot.waitUntil(lambda: not dialog.running)
        assert copies == ["/file0.txt"]  # it ended after the one it was busy with
        assert dialog.status.text() == "Aborted: 1 of 5 was copied, and 4 were not. What was done stays done."
        dialog.close()


def test_between_two_servers_the_copy_goes_through_this_computer(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    (first / "note.txt").write_text("hello", encoding="utf-8")
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        qtbot.waitUntil(lambda: "note.txt" in window.remote_side.file_list.names())
        pick(window, REMOTE, "note.txt")
        window.copy_files(REMOTE)
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        window.paste_files(REMOTE)
        qtbot.waitUntil(lambda: (second / "note.txt").read_text(encoding="utf-8") == "hello" if (second / "note.txt").exists() else False)
        qtbot.waitUntil(lambda: ended(window) and not window.relayed)
        assert len(window.queue.jobs) == 2  # fetched here first, then sent on
        assert (first / "note.txt").exists()  # a copy, never a move


def test_a_path_on_the_clipboard_opens_that_folder(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    here = tmp_path / "here"
    here.mkdir()
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        QGuiApplication.clipboard().setText(str(here))
        window.paste_files(LOCAL)
        assert window.local_side.folder == str(here)


def test_with_nothing_copied_the_paste_is_greyed_with_its_reason(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        QGuiApplication.clipboard().clear()
        window.update_actions()
        paste = window.list_actions[f"{REMOTE}_paste_files"]
        assert not paste.isEnabled() and paste.toolTip() == NO_PATH_THERE_TEXT  # greyed with its reason (OR-20)





def test_a_paste_from_a_tab_that_was_closed_says_so_and_does_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    for folder in (first, second):
        folder.mkdir()
    (first / "note.txt").write_text("hello", encoding="utf-8")
    with ssh_server(first) as one, ssh_server(second) as two:
        trust_server(one)
        trust_server(two)
        connected(qtbot, window, ssh_settings(one, "Shop", GREEN))
        qtbot.waitUntil(lambda: "note.txt" in window.remote_side.file_list.names())
        pick(window, REMOTE, "note.txt")
        window.copy_files(REMOTE)
        connected(qtbot, window, ssh_settings(two, "Other", MAGENTA))
        window.close_tab(0)
        window.update_actions()
        paste = window.list_actions[f"{REMOTE}_paste_files"]
        assert not paste.isEnabled() and paste.toolTip() == SOURCE_GONE_TEXT
        window.paste_files(REMOTE)
        assert window.queue.jobs == []


def test_ctrl_c_in_a_text_field_is_the_fields_own_copy(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "note.txt" in window.remote_side.file_list.names())
        pick(window, REMOTE, "note.txt")
        with qtbot.waitExposed(window):
            window.show()
        window.activateWindow()
        qtbot.waitUntil(window.isActiveWindow)
        line = window.remote_side.path_line
        line.setFocus()
        line.setText("/typed/path")
        line.selectAll()
        QTest.keySequence(line, QKeySequence("Ctrl+C"))
        assert window.copied is None  # the key acts on the part that has the focus (OR-21)
        assert QGuiApplication.clipboard().text() == "/typed/path"


def test_ctrl_c_and_ctrl_v_on_the_list_with_the_focus_work(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    there, here = tmp_path / "there", tmp_path / "here"
    for folder in (there, here):
        folder.mkdir()
    (here / "note.txt").write_text("hello", encoding="utf-8")
    transfer_window_accepted(monkeypatch)
    with ssh_server(there) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        pick(window, LOCAL, "note.txt")
        with qtbot.waitExposed(window):
            window.show()
        window.activateWindow()
        qtbot.waitUntil(window.isActiveWindow)
        window.local_side.file_list.setFocus()
        QTest.keySequence(window.local_side.file_list, QKeySequence("Ctrl+C"))
        assert window.copied is not None
        window.remote_side.file_list.setFocus()
        QTest.keySequence(window.remote_side.file_list, QKeySequence("Ctrl+V"))
        qtbot.waitUntil(lambda: (there / "note.txt").exists())
        qtbot.waitUntil(lambda: ended(window))


# --- a copy from this computer to this computer (FI-03, D-097) -------------------------


def copy_window(window: MainWindow) -> CopyDialog:
    found = [child for child in window.findChildren(CopyDialog) if child.isVisible()]
    assert found, "the window of the paste did not come up"
    return found[-1]


def paste_here(qtbot: QtBot, window: MainWindow, folder: Path, *names: str) -> CopyDialog:
    """Ctrl+C on these files, then the folder shown is changed to folder and Ctrl+V pressed there."""
    pick(window, LOCAL, *names)
    window.copy_files(LOCAL)
    window.local_side.go_to(str(folder))
    qtbot.waitUntil(lambda: window.local_side.folder == str(folder))
    window.paste_files(LOCAL)
    return copy_window(window)


def test_files_of_this_computer_are_copied_here_from_a_window_with_one_start_button(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "note.txt").write_text("hello", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, "note.txt")
        assert "1 thing goes to" in dialog.status.text() and "Nothing happens until you press Start." in dialog.status.text()
        assert dialog.start_button.text() == START_TEXT and dialog.start_button.isEnabled()
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)
        assert (there / "note.txt").read_text(encoding="utf-8") == "hello"
        assert (here / "note.txt").exists()  # a copy, never a move
        assert dialog.status.text() == "Finished: note.txt was copied."
        dialog.close()


def test_a_file_pasted_where_it_already_is_becomes_a_copy_next_to_it(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    here = tmp_path / "here"
    here.mkdir()
    (here / "note.txt").write_text("hello", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, here, "note.txt")
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)
        assert (here / "note (copy).txt").read_text(encoding="utf-8") == "hello"
        assert (here / "note.txt").read_text(encoding="utf-8") == "hello"  # nothing was written over (G-2)
        dialog.close()


def test_a_file_that_is_there_asks_first_and_no_leaves_it_as_it_is(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "note.txt").write_text("new", encoding="utf-8")
    (there / "note.txt").write_text("old", encoding="utf-8")
    asked: list[Conflict] = []

    def answer(dialog: OverwriteDialog) -> int:
        asked.append(dialog.conflict)
        dialog.answer = Answer(Choice.NO)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(OverwriteDialog, "exec", answer)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, "note.txt")
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)
        assert [conflict.copying for conflict in asked] == [True]  # the same question as a transfer asks, in words for a copy that stays here
        assert (there / "note.txt").read_text(encoding="utf-8") == "old"  # never lost (G-2)
        assert "Nothing was copied" in dialog.status.text()
        dialog.close()


def test_yes_replaces_the_file_that_is_there(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "note.txt").write_text("new", encoding="utf-8")
    (there / "note.txt").write_text("old", encoding="utf-8")

    def answer(dialog: OverwriteDialog) -> int:
        dialog.answer = Answer(Choice.YES)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(OverwriteDialog, "exec", answer)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, "note.txt")
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)
        assert (there / "note.txt").read_text(encoding="utf-8") == "new"
        dialog.close()


def test_the_start_button_reads_abort_while_the_copy_runs_and_closing_stops_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    for number in range(6):
        (here / f"file{number}.txt").write_text("x" * 100, encoding="utf-8")
    held, inside = threading.Event(), threading.Event()
    real_copy = windowpaste.on_this_computer

    def slowly(work: object, step: object) -> None:
        inside.set()  # the first file is really being copied now
        held.wait(5)  # it stays busy with it until the test lets go, so Abort has something to end
        real_copy(cast(SideWork, work), cast(Step, step))

    monkeypatch.setattr(windowpaste, "on_this_computer", slowly)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "file0.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, *[f"file{number}.txt" for number in range(6)])
        dialog.start_button.click()
        qtbot.waitUntil(inside.is_set)  # waits for the thing it checks, never for something that usually comes first
        assert dialog.running
        assert dialog.start_button.text() == ABORT_TEXT and dialog.start_button.isEnabled()  # the same button, now naming Abort (D-097)
        dialog.start_button.click()  # Abort
        held.set()
        qtbot.waitUntil(lambda: not dialog.running)
        assert dialog.status.text() == "Aborted: 1 of 6 was copied, and 5 were not. What was done stays done."  # it ends after the file it is busy with
        assert sorted(path.name for path in there.iterdir()) == ["file0.txt"]
        dialog.close()


def test_a_folder_is_copied_with_everything_in_it(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "box").mkdir()
    (here / "box" / "deep.txt").write_text("hello", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "box" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, "box")
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)
        assert (there / "box" / "deep.txt").read_text(encoding="utf-8") == "hello"
        dialog.close()


def test_closing_the_window_stops_the_copy_too(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    for number in range(4):
        (here / f"file{number}.txt").write_text("x", encoding="utf-8")
    held, inside = threading.Event(), threading.Event()
    real_copy = windowpaste.on_this_computer

    def slowly(work: object, step: object) -> None:
        inside.set()
        held.wait(5)
        real_copy(cast(SideWork, work), cast(Step, step))

    monkeypatch.setattr(windowpaste, "on_this_computer", slowly)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "file0.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, *[f"file{number}.txt" for number in range(4)])
        dialog.start_button.click()
        qtbot.waitUntil(inside.is_set)
        dialog.close()  # closing stops the work, so nothing is left running where nobody can see it (D-097)
        held.set()
        qtbot.waitUntil(lambda: not dialog.running)
        assert sorted(path.name for path in there.iterdir()) == ["file0.txt"]


def test_a_file_is_never_written_into_a_folder_that_has_its_name(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "x").write_text("the file", encoding="utf-8")
    (there / "x").mkdir()
    (there / "x" / "x").write_text("must stay", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "x" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, "x")
        assert "can't be copied" in dialog.status.text() and "folder with this name" in dialog.status.text()
        assert not dialog.start_button.isEnabled() and dialog.start_button.toolTip()  # greyed with its reason (OR-20)
        assert (there / "x" / "x").read_text(encoding="utf-8") == "must stay"  # the file the question never named is untouched (G-2)
        dialog.close()


def test_the_window_says_beforehand_that_a_copy_gets_a_copy_name(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    here = tmp_path / "here"
    here.mkdir()
    (here / "note.txt").write_text("hello", encoding="utf-8")
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, here, "note.txt")
        assert 'copied beside it as "note (copy).txt"' in dialog.status.text()  # said before Start, not sprung afterwards
        dialog.close()


def test_the_line_names_the_file_being_copied_and_the_end_names_what_could_not_be(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    for name in ("a.txt", "b.txt"):
        (here / name).write_text("x", encoding="utf-8")
    busy, held = threading.Event(), threading.Event()
    real_copy = windowpaste.on_this_computer

    def first_fails(work: object, step: object) -> None:
        busy.set()
        held.wait(5)
        if cast(Step, step).path.endswith("a.txt"):
            raise OSError("Permission denied")
        real_copy(cast(SideWork, work), cast(Step, step))

    monkeypatch.setattr(windowpaste, "on_this_computer", first_fails)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "a.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, "a.txt", "b.txt")
        dialog.start_button.click()
        qtbot.waitUntil(busy.is_set)
        assert dialog.status.text() == "Copying 1 of 2: a.txt"  # the file being copied now, not the one before it
        held.set()
        qtbot.waitUntil(lambda: not dialog.running)
        assert dialog.status.text() == "Finished: 1 of 2 was copied. This could not be: a.txt (Permission denied)."
        dialog.close()


def test_paste_is_greyed_when_the_clipboard_holds_no_files_and_no_path(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        QGuiApplication.clipboard().setText("hello")
        window.update_actions()
        for side_name, why in ((LOCAL, NO_PATH_HERE_TEXT), (REMOTE, NO_PATH_THERE_TEXT)):
            paste = window.list_actions[f"{side_name}_paste_files"]
            assert not paste.isEnabled() and paste.toolTip() == why  # it never offers what it can't do (OR-20)
        window.status.clearMessage()
        QTest.keyClick(window.local_side.file_list, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert window.status.currentMessage() == NO_PATH_HERE_TEXT  # pressing it says why, where the person will see it


def test_a_folder_pasted_where_it_already_is_also_gets_a_copy_name_on_the_server(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    copies: list[tuple[str, str]] = []

    def copied(remote: object, source: str, target: str, replace: bool = False) -> None:
        copies.append((source, target))

    monkeypatch.setattr(commands, "copy_on_server", copied)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "note.txt" in window.remote_side.file_list.names())
        pick(window, REMOTE, "note.txt")
        window.copy_files(REMOTE)
        window.paste_files(REMOTE)  # pasted into the folder it came from, as on this computer (one action, one way)
        dialog = copy_window(window)
        assert 'copied beside it as "note (copy).txt"' in dialog.status.text()
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)
        assert copies == [("/note.txt", "/note (copy).txt")]
        dialog.close()


def test_a_replacing_copy_here_that_breaks_off_leaves_what_was_there_whole(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy that breaks off (a full disk, a read error): the person agreed to replace one file and must not lose it (G-2)."""
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "note.txt").write_text("new", encoding="utf-8")
    (there / "note.txt").write_text("precious", encoding="utf-8")

    def breaks_off(work: object, step: object) -> None:
        Path(cast(Step, step).target).write_text("half a copy", encoding="utf-8")  # as a copy that stops halfway leaves it
        raise OSError("No space left on device")

    monkeypatch.setattr(windowpaste, "on_this_computer", breaks_off)

    def answer(dialog: OverwriteDialog) -> int:
        dialog.answer = Answer(Choice.YES)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(OverwriteDialog, "exec", answer)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "note.txt" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, "note.txt")
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)
        assert (there / "note.txt").read_text(encoding="utf-8") == "precious"  # untouched
        assert sorted(path.name for path in there.iterdir()) == ["note.txt"]  # and our own half copy went
        assert "No space left on device" in dialog.status.text()
        dialog.close()


def test_a_paste_on_a_server_whose_connection_drops_says_what_was_copied_and_stops_waiting(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    for number in range(3):
        (tmp_path / f"file{number}.txt").write_text("x", encoding="utf-8")
    copies: list[str] = []

    def drops(remote: object, source: str, target: str, replace: bool = False) -> str:
        if copies:
            raise ConnectionLost("The connection to the server was lost.")
        copies.append(source)
        return ""

    monkeypatch.setattr(commands, "copy_on_server", drops)
    told: list[str] = []

    def says(parent: object, title: str, text: str, *args: object, **kwargs: object) -> QMessageBox.StandardButton:
        told.append(plain_of(text))  # the lost connection is said in a box, as everywhere in the program
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", staticmethod(says))
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "file0.txt" in window.remote_side.file_list.names())
        pick(window, REMOTE, *[f"file{number}.txt" for number in range(3)])
        window.copy_files(REMOTE)
        window.duplicate_tab(window.sessions[0])
        settled_tab(qtbot, window.sessions[1])
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: window.sessions[1].remote_folder == "/inner")
        window.paste_files(REMOTE)
        dialog = copy_window(window)
        dialog.start_button.click()
        qtbot.waitUntil(lambda: not dialog.running)  # the window ends: no Abort button is left that does nothing
        assert "1 of 3 was copied" in dialog.status.text() and "connection was lost" in dialog.status.text()
        assert dialog.start_button.text() != ABORT_TEXT
        assert any("connection to the server was lost" in said for said in told)
        dialog.close()


def test_the_window_of_one_file_that_cannot_be_copied_says_this_not_these(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "x").write_text("the file", encoding="utf-8")
    (there / "x").mkdir()
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "x" in window.local_side.file_list.names())
        dialog = paste_here(qtbot, window, there, "x")
        assert " This can't be copied: x (" in dialog.status.text()  # one item: "This", never "These" (OR-23)
        dialog.close()
