import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast, override

from PySide6.QtCore import QByteArray, QFile, QFileSystemWatcher, QPoint, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QSizePolicy, QSpinBox, QSplitter, QToolBar, QToolButton, QVBoxLayout, QWidget

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import Capabilities, RemoteConnection, RemoteEntry, UnknownServer, joined, parent_of, safe_name, shown_name
from chrono_ftp.connections.factory import remote_connection
from chrono_ftp.core import credentials
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, CredentialError, LocalActionFailed, LoginRefused, RemoteActionFailed
from chrono_ftp.engine import commands, localfiles
from chrono_ftp.engine.compare import Found, compare_folders
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.engine.terminal import terminal_arguments, terminal_available
from chrono_ftp.transfers.queue import Job, State, TransferQueue, tidy
from chrono_ftp.ui import icons
from chrono_ftp.ui.bridge import Bridge
from chrono_ftp.ui.changedialog import ChangeDialog
from chrono_ftp.ui.comparedialog import CompareDialog, uploads
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.filelist import FROM_REMOTE, PARENT_ENTRY, FileList
from chrono_ftp.ui.localside import LocalSide
from chrono_ftp.ui.queueview import HEIGHT as QUEUE_HEIGHT
from chrono_ftp.ui.queueview import LOWEST_HEIGHT, QueuePanel, QueueView
from chrono_ftp.ui.remoteside import RemoteSide
from chrono_ftp.ui.searchdialog import SearchDialog
from chrono_ftp.ui.settingsdialog import SettingsDialog
from chrono_ftp.ui.sidebar import FolderHistory
from chrono_ftp.ui.tabbar import ConnectionTabBar

# --- Version ------------------------------------------------------------
__version__ = "mainwindow.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: One top row; the queue under a separator, hidden when idle unless pinned, open after a failure
# v0.0.6: Drag and drop, right-click menus, keys, actions on the side with the focus, also on this computer; sizes remembered; leftovers tidied
# v0.0.5: Tabs, actions and transfers: a tab is a connection with its own local and remote folder (D-008, D-027)
# v0.0.3: Initial: the tab bar above both sides, local on the left, remote on the right (OR-1, D-008)

WINDOW_TITLE: str = "Chrono FTP"
START_WIDTH: int = 1280
START_HEIGHT: int = 800
EMPTY_TAB_BAR_LINES: int = 2  # an empty tab bar has no height of its own: it keeps the height of a tab
HIGHEST_LIMIT: int = 1_000_000
CHECK_MILLISECONDS: int = 30_000  # how often an idle connection is checked, so a dropped one goes grey (OR-3)
SHELL_ACTIONS: tuple[str, ...] = ("copy", "search", "change", "terminal")  # hidden on FTP, greyed on SFTP without a shell (D-027)
LOCAL_MENU: tuple[str, ...] = ("upload", "local_edit", "local_rename", "local_delete", "local_new")
REMOTE_MENU: tuple[str, ...] = ("download", "edit", "rename", "move", "copy", "delete", "change", "new_folder")
CLOCK_MILLISECONDS: int = 1000
LOCK_SIZE: int = 16
CENTRE_MARGIN: int = 2
GENERAL_ICON_SIZE: int = 18
QUEUE_LINGER_MILLISECONDS: int = 5000  # the queue stays this long after the last transfer, so the result can be seen
HIGHEST_QUEUE_HEIGHT: int = 4000  # a number from a file is input
EDIT_SETTLE_MILLISECONDS: int = 500  # a program saves in steps: the copy is sent when it has settled
EDIT_WAIT_TRIES: int = 20  # a copy that stays away this often was removed
SETTINGS_NOT_KEPT_TEXT: str = "These settings apply now, but could not be kept for next time."
NOT_A_NAME_TEXT: str = "A name can't hold a slash."
NOT_OPENABLE_TEXT: str = "This name can't be handed to another program. You can still download, rename or delete it."


@dataclass(eq=False)
class EditedFile:
    """A file of the server, open in a program here: its copy goes back when the program saves it."""

    session: "Session"
    folder: str
    name: str
    unsent: bool = False  # saved, and not yet on its way to the server
    job: Job | None = None  # on its way now; only its end says the save reached the server
    waited: int = 0  # how often the copy was not there
    saves: int = 0  # how many saves were noticed: a fresh copy that started before one of them is out of date
    held: bool = False  # stopped or refused: it goes only with a new save of this file
    after_reconnect: bool = False  # refused: a new connection may also send it

    @property
    def pending(self) -> bool:
        return self.unsent or self.job is not None


@dataclass(eq=False)
class Session:
    """One tab: a connection with its own local and remote folder (OR-4)."""

    connection: Connection
    worker: Worker
    local_folder: str
    remote_folder: str = ""
    remote: RemoteConnection | None = None
    entries: tuple[RemoteEntry, ...] = ()
    alive: bool = False
    connecting: bool = False
    password: str | None = None
    typed_password: bool = False  # stored only once the server accepted it
    wanted_folder: str = ""  # the last folder asked for: an older answer is left out
    home_folder: str = ""
    connected_at: float = 0.0
    remote_history: FolderHistory = field(default_factory=FolderHistory)
    local_history: FolderHistory = field(default_factory=FolderHistory)
    name_mask: str = ""  # the filter of the right side
    leftovers: dict[str, tuple[float, int]] = field(default_factory=dict[str, tuple[float, int]])  # see tidy; only the session's thread uses it

    @property
    def capabilities(self) -> Capabilities:
        return self.remote.capabilities if self.remote is not None and self.alive else Capabilities()


def _no_progress(_amount: int) -> None:
    pass


def _login_of(session: Session) -> tuple[str, int, str]:
    return session.connection.host, session.connection.port, session.connection.user


def _same_server(first: Session, second: Session) -> bool:
    return _login_of(first) == _login_of(second)


def _is_at_or_below(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip("/") + "/")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(START_WIDTH, START_HEIGHT)
        self.bridge = Bridge(self)
        self.sessions: list[Session] = []
        self.shown: Session | None = None
        self.queue = TransferQueue(lambda job: self.bridge.deliver(lambda: self._job_changed(job)))
        self.connection_list: ConnectionList | None = None
        self.tab_bar = ConnectionTabBar()
        self.tab_bar.setFixedHeight(self.fontMetrics().height() * EMPTY_TAB_BAR_LINES)
        self.new_tab_button = QToolButton()
        self.new_tab_button.setIcon(icons.icon("tab-new"))
        self.new_tab_button.setText("New Tab")
        self.new_tab_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.new_tab_button.setToolTip("Open a connection in a new tab")
        self.top_row = QWidget()
        top = QHBoxLayout(self.top_row)
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.tab_bar)
        top.addWidget(self.new_tab_button)
        top.addStretch(1)
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)  # many tabs scroll: the buttons on the right stay
        self.general_bar = QToolBar()
        top.addWidget(self.general_bar)
        self.local_side = LocalSide()
        self.remote_side = RemoteSide()
        self.sides = QSplitter(Qt.Orientation.Horizontal)
        self.sides.setChildrenCollapsible(False)
        self.sides.addWidget(self.local_side)  # always left, never swapped (OR-1)
        self.sides.addWidget(self.remote_side)
        self.sides.setStretchFactor(0, 1)
        self.sides.setStretchFactor(1, 1)
        self.queue_view = QueueView()
        self.queue_panel = QueuePanel(self.queue_view)
        self.queue_height: int = QUEUE_HEIGHT
        self.failure_unseen: bool = False  # a failed transfer keeps the queue open until the person closes it
        self.queue_closed: bool = False  # closed by hand during these transfers: it stays closed until they end, unless one fails
        self.body = QSplitter(Qt.Orientation.Vertical)  # the separator above the queue
        self.body.setChildrenCollapsible(False)
        self.body.addWidget(self.sides)
        self.body.addWidget(self.queue_panel)
        self.body.setStretchFactor(0, 1)
        self.body.setStretchFactor(1, 0)
        self.queue_panel.hide()
        self.queue_linger = QTimer(self)
        self.queue_linger.setSingleShot(True)
        self.queue_linger.setInterval(QUEUE_LINGER_MILLISECONDS)
        self.queue_linger.timeout.connect(self._hide_idle_queue)
        self.queue_panel.pin.toggled.connect(self._pin_toggled)
        centre = QWidget()
        layout = QVBoxLayout(centre)
        layout.setContentsMargins(CENTRE_MARGIN, CENTRE_MARGIN, CENTRE_MARGIN, 0)
        layout.setSpacing(CENTRE_MARGIN)
        layout.addWidget(self.top_row)
        layout.addWidget(self.body, 1)
        self.setCentralWidget(centre)
        self.status = self.statusBar()
        self.actions_by_name: dict[str, QAction] = {}
        self.limit = QSpinBox()
        self._build_toolbar()
        self.new_tab_button.clicked.connect(self.show_connection_list)
        self.tab_bar.currentChanged.connect(self._tab_changed)
        self.tab_bar.tabCloseRequested.connect(self.close_tab)
        self.local_side.folder_changed.connect(self._local_folder_changed)
        self.local_side.selection_changed.connect(self.update_actions)
        self.remote_side.folder_requested.connect(self.open_remote_folder)
        self.remote_side.children_requested.connect(self._load_tree_children)
        self.remote_side.selection_changed.connect(self.update_actions)
        self.local_side.file_opened.connect(self._open_local_file)
        self.remote_side.file_opened.connect(self._remote_file_opened)
        self.local_side.file_list.dropped.connect(self._dropped_on_local)
        self.remote_side.file_list.dropped.connect(self._dropped_on_remote)
        self.local_side.file_list.customContextMenuRequested.connect(self._local_menu_wanted)
        self.remote_side.file_list.customContextMenuRequested.connect(self._remote_menu_wanted)
        self.local_side.filter_wanted.connect(lambda: self._ask_mask(self.local_side.file_list))
        self.remote_side.filter_wanted.connect(lambda: self._ask_mask(self.remote_side.file_list))
        self.remote_side.refresh_wanted.connect(self.refresh)
        self.edited: dict[str, EditedFile] = {}  # the files open in a program, by their copy on this computer
        self.fetching: set[tuple[str, int, str, str, str]] = set()  # the edits still on their way here: a second press opens no second copy
        self.edit_watcher = QFileSystemWatcher(self)
        self.edit_watcher.fileChanged.connect(self._edited_file_changed)
        self.edit_settle = QTimer(self)
        self.edit_settle.setSingleShot(True)
        self.edit_settle.setInterval(EDIT_SETTLE_MILLISECONDS)
        self.edit_settle.timeout.connect(self._send_edited_files)
        self.clock = QTimer(self)
        self.clock.setInterval(CLOCK_MILLISECONDS)
        self.clock.timeout.connect(self._show_connection_state)
        self.clock.start()
        self.check_timer = QTimer(self)
        self.check_timer.setInterval(CHECK_MILLISECONDS)
        self.check_timer.timeout.connect(self.check_connections)
        self.check_timer.start()
        self.settings = saved_settings.load()
        self._apply_settings()
        self._restore_sizes()
        self.update_actions()

    def _apply_settings(self) -> None:
        for side in (self.local_side, self.remote_side):
            side.set_tree_place(self.settings.tree_place)
            side.set_show_hidden(self.settings.show_hidden)

    def _sized_parts(self) -> dict[str, Callable[[], QByteArray]]:
        return {
            "window": self.saveGeometry,
            "sides": self.sides.saveState,
            "local_parts": self.local_side.parts.saveState,
            "remote_parts": self.remote_side.parts.saveState,
            "local_columns": self.local_side.file_list.header().saveState,
            "remote_columns": self.remote_side.file_list.header().saveState,
        }

    def _restore_sizes(self) -> None:
        restorers: dict[str, Callable[[QByteArray], bool]] = {
            "window": self.restoreGeometry,
            "sides": self.sides.restoreState,
            "local_parts": self.local_side.parts.restoreState,
            "remote_parts": self.remote_side.parts.restoreState,
            "local_columns": self.local_side.file_list.header().restoreState,
            "remote_columns": self.remote_side.file_list.header().restoreState,
        }
        state = saved_settings.load_window()
        for name, text in state.items():
            if name in restorers:
                restorers[name](QByteArray.fromBase64(text.encode("ascii", "replace")))  # a damaged value is refused by Qt and changes nothing
        height = state.get("queue_height", "")
        if height.isdecimal() and len(height) <= len(str(HIGHEST_QUEUE_HEIGHT)) and int(height) <= HIGHEST_QUEUE_HEIGHT:
            self.queue_height = max(int(height), LOWEST_HEIGHT)
        if state.get("queue_pinned") == "yes":
            self.queue_panel.pin.setChecked(True)
            self._show_queue()

    def _save_sizes(self) -> None:
        state = {name: bytes(read().toBase64().data()).decode("ascii") for name, read in self._sized_parts().items()}
        height = self.queue_panel.height() if self.queue_panel.isVisibleTo(self) else self.queue_height
        state["queue_height"] = str(height)
        state["queue_pinned"] = "yes" if self.queue_panel.pin.isChecked() else "no"
        try:
            saved_settings.save_window(state)
        except OSError:
            pass  # the sizes are a convenience: closing never fails on them

    def menu_for(self, names: tuple[str, ...]) -> QMenu:
        menu = QMenu(self)
        for name in names:
            action = self.actions_by_name[name]
            if action.isVisible():
                menu.addAction(action)
        return menu

    def _local_menu_wanted(self, place: QPoint) -> None:
        self.menu_for(LOCAL_MENU).exec(self.local_side.file_list.mapToGlobal(place))

    def _remote_menu_wanted(self, place: QPoint) -> None:
        self.menu_for(REMOTE_MENU).exec(self.remote_side.file_list.mapToGlobal(place))

    def _remote_file_opened(self, entry: RemoteEntry) -> None:
        screen = self._on_screen()
        if screen is not None:
            self._edit_entry(screen[0], screen[1], entry.name)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted.value:
            self.settings = dialog.chosen()
            self._apply_settings()
            try:
                saved_settings.save(self.settings)
            except OSError:
                self.status.showMessage(SETTINGS_NOT_KEPT_TEXT)

    def _button(self, bar: QToolBar, name: str, text: str, icon_name: str, key: str, handler: Callable[[], None], view: QWidget | None) -> None:
        action = QAction(icons.icon(icon_name), text, self)
        if key and view is not None:
            action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)  # a key acts on the list that has the focus
            view.addAction(action)
        action.triggered.connect(handler)
        bar.addAction(action)
        self.actions_by_name[name] = action

    def _build_toolbar(self) -> None:
        local, remote = self.local_side, self.remote_side
        for name, text, icon_name, key, handler in (
            ("upload", "Upload", "cloud-upload", "Ctrl+U", self.upload),
            ("local_edit", "Edit", "document-edit", "F4", self._edit_local),
            ("local_delete", "Delete", "edit-delete", "Del", self._delete_local),
            ("local_rename", "Rename", "edit-rename", "F2", self._rename_local),
            ("local_new", "New", "folder-new", "F7", self._new_local_folder),
        ):
            self._button(local.actions_bar, name, text, icon_name, key, handler, local.file_list)
        for name, text, icon_name, key, handler in (
            ("download", "Download", "cloud-download", "Ctrl+D", self.download),
            ("edit", "Edit", "document-edit", "F4", self.edit),
            ("delete", "Delete", "edit-delete", "Del", self.delete),
            ("rename", "Rename", "edit-rename", "F2", self.rename),
            ("move", "Move", "edit-cut", "", self.move_on_server),
            ("copy", "Copy", "edit-copy", "", self.copy),
            ("change", "Properties", "document-properties", "", self.change),
            ("new_folder", "New", "folder-new", "F7", self.new_folder),
        ):
            self._button(remote.actions_bar, name, text, icon_name, key, handler, remote.file_list)
        bar = self.general_bar
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        bar.setIconSize(QSize(GENERAL_ICON_SIZE, GENERAL_ICON_SIZE))
        for name, text, icon_name, key, handler in (
            ("compare", "Synchronize", "folder-sync", "", self.compare),
            ("search", "Find Files", "edit-find", "", self.search),
            ("terminal", "Terminal", "utilities-terminal", "", self.open_terminal),
            ("reconnect", "Reconnect", "network-connect", "", self.reconnect),
            ("queue", "Queue", "view-task", "", self.toggle_queue),
            ("stop", "Stop", "process-stop", "", self.stop_transfers),
            ("settings", "Settings", "configure", "", self.open_settings),
        ):
            self._button(bar, name, text, icon_name, key, handler, None)
        bar.setMinimumWidth(bar.sizeHint().width())
        refresh = QAction(icons.icon("view-refresh"), "Refresh", self)
        refresh.setShortcut(QKeySequence("F5"))
        refresh.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        remote.addAction(refresh)
        refresh.triggered.connect(self.refresh)
        self.actions_by_name["refresh"] = refresh
        local_refresh = QAction("Refresh", self)
        local_refresh.setShortcut(QKeySequence("F5"))
        local_refresh.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        local.addAction(local_refresh)
        local_refresh.triggered.connect(local.refresh)
        speed = QLabel("Speed limit")
        self.limit.setRange(0, HIGHEST_LIMIT)
        self.limit.setSpecialValueText("None")
        self.limit.setSuffix(" KB/s")
        self.limit.valueChanged.connect(self._limit_changed)
        self.protocol_label = QLabel()
        self.lock_label = QLabel()
        self.lock_label.setPixmap(icons.icon("object-locked").pixmap(LOCK_SIZE, LOCK_SIZE))
        self.lock_label.setToolTip("This connection is encrypted")
        self.lock_label.hide()
        self.time_label = QLabel()
        for widget in (speed, self.limit, self.protocol_label, self.lock_label, self.time_label):
            self.status.addPermanentWidget(widget)

    # --- tabs ---------------------------------------------------------------

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

    def _connect(self, session: Session) -> None:
        if session.connecting:
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
        if session.typed_password and session.password:
            self._store_password(session, session.password)
            session.typed_password = False
        self._set_tab(session)
        self.status.showMessage(f"Connected to {session.connection.host}")
        self.load_folder(session, folder)
        for copy in self._edits_of(session):
            if self.edited[copy].after_reconnect:
                self.edited[copy].held = False
        self.edit_settle.start()  # what was saved while the connection was gone goes now
        self.update_actions()

    def _not_connected(self, session: Session, error: Exception) -> None:
        if session not in self.sessions:
            return
        if isinstance(error, UnknownServer):
            answer = QMessageBox.question(self, WINDOW_TITLE, f"{error}\n\nServer: {session.connection.host}\nFingerprint: {error.fingerprint}\n\nDo you trust this server?")
            if answer == QMessageBox.StandardButton.Yes:
                (trust.remember_host_key if error.kind == "host key" else trust.remember_certificate)(error.to_remember)
                self._connect(session)
            return
        if isinstance(error, LoginRefused) and not session.connection.key_file:
            password, given = QInputDialog.getText(self, WINDOW_TITLE, f"Password for {session.connection.name}", QLineEdit.EchoMode.Password)
            if given and password:
                session.password = password
                session.typed_password = True
                self._connect(session)
                return
        self.status.showMessage(str(error))
        QMessageBox.information(self, WINDOW_TITLE, f"{session.connection.name}: {error}")
        self.update_actions()

    def _store_password(self, session: Session, password: str) -> None:
        try:
            credentials.store.write(session.connection.name, password)
        except CredentialError as error:
            self.status.showMessage(str(error))

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
        running = [job for job in self.queue.jobs if job.remote is session.remote and job.state in (State.WAITING, State.RUNNING)]
        open_here = self._edits_of(session)
        if running:
            text = f"{len(running)} transfers on {session.connection.name} are not finished. Stop them and close the tab?"
            if QMessageBox.question(self, WINDOW_TITLE, text) != QMessageBox.StandardButton.Yes:
                return
        if open_here and QMessageBox.question(self, WINDOW_TITLE, self._edits_text(open_here) + " Close the tab?") != QMessageBox.StandardButton.Yes:
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

    def _edits_of(self, session: Session) -> list[str]:
        return [copy for copy, edited in self.edited.items() if edited.session is session]

    def _edits_text(self, copies: list[str]) -> str:
        """What the person should know before these files lose their way back to the server."""
        known = [copy for copy in copies if copy in self.edited]
        waiting = [copy for copy in known if self.edited[copy].pending]
        others = [shown_name(self.edited[copy].name) for copy in known if not self.edited[copy].pending]
        lines: list[str] = []
        if waiting:
            lines.append(f"Saved, but not on the server yet. Your copy stays here:\n{chr(10).join(waiting)}")
        if others:
            lines.append(f"Open for editing: {', '.join(others)}. What you save after this no longer goes to the server.")
        return "\n\n".join(lines) + "\n\n"

    def _forget_edits(self, copies: list[str]) -> None:
        """A copy with changes that never reached the server stays on the disk: it may be the only one."""
        for copy in copies:
            edited = self.edited.pop(copy, None)
            self.edit_watcher.removePath(copy)
            if edited is None or not edited.pending:
                shutil.rmtree(os.path.dirname(copy), ignore_errors=True)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        running = [job for job in self.queue.jobs if job.state in (State.WAITING, State.RUNNING)]
        lines = f"{len(running)} transfers are not finished.\n\n" if running else ""
        if self.edited:
            lines += self._edits_text(list(self.edited))
        if lines and QMessageBox.question(self, WINDOW_TITLE, lines + "Close Chrono FTP?") != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._save_sizes()
        self.queue.close()
        self._forget_edits(list(self.edited))
        for session in self.sessions:
            self._let_go_of(session)
        super().closeEvent(event)

    # --- remote folders -----------------------------------------------------

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

    def _failed(self, session: Session, error: Exception) -> None:
        if session not in self.sessions:
            return
        if isinstance(error, ConnectionLost):
            self._mark_lost(session)
        text = shown_name(str(error))
        self.status.showMessage(text)
        if not isinstance(error, ChronoFtpError):
            raise error
        QMessageBox.information(self, WINDOW_TITLE, f"{session.connection.name}: {text}")

    def check_connections(self) -> None:
        for session in self.sessions:
            remote = session.remote
            if remote is not None and session.alive:

                def gone(error: Exception, session: Session = session) -> None:
                    if isinstance(error, ConnectionLost) and session in self.sessions:
                        self.status.showMessage(f"{session.connection.name}: {error}")
                        self._mark_lost(session)

                session.worker.submit(remote.check, lambda _nothing: None, gone)

    def load_folder(self, session: Session, folder: str) -> None:
        session.wanted_folder = folder
        place = (session.connection.host, session.connection.port)
        busy = any((job.connection.host, job.connection.port) == place and job.state in (State.WAITING, State.RUNNING) for job in self.queue.jobs)

        def read(remote: RemoteConnection) -> list[RemoteEntry]:
            found = remote.list_folder(folder)
            return found if busy else tidy(remote, folder, found, session.leftovers)  # a running transfer has its temporary files in use

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

    # --- actions ------------------------------------------------------------

    def update_actions(self) -> None:
        session = self.current()
        alive = session is not None and session.alive
        capabilities = session.capabilities if session is not None else Capabilities()
        selected = bool(self.remote_side.selected_entries())
        one = len(self.remote_side.selected_entries()) == 1
        local_chosen = len(self.local_side.selected_entries())
        enabled = {
            "upload": alive and bool(self.local_side.selected_paths()) and session is not None and bool(session.remote_folder),
            "download": alive and selected,
            "new_folder": alive,
            "rename": alive and one,
            "edit": alive and one and not self.remote_side.selected_entries()[0].is_folder,
            "local_new": True,
            "local_rename": local_chosen == 1,
            "local_delete": local_chosen > 0,
            "local_edit": local_chosen == 1 and not self.local_side.selected_entries()[0].is_folder,
            "queue": True,
            "compare": alive and session is not None and bool(session.remote_folder),
            "move": alive and selected,
            "copy": alive and one and commands.can_copy(capabilities),
            "delete": alive and selected,
            # A selected link is left out: a recursive change would go through it into whatever it points at.
            "change": alive and selected and not any(entry.is_link for entry in self.remote_side.selected_entries()) and (commands.can_change_mode(capabilities) or commands.can_change_owner(capabilities)),
            "search": alive and commands.can_search(capabilities),
            "terminal": alive and capabilities.shell and terminal_available(),
            "refresh": alive,
            "reconnect": session is not None and not session.alive and not session.connecting,
            "settings": True,
            "stop": bool(self.queue_view.selected_jobs()) or any(job.state in (State.WAITING, State.RUNNING) for job in self.queue.jobs),
        }
        on_ftp = session is not None and session.connection.protocol == FTP
        for name, action in self.actions_by_name.items():
            action.setEnabled(enabled[name])
            action.setVisible(not (on_ftp and name in SHELL_ACTIONS))  # D-027

    def _then_refresh(self, session: Session) -> Callable[[object], None]:
        def done(_result: object) -> None:
            self._reload(session)

        return done

    def _on_screen(self) -> tuple[Session, str, list[RemoteEntry]] | None:
        """The tab, folder and selection as the person sees them now. An action reads them first, before any window opens, so what it does
        is what was on the screen when the person chose it, whatever arrives in the meantime."""
        session = self.current()
        if session is None or not self.remote_side.folder:
            return None
        return session, self.remote_side.folder, self.remote_side.selected_entries()

    def _ask_text(self, title: str, label: str, start: str = "") -> str | None:
        text, given = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, start)
        return text.strip() if given and text.strip() else None

    def _on_this_computer(self, work: Callable[[], None]) -> None:
        try:
            work()
        except LocalActionFailed as error:
            QMessageBox.information(self, WINDOW_TITLE, str(error))
        self.local_side.refresh()

    def _open_local_file(self, path: str) -> None:
        if shown_name(path) != path:  # Qt would change the name, and open another file
            QMessageBox.information(self, WINDOW_TITLE, NOT_OPENABLE_TEXT)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _edit_local(self) -> None:
        paths = self.local_side.selected_paths()
        if len(paths) == 1:
            self._open_local_file(paths[0])

    def edit(self) -> None:
        """A copy goes to a temporary folder and opens in the person's own program; every save goes back to the server."""
        screen = self._on_screen()
        if screen is not None and len(screen[2]) == 1 and not screen[2][0].is_folder:
            self._edit_entry(screen[0], screen[1], screen[2][0].name)

    def _edit_entry(self, session: Session, folder: str, name: str) -> None:
        if shown_name(name) != name:
            QMessageBox.information(self, WINDOW_TITLE, NOT_OPENABLE_TEXT)
            return
        already = next((copy for copy, edited in self.edited.items() if _same_server(edited.session, session) and edited.folder == folder and edited.name == name), None)
        if already is not None and self.edited[already].pending:
            self._open_local_file(already)  # its changes are not on the server yet: a fresh copy would lose them
            return
        place = (*_login_of(session), folder, name)
        if not session.alive or place in self.fetching:
            return
        self.fetching.add(place)
        saves_before = self.edited[already].saves if already is not None else 0
        temporary = tempfile.mkdtemp(prefix="chrono-ftp-edit-")
        copy = os.path.join(temporary, name)

        def fetch(connection: RemoteConnection) -> None:
            connection.download(joined(folder, name), copy, _no_progress)

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
                edited.job = self._add_job(edited.session, copy, edited.folder, True, edited.name)
                self.status.showMessage(f"Sending {shown_name(edited.name)} back to the server")
            else:
                self.status.showMessage(f"{shown_name(edited.name)} goes to the server when the connection is back")

    def _ask_mask(self, view: FileList) -> None:
        mask, given = QInputDialog.getText(self, "Filter", "Show only names like (for example *.txt); empty shows all", QLineEdit.EchoMode.Normal, view.name_mask)
        if given:
            view.set_name_mask(mask.strip())
            session = self.current()
            if session is not None and view is self.remote_side.file_list:
                session.name_mask = view.name_mask

    def toggle_queue(self) -> None:
        if self.queue_panel.isVisibleTo(self):
            self.failure_unseen = False
            self.queue_panel.pin.setChecked(False)  # closing it by hand also lets go of the pin
            self.queue_closed = self._transfers_running()
            self._hide_queue()
        else:
            self.queue_closed = False
            self._show_queue()

    def _show_queue(self) -> None:
        self.queue_linger.stop()
        if not self.queue_panel.isVisibleTo(self):
            self.queue_panel.show()
            total = sum(self.body.sizes())
            self.body.setSizes([max(total - self.queue_height, 1), self.queue_height])

    def _hide_queue(self) -> None:
        if self.queue_panel.isVisibleTo(self):
            self.queue_height = self.queue_panel.height() or self.queue_height
            self.queue_panel.hide()

    def _transfers_running(self) -> bool:
        return any(job.state in (State.WAITING, State.RUNNING) for job in self.queue.jobs)

    def _hide_idle_queue(self) -> None:
        if not (self.queue_panel.pin.isChecked() or self.failure_unseen or self._transfers_running()):
            self._hide_queue()

    def _pin_toggled(self, pinned: bool) -> None:
        if pinned:
            self.queue_linger.stop()
        elif not self.failure_unseen and not self._transfers_running():
            self.queue_linger.start()

    def _follow_queue(self, job: Job) -> None:
        """The queue shows while something runs, and for a moment after, so the result can be seen."""
        if job.state == State.FAILED:
            self.failure_unseen = True
        running = self._transfers_running()
        if not running:
            self.queue_closed = False
        if job.state == State.FAILED or (running and not self.queue_closed):
            self._show_queue()
        elif not running and not self.queue_panel.pin.isChecked() and not self.failure_unseen:
            self.queue_linger.start()

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

    def _new_local_folder(self) -> None:
        folder = self.local_side.folder
        name = self._ask_text("New folder", "Name of the new folder")
        if name:
            self._on_this_computer(lambda: localfiles.make_folder(folder, name))

    def _rename_local(self) -> None:
        chosen = self.local_side.selected_entries()
        if len(chosen) != 1:
            return
        folder, old = self.local_side.folder, chosen[0].name
        name = self._ask_text("Rename", "New name", shown_name(old))
        if name and name != shown_name(old).strip():
            self._on_this_computer(lambda: localfiles.rename(folder, old, name))

    def _delete_local(self) -> None:
        paths = self.local_side.selected_paths()
        if not paths:
            return
        names = ", ".join(shown_name(Path(path).name) for path in paths)
        if QMessageBox.question(self, "Delete", f"Move to the Trash from {shown_name(self.local_side.folder)}: {names}?") != QMessageBox.StandardButton.Yes:
            return
        left = [shown_name(Path(path).name) for path in paths if not cast(object, QFile.moveToTrash(path)) is True]  # a plain bool, whatever the stub says
        if left:
            QMessageBox.information(self, "Delete", f"These could not go to the Trash and are still here: {', '.join(left)}")
        self.local_side.refresh()

    def new_folder(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        name = self._ask_text("New folder", "Name of the new folder")
        if name and not safe_name(name):
            QMessageBox.information(self, "New folder", NOT_A_NAME_TEXT)
        elif name:
            path = joined(folder, name)
            self._ask(session, lambda remote: remote.make_folder(path), self._then_refresh(session))

    def rename(self) -> None:
        screen = self._on_screen()
        if screen is None or len(screen[2]) != 1:
            return
        session, folder, chosen = screen
        name = self._ask_text("Rename", "New name", shown_name(chosen[0].name))
        if name == shown_name(chosen[0].name).strip():
            return
        if name and not safe_name(name):
            QMessageBox.information(self, "Rename", NOT_A_NAME_TEXT)
            return
        if name and name in {entry.name for entry in session.entries}:
            QMessageBox.information(self, "Rename", commands.TARGET_EXISTS_TEXT)
            return
        if name:
            old, new = joined(folder, chosen[0].name), joined(folder, name)
            self._ask(session, lambda remote: remote.rename(old, new), self._gone_and_refresh(session, folder, [chosen[0].name]))

    def move_on_server(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        target = self._ask_text("Move", "Folder on the server to move to", shown_name(folder))
        if target:
            self._move_into(session, folder, chosen, target)

    def _stop_editing(self, session: Session, folder: str, names: list[str]) -> None:
        """A file that is renamed, moved or deleted here is no longer where its saves would go: they must not make it again."""
        paths = [joined(folder, name) for name in names]
        gone = [copy for copy, edited in self.edited.items() if _same_server(edited.session, session) and any(_is_at_or_below(joined(edited.folder, edited.name), path) for path in paths)]
        if gone:
            for copy in gone:
                job = self.edited[copy].job
                if job is not None:
                    self.queue.stop(job)
            text = self._edits_text(gone)
            self._forget_edits(gone)
            QMessageBox.information(self, WINDOW_TITLE, f"This file changed its place on the server, so saving no longer sends it there.\n\n{text}".strip())

    def _gone_and_refresh(self, session: Session, folder: str, names: list[str]) -> Callable[[object], None]:
        """Only once the server agreed: a refused rename leaves the file where it is, and its edit goes on."""

        def done(_result: object) -> None:
            self._stop_editing(session, folder, names)
            self._reload(session)

        return done

    def _move_into(self, session: Session, folder: str, chosen: list[RemoteEntry], target: str) -> None:
        paths = [joined(folder, entry.name) for entry in chosen]
        finished: list[str] = []  # a refusal halfway: what went before it has moved all the same

        def work(remote: RemoteConnection) -> None:
            for path in paths:
                destination = joined(target, Path(path).name)
                if remote.capabilities.has("mv"):
                    commands.run_checked(remote, commands.without_overwriting(destination, commands.move_command(path, destination)))
                elif Path(path).name in {entry.name for entry in remote.list_folder(target)}:
                    raise RemoteActionFailed(commands.TARGET_EXISTS_TEXT)
                else:
                    remote.rename(path, destination)
                finished.append(Path(path).name)

        self._ask(session, work, self._gone_and_refresh(session, folder, finished), refresh_after_failure=True, on_failure=lambda: self._stop_editing(session, folder, finished))

    def copy(self) -> None:
        screen = self._on_screen()
        if screen is None or len(screen[2]) != 1:
            return
        session, folder, chosen = screen
        source = joined(folder, chosen[0].name)
        target = self._ask_text("Copy", "Copy on the server to", f"{shown_name(source)}-copy")
        if target:
            line = commands.without_overwriting(target, commands.copy_command(source, target))
            self._ask(session, lambda remote: commands.run_checked(remote, line), self._then_refresh(session), refresh_after_failure=True)

    def delete(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        names = ", ".join(shown_name(entry.name) for entry in chosen)
        if QMessageBox.question(self, "Delete", f"Delete in {shown_name(folder)} on the server: {names}?") != QMessageBox.StandardButton.Yes:
            return

        finished: list[str] = []  # a refusal halfway: what went before it is gone all the same

        def work(remote: RemoteConnection) -> None:
            for entry in chosen:
                remote.delete(joined(folder, entry.name), entry.is_folder and not entry.is_link)
                finished.append(entry.name)

        self._ask(session, work, self._gone_and_refresh(session, folder, finished), refresh_after_failure=True, on_failure=lambda: self._stop_editing(session, folder, finished))

    def change(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        paths = [joined(folder, entry.name) for entry in chosen]
        dialog = ChangeDialog(commands.can_change_owner(session.capabilities), self)
        if dialog.exec() != ChangeDialog.DialogCode.Accepted.value:
            return
        lines = dialog.commands_for(paths)
        text = "This will run on the server:\n\n" + "\n".join(shown_name(line) for line in lines) + "\n\nGo ahead?"  # OR-6: the exact command, first
        if QMessageBox.question(self, "Owner and permissions", text) != QMessageBox.StandardButton.Yes:
            return

        def work(remote: RemoteConnection) -> None:
            for line in lines:
                commands.run_checked(remote, line)

        self.status.showMessage("Running on the server")
        self._ask(session, work, self._then_refresh(session), refresh_after_failure=True)

    def search(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        dialog = SearchDialog(folder, self)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)  # it belongs to this tab: no other tab can come up meanwhile

        def find(pattern: str) -> None:
            remote = session.remote
            if remote is None:
                dialog.show_failure("The connection is closed.")
                return

            def show(found: list[str]) -> None:
                dialog.show_results(found)

            def failed(error: Exception) -> None:
                dialog.show_failure(str(error))

            session.worker.submit(lambda: commands.search(remote, folder, pattern), show, failed)

        def opened(path: str) -> None:
            self.load_folder(session, parent_of(path))
            dialog.close()

        dialog.search_requested.connect(find)
        dialog.result_opened.connect(opened)
        dialog.show()

    def compare(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        local_folder = self.local_side.folder
        dialog = CompareDialog(local_folder, folder, self)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)  # it belongs to this tab: no other tab can come up meanwhile
        remote = session.remote
        if remote is None:
            return

        def show(found: list[Found]) -> None:
            dialog.show_found(found)

        def failed(error: Exception) -> None:
            dialog.show_failure(shown_name(str(error)))

        def copy(rows: list[Found]) -> None:
            for row in rows:
                here = os.path.join(local_folder, row.folder) if row.folder else local_folder
                there = joined(folder, row.folder) if row.folder else folder
                if uploads(row):
                    self._add_job(session, os.path.join(here, row.name), there, True, row.name)
                else:
                    self._add_job(session, here, joined(there, row.name), False, row.name)

        dialog.copy_requested.connect(copy)
        session.worker.submit(lambda: compare_folders(remote, local_folder, folder), show, failed)
        dialog.show()

    def open_terminal(self) -> None:
        session = self.current()
        if session is not None:
            subprocess.Popen(terminal_arguments(session.connection, session.remote_folder), start_new_session=True)

    # --- transfers ----------------------------------------------------------

    def _limit_changed(self, value: int) -> None:
        self.queue.limit = value

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
        name = shown_name(edited.name)
        self.status.showMessage(f"{name} is not on the server yet")
        copy = next(path for path, item in self.edited.items() if item is edited)
        text = f"{name} could not go to the server: {shown_name(job.message)}\n\nYour changes are kept here:\n{copy}\n\nTry again now?"
        if QMessageBox.question(self, WINDOW_TITLE, text) == QMessageBox.StandardButton.Yes:
            edited.held = False
            self._send_edited_files()

    def _job_changed(self, job: Job) -> None:
        self.queue_view.show_job(job)
        self.update_actions()
        self._follow_queue(job)
        self._edit_job_ended(job)
        if job.state == State.DONE and job.upload:
            for session in self.sessions:
                if session.remote is job.remote and session.remote_folder == job.remote_path:
                    self._reload(session)
