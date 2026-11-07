"""The transfers of the main window: upload, download, drag and drop, the speed limit, stop, and what a finished job changes."""

from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtWidgets import QMessageBox

from chrono_ftp.connections.base import joined, parent_of, RemoteEntry, shown_name
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.filelist import FROM_REMOTE, PARENT_ENTRY
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import WINDOW_TITLE, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowtransfers.py 0.0.11"

# --- Version history ----------------------------------------------------
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


class TransfersPart(WindowBase):
    """The transfers of the main window: upload, download, drag and drop, the speed limit, stop, and what a finished job changes."""

    def _limit_changed(self, value: int) -> None:
        self.queue.limit = value

    @override
    def _add_job(self, session: Session, local: str, remote_path: str, upload: bool, name: str) -> Job | None:
        remote = session.remote
        if remote is None:
            return None
        job = Job(remote, session.connection, local, remote_path, upload, name, has_password=bool(session.password))
        self.queue.add(job)
        return job

    def upload(self) -> None:
        screen = self._on_screen()
        if screen is not None:
            self._upload_into(screen[0], self.local_side.selected_paths(), screen[1], list(screen[0].entries))

    def _upload_into(self, session: Session, paths: list[str], folder: str, present: list[RemoteEntry]) -> None:
        taken = {entry.name for entry in present}
        for path in self._without_refused(paths, lambda path: Path(path).name in taken):
            self._add_job(session, path, folder, True, Path(path).name)

    def _without_refused(self, items: list[str], exists: Callable[[str], bool]) -> list[str]:
        """Nothing is replaced without asking: the person sees what is there already."""
        present = [Path(item).name for item in items if exists(item)]
        if not present:
            return items
        if QMessageBox.question(self, WINDOW_TITLE, f"Already there: {', '.join(shown_name(name) for name in present)}\n\nReplace?") == QMessageBox.StandardButton.Yes:
            return items
        return [item for item in items if not exists(item)]

    def download(self) -> None:
        self._download_into(self.local_side.folder)

    def _download_into(self, target: str) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        names = [entry.name for entry in screen[2]]
        for name in self._without_refused(names, lambda name: Path(target, name).exists()):
            self._add_job(screen[0], target, joined(screen[1], name), False, name)

    def _dropped_on_local(self, _source: str, _paths: list[str], folder_name: str) -> None:
        folder = self.local_side.folder
        if folder_name == PARENT_ENTRY:
            folder = str(Path(folder).parent)
        elif folder_name:
            folder = str(Path(folder, folder_name))
        self._download_into(folder)

    def _dropped_on_remote(self, source: str, paths: list[str], folder_name: str) -> None:
        screen = self._on_screen()
        if screen is None or not screen[0].alive:
            return
        session, shown, chosen = screen
        target = parent_of(shown) if folder_name == PARENT_ENTRY else joined(shown, folder_name) if folder_name else shown
        if source == FROM_REMOTE:
            self._move_into(session, shown, chosen, target)
            return
        sent = paths or self.local_side.selected_paths()
        if target == shown:
            self._upload_into(session, sent, target, list(session.entries))
            return

        def present(found: object) -> None:
            assert isinstance(found, list)
            self._upload_into(session, sent, target, found)

        self._ask(session, lambda remote: remote.list_folder(target), present)

    def stop_transfers(self) -> None:
        running = [job for job in self.queue.jobs if job.state in (State.WAITING, State.RUNNING)]
        chosen = [job for job in self.queue_view.selected_jobs() if job in running] or running
        for job in chosen:
            self.queue.stop(job)

    def _job_changed(self, job: Job) -> None:
        self.queue_view.show_job(job)
        self.update_actions()
        self._follow_queue(job)
        self._edit_job_ended(job)
        if job.state == State.DONE and job.upload:
            for session in self.sessions:
                if session.remote is job.remote and session.remote_folder == job.remote_path:
                    self._reload(session)
