from dataclasses import replace
import json
from pathlib import Path
from typing import cast, override

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QMessageBox, QProgressBar
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections.base import CommandResult
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers.options import Answer, Answers, Choice, Conflict
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.afterqueue import CHOICES, CLOSE, Countdown, DISCONNECT, NOT_POSSIBLE_TIPS, POWER_OFF, STAY, SUSPEND, Power, failed_text
from chrono_ftp.ui import mainwindow
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.overwritedialog import OverwriteDialog
from chrono_ftp.transfers.queue import ENDED, HELD_NEXT, HELD_PLACE, HELD_ROOM
from chrono_ftp.ui.queueview import AT_ONCE_TIP, CLOSED_TAB_TEXT, FAILED_HINT, HELD_TEXTS, NOT_CONNECTED_TAB_TEXT, LIMIT_COLUMN, PAUSING_TEXT, PROGRESS_COLUMN, QUEUE_ACTIONS, SPEED_COLUMN, STATE_COLUMN, LimitDialog, QueueView
from chrono_ftp.ui.settingsdialog import SettingsDialog
from chrono_ftp.ui.session import EditedFile, Session
from chrono_ftp.ui.windowqueue import CALLED_OFF_TEXT, DISCONNECTED_TEXT, EDITS_TEXTS, NOT_ALLOWED_TEXTS, STOPPED_TEXT
from tests.fakeservers import USER, ftp_server
from tests.test_engine import Scripted
from tests.ui.test_ui_sessions import connected, window
from tests.ui.test_ui_transfers import settled

# --- Version ------------------------------------------------------------
__version__ = "test_ui_queue.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: The menu of a tab also holds Server Information and Session Log, after a separator (FP-02, FP-03)
# v0.0.37: The menu of a tab also holds Install My Public Key, greyed with its reason on FTP, and no Change Password there (FM-09, FM-10)
# v0.0.28: The menu of a tab says Edit settings…, has an icon and a normal width
# v0.0.27: The right-click menu of a tab: Edit opens the connections window on its connection; greyed when the connection is gone
# v0.0.26: Connect on a connection that has a tab: new settings log in again there, the same settings only switch or reconnect a
#          dropped one, and running transfers are asked about first
# v0.0.13: Initial: the queue's buttons, right-click menu and Delete key, greyed by the selection; pause, resume, retry and the speed
#          limit from the window; the switch to start items by themselves and the queue part of Settings; what happens when the queue
#          is done: disconnect, close, suspend and shut down, with a countdown that can be cancelled and is called off by new work; a
#          choice the computer does not allow is never taken, also by the keys; Stop and Stop All; unsent edits keep Chrono FTP open and
#          the computer on; work that arrived meanwhile keeps the choice; closing with paused items asks; Stop and Delete on a running
#          item; a person-stopped queue takes no step; a retry after a reconnect; the list keeps its place; Stop in the question; the
#          step after the queue through its own call; an armed choice keeps the queue shown; items of a closed tab; Stop All in the panel;
#          the countdown's real clock; a retried save from Edit; items of a tab that is not connected; the mark of a chosen step; Resume
#          All and a failed save; a choice that is not allowed brings back the one before it; "1 second"; the reason only for an item
#          that could go on; a pausing item of a closed tab; a dropped chosen step is told; transfers at once (FC-01): the number in the
#          queue's title bar and in Settings, kept and holding at once; why an item still waits; each running item's own speed; two items
#          side by side in the window; one question window at a time, and Yes to all answers the other item of the action

__all__ = ["window"]  # the fixture of the sessions tests

ALL_ACTIONS: set[str] = {name for name, _text, _icon, _tip, _with_text in QUEUE_ACTIONS}


class FakePower(Power):
    """Never asks the computer: what logind would say, and what was asked of it."""

    def __init__(self, can: bool) -> None:
        self.can: bool = can
        self.asked: list[str] = []

    @override
    def possible(self, action: str) -> bool:
        return self.can

    @override
    def act(self, action: str) -> str:
        self.asked.append(action)
        return ""


def ftp_settings(port: int) -> Connection:
    return Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/")


def standing(window: MainWindow, state: State, name: str = "a") -> Job:
    """A job in the queue in a state the queue never runs by itself (only a waiting job runs)."""
    job = Job(Scripted(CommandResult(0, "", "")), Connection(name="T", host="h"), "/a", "/", True, name, state=state)
    window.queue.add(job)
    window._job_changed(job)
    return job


def choose(window: MainWindow, *jobs: Job) -> None:
    view = window.queue_view
    view.clearSelection()
    for job in jobs:
        row = next(row for row, item in enumerate(view.jobs) if item is job)
        view.selectionModel().select(view.model().index(row, 0), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def offered(window: MainWindow) -> set[str]:
    return {name for name, action in window.queue_panel.actions_by_name.items() if action.isEnabled()}


def empty_queue(window: MainWindow) -> None:
    """The test's own jobs leave, so closing the window has nothing to ask about."""
    for job in window.queue.jobs:
        window.queue.delete(job)


def pick(window: MainWindow, choice: str) -> None:
    box = window.queue_panel.when_done
    box.setCurrentIndex(box.findData(choice))


def test_every_queue_button_has_an_icon_and_a_hover_text_and_is_greyed_with_nothing_in_the_queue(window: MainWindow) -> None:
    actions = window.queue_panel.actions_by_name
    assert set(actions) == ALL_ACTIONS
    assert all(action.toolTip() and not action.icon().isNull() for action in actions.values())
    assert offered(window) == set()
    assert window.queue_panel.start_by_themselves.toolTip() and window.queue_panel.when_done.toolTip()


def test_the_buttons_offer_only_what_applies_to_the_chosen_items(qtbot: QtBot, window: MainWindow) -> None:
    failed = standing(window, State.FAILED, "failed")
    paused = standing(window, State.PAUSED, "paused")
    other = standing(window, State.PAUSED, "other")
    done = standing(window, State.DONE, "done")
    choose(window, failed)
    assert offered(window) == {"retry", "delete", "stop_all", "resume_all", "clear_finished"}
    choose(window, paused)
    assert offered(window) == {"resume", "stop", "move_down", "run_now", "limit", "delete", "stop_all", "resume_all", "clear_finished"}
    choose(window, other)
    assert "move_up" in offered(window) and "move_down" not in offered(window)
    choose(window, paused, other)
    assert "run_now" not in offered(window)  # one item at a time runs next
    assert not {"move_up", "move_down"} & offered(window)  # the whole line is chosen: it can't move as a block
    choose(window, done)
    assert offered(window) == {"delete", "stop_all", "resume_all", "clear_finished"}
    empty_queue(window)
    qtbot.waitUntil(lambda: offered(window) == set())


def test_a_failed_item_shows_its_message_and_says_what_can_be_done(window: MainWindow) -> None:
    failed = standing(window, State.FAILED)
    failed.message = "The folder /missing is not on the server."
    window._job_changed(failed)
    cell = window.queue_view.item(0, STATE_COLUMN)
    assert cell is not None and cell.text() == failed.message and FAILED_HINT in cell.toolTip()
    empty_queue(window)


def test_a_job_that_is_being_paused_says_so(window: MainWindow) -> None:
    running = standing(window, State.RUNNING)
    window.queue.pause(running)
    window._job_changed(running)
    cell = window.queue_view.item(0, STATE_COLUMN)
    assert cell is not None and cell.text() == PAUSING_TEXT
    running.state = State.DONE
    empty_queue(window)


def test_moving_an_item_moves_its_row(window: MainWindow) -> None:
    first, second = standing(window, State.PAUSED, "first"), standing(window, State.PAUSED, "second")
    choose(window, second)
    window.queue_panel.actions_by_name["move_up"].trigger()
    assert window.queue.jobs == [second, first] and window.queue_view.jobs == [second, first]
    assert window.queue_view.selected_jobs() == [second]  # the selection goes with it
    empty_queue(window)


def test_the_delete_key_removes_the_chosen_item_only_where_the_queue_list_has_the_focus(qtbot: QtBot, window: MainWindow) -> None:
    with qtbot.waitExposed(window):
        window.show()
    window.toggle_queue()
    failed = standing(window, State.FAILED)
    choose(window, failed)
    window.local_side.file_list.setFocus()
    qtbot.waitUntil(window.local_side.file_list.hasFocus)
    QTest.keyClick(window.local_side.file_list, Qt.Key.Key_Delete)
    assert window.queue.jobs == [failed]
    window.queue_view.setFocus()
    qtbot.waitUntil(window.queue_view.hasFocus)
    QTest.keyClick(window.queue_view, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: window.queue.jobs == [] and window.queue_view.jobs == [])


def test_the_right_click_menu_offers_every_queue_action_greyed_as_its_button(window: MainWindow) -> None:
    failed = standing(window, State.FAILED)
    choose(window, failed)
    menu = window.queue_menu()
    assert [action.text() for action in menu.actions()] == [text for _name, text, _icon, _tip, _with_text in QUEUE_ACTIONS]
    assert {action.text() for action in menu.actions() if action.isEnabled()} == {"Retry", "Delete", "Clear Finished"}
    assert "Stop All" in [action.text() for action in menu.actions()]  # the same action, the same name as in the toolbar
    menu.deleteLater()
    empty_queue(window)


def test_the_speed_limit_window_gives_the_chosen_items_their_own_limit(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    paused = standing(window, State.PAUSED)

    def answered(dialog: LimitDialog) -> int:
        dialog.follow_queue.setChecked(False)
        dialog.speed.setValue(500)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(LimitDialog, "exec", answered)
    choose(window, paused)
    window.queue_panel.actions_by_name["limit"].trigger()
    assert paused.limit == 500
    window._job_changed(paused)
    cell = window.queue_view.item(0, LIMIT_COLUMN)
    assert cell is not None and cell.text() == "500 KB/s"
    empty_queue(window)


def test_the_limit_window_goes_back_to_the_limit_of_the_status_bar(qtbot: QtBot) -> None:
    dialog = LimitDialog(500)
    qtbot.addWidget(dialog)
    assert dialog.chosen() == 500 and dialog.speed.isEnabled()
    dialog.follow_queue.setChecked(True)
    assert dialog.chosen() is None and not dialog.speed.isEnabled()


def test_pause_resume_and_retry_from_the_window_on_a_real_server(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "big.bin").write_bytes(b"x" * 1024 * 1024)
    (here / "small.txt").write_text("small", encoding="utf-8")
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        window.limit.setValue(64)  # the status bar's limit: a file of 1 MB takes 16 seconds
        big = window._add_job(session, str(here / "big.bin"), "/", True, "big.bin")
        failing = window._add_job(session, str(here / "small.txt"), "/missing", True, "small.txt")
        assert big is not None and failing is not None
        qtbot.waitUntil(lambda: big.done > 0)
        choose(window, big)
        window.queue_panel.actions_by_name["pause"].trigger()
        qtbot.waitUntil(lambda: failing.state == State.FAILED and big.state == State.PAUSED)  # the other one ran meanwhile, side by side
        window.limit.setValue(0)
        choose(window, big)
        window.queue_panel.actions_by_name["resume"].trigger()
        qtbot.waitUntil(lambda: big.state == State.DONE)
        (root / "missing").mkdir()
        choose(window, failing)
        window.queue_panel.actions_by_name["retry"].trigger()
        qtbot.waitUntil(lambda: failing.state == State.DONE)
        settled(qtbot, window)
    assert (root / "big.bin").read_bytes() == (here / "big.bin").read_bytes() and (root / "missing" / "small.txt").is_file()


def test_the_switch_to_start_items_by_themselves_is_kept_in_settings(window: MainWindow) -> None:
    window.queue_panel.start_by_themselves.setChecked(False)
    assert not window.settings.start_by_themselves and not window.queue.start_by_themselves
    assert not saved_settings.load().start_by_themselves
    window.queue_panel.start_by_themselves.setChecked(True)
    assert saved_settings.load().start_by_themselves


def test_settings_keep_the_queue_part(qtbot: QtBot) -> None:
    chosen = saved_settings.Settings(start_by_themselves=False, keep_done_seconds=30, pause_on_error=True)
    dialog = SettingsDialog(chosen)
    qtbot.addWidget(dialog)
    assert dialog.chosen() == chosen
    dialog.keep_done.setValue(0)
    dialog.on_error.setCurrentIndex(0)
    dialog.start_by_themselves.setChecked(True)
    assert dialog.chosen() == saved_settings.Settings(keep_done_seconds=0)
    dialog.at_once.setValue(5)
    assert dialog.chosen().transfers_at_once == 5 and (dialog.at_once.minimum(), dialog.at_once.maximum()) == (1, 9)


def test_the_number_of_transfers_at_once_holds_at_once_and_is_kept(window: MainWindow) -> None:
    box = window.queue_panel.at_once
    assert box.value() == 2 == window.queue.at_once and box.toolTip() == AT_ONCE_TIP
    box.setValue(4)
    assert window.queue.at_once == 4 and window.settings.transfers_at_once == 4 and saved_settings.load().transfers_at_once == 4
    window.settings = saved_settings.Settings(transfers_at_once=1)
    window._apply_settings()  # as Settings does when it is closed with OK
    assert box.value() == 1 and window.queue.at_once == 1


def test_a_number_of_transfers_at_once_that_is_no_number_or_out_of_range_gives_the_standard_one() -> None:
    for wrong in (0, 10, "3", True, None):
        (saved_settings.config_folder() / saved_settings.FILE_NAME).parent.mkdir(parents=True, exist_ok=True)
        (saved_settings.config_folder() / saved_settings.FILE_NAME).write_text(json.dumps({"transfers_at_once": wrong}), encoding="utf-8")
        assert saved_settings.load().transfers_at_once == saved_settings.AT_ONCE


def test_an_item_that_waits_says_why_and_a_running_one_shows_its_own_speed(qtbot: QtBot) -> None:
    view = QueueView()
    qtbot.addWidget(view)
    place, room, fast, after = (Job(Scripted(CommandResult(0, "", "")), Connection(name="T", host="h"), "/a", "/", True, name) for name in ("place", "room", "fast", "next"))
    place.held, room.held, after.held = HELD_PLACE, HELD_ROOM, HELD_NEXT
    fast.state, fast.speed = State.RUNNING, 1024 * 1024
    for job in (place, room, fast, after):
        view.show_job(job)
    for row, held in ((0, HELD_PLACE), (1, HELD_ROOM), (3, HELD_NEXT)):
        cell = view.item(row, STATE_COLUMN)
        assert cell is not None and (cell.text(), cell.toolTip()) == HELD_TEXTS[held]
    speed = view.item(2, SPEED_COLUMN)
    assert speed is not None and speed.text() == "1.0 MB/s"


# --- when the queue is done (FC-04)


def test_disconnect_when_done_ends_the_connection_the_queue_used_once(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        pick(window, DISCONNECT)
        window._add_job(session, str(tmp_path / "a.txt"), "/", True, "a.txt")
        qtbot.waitUntil(lambda: not session.alive)
        assert window.status.currentMessage() == DISCONNECTED_TEXT.format(name="Shop")
        assert window.queue_panel.when_done.choice() == STAY  # once
        assert (root / "a.txt").is_file()
        settled(qtbot, window)


def test_close_when_done_counts_down_and_cancel_keeps_everything(qtbot: QtBot, window: MainWindow) -> None:
    with qtbot.waitExposed(window):
        window.show()
    pick(window, CLOSE)
    window._queue_finished([], 0)
    countdown = window.countdown
    assert countdown is not None and countdown.isVisible() and "30 seconds" in countdown.label.text() and not countdown.failed.isVisible()
    assert window.queue_panel.when_done.choice() == STAY
    assert countdown.timer.isActive() and countdown.timer.interval() == 1000
    qtbot.waitUntil(lambda: "29 seconds" in countdown.label.text(), timeout=3000)  # the real clock counts down
    countdown.reject()
    assert window.isVisible() and not countdown.isVisible() and not countdown.timer.isActive()


def test_close_when_done_closes_the_window_at_the_end_of_the_countdown(qtbot: QtBot, window: MainWindow) -> None:
    with qtbot.waitExposed(window):
        window.show()
    pick(window, CLOSE)
    window._queue_finished([], 0)
    countdown = window.countdown
    assert countdown is not None
    countdown.seconds = 1
    countdown.tick()
    assert not window.isVisible()


def test_suspend_and_shut_down_are_greyed_where_the_computer_does_not_allow_them(window: MainWindow) -> None:
    window.power = FakePower(False)
    box = window.queue_panel.when_done
    pick(window, SUSPEND)
    box.check_choices()
    model = box.model()
    assert isinstance(model, QStandardItemModel)
    for action in (SUSPEND, POWER_OFF):
        item = model.item(box.findData(action))
        assert item is not None and not item.isEnabled() and item.toolTip() == NOT_POSSIBLE_TIPS[action]
    assert box.choice() == STAY  # a choice that is not possible is not kept


def test_shut_down_when_done_asks_the_computer_after_the_countdown_and_names_the_failures(qtbot: QtBot, window: MainWindow) -> None:
    power = FakePower(True)
    window.power = power
    pick(window, POWER_OFF)
    window._queue_finished([], 2)
    countdown = window.countdown
    assert countdown is not None and countdown.failed.text() == failed_text(2) == "2 items could not be transferred."
    countdown.seconds = 1
    countdown.tick()
    assert power.asked == ["PowerOff"]


def test_the_countdown_is_called_off_when_the_queue_has_work_again(qtbot: QtBot, window: MainWindow) -> None:
    power = FakePower(True)
    window.power = power
    pick(window, SUSPEND)
    window._queue_finished([], 0)
    countdown = window.countdown
    assert countdown is not None
    standing(window, State.PAUSED)
    countdown.tick()
    assert not countdown.isVisible() and power.asked == [] and window.status.currentMessage() == CALLED_OFF_TEXT
    empty_queue(window)


def test_suspend_that_became_impossible_meanwhile_does_nothing_and_says_so(window: MainWindow) -> None:
    power = FakePower(True)
    window.power = power
    pick(window, SUSPEND)
    power.can = False  # logind changed its mind before the queue was done
    window._queue_finished([], 0)
    assert window.countdown is None and power.asked == [] and window.status.currentMessage() == NOT_ALLOWED_TEXTS[SUSPEND]


def test_a_choice_the_computer_does_not_allow_is_never_taken_also_without_opening_the_list(qtbot: QtBot, window: MainWindow) -> None:
    window.power = FakePower(False)
    with qtbot.waitExposed(window):
        window.show()
    window.toggle_queue()
    box = window.queue_panel.when_done
    model = box.model()
    assert isinstance(model, QStandardItemModel)
    item = model.item(box.findData(POWER_OFF))
    assert item is not None and not item.isEnabled()  # greyed as soon as the box is shown
    box.setFocus()
    qtbot.waitUntil(box.hasFocus)
    for _press in range(len(CHOICES)):
        QTest.keyClick(box, Qt.Key.Key_Down)  # the arrow keys walk past what is greyed
    box.setCurrentIndex(box.findData(SUSPEND))  # however it is chosen
    assert box.choice() in (STAY, DISCONNECT, CLOSE)


def test_stop_acts_on_the_chosen_items_only_and_stop_all_on_every_item(window: MainWindow) -> None:
    first, second = standing(window, State.PAUSED, "first"), standing(window, State.PAUSED, "second")
    failed = standing(window, State.FAILED, "failed")
    choose(window, failed)
    assert "stop" not in offered(window)  # nothing chosen that could be stopped
    choose(window, first)
    window.queue_panel.actions_by_name["stop"].trigger()
    assert first.state == State.STOPPED and second.state == State.PAUSED
    stop_all = window.actions_by_name["stop_all"]
    assert stop_all.text() == "Stop All" and stop_all.toolTip() and stop_all.isEnabled()
    stop_all.trigger()
    assert second.state == State.STOPPED and failed.state == State.FAILED
    window.update_actions()
    assert not stop_all.isEnabled()
    empty_queue(window)


def unsent_edit(window: MainWindow, tmp_path: Path, pending: bool) -> None:
    session = cast(Session, None)  # never reached: only whether it is sent counts here
    window.edited[str(tmp_path / "copy" / "a.txt")] = EditedFile(session, "/", "a.txt", unsent=pending)


@pytest.mark.parametrize("choice", [CLOSE, POWER_OFF])
def test_unsent_edits_keep_chrono_ftp_open_and_the_computer_on(qtbot: QtBot, window: MainWindow, tmp_path: Path, choice: str) -> None:
    power = FakePower(True)
    window.power = power
    with qtbot.waitExposed(window):
        window.show()
    unsent_edit(window, tmp_path, True)
    pick(window, choice)
    window._queue_finished([], 0)
    assert window.countdown is None and window.isVisible() and power.asked == []
    assert window.status.currentMessage() == EDITS_TEXTS[choice][0]


def test_an_edit_saved_during_the_countdown_still_keeps_the_computer_on(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    power = FakePower(True)
    window.power = power
    pick(window, POWER_OFF)
    window._queue_finished([], 0)
    countdown = window.countdown
    assert countdown is not None
    unsent_edit(window, tmp_path, True)
    countdown.seconds = 1
    countdown.tick()
    assert power.asked == [] and window.status.currentMessage() == EDITS_TEXTS[POWER_OFF][0]


def test_unsent_edits_do_not_hold_a_suspend(window: MainWindow, tmp_path: Path) -> None:
    power = FakePower(True)
    window.power = power
    unsent_edit(window, tmp_path, True)
    pick(window, SUSPEND)
    window._queue_finished([], 0)
    countdown = window.countdown
    assert countdown is not None
    countdown.seconds = 1
    countdown.tick()
    assert power.asked == ["Suspend"]


def test_close_when_done_with_every_edit_sent_closes_without_a_question(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with qtbot.waitExposed(window):
        window.show()
    unsent_edit(window, tmp_path, False)  # open for editing, every save on the server
    pick(window, CLOSE)
    window._queue_finished([], 0)
    countdown = window.countdown
    assert countdown is not None
    countdown.seconds = 1
    countdown.tick()
    assert not window.isVisible()  # a question nobody answers would fail this test


def test_work_that_arrived_meanwhile_keeps_the_choice_for_when_it_is_done(window: MainWindow) -> None:
    pick(window, CLOSE)
    standing(window, State.PAUSED)
    window._queue_finished([], 0)
    assert window.countdown is None and window.queue_panel.when_done.choice() == CLOSE
    empty_queue(window)


# --- review round 3


def test_closing_the_window_with_a_paused_item_asks_first(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    questions: list[str] = []

    def no(_parent: object, _title: str, text: str, *_details: object) -> bool:
        questions.append(text)
        return False

    monkeypatch.setattr(mainwindow, "asked", no)
    standing(window, State.PAUSED)
    assert not window.close()
    assert len(questions) == 1 and "1 transfer is not finished." in questions[0]
    empty_queue(window)


def slow_upload(qtbot: QtBot, window: MainWindow, here: Path, port: int) -> Job:
    (here / "big.bin").write_bytes(b"x" * 1024 * 1024)
    connected(qtbot, window, ftp_settings(port))
    window.limit.setValue(64)
    job = window._add_job(window.sessions[0], str(here / "big.bin"), "/", True, "big.bin")
    assert job is not None
    qtbot.waitUntil(lambda: job.done > 0)
    return job


def test_stop_and_delete_act_on_a_running_item_through_the_buttons(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    with ftp_server(root) as port:
        job = slow_upload(qtbot, window, here, port)
        choose(window, job)
        window.queue_panel.actions_by_name["stop"].trigger()
        qtbot.waitUntil(lambda: job.state == State.STOPPED)
        choose(window, job)
        window.queue_panel.actions_by_name["retry"].trigger()
        qtbot.waitUntil(lambda: job.state == State.RUNNING and job.done > 0)
        choose(window, job)
        window.queue_panel.actions_by_name["delete"].trigger()
        qtbot.waitUntil(lambda: window.queue.jobs == [] and window.queue_view.jobs == [])
        settled_after_delete(qtbot, window)
    assert not (root / "big.bin").exists()  # never a half file under its name


def settled_after_delete(qtbot: QtBot, window: MainWindow) -> None:
    """What the end of the job started on the server (the fresh look at a folder) answered before the server goes."""
    for session in window.sessions:
        answered: list[bool] = []
        session.worker.submit(lambda: None, lambda _result: answered.append(True), lambda _error: answered.append(True))
        qtbot.waitUntil(lambda: bool(answered))


def test_after_the_person_stopped_everything_the_step_is_not_taken_and_the_choice_goes_back_to_stay(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    with qtbot.waitExposed(window):
        window.show()
    with ftp_server(root) as port:
        job = slow_upload(qtbot, window, here, port)
        pick(window, CLOSE)
        window.actions_by_name["stop_all"].trigger()
        qtbot.waitUntil(lambda: job.state == State.STOPPED and window.queue_panel.when_done.choice() == STAY)
        settled(qtbot, window)
    assert window.countdown is None and window.isVisible() and window.status.currentMessage() == STOPPED_TEXT


def test_a_retry_after_a_reconnect_goes_over_the_new_connection_and_closing_the_tab_counts_its_items(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        failing = window._add_job(session, str(here / "a.txt"), "/missing", True, "a.txt")
        assert failing is not None
        qtbot.waitUntil(lambda: failing.state == State.FAILED)
        old = session.remote
        assert old is not None
        window._disconnect(session)
        window.reconnect()
        qtbot.waitUntil(lambda: session.alive and session.remote is not old)
        (root / "missing").mkdir()
        choose(window, failing)
        window.queue_panel.actions_by_name["retry"].trigger()
        qtbot.waitUntil(lambda: failing.state == State.DONE)
        assert failing.remote is session.remote
        held = Job(old, session.connection, str(here / "a.txt"), "/", True, "a.txt", state=State.PAUSED, tab=session)  # still the old one
        window.queue.add(held)
        asked: list[str] = []

        def no(*args: object) -> QMessageBox.StandardButton:
            asked.append(str(args[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", no)
        window.close_tab(0)
        assert len(asked) == 1 and "1 transfer on " in asked[0]
        empty_queue(window)
        settled_after_delete(qtbot, window)
    assert (root / "missing" / "a.txt").is_file()


def test_following_the_queue_keeps_the_scroll_position_and_the_current_row(qtbot: QtBot, window: MainWindow) -> None:
    with qtbot.waitExposed(window):
        window.show()
    window.toggle_queue()
    jobs = [standing(window, State.PAUSED, f"item {number}") for number in range(60)]
    view = window.queue_view
    bar = view.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0)
    view.setCurrentCell(55, 0)
    choose(window, jobs[55])
    bar.setValue(0)  # the person scrolled back to the top: the moved item is far out of sight
    window.queue_panel.actions_by_name["move_up"].trigger()
    assert window.queue.jobs[54] is jobs[55] and bar.value() == 0
    assert view.jobs[view.currentRow()] is jobs[55]
    empty_queue(window)


def test_stop_in_the_question_also_stops_the_paused_jobs_of_the_same_action(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    asking, paused = standing(window, State.RUNNING, "asking"), standing(window, State.PAUSED, "paused")
    paused.answers = asking.answers
    def any_tab(_job: Job) -> object:
        return object()  # its tab is open

    monkeypatch.setattr(window, "_tab_of", any_tab)

    def stop(dialog: OverwriteDialog) -> int:
        dialog.reject()  # closing the question is Stop
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(OverwriteDialog, "exec", stop)
    replies: list[Answer] = []
    window._overwrite_question(asking, Conflict("a.txt", "/", True, 1, 1.0, 1, 1.0, None), replies.append)
    assert paused.state == State.STOPPED and replies and replies[0].choice == Choice.STOP
    asking.state = State.DONE
    empty_queue(window)


# --- review round 4


def test_stopping_one_of_three_items_then_the_rest_finish_takes_the_step(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for name in ("b.txt", "c.txt"):
        (here / name).write_text(name, encoding="utf-8")
    window.queue_panel.at_once.setValue(1)  # one at a time: the second waits while the first runs, so it can be stopped before it ran
    with ftp_server(root) as port:
        first = slow_upload(qtbot, window, here, port)
        session = window.sessions[0]
        second = window._add_job(session, str(here / "b.txt"), "/", True, "b.txt")
        third = window._add_job(session, str(here / "c.txt"), "/", True, "c.txt")
        assert second is not None and third is not None
        pick(window, DISCONNECT)
        qtbot.waitUntil(lambda: any(item is second for item in window.queue_view.jobs))
        choose(window, second)
        window.queue_panel.actions_by_name["stop"].trigger()
        window.limit.setValue(0)
        qtbot.waitUntil(lambda: not session.alive)  # through the queue's own call when it ended by itself
        assert first.state == State.DONE and third.state == State.DONE and second.state == State.STOPPED
        assert window.queue_panel.when_done.choice() == STAY
        settled(qtbot, window)


def test_an_armed_choice_keeps_the_queue_on_the_screen_when_idle(qtbot: QtBot, window: MainWindow) -> None:
    with qtbot.waitExposed(window):
        window.show()
    window.toggle_queue()
    pick(window, CLOSE)
    window._hide_idle_queue()  # what the moment after the last transfer does
    assert window.queue_panel.isVisible()
    pick(window, STAY)
    window._hide_idle_queue()
    assert not window.queue_panel.isVisible()


def test_an_item_whose_tab_is_closed_can_not_go_on_and_says_why(window: MainWindow) -> None:
    paused = standing(window, State.PAUSED)
    paused.tab = object()  # a tab that is no longer open
    choose(window, paused)
    actions = window.queue_panel.actions_by_name
    assert not actions["resume"].isEnabled() and not actions["run_now"].isEnabled() and actions["resume"].toolTip() == CLOSED_TAB_TEXT
    window._again(window.queue.resume)(paused)  # also never through another way in
    assert paused.state == State.PAUSED
    empty_queue(window)


def test_stop_all_in_the_queue_panel_stops_every_item(window: MainWindow) -> None:
    first, second = standing(window, State.PAUSED, "first"), standing(window, State.PAUSED, "second")
    action = window.queue_panel.actions_by_name["stop_all"]
    assert action.text() == window.actions_by_name["stop_all"].text() == "Stop All" and action.isEnabled()
    action.trigger()
    assert first.state == State.STOPPED and second.state == State.STOPPED
    empty_queue(window)


# --- review round 5


def test_a_retried_save_from_edit_counts_as_the_edit_s_save(window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failed = standing(window, State.FAILED)
    edited = EditedFile(cast(Session, None), "/", "a.txt", unsent=True, held=True, last_job=failed)  # refused before, held
    window.edited[str(tmp_path / "copy" / "a.txt")] = edited
    retried: list[Job] = []

    def retry(job: Job) -> bool:
        retried.append(job)
        return True  # it acted: what the queue then does is its own test

    monkeypatch.setattr(window.queue, "retry", retry)
    choose(window, failed)
    window.queue_panel.actions_by_name["retry"].trigger()
    assert retried == [failed] and edited.job is failed and not edited.held
    failed.state = State.DONE  # the retry went well
    window._edit_job_ended(failed)
    assert not edited.pending and not edited.held  # closing and "When done" no longer wait for it
    empty_queue(window)


def test_items_of_a_tab_that_is_not_connected_wait_for_a_reconnect(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        failing = window._add_job(session, str(here / "a.txt"), "/missing", True, "a.txt")
        assert failing is not None
        qtbot.waitUntil(lambda: failing.state == State.FAILED and any(item is failing for item in window.queue_view.jobs))
        window._disconnect(session)
        choose(window, failing)
        retry = window.queue_panel.actions_by_name["retry"]
        assert not retry.isEnabled() and retry.toolTip() == NOT_CONNECTED_TAB_TEXT
        window._again(window.queue.retry)(failing)  # never through another way in either
        assert failing.state == State.FAILED
        settled(qtbot, window)


def test_a_chosen_step_shows_in_the_status_bar_also_with_the_queue_hidden(qtbot: QtBot, window: MainWindow) -> None:
    with qtbot.waitExposed(window):
        window.show()
    window.toggle_queue()
    pick(window, DISCONNECT)
    mark = window.when_done_label
    assert mark.isVisible() and mark.text() == "When done: Disconnect" and "waiting, running or paused" in mark.toolTip()
    window.toggle_queue()  # hidden by hand
    assert not window.queue_panel.isVisible() and mark.isVisible()
    pick(window, STAY)
    assert not mark.isVisible()


# --- review round 6


def test_resume_all_never_counts_a_failed_save_from_edit_as_on_its_way(window: MainWindow, tmp_path: Path) -> None:
    failed = standing(window, State.FAILED)
    edited = EditedFile(cast(Session, None), "/", "a.txt", unsent=True, held=True, last_job=failed)
    window.edited[str(tmp_path / "copy" / "a.txt")] = edited
    window.queue_panel.actions_by_name["resume_all"].trigger()  # it does nothing for a failed item
    assert edited.job is None and edited.held and edited.unsent  # a later save still goes
    empty_queue(window)


def test_a_choice_that_is_not_allowed_brings_back_the_one_before_it(window: MainWindow) -> None:
    window.power = FakePower(False)
    box = window.queue_panel.when_done
    pick(window, DISCONNECT)
    box.setCurrentIndex(box.findData(SUSPEND))  # as the arrow keys or the wheel do
    assert box.choice() == DISCONNECT


# --- review round 7


@pytest.mark.parametrize("action", [CLOSE, SUSPEND, POWER_OFF])
def test_the_countdown_says_1_second_not_1_seconds(qtbot: QtBot, action: str) -> None:
    countdown = Countdown(action, 0, lambda: None, lambda: True, lambda: None, None, 2)
    qtbot.addWidget(countdown)
    countdown.tick()
    assert countdown.label.text().endswith(" 1 second.") and "seconds" not in countdown.label.text()
    countdown.reject()


def test_a_done_item_of_a_closed_tab_gets_no_reason_it_could_not_go_on(window: MainWindow) -> None:
    done = standing(window, State.DONE)
    done.tab = object()
    choose(window, done)
    tips = {name: tip for name, _text, _icon, tip, _with_text in QUEUE_ACTIONS}
    assert window.queue_panel.actions_by_name["resume"].toolTip() == tips["resume"]  # it is done: nothing to go on with
    empty_queue(window)


def test_a_pausing_item_of_a_closed_tab_offers_no_resume(window: MainWindow) -> None:
    pausing = standing(window, State.RUNNING)
    pausing.tab = object()
    window.queue.pause(pausing)
    choose(window, pausing)
    assert not window.queue_panel.actions_by_name["resume"].isEnabled() and not window.queue_panel.actions_by_name["resume_all"].isEnabled()
    pausing.state = State.DONE
    empty_queue(window)


def test_a_chosen_step_dropped_as_no_longer_allowed_is_told(window: MainWindow) -> None:
    power = FakePower(True)
    window.power = power
    pick(window, SUSPEND)
    power.can = False
    window.queue_panel.when_done.check_choices()  # as when the box shows again
    assert window.queue_panel.when_done.choice() == STAY and window.status.currentMessage() == NOT_ALLOWED_TEXTS[SUSPEND]


# --- several transfers at once (FC-01)


def test_two_items_run_side_by_side_in_the_window_each_with_its_own_row_progress_and_speed(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for name in ("a.bin", "b.bin"):
        (here / name).write_bytes(b"x" * 1024 * 1024)
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        window.limit.setValue(128)  # shared: 64 KB/s each, so both run for a while
        items = [window._add_job(session, str(here / name), "/", True, name) for name in ("a.bin", "b.bin")]
        assert all(item is not None for item in items)
        view = window.queue_view

        def shown_running() -> bool:
            rows = [(view.item(row, STATE_COLUMN), view.item(row, SPEED_COLUMN), view.cellWidget(row, PROGRESS_COLUMN)) for row in range(view.rowCount())]
            return len(rows) == 2 and all(state is not None and state.text() == State.RUNNING.value and speed is not None and speed.text().endswith("/s") and isinstance(bar, QProgressBar) and bar.value() > 0 for state, speed, bar in rows)

        qtbot.waitUntil(shown_running, timeout=15000)
        assert [job.name for job in view.jobs] == ["a.bin", "b.bin"]
        window.limit.setValue(0)
        settled(qtbot, window)
    assert all((root / name).read_bytes() == (here / name).read_bytes() for name in ("a.bin", "b.bin"))


def test_one_question_at_a_time_in_the_window_and_yes_to_all_answers_the_other_without_a_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
        (root / name).write_text("old", encoding="utf-8")
        (here / name).write_text("new", encoding="utf-8")
    shown: list[str] = []
    open_now: list[int] = [0]  # question windows open at this moment
    open_at_once: list[int] = []
    answers: dict[str, Choice] = {}

    def answered(dialog: OverwriteDialog) -> int:
        shown.append(dialog.conflict.name)
        open_now[0] += 1
        others = [job for job in window.queue.jobs if job.name != dialog.conflict.name and job.state == State.RUNNING]
        qtbot.waitUntil(lambda: all(job.waiting_for_answer or job.state in ENDED for job in others))  # the other waits for its turn, or is done
        open_at_once.append(open_now[0])  # a second window would have come while this one waited
        dialog.buttons[answers[dialog.conflict.name]].click()
        open_now[0] -= 1
        return dialog.result()

    monkeypatch.setattr(OverwriteDialog, "exec", answered)
    with ftp_server(root) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        asking = TransferOptions()
        answers.update({"a.txt": Choice.YES, "b.txt": Choice.NO})
        first = [window._add_job(session, str(here / name), "/", True, name, asking) for name in ("a.txt", "b.txt")]  # two actions
        qtbot.waitUntil(lambda: all(job is not None and job.state == State.DONE for job in first), timeout=15000)
        assert sorted(shown) == ["a.txt", "b.txt"] and open_at_once == [1, 1]  # one after the other, never two at once
        shown.clear()
        answers.update({"c.txt": Choice.YES_TO_ALL, "d.txt": Choice.YES_TO_ALL})
        together = Answers()  # one action: both items
        second = [window._add_job(session, str(here / name), "/", True, name, asking, together) for name in ("c.txt", "d.txt")]
        qtbot.waitUntil(lambda: all(job is not None and job.state == State.DONE for job in second), timeout=15000)
        assert len(shown) == 1  # Yes to all in the first answered the other without a window
        settled(qtbot, window)
    texts = {name: (root / name).read_text(encoding="utf-8") for name in ("a.txt", "b.txt", "c.txt", "d.txt")}
    assert texts == {"a.txt": "new", "b.txt": "old", "c.txt": "new", "d.txt": "new"}


def test_connect_with_new_settings_logs_in_again_in_the_same_tab(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        old = session.remote
        window.open_connection(replace(ftp_settings(port), colour="#336699"))
        qtbot.waitUntil(lambda: session.alive and session.remote is not old)
        assert window.tab_bar.count() == 1
        assert session.connection.colour == "#336699"


def test_connect_with_the_same_settings_on_a_live_tab_only_switches(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        old = session.remote
        window.open_connection(ftp_settings(port))
        assert session.remote is old
        assert session.alive
        assert window.tab_bar.count() == 1


def test_connect_with_the_same_settings_on_a_dropped_tab_reconnects(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        window._disconnect(session)
        assert not session.alive
        window.open_connection(ftp_settings(port))
        qtbot.waitUntil(lambda: session.alive)
        assert window.tab_bar.count() == 1


def test_connect_with_new_settings_asks_about_running_transfers_and_no_changes_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here = tmp_path / "here"
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, ftp_settings(port))
        session = window.sessions[0]
        old = session.remote
        assert old is not None
        held = Job(old, session.connection, str(here / "a.txt"), "/", True, "a.txt", state=State.PAUSED, tab=session)
        window.queue.add(held)
        asked: list[str] = []

        def no(*args: object) -> QMessageBox.StandardButton:
            asked.append(str(args[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", no)
        window.open_connection(replace(ftp_settings(port), colour="#336699"))
        assert len(asked) == 1 and "Stop it and reconnect?" in asked[0]
        assert session.remote is old
        assert session.connection.colour == ""
        assert held.state == State.PAUSED
        empty_queue(window)
        settled_after_delete(qtbot, window)


def test_the_menu_of_a_tab_has_edit_which_opens_its_connection(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        saved_connections.save([ftp_settings(port), Connection(name="Other", host="other.example.com")])
        with qtbot.waitExposed(window):
            window.show()
        connected(qtbot, window, ftp_settings(port))
        assert window.tab_menu_for(window.tab_bar.rect().bottomRight()) is None  # beside the tabs: no menu
        menu = window.tab_menu_for(window.tab_bar.tabRect(0).center())
        assert menu is not None
        actions = menu.actions()
        assert [action.text() for action in actions] == ["Edit settings…", "Install My Public Key…", "", "Server Information…", "Session Log…"]  # Change Password needs a shell: not on FTP
        assert not actions[1].isEnabled() and actions[1].toolTip()  # a key logs in over SSH: greyed with its reason
        assert not actions[0].icon().isNull()
        assert menu.minimumWidth() >= 10 * menu.fontMetrics().averageCharWidth()
        assert actions[0].isEnabled()
        actions[0].trigger()
        assert window.connection_list is not None and window.connection_list.isVisible()
        assert window.connection_list.name.text() == "Shop"
        assert window.connection_list.host.text() == "127.0.0.1"
        window.connection_list.hide()


def test_edit_is_greyed_with_its_reason_when_the_connection_is_gone(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        saved_connections.save([])
        with qtbot.waitExposed(window):
            window.show()
        connected(qtbot, window, ftp_settings(port))
        menu = window.tab_menu_for(window.tab_bar.tabRect(0).center())
        assert menu is not None
        edit = menu.actions()[0]
        assert not edit.isEnabled()
        assert edit.toolTip()
