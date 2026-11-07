"""The main window of Chrono FTP, put together from its parts."""

from collections.abc import Callable
from typing import override

from PySide6.QtCore import QFileSystemWatcher, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMessageBox, QSizePolicy, QSpinBox, QSplitter, QToolBar, QToolButton, QVBoxLayout, QWidget

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections.base import Capabilities
from chrono_ftp.core.connection import FTP
from chrono_ftp.engine import commands
from chrono_ftp.engine.terminal import terminal_available
from chrono_ftp.transfers.queue import State, TransferQueue
from chrono_ftp.ui import icons
from chrono_ftp.ui.bridge import Bridge
from chrono_ftp.ui.localside import LocalSide
from chrono_ftp.ui.queueview import QueuePanel, QueueView
from chrono_ftp.ui.queueview import HEIGHT as QUEUE_HEIGHT
from chrono_ftp.ui.remoteside import RemoteSide
from chrono_ftp.ui.tabbar import ConnectionTabBar
from chrono_ftp.ui.windowbase import CENTRE_MARGIN, CHECK_MILLISECONDS, CLOCK_MILLISECONDS, EDIT_SETTLE_MILLISECONDS, EMPTY_TAB_BAR_LINES, GENERAL_ICON_SIZE, HIGHEST_LIMIT, LOCK_SIZE, QUEUE_LINGER_MILLISECONDS, SHELL_ACTIONS, START_HEIGHT, START_WIDTH, WINDOW_TITLE
from chrono_ftp.ui.windowedit import EditPart
from chrono_ftp.ui.windowfolders import FoldersPart
from chrono_ftp.ui.windowlayout import LayoutPart
from chrono_ftp.ui.windowlocal import LocalPart
from chrono_ftp.ui.windowqueue import QueuePart
from chrono_ftp.ui.windowserver import ServerPart
from chrono_ftp.ui.windowtabs import TabsPart
from chrono_ftp.ui.windowtransfers import TransfersPart

# --- Version ------------------------------------------------------------
__version__ = "mainwindow.py 0.0.11"

# --- Version history ----------------------------------------------------
# v0.0.11: Split: each group of actions moved, unchanged, into a part module of its own; this file puts them together (D-041, OR-8); closing closes the bridge, so a late answer goes nowhere
# v0.0.8: One login window with user and password; no login goes out without a user name (D-040)
# v0.0.7: One top row; the queue under a separator, hidden when idle unless pinned, open after a failure
# v0.0.6: Drag and drop, right-click menus, keys, actions on the side with the focus, also on this computer; sizes remembered; leftovers tidied
# v0.0.5: Tabs, actions and transfers: a tab is a connection with its own local and remote folder (D-008, D-027)
# v0.0.3: Initial: the tab bar above both sides, local on the left, remote on the right (OR-1, D-008)


class MainWindow(LayoutPart, TabsPart, FoldersPart, EditPart, QueuePart, LocalPart, ServerPart, TransfersPart):
    """The window: this computer on the left, the server on the right (OR-1), built from one part per group of actions (D-041)."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(START_WIDTH, START_HEIGHT)
        self.bridge = Bridge(self)
        self.sessions = []
        self.shown = None
        self.queue = TransferQueue(lambda job: self.bridge.deliver(lambda: self._job_changed(job)))
        self.connection_list = None
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
        self.queue_height = QUEUE_HEIGHT
        self.failure_unseen = False  # a failed transfer keeps the queue open until the person closes it
        self.queue_closed = False  # closed by hand during these transfers: it stays closed until they end, unless one fails
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
        self.actions_by_name = {}
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
        self.edited = {}  # the files open in a program, by their copy on this computer
        self.fetching = set()  # the edits still on their way here: a second press opens no second copy
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

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        running = [job for job in self.queue.jobs if job.state in (State.WAITING, State.RUNNING)]
        lines = f"{len(running)} transfers are not finished.\n\n" if running else ""
        if self.edited:
            lines += self._edits_text(list(self.edited))
        if lines and QMessageBox.question(self, WINDOW_TITLE, lines + "Close Chrono FTP?") != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self.bridge.close()  # from here on nothing happens in this window: what its threads still answer goes nowhere
        self._save_sizes()
        self.queue.close()
        self._forget_edits(list(self.edited))
        for session in self.sessions:
            self._let_go_of(session)
        super().closeEvent(event)

    @override
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
