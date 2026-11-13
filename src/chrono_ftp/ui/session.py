"""One tab of the main window: a connection with its own local and remote folder, and the files of it open for editing (OR-4)."""

from dataclasses import dataclass, field

from chrono_ftp.connections.base import Capabilities, RemoteConnection, RemoteEntry
from chrono_ftp.core.connection import Connection
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.transfers.queue import Job
from chrono_ftp.ui.sidebar import FolderHistory

# --- Version ------------------------------------------------------------
__version__ = "session.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: What Ctrl+C copied, and a copy from one server to another that goes through this computer (FI-03)
# v0.0.41: Each tab keeps the filter of the left side too (FD-06)
# v0.0.40: Each tab can pause its automatic refresh (FG-15)
# v0.0.39: Each tab keeps whether each side shows thumbnails (FQ-02)
# v0.0.38: Each tab can keep its own sort order and column widths, and knows whether an automatic refresh is on its way (FP-01)
# v0.0.34: Each tab keeps whether synchronized browsing is on, and the folder a step of it asked for on the server (FH-05); the folder
#          each side fell back to while the person has not moved it (FH-06)
# v0.0.13: An edited file knows the last save sent, so a retry of it in the queue counts as its save again
# v0.0.11: Moved here from mainwindow.py, unchanged; the helpers lost their leading underscore (D-041)


@dataclass(eq=False)
class EditedFile:
    """A file of the server, open in a program here: its copy goes back when the program saves it."""

    session: "Session"
    folder: str
    name: str
    unsent: bool = False  # saved, and not yet on its way to the server
    job: Job | None = None  # on its way now; only its end says the save reached the server
    last_job: Job | None = None  # the last one sent: a retry of it in the queue is on its way again (EditPart._relink_edit)
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
    typed_user: bool = False  # saved with the connection only once the server accepted it
    user_before: str = ""  # what Cancel goes back to
    wanted_folder: str = ""  # the last folder asked for: an older answer is left out
    home_folder: str = ""
    connected_at: float = 0.0
    remote_history: FolderHistory = field(default_factory=FolderHistory)
    local_history: FolderHistory = field(default_factory=FolderHistory)
    name_mask: str = ""  # the filter of the right side
    local_name_mask: str = ""  # the filter of the left side while this tab is shown (FD-06)
    sync_browsing: bool = False  # synchronized browsing, per tab (FH-05)
    local_fallback: str = ""  # the home folder the left side opened as its starting folder was gone; empty once the person moved it
    remote_fallback: str = ""  # the same on the server
    sync_asked: str = ""  # the folder synchronized browsing asked the server for: its arrival is no step of the person
    leftovers: dict[str, tuple[float, int]] = field(default_factory=dict[str, tuple[float, int]])  # see tidy; only the session's thread uses it
    local_columns: bytes = b""  # the sort order and column widths of each side while another tab is shown, where they are kept per tab (FP-01)
    remote_columns: bytes = b""
    auto_refreshing: bool = False  # an automatic refresh is on its way: the next waits for it (FP-01)
    local_thumbnails: bool = False  # the thumbnail view of each side, per tab (FQ-02)
    remote_thumbnails: bool = False
    refresh_problem: str = ""  # what the last automatic refresh could not do: said once, not at every refresh
    refresh_paused: bool = False  # Pause Automatic Refresh, from the menu of the tab (FG-15)
    wanted_start: str = ""  # the folder on the server this tab is to open in, from the workspace it came from (FI-02)

    @property
    def capabilities(self) -> Capabilities:
        return self.remote.capabilities if self.remote is not None and self.alive else Capabilities()


@dataclass(eq=False)
class CopiedFiles:
    """What Ctrl+C copied: where the files are, so Ctrl+V in another tab knows what to fetch (FI-03). text is exactly what was put on
    the clipboard: once the clipboard holds something else, this copy is no longer what the person would paste."""

    session: "Session | None"  # None: the left side, this computer
    folder: str
    entries: tuple[RemoteEntry, ...]  # exactly what the person saw and chose
    text: str

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.entries)


@dataclass(eq=False)
class Relay:
    """A copy from one server to another that goes through this computer: first the download, then the upload of what arrived (FI-03)."""

    target: "Session"
    folder: str  # where it goes on the target server
    name: str
    copy: str  # the copy on this computer
    temporary: str  # the folder that holds it; it goes when the relay ends
    uploading: bool = False  # the download is done and the upload runs


def no_progress(_amount: int) -> None:
    pass


def login_of(session: Session) -> tuple[str, int, str]:
    return session.connection.host, session.connection.port, session.connection.user


def same_server(first: Session, second: Session) -> bool:
    return login_of(first) == login_of(second)


def is_at_or_below(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip("/") + "/")
