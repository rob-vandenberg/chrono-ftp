import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import override

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QSpinBox, QSplitter, QToolBar, QToolButton, QVBoxLayout, QWidget

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import Capabilities, RemoteConnection, RemoteEntry, UnknownServer, joined, parent_of
from chrono_ftp.connections.factory import remote_connection
from chrono_ftp.core import credentials
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, CredentialError, LoginRefused, RemoteActionFailed
from chrono_ftp.engine import commands
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.engine.terminal import terminal_arguments, terminal_available
from chrono_ftp.transfers.queue import Job, State, TransferQueue
from chrono_ftp.ui import icons
from chrono_ftp.ui.bridge import Bridge
from chrono_ftp.ui.changedialog import ChangeDialog
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.localside import LocalSide
from chrono_ftp.ui.queueview import QueueView
from chrono_ftp.ui.remoteside import RemoteSide
from chrono_ftp.ui.searchdialog import SearchDialog
from chrono_ftp.ui.settingsdialog import SettingsDialog
from chrono_ftp.ui.tabbar import ConnectionTabBar

# --- Version ------------------------------------------------------------
__version__ = "mainwindow.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Tabs, actions and transfers: a tab is a connection with its own local and remote folder (D-008, D-027)
# v0.0.3: Initial: the tab bar above both sides, local on the left, remote on the right (OR-1, D-008)

WINDOW_TITLE: str = "Chrono FTP"
START_WIDTH: int = 1280
START_HEIGHT: int = 800
EMPTY_TAB_BAR_LINES: int = 2  # an empty tab bar has no height of its own: it keeps the height of a tab
HIGHEST_LIMIT: int = 1_000_000
CHECK_MILLISECONDS: int = 30_000  # how often an idle connection is checked, so a dropped one goes grey (OR-3)
SHELL_ACTIONS: tuple[str, ...] = ("copy", "search", "change", "terminal")  # hidden on FTP, greyed on SFTP without a shell (D-027)


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

    @property
    def capabilities(self) -> Capabilities:
        return self.remote.capabilities if self.remote is not None and self.alive else Capabilities()


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
        self.local_side = LocalSide()
        self.remote_side = RemoteSide()
        self.sides = QSplitter(Qt.Orientation.Horizontal)
        self.sides.setChildrenCollapsible(False)
        self.sides.addWidget(self.local_side)  # always left, never swapped (OR-1)
        self.sides.addWidget(self.remote_side)
        self.sides.setStretchFactor(0, 1)
        self.sides.setStretchFactor(1, 1)
        self.queue_view = QueueView()
        centre = QWidget()
        layout = QVBoxLayout(centre)
        layout.addWidget(self.top_row)
        layout.addWidget(self.sides, 1)
        layout.addWidget(self.queue_view)
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
        self.check_timer = QTimer(self)
        self.check_timer.setInterval(CHECK_MILLISECONDS)
        self.check_timer.timeout.connect(self.check_connections)
        self.check_timer.start()
        self.settings = saved_settings.load()
        self._apply_settings()
        self.update_actions()

    def _apply_settings(self) -> None:
        self.local_side.set_tree_place(self.settings.tree_place)
        self.remote_side.set_tree_place(self.settings.tree_place)

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted.value:
            self.settings = dialog.chosen()
            saved_settings.save(self.settings)
            self._apply_settings()

    def _build_toolbar(self) -> None:
        bar = QToolBar()
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(bar)
        entries: tuple[tuple[str, str, str, str, Callable[[], None]], ...] = (
            ("upload", "Upload", "cloud-upload", "", self.upload),
            ("download", "Download", "cloud-download", "", self.download),
            ("new_folder", "New", "folder-new", "", self.new_folder),
            ("rename", "Rename", "edit-rename", "F2", self.rename),
            ("move", "Move", "edit-cut", "", self.move_on_server),
            ("copy", "Copy", "edit-copy", "", self.copy),
            ("delete", "Delete", "edit-delete", "Del", self.delete),
            ("change", "Properties", "document-properties", "", self.change),
            ("search", "Find Files", "edit-find", "", self.search),
            ("terminal", "Terminal", "utilities-terminal", "", self.open_terminal),
            ("refresh", "Refresh", "view-refresh", "F5", self.refresh),
            ("reconnect", "Reconnect", "network-connect", "", self.reconnect),
            ("stop", "Stop", "process-stop", "", self.stop_transfers),
            ("settings", "Settings", "configure", "", self.open_settings),
        )
        for name, text, icon_name, key, handler in entries:
            action = QAction(icons.icon(icon_name), text, self)
            if key:
                action.setShortcut(QKeySequence(key))
                action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)  # acts on the part with the focus (OR-21)
                (self.remote_side if name == "refresh" else self.remote_side.file_list).addAction(action)
            action.triggered.connect(handler)
            bar.addAction(action)
            self.actions_by_name[name] = action
        speed = QLabel("Speed limit")
        self.limit.setRange(0, HIGHEST_LIMIT)
        self.limit.setSpecialValueText("None")
        self.limit.setSuffix(" KB/s")
        self.limit.valueChanged.connect(self._limit_changed)
        self.status.addPermanentWidget(speed)
        self.status.addPermanentWidget(self.limit)

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
            start = session.remote_folder or session.connection.start_folder
            if start:
                try:
                    remote.list_folder(start)
                    return start
                except ConnectionLost:
                    raise
                except RemoteActionFailed:
                    pass  # a starting folder that is gone: the home folder is always there
            return remote.home()

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
        if session.typed_password and session.password:
            self._store_password(session, session.password)
            session.typed_password = False
        self._set_tab(session)
        self.status.showMessage(f"Connected to {session.connection.host}")
        self.load_folder(session, folder)
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
        self.local_side.go_to(session.local_folder)
        self.remote_side.clear()
        self.remote_side.set_colour(session.connection.colour if session.alive else None)
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
        if running:
            text = f"{len(running)} transfers on {session.connection.name} are not finished. Stop them and close the tab?"
            if QMessageBox.question(self, WINDOW_TITLE, text) != QMessageBox.StandardButton.Yes:
                return
            for job in running:
                self.queue.stop(job)
        self.sessions.pop(index)
        session.alive = False
        self.tab_bar.removeTab(index)
        self._let_go_of(session)

    def _let_go_of(self, session: Session) -> None:
        remote = session.remote
        session.worker.finish_with(remote.close if remote is not None else lambda: None)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self.queue.close()
        for session in self.sessions:
            self._let_go_of(session)
        super().closeEvent(event)

    # --- remote folders -----------------------------------------------------

    def _ask(self, session: Session, work: Callable[[RemoteConnection], object], done: Callable[[object], None], refresh_after_failure: bool = False) -> None:
        remote = session.remote
        if remote is None or not session.alive:
            return

        def failed(error: Exception) -> None:
            self._failed(session, error)
            if refresh_after_failure and session.alive:
                self.load_folder(session, session.remote_folder)  # part of it may have been done

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
        self.status.showMessage(str(error))
        if not isinstance(error, ChronoFtpError):
            raise error
        QMessageBox.information(self, WINDOW_TITLE, f"{session.connection.name}: {error}")

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

        def show(found: object) -> None:
            assert isinstance(found, list)
            if folder != session.wanted_folder:
                return  # the person has moved on
            session.remote_folder = folder
            session.entries = tuple(found)
            if session is self.current():
                self.remote_side.show_folder(folder, list(session.entries))
                self.update_actions()

        self._ask(session, lambda remote: remote.list_folder(folder), show)

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
                self.remote_side.fill_tree(folder, found)

        self._ask(session, lambda remote: remote.list_folder(folder), show)

    def refresh(self) -> None:
        session = self.current()
        if session is not None and session.remote_folder:
            self.load_folder(session, session.wanted_folder or session.remote_folder)

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
        enabled = {
            "upload": alive and bool(self.local_side.selected_paths()) and session is not None and bool(session.remote_folder),
            "download": alive and selected,
            "new_folder": alive,
            "rename": alive and one,
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
            self.load_folder(session, session.remote_folder)

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

    def new_folder(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        name = self._ask_text("New folder", "Name of the new folder")
        if name:
            path = joined(folder, name)
            self._ask(session, lambda remote: remote.make_folder(path), self._then_refresh(session))

    def rename(self) -> None:
        screen = self._on_screen()
        if screen is None or len(screen[2]) != 1:
            return
        session, folder, chosen = screen
        name = self._ask_text("Rename", "New name", chosen[0].name)
        if name and name in {entry.name for entry in session.entries}:
            QMessageBox.information(self, "Rename", commands.TARGET_EXISTS_TEXT)
            return
        if name and name != chosen[0].name:
            old, new = joined(folder, chosen[0].name), joined(folder, name)
            self._ask(session, lambda remote: remote.rename(old, new), self._then_refresh(session))

    def move_on_server(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        target = self._ask_text("Move", "Folder on the server to move to", folder)
        if not target:
            return
        paths = [joined(folder, entry.name) for entry in chosen]

        def work(remote: RemoteConnection) -> None:
            for path in paths:
                destination = joined(target, Path(path).name)
                if remote.capabilities.has("mv"):
                    commands.run_checked(remote, commands.without_overwriting(destination, commands.move_command(path, destination)))
                elif Path(path).name in {entry.name for entry in remote.list_folder(target)}:
                    raise RemoteActionFailed(commands.TARGET_EXISTS_TEXT)
                else:
                    remote.rename(path, destination)

        self._ask(session, work, self._then_refresh(session), refresh_after_failure=True)

    def copy(self) -> None:
        screen = self._on_screen()
        if screen is None or len(screen[2]) != 1:
            return
        session, folder, chosen = screen
        source = joined(folder, chosen[0].name)
        target = self._ask_text("Copy", "Copy on the server to", f"{source}-copy")
        if target:
            line = commands.without_overwriting(target, commands.copy_command(source, target))
            self._ask(session, lambda remote: commands.run_checked(remote, line), self._then_refresh(session), refresh_after_failure=True)

    def delete(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        names = ", ".join(entry.name for entry in chosen)
        if QMessageBox.question(self, "Delete", f"Delete in {folder} on the server: {names}?") != QMessageBox.StandardButton.Yes:
            return

        def work(remote: RemoteConnection) -> None:
            for entry in chosen:
                remote.delete(joined(folder, entry.name), entry.is_folder and not entry.is_link)

        self._ask(session, work, self._then_refresh(session), refresh_after_failure=True)

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
        text = "This will run on the server:\n\n" + "\n".join(lines) + "\n\nGo ahead?"  # OR-6: the exact command, first
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

    def open_terminal(self) -> None:
        session = self.current()
        if session is not None:
            subprocess.Popen(terminal_arguments(session.connection, session.remote_folder), start_new_session=True)

    # --- transfers ----------------------------------------------------------

    def _limit_changed(self, value: int) -> None:
        self.queue.limit = value

    def _add_job(self, session: Session, local: str, remote_path: str, upload: bool, name: str) -> None:
        remote = session.remote
        if remote is not None:
            self.queue.add(Job(remote, session.connection, local, remote_path, upload, name, has_password=bool(session.password)))

    def upload(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        paths = self.local_side.selected_paths()
        taken = {entry.name for entry in screen[0].entries}
        for path in self._without_refused(paths, lambda path: Path(path).name in taken):
            self._add_job(screen[0], path, screen[1], True, Path(path).name)

    def _without_refused(self, items: list[str], exists: Callable[[str], bool]) -> list[str]:
        """Nothing is replaced without asking: the person sees what is there already."""
        present = [Path(item).name for item in items if exists(item)]
        if not present:
            return items
        if QMessageBox.question(self, WINDOW_TITLE, f"Already there: {', '.join(present)}\n\nReplace?") == QMessageBox.StandardButton.Yes:
            return items
        return [item for item in items if not exists(item)]

    def download(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        target = self.local_side.folder
        names = [entry.name for entry in screen[2]]
        for name in self._without_refused(names, lambda name: Path(target, name).exists()):
            self._add_job(screen[0], target, joined(screen[1], name), False, name)

    def stop_transfers(self) -> None:
        chosen = self.queue_view.selected_jobs() or [job for job in self.queue.jobs if job.state in (State.WAITING, State.RUNNING)]
        for job in chosen:
            self.queue.stop(job)

    def _job_changed(self, job: Job) -> None:
        self.queue_view.show_job(job)
        self.update_actions()
        if job.state == State.DONE and job.upload:
            for session in self.sessions:
                if session.remote is job.remote and session.remote_folder == job.remote_path:
                    self.load_folder(session, session.remote_folder)
