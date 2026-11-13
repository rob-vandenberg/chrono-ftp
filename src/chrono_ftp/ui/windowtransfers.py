"""The transfers of the main window: upload, download, drag and drop, the speed limit, stop, and what a finished job changes."""

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections.base import joined, parent_of, RemoteConnection, RemoteEntry, shown_name
from chrono_ftp.core.errors import ConnectionLost, RemoteActionFailed
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers.options import Answer, Answers, Choice, Conflict, usable
from chrono_ftp.transfers.queue import ENDED, LINE, Job, State
from chrono_ftp.ui.filelist import FROM_REMOTE, PARENT_ENTRY
from chrono_ftp.ui.overwritedialog import OverwriteDialog
from chrono_ftp.ui.remotefolderpicker import Failed, RemoteFolderPicker
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.transferdialog import TransferDialog, agreed_to_make, made_now
from chrono_ftp.ui.windowbase import SETTINGS_NOT_KEPT_TEXT, WINDOW_TITLE, WindowBase
from chrono_ftp.ui.plaintext import box_text, fitting

# --- Version ------------------------------------------------------------
__version__ = "windowtransfers.py 0.0.40"

# --- Version history ----------------------------------------------------
# v0.0.40: A transfer can be a move from the start (F6, Move Here of a right-button drag): ticked in the transfer window, or asked
#          first where that window is off (FG-04, FG-14)
# v0.0.38: A drop asks first: through the transfer window, or a short question where that window is off; with the question switched off
#          in Settings, a drop starts at once (FP-01)
# v0.0.36: The question about a missing local folder is titled with the program's name, not the window title with its version (D-059)
# v0.0.13: A job knows its tab, also after a reconnect; Stop in the question also stops the paused jobs of the action; Edit's saves start by themselves; a question of a paused job ends it paused; Stop All stops every running, waiting and paused item; a job that left the
#          queue stays out of the list; the speed limit of the status bar holds at once, also for a running transfer
# v0.0.12: The transfer window before an upload, a download or a drop (FB-09); every job carries its options, greyed by what the
#          connection can do (FB-01), and the window shows and starts from those; the overwrite question comes from the queue for every
#          file there already (FB-03); the server folder picker says when the connection is lost; a server folder that isn't there
#          is offered to be made before anything starts; after a drop the window comes once the drop has finished; a stale question
#          is never shown; "Don't show this window again" is kept only when the transfer starts
#          names are shown as written, never read as HTML; whether a server folder is there is found from its details, so a folder that
#          can't be read still takes an upload and a file in its place is said plainly; the picker starts at an absolute folder; every
#          window made for a transfer goes again after use; the top of the server is always a folder, and a link whose kind can't be
#          seen is left to the queue; the right side shows what arrived after every upload, also one that failed or was stopped, once
#          per job; boxes fit long names to the screen and name the connection a question is about; a download folder that isn't on
#          this computer yet is offered to be made, as in the transfer window; the move's hover text knows what the server can confirm;
#          the deferred offer after a drop never runs on a deleted window; a box says when the connection was lost meanwhile; a folder
#          here is made only when the transfer starts; the picker hears whether a folder is not there, or could not be read; a tab
#          closed or lost during the folder check is told; a download folder that is a link to nowhere, something else, or can't be
#          looked at, is said so; a drop on the left from a lost tab is refused at once, as one on the right; a link to a place that can't
#          be opened is said so, not called a link to nowhere
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


IS_A_FILE_TEXT: str = "{folder} on the server of {connection} is a file, not a folder, so nothing was sent. Please choose a folder."
DROP_TEXTS: tuple[str, str] = ("1 item", "{count} items")
UPLOAD_DROP_TEXT: str = "Upload {items} to {folder} on the server?"
DOWNLOAD_DROP_TEXT: str = "Download {items} to {folder} on this computer?"
UPLOAD_MOVE_TEXT: str = "Move {items} to {folder} on the server? Each is removed from this computer once it arrived."
DOWNLOAD_MOVE_TEXT: str = "Move {items} to {folder} on this computer? Each is removed from the server once it arrived."
MISSING: str = "missing"
A_FILE: str = "file"
A_FOLDER: str = "folder"
UNKNOWN: str = "unknown"
NO_FOLDER_YET_TEXT: str = "This folder isn't on the server of {connection} yet:\n{folder}\n\nMake it?"
IS_A_FILE_HERE_TEXT: str = "{folder} on this computer is a file, not a folder, so nothing was fetched. Please choose a folder."
LINK_NOWHERE_HERE_TEXT: str = "{folder} on this computer is a link that leads nowhere, so nothing was fetched. Please choose a folder."
LINK_CLOSED_HERE_TEXT: str = "{folder} on this computer is a link to a place that can't be opened here, so nothing was fetched. Please choose a folder."
NOT_A_FOLDER_HERE_TEXT: str = "{folder} on this computer is not a folder, so nothing was fetched. Please choose a folder."
CANT_LOOK_HERE_TEXT: str = "{folder} on this computer can't be looked at ({reason}), so nothing was fetched."
NOT_CONNECTED_TEXT: str = "The connection to the server is lost, so its folders can't be shown. Reconnect and try again."


class FolderMissing(RemoteActionFailed):
    """A server folder that is not there: the only failure that makes the folder picker start at a folder above."""


def what_is_in_the_way_here(folder: str) -> str:
    """What stands where a download folder here was asked for and isn't a folder: a link that leads nowhere or to a place that can't be
    opened, a file, or something else.
    Empty where nothing is in the way (also where nothing is there, or it can't be looked at)."""
    if os.path.islink(folder):
        try:
            os.stat(folder)
        except PermissionError:
            return LINK_CLOSED_HERE_TEXT  # it leads somewhere this computer won't let us look
        except OSError:
            return LINK_NOWHERE_HERE_TEXT  # gone, not a folder on the way, a loop of links, a path too long: it leads nowhere
    if os.path.lexists(folder) and not os.path.isdir(folder):
        return IS_A_FILE_HERE_TEXT if os.path.isfile(folder) else NOT_A_FOLDER_HERE_TEXT
    return ""


def what_is_there(remote: RemoteConnection, path: str) -> str:
    """What the server has at path, found from the details of that one entry (its folder's listing where nothing else tells): never
    from whether the path itself can be listed, as a folder that takes files may not be readable. The top of the server is always a
    folder. A link whose kind can't be seen is UNKNOWN: the queue finds out what it is, never this guess."""
    if path in ("", "/"):
        return A_FOLDER
    try:
        found = remote.details(path)
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        return UNKNOWN
    if found is None:
        return MISSING
    if not found.kind_known or (found.is_link and not found.is_folder):
        return UNKNOWN
    return A_FOLDER if found.is_folder else A_FILE


def make_folders(remote: RemoteConnection, folder: str) -> None:
    """The folder and every folder above it that is not there yet, top first."""
    missing: list[str] = []
    path = folder
    while path not in ("", "/") and what_is_there(remote, path) == MISSING:
        missing.append(path)
        path = parent_of(path)
    for item in reversed(missing):
        remote.make_folder(item)


class TransfersPart(WindowBase):
    """The transfers of the main window: upload, download, drag and drop, the speed limit, stop, and what a finished job changes."""

    def _limit_changed(self, value: int) -> None:
        self.queue.limit = value

    @override
    def _add_job(self, session: Session, local: str, remote_path: str, upload: bool, name: str, options: TransferOptions | None = None, answers: Answers | None = None, by_itself: bool = False) -> Job | None:
        """Without options: the defaults of Settings. Either way only what this connection can do (OR-7). by_itself: it starts in its
        turn also when items do not start by themselves (a save from Edit)."""
        remote = session.remote
        if remote is None:
            return None
        wanted = options if options is not None else self.settings.transfer_options
        effective = usable(wanted, session.capabilities, session.connection.protocol, upload)[0]
        job = Job(remote, session.connection, local, remote_path, upload, name, has_password=bool(session.password), options=effective, answers=answers or Answers(), tab=session)
        self.queue.add(job, by_itself)
        return job

    def upload(self) -> None:
        screen = self._on_screen()
        if screen is not None:
            self._offer(screen[0], self.local_side.selected_paths(), screen[1], True)

    def download(self) -> None:
        self._download_into(self.local_side.folder)

    def _download_into(self, target: str, after_a_drop: bool = False) -> None:
        screen = self._on_screen()
        if screen is not None:
            self._offer(screen[0], [joined(screen[1], entry.name) for entry in screen[2]], target, False, after_a_drop)

    @override
    def _offer(self, session: Session, sources: list[str], target: str, upload: bool, after_a_drop: bool = False, dropped: bool = False, move: bool = False) -> None:
        """The transfer window first, unless the person switched it off (FB-09). sources: local paths for an upload, server paths for a
        download; target: the folder they go into. After a drop the window waits until the drop has finished, so the drag lets go of
        what it held first: what was dropped is taken now. A drop is confirmed by the transfer window, or by a question where that window
        is off, unless the person switched that off too (FP-01). move: F6 or Move Here (FG-04, FG-14): the move is ticked in the transfer
        window; without that window a question always comes first, as the sources go."""
        if after_a_drop:
            QTimer.singleShot(0, self, lambda: self._offer(session, sources, target, upload, dropped=True, move=move))  # with the window as context: never runs on a deleted window
            return
        if not sources or session not in self.sessions:
            return
        never_again = False
        options, reasons = usable(self.settings.transfer_options, session.capabilities, session.connection.protocol, upload)  # what will happen
        window_wanted = self.settings.show_transfer_window and (self.settings.confirm_drop or not dropped)
        if not window_wanted and (move or (dropped and self.settings.confirm_drop)):
            count = DROP_TEXTS[0] if len(sources) == 1 else DROP_TEXTS[1].format(count=len(sources))
            texts = (UPLOAD_MOVE_TEXT, DOWNLOAD_MOVE_TEXT) if move else (UPLOAD_DROP_TEXT, DOWNLOAD_DROP_TEXT)
            text = (texts[0] if upload else texts[1]).format(items=count, folder=fitting(shown_name(target), self))
            if QMessageBox.question(self, WINDOW_TITLE, box_text(text)) != QMessageBox.StandardButton.Yes:
                return
        if window_wanted:
            names = [Path(source).name if upload else source.rsplit("/", 1)[-1] for source in sources]
            def browse(start: str) -> str | None:
                return self._pick_remote_folder(session, start) if upload else self._pick_local_folder(start)

            dialog = TransferDialog(upload, names, target, options, reasons, browse, self, session.capabilities.durable, move)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted.value
            target, options, never_again, to_make = dialog.folder(), dialog.chosen_options(), dialog.never_again.isChecked(), dialog.to_make
            dialog.deleteLater()  # its parent is the window: without this every transfer would leave one behind
            if not accepted:
                return
        else:
            to_make = ""
            options = replace(options, delete_source=move)  # asked above where it is a move
        chosen = options

        def start(make: str = to_make) -> None:
            self._start(session, sources, target, upload, chosen, never_again, make)

        if upload and target != session.remote_folder and session.alive:
            self._when_the_folder_is_there(session, target, start)  # checked before anything starts, never halfway in the queue
        elif not upload and not to_make and not os.path.isdir(target):
            self._when_the_local_folder_is_there(target, start)
        else:
            start()

    def _when_the_local_folder_is_there(self, folder: str, start: Callable[[str], None]) -> None:
        """A folder on this computer that isn't there yet is offered to be made, as one on the server is; No fetches nothing. It is made
        only when the transfer really starts."""
        problem = what_is_in_the_way_here(folder)
        if problem:
            QMessageBox.information(self, WINDOW_TITLE, box_text(problem.format(folder=fitting(shown_name(folder), self), reason="")))
            return
        try:
            os.lstat(folder)
        except FileNotFoundError:
            if agreed_to_make(self, folder, WINDOW_TITLE):  # the program's name, not the title with its version
                start(folder)
            return
        except OSError as error:
            QMessageBox.information(self, WINDOW_TITLE, box_text(CANT_LOOK_HERE_TEXT.format(folder=fitting(shown_name(folder), self), reason=error.strerror or str(error))))

    def _start(self, session: Session, sources: list[str], target: str, upload: bool, options: TransferOptions, never_again: bool, make: str = "") -> None:
        """make: a folder here the person agreed to have made, made only now that the transfer really starts."""
        if session not in self.sessions or not session.alive:
            self._lost_meanwhile(session)  # the tab is grey, and Reconnect brings it back: no folder was made
            return
        problem = made_now(self, make) if make else ""
        if problem:
            QMessageBox.information(self, WINDOW_TITLE, box_text(problem))
            return
        if never_again:
            self._never_show_transfer_window()  # only now that the transfer really starts
        answers = Answers()  # Yes to all holds for every item of this one action
        for source in sources:
            if upload:
                self._add_job(session, source, target, True, Path(source).name, options, answers)
            else:
                self._add_job(session, target, source, False, source.rsplit("/", 1)[-1], options, answers)

    def _when_the_folder_is_there(self, session: Session, folder: str, start: Callable[[], None]) -> None:
        """A server folder that isn't there yet is offered to be made; No sends nothing."""
        if session.remote is None:
            return
        remote: RemoteConnection = session.remote

        def made(_nothing: object) -> None:
            self._reload(session)
            start()

        def found(kind: object) -> None:
            if session not in self.sessions or not session.alive:
                self._lost_meanwhile(session)  # its tab is gone or grey: nothing to ask about any more, and the person is told
                return
            if kind == MISSING:
                if QMessageBox.question(self, WINDOW_TITLE, box_text(NO_FOLDER_YET_TEXT.format(connection=fitting(session.connection.name, self), folder=fitting(shown_name(folder), self)))) == QMessageBox.StandardButton.Yes:
                    session.worker.submit(lambda: make_folders(remote, folder), made, lambda error: self._failed(session, error))
            elif kind == A_FILE:
                QMessageBox.information(self, WINDOW_TITLE, box_text(IS_A_FILE_TEXT.format(connection=fitting(session.connection.name, self), folder=fitting(shown_name(folder), self))))
            else:
                start()  # a folder, also one that takes files but can't be read; or nothing could be told: the queue says how it went

        session.worker.submit(lambda: what_is_there(remote, folder), found, lambda error: self._failed(session, error))

    def _never_show_transfer_window(self) -> None:
        self.settings = replace(self.settings, show_transfer_window=False)
        try:
            saved_settings.save(self.settings)
        except OSError:
            self.status.showMessage(SETTINGS_NOT_KEPT_TEXT)

    def _pick_local_folder(self, start: str) -> str | None:
        chosen = QFileDialog.getExistingDirectory(self, "Download to", start if os.path.isdir(start) else self.local_side.folder)
        return chosen or None

    def _pick_remote_folder(self, session: Session, start: str) -> str | None:
        def load(folder: str, shown: Callable[[list[RemoteEntry]], None], failed: Failed) -> None:
            def arrived(found: object) -> None:
                assert isinstance(found, list)
                shown([entry for entry in found if isinstance(entry, RemoteEntry)])

            def refused(error: Exception) -> None:
                failed(str(error), isinstance(error, FolderMissing))

            remote = session.remote
            if remote is None or not session.alive:
                failed(NOT_CONNECTED_TEXT, False)
                return

            def read() -> list[RemoteEntry]:
                try:
                    return remote.list_folder(folder)
                except ConnectionLost:
                    raise
                except RemoteActionFailed as error:
                    if remote.details(folder) is None:
                        raise FolderMissing(str(error)) from error  # not there: the picker starts at the nearest folder above
                    raise

            session.worker.submit(read, arrived, refused)

        absolute = start if start.startswith("/") else session.home_folder or session.remote_folder or "/"  # a relative path means nothing here
        picker = RemoteFolderPicker(absolute, load, self, NOT_CONNECTED_TEXT)
        chosen = picker.chosen() if picker.exec() == QDialog.DialogCode.Accepted.value else None
        picker.deleteLater()
        return chosen

    def _dropped_on_local(self, _source: str, _paths: list[str], folder_name: str) -> None:
        screen = self._on_screen()
        if screen is None or not screen[0].alive:
            return  # a lost connection's listing is no longer the server's: nothing is offered from it
        folder = self.local_side.folder
        if folder_name == PARENT_ENTRY:
            folder = str(Path(folder).parent)
        elif folder_name:
            folder = str(Path(folder, folder_name))
        self._download_into(folder, after_a_drop=True)

    def _dropped_on_remote(self, source: str, paths: list[str], folder_name: str) -> None:
        screen = self._on_screen()
        if screen is None or not screen[0].alive:
            return
        session, shown, chosen = screen
        target = parent_of(shown) if folder_name == PARENT_ENTRY else joined(shown, folder_name) if folder_name else shown
        if source == FROM_REMOTE:
            self._move_into(session, shown, chosen, target)
            return
        self._offer(session, paths or self.local_side.selected_paths(), target, True, after_a_drop=True)

    def _overwrite_question(self, job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
        """Asked in the window's thread; the queue thread waits for reply. Stop stops every job of the same action. A question whose job
        was stopped meanwhile, or whose tab was closed, is never shown: it stops only that job."""
        if self._tab_of(job) is None:
            self.queue.stop(job)
            reply(Answer(Choice.STOP))
            return
        if job.stop_asked.is_set() or job.state != State.RUNNING:
            reply(Answer(Choice.STOP))  # stopped, paused or deleted meanwhile: it ends as the person asked
            return
        dialog = OverwriteDialog(conflict, self)
        dialog.exec()
        answer = dialog.answer
        dialog.deleteLater()
        if answer.choice == Choice.STOP:
            for other in self.queue.jobs:
                if other.answers is job.answers and other.state in (*LINE, State.RUNNING):
                    self.queue.stop(other)
        reply(answer)

    @override
    def stop_transfers(self) -> None:
        """Stop All: every transfer that runs, waits or is paused. The queue's own Stop acts on the selected items only."""
        for job in self.queue.jobs:
            if job.state in (*LINE, State.RUNNING):
                self.queue.stop(job)

    def _job_changed(self, job: Job) -> None:
        if self.queue.has(job):
            self.queue_view.show_job(job)  # one that left the queue meanwhile stays out of the list
        self.update_actions()
        self._follow_queue(job)
        self._edit_job_ended(job)
        if job.state == State.RUNNING:
            self.ends_shown.discard(id(job))  # a retry or a resume: its next end is news again
        ended = job.state in ENDED
        changed_there = ended and (job.upload or job.options.delete_source) and id(job) not in self.ends_shown  # what arrived, also from a failed or stopped upload; a move took files away
        if ended:
            self.ends_shown.add(id(job))  # later news of the same end (queued before it was taken in) refreshes nothing more
        if changed_there:
            folder = job.remote_path if job.upload else parent_of(job.remote_path)
            for session in self.sessions:
                if session is self._tab_of(job) and session.remote_folder == folder:
                    self._reload(session)
