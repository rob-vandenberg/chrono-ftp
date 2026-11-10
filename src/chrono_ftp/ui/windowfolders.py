"""The remote folders of the main window: asking the server, loading, refreshing, and noticing a dropped connection (OR-3)."""

from collections.abc import Callable
from typing import override

from PySide6.QtWidgets import QMessageBox

from chrono_ftp.connections.base import RemoteConnection, RemoteEntry, shown_name
from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost
from chrono_ftp.transfers.queue import Owned, State, tidy
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import WINDOW_TITLE, WindowBase
from chrono_ftp.ui.plaintext import box_text

# --- Version ------------------------------------------------------------
__version__ = "windowfolders.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Tidying keeps a part file only while the next transfer of that file can continue it, and a part it removes loses its note
#          (FB-02); _failed is declared in the base
#          names are shown as written, never read as HTML; tidying touches only the leftovers this program made itself
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


class FoldersPart(WindowBase):
    """The remote folders of the main window: asking the server, loading, refreshing, and noticing a dropped connection (OR-3)."""

    @override
    def _ask(self, session: Session, work: Callable[[RemoteConnection], object], done: Callable[[object], None], refresh_after_failure: bool = False, on_failure: Callable[[], None] | None = None) -> None:
        remote = session.remote
        if remote is None or not session.alive:
            return

        def failed(error: Exception) -> None:
            if on_failure is not None:
                on_failure()
            self._failed(session, error)
            if refresh_after_failure and session.alive:
                self._reload(session)  # part of it may have been done

        session.worker.submit(lambda: work(remote), done, failed)

    def _mark_lost(self, session: Session) -> None:
        session.alive = False
        if session in self.sessions:
            self._set_tab(session)
        self.update_actions()

    @override
    def _failed(self, session: Session, error: Exception) -> None:
        if session not in self.sessions:
            return
        if isinstance(error, ConnectionLost):
            self._mark_lost(session)
        text = shown_name(str(error))
        self.status.showMessage(text)
        if not isinstance(error, ChronoFtpError):
            raise error
        QMessageBox.information(self, WINDOW_TITLE, box_text(f"{session.connection.name}: {text}"))

    def check_connections(self) -> None:
        for session in self.sessions:
            remote = session.remote
            if remote is not None and session.alive:

                def gone(error: Exception, session: Session = session) -> None:
                    if isinstance(error, ConnectionLost) and session in self.sessions:
                        self.status.showMessage(f"{session.connection.name}: {error}")
                        self._mark_lost(session)

                session.worker.submit(remote.check, lambda _nothing: None, gone)

    @override
    def load_folder(self, session: Session, folder: str) -> None:
        session.wanted_folder = folder
        place = (session.connection.host, session.connection.port)
        busy = any((job.connection.host, job.connection.port) == place and job.state in (State.WAITING, State.RUNNING) for job in self.queue.jobs)

        def read(remote: RemoteConnection) -> list[RemoteEntry]:
            found = remote.list_folder(folder)
            owned = Owned(session.connection, self.settings.transfer_options)
            return found if busy else tidy(remote, folder, found, session.leftovers, owned)  # a running transfer has its temporary files in use

        def show(found: object) -> None:
            assert isinstance(found, list)
            if folder != session.wanted_folder:
                return  # the person has moved on
            session.remote_folder = folder
            session.entries = tuple(found)
            if session is self.current():
                self.remote_side.show_folder(folder, list(session.entries))
                self.update_actions()

        def not_there() -> None:
            if session.wanted_folder == folder:
                session.wanted_folder = session.remote_folder  # Refresh goes on with the folder on the screen

        self._ask(session, read, show, on_failure=not_there)

    @override
    def _reload(self, session: Session) -> None:
        """A fresh look, without undoing a move the person just asked for."""
        self.load_folder(session, session.wanted_folder or session.remote_folder)

    def open_remote_folder(self, folder: str) -> None:
        session = self.current()
        if session is not None:
            self.load_folder(session, folder)

    def _load_tree_children(self, folder: str) -> None:
        session = self.current()
        if session is None:
            return

        def show(found: object) -> None:
            assert isinstance(found, list)
            if session is self.current():
                self.remote_side.fill_children(folder, found)

        self._ask(session, lambda remote: remote.list_folder(folder), show)

    def refresh(self) -> None:
        session = self.current()
        if session is not None and session.remote_folder:
            self._reload(session)

    def reconnect(self) -> None:
        session = self.current()
        if session is not None and not session.alive:
            self._connect(session)

    @override
    def _then_refresh(self, session: Session) -> Callable[[object], None]:
        def done(_result: object) -> None:
            self._reload(session)

        return done

    @override
    def _on_screen(self) -> tuple[Session, str, list[RemoteEntry]] | None:
        """The tab, folder and selection as the person sees them now. An action reads them first, before any window opens, so what it does
        is what was on the screen when the person chose it, whatever arrives in the meantime."""
        session = self.current()
        if session is None or not self.remote_side.folder:
            return None
        return session, self.remote_side.folder, self.remote_side.selected_entries()
