"""One tab of the main window: a connection with its own local and remote folder, and the files of it open for editing (OR-4)."""

from dataclasses import dataclass, field

from chrono_ftp.connections.base import Capabilities, RemoteConnection, RemoteEntry
from chrono_ftp.core.connection import Connection
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.transfers.queue import Job
from chrono_ftp.ui.sidebar import FolderHistory

# --- Version ------------------------------------------------------------
__version__ = "session.py 0.0.13"

# --- Version history ----------------------------------------------------
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
    leftovers: dict[str, tuple[float, int]] = field(default_factory=dict[str, tuple[float, int]])  # see tidy; only the session's thread uses it

    @property
    def capabilities(self) -> Capabilities:
        return self.remote.capabilities if self.remote is not None and self.alive else Capabilities()


def no_progress(_amount: int) -> None:
    pass


def login_of(session: Session) -> tuple[str, int, str]:
    return session.connection.host, session.connection.port, session.connection.user


def same_server(first: Session, second: Session) -> bool:
    return login_of(first) == login_of(second)


def is_at_or_below(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip("/") + "/")
