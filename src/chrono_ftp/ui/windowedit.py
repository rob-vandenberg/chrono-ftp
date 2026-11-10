"""Edit in the main window: a server file opens in the person's own program, and every save goes back to the server."""

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import replace
from typing import override

from PySide6.QtWidgets import QMessageBox

from chrono_ftp.connections.base import joined, RemoteConnection, RemoteEntry, shown_name
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.session import EditedFile, is_at_or_below, login_of, no_progress, same_server, Session
from chrono_ftp.ui.windowbase import EDIT_WAIT_TRIES, NOT_OPENABLE_TEXT, WINDOW_TITLE, WindowBase
from chrono_ftp.ui.plaintext import asked, box_text, fitting, told, wrapped

# --- Version ------------------------------------------------------------
__version__ = "windowedit.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: A save goes with the default transfer options, never asks the overwrite question, is never skipped as not newer, and never
#          changes the rights of the file on the server; names in message boxes are shown as written; a pending edit whose copy is gone
#          leaves no empty folder behind; long names in its message boxes are shortened in their middle, but the path of a copy with
#          unsent changes is shown whole, over several lines, and unbroken in the box's details, to copy
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)

EDIT_FOLDER_PREFIX: str = "chrono-ftp-edit-"


class EditPart(WindowBase):
    """Edit in the main window: a server file opens in the person's own program, and every save goes back to the server."""

    @override
    def _edits_of(self, session: Session) -> list[str]:
        return [copy for copy, edited in self.edited.items() if edited.session is session]

    @override
    def _unsent_paths(self, copies: list[str]) -> str:
        """The paths of the copies whose changes are not on the server yet, each whole on a line of its own: the box's details."""
        return "\n".join(copy for copy in copies if copy in self.edited and self.edited[copy].pending)

    @override
    def _edits_text(self, copies: list[str]) -> str:
        """What the person should know before these files lose their way back to the server."""
        known = [copy for copy in copies if copy in self.edited]
        waiting = [copy for copy in known if self.edited[copy].pending]
        others = [fitting(shown_name(self.edited[copy].name), self) for copy in known if not self.edited[copy].pending]
        lines: list[str] = []
        if waiting:
            lines.append(f"Saved, but not on the server yet. Your copy stays here:\n{chr(10).join(wrapped(copy, self) for copy in waiting)}")
        if others:
            lines.append(f"Open for editing: {', '.join(others)}. What you save after this no longer goes to the server.")
        return "\n\n".join(lines) + "\n\n"

    @override
    def _forget_edits(self, copies: list[str]) -> None:
        """A copy with changes that never reached the server stays on the disk: it may be the only one. When that copy is gone itself
        (its program removed or moved it), its folder goes once nothing else is in it."""
        for copy in copies:
            edited = self.edited.pop(copy, None)
            self.edit_watcher.removePath(copy)
            if edited is None or not edited.pending:
                shutil.rmtree(os.path.dirname(copy), ignore_errors=True)
            elif not os.path.lexists(copy):
                try:
                    os.rmdir(os.path.dirname(copy))
                except OSError:
                    pass  # something else is in it, perhaps the program's own copy: it stays

    def _remote_file_opened(self, entry: RemoteEntry) -> None:
        screen = self._on_screen()
        if screen is not None:
            self._edit_entry(screen[0], screen[1], entry.name)

    def edit(self) -> None:
        """A copy goes to a temporary folder and opens in the person's own program; every save goes back to the server."""
        screen = self._on_screen()
        if screen is not None and len(screen[2]) == 1 and not screen[2][0].is_folder:
            self._edit_entry(screen[0], screen[1], screen[2][0].name)

    def _edit_entry(self, session: Session, folder: str, name: str) -> None:
        if shown_name(name) != name:
            QMessageBox.information(self, WINDOW_TITLE, box_text(NOT_OPENABLE_TEXT))
            return
        already = next((copy for copy, edited in self.edited.items() if same_server(edited.session, session) and edited.folder == folder and edited.name == name), None)
        if already is not None and self.edited[already].pending:
            self._open_local_file(already)  # its changes are not on the server yet: a fresh copy would lose them
            return
        place = (*login_of(session), folder, name)
        if not session.alive or place in self.fetching:
            return
        self.fetching.add(place)
        saves_before = self.edited[already].saves if already is not None else 0
        temporary = tempfile.mkdtemp(prefix=EDIT_FOLDER_PREFIX)
        copy = os.path.join(temporary, name)

        def fetch(connection: RemoteConnection) -> None:
            connection.download(joined(folder, name), copy, no_progress)

        def opened(_nothing: object) -> None:
            self.fetching.discard(place)
            if session not in self.sessions:  # the tab was closed in the meantime: a save could never go back
                shutil.rmtree(temporary, ignore_errors=True)
                return
            if already is not None and already in self.edited:
                if self.edited[already].pending or self.edited[already].saves != saves_before:  # saved during the download: that copy is the newest
                    shutil.rmtree(temporary, ignore_errors=True)
                    self._open_local_file(already)
                    return
                self._forget_edits([already])  # all of it reached the server, and the file may have changed there since
                self.status.showMessage(f"Fresh copy of {shown_name(name)} opened. A window with the earlier copy no longer sends to the server")
            self.edited[copy] = EditedFile(session, folder, name)
            self.edit_watcher.addPath(copy)
            self._open_local_file(copy)

        def not_fetched() -> None:
            self.fetching.discard(place)
            shutil.rmtree(temporary, ignore_errors=True)

        self._ask(session, fetch, opened, on_failure=not_fetched)

    def _edited_file_changed(self, copy: str) -> None:
        edited = self.edited.get(copy)
        if edited is not None:
            edited.unsent = True
            edited.saves += 1
            edited.held = False
            edited.after_reconnect = False
            self.edit_settle.start()

    def _send_edited_files(self) -> None:
        for copy, edited in list(self.edited.items()):
            if not edited.unsent:
                continue
            if not os.path.exists(copy):
                edited.waited += 1
                if edited.waited < EDIT_WAIT_TRIES:
                    self.edit_settle.start()  # a program that saves by moving the old file away: the new one comes in a moment
                else:
                    self.status.showMessage(f"The copy of {shown_name(edited.name)} is gone, so nothing more goes to the server")
                    self._forget_edits([copy])
                continue
            edited.waited = 0
            if copy not in self.edit_watcher.files():
                self.edit_watcher.addPath(copy)  # a file replaced by a save has left the watcher
            if edited.job is not None or edited.held:
                continue  # one on its way: its end looks again
            if edited.session.alive:
                edited.unsent = False
                # The save is what the person wants there: no question, never skipped as not newer, and the file keeps its own rights.
                options = replace(self.settings.transfer_options, ask_before_replacing=False, only_newer=False, set_rights=False)
                edited.job = self._add_job(edited.session, copy, edited.folder, True, edited.name, options)
                self.status.showMessage(f"Sending {shown_name(edited.name)} back to the server")
            else:
                self.status.showMessage(f"{shown_name(edited.name)} goes to the server when the connection is back")

    @override
    def _stop_editing(self, session: Session, folder: str, names: list[str]) -> None:
        """A file that is renamed, moved or deleted here is no longer where its saves would go: they must not make it again."""
        paths = [joined(folder, name) for name in names]
        gone = [copy for copy, edited in self.edited.items() if same_server(edited.session, session) and any(is_at_or_below(joined(edited.folder, edited.name), path) for path in paths)]
        if gone:
            for copy in gone:
                job = self.edited[copy].job
                if job is not None:
                    self.queue.stop(job)
            text = self._edits_text(gone)
            detail = self._unsent_paths(gone)
            self._forget_edits(gone)
            told(self, WINDOW_TITLE, f"This file changed its place on the server, so saving no longer sends it there.\n\n{text}".strip(), detail)

    @override
    def _gone_and_refresh(self, session: Session, folder: str, names: list[str]) -> Callable[[object], None]:
        """Only once the server agreed: a refused rename leaves the file where it is, and its edit goes on."""

        def done(_result: object) -> None:
            self._stop_editing(session, folder, names)
            self._reload(session)

        return done

    @override
    def _edit_job_ended(self, job: Job) -> None:
        edited = next((item for item in self.edited.values() if item.job is job), None)
        if edited is None or job.state in (State.WAITING, State.RUNNING):
            return
        edited.job = None
        if job.state != State.FAILED and edited.unsent:
            self.edit_settle.start()  # saved again in the meantime: that save is a new one, whatever happened to this send
            return
        if job.state == State.DONE:
            return
        edited.unsent = True  # closing asks first
        edited.held = True
        edited.after_reconnect = job.state == State.FAILED
        if job.state == State.STOPPED:
            return
        name = fitting(shown_name(edited.name), self)
        self.status.showMessage(f"{name} is not on the server yet")
        copy = next(path for path, item in self.edited.items() if item is edited)
        text = f"{name} could not go to the server: {shown_name(job.message)}\n\nYour changes are kept here:\n{wrapped(copy, self)}\n\nTry again now?"
        if asked(self, WINDOW_TITLE, text, copy):  # the path also whole, to copy, in the details
            edited.held = False
            self._send_edited_files()
