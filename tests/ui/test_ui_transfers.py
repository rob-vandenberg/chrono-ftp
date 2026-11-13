"""The transfer window (FB-09), its settings (FB-01), the overwrite question (FB-03) and the Transfers part of Settings, in the real
window against the fake servers."""

from collections.abc import Callable
from pathlib import Path

import os
from dataclasses import replace
import stat
import sys
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QLabel, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_ftp.config import parts
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.parts import PartRecord, upload_key, upload_source
from chrono_ftp.connections.base import Capabilities, CommandResult, RemoteEntry
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.core.connection import FTP, SFTP, Connection
from chrono_ftp.core.transferoptions import RESUME_OFF, SMART_RESUME_BYTES, TransferOptions, rights_valid
from chrono_ftp.transfers.filebyfile import FileWay
from chrono_ftp.transfers.job import PART_SUFFIX, Stopped
from chrono_ftp.transfers.options import KIND_UNKNOWN, NO_CHMOD_TEXT, NO_CUT, NO_MFMT_TEXT, NO_REST_TEXT, SIZE_UNKNOWN, Answer, Answers, Choice, Conflict
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.comparedialog import CompareDialog
from chrono_ftp.ui.plaintext import asked, box_text, fitting, plain_of, told
from chrono_ftp.ui.filelist import FROM_LOCAL, FROM_REMOTE, PARENT_ENTRY
from chrono_ftp.ui.overwritedialog import APPEND_TEXT, NO_APPEND_TEXT, NO_NEW_NAME_TEXT, SAME_NAME_TEXT, NO_KIND_APPEND_TEXT, NO_PART_TEXT, NO_SIZE_APPEND_TEXT, SIZE_UNKNOWN_TEXT, STOP_TEXT, OverwriteDialog
from chrono_ftp.ui.queueview import WAITING_TEXT, QueueView, state_text
from chrono_ftp.ui.remotefolderpicker import CHOOSE_TEXT, INSTEAD_TEXT, LIST_TEXT, LOADING_TEXT, NOTHING_TO_CHOOSE_TEXT, WAIT_TEXT, RemoteFolderPicker
from chrono_ftp.ui.settingsdialog import SettingsDialog
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.transferdialog import NOT_WRITABLE_HERE_TEXT, LOCAL_FILE_TEXT, MOVE_UP_TEXT, MOVE_UP_UNFLUSHED_TEXT, NO_LOCAL_FOLDER_TEXT, NO_LOCAL_FOLDER_YET_TEXT, NOT_WHOLE_PATH_TEXT, TransferDialog
from chrono_ftp.ui.windowbase import SETTINGS_NOT_KEPT_TEXT
from chrono_ftp.ui.windowbase import LOST_MEANWHILE_TEXT, TAB_CLOSED_MEANWHILE_TEXT
from chrono_ftp.ui.windowtransfers import A_FOLDER, CANT_LOOK_HERE_TEXT, IS_A_FILE_HERE_TEXT, IS_A_FILE_TEXT, LINK_CLOSED_HERE_TEXT, LINK_NOWHERE_HERE_TEXT, NOT_A_FOLDER_HERE_TEXT, NOT_CONNECTED_TEXT, UNKNOWN, what_is_there
from chrono_ftp.ui.transferoptionsform import RIGHTS_INCOMPLETE_TEXT, TransferOptionsDialog, TransferOptionsForm
from tests.fakeservers import AS_ROOT, NOT_AS_ROOT, PASSWORD, USER, ftp_server, ssh_server
from tests.test_engine import Scripted
from tests.ui.test_ui_sessions import MAGENTA, both_ways, connected, names_on_the_right, pick_local, select, ssh_settings, synchronize_window, transfers_ended, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_transfers.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: Synchronize through its checklist window and Apply (block FF)
# v0.0.34: The test of two tabs on one login opens two differently named connections, as one connection opens one tab only (D-051)
# v0.0.12: Initial: the transfer window, Transfer settings greyed by the server, the overwrite question, the rights field, Settings; the
#          window starts from what the server can do, ~ and relative folders, Browse both ways, the folder picker without a connection,
#          closing the question is Stop, Yes to all, a download-move refreshes the right side, Edit keeps the rights, Synchronize asks;
#          the picker starts at the nearest folder; a missing server folder is offered to be made; stale questions; Synchronize per way;
#          tidying by the resume setting; never-again kept only on start; an Edit save is never skipped as not newer; names never read as
#          HTML; a typed server folder in its one form; the time left empty while waiting; Synchronize does exactly the ticked rows;
#          Append greyed where it can't be undone; a folder that can't be read, a file as target; long names; the picker's start; No is
#          the question's default; the queue's columns and hover texts; a file as download target; windows go after use; a broken-off
#          append set back by the queue only, never by a tab that connects; why Append is greyed; the top of the server; an FTP link of
#          unknown kind; a stopped upload refreshes the right side, once per job; long names in boxes; the question to make a folder
#          names its connection; the picker says why it starts higher up and its hover text is what it shows; a missing download folder,
#          also in the transfer window; the move's hover text; a size that is unknown; the overwrite question names its connection; a long
#          path in a server's reason; a greyed Choose says why; the problem line goes with a change; Resume of an unknown size;
#          Synchronize after the connection was lost; the set-back by the queue with a note of how the append broke off; boxes when the
#          connection was lost meanwhile; Append's hover text; a folder here made only when the transfer starts; Choose takes the
#          highlighted folder; boxes go after use; a tab lost or closed during the folder check; Rename without a new name; drops on
#          both sides; Enter in the picker's list; what stands where a download folder was asked for; a name taken exactly as typed;
#          a drop on the left from a lost tab; a corrected folder taken as typed; the box of a download lost meanwhile; Settings
#          switches the transfer window off; a download folder that can't be written; a link to a place that can't be opened

__all__ = ["window"]  # the fixture of the sessions tests: a window that lets go of its edits when the test is over

OLD_TIME: float = 1_600_000_000.0


def settled(qtbot: QtBot, window: MainWindow) -> None:
    """Every transfer ended, the window took in its end, and what that end started on a server (the fresh look at a folder) answered: a
    test that leaves its server earlier would see that look fail."""
    qtbot.waitUntil(lambda: transfers_ended(window) and len(window.queue_view.jobs) == len(window.queue.jobs))
    qtbot.waitUntil(lambda: all((item := window.queue_view.item(row, 3)) is not None and item.text() == state_text(job) for row, job in enumerate(window.queue_view.jobs)))
    for session in window.sessions:
        answered: list[bool] = []
        session.worker.submit(lambda: None, lambda _result: answered.append(True), lambda _error: answered.append(True))
        qtbot.waitUntil(lambda: bool(answered))


def write_at(path: Path, text: str, moment: float) -> None:
    path.write_text(text, encoding="utf-8")
    os.utime(path, (moment, moment))


def ftp_settings(port: int) -> Connection:
    return Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/")


def test_upload_opens_the_transfer_window_and_its_choices_reach_the_transfer(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "inner").mkdir(parents=True)
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    shown: list[TransferDialog] = []

    def accept(dialog: TransferDialog) -> int:
        shown.append(dialog)
        assert dialog.folder() == "/" and dialog.windowTitle() == "Upload"
        assert "a.txt" in dialog.what.text() and "Ask before replacing" in dialog.summary.text()
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok is not None and ok.text() == "Upload"
        dialog.target.setText("/inner")
        dialog.move_tick.setChecked(True)
        dialog.never_again.setChecked(True)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "a.txt" in window.local_side.file_list.names())
        pick_local(window, "a.txt")
        window.actions_by_name["upload"].trigger()
        settled(qtbot, window)
        job = window.queue.jobs[0]
        assert job.state == State.DONE, job.message
        assert job.options.delete_source and job.remote_path == "/inner"
        assert (root / "inner" / "a.txt").read_text(encoding="utf-8") == "a"
        assert not (here / "a.txt").exists()  # moved: it arrived, so it went here
        assert not saved_settings.load().show_transfer_window
        (here / "b.txt").write_text("b", encoding="utf-8")
        qtbot.waitUntil(lambda: "b.txt" in window.local_side.file_list.names())
        pick_local(window, "b.txt")
        window.actions_by_name["upload"].trigger()  # no window this time: the defaults, into the folder on the screen
        qtbot.waitUntil(lambda: len(window.queue.jobs) == 2)
        settled(qtbot, window)
        assert len(shown) == 1
        assert (root / "b.txt").exists() and (here / "b.txt").exists()


@pytest.mark.parametrize("make", [True, False])
def test_a_download_to_a_folder_that_is_not_there_is_offered_to_be_made_in_the_window(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make: bool) -> None:
    asked: list[str] = []

    def answer(*args: object) -> QMessageBox.StandardButton:
        asked.append(plain_of(str(args[2])))
        return QMessageBox.StandardButton.Yes if make else QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", answer)
    missing = tmp_path / "missing" / "deeper"
    dialog = TransferDialog(False, ["a.txt"], str(missing), TransferOptions(), {}, lambda start: None)
    qtbot.addWidget(dialog)
    dialog.buttons.accepted.emit()
    assert asked == [NO_LOCAL_FOLDER_YET_TEXT.format(folder=fitting(str(missing), dialog))]
    if make:
        assert dialog.result() == QDialog.DialogCode.Accepted.value and dialog.to_make == str(missing)
        assert not missing.exists()  # made only once the transfer really starts
        return
    assert not missing.exists() and dialog.result() != QDialog.DialogCode.Accepted.value
    assert dialog.problem.text() == NO_LOCAL_FOLDER_TEXT and not dialog.problem.isHidden()
    dialog.target.setText(str(tmp_path))
    dialog.buttons.accepted.emit()
    assert dialog.result() == QDialog.DialogCode.Accepted.value


def test_transfer_settings_grey_out_what_this_ftp_server_can_not_do_and_say_why(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    window.settings = saved_settings.Settings(transfer_options=TransferOptions(set_rights=True))
    seen: list[TransferOptionsForm] = []

    def look(dialog: TransferOptionsDialog) -> int:
        seen.append(dialog.form)
        return QDialog.DialogCode.Rejected.value

    def open_settings(dialog: TransferDialog) -> int:
        dialog.settings_button.click()
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(TransferOptionsDialog, "exec", look)
    monkeypatch.setattr(TransferDialog, "exec", open_settings)
    with ftp_server(tmp_path, without=("REST", "MFMT", "SITE CHMOD")) as port:
        connected(qtbot, window, ftp_settings(port))
        window.local_side.go_to(str(tmp_path))
        qtbot.waitUntil(lambda: "a.txt" in window.local_side.file_list.names())
        pick_local(window, "a.txt")
        window.actions_by_name["upload"].trigger()
    form = seen[0]
    assert not form.resume.isEnabled() and form.resume.toolTip() == NO_REST_TEXT and form.resume.currentData() == RESUME_OFF
    assert not form.keep_timestamps.isEnabled() and form.keep_timestamps.toolTip() == NO_MFMT_TEXT and not form.keep_timestamps.isChecked()
    assert not form.set_rights.isEnabled() and form.set_rights.toolTip() == NO_CHMOD_TEXT and not form.set_rights.isChecked()
    assert not form.rights.isEnabled() and form.rights.toolTip() == NO_CHMOD_TEXT
    assert window.queue.jobs == []  # cancelled: nothing went


def test_the_question_comes_while_the_queue_waits_and_yes_replaces(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "a.txt").write_text("new", encoding="utf-8")
    (here / "a.txt").write_text("old!", encoding="utf-8")
    window.settings = saved_settings.Settings(show_transfer_window=False)
    asked: list[tuple[Conflict, str]] = []

    def yes(dialog: OverwriteDialog) -> int:
        state = window.queue_view.item(0, 3)
        asked.append((dialog.conflict, state.text() if state is not None else ""))
        dialog.buttons[Choice.YES].click()
        return dialog.result()

    monkeypatch.setattr(OverwriteDialog, "exec", yes)
    with ftp_server(root) as port:
        window.local_side.go_to(str(here))
        connected(qtbot, window, ftp_settings(port))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["download"].trigger()
        settled(qtbot, window)
    conflict, state = asked[0]
    assert (conflict.name, conflict.source_size, conflict.target_size, conflict.upload) == ("a.txt", 3, 4, False)
    assert state == WAITING_TEXT
    assert (here / "a.txt").read_text(encoding="utf-8") == "new"


def test_stop_in_the_question_stops_every_item_of_that_transfer(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for name in ("a.txt", "b.txt"):
        (root / name).write_text("new", encoding="utf-8")
        (here / name).write_text("old", encoding="utf-8")
    window.settings = saved_settings.Settings(show_transfer_window=False)
    questions: list[str] = []

    def stop(dialog: OverwriteDialog) -> int:
        questions.append(dialog.conflict.name)
        dialog.reject()  # closing the question is Stop
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(OverwriteDialog, "exec", stop)
    with ftp_server(root) as port:
        window.local_side.go_to(str(here))
        connected(qtbot, window, ftp_settings(port))
        qtbot.waitUntil(lambda: "b.txt" in names_on_the_right(window))
        select(window, "a.txt", "b.txt")
        window.actions_by_name["download"].trigger()
        qtbot.waitUntil(lambda: len(window.queue.jobs) == 2)
        settled(qtbot, window)
    assert len(questions) == 1
    assert [job.state for job in window.queue.jobs] == [State.STOPPED, State.STOPPED]
    assert [(here / name).read_text(encoding="utf-8") for name in ("a.txt", "b.txt")] == ["old", "old"]


def test_the_question_shows_resume_only_for_a_part_that_can_be_continued(qtbot: QtBot) -> None:
    without = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, None))
    qtbot.addWidget(without)
    resume = without.buttons[Choice.RESUME]
    assert not resume.isEnabled() and resume.toolTip() == NO_PART_TEXT
    with_part = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, 4))
    qtbot.addWidget(with_part)
    with_part.buttons[Choice.RESUME].click()
    assert with_part.answer == Answer(Choice.RESUME)


def test_rename_in_the_question_refuses_a_name_with_a_slash_and_takes_a_good_one(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, None))
    qtbot.addWidget(dialog)
    names = iter([("x/y.txt", True), ("b.txt", True)])
    told: list[str] = []
    def typed(*_args: object) -> tuple[str, bool]:
        return next(names)

    def note(*args: object) -> None:
        told.append(str(args[2]))

    monkeypatch.setattr(QInputDialog, "getText", typed)
    monkeypatch.setattr(QMessageBox, "information", note)
    dialog.buttons[Choice.RENAME].click()
    assert dialog.result() != QDialog.DialogCode.Accepted.value and len(told) == 1  # the question stays
    dialog.buttons[Choice.RENAME].click()
    assert dialog.answer == Answer(Choice.RENAME, "b.txt") and dialog.result() == QDialog.DialogCode.Accepted.value


@pytest.mark.parametrize("typed", ["9", "abc", "-1", "6 4 4", "٤٤٤", "77777", "64x4"])
def test_the_rights_field_refuses_what_is_no_octal_number_as_it_is_typed_or_pasted(qtbot: QtBot, typed: str) -> None:
    form = TransferOptionsForm()
    qtbot.addWidget(form)
    form.set_options(TransferOptions(set_rights=True))
    form.rights.clear()
    form.rights.insert(typed)  # as a paste: the field checks it as it does a key
    if typed.isascii():
        QTest.keyClicks(form.rights, typed)  # QTest can only type plain keys
    assert all(character in "01234567" for character in form.rights.text()) and len(form.rights.text()) <= 4
    text = form.rights.text()
    if rights_valid(text):
        assert form.acceptable() and form.chosen().rights == text and form.chosen().set_rights
    else:
        assert not form.acceptable() and form.chosen().rights == "644" and not form.chosen().set_rights  # nothing invalid ever leaves


@pytest.mark.parametrize("set_text", ["999", "", "64", "\udcff", "8" * 10_000])
def test_a_rights_value_that_is_not_valid_never_leaves_the_window(qtbot: QtBot, set_text: str) -> None:
    dialog = SettingsDialog(saved_settings.Settings())
    qtbot.addWidget(dialog)
    dialog.transfer_options.set_rights.setChecked(True)
    dialog.transfer_options.rights.setText(set_text)  # also what no keyboard can type, as a paste or another program might give
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None and not ok.isEnabled() and ok.toolTip() == RIGHTS_INCOMPLETE_TEXT
    chosen = dialog.chosen().transfer_options
    assert chosen.rights == "644" and not chosen.set_rights


def test_settings_keep_the_default_options_and_bring_the_transfer_window_back(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    window.settings = saved_settings.Settings(show_transfer_window=False)

    def accepted(dialog: SettingsDialog) -> int:
        assert not dialog.show_transfer_window.isChecked()
        dialog.show_transfer_window.setChecked(True)
        form = dialog.transfer_options
        assert form.resume.isEnabled() and form.set_rights.isEnabled()  # the defaults hold for every server: nothing is greyed
        form.only_newer.setChecked(True)
        form.set_rights.setChecked(True)
        form.rights.setText("0600")
        return SettingsDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SettingsDialog, "exec", accepted)
    window.actions_by_name["settings"].trigger()
    loaded = saved_settings.load()
    assert loaded.show_transfer_window and window.settings.show_transfer_window
    assert loaded.transfer_options == TransferOptions(only_newer=True, set_rights=True, rights="0600")


def test_settings_can_switch_the_transfer_window_off(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    window.settings = saved_settings.Settings(show_transfer_window=True)

    def accepted(dialog: SettingsDialog) -> int:
        assert dialog.show_transfer_window.isChecked()
        dialog.show_transfer_window.setChecked(False)
        return SettingsDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SettingsDialog, "exec", accepted)
    window.actions_by_name["settings"].trigger()
    assert not saved_settings.load().show_transfer_window and not window.settings.show_transfer_window


def test_browse_in_an_upload_picks_a_folder_on_the_server(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "inner" / "deeper").mkdir(parents=True)
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")

    def walk(picker: RemoteFolderPicker) -> int:
        qtbot.waitUntil(lambda: picker.names == ["inner"])
        item = picker.folders.item(0)
        assert item is not None
        picker.folders.itemActivated.emit(item)
        qtbot.waitUntil(lambda: picker.names == [None, "deeper"])
        return QDialog.DialogCode.Accepted.value

    def browse(dialog: TransferDialog) -> int:
        dialog.browse_button.click()
        assert dialog.folder() == "/inner"
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(RemoteFolderPicker, "exec", walk)
    monkeypatch.setattr(TransferDialog, "exec", browse)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "a.txt" in window.local_side.file_list.names())
        pick_local(window, "a.txt")
        window.actions_by_name["upload"].trigger()
    assert window.queue.jobs == []


def test_the_queue_shows_the_time_left_and_a_move(qtbot: QtBot) -> None:
    view = QueueView()
    qtbot.addWidget(view)
    remote = Scripted(CommandResult(0, "", ""))
    remote.capabilities = Capabilities()
    job = Job(remote, Connection(name="T", host="h"), "/a", "/srv", True, "a", state=State.RUNNING, options=TransferOptions(delete_source=True), seconds_left=125.0, total=10, percent=40)
    view.show_job(job)
    texts = [item.text() if (item := view.item(0, column)) is not None else "" for column in (1, 5)]
    assert texts == ["Upload, then delete the source", "2 min 05 s"]


# --- review round 1


def upload_one(qtbot: QtBot, window: MainWindow, here: Path, name: str) -> None:
    window.local_side.go_to(str(here))
    qtbot.waitUntil(lambda: name in window.local_side.file_list.names())
    pick_local(window, name)
    window.actions_by_name["upload"].trigger()


def test_the_transfer_window_shows_and_starts_from_what_this_server_can_do(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    summaries: list[str] = []

    def accept(dialog: TransferDialog) -> int:
        summaries.append(dialog.summary.text())
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    with ftp_server(root, without=("MFMT",)) as port:
        connected(qtbot, window, ftp_settings(port))
        upload_one(qtbot, window, here, "a.txt")
        settled(qtbot, window)
    assert "New dates" in summaries[0] and "Keep dates" not in summaries[0]  # the default keeps them; this server can't
    assert not window.queue.jobs[0].options.keep_timestamps and window.queue.jobs[0].state == State.DONE


def test_a_connection_lost_while_the_transfer_window_is_open_sends_nothing_and_says_so(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")

    def lost_meanwhile(_dialog: TransferDialog) -> int:
        window.sessions[0].alive = False
        return QDialog.DialogCode.Accepted.value

    boxes: list[str] = []

    def box(*args: object) -> None:
        boxes.append(plain_of(str(args[2])))

    monkeypatch.setattr(TransferDialog, "exec", lost_meanwhile)
    monkeypatch.setattr(QMessageBox, "information", box)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        upload_one(qtbot, window, here, "a.txt")
    said = f"Shop: {LOST_MEANWHILE_TEXT}"
    assert window.queue.jobs == [] and window.status.currentMessage() == said and boxes == [said]  # a box: the person expects something
    assert list(root.iterdir()) == []


def test_a_connection_lost_while_the_download_window_is_open_fetches_nothing_and_says_so_truly(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")

    def lost_meanwhile(_dialog: TransferDialog) -> int:
        window.sessions[0].alive = False
        return QDialog.DialogCode.Accepted.value

    boxes: list[str] = []

    def box(*args: object) -> None:
        boxes.append(plain_of(str(args[2])))

    monkeypatch.setattr(TransferDialog, "exec", lost_meanwhile)
    monkeypatch.setattr(QMessageBox, "information", box)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["download"].trigger()
    assert window.queue.jobs == [] and boxes == ["Shop: The connection was lost meanwhile, so nothing was transferred. Reconnect and try again."]
    assert list(here.iterdir()) == []


def test_browse_in_a_download_picks_a_folder_on_this_computer(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here, other = tmp_path / "server", tmp_path / "here", tmp_path / "other"
    for folder in (root, here, other):
        folder.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    started: list[str] = []

    def pick(_parent: object, _title: str, start: str) -> str:
        started.append(start)
        return str(other)

    def browse(dialog: TransferDialog) -> int:
        dialog.browse_button.click()
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", pick)
    monkeypatch.setattr(TransferDialog, "exec", browse)
    with ssh_server(root) as server:
        trust_server(server)
        window.local_side.go_to(str(here))
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["download"].trigger()
        settled(qtbot, window)
    assert started == [str(here)]
    assert (other / "a.txt").read_text(encoding="utf-8") == "a" and not (here / "a.txt").exists()


def test_a_download_that_moves_takes_the_file_off_the_right_side(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")

    def move(dialog: TransferDialog) -> int:
        dialog.move_tick.setChecked(True)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", move)
    with ssh_server(root) as server:
        trust_server(server)
        window.local_side.go_to(str(here))
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["download"].trigger()
        settled(qtbot, window)
        qtbot.waitUntil(lambda: "a.txt" not in names_on_the_right(window))
    assert (here / "a.txt").exists() and not (root / "a.txt").exists()


def test_yes_to_all_in_the_question_holds_for_every_item_of_the_download(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for name in ("a.txt", "b.txt"):
        (root / name).write_text("new", encoding="utf-8")
        (here / name).write_text("old", encoding="utf-8")
    window.settings = saved_settings.Settings(show_transfer_window=False)
    asked: list[str] = []

    def yes_to_all(dialog: OverwriteDialog) -> int:
        asked.append(dialog.conflict.name)
        dialog.buttons[Choice.YES_TO_ALL].click()
        return dialog.result()

    monkeypatch.setattr(OverwriteDialog, "exec", yes_to_all)
    with ftp_server(root) as port:
        window.local_side.go_to(str(here))
        connected(qtbot, window, ftp_settings(port))
        qtbot.waitUntil(lambda: "b.txt" in names_on_the_right(window))
        select(window, "a.txt", "b.txt")
        window.actions_by_name["download"].trigger()
        qtbot.waitUntil(lambda: len(window.queue.jobs) == 2)
        settled(qtbot, window)
    assert len(asked) == 1
    assert [(here / name).read_text(encoding="utf-8") for name in ("a.txt", "b.txt")] == ["new", "new"]


def closed_by_its_title_bar(dialog: OverwriteDialog) -> None:
    dialog.close()


def closed_with_esc(dialog: OverwriteDialog) -> None:
    QTest.keyClick(dialog, Qt.Key.Key_Escape)


def test_closing_the_question_or_pressing_esc_is_the_same_as_stop(qtbot: QtBot) -> None:
    for close in (closed_by_its_title_bar, closed_with_esc):
        dialog = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, None))
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.answer = Answer(Choice.YES)  # whatever came before: closing stops
        close(dialog)
        assert dialog.answer == Answer(Choice.STOP) and dialog.result() == QDialog.DialogCode.Rejected.value
    assert dialog.buttons[Choice.STOP].toolTip() == STOP_TEXT and "also the other items" in STOP_TEXT and "Esc" in STOP_TEXT


def test_the_folder_picker_takes_only_a_folder_that_is_shown(qtbot: QtBot) -> None:
    waiting: list[Callable[[list[RemoteEntry]], None]] = []
    picker = RemoteFolderPicker("/srv", lambda folder, shown, failed: waiting.append(shown))
    qtbot.addWidget(picker)
    assert not picker.choose.isEnabled() and picker.choose.toolTip() == WAIT_TEXT  # still loading
    waiting[0]([RemoteEntry("inner", True)])
    assert picker.choose.isEnabled() and picker.chosen() == "/srv"
    item = picker.folders.item(1)
    assert item is not None
    picker.folders.itemActivated.emit(item)
    assert not picker.choose.isEnabled()  # /srv/inner is loading: Choose can't take /srv while the list says otherwise


@pytest.mark.parametrize("answer", ["shown", "failed"])
def test_a_folder_picker_closed_while_it_loads_takes_no_late_answer(qtbot: QtBot, answer: str) -> None:
    """Closed and deleted before the server answered: the answer that comes later goes nowhere, and nothing fails."""
    waiting: list[tuple[Callable[[list[RemoteEntry]], None], Callable[[str, bool], None]]] = []
    picker = RemoteFolderPicker("/srv", lambda folder, shown, failed: waiting.append((shown, failed)))
    picker.reject()
    picker.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    shown, failed = waiting[0]
    if answer == "shown":
        shown([RemoteEntry("inner", True)])
    else:
        failed("The folder is not there.", True)
    assert len(waiting) == 1  # no folder above was tried either


def test_the_folder_picker_says_when_the_connection_is_lost(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, bool]] = []

    def look(picker: RemoteFolderPicker) -> int:
        seen.append((picker.place.text(), picker.choose.isEnabled()))
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(RemoteFolderPicker, "exec", look)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        session.alive = False
        assert window._pick_remote_folder(session, "/") is None
    assert seen == [(NOT_CONNECTED_TEXT, False)]


def test_a_local_target_takes_the_home_folder_and_refuses_a_relative_path(qtbot: QtBot, tmp_path: Path) -> None:
    dialog = TransferDialog(False, ["a.txt"], "~", TransferOptions(), {}, lambda start: None)
    qtbot.addWidget(dialog)
    assert dialog.folder() == os.path.expanduser("~")
    dialog.buttons.accepted.emit()
    assert dialog.result() == QDialog.DialogCode.Accepted.value
    relative = TransferDialog(False, ["a.txt"], "some/folder", TransferOptions(), {}, lambda start: None)
    qtbot.addWidget(relative)
    relative.buttons.accepted.emit()
    assert relative.result() != QDialog.DialogCode.Accepted.value and relative.problem.text() == NOT_WHOLE_PATH_TEXT


@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_a_corrected_folder_is_taken_as_typed_and_an_untouched_one_is_the_folder_shown(qtbot: QtBot, upload: bool) -> None:
    for shown in ("/a ", "/f\udcffd "):  # a name that ends in a space, and one that is not valid UTF-8 as well
        untouched = TransferDialog(upload, ["x"], shown, TransferOptions(), {}, lambda start: None)
        qtbot.addWidget(untouched)
        assert untouched.folder() == shown
        corrected = TransferDialog(upload, ["x"], shown, TransferOptions(), {}, lambda start: None)
        qtbot.addWidget(corrected)
        corrected.target.setText("/a")
        assert corrected.folder() == "/a"


def test_browse_starts_at_the_real_folder_also_when_its_name_is_not_utf8(qtbot: QtBot) -> None:
    started: list[str] = []

    def browse(start: str) -> str | None:
        started.append(start)
        return "/b\udcffd"

    dialog = TransferDialog(True, ["a.txt"], "/f\udcffd", TransferOptions(), {}, browse)
    qtbot.addWidget(dialog)
    dialog.browse_button.click()
    assert started == ["/f\udcffd"] and dialog.folder() == "/b\udcffd"


def test_a_save_from_edit_never_changes_the_rights_on_the_server(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    (root / "a.txt").chmod(0o644)
    window.settings = saved_settings.Settings(transfer_options=TransferOptions(set_rights=True, rights="600"))
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        Path(opened[0]).write_text("new", encoding="utf-8")
        settled(qtbot, window)
        assert window.queue.jobs[0].state == State.DONE
    assert (root / "a.txt").read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE((root / "a.txt").stat().st_mode) == 0o644


def test_the_folder_picker_starts_at_the_nearest_folder_that_is_there(qtbot: QtBot) -> None:
    there = {"/": [RemoteEntry("a", True)], "/a": [RemoteEntry("x", True)]}

    def load(folder: str, shown: Callable[[list[RemoteEntry]], None], failed: Callable[[str, bool], None]) -> None:
        if folder in there:
            shown(there[folder])
        else:
            failed("No such folder", True)  # not there

    picker = RemoteFolderPicker("/a/b/c", load)
    qtbot.addWidget(picker)
    assert picker.chosen() == "/a" and picker.choose.isEnabled()
    assert picker.names == [None, "x"]  # with the way up
    said = "/a\n" + INSTEAD_TEXT.format(folder="/a/b/c", reason="No such folder")
    assert picker.place.text() == said and plain_of(picker.place.toolTip()) == said  # which folder it could not show, and why


def test_the_folder_picker_hover_text_is_always_what_it_shows(qtbot: QtBot) -> None:
    waiting: list[tuple[Callable[[list[RemoteEntry]], None], Callable[[str, bool], None]]] = []
    picker = RemoteFolderPicker("/" + LONG_NAME, lambda folder, shown, failed: waiting.append((shown, failed)))
    qtbot.addWidget(picker)
    assert picker.place.text() == LOADING_TEXT and picker.place.toolTip() == ""  # nothing to hover over while it loads
    waiting[0][0]([RemoteEntry("inner", True)])
    assert "…" in picker.place.text() and plain_of(picker.place.toolTip()) == "/" + LONG_NAME  # shortened, and whole in the hover text
    picker.open_folder("/" + LONG_NAME + "/inner")
    assert picker.place.text() == LOADING_TEXT and picker.place.toolTip() == ""  # never the hover text of the folder before
    waiting[1][1]("Not allowed", False)
    assert plain_of(picker.place.toolTip()) == "/" + LONG_NAME + "\nNot allowed" and picker.place.text().endswith("\nNot allowed")


@pytest.mark.parametrize("make", [True, False])
def test_an_upload_to_a_server_folder_that_is_not_there_asks_to_make_it_first(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    asked: list[str] = []

    def to_a_new_folder(dialog: TransferDialog) -> int:
        dialog.target.setText("/new/deeper")
        return QDialog.DialogCode.Accepted.value

    def answer(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.Yes if make else QMessageBox.StandardButton.No

    monkeypatch.setattr(TransferDialog, "exec", to_a_new_folder)
    monkeypatch.setattr(QMessageBox, "question", answer)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        upload_one(qtbot, window, here, "a.txt")
        qtbot.waitUntil(lambda: bool(asked))
        if make:
            settled(qtbot, window)
    assert "isn't on the server of Shop yet" in asked[0] and "/new/deeper" in asked[0]
    if make:
        assert window.queue.jobs[0].state == State.DONE and (root / "new" / "deeper" / "a.txt").exists()
    else:
        assert window.queue.jobs == [] and not (root / "new").exists()


def test_a_question_whose_job_was_stopped_or_whose_tab_closed_is_never_shown_and_stops_nothing_else(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        assert session.remote is not None
        shared = Answers()
        stopped = Job(session.remote, session.connection, "/a", "/", True, "a", state=State.RUNNING, answers=shared)
        stopped.stop_asked.set()
        other = Job(session.remote, session.connection, "/b", "/", True, "b", state=State.RUNNING, answers=shared)
        replies: list[Answer] = []
        conflict = Conflict("a", "/", True, 1, None, 1, None, None)
        window._overwrite_question(stopped, conflict, replies.append)  # an unanswered window would fail this test
        closed_tab = Job(Scripted(CommandResult(0, "", "")), session.connection, "/c", "/", True, "c", state=State.RUNNING, answers=shared)
        window._overwrite_question(closed_tab, conflict, replies.append)
    assert replies == [Answer(Choice.STOP), Answer(Choice.STOP)]
    assert not other.stop_asked.is_set()


def test_tidying_in_the_window_keeps_only_a_part_file_that_can_still_be_continued(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    hour_ago = time.time() - 2 * 3600
    for name in ("big.bin", "small.bin"):
        part = root / (name + PART_SUFFIX)
        part.write_bytes(b"x" * 10)
        os.utime(part, (hour_ago, hour_ago))
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        for name, size in (("big.bin", SMART_RESUME_BYTES), ("small.bin", 10)):
            source = tmp_path / name
            source.write_bytes(b"s" * size)
            parts.remember(upload_key(session.connection, f"/{name}{PART_SUFFIX}"), PartRecord(upload_source(str(source)), size, source.stat().st_mtime, 10))
            session.leftovers[f"/{name}{PART_SUFFIX}"] = (time.time() - 3600, 10)  # seen long ago, unchanged: nothing writes it

        def reloaded_with(options: TransferOptions) -> None:
            window.settings = saved_settings.Settings(transfer_options=options)
            waiting = RemoteEntry("not read yet", False)
            session.entries = (waiting,)
            window._reload(session)
            qtbot.waitUntil(lambda: waiting not in session.entries)  # the folder as read and tidied, also when it ends up empty

        reloaded_with(TransferOptions())  # smart: the part of a source of 100 KB stays, that of a small one goes
        assert (root / ("big.bin" + PART_SUFFIX)).exists() and not (root / ("small.bin" + PART_SUFFIX)).exists()
        reloaded_with(TransferOptions(resume=RESUME_OFF))
        assert not (root / ("big.bin" + PART_SUFFIX)).exists()


def test_dont_show_again_is_kept_only_when_the_transfer_starts(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")

    def never_again_then_lost(dialog: TransferDialog) -> int:
        dialog.never_again.setChecked(True)
        window.sessions[0].alive = False
        return QDialog.DialogCode.Accepted.value

    boxes: list[str] = []

    def box(*args: object) -> None:
        boxes.append(plain_of(str(args[2])))

    monkeypatch.setattr(TransferDialog, "exec", never_again_then_lost)
    monkeypatch.setattr(QMessageBox, "information", box)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        upload_one(qtbot, window, here, "a.txt")
    assert window.queue.jobs == [] and boxes == [f"Shop: {LOST_MEANWHILE_TEXT}"]
    assert window.settings.show_transfer_window and saved_settings.load().show_transfer_window


def test_dont_show_again_that_can_not_be_kept_says_so_and_the_transfer_goes(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")

    def never_again(dialog: TransferDialog) -> int:
        dialog.never_again.setChecked(True)
        return QDialog.DialogCode.Accepted.value

    def full_disk(_settings: saved_settings.Settings) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(TransferDialog, "exec", never_again)
    monkeypatch.setattr(saved_settings, "save", full_disk)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        upload_one(qtbot, window, here, "a.txt")
        assert window.status.currentMessage() == SETTINGS_NOT_KEPT_TEXT
        settled(qtbot, window)
    assert (root / "a.txt").exists() and not window.settings.show_transfer_window  # for now, as chosen


def test_a_save_from_edit_is_sent_even_when_the_server_copy_looks_newer(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    window.settings = saved_settings.Settings(transfer_options=TransferOptions(only_newer=True))
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        later = time.time() + 3600
        os.utime(root / "a.txt", (later, later))  # a clock ahead on the server: its copy looks newer than the save
        Path(opened[0]).write_text("saved", encoding="utf-8")
        settled(qtbot, window)
    assert (root / "a.txt").read_text(encoding="utf-8") == "saved"


def test_synchronize_does_exactly_the_ticked_rows_without_a_question_or_only_newer(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    write_at(root / "a.txt", "short", OLD_TIME)
    write_at(here / "a.txt", "the longer one", OLD_TIME)  # not newer: "only newer" alone would leave it
    window.settings = saved_settings.Settings(transfer_options=TransferOptions(only_newer=True, ask_before_replacing=True))
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 1)
        row = dialog.table.item(0, 0)
        assert row is not None
        row.setCheckState(Qt.CheckState.Checked)  # the person ticks it: that is the decision, no question follows
        dialog.apply_button.click()
        settled(qtbot, window)
    assert (root / "a.txt").read_text(encoding="utf-8") == "the longer one"
    assert (root / "a.txt").stat().st_mtime == OLD_TIME  # the dates of Settings still hold


# --- review round 3

ODD_NAME: str = "a<b>x<i>&amp;.txt"  # HTML would show x bold and the rest in italics, and &amp; as &


def test_names_are_shown_as_written_never_as_html(qtbot: QtBot) -> None:
    dialog = TransferDialog(True, [ODD_NAME], "/", TransferOptions(), {}, lambda start: None)
    qtbot.addWidget(dialog)
    assert dialog.what.textFormat() == Qt.TextFormat.PlainText and ODD_NAME in dialog.what.text()
    question = OverwriteDialog(Conflict(ODD_NAME, "/b<i>c</i>", True, 1, OLD_TIME, 1, OLD_TIME, None))
    qtbot.addWidget(question)
    labels = [label for label in question.findChildren(QLabel) if ODD_NAME in label.text()]
    assert labels and all(label.textFormat() == Qt.TextFormat.PlainText for label in labels)
    shown: list[Callable[[list[RemoteEntry]], None]] = []
    picker = RemoteFolderPicker("/b<i>c</i>", lambda folder, ready, failed: shown.append(ready))
    qtbot.addWidget(picker)
    shown[0]([])
    assert picker.place.textFormat() == Qt.TextFormat.PlainText and picker.place.text() == "/b<i>c</i>"
    assert plain_of(box_text(ODD_NAME + "\nnext line")) == ODD_NAME + "\nnext line"
    assert "<b>" not in box_text(ODD_NAME) and "&lt;b&gt;" in box_text(ODD_NAME) and "&amp;amp;" in box_text(ODD_NAME)


def test_a_message_box_about_a_server_file_shows_its_name_as_written(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ODD_NAME).write_text("x", encoding="utf-8")
    asked: list[str] = []

    def no(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: ODD_NAME in names_on_the_right(window))
        select(window, ODD_NAME)
        window.actions_by_name["delete"].trigger()
    assert ODD_NAME in plain_of(asked[0]) and "<b>x" not in asked[0] and "&amp;lt;" not in asked[0]  # the name, not bold text
    assert (tmp_path / ODD_NAME).exists()


def test_a_typed_server_folder_counts_in_its_one_form_and_the_list_shows_the_upload(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for typed, meant in (("/a/", "/a"), ("/a//", "/a"), ("/a/./", "/a"), ("/a/b/..", "/a/b/..")):  # .. is the server's to resolve
        dialog = TransferDialog(True, ["x"], typed, TransferOptions(), {}, lambda start: None)
        qtbot.addWidget(dialog)
        assert dialog.folder() == meant
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "inner").mkdir(parents=True)
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")

    def with_a_trailing_slash(dialog: TransferDialog) -> int:
        dialog.target.setText("/inner/./")
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", with_a_trailing_slash)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/inner")
        upload_one(qtbot, window, here, "a.txt")
        settled(qtbot, window)
        assert window.queue.jobs[0].remote_path == "/inner"
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))  # the list on the screen took in the upload


def test_the_time_left_is_empty_while_the_job_waits_for_an_answer(qtbot: QtBot) -> None:
    view = QueueView()
    qtbot.addWidget(view)
    job = Job(Scripted(CommandResult(0, "", "")), Connection(name="T", host="h"), "/a", "/srv", True, "a", state=State.RUNNING, seconds_left=125.0, total=10)
    job.waiting_for_answer = True
    view.show_job(job)
    item = view.item(0, 5)
    assert item is not None and item.text() == ""
    job.waiting_for_answer = False
    view.show_job(job)
    item = view.item(0, 5)
    assert item is not None and item.text() == "2 min 05 s"


# --- review round 4

LONG_NAME: str = "x" * 250  # no space anywhere to break at


@pytest.mark.parametrize(("reason", "said"), [(NO_CUT, NO_APPEND_TEXT), (SIZE_UNKNOWN, NO_SIZE_APPEND_TEXT), (KIND_UNKNOWN, NO_KIND_APPEND_TEXT)])
def test_append_is_not_offered_where_a_broken_off_append_could_not_be_undone_and_says_why(qtbot: QtBot, reason: str, said: str) -> None:
    dialog = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, None, reason))
    qtbot.addWidget(dialog)
    append = dialog.buttons[Choice.APPEND]
    assert not append.isEnabled() and append.toolTip() == said
    assert dialog.buttons[Choice.NO].isDefault()


def test_an_upload_into_a_folder_that_takes_files_but_can_not_be_read_is_sent_without_a_question(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "incoming").mkdir(parents=True)
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")

    def into_incoming(dialog: TransferDialog) -> int:
        dialog.target.setText("/incoming")
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", into_incoming)  # an unanswered "Make it?" would fail this test
    with ftp_server(root, write_only=("incoming",)) as port:
        connected(qtbot, window, ftp_settings(port))
        upload_one(qtbot, window, here, "a.txt")
        qtbot.waitUntil(lambda: bool(window.queue.jobs))
        settled(qtbot, window)
    assert window.queue.jobs[0].state == State.DONE, window.queue.jobs[0].message
    assert (root / "incoming" / "a.txt").read_text(encoding="utf-8") == "a"


def test_an_upload_to_a_path_that_is_a_file_says_so_and_sends_nothing(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "notes.txt").write_text("n", encoding="utf-8")
    (here / "a.txt").write_text("a", encoding="utf-8")
    told: list[str] = []

    def to_the_file(dialog: TransferDialog) -> int:
        dialog.target.setText("/notes.txt")
        return QDialog.DialogCode.Accepted.value

    def note(*args: object) -> None:
        told.append(plain_of(str(args[2])))

    monkeypatch.setattr(TransferDialog, "exec", to_the_file)
    monkeypatch.setattr(QMessageBox, "information", note)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        upload_one(qtbot, window, here, "a.txt")
        qtbot.waitUntil(lambda: bool(told))
    assert told == [IS_A_FILE_TEXT.format(connection="Shop", folder="/notes.txt")]
    assert window.queue.jobs == [] and (root / "notes.txt").read_text(encoding="utf-8") == "n"


def screen_width() -> int:
    screen = QGuiApplication.primaryScreen()
    assert screen is not None
    return screen.availableGeometry().width()


def test_a_long_name_keeps_every_window_on_the_screen(qtbot: QtBot) -> None:
    transfer = TransferDialog(True, [LONG_NAME], "/" + LONG_NAME, TransferOptions(), {}, lambda start: None)
    question = OverwriteDialog(Conflict(LONG_NAME, "/" + LONG_NAME, True, 1, OLD_TIME, 1, OLD_TIME, None))
    shown: list[Callable[[list[RemoteEntry]], None]] = []
    picker = RemoteFolderPicker("/" + LONG_NAME, lambda folder, ready, failed: shown.append(ready))
    for widget in (transfer, question, picker):
        qtbot.addWidget(widget)
    shown[0]([])
    for widget in (transfer, question, picker):
        assert widget.sizeHint().width() <= screen_width(), type(widget).__name__
    assert LONG_NAME in plain_of(transfer.what.toolTip())  # the whole name is one hover away


def test_the_folder_picker_starts_at_an_absolute_folder(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    picker = RemoteFolderPicker("relative/folder", lambda folder, ready, failed: None)
    qtbot.addWidget(picker)
    assert picker.wanted == "/"
    started: list[str] = []

    def look(dialog: RemoteFolderPicker) -> int:
        started.append(dialog.wanted)
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(RemoteFolderPicker, "exec", look)
    (tmp_path / "home").mkdir()
    with ssh_server(tmp_path) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        session.home_folder = "/home"
        assert window._pick_remote_folder(session, "relative/folder") is None
    assert started == ["/home"]


# --- review round 5


def test_no_key_of_the_question_ever_replaces_a_file(qtbot: QtBot) -> None:
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
        dialog = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, None))
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitUntil(dialog.isVisible)
        assert dialog.buttons[Choice.NO].isDefault() and dialog.focusWidget() is dialog.buttons[Choice.NO]
        QTest.keyClick(dialog.buttons[Choice.NO], key)
        assert dialog.answer == Answer(Choice.NO)


def test_the_queue_columns_fit_their_texts_and_every_cell_has_its_whole_text_as_hover_text(qtbot: QtBot) -> None:
    view = QueueView()
    qtbot.addWidget(view)
    job = Job(Scripted(CommandResult(0, "", "")), Connection(name="T", host="h"), "/a", "/srv", False, ODD_NAME, state=State.RUNNING, options=TransferOptions(delete_source=True))
    job.waiting_for_answer = True
    view.show_job(job)
    metrics = view.fontMetrics()
    for column, text in ((1, "Download, then delete the source"), (3, WAITING_TEXT)):
        item = view.item(0, column)
        assert item is not None and item.text() == text
        assert view.columnWidth(column) >= metrics.horizontalAdvance(text)
        assert plain_of(item.toolTip()) == text
    name = view.item(0, 0)
    assert name is not None and plain_of(name.toolTip()) == ODD_NAME


def test_a_download_target_that_is_a_file_is_named_so(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("n", encoding="utf-8")
    dialog = TransferDialog(False, ["a.txt"], str(tmp_path / "notes.txt"), TransferOptions(), {}, lambda start: None)
    qtbot.addWidget(dialog)
    dialog.buttons.accepted.emit()
    assert dialog.result() != QDialog.DialogCode.Accepted.value and dialog.problem.text() == LOCAL_FILE_TEXT


@NOT_AS_ROOT
def test_a_download_folder_that_can_not_be_written_is_said_so_before_anything_starts(qtbot: QtBot, tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        dialog = TransferDialog(False, ["a.txt"], str(locked), TransferOptions(), {}, lambda start: None)
        qtbot.addWidget(dialog)
        dialog.buttons.accepted.emit()
        assert dialog.result() != QDialog.DialogCode.Accepted.value and dialog.problem.text() == NOT_WRITABLE_HERE_TEXT
    finally:
        locked.chmod(0o700)


def test_the_windows_of_a_transfer_go_again_after_use(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    for name in ("a.txt", "b.txt"):
        (root / name).write_text("new", encoding="utf-8")
        (here / name).write_text("old", encoding="utf-8")

    def accept(_dialog: TransferDialog) -> int:
        return QDialog.DialogCode.Accepted.value

    def no(dialog: OverwriteDialog) -> int:
        dialog.buttons[Choice.NO].click()
        return dialog.result()

    def cancel(_picker: RemoteFolderPicker) -> int:
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    monkeypatch.setattr(OverwriteDialog, "exec", no)
    monkeypatch.setattr(RemoteFolderPicker, "exec", cancel)
    with ssh_server(root) as server:
        trust_server(server)
        window.local_side.go_to(str(here))
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "b.txt" in names_on_the_right(window))
        select(window, "a.txt", "b.txt")
        window.actions_by_name["download"].trigger()
        qtbot.waitUntil(lambda: len(window.queue.jobs) == 2)
        settled(qtbot, window)
        window._pick_remote_folder(window.sessions[0], "/")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for kind in (TransferDialog, OverwriteDialog, RemoteFolderPicker):
        assert window.findChildren(kind) == [], kind.__name__


def test_only_the_queue_sets_back_a_file_an_append_left_half_done_never_a_tab_that_connects(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two tabs on one login: the second tab connecting while the first may be appending must not cut the file. Only the queue, which
    runs one job at a time, sets it back (its note says how it broke off, and the file is unchanged since), before its next transfer,
    and the status bar tells it."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "log.txt").write_text("the old content" + "half of an append", encoding="utf-8")
    (here / "a.txt").write_text("a", encoding="utf-8")
    shown: list[str] = []

    def accept(dialog: TransferDialog) -> int:
        return QDialog.DialogCode.Accepted.value

    def show(text: str, timeout: int = 0) -> None:
        shown.append(text)

    monkeypatch.setattr(TransferDialog, "exec", accept)
    monkeypatch.setattr(window.status, "showMessage", show)
    with ssh_server(root) as server:
        trust_server(server)
        settings = ssh_settings(server)
        parts.remember_append(settings, "/log.txt", len("the old content"), len("the old content") + 100)
        parts.append_ended(settings, "/log.txt")  # it broke off earlier and runs no more
        os.utime(root / "log.txt", (OLD_TIME, OLD_TIME))
        parts.mark_append(settings, "/log.txt", parts.BROKEN, len("the old content" + "half of an append"), OLD_TIME)  # how it broke off
        connected(qtbot, window, settings)
        connected(qtbot, window, replace(settings, name="Other"))  # a second tab on the same login: one connection opens one tab only (D-051)
        window.tab_bar.setCurrentIndex(0)
        assert len(window.sessions) == 2
        answered: list[Session] = []
        for session in window.sessions:  # each tab's worker runs in order: this answers after everything connecting started there
            session.worker.submit(lambda: None, lambda _nothing, session=session: answered.append(session), lambda error: None)
        qtbot.waitUntil(lambda: len(answered) == 2)
        assert (root / "log.txt").stat().st_size == len("the old content" + "half of an append")
        assert [(path, note.start, note.most) for path, note in parts.broken_appends(settings).items()] == [("/log.txt", len("the old content"), len("the old content") + 100)]
        upload_one(qtbot, window, here, "a.txt")
        qtbot.waitUntil(lambda: (root / "a.txt").exists())
        settled(qtbot, window)
    assert (root / "log.txt").read_text(encoding="utf-8") == "the old content"
    assert parts.broken_appends(settings) == {}
    assert f"{settings.name}: 1 file that an append left half done has its old length back." in shown

# --- review round 7


def test_the_top_of_the_server_is_always_a_folder(tmp_path: Path) -> None:
    with ftp_server(tmp_path, without=("MLST",)) as port:
        ftp = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        ftp.connect()
        try:
            assert what_is_there(ftp, "/") == A_FOLDER and what_is_there(ftp, "") == A_FOLDER
        finally:
            ftp.close()


def test_an_ftp_link_whose_kind_can_not_be_seen_is_left_to_the_queue(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("real")
    with ftp_server(tmp_path, links_as_links=True) as port:
        ftp = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        ftp.connect()
        try:
            assert what_is_there(ftp, "/link") == UNKNOWN  # never "is a file"
        finally:
            ftp.close()


def test_a_stopped_upload_shows_on_the_right_what_arrived(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The folder and its first file arrived, then the upload was stopped: the right side shows them all the same."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    (here / "box").mkdir(parents=True)
    (here / "box" / "a.txt").write_text("a", encoding="utf-8")
    (here / "box" / "b.txt").write_text("b", encoding="utf-8")
    real_send = FileWay._send

    def stopped_at_b(way: FileWay, source: Path, target: str, temporary: str, old: RemoteEntry | None, size: int, modified: float, offset: int, resumed_by_choice: bool, part_there: bool, present: dict[str, RemoteEntry]) -> None:
        if source.name == "b.txt":
            way.job.stop_asked.set()
            raise Stopped
        real_send(way, source, target, temporary, old, size, modified, offset, resumed_by_choice, part_there, present)

    def accept(dialog: TransferDialog) -> int:
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    monkeypatch.setattr(FileWay, "_send", stopped_at_b)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        assert "box" not in names_on_the_right(window)
        upload_one(qtbot, window, here, "box")
        qtbot.waitUntil(lambda: bool(window.queue.jobs) and window.queue.jobs[0].state == State.STOPPED)
        settled(qtbot, window)
        qtbot.waitUntil(lambda: "box" in names_on_the_right(window))
    assert (root / "box" / "a.txt").exists() and not (root / "box" / "b.txt").exists()


def test_the_end_of_an_upload_refreshes_the_right_side_once(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The queue may tell of a job more than once after its end (each news is queued for the window): one look at the folder is enough."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    reloads: list[str] = []
    real_reload = window._reload

    def counted(session: Session) -> None:
        reloads.append(session.remote_folder)
        real_reload(session)

    def accept(dialog: TransferDialog) -> int:
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        monkeypatch.setattr(window, "_reload", counted)
        upload_one(qtbot, window, here, "a.txt")
        settled(qtbot, window)
        job = window.queue.jobs[0]
        for _again in range(3):
            window._job_changed(job)  # late news of the same end
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
    assert reloads == ["/"]


@pytest.mark.parametrize("meanwhile", ["switched-tab", "tab-lost", "tab-closed"])
def test_the_question_to_make_a_server_folder_names_its_connection_and_fits_the_screen(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, meanwhile: str) -> None:
    """The person switched to another tab before the question came: it names the connection it is about, and its answer goes there.
    Where that tab lost its connection, or was closed, meanwhile, nothing is asked, and a box says why. The folder's long name is
    shortened in its middle."""
    first, second, here = tmp_path / "first", tmp_path / "second", tmp_path / "here"
    first.mkdir()
    second.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    asked: list[str] = []

    def to_a_new_folder(dialog: TransferDialog) -> int:
        dialog.target.setText("/" + LONG_NAME)
        return QDialog.DialogCode.Accepted.value

    def answer(*args: object) -> QMessageBox.StandardButton:
        asked.append(plain_of(str(args[2])))
        return QMessageBox.StandardButton.Yes

    boxes: list[str] = []

    def box(*args: object) -> None:
        boxes.append(plain_of(str(args[2])))

    monkeypatch.setattr(TransferDialog, "exec", to_a_new_folder)
    monkeypatch.setattr(QMessageBox, "question", answer)
    monkeypatch.setattr(QMessageBox, "information", box)
    with ssh_server(first) as shop, ssh_server(second) as other:
        trust_server(shop)
        trust_server(other)
        connected(qtbot, window, ssh_settings(shop, "Shop"))
        connected(qtbot, window, ssh_settings(other, "Other", MAGENTA))
        window.tab_bar.setCurrentIndex(0)
        qtbot.waitUntil(lambda: window.current() is window.sessions[0])
        upload_one(qtbot, window, here, "a.txt")
        if meanwhile == "switched-tab":
            window.tab_bar.setCurrentIndex(1)
            qtbot.waitUntil(lambda: bool(asked))
            settled(qtbot, window)
        elif meanwhile == "tab-lost":
            window.sessions[0].alive = False
            qtbot.waitUntil(lambda: bool(boxes))  # the look at the folder answered
        else:
            window.close_tab(0)
            qtbot.waitUntil(lambda: bool(boxes))
    if meanwhile == "switched-tab":
        assert len(asked) == 1 and asked[0].startswith("This folder isn't on the server of Shop yet:")
        assert LONG_NAME not in asked[0] and "…" in asked[0]
        assert (first / LONG_NAME / "a.txt").exists() and not (second / LONG_NAME).exists()
    else:
        assert asked == [] and window.queue.jobs == [] and not (first / LONG_NAME).exists()
        assert boxes == [f"Shop: {LOST_MEANWHILE_TEXT if meanwhile == 'tab-lost' else TAB_CLOSED_MEANWHILE_TEXT}"]


@pytest.mark.parametrize("side", ["server", "here"])
def test_a_delete_question_shortens_a_long_name_in_its_middle(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: str) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / LONG_NAME).write_text("a", encoding="utf-8")
    (here / LONG_NAME).write_text("a", encoding="utf-8")
    asked: list[str] = []

    def no(*args: object) -> QMessageBox.StandardButton:
        asked.append(plain_of(str(args[2])))
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", no)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        if side == "server":
            qtbot.waitUntil(lambda: LONG_NAME in names_on_the_right(window))
            select(window, LONG_NAME)
            window.delete()
        else:
            window.local_side.go_to(str(here))
            qtbot.waitUntil(lambda: LONG_NAME in window.local_side.file_list.names())
            pick_local(window, LONG_NAME)
            window._delete_local()
    assert len(asked) == 1 and LONG_NAME not in asked[0] and "…" in asked[0]
    assert (root / LONG_NAME).exists() and (here / LONG_NAME).exists()


@pytest.mark.parametrize("make", [True, False])
def test_a_download_to_a_folder_that_is_not_on_this_computer_asks_to_make_it_first(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    wanted = here / "new" / "deeper"
    asked: list[str] = []

    def to_a_new_folder(dialog: TransferDialog) -> int:
        dialog.target.setText(str(wanted))
        return QDialog.DialogCode.Accepted.value

    def answer(*args: object) -> QMessageBox.StandardButton:
        asked.append(plain_of(str(args[2])))
        return QMessageBox.StandardButton.Yes if make else QMessageBox.StandardButton.No

    monkeypatch.setattr(TransferDialog, "exec", to_a_new_folder)
    monkeypatch.setattr(QMessageBox, "question", answer)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["download"].trigger()
        if make:
            qtbot.waitUntil(lambda: bool(window.queue.jobs))
            settled(qtbot, window)
    assert asked == [NO_LOCAL_FOLDER_YET_TEXT.format(folder=fitting(str(wanted), window))]  # fitted to the screen, as every name in a box
    if make:
        assert window.queue.jobs[0].state == State.DONE and (wanted / "a.txt").read_text(encoding="utf-8") == "a"
    else:
        assert window.queue.jobs == [] and not (here / "new").exists()


@pytest.mark.parametrize("protocol", [SFTP, FTP])
def test_the_move_hover_text_says_only_what_is_checked(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protocol: str) -> None:
    """SFTP's server puts each file on its disk on request; FTP can't be asked, so only the size and date are checked."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    hover: list[str] = []

    def look(dialog: TransferDialog) -> int:
        hover.append(dialog.move_tick.toolTip())
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(TransferDialog, "exec", look)
    if protocol == SFTP:
        with ssh_server(root) as server:
            trust_server(server)
            connected(qtbot, window, ssh_settings(server))
            upload_one(qtbot, window, here, "a.txt")
    else:
        with ftp_server(root) as port:
            connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
            upload_one(qtbot, window, here, "a.txt")
    assert hover == [MOVE_UP_TEXT if protocol == SFTP else MOVE_UP_UNFLUSHED_TEXT]
    assert "date" not in MOVE_UP_UNFLUSHED_TEXT  # only what is checked: its size, and that it did not change here


def test_a_size_the_server_never_gave_is_shown_as_unknown_never_as_0_bytes(qtbot: QtBot) -> None:
    dialog = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 0, OLD_TIME, None, target_size_known=False))
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any(text.startswith(SIZE_UNKNOWN_TEXT) for text in texts) and not any(text.startswith("0 bytes") for text in texts), texts


def test_the_overwrite_question_names_the_connection_it_is_about(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Asked while another tab is in front: it says which connection it is about."""
    first, second, here = tmp_path / "first", tmp_path / "second", tmp_path / "here"
    for folder in (first, second, here):
        folder.mkdir()
    (here / "a.txt").write_text("new", encoding="utf-8")
    (first / "a.txt").write_text("old", encoding="utf-8")
    said: list[str] = []

    def accept(dialog: TransferDialog) -> int:
        return QDialog.DialogCode.Accepted.value

    def no(dialog: OverwriteDialog) -> int:
        said.append(dialog.text.text())
        dialog.answer = Answer(Choice.NO)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    monkeypatch.setattr(OverwriteDialog, "exec", no)
    with ssh_server(first) as shop, ssh_server(second) as other:
        trust_server(shop)
        trust_server(other)
        connected(qtbot, window, ssh_settings(shop, "Shop"))
        connected(qtbot, window, ssh_settings(other, "Other", MAGENTA))
        window.tab_bar.setCurrentIndex(0)
        qtbot.waitUntil(lambda: window.current() is window.sessions[0])
        upload_one(qtbot, window, here, "a.txt")
        window.tab_bar.setCurrentIndex(1)
        qtbot.waitUntil(lambda: bool(said))
        settled(qtbot, window)
    assert said == ["a.txt is already in / on the server of Shop."]
    assert (first / "a.txt").read_text(encoding="utf-8") == "old"


def test_the_folder_picker_shortens_a_long_path_in_a_server_reason_and_hovers_it_whole(qtbot: QtBot) -> None:
    waiting: list[tuple[Callable[[list[RemoteEntry]], None], Callable[[str, bool], None]]] = []
    picker = RemoteFolderPicker("/srv", lambda folder, shown, failed: waiting.append((shown, failed)))
    qtbot.addWidget(picker)
    waiting[0][0]([RemoteEntry("inner", True)])
    picker.open_folder("/srv/inner")
    reason = f"Permission denied: /srv/inner/{LONG_NAME}"
    waiting[1][1](reason, False)
    assert LONG_NAME not in picker.place.text() and "…" in picker.place.text() and picker.place.text().startswith("/srv\nPermission denied: ")
    assert plain_of(picker.place.toolTip()) == f"/srv\n{reason}"


@pytest.mark.parametrize("reason", ["Permission denied", NOT_CONNECTED_TEXT])
def test_a_greyed_choose_says_why_when_nothing_could_be_shown(qtbot: QtBot, reason: str) -> None:
    waiting: list[Callable[[str, bool], None]] = []
    picker = RemoteFolderPicker("/", lambda folder, shown, failed: waiting.append(failed), lost_text=NOT_CONNECTED_TEXT)
    qtbot.addWidget(picker)
    assert picker.choose.toolTip() == WAIT_TEXT  # loading
    waiting[0](reason, False)
    assert not picker.choose.isEnabled() and plain_of(picker.choose.toolTip()) == NOTHING_TO_CHOOSE_TEXT.format(reason=reason)


@pytest.mark.parametrize("how", ["typed", "browsed"])
def test_the_problem_line_goes_as_soon_as_the_folder_changes(qtbot: QtBot, tmp_path: Path, how: str) -> None:
    dialog = TransferDialog(True, ["a.txt"], "relative", TransferOptions(), {}, lambda start: "/srv")
    qtbot.addWidget(dialog)
    dialog.buttons.accepted.emit()
    assert not dialog.problem.isHidden()
    if how == "typed":
        dialog.target.setText("/srv")
    else:
        dialog.browse_button.click()
    assert dialog.problem.isHidden() and dialog.target.text() == "/srv"


def test_resume_never_calls_a_size_the_server_never_gave_0_bytes(qtbot: QtBot) -> None:
    dialog = OverwriteDialog(Conflict("a.txt", "/here", False, 0, OLD_TIME, 5, OLD_TIME, 4, source_size_known=False))
    qtbot.addWidget(dialog)
    assert dialog.buttons[Choice.RESUME].toolTip() == "Continue the unfinished copy: 4 bytes of a file of unknown size is there already"


def test_synchronize_queues_nothing_once_its_connection_was_lost(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    write_at(here / "a.txt", "here only", OLD_TIME)
    shown: list[str] = []
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 1)
        row = dialog.table.item(0, 0)
        assert row is not None
        row.setCheckState(Qt.CheckState.Checked)
        window.sessions[0].alive = False  # the connection was lost while the person ticked

        def show(text: str, timeout: int = 0) -> None:
            shown.append(text)

        boxes: list[str] = []

        def box(*args: object) -> None:
            boxes.append(plain_of(str(args[2])))

        monkeypatch.setattr(window.status, "showMessage", show)
        monkeypatch.setattr(QMessageBox, "information", box)
        dialog.apply_button.click()
    assert window.queue.jobs == [] and not (root / "a.txt").exists()
    assert shown == boxes == [f"Shop: {LOST_MEANWHILE_TEXT}"]


def test_append_says_what_happens_when_it_breaks_off_and_when_the_connection_is_lost(qtbot: QtBot) -> None:
    dialog = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, None))
    qtbot.addWidget(dialog)
    assert dialog.buttons[Choice.APPEND].toolTip() == APPEND_TEXT
    assert "old length back" in APPEND_TEXT and "left as it is" in APPEND_TEXT  # never a set-back it can't promise


def test_a_folder_here_agreed_to_is_never_made_for_a_transfer_that_does_not_start(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    wanted = here / "new"

    def agreed_then_lost(dialog: TransferDialog) -> int:
        dialog.target.setText(str(wanted))
        dialog.to_make = str(wanted)  # the person said Yes to making it
        window.sessions[0].alive = False  # and the connection was lost meanwhile
        return QDialog.DialogCode.Accepted.value

    def box(*args: object) -> None:
        pass

    monkeypatch.setattr(TransferDialog, "exec", agreed_then_lost)
    monkeypatch.setattr(QMessageBox, "information", box)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["download"].trigger()
    assert window.queue.jobs == [] and not wanted.exists()  # no empty folder left behind


@pytest.mark.parametrize("reason", ["Permission denied", "The server did not answer in time."])
def test_the_folder_picker_climbs_only_from_a_folder_that_is_not_there(qtbot: QtBot, reason: str) -> None:
    """Any other failure is shown as it is, with nothing to choose and the way up; it never silently starts somewhere else."""
    asked: list[str] = []

    def load(folder: str, shown: Callable[[list[RemoteEntry]], None], failed: Callable[[str, bool], None]) -> None:
        asked.append(folder)
        if folder == "/a":
            shown([RemoteEntry("b", True)])
        else:
            failed(reason, False)

    picker = RemoteFolderPicker("/a/b", load)
    qtbot.addWidget(picker)
    assert asked == ["/a/b"]  # no climbing
    assert not picker.choose.isEnabled() and plain_of(picker.choose.toolTip()) == NOTHING_TO_CHOOSE_TEXT.format(reason=reason)
    assert picker.place.text() == f"/a/b\n{reason}" and picker.names == [None]
    item = picker.folders.item(0)
    assert item is not None and item.text() == ".."
    picker.folders.itemActivated.emit(item)  # the way up, by the person
    assert asked == ["/a/b", "/a"] and picker.chosen() == "/a" and picker.choose.isEnabled()


def test_the_window_tells_the_picker_when_a_server_folder_is_not_there(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    (root / "there").mkdir(parents=True)

    def choose_once_shown(picker: RemoteFolderPicker) -> int:
        qtbot.waitUntil(picker.choose.isEnabled)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(RemoteFolderPicker, "exec", choose_once_shown)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        chosen = window._pick_remote_folder(window.sessions[0], "/there/missing/deeper")
    assert chosen == "/there"  # not there: it starts at the nearest folder above that is


def test_choose_takes_the_highlighted_folder_and_says_which_and_that_nothing_is_sent_yet(qtbot: QtBot) -> None:
    waiting: list[Callable[[list[RemoteEntry]], None]] = []
    picker = RemoteFolderPicker("/srv", lambda folder, shown, failed: waiting.append(shown))
    qtbot.addWidget(picker)
    waiting[0]([RemoteEntry("inner", True)])
    assert picker.chosen() == "/srv" and plain_of(picker.choose.toolTip()) == CHOOSE_TEXT.format(folder="/srv")
    picker.folders.setCurrentRow(1)  # inner, highlighted
    assert picker.chosen() == "/srv/inner" and plain_of(picker.choose.toolTip()) == CHOOSE_TEXT.format(folder="/srv/inner")
    picker.folders.setCurrentRow(0)  # the way up is no folder to take
    assert picker.chosen() == "/srv" and plain_of(picker.choose.toolTip()) == CHOOSE_TEXT.format(folder="/srv")
    assert "nothing is sent yet" in CHOOSE_TEXT


def test_boxes_with_details_go_after_use(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)

    def answer(box: QMessageBox) -> int:
        return QMessageBox.StandardButton.Yes.value

    monkeypatch.setattr(QMessageBox, "exec", answer)
    for _round in range(3):
        assert asked(parent, "Chrono FTP", "Go on?", "/tmp/a whole path")
        told(parent, "Chrono FTP", "It went.", "/tmp/a whole path")
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert parent.findChildren(QMessageBox) == []  # none piles up


@pytest.mark.parametrize(("typed", "said"), [("", NO_NEW_NAME_TEXT), ("a.txt", SAME_NAME_TEXT)], ids=["empty", "unchanged"])
def test_rename_without_a_new_name_says_why_nothing_happened(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, typed: str, said: str) -> None:
    dialog = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, None))
    qtbot.addWidget(dialog)
    told: list[str] = []

    def entered(*_args: object) -> tuple[str, bool]:
        return typed, True

    def note(*args: object) -> None:
        told.append(plain_of(str(args[2])))

    monkeypatch.setattr(QInputDialog, "getText", entered)
    monkeypatch.setattr(QMessageBox, "information", note)
    dialog.buttons[Choice.RENAME].click()
    assert told == [said] and dialog.result() != QDialog.DialogCode.Accepted.value  # the question stays open


@pytest.mark.parametrize("onto", ["sub", PARENT_ENTRY, ""], ids=["a-folder", "the-way-up", "the-list"])
def test_a_drop_on_the_left_downloads_into_the_folder_it_was_dropped_on(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, onto: str) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    (here / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("a", encoding="utf-8")
    targets: list[str] = []

    def accept(dialog: TransferDialog) -> int:
        targets.append(dialog.folder())
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window._dropped_on_local(FROM_REMOTE, [], onto)
        qtbot.waitUntil(lambda: bool(window.queue.jobs))  # the window comes once the drop has finished
        settled(qtbot, window)
    folder = {"sub": here / "sub", PARENT_ENTRY: tmp_path, "": here}[onto]
    assert targets == [str(folder)] and (folder / "a.txt").read_text(encoding="utf-8") == "a"


def test_a_drop_on_the_left_from_a_lost_tab_opens_no_transfer_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lost tab still shows its old listing, which is no longer the server's: a drag from it to the left is refused at once, as a
    drop on the right is."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    shown: list[str] = []

    def opened(dialog: TransferDialog) -> int:
        shown.append(dialog.folder())
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(TransferDialog, "exec", opened)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.sessions[0].alive = False  # the connection dropped; its listing stays on screen
        window._dropped_on_local(FROM_REMOTE, [], "")
        after: list[bool] = []
        QTimer.singleShot(0, lambda: after.append(True))  # runs after anything the drop put off until it finished
        qtbot.waitUntil(lambda: bool(after))
    assert shown == [] and window.queue.jobs == []


@pytest.mark.parametrize("source", [FROM_LOCAL, FROM_REMOTE])
def test_a_drop_on_the_right_uploads_or_moves_into_the_folder_it_was_dropped_on(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "dir").mkdir(parents=True)
    here.mkdir()
    (here / "a.txt").write_text("from here", encoding="utf-8")
    (root / "b.txt").write_text("on the server", encoding="utf-8")

    def accept(dialog: TransferDialog) -> int:
        return QDialog.DialogCode.Accepted.value

    def yes(*_args: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(TransferDialog, "exec", accept)
    monkeypatch.setattr(QMessageBox, "question", yes)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "b.txt" in names_on_the_right(window))
        if source == FROM_LOCAL:
            window._dropped_on_remote(FROM_LOCAL, [str(here / "a.txt")], "dir")
            qtbot.waitUntil(lambda: (root / "dir" / "a.txt").exists())
            settled(qtbot, window)
        else:
            moves: list[tuple[str, list[str], str]] = []

            def move(session: Session, folder: str, chosen: list[RemoteEntry], target: str) -> None:
                moves.append((folder, [entry.name for entry in chosen], target))  # the server's own move (its shell is tested elsewhere)

            monkeypatch.setattr(window, "_move_into", move)
            select(window, "b.txt")
            window._dropped_on_remote(FROM_REMOTE, [], "dir")
            assert moves == [("/", ["b.txt"], "/dir")]
            window._dropped_on_remote(FROM_REMOTE, [], PARENT_ENTRY)
            assert moves[-1] == ("/", ["b.txt"], "/")  # the way up from the top is the top
    if source == FROM_LOCAL:
        assert (root / "dir" / "a.txt").read_text(encoding="utf-8") == "from here"


def test_enter_in_the_pickers_list_opens_the_highlighted_folder_and_chooses_nothing(qtbot: QtBot) -> None:
    asked: list[str] = []
    contents = {"/srv": [RemoteEntry("inner", True)], "/srv/inner": [RemoteEntry("deeper", True)]}

    def load(folder: str, shown: Callable[[list[RemoteEntry]], None], failed: Callable[[str, bool], None]) -> None:
        asked.append(folder)
        shown(contents.get(folder, []))

    picker = RemoteFolderPicker("/srv", load)
    qtbot.addWidget(picker)
    picker.show()
    picker.folders.setFocus()
    picker.folders.setCurrentRow(1)  # inner
    QTest.keyClick(picker.folders, Qt.Key.Key_Return)
    assert asked == ["/srv", "/srv/inner"] and picker.result() != QDialog.DialogCode.Accepted.value  # opened, not chosen
    assert picker.folders.toolTip() == LIST_TEXT
    picker.choose.setFocus()
    QTest.keyClick(picker.choose, Qt.Key.Key_Return)  # Enter on Choose itself takes the folder shown
    assert picker.result() == QDialog.DialogCode.Accepted.value and picker.chosen() == "/srv/inner"


@pytest.mark.parametrize("what", ["link-to-nowhere", "link-in-a-loop", "link-to-a-closed-place", "file", "pipe", "closed"])
def test_a_download_folder_here_that_is_not_one_is_said_truthfully(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, what: str) -> None:
    if what in ("closed", "link-to-a-closed-place") and AS_ROOT:
        pytest.skip("as root every folder can be looked at")
    folder = tmp_path / "target"
    if what == "link-to-nowhere":
        folder.symlink_to(tmp_path / "missing")
    elif what == "link-in-a-loop":
        folder.symlink_to(folder)  # leads to itself: nothing is closed there, it leads nowhere
    elif what == "link-to-a-closed-place":
        (tmp_path / "shut" / "inside").mkdir(parents=True)
        folder.symlink_to(tmp_path / "shut" / "inside")
    elif what == "file":
        folder.write_text("a file", encoding="utf-8")
    elif what == "pipe":
        if sys.platform == "win32":
            pytest.skip("Windows has no named pipe in a folder")
        else:
            os.mkfifo(folder)
    else:
        (tmp_path / "closed").mkdir()
        folder = tmp_path / "closed" / "target"
    told: list[str] = []
    started: list[str] = []

    def box(*args: object) -> None:
        told.append(plain_of(str(args[2])))

    monkeypatch.setattr(QMessageBox, "information", box)
    closed = {"closed": tmp_path / "closed", "link-to-a-closed-place": tmp_path / "shut"}.get(what)
    if closed is not None:
        closed.chmod(0o000)
    try:
        window._when_the_local_folder_is_there(str(folder), started.append)
    finally:
        if closed is not None:
            closed.chmod(0o755)
    shown = fitting(str(folder), window)
    expected = {
        "link-to-nowhere": LINK_NOWHERE_HERE_TEXT.format(folder=shown),
        "link-in-a-loop": LINK_NOWHERE_HERE_TEXT.format(folder=shown),
        "link-to-a-closed-place": LINK_CLOSED_HERE_TEXT.format(folder=shown),
        "file": IS_A_FILE_HERE_TEXT.format(folder=shown),
        "pipe": NOT_A_FOLDER_HERE_TEXT.format(folder=shown),
        "closed": CANT_LOOK_HERE_TEXT.format(folder=shown, reason="Permission denied"),
    }[what]
    assert told == [expected] and started == []


@pytest.mark.parametrize(("typed", "taken"), [(" a.txt", True), ("a.txt ", True), ("a.txt", False), ("", False)])
def test_rename_takes_the_name_exactly_as_typed(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, typed: str, taken: bool) -> None:
    dialog = OverwriteDialog(Conflict("a.txt", "/srv", True, 10, OLD_TIME, 5, OLD_TIME, None))
    qtbot.addWidget(dialog)

    def entered(*_args: object) -> tuple[str, bool]:
        return typed, True

    def note(*_args: object) -> None:
        pass

    monkeypatch.setattr(QInputDialog, "getText", entered)
    monkeypatch.setattr(QMessageBox, "information", note)
    dialog.buttons[Choice.RENAME].click()
    if taken:
        assert dialog.answer == Answer(Choice.RENAME, typed)  # spaces and all: it is the name the person gave
    else:
        assert dialog.result() != QDialog.DialogCode.Accepted.value
