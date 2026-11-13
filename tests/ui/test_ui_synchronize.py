"""Block FF in the window: the choices of the Synchronize window, its checklist with the size and date of both sides, Select by
action, Reverse of one row, the size of a one-sided folder counted in the background, the deletes that are named and asked for first,
Start through the queue, the choices kept for next time, Mark Differences in both lists and the window for two folders on this
computer. Only the fake servers are used (OR-26)."""

import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QMessageBox, QPushButton, QRadioButton
from pytestqt.qtbot import QtBot

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.bookmarks import LOCAL, REMOTE
from chrono_ftp.core.synchronizing import Criterion, Direction, Mode
from chrono_ftp.engine.synchronize import NO_REVERSE_WHY, Action, text_of
from chrono_ftp.transfers.job import State
from chrono_ftp.ui.comparedialog import ABORT_TEXT, COMPARE_AGAIN_TEXT, CONTENTS_TEXT, COUNTING_STOPPED_TEXT, NOTHING_TICKED_WHY, SCAN_STOPPED_TEXT, START_TEXT, STOP_COMPARING_TEXT, STOP_CONTENTS_TEXT, STOP_COUNTING_TEXT, CompareDialog
from chrono_ftp.ui import comparedialog, synchronizeoptions, windowsynchronize
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.windowsynchronize import ANOTHER_FOLDER_TEXT, MARK_TEXT
from tests.fakeservers import ftp_server, ssh_server
from tests.ui.test_ui_sessions import both_ways, connected, ssh_settings, synchronize_window, transfers_ended, trust_server, window
from tests.ui.test_ui_transfers import OLD_TIME, ftp_settings, settled, write_at

# --- Version ------------------------------------------------------------
__version__ = "test_ui_synchronize.py 0.0.44"

# --- Version history ----------------------------------------------------
# v0.0.44: The owner's review: one button that reads Start and then Abort, the window on screen while two folders here are
#          synchronized and abortable from it and by closing it, the queue shown where the work went on a server, the folder counting
#          stopped from its own button, and the words of the window checked one by one (D-097)
# v0.0.43: Initial: block FF, synchronize (FF-01 to FF-14); review round 2: two folders one inside the other are refused before
#          a window opens, and a folder count ends with the window that started it; review round 1: the question before a delete names a folder and what
#          is in it, a scan can be stopped, Reverse is greyed on a move row, and Reverse of a delete copies from the side the file is on

__all__ = ["window"]  # the fixture, used by name
NEW_TIME: float = 1_700_000_000.0
ALL_WINDOW_TEXTS: tuple[str, ...] = tuple(  # every text the two modules of the window can show (OR-33 is about the project, this is about words)
    value
    for module in (comparedialog, synchronizeoptions)
    for name, value in vars(module).items()
    if isinstance(value, str) and name.isupper()
)


def _yes(_parent: object, _title: str, _text: str, *_rest: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def two_sides(tmp_path: Path) -> tuple[Path, Path]:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    return root, here


def row_texts(dialog: CompareDialog, column: int) -> list[str]:
    return [item.text() for number in range(dialog.table.rowCount()) if (item := dialog.table.item(number, column)) is not None]


# --- FF-01, FF-11: the direction starts from the side with the focus ------------------------


def test_the_direction_starts_from_the_side_with_the_focus(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "mine.txt", "mine", OLD_TIME)
    write_at(root / "theirs.txt", "theirs", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        window.focused_side = LOCAL
        dialog = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        assert dialog.choices.chosen_direction() == Direction.TO_THERE and row_texts(dialog, 0) == ["mine.txt"]
        dialog.close()
        dialog.deleteLater()
        window.focused_side = REMOTE
        other = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: other.table.rowCount() == 1)
        assert other.choices.chosen_direction() == Direction.TO_HERE and row_texts(other, 0) == ["theirs.txt"]
        other.close()


def test_every_choice_is_kept_for_next_time(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "a.txt", "a", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        dialog.choices.mode.setCurrentIndex(list(Mode).index(Mode.MIRROR))
        dialog.choices.case.setChecked(False)
        dialog.choices.mask_field.set_text("*.txt")
        dialog.again_button.click()
        qtbot.waitUntil(lambda: not dialog.scanning)
        dialog.close()
    kept = window.settings.synchronize
    assert kept.mode == Mode.MIRROR and not kept.case_matters and kept.mask == "*.txt"
    assert saved_settings.load().synchronize.mode == Mode.MIRROR  # and on disk, for next time (FF-11)


# --- FF-04: the checklist -------------------------------------------------------------------


def test_the_checklist_shows_the_action_and_both_sides_and_counts_what_is_ticked(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "mine.txt", "mine", OLD_TIME)
    write_at(root / "theirs.txt", "theirs!", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 2)
        assert set(row_texts(dialog, 1)) == {"Copy to the server", "Copy to this computer"}
        assert any(text for text in row_texts(dialog, 2)) and any(text for text in row_texts(dialog, 4))  # a size on each side
        assert any(text for text in row_texts(dialog, 3)) and any(text for text in row_texts(dialog, 5))  # a date on each side
        assert "Copy to the server: 1" in dialog.counts.text() and "Copy to this computer: 1" in dialog.counts.text()
        dialog.table.sortItems(1, Qt.SortOrder.AscendingOrder)
        assert row_texts(dialog, 1) == sorted(row_texts(dialog, 1))  # sorted by any column
        dialog.close()


def test_select_by_action_ticks_only_the_rows_of_that_action(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "mine.txt", "mine", OLD_TIME)
    write_at(root / "theirs.txt", "theirs", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 2)
        actions = {action.text(): action for action in dialog.select_menu.actions()}
        actions["Nothing"].trigger()
        assert dialog.ticked() == []
        actions[text_of(Action.COPY_THERE.value)].trigger()
        assert [row.name for row in dialog.ticked()] == ["mine.txt"]
        dialog.close()


# --- FF-05: one row reversed ------------------------------------------------------------------


def test_reverse_turns_the_selected_row_around(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "mine.txt", "mine", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        dialog.table.selectRow(0)
        assert dialog.reverse_button.isEnabled()
        dialog.reverse_button.click()
        assert row_texts(dialog, 1) == ["Copy to this computer"]
        dialog.table.selectRow(0)
        dialog.reverse_button.click()
        assert row_texts(dialog, 1) == ["Copy to the server"]
        dialog.close()


# --- FF-06: the size of a one-sided folder ------------------------------------------------------


def test_a_folder_on_one_side_gets_its_size_counted_in_the_background(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    (here / "tree").mkdir()
    write_at(here / "tree" / "deep.txt", "1234567890", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        qtbot.waitUntil(lambda: dialog.rows[0].size is not None)
        assert dialog.rows[0].size is not None and dialog.rows[0].size.size == 10
        assert "10 bytes" in row_texts(dialog, 2)[0]
        dialog.close()


# --- FF-08, FF-10: the deletes and Start through the queue ---------------------------------------


def test_apply_names_how_many_will_be_deleted_and_a_no_deletes_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    write_at(root / "gone.txt", "gone", OLD_TIME)
    asked: list[str] = []

    def question(_parent: object, _title: str, text: str, *_rest: object) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        dialog.choices.mode.setCurrentIndex(list(Mode).index(Mode.MIRROR))
        dialog.choices.delete_other.setChecked(True)
        dialog.again_button.click()
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
        assert row_texts(dialog, 1) == ["Delete on the server"]
        dialog.start_button.click()
        assert asked and "1 file will be deleted on the server" in asked[0] and "can't be brought back" in asked[0]
        assert window.queue.jobs == [] and (root / "gone.txt").exists()  # No: nothing was queued and nothing is gone
        dialog.close()


def test_apply_sends_the_deletes_and_the_dates_to_the_queue_and_they_run(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    write_at(root / "gone.txt", "gone", OLD_TIME)
    write_at(root / "same.txt", "12345", OLD_TIME)
    write_at(here / "same.txt", "12345", NEW_TIME)
    monkeypatch.setattr(QMessageBox, "question", _yes)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        dialog.choices.mode.setCurrentIndex(list(Mode).index(Mode.MIRROR))
        dialog.choices.delete_other.setChecked(True)
        dialog.again_button.click()
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 2)
        dialog.start_button.click()
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    assert not (root / "gone.txt").exists() and (root / "same.txt").exists()
    assert all(job.state == State.DONE for job in window.queue.jobs), [job.message for job in window.queue.jobs]
    assert any(job.side is not None for job in window.queue.jobs)  # the delete is an item of the queue of its own (FF-10)


def test_timestamps_only_sets_the_date_on_the_server_through_the_queue(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(root / "same.txt", "12345", OLD_TIME)
    write_at(here / "same.txt", "12345", NEW_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        dialog.choices.mode.setCurrentIndex(list(Mode).index(Mode.TIMESTAMPS))
        dialog.again_button.click()
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
        assert row_texts(dialog, 1) == ["Set the date on the server"]
        assert not dialog.choices.delete_other.isEnabled()  # greyed with its reason in timestamps-only mode (FF-08)
        dialog.start_button.click()
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    assert (root / "same.txt").read_text(encoding="utf-8") == "12345"
    assert abs((root / "same.txt").stat().st_mtime - NEW_TIME) < 2


def test_a_move_on_the_server_replaces_an_upload_and_a_delete(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "new-name.txt", "the same bytes", OLD_TIME)
    write_at(root / "old-name.txt", "the same bytes", OLD_TIME)
    monkeypatch.setattr(QMessageBox, "question", _yes)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        dialog.choices.mode.setCurrentIndex(list(Mode).index(Mode.MIRROR))
        dialog.choices.delete_other.setChecked(True)
        dialog.again_button.click()
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
        assert row_texts(dialog, 1) == ["Move on the server"] and "old-name.txt → new-name.txt" in row_texts(dialog, 0)[0]
        dialog.start_button.click()
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    assert (root / "new-name.txt").read_text(encoding="utf-8") == "the same bytes" and not (root / "old-name.txt").exists()


def test_synchronize_over_ftp_plans_and_applies_the_same_way(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "mine.txt", "mine", OLD_TIME)
    write_at(root / "theirs.txt", "theirs", OLD_TIME)
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 2)
        assert not dialog.contents_button.isEnabled()  # no checksums on this FTP server: greyed with its reason (OR-20)
        dialog.start_button.click()
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    assert (root / "mine.txt").exists() and (here / "theirs.txt").exists()


def test_the_question_before_deleting_a_folder_says_it_goes_with_everything_in_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    (root / "tree").mkdir()
    for name in ("one.txt", "two.txt", "three.txt"):
        write_at(root / "tree" / name, name, OLD_TIME)
    asked: list[str] = []

    def question(_parent: object, _title: str, text: str, *_rest: object) -> QMessageBox.StandardButton:
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", question)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        dialog.choices.mode.setCurrentIndex(list(Mode).index(Mode.MIRROR))
        dialog.choices.delete_other.setChecked(True)
        dialog.again_button.click()
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
        qtbot.waitUntil(lambda: dialog.rows[0].size is not None)  # the background count of FF-06 knows what is in it
        dialog.start_button.click()
        assert asked and "1 folder will be deleted" in asked[0]
        assert "deleted with everything in it" in asked[0] and "3 files more here" in asked[0]
        assert (root / "tree" / "one.txt").exists()  # No: nothing was queued and nothing is gone
        dialog.close()


def test_a_scan_can_be_stopped_and_leaves_the_rows_as_they_were(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "a.txt", "a", OLD_TIME)
    running = threading.Event()

    def never_ending(*args: object, **rest: object) -> object:
        """A scan that only ends when it is told to stop: the test never has to wait for a moment that may already have passed."""
        keep_going = args[5] if len(args) > 5 else rest.get("keep_going")
        assert callable(keep_going)
        running.set()
        while keep_going():
            time.sleep(0.01)
        return None  # stopped, exactly as the engine's own scan answers then

    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        rows_before = dialog.table.rowCount()
        monkeypatch.setattr(windowsynchronize, "scan", never_ending)
        dialog.again_button.click()
        assert running.wait(10) and dialog.scanning and dialog.again_button.text() == STOP_COMPARING_TEXT  # the same button stops it (OR-20)
        dialog.again_button.click()  # Stop
        qtbot.waitUntil(lambda: not dialog.scanning)
        assert dialog.status.text() == SCAN_STOPPED_TEXT and dialog.table.rowCount() == rows_before
        assert dialog.again_button.text() == COMPARE_AGAIN_TEXT and dialog.again_button.isEnabled()
        monkeypatch.undo()
        dialog.again_button.click()  # a scan after a stop runs as any other
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
        dialog.close()


def test_reverse_is_greyed_with_its_reason_on_a_move_row(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "new-name.txt", "the same bytes", OLD_TIME)
    write_at(root / "old-name.txt", "the same bytes", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        dialog.choices.mode.setCurrentIndex(list(Mode).index(Mode.MIRROR))
        dialog.choices.delete_other.setChecked(True)
        dialog.again_button.click()
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
        dialog.table.selectRow(0)
        assert not dialog.reverse_button.isEnabled() and dialog.reverse_button.toolTip() == NO_REVERSE_WHY
        assert row_texts(dialog, 1) == ["Move on the server"]  # and the row is untouched
        dialog.close()


def test_reverse_of_a_delete_copies_from_the_side_the_file_is_still_on(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(root / "gone.txt", "only on the server", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        dialog.choices.mode.setCurrentIndex(list(Mode).index(Mode.MIRROR))
        dialog.again_button.click()
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
        dialog.table.selectRow(0)
        dialog.reverse_button.click()
        assert row_texts(dialog, 1) == ["Copy to this computer"]
        dialog.start_button.click()
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    assert (here / "gone.txt").read_text(encoding="utf-8") == "only on the server"  # the file was kept, never asked for from nowhere
    assert all(job.state == State.DONE for job in window.queue.jobs), [job.message for job in window.queue.jobs]


# --- FF-12: Mark Differences ----------------------------------------------------------------


def test_mark_differences_marks_what_differs_in_the_folder_shown(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "same.txt", "same", OLD_TIME)
    write_at(root / "same.txt", "same", OLD_TIME)
    write_at(here / "mine.txt", "mine", OLD_TIME)
    write_at(here / "newer.txt", "newer here", NEW_TIME)
    write_at(root / "newer.txt", "older there", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "same.txt" in window.local_side.file_list.names())
        window.list_actions[f"{LOCAL}_mark_differences"].trigger()
        qtbot.waitUntil(lambda: {entry.name for entry in window.local_side.file_list.selected_entries()} == {"mine.txt", "newer.txt"})
        assert window.queue.jobs == []  # nothing is copied (FF-12)


def test_mark_differences_is_greyed_without_a_live_connection(qtbot: QtBot, window: MainWindow) -> None:
    window.update_actions()
    action = window.list_actions[f"{REMOTE}_mark_differences"]
    assert not action.isEnabled() and action.toolTip()
    assert action.text() == MARK_TEXT
    _ = qtbot


# --- FF-14: two folders on this computer ------------------------------------------------------


def test_two_folders_on_this_computer_use_the_same_window_and_leave_the_right_side_alone(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    write_at(here / "mine.txt", "mine", OLD_TIME)
    write_at(root / "untouched.txt", "untouched", OLD_TIME)
    def picked(_parent: object, _title: str, _start: str, *_rest: object) -> str:
        return str(other)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", picked)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        assert window.list_actions[f"{LOCAL}_sync_folder"].text() == ANOTHER_FOLDER_TEXT
        window.list_actions[f"{LOCAL}_sync_folder"].trigger()
        dialog = window.findChild(CompareDialog)
        assert dialog is not None
        qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
        assert row_texts(dialog, 1) == ["Copy to the other folder"]  # never "the server": this is no server (OR-1)
        dialog.start_button.click()
        qtbot.waitUntil(lambda: (other / "mine.txt").exists())
    assert (other / "mine.txt").read_text(encoding="utf-8") == "mine"
    assert sorted(os.listdir(root)) == ["untouched.txt"]  # the right side of the window was never touched (OR-1, FF-14)


def test_a_folder_inside_the_other_is_refused_before_anything_can_start(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    inside = here / "backup"
    inside.mkdir()
    write_at(here / "mine.txt", "mine", OLD_TIME)
    told: list[str] = []

    def picked(_parent: object, _title: str, _start: str, *_rest: object) -> str:
        return str(inside)

    def note(_parent: object, _title: str, text: str, *_rest: object) -> None:
        told.append(text)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", picked)
    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        before = len(window.findChildren(CompareDialog))
        window.list_actions[f"{LOCAL}_sync_folder"].trigger()
        assert told and "lies inside the other" in told[0]
        assert len(window.findChildren(CompareDialog)) == before  # no window opened, so such a run can't be started at all
        assert sorted(os.listdir(inside)) == []


def test_a_folder_count_of_the_synchronize_window_ends_with_that_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    (here / "tree").mkdir()
    write_at(here / "tree" / "deep.txt", "1234567890", OLD_TIME)
    counting = threading.Event()
    stopped: list[bool] = []

    def never_ending(_path: str, keep_going: object) -> object:
        """A count that only ends when it is told to stop, so the test never waits for a moment that may already have passed."""
        assert callable(keep_going)
        counting.set()
        while keep_going():
            time.sleep(0.01)
        stopped.append(True)
        return None

    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        monkeypatch.setattr(windowsynchronize, "local_count", never_ending)
        dialog = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        assert counting.wait(10)
        dialog.close()  # the window goes: a count nobody can see may never go on
        qtbot.waitUntil(lambda: bool(stopped))


def test_the_folder_counting_has_a_button_on_screen_that_stops_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    (here / "tree").mkdir()
    write_at(here / "tree" / "deep.txt", "1234567890", OLD_TIME)
    counting, may_go_on = threading.Event(), threading.Event()

    def never_ending(_path: str, keep_going: object) -> object:
        assert callable(keep_going)
        counting.set()
        while keep_going():
            time.sleep(0.01)
        may_go_on.set()
        return None

    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        monkeypatch.setattr(windowsynchronize, "local_count", never_ending)
        dialog = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        assert counting.wait(10)
        assert dialog.again_button.text() == STOP_COUNTING_TEXT and dialog.again_button.isEnabled()  # a button they can see (D-097)
        assert "how much is in 1 folder" in dialog.status.text()
        dialog.again_button.click()
        assert may_go_on.wait(10)
        qtbot.waitUntil(lambda: dialog.again_button.text() == COMPARE_AGAIN_TEXT)
        assert dialog.status.text() == COUNTING_STOPPED_TEXT
        dialog.close()


# --- the words and the one button that starts and aborts (D-097) -------------------------------


def test_the_button_reads_start_and_then_abort_and_the_window_stays_while_it_runs(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    for number in range(6):
        write_at(here / f"file{number}.txt", "x" * (number + 1), OLD_TIME)
    busy, may_go_on = threading.Event(), threading.Event()
    copied: list[str] = []
    real_copy = windowsynchronize.on_this_computer

    def slowly(work: object, step: object) -> None:
        busy.set()
        may_go_on.wait(10)
        real_copy(work, step)  # pyrefly: ignore[bad-argument-type]
        copied.append("one")

    def picked(_parent: object, _title: str, _start: str, *_rest: object) -> str:
        return str(other)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", picked)
    window.local_side.go_to(str(here))
    dialog_before = len(window.findChildren(CompareDialog))
    window.list_actions[f"{LOCAL}_sync_folder"].trigger()
    dialog = window.findChildren(CompareDialog)[-1]
    assert len(window.findChildren(CompareDialog)) == dialog_before + 1
    qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 6)
    assert dialog.start_button.text() == START_TEXT and dialog.start_button.isEnabled()
    monkeypatch.setattr(windowsynchronize, "on_this_computer", slowly)
    dialog.start_button.click()
    assert busy.wait(10)
    assert dialog.start_button.text() == ABORT_TEXT and dialog.start_button.isEnabled()  # the same button, now naming Abort
    assert dialog.isVisible()  # the window stays on screen while the work runs
    dialog.start_button.click()  # Abort
    may_go_on.set()
    qtbot.waitUntil(lambda: not dialog.running)
    assert "Aborted" in dialog.status.text() and "stays done" in dialog.status.text()
    assert len(copied) == 1 and len(list(other.iterdir())) == 1  # it ended after the file it was busy with
    assert dialog.start_button.text() == START_TEXT
    dialog.close()
    _ = root


def test_closing_the_window_aborts_a_sync_of_two_folders_here(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    for number in range(6):
        write_at(here / f"file{number}.txt", "x", OLD_TIME)
    busy, may_go_on = threading.Event(), threading.Event()
    steps: list[str] = []

    def slowly(_work: object, _step: object) -> None:
        steps.append("one")
        busy.set()
        may_go_on.wait(10)

    def picked(_parent: object, _title: str, _start: str, *_rest: object) -> str:
        return str(other)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", picked)
    window.local_side.go_to(str(here))
    window.list_actions[f"{LOCAL}_sync_folder"].trigger()
    dialog = window.findChildren(CompareDialog)[-1]
    qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 6)
    monkeypatch.setattr(windowsynchronize, "on_this_computer", slowly)
    dialog.start_button.click()
    assert busy.wait(10)
    dialog.close()  # closing aborts too: nothing is left running
    may_go_on.set()
    qtbot.waitUntil(lambda: not dialog.running)
    qtbot.wait(200)
    assert steps == ["one"]  # it ended after the one it was busy with; the other five never started
    _ = root


def test_start_on_a_server_opens_the_queue_and_says_the_work_went_there(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "mine.txt", "mine", OLD_TIME)
    shown: list[str] = []
    def show(text: str, timeout: int = 0) -> None:
        shown.append(text)

    monkeypatch.setattr(window.status, "showMessage", show)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        assert dialog.start_button.text() == START_TEXT
        dialog.start_button.click()
        assert window.queue_panel.isVisibleTo(window)  # the screen shows where the work went
        assert shown and "went to the queue" in shown[-1]
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    assert (root / "mine.txt").exists()


def test_start_is_greyed_with_its_reason_while_nothing_is_ticked(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "mine.txt", "mine", OLD_TIME)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        qtbot.waitUntil(lambda: dialog.table.rowCount() == 1)
        actions = {action.text(): action for action in dialog.select_menu.actions()}
        actions["Nothing"].trigger()
        assert not dialog.start_button.isEnabled() and dialog.start_button.toolTip() == NOTHING_TICKED_WHY
        actions["Everything"].trigger()
        assert dialog.start_button.isEnabled() and "ticked" in dialog.start_button.toolTip()
        dialog.close()


def test_every_word_of_the_window_says_what_it_does(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    write_at(here / "mine.txt", "mine", OLD_TIME)

    def picked(_parent: object, _title: str, _start: str, *_rest: object) -> str:
        return str(other)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", picked)
    window.local_side.go_to(str(here))
    window.list_actions[f"{LOCAL}_sync_folder"].trigger()
    dialog = window.findChildren(CompareDialog)[-1]
    qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
    headings = [item.text() for number in range(dialog.table.columnCount()) if (item := dialog.table.horizontalHeaderItem(number)) is not None]
    assert headings == ["Name", "What will happen", "Size on this computer", "Date on this computer", "Size on the other folder", "Date on the other folder", "Why"]
    assert "the server" not in dialog.choices.to_there.text()  # two folders here are no server (OR-1)
    assert dialog.choices.to_there.text() == "To the other folder"
    assert "1 thing to do" in dialog.status.text() and "Start" in dialog.status.text()
    for button in (dialog.start_button, dialog.again_button, dialog.reverse_button, dialog.select_button, dialog.contents_button):
        assert button.toolTip(), button.text()  # every button says in plain words what it does
    dialog.close()
    _ = root


def test_no_word_of_the_window_names_a_button_that_is_not_there(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every word a person can read on this window, and every hover text, names only buttons that are on it (D-097)."""
    root, here = two_sides(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    write_at(here / "mine.txt", "mine", OLD_TIME)

    def picked(_parent: object, _title: str, _start: str, *_rest: object) -> str:
        return str(other)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", picked)
    window.local_side.go_to(str(here))
    window.list_actions[f"{LOCAL}_sync_folder"].trigger()
    dialog = window.findChildren(CompareDialog)[-1]
    qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
    on_screen = {button.text().rstrip("…") for button in dialog.findChildren(QPushButton)}
    words: list[str] = [dialog.status.text(), dialog.counts.text()]
    for button in dialog.findChildren(QPushButton):
        words += [button.text(), button.toolTip()]
    for box in dialog.choices.findChildren(QCheckBox) + dialog.choices.findChildren(QRadioButton):
        words += [box.text(), box.toolTip()]
    for drop in dialog.choices.findChildren(QComboBox):
        words += [drop.itemText(number) for number in range(drop.count())]
    for text in (*words, *(module_text for module_text in ALL_WINDOW_TEXTS)):
        assert "Apply" not in text, text
        for programmer_word in ("walk", "Walk", "read through", "Read Through", "the source", "the target", "checksum", "Checksum"):
            assert programmer_word not in text, (programmer_word, text)  # words a person who never read a manual would not use
        # "bytes" stays: it is the size unit every file list in the program already shows.
    assert "Start" in dialog.start_button.text() and "Start" in on_screen


def test_an_abort_after_the_last_step_says_it_finished(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = two_sides(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    write_at(here / "only.txt", "only one step", OLD_TIME)
    busy, may_go_on = threading.Event(), threading.Event()
    real_copy = windowsynchronize.on_this_computer

    def slowly(work: object, step: object) -> None:
        real_copy(work, step)  # pyrefly: ignore[bad-argument-type]
        busy.set()
        may_go_on.wait(10)  # the last step is done; the person presses Abort while the thread is still here

    def picked(_parent: object, _title: str, _start: str, *_rest: object) -> str:
        return str(other)

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", picked)
    window.local_side.go_to(str(here))
    window.list_actions[f"{LOCAL}_sync_folder"].trigger()
    dialog = window.findChildren(CompareDialog)[-1]
    qtbot.waitUntil(lambda: not dialog.scanning and dialog.table.rowCount() == 1)
    monkeypatch.setattr(windowsynchronize, "on_this_computer", slowly)
    dialog.start_button.click()
    assert busy.wait(10)
    dialog.start_button.click()  # Abort, with nothing left to do
    may_go_on.set()
    qtbot.waitUntil(lambda: not dialog.running)
    assert "Aborted" not in dialog.status.text()  # nothing was aborted: it finished (G-2)
    assert dialog.status.text() == "Finished: all 1 were done."
    assert (other / "only.txt").exists()
    dialog.close()
    _ = root


def test_the_two_stops_say_plainly_which_one_stops_which(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    for side in (here, root):
        write_at(side / "same.txt", "the same bytes", OLD_TIME)
    os.utime(root / "same.txt", (NEW_TIME, NEW_TIME))  # the same size, another date: there is something to read through
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 1)
        walking, reading = dialog.again_button.text(), dialog.contents_button.text()
        assert walking == COMPARE_AGAIN_TEXT and reading == CONTENTS_TEXT
        assert "Folders" in walking and "Files" in reading  # one is about folders, the other about files
        assert walking.split()[0] != reading.split()[0]  # and they differ at the first word, which is the word a person reads
        assert STOP_COMPARING_TEXT != STOP_CONTENTS_TEXT
        assert "Folders" in STOP_COMPARING_TEXT and "Files" in STOP_CONTENTS_TEXT
        assert STOP_COMPARING_TEXT.split()[1] != STOP_CONTENTS_TEXT.split()[1]  # "Stop Comparing…" against "Stop Looking…"
        assert dialog.again_button.toolTip() != dialog.contents_button.toolTip()
        dialog.close()


# --- the choices this connection can't carry out ----------------------------------------------


def test_a_criterion_the_connection_can_not_compute_is_greyed_with_its_reason(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = two_sides(tmp_path)
    write_at(here / "a.txt", "a", OLD_TIME)
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        number = list(Criterion).index(Criterion.CHECKSUM)
        assert dialog.choices.criterion.itemData(number, Qt.ItemDataRole.ToolTipRole)  # the reason, as hover text (OR-20)
        model = dialog.choices.criterion.model()
        assert isinstance(model, QStandardItemModel)
        item = model.item(number)
        assert item is not None and not item.isEnabled() and dialog.choices.criterion.count() == len(Criterion)  # greyed, never hidden
        dialog.close()
