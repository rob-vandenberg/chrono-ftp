"""The tabs of the main window: each is one connection, with its own local and remote folder (OR-4)."""

import threading
import time
from dataclasses import replace
from typing import override

from PySide6.QtWidgets import QMessageBox

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import RemoteConnection, UnknownServer
from chrono_ftp.connections.factory import remote_connection
from chrono_ftp.core import credentials
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.errors import ConnectionLost, CredentialError, LoginRefused, RemoteActionFailed
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.transfers.queue import LINE, State
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.logindialog import LoginDialog
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import WINDOW_TITLE, WindowBase
from chrono_ftp.ui.plaintext import asked, box_text

# --- Version ------------------------------------------------------------
__version__ = "windowtabs.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Closing a tab also stops its paused transfers, and counts every transfer of the tab whatever connection it holds; after a
#          reconnect the tab's transfers that are still to run, or may run again, go over the new connection; "1 transfer ... is"
# v0.0.12: Names in message boxes are shown as written, never read as HTML; the paths of unsent copies are in the box's details
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


TAB_UNFINISHED_TEXTS: tuple[str, str] = ("1 transfer on {name} is not finished. Stop it and close the tab?", "{count} transfers on {name} are not finished. Stop them and close the tab?")


class TabsPart(WindowBase):
    """The tabs of the main window: each is one connection, with its own local and remote folder (OR-4)."""

    @override
    def current(self) -> Session | None:
        index = self.tab_bar.currentIndex()
        return self.sessions[index] if 0 <= index < len(self.sessions) else None

    def show_connection_list(self) -> None:
        if self.connection_list is None:
            self.connection_list = ConnectionList(self)
            self.connection_list.connect_requested.connect(self.open_connection)
        self.connection_list.show()
        self.connection_list.raise_()

    def open_connection(self, connection: Connection) -> None:
        session = Session(connection, Worker(self.bridge.deliver), self.local_side.folder)
        self.sessions.append(session)
        index = self.tab_bar.addTab(connection.name)
        self.tab_bar.set_colour(index, None)  # grey until the connection is live
        self.tab_bar.setCurrentIndex(index)
        self._connect(session)

    def _ask_login(self, session: Session, password_needed: bool) -> bool:
        """False when the person cancelled: nothing about the connection has changed then."""
        connection = session.connection
        dialog = LoginDialog(connection.name, connection.user, not connection.key_file, password_needed, self)
        if dialog.exec() != LoginDialog.DialogCode.Accepted.value:
            if session.typed_user:
                session.connection = replace(connection, user=session.user_before)
                session.typed_user = False
            if session.typed_password:
                session.password = None  # what was typed for a login the person gave up
                session.typed_password = False
            return False
        user = dialog.user.text().strip()
        if user != connection.user:
            if not session.typed_user:
                session.user_before = connection.user
            session.connection = replace(connection, user=user)
            session.typed_user = True
        if dialog.password.text():
            session.password = dialog.password.text()
            session.typed_password = True
        return True

    @override
    def _connect(self, session: Session) -> None:
        if session.connecting:
            return
        if not session.connection.user and not self._ask_login(session, False):
            self.update_actions()  # a login without a name would go out under a name the person never chose
            return
        session.connecting = True
        self.status.showMessage(f"Connecting to {session.connection.host}")
        old = session.remote
        session.remote = None
        session.alive = False
        made: list[RemoteConnection] = []

        def work() -> str:
            if old is not None:
                old.close()
            if not session.password:
                try:
                    session.password = credentials.store.read(session.connection.name)
                except CredentialError:
                    session.password = None
            remote = remote_connection(session.connection, session.password)
            made.append(remote)
            remote.connect()
            session.home_folder = remote.home()
            start = session.remote_folder or session.connection.start_folder
            if start:
                try:
                    remote.list_folder(start)
                    return start
                except ConnectionLost:
                    raise
                except RemoteActionFailed:
                    pass  # a starting folder that is gone: the home folder is always there
            return session.home_folder

        def connected(folder: str) -> None:
            self._connected(session, made[0], folder)

        def failed(error: Exception) -> None:
            session.connecting = False
            if made:
                threading.Thread(target=made[0].close, daemon=True).start()
            self._not_connected(session, error)

        session.worker.submit(work, connected, failed)
        self.update_actions()

    def _connected(self, session: Session, remote: RemoteConnection, folder: str) -> None:
        session.connecting = False
        if session not in self.sessions:  # the tab was closed in the meantime
            threading.Thread(target=remote.close, daemon=True).start()
            return
        session.remote = remote
        session.alive = True
        session.connected_at = time.monotonic()
        if session is self.current():
            self.remote_side.home_folder = session.home_folder
        self.status.showMessage(f"Connected to {session.connection.host}")
        if session.typed_password and session.password:
            self._store_password(session, session.password)
            session.typed_password = False
        if session.typed_user:
            self._store_user(session)
            session.typed_user = False
        self._set_tab(session)
        self.load_folder(session, folder)
        for job in self.queue.jobs:
            if job.tab is session and job.state != State.RUNNING:
                job.remote = remote  # what is still to run, or may run again, goes over the tab's new connection
        for copy in self._edits_of(session):
            if self.edited[copy].after_reconnect:
                self.edited[copy].held = False
        self.edit_settle.start()  # what was saved while the connection was gone goes now
        self.update_actions()

    def _not_connected(self, session: Session, error: Exception) -> None:
        if session not in self.sessions:
            return
        if isinstance(error, UnknownServer):
            answer = QMessageBox.question(self, WINDOW_TITLE, box_text(f"{error}\n\nServer: {session.connection.host}\nFingerprint: {error.fingerprint}\n\nDo you trust this server?"))
            if answer == QMessageBox.StandardButton.Yes:
                (trust.remember_host_key if error.kind == "host key" else trust.remember_certificate)(error.to_remember)
                self._connect(session)
            else:
                self.update_actions()  # Reconnect is there again
            return
        if isinstance(error, LoginRefused) and (not session.connection.key_file or session.typed_user):
            if self._ask_login(session, not session.connection.key_file):
                self._connect(session)
            else:
                self.status.showMessage(str(error))
                self.update_actions()
            return
        self.status.showMessage(str(error))
        QMessageBox.information(self, WINDOW_TITLE, box_text(f"{session.connection.name}: {error}"))
        self.update_actions()

    def _store_user(self, session: Session) -> None:
        try:
            saved_connections.remember_user(session.connection.name, session.connection.user)
        except OSError:
            self.status.showMessage(f"The user name of {session.connection.name} could not be kept for next time.")
        if self.connection_list is not None:
            self.connection_list.user_accepted(session.connection.name, session.connection.user)

    def _store_password(self, session: Session, password: str) -> None:
        try:
            credentials.store.write(session.connection.name, password)
        except CredentialError as error:
            self.status.showMessage(str(error))

    @override
    def _set_tab(self, session: Session) -> None:
        index = self.sessions.index(session)
        self.tab_bar.set_colour(index, session.connection.colour if session.alive else None)
        if session is self.current():
            self.remote_side.set_colour(session.connection.colour if session.alive else None)
        self._show_connection_state()

    def _tab_changed(self, index: int) -> None:
        session = self.current()
        self.tab_bar.update()
        if session is self.shown:
            return  # another tab went away: this one stays as it is, with its selection
        self.shown = session
        if session is None:
            self.remote_side.clear()
            self.update_actions()
            return
        self.local_side.history = session.local_history
        self.local_side.walk_to(session.local_folder)
        self.remote_side.clear()
        self.remote_side.history = session.remote_history
        self.remote_side.file_list.name_mask = session.name_mask
        self.remote_side.set_colour(session.connection.colour if session.alive else None)
        self.remote_side.home_folder = session.home_folder
        if session.remote_folder:
            self.remote_side.show_folder(session.remote_folder, list(session.entries))
        self.update_actions()

    def _local_folder_changed(self, folder: str) -> None:
        session = self.current()
        if session is not None:
            session.local_folder = folder
        self.update_actions()

    def close_tab(self, index: int) -> None:
        session = self.sessions[index]
        running = [job for job in self.queue.jobs if self._tab_of(job) is session and job.state in (*LINE, State.RUNNING)]  # whatever connection it holds
        open_here = self._edits_of(session)
        if running:
            text = (TAB_UNFINISHED_TEXTS[0] if len(running) == 1 else TAB_UNFINISHED_TEXTS[1]).format(count=len(running), name=session.connection.name)
            if QMessageBox.question(self, WINDOW_TITLE, box_text(text)) != QMessageBox.StandardButton.Yes:
                return
        if open_here and not asked(self, WINDOW_TITLE, self._edits_text(open_here) + " Close the tab?", self._unsent_paths(open_here)):
            return
        for job in running:  # only now: every question came first
            self.queue.stop(job)
        self._forget_edits(self._edits_of(session))  # read again: a download may have ended during a question
        self.sessions.pop(index)
        session.alive = False
        self.tab_bar.removeTab(index)
        self._let_go_of(session)

    def _let_go_of(self, session: Session) -> None:
        remote = session.remote
        session.worker.finish_with(remote.close if remote is not None else lambda: None)

    def _show_connection_state(self) -> None:
        session = self.current()
        alive = session is not None and session.alive and session.remote is not None
        self.protocol_label.setText(session.connection.protocol.upper() if session is not None and alive else "")
        self.lock_label.setVisible(alive and session is not None and session.remote is not None and session.remote.encrypted)
        if session is not None and alive:
            seconds = int(time.monotonic() - session.connected_at)
            self.time_label.setText(f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}")
        else:
            self.time_label.setText("")
