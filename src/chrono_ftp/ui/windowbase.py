"""The base of the main window's parts: the shared widgets, state and numbers, and the methods the parts call in each other (D-041)."""

from collections.abc import Callable

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QSpinBox, QSplitter, QStatusBar, QToolBar, QToolButton, QWidget

from chrono_ftp.config.bookmarks import Places
from chrono_ftp.config.settings import Settings
from chrono_ftp.connections.base import RemoteConnection, RemoteEntry
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.transfers.options import Answers
from chrono_ftp.transfers.queue import Job, TransferQueue
from chrono_ftp.ui.afterqueue import Countdown, Power
from chrono_ftp.ui.bridge import Bridge
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.localside import LocalSide
from chrono_ftp.ui.queueview import QueuePanel, QueueView
from chrono_ftp.ui.remoteside import RemoteSide
from chrono_ftp.ui.session import EditedFile, Session
from chrono_ftp.ui.tabbar import ConnectionTabBar
from chrono_ftp.ui.plaintext import box_text

# --- Version ------------------------------------------------------------
__version__ = "windowbase.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Block FM: Properties, New File, Link and the file manager on the left; Properties, Duplicate, Copy to, New File and Link on
#          the right; copy on the server is no longer an action only for a shell (D-061); a thread for long work on this computer; the
#          edit of a server file is shared, so a new file opens in it; whether Properties is being prepared, on each side
# v0.0.34: Navigation (block FH): the bookmarks and profiles, the side with the focus, the bookmark keys; load_folder can hand a
#          failure to the one who asked, and tell once the folder shows; the steps of each side, the last folders, a saved connection
#          and an ended or deleted connection go to the navigation part
# v0.0.24: The row of the tabs, not the tab bar, keeps the height of two lines
# v0.0.16: The name under which the splitter of the connections window is kept
# v0.0.15: The name under which the size and place of the connections window are kept
# v0.0.13: The power of the computer and the countdown after the queue (FC-04); the queue's own buttons and settings are shared;
#          whether closing was chosen as the step after the queue; the tab a job belongs to
# v0.0.12: _add_job takes the options of a transfer and the answers its action shares; _failed is shared; the jobs whose end was shown;
#          a box for a connection lost meanwhile, or a tab closed meanwhile; the paths of unsent copies; those boxes are true for
#          downloads too
# v0.0.11: Initial: the shared part of mainwindow.py, so each group of actions can live in a module of its own (D-041)

WINDOW_TITLE: str = "Chrono FTP"
LOST_MEANWHILE_TEXT: str = "The connection was lost meanwhile, so nothing was transferred. Reconnect and try again."  # uploads and downloads
TAB_CLOSED_MEANWHILE_TEXT: str = "Its tab was closed meanwhile, so nothing was transferred."
START_WIDTH: int = 1280
START_HEIGHT: int = 800
EMPTY_TAB_BAR_LINES: int = 2  # the row of the tabs keeps this height in lines, with or without tabs
HIGHEST_LIMIT: int = 1_000_000
CHECK_MILLISECONDS: int = 30_000  # how often an idle connection is checked, so a dropped one goes grey (OR-3)
SHELL_ACTIONS: tuple[str, ...] = ("search", "terminal")  # hidden on FTP, greyed on SFTP without a shell (D-027)
LOCAL_MENU: tuple[str, ...] = ("upload", "local_edit", "local_rename", "local_delete", "local_new", "local_new_file", "local_link", "local_properties", "local_explore")
REMOTE_MENU: tuple[str, ...] = ("download", "edit", "rename", "move", "duplicate", "copy_to", "delete", "new_folder", "new_file", "link", "properties")
CLOCK_MILLISECONDS: int = 1000
LOCK_SIZE: int = 16
CENTRE_MARGIN: int = 2
GENERAL_ICON_SIZE: int = 18
QUEUE_LINGER_MILLISECONDS: int = 5000  # the queue stays this long after the last transfer, so the result can be seen
HIGHEST_QUEUE_HEIGHT: int = 4000  # a number from a file is input
CONNECTION_LIST_SIZE: str = "connection_list"  # the size and place of the connections window in the window file
CONNECTION_LIST_PARTS: str = "connection_list_parts"  # the splitter between its list and its fields
EDIT_SETTLE_MILLISECONDS: int = 500  # a program saves in steps: the copy is sent when it has settled
EDIT_WAIT_TRIES: int = 20  # a copy that stays away this often was removed
SETTINGS_NOT_KEPT_TEXT: str = "These settings apply now, but could not be kept for next time."
NOT_A_NAME_TEXT: str = "A name can't hold a slash."
NOT_OPENABLE_TEXT: str = "This name can't be handed to another program. You can still download, rename or delete it."


class WindowBase(QMainWindow):
    """What every part of the main window shares: its widgets and state, made in MainWindow.__init__, and the methods one part calls in another.
    Each declared method is defined by exactly one part; MainWindow puts the parts before this class, so the part's method is the one that runs."""

    bridge: Bridge
    sessions: list[Session]
    shown: Session | None
    queue: TransferQueue
    connection_list: ConnectionList | None
    saved_sizes: dict[str, str]  # the window file as it was read at the start
    tab_bar: ConnectionTabBar
    new_tab_button: QToolButton
    top_row: QWidget
    general_bar: QToolBar
    local_side: LocalSide
    remote_side: RemoteSide
    sides: QSplitter
    queue_view: QueueView
    queue_panel: QueuePanel
    queue_height: int
    failure_unseen: bool
    ends_shown: set[int]  # the jobs (by id) whose end was taken in: each end refreshes the screen once
    queue_closed: bool
    body: QSplitter
    queue_linger: QTimer
    status: QStatusBar
    actions_by_name: dict[str, QAction]
    limit: QSpinBox
    edited: dict[str, EditedFile]
    fetching: set[tuple[str, int, str, str, str]]
    edit_watcher: QFileSystemWatcher
    edit_settle: QTimer
    clock: QTimer
    check_timer: QTimer
    settings: Settings
    protocol_label: QLabel
    lock_label: QLabel
    time_label: QLabel
    power: Power  # suspend and shut down, through logind (FC-04)
    countdown: Countdown | None  # the countdown before the step after the queue, while it runs
    when_done_label: QLabel  # the mark in the status bar while a step is chosen for when the queue is done
    closing_when_done: bool  # the person chose to close when the queue is done: closing asks nothing, as nothing is left unsent
    places: Places  # the bookmarks, location profiles and typed paths, as kept in bookmarks.json (FH-02, FH-03)
    focused_side: str  # LOCAL or REMOTE of config.bookmarks: the side that had the keyboard focus last; its path is highlighted (FH-01)
    quiet_local: bool  # the left side is set by the program itself: its move is no step for synchronized browsing (FH-05)
    bookmark_keys: list[QAction]  # the keys of the bookmarks, on their side
    places_writable: bool  # False: bookmarks.json is there but can't be read; it is never overwritten then
    local_properties_waiting: bool  # the same for the left side, whose users and groups are read on a thread of their own
    properties_waiting: bool  # the names for the Properties of the server are being read: a second F9 waits for that window
    local_worker: Worker | None  # long work on this computer (Properties of everything inside a folder), made at its first use

    def _ask_text(self, title: str, label: str, start: str = "") -> str | None:
        text, given = QInputDialog.getText(self, title, label, QLineEdit.EchoMode.Normal, start)
        return text.strip() if given and text.strip() else None

    def current(self) -> Session | None:
        raise NotImplementedError

    def _connect(self, session: Session) -> None:
        raise NotImplementedError

    def _set_tab(self, session: Session) -> None:
        raise NotImplementedError

    def _edits_of(self, session: Session) -> list[str]:
        raise NotImplementedError

    def _edits_text(self, copies: list[str]) -> str:
        raise NotImplementedError

    def _unsent_paths(self, copies: list[str]) -> str:
        raise NotImplementedError

    def _forget_edits(self, copies: list[str]) -> None:
        raise NotImplementedError

    def _ask(self, session: Session, work: Callable[[RemoteConnection], object], done: Callable[[object], None], refresh_after_failure: bool = False, on_failure: Callable[[], None] | None = None) -> None:
        raise NotImplementedError

    def load_folder(self, session: Session, folder: str, when_failed: Callable[[Exception], None] | None = None, when_shown: Callable[[], None] | None = None) -> None:
        raise NotImplementedError

    def _tab_shown(self) -> None:
        raise NotImplementedError

    def _local_moved(self, session: Session, previous: str, folder: str) -> None:
        raise NotImplementedError

    def _remote_moved(self, session: Session, previous: str, folder: str) -> None:
        raise NotImplementedError

    def _remember_folders(self, session: Session) -> None:
        raise NotImplementedError

    def _connection_saved(self, old: str, connection: Connection) -> None:
        raise NotImplementedError

    def _sync_ends(self, session: Session) -> None:
        raise NotImplementedError

    def _connection_deleted(self, name: str) -> None:
        raise NotImplementedError

    def _failed(self, session: Session, error: Exception) -> None:
        raise NotImplementedError

    def _reload(self, session: Session) -> None:
        raise NotImplementedError

    def _lost_meanwhile(self, session: Session) -> None:
        """The person confirmed a transfer, but its connection was lost meanwhile: a box says that nothing was transferred, and why, as the person
        expects something to happen; the status bar says it too."""
        text = f"{session.connection.name}: {LOST_MEANWHILE_TEXT if session in self.sessions else TAB_CLOSED_MEANWHILE_TEXT}"
        self.status.showMessage(text)
        QMessageBox.information(self, WINDOW_TITLE, box_text(text))

    def update_actions(self) -> None:
        raise NotImplementedError

    def _then_refresh(self, session: Session) -> Callable[[object], None]:
        raise NotImplementedError

    def _on_screen(self) -> tuple[Session, str, list[RemoteEntry]] | None:
        raise NotImplementedError

    def _open_local_file(self, path: str) -> None:
        raise NotImplementedError

    def _edit_entry(self, session: Session, folder: str, name: str) -> None:
        raise NotImplementedError

    def install_my_key(self, session: Session | None = None) -> None:
        raise NotImplementedError

    def change_password(self, session: Session | None = None) -> None:
        raise NotImplementedError

    def key_reason(self, session: Session) -> str:
        raise NotImplementedError

    def password_reason(self, session: Session) -> str:
        raise NotImplementedError

    def _show_queue(self) -> None:
        raise NotImplementedError

    def _follow_queue(self, job: Job) -> None:
        raise NotImplementedError

    def _stop_editing(self, session: Session, folder: str, names: list[str]) -> None:
        raise NotImplementedError

    def _gone_and_refresh(self, session: Session, folder: str, names: list[str]) -> Callable[[object], None]:
        raise NotImplementedError

    def _move_into(self, session: Session, folder: str, chosen: list[RemoteEntry], target: str) -> None:
        raise NotImplementedError

    def _add_job(self, session: Session, local: str, remote_path: str, upload: bool, name: str, options: TransferOptions | None = None, answers: Answers | None = None, by_itself: bool = False) -> Job | None:
        raise NotImplementedError

    def _edit_job_ended(self, job: Job) -> None:
        raise NotImplementedError

    def _transfers_running(self) -> bool:
        raise NotImplementedError

    def stop_transfers(self) -> None:
        raise NotImplementedError

    def _relink_edit(self, job: Job) -> None:
        raise NotImplementedError

    def _tab_of(self, job: Job) -> Session | None:
        """The open tab a job belongs to; for a job made without one, the tab whose connection it holds."""
        tab = job.tab
        if isinstance(tab, Session):
            return tab if tab in self.sessions else None
        return next((session for session in self.sessions if session.remote is job.remote), None)

    def update_queue_actions(self) -> None:
        raise NotImplementedError

    def _apply_queue_settings(self) -> None:
        raise NotImplementedError
