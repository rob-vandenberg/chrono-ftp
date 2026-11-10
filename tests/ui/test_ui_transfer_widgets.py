"""The parts of the transfer windows on their own, without a server: the queue, the Transfer settings form, the folder picker on the
server, and the overwrite question."""

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QInputDialog, QLabel, QListWidgetItem, QMessageBox, QProgressBar, QWidget
from pytestqt.qtbot import QtBot

from chrono_ftp.connections.base import Capabilities, CommandResult, RemoteEntry
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.transferoptions import RESUME_OFF, RESUME_ON, RESUME_SMART, TransferOptions
from chrono_ftp.transfers.options import KEEP_TIMESTAMPS, RESUME, SET_RIGHTS, Answer, Choice, Conflict
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.filelist import size_text
from chrono_ftp.ui.overwritedialog import SAME_NAME_TEXT, OverwriteDialog, _details
from chrono_ftp.ui.plaintext import box_text, plain_of
from chrono_ftp.ui.queueview import CELL_PADDING, DIRECTION_COLUMN, LONGEST_DIRECTION, PROGRESS_COLUMN, QueueView, time_text
from chrono_ftp.ui.remotefolderpicker import UP_TEXT, Failed, RemoteFolderPicker
from chrono_ftp.ui.transferoptionsform import ADD_X_TEXT, IGNORE_TEXT, RIGHTS_FIRST_TEXT, RIGHTS_INCOMPLETE_TEXT, TransferOptionsDialog, TransferOptionsForm, summary_text
from tests.test_engine import Scripted

# --- Version ------------------------------------------------------------
__version__ = "test_ui_transfer_widgets.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: the queue's columns, times, progress and selection; the Transfer settings form, its greyed parts, what it shows and
#          what it hands out; the folder picker's list, keys, Choose and its way up past a folder that fails; the overwrite question's
#          texts and Rename

NAME_NOT_UTF8: str = "b\udcffd"  # a name whose bytes are no UTF-8, kept through a surrogate
NAME_SHOWN: str = "b�d"


def a_job(name: str = "a", way: str = "", state: State = State.WAITING, seconds_left: float | None = None, total: int = 0, percent: int = 0, done: int = 0) -> Job:
    remote = Scripted(CommandResult(0, "", ""))
    remote.capabilities = Capabilities()
    return Job(remote, Connection(name="T", host="h"), "/a", "/srv", True, name, way=way, state=state, seconds_left=seconds_left, total=total, percent=percent, done=done)


# --- the queue


def test_the_time_left_reads_in_seconds_minutes_and_hours() -> None:
    assert [time_text(seconds) for seconds in (None, 0, 59, 60, 3599, 3600, 3725)] == ["", "0 s", "59 s", "1 min 00 s", "59 min 59 s", "1 h 00 min", "1 h 02 min"]


def cell(view: QueueView, row: int, column: int) -> str:
    item = view.item(row, column)
    return item.text() if item is not None else ""


def test_the_queue_shows_the_name_as_it_can_the_way_and_the_state(qtbot: QtBot) -> None:
    view = QueueView()
    qtbot.addWidget(view)
    view.show_job(a_job(name=NAME_NOT_UTF8, way="rsync", state=State.WAITING, seconds_left=30.0))
    assert [cell(view, 0, column) for column in (0, 2, 3, 5)] == [NAME_SHOWN, "rsync", "Waiting", ""]  # no time left before it runs


@pytest.mark.parametrize(("state", "total", "percent", "shown"), [(State.RUNNING, 0, 0, "size"), (State.RUNNING, 0, 5, "%p%"), (State.RUNNING, 10, 0, "%p%"), (State.WAITING, 0, 0, "%p%")])
def test_the_bar_shows_the_size_done_only_while_nothing_else_is_known(qtbot: QtBot, state: State, total: int, percent: int, shown: str) -> None:
    view = QueueView()
    qtbot.addWidget(view)
    view.show_job(a_job(state=state, total=total, percent=percent, done=2048))
    bar = view.cellWidget(0, PROGRESS_COLUMN)
    assert isinstance(bar, QProgressBar) and bar.value() == percent
    assert bar.format() == (size_text(2048) if shown == "size" else shown)


def test_a_click_on_a_cell_selects_its_job_and_nothing_in_the_queue_can_be_edited(qtbot: QtBot) -> None:
    view = QueueView()
    qtbot.addWidget(view)
    first, second = a_job(name="first"), a_job(name="second")
    view.show_job(first)
    view.show_job(second)
    view.setCurrentCell(1, 2)  # a cell of the second row
    assert view.selected_jobs() == [second]
    assert view.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert view.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    assert view.columnWidth(DIRECTION_COLUMN) == view.fontMetrics().horizontalAdvance(LONGEST_DIRECTION) + CELL_PADDING


# --- the Transfer settings form


def a_form(qtbot: QtBot, options: TransferOptions, reasons: dict[str, str] | None = None) -> TransferOptionsForm:
    form = TransferOptionsForm()
    qtbot.addWidget(form)
    form.set_options(options, reasons)
    return form


def test_what_belongs_to_set_the_rights_is_greyed_and_says_why_until_it_is_ticked(qtbot: QtBot) -> None:
    form = a_form(qtbot, TransferOptions(set_rights=False))
    assert [widget.isEnabled() for widget in (form.rights, form.add_x, form.ignore_errors)] == [False, False, False]
    assert [widget.toolTip() for widget in (form.rights, form.add_x, form.ignore_errors)] == [RIGHTS_FIRST_TEXT] * 3
    form.set_rights.setChecked(True)  # the tick alone brings them back
    assert [widget.isEnabled() for widget in (form.rights, form.add_x, form.ignore_errors)] == [True, True, True]
    assert (form.add_x.toolTip(), form.ignore_errors.toolTip()) == (ADD_X_TEXT, IGNORE_TEXT)


def test_what_a_server_can_not_do_is_shown_off_greyed_and_never_handed_out(qtbot: QtBot) -> None:
    reasons = {RESUME: "no REST", KEEP_TIMESTAMPS: "no MFMT", SET_RIGHTS: "no CHMOD"}
    form = a_form(qtbot, TransferOptions(resume=RESUME_ON, keep_timestamps=True, set_rights=True), reasons)
    assert form.resume.currentData() == RESUME_OFF and not form.keep_timestamps.isChecked() and not form.set_rights.isChecked()
    assert not form.resume_label.isEnabled() and not form.resume.isEnabled()
    form.resume.setCurrentIndex(form.resume.findData(RESUME_ON))  # set by something other than the person: still never handed out
    form.keep_timestamps.setChecked(True)
    form.set_rights.setChecked(True)
    chosen = form.chosen()
    assert (chosen.resume, chosen.keep_timestamps, chosen.set_rights) == (RESUME_OFF, False, False)


@pytest.mark.parametrize("resume", [RESUME_ON, RESUME_OFF, RESUME_SMART])
def test_the_form_shows_every_option_as_saved_and_hands_back_what_is_ticked(qtbot: QtBot, resume: str) -> None:
    saved = TransferOptions(resume=resume, ask_before_replacing=False, only_newer=True, ignore_rights_errors=True, add_x_to_folders=False, count_first=False)
    form = a_form(qtbot, saved)
    assert form.resume.currentData() == resume and form.only_newer.isChecked() and form.ignore_errors.isChecked()
    assert form.chosen() == saved  # through OK unchanged
    for box in (form.ask, form.add_x, form.ignore_errors, form.count_first):
        box.setChecked(not box.isChecked())
    chosen = form.chosen()
    assert (chosen.ask_before_replacing, chosen.add_x_to_folders, chosen.ignore_rights_errors, chosen.count_first) == (True, True, False, True)


def test_a_resume_setting_the_form_does_not_know_is_kept(qtbot: QtBot) -> None:
    form = a_form(qtbot, replace(TransferOptions(), resume="later"))
    assert form.resume.currentIndex() == -1 and form.chosen().resume == "later"


def test_the_form_says_each_change_and_names_smart_resume_in_full(qtbot: QtBot) -> None:
    form = a_form(qtbot, TransferOptions())
    heard: list[bool] = []
    form.changed.connect(lambda: heard.append(True))
    form.resume.setCurrentIndex(form.resume.findData(RESUME_OFF))
    form.only_newer.setChecked(True)
    assert len(heard) == 2
    assert form.resume.itemText(form.resume.findData(RESUME_SMART)) == "Smart: files of 100 KB or more"


def test_the_rights_field_takes_up_to_four_octal_digits_and_ok_waits_for_three(qtbot: QtBot) -> None:
    """Three or four octal digits are rights (644, 0644): "64" is not complete yet, so OK stays greyed and says why."""
    form = TransferOptionsForm()
    qtbot.addWidget(form)
    validator = form.rights.validator()
    assert validator is not None
    assert [state_of(validator, text) for text in ("", "64", "644", "0644")] == [QValidator.State.Acceptable] * 4
    assert state_of(validator, "8") == QValidator.State.Invalid
    dialog = TransferOptionsDialog(TransferOptions(set_rights=True), {})
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None and ok.isEnabled() and ok.toolTip() == ""
    dialog.form.rights.setText("64")
    assert not ok.isEnabled() and ok.toolTip() == RIGHTS_INCOMPLETE_TEXT


def state_of(validator: QValidator, text: str) -> QValidator.State:
    return cast(tuple[QValidator.State, str, int], validator.validate(text, 0))[0]


def test_the_summary_names_each_choice() -> None:
    assert summary_text(TransferOptions(resume=RESUME_ON), True).startswith("Resume every file")
    assert summary_text(TransferOptions(resume=RESUME_OFF), True).startswith("No resume")
    assert summary_text(TransferOptions(resume=RESUME_SMART), True).startswith("Resume large files")
    assert "Only new and updated" in summary_text(TransferOptions(only_newer=True), True)
    rights = TransferOptions(set_rights=True, rights="0640")
    assert "Rights 0640" in summary_text(rights, True) and "Rights" not in summary_text(rights, False)  # rights go up only


# --- the folder picker on the server


class Server:
    """Answers each folder the picker asks for at once, from contents; a folder not in it is not there."""

    def __init__(self, contents: dict[str, list[RemoteEntry]], refused: tuple[str, ...] = ()) -> None:
        self.contents: dict[str, list[RemoteEntry]] = contents
        self.refused: tuple[str, ...] = refused
        self.asked: list[str] = []

    def __call__(self, folder: str, shown: Callable[[list[RemoteEntry]], None], failed: Failed) -> None:
        self.asked.append(folder)
        if folder in self.refused:
            failed("Permission denied", False)
        elif folder in self.contents:
            shown(self.contents[folder])
        else:
            failed("No such file", True)


def a_picker(qtbot: QtBot, start: str, load: Callable[[str, Callable[[list[RemoteEntry]], None], Failed], None], lost_text: str = "") -> RemoteFolderPicker:
    picker = RemoteFolderPicker(start, load, lost_text=lost_text)
    qtbot.addWidget(picker)
    return picker


def items(picker: RemoteFolderPicker) -> list[str]:
    return [item.text() for item in (picker.folders.item(row) for row in range(picker.folders.count())) if item is not None]


def test_the_picker_lists_only_folders_sorted_as_people_read_and_shown_as_they_can_be(qtbot: QtBot) -> None:
    server = Server({"/srv": [RemoteEntry("gamma", True), RemoteEntry("Delta", True), RemoteEntry("file.txt", False), RemoteEntry(NAME_NOT_UTF8, True)]})
    picker = a_picker(qtbot, "/srv", server)
    assert items(picker) == [UP_TEXT, NAME_SHOWN, "Delta", "gamma"] and picker.choose.text() == "Choose"


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_both_enter_keys_open_the_highlighted_folder_and_never_also_choose(qtbot: QtBot, key: Qt.Key) -> None:
    server = Server({"/": [RemoteEntry("inner", True)], "/inner": []})
    picker = a_picker(qtbot, "/", server)
    picker.show()
    picker.folders.setCurrentRow(0)
    picker.folders.setFocus()
    QTest.keyClick(picker.folders, key)
    assert server.asked == ["/", "/inner"] and picker.isVisible() and picker.result() != QDialog.DialogCode.Accepted.value


def test_a_row_that_is_not_in_the_list_opens_nothing(qtbot: QtBot) -> None:
    server = Server({"/": [RemoteEntry("inner", True)]})
    picker = a_picker(qtbot, "/", server)
    picker._open(QListWidgetItem("elsewhere"))
    assert server.asked == ["/"]


def test_a_start_at_the_top_that_is_not_there_climbs_no_further(qtbot: QtBot) -> None:
    server = Server({})
    picker = a_picker(qtbot, "/", server)
    assert server.asked == ["/"] and not picker.choose.isEnabled() and items(picker) == []


def test_without_a_connection_there_is_no_way_up_either(qtbot: QtBot) -> None:
    def lost(folder: str, shown: Callable[[list[RemoteEntry]], None], failed: Failed) -> None:
        failed("Not connected", False)

    picker = a_picker(qtbot, "/srv", lost, lost_text="Not connected")
    assert items(picker) == [] and not picker.choose.isEnabled()


def test_a_folder_that_fails_after_one_was_shown_keeps_that_one_and_climbs_nowhere(qtbot: QtBot) -> None:
    server = Server({"/srv": [RemoteEntry("gone", True), RemoteEntry("closed", True)]}, refused=("/srv/closed",))
    picker = a_picker(qtbot, "/srv", server)
    for name in ("gone", "closed"):  # not there any more; there, but it can't be read
        picker.open_folder(f"/srv/{name}")
        assert picker.folder == "/srv" and picker.choose.isEnabled() and picker.chosen() == "/srv"
    assert server.asked == ["/srv", "/srv/gone", "/srv/closed"]


def test_an_answer_for_a_folder_the_person_has_left_is_ignored(qtbot: QtBot) -> None:
    waiting: dict[str, tuple[Callable[[list[RemoteEntry]], None], Failed]] = {}

    def later(folder: str, shown: Callable[[list[RemoteEntry]], None], failed: Failed) -> None:
        waiting[folder] = (shown, failed)

    picker = a_picker(qtbot, "/srv", later)
    waiting["/srv"][0]([RemoteEntry("a", True), RemoteEntry("b", True)])
    picker.open_folder("/srv/a")
    picker.open_folder("/srv/b")
    waiting["/srv/a"][1]("Permission denied", False)  # the person has moved on to b
    waiting["/srv/b"][0]([])
    assert picker.folder == "/srv/b" and items(picker) == [UP_TEXT]


# --- the overwrite question


def a_question(qtbot: QtBot, upload: bool = True, name: str = "a.txt", target_modified: float | None = 1_600_000_000.0) -> OverwriteDialog:
    dialog = OverwriteDialog(Conflict(name, "/srv", upload, 5, 1_700_000_000.0, 3, target_modified, None, connection="Shop"))
    qtbot.addWidget(dialog)
    return dialog


def test_the_question_says_where_the_file_is_in_its_title_its_text_and_its_hover_text(qtbot: QtBot) -> None:
    dialog = a_question(qtbot, upload=False)
    assert dialog.windowTitle() == "File already there"
    assert plain_of(dialog.text.text()) == "a.txt is already in /srv on this computer, for the download from Shop."
    assert dialog.text.toolTip() == box_text("a.txt\n/srv\nShop")
    assert "Replace it?" in [label.text() for label in dialog.findChildren(QLabel)]


def test_a_date_that_is_unknown_leaves_no_comma_behind() -> None:
    assert _details(3, None) == size_text(3)


def test_cancel_in_the_rename_box_goes_back_to_the_question_quietly(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = a_question(qtbot, name=NAME_NOT_UTF8)
    offered: list[str] = []

    def cancelled(_parent: QWidget, _title: str, _label: str, _mode: object, text: str) -> tuple[str, bool]:
        offered.append(text)
        return "typed but cancelled", False

    monkeypatch.setattr(QInputDialog, "getText", cancelled)
    dialog.buttons[Choice.RENAME].click()
    assert offered == [NAME_SHOWN] and dialog.answer == Answer(Choice.STOP) and dialog.result() != QDialog.DialogCode.Accepted.value


def test_the_name_as_shown_is_the_name_it_has_already(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = a_question(qtbot, name=NAME_NOT_UTF8)
    said: list[str] = []
    def typed(*_args: object) -> tuple[str, bool]:
        return NAME_SHOWN, True

    def told(*args: object) -> None:
        said.append(plain_of(str(args[2])))

    monkeypatch.setattr(QInputDialog, "getText", typed)
    monkeypatch.setattr(QMessageBox, "information", told)
    dialog.buttons[Choice.RENAME].click()
    assert said == [SAME_NAME_TEXT] and dialog.result() != QDialog.DialogCode.Accepted.value
