"""The tabs of the main window: each is one connection, with its own local and remote folder (OR-4)."""

import os
import threading
import time
from dataclasses import replace
from typing import override

from PySide6.QtCore import QByteArray, QDir, QPoint
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu, QMessageBox

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import RemoteConnection, UnknownServer, shown_name
from chrono_ftp.connections.factory import remote_connection
from chrono_ftp.core import credentials
from chrono_ftp.core.connection import FTP, LONGEST_TAB_NAME, Connection
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
__version__ = "windowtabs.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 2: open_another_tab makes the further tabs of one connection (Duplicate Tab, and a workspace that holds
#          more than one on it); a tab that can't take dropped files says why while they hang over it (OR-7)
# v0.0.45: Review round 1: a tab name longer than LONGEST_TAB_NAME is refused when it is typed
# v0.0.45: Block FI: a tab dragged to another place in the row, and that order kept for next time; the middle mouse button closes a
#          tab; files dropped on a tab go to that connection without switching to it, and the tab is marked while it would take them;
#          Ctrl+Tab, Ctrl+Shift+Tab and Alt+1 to Alt+9; Rename Tab, Duplicate Tab, Reconnect, Disconnect and Close Tab in the menu of
#          a tab; a list of every open tab; a tab shows the folder it is in next to its name (FI-01)
# v0.0.42: Closing a tab, or showing another one, stops the folder sizes counted for it, silently (FE-03)
# v0.0.41: A tab shows the left side with its own filter, as it left it (FD-06)
# v0.0.40: The menu of a tab pauses the automatic refresh of that tab and clears its cached listings (FG-15)
# v0.0.39: A tab shows each side with or without thumbnails, as it left them (FQ-02)
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
DISCONNECT_UNFINISHED_TEXTS: tuple[str, str] = ("1 transfer on {name} is not finished. Stop it and disconnect?", "{count} transfers on {name} are not finished. Stop them and disconnect?")
RENAME_TEXT: str = "Rename Tab…"
RENAME_TITLE: str = "Rename Tab"
RENAME_LABEL: str = "Name for this tab (empty brings back the name of the connection):"
TAB_NAME_TOO_LONG_TEXT: str = "A tab name can be at most {count} characters, so the tab was not renamed. Give a shorter name."
DUPLICATE_TEXT: str = "Duplicate Tab"
RECONNECT_TEXT: str = "Reconnect"
DISCONNECT_TEXT: str = "Disconnect"
CLOSE_TEXT: str = "Close Tab"
ALREADY_LIVE_TEXT: str = "This tab is connected."
CONNECTING_TEXT: str = "This tab is connecting."
NOT_LIVE_TO_STOP_TEXT: str = "This tab is not connected."
DISCONNECTED_TEXT: str = "{name} is disconnected. Reconnect to use it again."
RENAMED_NOT_KEPT_TEXT: str = "The tab is called {name} now, but that could not be kept with the connection for next time."
FOLDER_SEPARATOR: str = " — "  # between the name of a tab and the folder it is in
ROOT_FOLDER: str = "/"
TAB_TIP_TEXT: str = "{name}\nOn the server: {remote}\nOn this computer: {local}"
NO_FOLDER_YET_TIP: str = "no folder open yet"
NO_TAB_TEXT: str = "No tab is open."
TAB_NOT_LIVE_TEXT: str = "{name} is not connected, so it can't take files."
TAB_NO_FOLDER_TEXT: str = "{name} has no folder open on the server yet."
DROP_HERE_TEXT: str = "Drop them on {name} to send them to {folder}."
ORDER_NOT_KEPT_TEXT: str = "The tabs are in this order now, but that could not be kept for next time."


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
        rename = QAction(icons.icon("edit-rename"), RENAME_TEXT, menu)
        rename.triggered.connect(lambda: self.rename_tab(session))
        menu.addAction(rename)
        duplicate = QAction(icons.icon("edit-copy"), DUPLICATE_TEXT, menu)
        duplicate.triggered.connect(lambda: self.duplicate_tab(session))
        menu.addAction(duplicate)
        menu.addSeparator()
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
        menu.addSeparator()
        self.tab_refresh_items(menu, session)  # Pause Automatic Refresh and Clear Cached Listings (FG-15)
        menu.addSeparator()
        for icon_name, text, why, handler in (
            ("network-connect", RECONNECT_TEXT, CONNECTING_TEXT if session.connecting else ALREADY_LIVE_TEXT if session.alive else "", lambda: self.reconnect_tab(session)),
            ("network-disconnect", DISCONNECT_TEXT, "" if session.alive else NOT_LIVE_TO_STOP_TEXT, lambda: self.disconnect_tab(session)),
            ("tab-close", CLOSE_TEXT, "", lambda: self.close_tab_of(session)),
        ):
            action = QAction(icons.icon(icon_name), text, menu)
            action.triggered.connect(handler)
            if why:
                action.setEnabled(False)  # greyed with its reason, never hidden (OR-20)
                action.setToolTip(why)
                menu.setToolTipsVisible(True)
            menu.addAction(action)
        return menu

    # --- what a tab says (FI-01) -----------------------------------------------------------

    def tab_label(self, session: Session) -> str:
        """The name of the tab and, after it, the last part of the folder it is in on the server, so two tabs on one server can be
        told apart (FI-01)."""
        name = session.connection.tab_name or session.connection.name
        folder = session.remote_folder
        if not folder:
            return name
        return name + FOLDER_SEPARATOR + (folder.rstrip("/").rsplit("/", 1)[-1] or ROOT_FOLDER)

    @override
    def refresh_tab_label(self, session: Session) -> None:
        if session in self.sessions:
            index = self.sessions.index(session)
            self.tab_bar.setTabText(index, shown_name(self.tab_label(session)))
            self.tab_bar.setTabToolTip(index, TAB_TIP_TEXT.format(
                name=shown_name(session.connection.tab_name or session.connection.name),
                remote=shown_name(session.remote_folder) or NO_FOLDER_YET_TIP,
                local=shown_name(session.local_folder),
            ))

    def rename_tab(self, session: Session) -> None:
        """The name the person gives this tab, kept with its connection; an empty name brings the connection's own name back (FI-01)."""
        if session not in self.sessions:
            return
        typed = QInputDialog.getText(self, RENAME_TITLE, RENAME_LABEL, QLineEdit.EchoMode.Normal, session.connection.tab_name)
        if not typed[1]:
            return
        chosen = typed[0].strip()
        if len(chosen) > LONGEST_TAB_NAME:  # never accept what would not fit a tab (OR-20)
            self.status.showMessage(TAB_NAME_TOO_LONG_TEXT.format(count=LONGEST_TAB_NAME))
            return
        session.connection = replace(session.connection, tab_name=chosen)
        self.refresh_tab_label(session)
        for other in self.sessions:  # every other tab of the same connection carries the same name
            if other is not session and other.connection.name == session.connection.name:
                other.connection = replace(other.connection, tab_name=chosen)
                self.refresh_tab_label(other)
        try:
            kept = saved_connections.remember_tab_name(session.connection.name, chosen)
        except OSError:
            kept = False
        if self.connection_list is not None:
            self.connection_list.tab_renamed(session.connection.name, chosen)  # an open connections window must not put the old name back
        if not kept:
            self.status.showMessage(RENAMED_NOT_KEPT_TEXT.format(name=shown_name(self.tab_label(session))))

    @override
    def open_another_tab(self, connection: Connection, local_folder: str = "", remote_folder: str = "", index: int | None = None) -> Session:
        """A further tab on a connection that already has one. Only the person's own Duplicate Tab and a workspace that holds more than
        one tab on one connection come here; Connect never does, so it still switches to the tab there is (D-051, D-098)."""
        place = len(self.sessions) if index is None else index
        copy = Session(connection, Worker(self.bridge.deliver), local_folder or self.local_side.folder)
        copy.wanted_start = remote_folder
        self.sessions.insert(place, copy)
        self.tab_bar.insertTab(place, shown_name(connection.tab_name or connection.name))
        self.tab_bar.set_colour(place, None)  # grey until this login is live
        self.refresh_tab_label(copy)
        self.tab_bar.setCurrentIndex(place)
        self._connect(copy)
        return copy

    def duplicate_tab(self, session: Session) -> None:
        """A second tab to the same server, with the folders this one is in; it keeps its own two folders from then on (OR-4, D-098)."""
        if session not in self.sessions:
            return
        index = self.sessions.index(session) + 1
        copy = Session(session.connection, Worker(self.bridge.deliver), session.local_folder)
        copy.remote_folder = session.remote_folder
        copy.entries = session.entries  # the same folder on the same server: the duplicate shows it at once
        copy.home_folder = session.home_folder
        self.sessions.insert(index, copy)
        self.tab_bar.insertTab(index, shown_name(self.tab_label(copy)))
        self.tab_bar.set_colour(index, None)
        self.refresh_tab_label(copy)
        self.tab_bar.setCurrentIndex(index)
        self._connect(copy)

    def close_tab_of(self, session: Session) -> None:
        if session in self.sessions:
            self.close_tab(self.sessions.index(session))

    def reconnect_tab(self, session: Session) -> None:
        if session in self.sessions and not session.alive and not session.connecting:
            self.tab_bar.setCurrentIndex(self.sessions.index(session))
            self.reconnect()

    def disconnect_tab(self, session: Session) -> None:
        """The connection is closed and the tab stays, grey (OR-3); unfinished transfers are asked about first, as closing asks."""
        if session not in self.sessions or not session.alive:
            return
        running = [job for job in self.queue.jobs if self._tab_of(job) is session and job.state in (*LINE, State.RUNNING)]
        if running and self.settings.confirm_close_running:
            text = (DISCONNECT_UNFINISHED_TEXTS[0] if len(running) == 1 else DISCONNECT_UNFINISHED_TEXTS[1]).format(count=len(running), name=session.connection.name)
            if QMessageBox.question(self, WINDOW_TITLE, box_text(text)) != QMessageBox.StandardButton.Yes:
                return
        for job in running:
            self.queue.stop(job)
        remote = session.remote
        session.remote = None
        session.alive = False
        session.connecting = False
        if remote is not None:
            threading.Thread(target=remote.close, daemon=True).start()
        self._mark_lost(session)
        self.status.showMessage(DISCONNECTED_TEXT.format(name=session.connection.name))

    # --- the order of the tabs (FI-01) -----------------------------------------------------

    def _tab_moved(self, first: int, second: int) -> None:
        """The person dragged a tab to another place: its connection goes with it, and the order is kept for next time."""
        if not (0 <= first < len(self.sessions) and 0 <= second < len(self.sessions)):
            return
        self.sessions.insert(second, self.sessions.pop(first))  # Qt moves the tab the same way, and tells this before it tells the new place
        self._keep_tab_order()
        self.tab_bar.update()

    def _keep_tab_order(self) -> None:
        open_names: list[str] = []
        for session in self.sessions:
            if session.connection.name not in open_names:
                open_names.append(session.connection.name)
        rest = [name for name in self.settings.tab_order if name not in open_names]
        self.settings = replace(self.settings, tab_order=(*open_names, *rest))
        try:
            saved_settings.save(self.settings)
        except OSError:
            self.status.showMessage(ORDER_NOT_KEPT_TEXT)

    def _place_for(self, name: str) -> int:
        """Where a new tab for this connection goes: the place the person dragged it to last time, else the end (FI-01)."""
        order = self.settings.tab_order
        if self.opening_workspace or name not in order:  # a workspace opens its tabs in the order it saved (FI-02)
            return len(self.sessions)
        after = order.index(name)
        for index, session in enumerate(self.sessions):
            other = session.connection.name
            if other in order and order.index(other) > after:
                return index
        return len(self.sessions)

    # --- a list of every open tab (FI-01) --------------------------------------------------

    def tabs_menu(self) -> QMenu:
        """Every open tab, for when more fit in the row than can be seen; the one shown now is ticked."""
        menu = QMenu(self)
        menu.setMinimumWidth(menu.fontMetrics().averageCharWidth() * MENU_WIDTH_CHARACTERS)
        for index, session in enumerate(self.sessions):
            action = QAction(shown_name(self.tab_label(session)), menu)
            action.setCheckable(True)
            action.setChecked(index == self.tab_bar.currentIndex())
            action.triggered.connect(lambda _checked=False, index=index: self.tab_bar.setCurrentIndex(index))
            menu.addAction(action)
        return menu

    def show_tabs_menu(self) -> None:
        if not self.sessions:
            return
        menu = self.tabs_menu()
        open_menu(menu, self.all_tabs_button.mapToGlobal(QPoint(0, self.all_tabs_button.height())))
        menu.deleteLater()

    def go_to_next_tab(self, step: int) -> None:
        """Ctrl+Tab and Ctrl+Shift+Tab: the next or the previous tab, round and round (FI-01)."""
        if len(self.sessions) > 1:
            self.tab_bar.setCurrentIndex((self.tab_bar.currentIndex() + step) % len(self.sessions))

    def go_to_tab_number(self, number: int) -> None:
        """Alt+1 to Alt+9: the tab at that place in the row; a place without a tab does nothing (FI-01)."""
        if 0 <= number - 1 < len(self.sessions):
            self.tab_bar.setCurrentIndex(number - 1)

    # --- files dropped on a tab (FI-01) ----------------------------------------------------

    def tab_takes_files(self, index: int) -> str:
        """Why the tab at this place can't take dropped files; empty when it can."""
        if not 0 <= index < len(self.sessions):
            return NO_TAB_TEXT
        session = self.sessions[index]
        name = shown_name(session.connection.tab_name or session.connection.name)
        if not session.alive:
            return TAB_NOT_LIVE_TEXT.format(name=name)
        if not session.remote_folder:
            return TAB_NO_FOLDER_TEXT.format(name=name)
        return ""

    def _hovered_with_files(self, index: int) -> None:
        """While files are dragged over the row: what would happen if they were let go here, or why nothing would (G-2, OR-7)."""
        if not 0 <= index < len(self.sessions):
            self.status.clearMessage()
            return
        why = self.tab_takes_files(index)
        if why:
            self.status.showMessage(why)  # it can't take them, and says so, never silently (OR-7)
            return
        session = self.sessions[index]
        self.status.showMessage(DROP_HERE_TEXT.format(name=shown_name(session.connection.tab_name or session.connection.name), folder=shown_name(session.remote_folder)))

    def files_dropped_on_tab(self, index: int, paths: list[str]) -> None:
        """Files from the left side, or from a file manager, let go on a tab: they go to that connection, and the tab shown does not
        change (FI-01). The transfer is offered exactly as a drop on the right side is."""
        if not 0 <= index < len(self.sessions) or self.tab_takes_files(index):
            return
        session = self.sessions[index]
        self._offer(session, paths or self.local_side.selected_paths(), session.remote_folder, True, after_a_drop=True)

    def _show_tab_menu(self, place: QPoint) -> None:
        """The menu of the tab at this place, or, beside the tabs, the menu of the workspaces (FI-02)."""
        menu = self.tab_menu_for(place) or self.workspaces_menu()
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

    @override
    def open_connection(self, connection: Connection, local_folder: str = "", remote_folder: str = "") -> None:
        """local_folder and remote_folder: the folders a workspace saved for this tab; they win over the starting folders of the
        connection, which is what the person saved (FI-02)."""
        for index, open_session in enumerate(self.sessions):
            if open_session.connection.name == connection.name:
                self.tab_bar.setCurrentIndex(index)  # already open: its tab, never a second one
                self._open_again(index, open_session, connection)
                return
        wanted_local, local_gone = local_folder, False
        local_folder = self.local_side.folder
        if wanted_local and os.path.isdir(wanted_local):
            local_folder = wanted_local
        elif connection.local_start_folder:
            wanted = local_start_folder(connection.local_start_folder)  # a relative one starts at the home folder
            local_gone = not os.path.isdir(wanted)
            if not local_gone:
                local_folder = wanted
            elif os.path.isdir(QDir.homePath()):
                local_folder = QDir.homePath()
        session = Session(connection, Worker(self.bridge.deliver), local_folder)
        session.wanted_start = remote_folder  # empty: the starting folder of the connection, as ever
        if local_gone:
            session.local_fallback = local_folder  # not written back as the starting folder unless the person moves it (D-058)
        index = self._place_for(connection.name)  # the place the person dragged this tab to last time (FI-01)
        self.sessions.insert(index, session)
        self.tab_bar.insertTab(index, shown_name(connection.tab_name or connection.name))
        self.tab_bar.set_colour(index, None)  # grey until the connection is live
        self.refresh_tab_label(session)
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
            start = session.remote_folder or session.wanted_start or session.connection.start_folder
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
        self.refresh_tab_label(session)
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
        self._stop_counts(lambda counted: counted is not None and counted is not session)  # a count belongs to the tab it was asked on
        if session is None:
            self.remote_side.clear()
            self._enter_looks(None)
            self._tab_shown()
            self.update_actions()
            return
        self.local_side.history = session.local_history
        self.local_side.walk_to(session.local_folder)
        self.local_side.file_list.set_name_mask(session.local_name_mask)  # each tab keeps the filter of both sides (FD-06)
        self.remote_side.clear()
        self.remote_side.history = session.remote_history
        self.remote_side.file_list.name_mask = session.name_mask
        self.remote_side.set_colour(session.connection.colour if session.alive else None)
        self.remote_side.home_folder = session.home_folder
        if session.remote_folder:
            self.remote_side.show_folder(session.remote_folder, list(session.entries))
        self._enter_columns(session)
        self._enter_looks(session)
        self._tab_shown()
        self.update_actions()

    def _local_folder_changed(self, folder: str) -> None:
        session = self.current()
        if session is not None:
            previous = session.local_folder
            session.local_folder = folder
            if previous != folder:
                self._local_moved(session, previous, folder)
            self.refresh_tab_label(session)  # the hover text of the tab names both its folders
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
        self._stop_counts(lambda counted: counted is session)  # silently: nobody waits for those sizes any more
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
