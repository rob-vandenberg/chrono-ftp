import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast

from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, LocalActionFailed, LocalReadFailed, LocalWriteFailed, NameTaken, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "base.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: A speed limit may be a function, asked again for every block, so a new limit takes effect while a file is on its way; the
#          pauses it asks for are cut in short steps; one range of a file, for a file split over several connections, and another login
#          of the same connection for such a part (FC-05)
# v0.0.12: What a connection can do for a transfer (resume, dates, rights); a transfer can continue at an offset or append; dates, rights,
#          the size of a file (None when unknown), the details of one file, and the removal of an empty folder; whether a size is known;
#          a server path in its one form, .. left to the server; cutting a file back; a size is unknown where the folder can't be read;
#          the texts for a local read or write problem; asking the server to put a file on its disk; LocalFile, shared by SFTP and FTP;
#          swap_in moves the old file aside to a free name it is given and never removes what was there before, and says when the old
#          file stayed; place_new gives a new file its name only while nothing has it; fresh_details for every check of a change; the
#          text for a server without room, shared; whether the server confirms a file is on its disk; a transfer may make its file only
#          where nothing is (exclusive); a listing with the names it leaves out; whether a name is taken this moment; whether an entry
#          is neither a file nor a folder; what swap_in moved aside is removed only while it is still the old file; own_name keeps a
#          name of our own within the length a folder holds; details_or_refusal tells a name that is not there from one the server
#          won't tell about
# v0.0.6: shown_name: a name that is not valid UTF-8 keeps its real bytes and only its display is replaced
# v0.0.5: Initial: the common connection layer: panes, tabs and colours see only this (D-009)

OLD_SUFFIX: str = ".chrono-ftp-old"
NAME_TAKEN_TEXT: str = "Something has taken this name meanwhile."
NO_RIGHTS_TEXT: str = "This server can't set permissions."
NO_TIME_TEXT: str = "This server can't set the date of a file."
GONE_TEXT: str = "This file is no longer on the server."
NOT_EMPTY_TEXT: str = "This folder is not empty, so it stays."
NO_CUT_TEXT: str = "This server can't cut a file back to its earlier length."
SHORT_TEXT: str = "The source ended earlier than its size said while it was being copied, so the copy was not put in place. Please transfer it once more."
NO_ROOM_TEXT: str = "The server has no room for this file ({reply}). The other files go on; you can send this one again once there is room."
LOCAL_READ_TEXT: str = "This computer could not read the file it was sending: {reason}."
LOCAL_WRITE_TEXT: str = "This computer could not write what arrived: {reason}."
NO_SPLIT_TEXT: str = "This connection sends a file in one piece."
Progress = Callable[[int], None]  # bytes done since the last call; it may raise to stop the transfer
Limit = int | Callable[[], int]  # KB per second, 0 for none; a function is asked again for every block, so a change takes effect at once
LIMIT_STEP_SECONDS: float = 0.2  # a pause for the limit is cut in steps this long, so a stop or a new limit is never kept waiting


def limit_now(limit: Limit) -> int:
    """The limit at this moment, in KB per second; 0 for none. A number from input is never below 0."""
    return max(0, limit() if callable(limit) else limit)


def limit_pauses(limit: Limit, amount: int) -> Iterator[float]:
    """The pauses after amount bytes that keep the transfer within the limit, each at most LIMIT_STEP_SECONDS. The limit is asked again
    before each, so a raised limit shortens the rest and none ends it. After each pause the caller asks progress with 0 bytes, which raises
    when the transfer was stopped meanwhile."""
    owed = float(amount)
    while owed > 0:
        now = limit_now(limit)
        if not now:
            return
        step = min(LIMIT_STEP_SECONDS, owed / (now * 1024))
        yield step
        owed -= step * now * 1024


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
    link_target: str = ""
    size_known: bool = True  # False when the server gave no size, or one that is no number: size is then 0 and means nothing
    kind_known: bool = True  # False when only its being there is known (a folder that can't be read): file or folder, nobody knows
    special: bool = False  # neither a file nor a folder (a pipe, a socket or a device), as far as the server tells: nothing to fetch


class Swapped(Enum):
    """What became of the old file once a new one took its place."""

    REMOVED = "removed"
    NOT_REMOVED = "not removed"  # it could not be removed: it stays under its aside name
    CHANGED = "changed"  # it changed after it was agreed to be replaced: it stays under its aside name, never removed


def _same_file(now: RemoteEntry, old: RemoteEntry) -> bool:
    """now is still old as far as the server tells: a file, of the same size and date where both are known."""
    if now.is_folder or not now.kind_known:
        return False
    if now.size_known and old.size_known and now.size != old.size:
        return False
    return now.modified is None or old.modified is None or now.modified == old.modified


@dataclass(frozen=True)
class Capabilities:
    """What this connection can do, found out on connecting (D-016). Nothing is assumed."""

    shell: bool = False  # POSIX commands can run on the server
    system: str = ""
    tools: frozenset[str] = field(default_factory=frozenset[str])
    is_root: bool = False
    resume: bool = False  # a file can be continued at an offset (FB-02)
    timestamps: bool = False  # the date of a file can be set (FB-05)
    rights: bool = False  # the permissions of a file can be set (FB-06)
    cut: bool = False  # a file on the server can be cut back to a length, so a broken-off append can be undone (FB-03)
    durable: bool = False  # the server confirms on request that a file is on its disk (SFTP's fsync@openssh.com)

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


class LocalFile:
    """A file on this computer in a copy: whatever fails in it (opening, reading, writing, moving the position, cutting, closing) is this
    computer's problem, never a refusal of the server nor a refused continuation, and the text says so. SFTP and FTP both use it."""

    def __init__(self, path: str, mode: str, writing: bool) -> None:
        self.writing: bool = writing
        try:
            self.file: BinaryIO = cast(BinaryIO, Path(path).open(mode))
        except FileExistsError as error:
            raise NameTaken(NAME_TAKEN_TEXT) from error  # made only where nothing was (mode x): something has that name now
        except OSError as error:
            raise self._problem(error) from error

    def _problem(self, error: OSError) -> LocalActionFailed:
        reason = error.strerror or str(error)
        return LocalWriteFailed(LOCAL_WRITE_TEXT.format(reason=reason)) if self.writing else LocalReadFailed(LOCAL_READ_TEXT.format(reason=reason))

    def read(self, size: int = -1, /) -> bytes:
        try:
            return self.file.read(size)
        except OSError as error:
            raise self._problem(error) from error

    def write(self, data: bytes) -> None:
        try:
            self.file.write(data)
        except OSError as error:
            raise self._problem(error) from error

    def seek(self, offset: int, whence: int = 0) -> int:
        try:
            return self.file.seek(offset, whence)
        except OSError as error:
            raise self._problem(error) from error

    def truncate(self, size: int) -> None:
        try:
            self.file.truncate(size)
        except OSError as error:
            raise self._problem(error) from error

    def __enter__(self) -> "LocalFile":
        return self

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None, trace: object) -> None:
        try:
            self.file.close()  # also writes what is still in the buffer
        except OSError as problem:
            if kind is None:
                raise self._problem(problem) from problem  # with another error under way, that one is what happened


NAME_BYTES: int = 255  # the longest name a folder holds, in bytes, here and on the servers (as UTF-8, with the real bytes kept)
HASH_LENGTH: int = 8  # hex digits of the full name's hash in a shortened name of our own


def own_name(name: str, extra: str) -> str:
    """A name of our own made from a real one (a part file, an aside copy, a kept version): name with extra after it, which ends in our
    suffix. Where that would be longer than a folder can hold, only the part taken from the real name is shortened, at a whole
    character, and the first digits of the full name's hash keep it its own: two long names that start alike never share it."""
    whole = name + extra
    if len(_stored(whole)) <= NAME_BYTES:
        return whole
    mark = "~" + hashlib.sha256(_stored(name)).hexdigest()[:HASH_LENGTH]
    room = NAME_BYTES - len(_stored(mark + extra))
    kept, used = "", 0
    for character in name:
        size = len(_stored(character))
        if used + size > room:
            break
        kept, used = kept + character, used + size
    return kept + mark + extra


def _stored(text: str) -> bytes:
    """A name as a folder stores it: UTF-8, and a byte that is no UTF-8 as itself (it was kept through surrogates)."""
    return text.encode("utf-8", "surrogateescape")


def safe_name(name: str) -> bool:
    """A name from a server is input: one with a slash or a parent step could write outside the folder it is copied to."""
    return bool(name) and name not in (".", "..") and "/" not in name and "\\" not in name and "\0" not in name


def shown_name(name: str) -> str:
    """What the screen shows. Names keep the bytes of the server through surrogates, so an action reaches the real file; Qt can't hold those."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def joined(folder: str, name: str) -> str:
    return str(PurePosixPath(folder) / name)


def normalized(path: str) -> str:
    """A server path as typed, in its one form: /a/, /a//, /a/./ are all /a. A .. is left to the server: through a link it may lead
    elsewhere than the text before it says."""
    parts = [part for part in path.split("/") if part not in ("", ".")]
    joined_parts = "/".join(parts)
    return "/" + joined_parts if path.startswith("/") else joined_parts or "."


def parent_of(path: str) -> str:
    return str(PurePosixPath(path).parent)


class RemoteConnection(ABC):
    """One login on one server. Every method blocks, so the window calls them from a worker (D-019)."""

    capabilities: Capabilities = Capabilities()
    encrypted: bool = True  # False only for FTP without TLS, which the person chose (OR-19)

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
    def download(self, remote: str, local: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        """One file; limit is in KB per second, 0 for no limit. progress is called only for bytes written in order from the start (or from
        offset), so their sum is a length that is known to be there. offset: a resume: the target keeps its first offset bytes, anything
        after them goes, and the source from offset on is written there. append: the whole source is written after the end of the target."""

    @abstractmethod
    def upload(self, local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None: ...

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

    def set_rights(self, path: str, mode: str) -> None:
        """The rights the person chose (FB-06); a refusal is raised, so the person's choice to ignore it decides."""
        raise RemoteActionFailed(NO_RIGHTS_TEXT)

    def set_time(self, path: str, modified: float) -> None:
        """The date of a file or folder (FB-05); a refusal is raised."""
        raise RemoteActionFailed(NO_TIME_TEXT)

    def file_size(self, path: str) -> int | None:
        """The size of a file as the server has it now; None when the server can't tell it, also when its folder can't be read."""
        try:
            found = self.fresh_details(path)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return None
        if found is None or found.is_folder:
            raise RemoteActionFailed(GONE_TEXT)
        return found.size if found.size_known else None

    def name_taken(self, path: str) -> bool:
        """Whether something has this name this moment, for a name about to be used."""
        return self.fresh_details(path) is not None

    def list_folder_all(self, path: str) -> tuple[list[RemoteEntry], list[str]]:
        """The listing, and the names it leaves out because they can't be handled safely (a slash, a backslash, a NUL): a transfer fails
        each of those on its own, never in silence."""
        return self.list_folder(path), []

    def fresh_details(self, path: str) -> RemoteEntry | None:
        """As details, read from the server this moment, never from a listing kept earlier: for every check of whether something changed."""
        return self.details(path)

    def details_or_refusal(self, path: str) -> RemoteEntry | None:
        """As details, but None only where the server says the name is not there; a server that refuses to tell raises
        RemoteActionFailed. Where a connection can't tell the two apart, this is details."""
        return self.details(path)

    def details(self, path: str) -> RemoteEntry | None:
        """One file or folder as the server has it now; None when it is not there."""
        parent, name = path.rsplit("/", 1) if "/" in path else ("", path)
        return next((entry for entry in self.list_folder(parent or "/") if entry.name == name), None)

    def revive(self) -> None:
        """Makes sure the login still works, logging in again where that needs nothing from the person; raises ConnectionLost when it is
        gone for good. A connection that can't drop on its own (SSH keeps itself alive) does nothing."""

    def make_durable(self, path: str) -> None:
        """Asks the server to put this file on its disk, where the protocol offers it; elsewhere nothing happens. A refusal is raised."""

    def cut(self, path: str, length: int) -> None:
        """The file is cut back to length; only where capabilities.cut says the server can."""
        raise RemoteActionFailed(NO_CUT_TEXT)

    def remove_empty_folder(self, path: str) -> None:
        """Only an empty folder: one that still holds something is refused, never emptied (FB-08)."""
        raise RemoteActionFailed(NOT_EMPTY_TEXT)

    def swap_in(self, temporary: str, target: str, old: RemoteEntry, kept: str = "") -> Swapped:
        """A finished temporary file takes the place of the target. The old file is only moved aside, to kept (a free name the caller
        chose), until the new one stands, so at every moment one of them is there under a name, and the new file keeps the old one's
        permissions. Nothing that was there before under kept is ever touched: the caller makes sure the name is free. What was moved
        aside is removed only while it is still old (its size and date, where the server tells them): something else may have saved
        the target between the caller's last look and the move, and that version is never removed."""
        kept = kept or joined(parent_of(target), own_name(target.rsplit("/", 1)[-1], OLD_SUFFIX))
        if old.permissions:
            self.set_mode(temporary, old.permissions)  # before it has the real name, so it is never there with other permissions
        self.rename(target, kept)
        try:
            self.rename(temporary, target)
        except RemoteActionFailed:
            self.rename(kept, target)
            raise
        try:
            aside = self.fresh_details(kept)
            if aside is not None and not _same_file(aside, old):
                return Swapped.CHANGED
            self.delete(kept, False)
        except RemoteActionFailed:  # also a connection that broke off just now: the new file stands all the same
            return Swapped.NOT_REMOVED
        return Swapped.REMOVED

    def place_new(self, temporary: str, target: str) -> None:
        """A finished temporary file takes a name nothing has, and never replaces what took that name meanwhile (NameTaken). This
        server's rename would replace it, so the name is looked up right before; something that takes it in that last moment is
        replaced all the same, a limit only a server that refuses to rename onto a name can close."""
        if self.details(target) is not None:
            raise NameTaken(NAME_TAKEN_TEXT)
        self.rename(temporary, target)

    def for_a_transfer(self) -> "RemoteConnection":
        """A connection for the transfer queue, so browsing goes on during a transfer."""
        return self

    def can_split(self, upload: bool) -> bool:
        """Whether one file can go in ranges, each over its own login (FC-05): download_range, upload_range and another_login work."""
        return False

    def another_login(self) -> "RemoteConnection":
        """A login of its own, with what this one logged in with, for one range of a split file. Raises ConnectionFailed (NoMoreLogins
        where the server takes no more at the moment)."""
        raise RemoteActionFailed(NO_SPLIT_TEXT)

    def download_range(self, remote: str, local: str, start: int, end: int, progress: Progress, limit: Limit = 0) -> None:
        """The bytes [start, end) of the server's file written at the same place in the file here, which exists already; nothing else of
        it is touched. progress is called only for bytes written in order from start."""
        raise RemoteActionFailed(NO_SPLIT_TEXT)

    def upload_range(self, local: str, remote: str, start: int, end: int, progress: Progress, limit: Limit = 0) -> None:
        """The bytes [start, end) of the file here written at the same place in the server's file, which exists already; as download_range."""
        raise RemoteActionFailed(NO_SPLIT_TEXT)

    def make_empty(self, remote: str) -> None:
        """An empty file made only where nothing is (NameTaken otherwise): the part a split upload writes its ranges into."""
        raise RemoteActionFailed(NO_SPLIT_TEXT)
