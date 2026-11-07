from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "base.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: the common connection layer: panes, tabs and colours see only this (D-009)

OLD_SUFFIX: str = ".chrono-ftp-old"
Progress = Callable[[int], None]  # bytes done since the last call; it may raise to stop the transfer


@dataclass(frozen=True)
class RemoteEntry:
    name: str
    is_folder: bool
    size: int = 0
    modified: float | None = None
    permissions: str = ""
    owner: str = ""
    group: str = ""
    is_link: bool = False


@dataclass(frozen=True)
class Capabilities:
    """What this connection can do, found out on connecting (D-016). Nothing is assumed."""

    shell: bool = False  # POSIX commands can run on the server
    system: str = ""
    tools: frozenset[str] = field(default_factory=frozenset[str])
    is_root: bool = False

    def has(self, tool: str) -> bool:
        return self.shell and tool in self.tools


@dataclass(frozen=True)
class CommandResult:
    status: int
    output: str
    errors: str


class UnknownServer(ChronoFtpError):
    """The server's identity is not known yet: the person decides (OR-19). It holds what to show and what to remember."""

    def __init__(self, kind: str, fingerprint: str, to_remember: str, text: str = "") -> None:
        super().__init__(text or f"This server's {kind} is new to Chrono FTP.")
        self.kind: str = kind
        self.fingerprint: str = fingerprint
        self.to_remember: str = to_remember


def safe_name(name: str) -> bool:
    """A name from a server is input: one with a slash or a parent step could write outside the folder it is copied to."""
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name and "\0" not in name


def joined(folder: str, name: str) -> str:
    return str(PurePosixPath(folder) / name)


def parent_of(path: str) -> str:
    return str(PurePosixPath(path).parent)


class RemoteConnection(ABC):
    """One login on one server. Every method blocks, so the window calls them from a worker (D-019)."""

    capabilities: Capabilities = Capabilities()

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def home(self) -> str: ...

    @abstractmethod
    def list_folder(self, path: str) -> list[RemoteEntry]: ...

    @abstractmethod
    def rename(self, old: str, new: str) -> None: ...

    @abstractmethod
    def make_folder(self, path: str) -> None: ...

    @abstractmethod
    def delete(self, path: str, is_folder: bool) -> None: ...

    @abstractmethod
    def download(self, remote: str, local: str, progress: Progress, limit: int = 0) -> None:
        """One file; limit is in KB per second, 0 for no limit."""

    @abstractmethod
    def upload(self, local: str, remote: str, progress: Progress, limit: int = 0) -> None: ...

    @abstractmethod
    def check(self) -> None:
        """Raises ConnectionLost when the connection is gone (D-029)."""

    def run(self, command: str) -> CommandResult:
        raise NotImplementedError

    def resolved(self, path: str) -> str | None:
        """Where a link really leads; None when the protocol can't tell."""
        return None

    def set_mode(self, path: str, mode: str) -> None:
        """Gives a file back its permissions; a server that can't do this is left as it is."""

    def swap_in(self, temporary: str, target: str, old: RemoteEntry) -> None:
        """A finished temporary file takes the place of the target. The old file is only moved aside until the new one stands, so at every
        moment one of them is there under a name, and the new file keeps the old one's permissions."""
        kept = target + OLD_SUFFIX
        if old.permissions:
            self.set_mode(temporary, old.permissions)  # before it has the real name, so it is never there with other permissions
        try:
            self.delete(kept, False)  # what an earlier drop left behind: the target itself is there, so this copy is not the only one
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            pass  # usually there is none
        self.rename(target, kept)
        try:
            self.rename(temporary, target)
        except RemoteActionFailed:
            self.rename(kept, target)
            raise
        self.delete(kept, False)

    def for_a_transfer(self) -> "RemoteConnection":
        """A connection for the transfer queue, so browsing goes on during a transfer."""
        return self
