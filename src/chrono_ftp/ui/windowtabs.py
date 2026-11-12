"""The tabs of the main window: each is one connection, with its own local and remote folder (OR-4)."""

import os
import threading
import time
from dataclasses import replace
from typing import override

from PySide6.QtCore import QByteArray, QDir, QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QMessageBox

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import RemoteConnection, UnknownServer, shown_name
from chrono_ftp.connections.factory import remote_connection
from chrono_ftp.core import credentials
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ConnectionLost, CredentialError, LoginRefused, RemoteActionFailed
from chrono_ftp.engine.browsing import local_start_folder
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.transfers.queue import LINE, State
from chrono_ftp.ui import icons
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.logindialog import LoginDialog
from chrono_ftp.ui.menus import open_menu
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import CONNECTION_LIST_PARTS, CONNECTION_LIST_SIZE, WINDOW_TITLE, WindowBase
from chrono_ftp.ui.plaintext import asked, box_text

# --- Version ------------------------------------------------------------
__version__ = "windowtabs.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: The menu of a tab opens Server Information, greyed while the tab is not connected (FP-03), and the Session Log (FP-02);
#          closing a tab with running transfers asks unless switched off (FP-01); a tab keeps its own sort order and columns where
#          that is chosen
# v0.0.37: The menu of a tab also installs my public key on its server and changes the password there, each greyed with its reason;
#          Change Password is not shown on FTP, as it needs a shell (FM-09, FM-10, D-027)
# v0.0.34: A connection opens in its local starting folder too; a starting folder that is gone says so calmly and opens the home
#          folder; closing a tab keeps its last folders when its connection remembers them; each step of the left side and each tab
#          shown is told to navigation; a connection saved in the connections window reaches its open tabs; a new login ends
#          synchronized browsing (FH-05, FH-06); a deleted connection is told to navigation; a relative local starting folder is read
#          from the home folder; the folder a gone starting folder fell back to is noted
# v0.0.31: The connection that goes live is kept as the last one; at start the connections window opens on it (D-053)
# v0.0.29: The menu of a tab opens through open_menu, which lets go of the keyboard (D-052)
# v0.0.28: The menu of a tab is a full-width menu with an icon; Edit settings…
# v0.0.27: The right-click menu of a tab: Edit opens the connections window on that connection
# v0.0.26: Connect on a connection that has a tab: changed settings log in again in that tab, asking first about its running
#          transfers; unchanged and dropped reconnects; unchanged and live only switches (D-051)
# v0.0.25: A connection that already has a tab switches to it; a second start of the program brings the window to the front
# v0.0.16: Also with its splitter where it was
# v0.0.15: The connections window opens with the size and place it had last time
# v0.0.13: Closing a tab also stops its paused transfers, and counts every transfer of the tab whatever connection it holds; after a
#          reconnect the tab's transfers that are still to run, or may run again, go over the new connection; "1 transfer ... is"
# v0.0.12: Names in message boxes are shown as written, never read as HTML; the paths of unsent copies are in the box's details
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


RECONNECT_UNFINISHED_TEXTS: tuple[str, str] = ("1 transfer on {name} is not finished. Stop it and reconnect?", "{count} transfers on {name} are not finished. Stop them and reconnect?")
EDIT_TEXT: str = "Edit settings…"
KEY_TEXT: str = "Install My Public Key…"
PASSWORD_TEXT: str = "Change Password…"
MENU_WIDTH_CHARACTERS: int = 22  # a menu of normal width, also with one short entry
INFORMATION_TEXT: str = "Server Information…"
LOG_TEXT: str = "Session Log…"
NOT_LIVE_TEXT: str = "This tab is not connected. Reconnect to see what the server tells."
EDIT_GONE_TEXT: str = "This connection is no longer in your connection list."
LOCAL_START_GONE_TEXT: str = "The starting folder {path} of {name} is no longer on this computer, so your home folder opens instead."
REMOTE_START_GONE_TEXT: str = "The starting folder {path} of {name} can't be opened on the server, so your home folder there opens instead."
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
            saved_place = self.saved_sizes.get(CONNECTION_LIST_SIZE, "")
            saved_parts = self.saved_sizes.get(CONNECTION_LIST_PARTS, "")
            # nothing or a damaged value: the default stays
            self.connection_list.restoreGeometry(QByteArray.fromBase64(saved_place.encode("ascii", "replace")))
            self.connection_list.parts.restoreState(QByteArray.fromBase64(saved_parts.encode("ascii", "replace")))
            self.connection_list.connect_requested.connect(self.open_connection)
            self.connection_list.saved.connect(self._connection_saved)
            self.connection_list.deleted.connect(self._connection_deleted)
        self.connection_list.show()
        self.connection_list.raise_()

    def show_connection_list_at_start(self) -> None:
        """The connections window at start, on the connection that was last live (D-053); the top one when that is gone."""
        self.show_connection_list()
        assert self.connection_list is not None
        self.connection_list.start_on(self.settings.last_connection)

    def _keep_as_last(self, session: Session) -> None:
        name = session.connection.name
        if name == self.settings.last_connection:
            return
        self.settings = replace(self.settings, last_connection=name)
        try:
            saved_settings.save(self.settings)
        except OSError:
            pass  # a convenience: the connection itself goes on

    def tab_menu_for(self, place: QPoint) -> QMenu | None:
        """The right-click menu of the tab at this place in the tab bar; nothing beside the tabs."""
        index = self.tab_bar.tabAt(place)
        if not 0 <= index < len(self.sessions):
            return None
        name = self.sessions[index].connection.name
        menu = QMenu(self)
        menu.setMinimumWidth(menu.fontMetrics().averageCharWidth() * MENU_WIDTH_CHARACTERS)
        edit = QAction(icons.icon("document-properties"), EDIT_TEXT, menu)
        if name in {connection.name for connection in saved_connections.load().connections}:
            edit.triggered.connect(lambda: self.edit_connection(name))
        else:
            edit.setEnabled(False)  # greyed with its reason, never hidden (OR-20)
            edit.setToolTip(EDIT_GONE_TEXT)
            menu.setToolTipsVisible(True)
        menu.addAction(edit)
        session = self.sessions[index]
        key = QAction(icons.icon("database-change-key"), KEY_TEXT, menu)
        key.triggered.connect(lambda: self.install_my_key(session))
        password = QAction(icons.icon("object-locked"), PASSWORD_TEXT, menu)
        password.triggered.connect(lambda: self.change_password(session))
        for action, why in ((key, self.key_reason(session)), (password, self.password_reason(session))):
            if action is password and session.connection.protocol == FTP:
                continue  # it needs a shell: not shown on FTP (D-027)
            if why:
                action.setEnabled(False)  # greyed with its reason (OR-20)
                action.setToolTip(why)
                menu.setToolTipsVisible(True)
            menu.addAction(action)
        menu.addSeparator()
        information = QAction(icons.icon("documentinfo"), INFORMATION_TEXT, menu)
        information.triggered.connect(lambda: self.show_server_info(session))
        if not session.alive:
            information.setEnabled(False)  # greyed with its reason (OR-20)
            information.setToolTip(NOT_LIVE_TEXT)
            menu.setToolTipsVisible(True)
        menu.addAction(information)
        log = QAction(icons.icon("view-list-text"), LOG_TEXT, menu)
        log.triggered.connect(lambda: self.show_session_log(session))
        menu.addAction(log)
        return menu

    def _show_tab_menu(self, place: QPoint) -> None:
        menu = self.tab_menu_for(place)
        if menu is not None:
            open_menu(menu, self.tab_bar.mapToGlobal(place))
            menu.deleteLater()

    def edit_connection(self, name: str) -> None:
        """The connections window, open on this connection and ready to change it."""
        self.show_connection_list()
        if self.connection_list is not None:
            self.connection_list.select_connection(name)

    def come_forward(self) -> None:
        """Another start of Chrono FTP: this window comes to the front instead (D-051)."""
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def open_connection(self, connection: Connection) -> None:
        for index, open_session in enumerate(self.sessions):
            if open_session.connection.name == connection.name:
                self.tab_bar.setCurrentIndex(index)  # already open: its tab, never a second one
                self._open_again(index, open_session, connection)
                return
        local_folder, local_gone = self.local_side.folder, False
        if connection.local_start_folder:
            wanted = local_start_folder(connection.local_start_folder)  # a relative one starts at the home folder
            local_gone = not os.path.isdir(wanted)
            if not local_gone:
                local_folder = wanted
            elif os.path.isdir(QDir.homePath()):
                local_folder = QDir.homePath()
        session = Session(connection, Worker(self.bridge.deliver), local_folder)
        if local_gone:
            session.local_fallback = local_folder  # not written back as the starting folder unless the person moves it (D-058)
        self.sessions.append(session)
        index = self.tab_bar.addTab(connection.name)
        self.tab_bar.set_colour(index, None)  # grey until the connection is live
        self.tab_bar.setCurrentIndex(index)
        self._connect(session)
        if local_gone:
            QMessageBox.information(self, WINDOW_TITLE, box_text(LOCAL_START_GONE_TEXT.format(path=shown_name(connection.local_start_folder), name=connection.name)))

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
        self._sync_ends(session)  # a new login is no step: the folder it falls back to must not be mirrored
        self.status.showMessage(f"Connecting to {session.connection.host}")
        old = session.remote
        session.remote = None
        session.alive = False
        made: list[RemoteConnection] = []
        start_gone: list[str] = []  # the starting folder of the connection, when it can't be opened

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
                    if not session.remote_folder:
                        start_gone.append(start)  # a starting folder that is gone: the home folder is always there
            return session.home_folder

        def connected(folder: str) -> None:
            if start_gone:
                session.remote_fallback = folder  # not written back as the starting folder unless the person moves it (D-058)
            self._connected(session, made[0], folder)
            if start_gone and session in self.sessions:
                QMessageBox.information(self, WINDOW_TITLE, box_text(REMOTE_START_GONE_TEXT.format(path=shown_name(start_gone[0]), name=session.connection.name)))

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
        self._keep_as_last(session)
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
            self._tab_shown()
        self._show_connection_state()

    def _tab_changed(self, index: int) -> None:
        session = self.current()
        self.tab_bar.update()
        if session is self.shown:
            return  # another tab went away: this one stays as it is, with its selection
        self._leave_columns(self.shown)
        self.shown = session
        if session is None:
            self.remote_side.clear()
            self._tab_shown()
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
        self._enter_columns(session)
        self._tab_shown()
        self.update_actions()

    def _local_folder_changed(self, folder: str) -> None:
        session = self.current()
        if session is not None:
            previous = session.local_folder
            session.local_folder = folder
            if previous != folder:
                self._local_moved(session, previous, folder)
        self.update_actions()

    def close_tab(self, index: int) -> None:
        session = self.sessions[index]
        running = [job for job in self.queue.jobs if self._tab_of(job) is session and job.state in (*LINE, State.RUNNING)]  # whatever connection it holds
        open_here = self._edits_of(session)
        if running and self.settings.confirm_close_running:  # unless switched off in Settings, Confirmations (FP-01)
            text = (TAB_UNFINISHED_TEXTS[0] if len(running) == 1 else TAB_UNFINISHED_TEXTS[1]).format(count=len(running), name=session.connection.name)
            if QMessageBox.question(self, WINDOW_TITLE, box_text(text)) != QMessageBox.StandardButton.Yes:
                return
        if open_here and not asked(self, WINDOW_TITLE, self._edits_text(open_here) + " Close the tab?", self._unsent_paths(open_here)):
            return
        for job in running:  # only now: every question came first
            self.queue.stop(job)
        self._forget_edits(self._edits_of(session))  # read again: a download may have ended during a question
        self._remember_folders(session)
        session.sync_browsing = False
        self.sessions.pop(index)
        session.alive = False
        self.tab_bar.removeTab(index)
        self._let_go_of(session)

    def _open_again(self, index: int, session: Session, connection: Connection) -> None:
        """Connect on a connection that has a tab (D-051): new settings log in again there; the same settings reconnect only a dropped one."""
        changed = connection != session.connection
        if not changed:
            if not session.alive:
                self._connect(session)
            return
        running = [job for job in self.queue.jobs if self._tab_of(job) is session and job.state in (*LINE, State.RUNNING)]
        if running:
            text = (RECONNECT_UNFINISHED_TEXTS[0] if len(running) == 1 else RECONNECT_UNFINISHED_TEXTS[1]).format(count=len(running), name=connection.name)
            if QMessageBox.question(self, WINDOW_TITLE, box_text(text)) != QMessageBox.StandardButton.Yes:
                return
            for job in running:
                self.queue.stop(job)
        session.connection = connection
        session.password = None  # read from the store again: the new settings may have a new password
        self.tab_bar.set_colour(index, None)  # grey until the new login is live
        self._connect(session)

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
