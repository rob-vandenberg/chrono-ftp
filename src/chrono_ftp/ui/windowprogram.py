"""The program-wide part of the main window (block FP): what a double-click on a file does, the automatic refresh, the columns per tab,
the session log, the server information and the clean-up of stored data."""

import os
from dataclasses import replace
from typing import override

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtWidgets import QApplication

from chrono_ftp.config import bookmarks as saved_places
from chrono_ftp.config.settings import TRANSFER, Settings
from chrono_ftp.connections import sessionlog
from chrono_ftp.connections.base import RemoteConnection, RemoteEntry, joined, shown_name
from chrono_ftp.core.connection import FTP
from chrono_ftp.core.errors import ConnectionLost
from chrono_ftp.core.logrules import LogRules
from chrono_ftp.engine import cleanup, serverinfo
from chrono_ftp.transfers.queue import ENDED
from chrono_ftp.ui.cleanupdialog import CleanupWindow
from chrono_ftp.ui.logwindow import LogWindow
from chrono_ftp.ui.serverinfodialog import ServerInfoWindow
from chrono_ftp.ui.session import Session, may_keep
from chrono_ftp.ui.windowbase import WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowprogram.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: A tab opened from an address gets its own Session Log window, on the log that stands apart (FJ-11)
# v0.0.47: A tab opened from an address gets its own Server Information window, never the one of a connection that carries the name
#          that tab was given (FJ-11)
# v0.0.40: A tab whose automatic refresh is paused is left alone (FG-15)
# v0.0.38: Initial: block FP: the double-click on a file opens or transfers it, the right side refreshes itself every few seconds where
#          chosen, a tab keeps its own sort order and columns where chosen (FP-01); the Session Log (FP-02), Server Information (FP-03)
#          and Clean Up Stored Data (FP-04) windows; review: the automatic refresh waits while a drag runs from or onto either side,
#          and writes one summary line to the session log, its listing only at Debug

NOT_CONNECTED_UPLOAD_TEXT: str = "This tab is not connected, so nothing was uploaded. Reconnect, or switch the double-click to open in Settings, Panels."
NOT_CONNECTED_DOWNLOAD_TEXT: str = "This tab is not connected, so nothing was downloaded. Reconnect, or switch the double-click to open in Settings, Panels."
REFRESH_LOG_TEXT: str = "automatic refresh of {folder}: {count} entries, {changed}"
REFRESH_LOG_FAILED_TEXT: str = "automatic refresh of {folder} failed: {reason}"
REFRESH_FAILED_TEXT: str = "{name}: the folder could not be read again: {reason}"
SETTINGS_REMOVED_TEXT: str = "The settings were removed: Chrono FTP uses its standard settings from now on."
NOT_CONNECTED_TEXT: str = "this tab is not connected"
LOST_TEXT: str = "the connection was lost"
MILLISECONDS: int = 1000


class ProgramPart(WindowBase):
    """The program-wide part of the main window (block FP)."""

    def _build_program(self) -> None:
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_by_itself)
        self.log_windows = {}
        self.info_windows = {}
        self.sizes_forgotten = False
        self.local_side.file_opened.connect(self._local_file_chosen)
        self.remote_side.file_opened.connect(self._remote_file_chosen)

    @override
    def _apply_program_settings(self) -> None:
        settings = self.settings
        sessionlog.set_rules(LogRules(settings.log_level, settings.log_size_megabytes, settings.logs_kept))
        if settings.refresh_seconds:
            self.refresh_timer.start(settings.refresh_seconds * MILLISECONDS)
        else:
            self.refresh_timer.stop()
        for window in self.log_windows.values():
            if isinstance(window, LogWindow):
                window.show_state()

    # --- the double-click on a file (FP-01) -------------------------------------

    def _local_file_chosen(self, path: str) -> None:
        if self.settings.double_click != TRANSFER:
            self._open_local_file(path)
            return
        session = self.current()
        if session is None or not session.alive or not session.remote_folder:
            self.status.showMessage(NOT_CONNECTED_UPLOAD_TEXT)
            return
        self._offer(session, [path], session.remote_folder, True)

    def _remote_file_chosen(self, entry: RemoteEntry) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        if self.settings.double_click != TRANSFER:
            self._edit_entry(session, folder, entry.name)
            return
        if session.alive:
            self._offer(session, [joined(folder, entry.name)], self.local_side.folder, False)
        else:
            self.status.showMessage(NOT_CONNECTED_DOWNLOAD_TEXT)

    # --- the automatic refresh (FP-01) -------------------------------------------

    def refresh_by_itself(self) -> None:
        """The folder on the right read again, quietly: never under an open question or menu, never twice at once, and a failure says so
        in the status bar only; a lost connection makes the tab grey as the regular check does (OR-3)."""
        shown = self.current()
        if shown is None or not shown.alive or shown.connecting or shown.auto_refreshing or shown.refresh_paused or not shown.remote_folder:
            return
        session: Session = shown
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return
        if self.local_side.file_list.in_drag or self.remote_side.file_list.in_drag:
            return  # a list must not change under a drag: the next tick tries again
        remote = session.remote
        if remote is None:
            return
        folder = session.remote_folder
        session.auto_refreshing = True

        def read() -> list[RemoteEntry]:
            with remote.log.summary_only():  # repeated by itself: one line in the log, the listing only at Debug (D-065)
                return remote.list_folder(folder)

        def arrived(found: list[RemoteEntry]) -> None:
            session.auto_refreshing = False
            changed = tuple(found) != session.entries
            remote.log.summary(REFRESH_LOG_TEXT.format(folder=folder, count=len(found), changed="changed" if changed else "unchanged"))
            if session not in self.sessions or folder != session.remote_folder or session.wanted_folder not in ("", folder):
                return  # the person moved on meanwhile: what they asked for comes as usual
            session.refresh_problem = ""
            if tuple(found) == session.entries:
                return  # nothing changed: the screen stays exactly as it is, the tree too
            session.entries = tuple(found)
            if session is self.current() and self.remote_side.folder == folder:
                self.remote_side.show_folder(folder, list(session.entries))
                self.update_actions()

        def failed(error: Exception) -> None:
            session.auto_refreshing = False
            remote.log.summary(REFRESH_LOG_FAILED_TEXT.format(folder=folder, reason=error))
            if session not in self.sessions:
                return
            text = REFRESH_FAILED_TEXT.format(name=session.connection.name, reason=shown_name(str(error)))
            if text != session.refresh_problem:
                session.refresh_problem = text  # said once, not at every refresh
                self.status.showMessage(text)
            if isinstance(error, ConnectionLost):
                self._mark_lost(session)

        session.worker.submit(read, arrived, failed)

    # --- the columns per tab (FP-01) ---------------------------------------------

    @override
    def _leave_columns(self, session: Session | None) -> None:
        if session is None or not self.settings.columns_per_tab:
            return
        session.local_columns = bytes(self.local_side.file_list.header().saveState().data())
        session.remote_columns = bytes(self.remote_side.file_list.header().saveState().data())

    @override
    def _enter_columns(self, session: Session) -> None:
        if not self.settings.columns_per_tab:
            return
        for state, side in ((session.local_columns, self.local_side), (session.remote_columns, self.remote_side)):
            if state:
                side.file_list.header().restoreState(QByteArray(state))  # a tab seen first keeps what is shown now

    # --- the session log (FP-02) ---------------------------------------------------

    @override
    def show_session_log(self, session: Session) -> None:
        name = sessionlog.log_name(session.connection.name, session.connection.not_saved)  # the log of this tab, never another's (FJ-11)
        open_window = self.log_windows.get(name)
        if open_window is not None and open_window.isVisible():
            open_window.raise_()
            open_window.activateWindow()
            return
        window = LogWindow(name, sessionlog.log_for(session.connection.name, session.connection.not_saved), self)
        self.log_windows[name] = window
        window.destroyed.connect(lambda _gone=None: self.log_windows.pop(name, None) if self.log_windows.get(name) is window else None)
        window.show()

    # --- server information (FP-03) -------------------------------------------------

    @override
    def show_server_info(self, session: Session) -> None:
        name = session.connection.name
        key = name if may_keep(session) else f"{name}\0{id(session)}"  # a tab opened from an address has a window of its own (FJ-11)
        window = self.info_windows.get(key)
        if not isinstance(window, ServerInfoWindow):
            window = ServerInfoWindow(name, self)
            self.info_windows[key] = window
        window.show()
        window.raise_()
        window.show_reading()
        connection = session.connection
        address = f"{connection.user}@{connection.host}:{connection.port}" if connection.user else f"{connection.host}:{connection.port}"
        folder = session.remote_folder
        ftp = connection.protocol == FTP
        shown_window = window

        def read(remote: RemoteConnection) -> serverinfo.Report:
            return serverinfo.report(remote, folder, ftp, address)

        def arrived(report: object) -> None:
            if isinstance(report, serverinfo.Report):
                shown_window.show_report(report)

        if not session.alive or session.remote is None:
            shown_window.show_problem(NOT_CONNECTED_TEXT)
            return
        self._ask(session, read, arrived, on_failure=lambda: shown_window.show_problem(LOST_TEXT))

    # --- clean up stored data (FP-04) ------------------------------------------------

    @override
    def clean_up_stored_data(self) -> bool:
        """The Clean Up window; True when the settings were removed, so an open Settings window keeps nothing over that."""
        removed_kinds: set[str] = set()

        def kinds_now() -> list[cleanup.StoredKind]:
            queued = any(job.state not in ENDED for job in self.queue.jobs)
            return cleanup.stored_kinds([os.path.dirname(copy) for copy in self.edited], queued)  # the folders of files open for editing stay

        def removed(keys: set[str]) -> None:
            removed_kinds.update(keys)
            self._follow_cleanup(keys)

        window = CleanupWindow(kinds_now, removed, self)
        window.exec()
        window.deleteLater()
        return cleanup.SETTINGS in removed_kinds

    def _follow_cleanup(self, keys: set[str]) -> None:
        """What the program holds in memory follows what went, so nothing removed is written back by itself."""
        if cleanup.SETTINGS in keys:
            self.settings = Settings()  # the standard settings, in memory only: nothing is written until the person changes one
            self._apply_settings()
            self.sizes_forgotten = True  # closing writes no window places: they were removed on purpose
            self.saved_sizes = {}
            self.status.showMessage(SETTINGS_REMOVED_TEXT)
        if cleanup.BOOKMARKS in keys:
            loaded = saved_places.load()
            self.places = loaded.places
            self.places_writable = loaded.writable
            self._tab_shown()
        if cleanup.CONNECTIONS in keys:
            self.settings = replace(self.settings, last_connection="")
            if self.connection_list is not None:
                self.connection_list.reload()
        if cleanup.LOGS in keys:
            for window in self.log_windows.values():
                if isinstance(window, LogWindow) and window.isVisible():
                    window.text.clear()

