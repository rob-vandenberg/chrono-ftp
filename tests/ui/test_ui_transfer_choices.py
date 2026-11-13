"""What the window hands to the queue: the options a connection can carry out, the choices of the transfer window and of Synchronize,
a drop on the way up, and what the queue shows of a job without a counted total; in the real window against the fake servers."""

from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QProgressBar
from pytestqt.qtbot import QtBot

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections.base import Capabilities, CommandResult
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.comparedialog import CompareDialog
from chrono_ftp.ui.filelist import FROM_LOCAL, PARENT_ENTRY, size_text
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.overwritedialog import OverwriteDialog
from chrono_ftp.ui.queueview import PROGRESS_COLUMN, QueueView
from chrono_ftp.ui.settingsdialog import SettingsDialog
from chrono_ftp.ui.transferdialog import NOT_WRITABLE_HERE_TEXT, TransferDialog
from chrono_ftp.ui.transferoptionsform import TransferOptionsDialog, summary_text
from tests.fakeservers import NOT_AS_ROOT, ftp_server, ssh_server
from tests.test_engine import Scripted
from tests.ui.test_ui_sessions import both_ways, connected, names_on_the_right, select, ssh_settings, synchronize_window, transfers_ended, trust_server, window
from tests.ui.test_ui_transfers import OLD_TIME, ftp_settings, settled, upload_one, write_at

# --- Version ------------------------------------------------------------
__version__ = "test_ui_transfer_choices.py 0.0.44"

# --- Version history ----------------------------------------------------
# v0.0.44: Synchronize starts from the Start button, not Apply (D-097)
# v0.0.43: Synchronize through its checklist window and Apply (block FF)
# v0.0.12: Initial: an Edit save and Synchronize carry only what the connection can do; Transfer settings from the transfer window
#          reach the summary and the job; Synchronize's downloads ask nothing and use the options; a drop on the way up on the right;
#          the size done of a job without a counted total; a drop on the right from a lost tab; OK without rights to set; Count first in
#          the summary; a download folder that can be written but not entered; the Edit test reads the file only after the save ended

__all__ = ["window"]  # the fixture of the sessions tests

WANTED: TransferOptions = TransferOptions(keep_timestamps=True, set_rights=True, rights="0640")  # neither can this FTP server do
LIMITED: tuple[str, ...] = ("MFMT", "SITE CHMOD")


def test_an_edit_save_on_a_server_without_dates_and_rights_carries_only_what_it_can_do(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    window.settings = saved_settings.Settings(transfer_options=WANTED, show_transfer_window=False)
    opened: list[str] = []
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ftp_server(root, without=LIMITED) as port:
        connected(qtbot, window, ftp_settings(port))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["edit"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        Path(opened[0]).write_text("new text", encoding="utf-8")
        qtbot.waitUntil(lambda: any(job.upload for job in window.queue.jobs) and transfers_ended(window))  # the save went, to its end
        settled(qtbot, window)
        assert (root / "a.txt").read_text(encoding="utf-8") == "new text"  # read once nothing replaces it any more
    saved = [job for job in window.queue.jobs if job.upload]
    assert saved and all(not job.options.keep_timestamps and not job.options.set_rights for job in saved)
    assert all(job.state == State.DONE and job.message == "" for job in saved), [job.message for job in saved]  # nothing failed halfway


def test_synchronize_on_a_server_without_dates_and_rights_carries_only_what_it_can_do(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "mine.txt").write_text("mine", encoding="utf-8")
    window.settings = saved_settings.Settings(transfer_options=WANTED, show_transfer_window=False)
    with ftp_server(root, without=LIMITED) as port:
        connected(qtbot, window, ftp_settings(port))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 1)
        dialog.start_button.click()
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    [job] = window.queue.jobs
    assert not job.options.keep_timestamps and not job.options.set_rights
    assert job.state == State.DONE and job.message == "", job.message
    assert (root / "mine.txt").read_text(encoding="utf-8") == "mine"


def test_transfer_settings_from_the_transfer_window_reach_its_summary_and_the_job(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    summaries: list[str] = []

    def changed(dialog: TransferOptionsDialog) -> int:
        dialog.form.only_newer.setChecked(True)
        return QDialog.DialogCode.Accepted.value

    def through_settings(dialog: TransferDialog) -> int:
        summaries.append(dialog.summary.text())
        dialog.settings_button.click()
        summaries.append(dialog.summary.text())
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferOptionsDialog, "exec", changed)
    monkeypatch.setattr(TransferDialog, "exec", through_settings)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        upload_one(qtbot, window, here, "a.txt")
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    [job] = window.queue.jobs
    assert job.options.only_newer and job.state == State.DONE, job.message
    assert summaries[0] != summaries[1] and summaries[1] == summary_text(job.options, True)


def test_synchronize_fetches_a_ticked_file_over_the_one_here_without_a_question_and_with_the_options(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    write_at(here / "a.txt", "older here", OLD_TIME)
    (root / "a.txt").write_text("newer there", encoding="utf-8")
    asked: list[OverwriteDialog] = []

    def question(dialog: OverwriteDialog) -> int:
        asked.append(dialog)
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(OverwriteDialog, "exec", question)
    window.settings = saved_settings.Settings(transfer_options=TransferOptions(only_newer=True, keep_timestamps=False), show_transfer_window=False)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.go_to(str(here))
        dialog = synchronize_window(qtbot, window)
        both_ways(qtbot, dialog, 1)
        dialog.start_button.click()
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    [job] = window.queue.jobs
    assert not job.upload and asked == [] and job.state == State.DONE, job.message
    assert job.options == TransferOptions(ask_before_replacing=False, only_newer=False, keep_timestamps=False)  # Settings, less the question
    assert (here / "a.txt").read_text(encoding="utf-8") == "newer there" and (here / "a.txt").stat().st_mtime != OLD_TIME


def test_a_drop_on_the_way_up_on_the_right_uploads_into_the_folder_above(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "inner").mkdir(parents=True)
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    targets: list[str] = []

    def accept(dialog: TransferDialog) -> int:
        targets.append(dialog.folder())
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(TransferDialog, "exec", accept)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/inner")
        window._dropped_on_remote(FROM_LOCAL, [str(here / "a.txt")], PARENT_ENTRY)
        qtbot.waitUntil(lambda: transfers_ended(window))
        settled(qtbot, window)
    assert targets == ["/"] and (root / "a.txt").read_text(encoding="utf-8") == "a" and not (root / "inner" / "a.txt").exists()


def test_a_running_job_without_a_counted_total_shows_the_size_done_so_far(qtbot: QtBot) -> None:
    view = QueueView()
    qtbot.addWidget(view)
    remote = Scripted(CommandResult(0, "", ""))
    remote.capabilities = Capabilities()
    job = Job(remote, Connection(name="T", host="h"), "/a", "/srv", True, "a", state=State.RUNNING, total=0, percent=0, done=123_456)
    view.show_job(job)
    bar = view.cellWidget(0, PROGRESS_COLUMN)
    assert isinstance(bar, QProgressBar) and bar.format() == size_text(123_456)


def test_a_drop_on_the_right_from_a_lost_tab_opens_no_transfer_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_text("a", encoding="utf-8")
    shown: list[str] = []

    def opened(dialog: TransferDialog) -> int:
        shown.append(dialog.folder())
        return QDialog.DialogCode.Rejected.value

    monkeypatch.setattr(TransferDialog, "exec", opened)
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.sessions[0].alive = False  # the connection dropped; its listing stays on screen
        window._dropped_on_remote(FROM_LOCAL, [str(here / "a.txt")], "")
        after: list[bool] = []
        QTimer.singleShot(0, lambda: after.append(True))  # runs after anything the drop put off until it finished
        qtbot.waitUntil(lambda: bool(after))
    assert shown == [] and window.queue.jobs == [] and list(root.iterdir()) == []


def test_ok_needs_no_rights_value_while_set_the_rights_is_unticked(qtbot: QtBot) -> None:
    dialog = SettingsDialog(saved_settings.Settings())
    qtbot.addWidget(dialog)
    dialog.transfer_options.set_rights.setChecked(False)
    dialog.transfer_options.rights.setText("64")  # not complete, and not wanted
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None and ok.isEnabled() and dialog.transfer_options.acceptable()


def test_the_summary_says_count_first_when_it_is_ticked() -> None:
    assert "Count first" in summary_text(TransferOptions(count_first=True), True)
    assert "Count first" not in summary_text(TransferOptions(count_first=False), True)


@NOT_AS_ROOT
def test_a_download_folder_that_can_be_written_but_not_entered_is_refused(qtbot: QtBot, tmp_path: Path) -> None:
    closed = tmp_path / "closed"
    closed.mkdir()
    closed.chmod(0o600)  # writable, but nothing in it can be reached
    try:
        dialog = TransferDialog(False, ["a.txt"], str(closed), TransferOptions(), {}, lambda start: None)
        qtbot.addWidget(dialog)
        dialog.buttons.accepted.emit()
        assert dialog.result() != QDialog.DialogCode.Accepted.value and dialog.problem.text() == NOT_WRITABLE_HERE_TEXT
    finally:
        closed.chmod(0o700)

