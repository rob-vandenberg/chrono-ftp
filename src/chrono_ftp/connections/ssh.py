import asyncio
import math
import re
import shlex
import struct
import threading
from collections.abc import Callable, Coroutine, Sequence
from dataclasses import replace
from concurrent.futures import CancelledError
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import BinaryIO, cast, override

import asyncssh

from chrono_ftp.connections import sessionlog, trust
from chrono_ftp.connections.passwordchange import PASSWD_COMMAND, AskedTooOften, PasswordAnswer, PasswordTalk, shown_answer
from chrono_ftp.connections.base import TOO_LARGE_TEXT, FreeSpace, KeepGoing, ServerInfo, log_listing, size_from, HALF_COPY_LEFT_TEXT, NO_ATOMIC_TEXT, NO_COPY_TEXT, NO_HARD_LINKS_TEXT, NO_PASSWORD_TEXT, LOCAL_READ_TEXT, LOCAL_WRITE_TEXT, LocalFile, NAME_TAKEN_TEXT, NO_ROOM_TEXT, NO_TIME_TEXT, NOT_EMPTY_TEXT, SHORT_TEXT, Capabilities, CommandResult, Limit, Progress, RemoteConnection, RemoteEntry, UnknownServer, joined, limit_now, limit_pauses, safe_name
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.logrules import DEBUG, NORMAL
from chrono_ftp.core.errors import ChronoFtpError, ConnectionFailed, ConnectionLost, ContinuationRefused, LocalActionFailed, LocalReadFailed, LocalWriteFailed, LoginRefused, NameTaken, RefusedForNow, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "ssh.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: A small file read into memory block by block, stopped as soon as it is told to, for the thumbnails (FQ-02)
# v0.0.38: The session log: the login, every SFTP request and command with the server's answer, transfers, and at the debug level the
#          encryption agreed and what the server offers; the passwords of the connection and of passwd are never written (FP-02); what
#          the connection tells about itself, and the free space of a folder through statvfs@openssh.com or df (FP-03)
# v0.0.37: Symbolic and hard links and a copy of a file on the server itself through SFTP, where the server offers them (copy-data,
#          hardlink@openssh.com); the password changed by the server's passwd in a terminal of its own, never written anywhere;
#          whether passwd is there is detected (FM-05, FM-06, FM-10); posix-rename and ln detected, for a link led elsewhere in one step;
#          a copy that fails removes the half copy it made; a name is taken also by a link that leads nowhere (lstat); passwd that
#          never says it is done, or a talk that breaks off, gives a calm and true message, never "no shell"; a line lost after both new
#          passwords were typed says the server may have taken the new one; a command that should answer at once is ended after a few
#          seconds, and the connection goes on; passwd gets 60 seconds per answer
# v0.0.13: The speed limit is asked again for every block, also in a copy that started without one, so a new limit takes effect at
#          once; its pauses are short steps, so a stop is never kept waiting; one range of a file and another login of its own, for a file
#          split over several connections (FC-05)
# v0.0.12: A transfer can continue at an offset or append; dates, rights, sizes and empty folders over SFTP (FB-02, FB-05, FB-06, FB-08);
#          a date SFTP can't carry is a refusal, not a failure of the transfer; a link to a file is listed with the size and date of
#          the file it leads to, which is what a transfer reads; every transfer goes block by block, so what progress counted is there
#          in order, and a continued file is first cut to where it continues; the details of one file; a refused rmdir says why.
#          Without a limit many requests travel at once again, as fast as asyncssh's own copy; only the bytes with no gap before them
#          count, and on a failure or a stop the file is cut to them; a copy is complete only when they reach the whole size; an append
#          is cut back on a failure or a stop (an upload's by its caller, only where nothing but it can be in the file); a file can be
#          cut back; a local read or write problem is this computer's; resolved lets a lost
#          connection through; every operation on this computer's file in a copy is this computer's problem (LocalFile, now in base.py);
#          a file is put on the server's disk by fsync where the server offers it, with no answer limit while the connection lives; a
#          new file never replaces what took its name meanwhile; a server without room refuses that one file for now; whether it can put
#          a file on its disk on request is known from the start; a temporary file is made only where nothing is (exclusive); a listing
#          says which names it left out; only a refused exclusive making of the file means its name is taken, never a failure while
#          writing; a part the server won't cut is a refused continuation; a failure it does not explain keeps the part; a download
#          asks for nothing more once the source ended, and every request that ended is a moment to see a Stop; a pipe, socket or
#          device is marked as such; a refusal to tell about a name is told apart from a name that is not there
# v0.0.6: Names that are not valid UTF-8 keep their real bytes, in paths and in commands
# v0.0.5: Initial: browse over SFTP and run commands over the same login (D-010); capabilities are detected (D-016)

CONNECT_SECONDS: float = 20.0
ANSWER_SECONDS: float = 60.0
CLOSE_SECONDS: float = 3.0
KEEPALIVE_SECONDS: float = 20.0
KEEPALIVE_TRIES: int = 3
COMMAND_SECONDS: float = 3600.0  # a recursive change on a big tree runs long
BLOCK_SIZE: int = 64 * 1024
HIGHEST_TIME: int = 2**32  # SFTP carries a date as an unsigned 32-bit number of seconds: from 1970 to 2106
IN_FLIGHT_BYTES: int = 4 * 1024 * 1024  # as asyncssh's own copy: about this much travels at once, in 16 to 128 requests
FEWEST_REQUESTS: int = 16
MOST_REQUESTS: int = 128
TOOLS: tuple[str, ...] = ("rsync", "find", "chown", "chmod", "cp", "mv", "rm", "sha256sum", "passwd", "ln", "df")
PROBE_MARK: str = "chrono-ftp-probe"
PROBE: str = f"echo {PROBE_MARK}; uname -s; id -u; for tool in " + " ".join(TOOLS) + "; do command -v $tool >/dev/null 2>&1 && echo tool:$tool; done; exit 0"
PROBE_SECONDS: float = 15.0
CHANGED_TEXT: str = "This server's host key has changed since you trusted it, so Chrono FTP did not connect. If you know why, remove its line from known_hosts."
NOT_REACHED_TEXT: str = "The server could not be reached. Please check the host and the port."
LOGIN_TEXT: str = "The server did not accept this login. Please check the user, password or key file."


NOT_CUT_TEXT: str = "The server would not cut the unfinished copy back to where it was to be continued."
STOPPED_TAKING_TEXT: str = "The server stopped this file ({reply}) and does not say why; perhaps it has no room. The other files go on; you can send this one again later."
LOST_TEXT: str = "The connection to the server was lost."
SLOW_COMMAND_TEXT: str = "it did not answer within {seconds} seconds"
PASSWORD_READ_BYTES: int = 4096
PASSWORD_ANSWER_SECONDS: float = 60.0  # per answer of passwd: a slow PAM or LDAP check may take a while
PASSWORD_REFUSED_TEXT: str = "The server did not change the password and gave no reason. The old password still works."
PASSWORD_NO_ANSWER_TEXT: str = "The server did not answer in time, so the password was left as it was."
PASSWORD_NO_SESSION_TEXT: str = "The server opened no session to run passwd, so the password was left as it was."
PASSWORD_FAILED_TEXT: str = "The talk with passwd broke off ({reason}), so the server did not confirm a change; your stored password was left as it was."
PASSWORD_UNKNOWN_TEXT: str = "The server did not say whether the password changed, and it may have taken the new one. Your stored password was left as it was; if the old password is refused at the next login, type the new one there."
LOST_ERRORS: tuple[type[BaseException], ...] = (asyncssh.ConnectionLost, asyncssh.SFTPConnectionLost, asyncssh.SFTPNoConnection, ConnectionError)


DF_LINE: re.Pattern[str] = re.compile(r"\s([0-9]{1,18})\s+([0-9]{1,18})\s+([0-9]{1,18})\s+[0-9]{1,3}%\s")  # blocks, used, available, capacity
DF_BLOCK: int = 1024  # df -k counts in blocks of this size
EXTENSIONS: tuple[tuple[str, str], ...] = (  # what asyncssh learnt from the server's hello, by the name the server gave it
    ("_supports_posix_rename", "posix-rename@openssh.com"),
    ("_supports_statvfs", "statvfs@openssh.com"),
    ("_supports_fstatvfs", "fstatvfs@openssh.com"),
    ("_supports_hardlink", "hardlink@openssh.com"),
    ("_supports_fsync", "fsync@openssh.com"),
    ("_supports_lsetstat", "lsetstat@openssh.com"),
    ("_supports_limits", "limits@openssh.com"),
    ("_supports_copy_data", "copy-data"),
)


def space_from_df(output: str) -> FreeSpace | None:
    """The room df -Pk tells: the last line holds the numbers, whatever the name of the file system before them. Input (OR-15)."""
    lines = [line for line in output.splitlines() if line.strip()]
    found = DF_LINE.search(lines[-1] + " ") if len(lines) >= 2 else None
    if found is None:
        return None
    total, available = int(found.group(1)), int(found.group(3))
    return FreeSpace(size_from(available * DF_BLOCK), size_from(total * DF_BLOCK), "df")


def _operation(work: object) -> str:
    """The name of an SFTP request, as asyncssh names its call: readdir, stat, rename."""
    return str(getattr(work, "__qualname__", "request")).rsplit(".", 1)[-1]


def _answer_of(result: object) -> str:
    if isinstance(result, list):
        return f"{len(cast(list[object], result))} names"
    if isinstance(result, (str, bytes)):
        return _text(result)
    return "done"


SPECIAL_TYPES: frozenset[int] = frozenset({asyncssh.FILEXFER_TYPE_FIFO, asyncssh.FILEXFER_TYPE_SOCKET, asyncssh.FILEXFER_TYPE_CHAR_DEVICE, asyncssh.FILEXFER_TYPE_BLOCK_DEVICE, asyncssh.FILEXFER_TYPE_SPECIAL})


def _special(attributes: asyncssh.SFTPAttrs) -> bool:
    """Neither a file nor a folder: a pipe, a socket or a device. Reading one could wait forever, so it is never fetched."""
    return attributes.type in SPECIAL_TYPES


def _modes(permissions: int | None) -> str:
    return "" if permissions is None else format(permissions & 0o7777, "04o")


def sendable(command: str) -> str:
    """A command travels as strict UTF-8. One with a name that is not valid UTF-8 goes as plain digits, and the shell makes the real bytes."""
    try:
        command.encode("utf-8")
        return command
    except UnicodeEncodeError:
        digits = "".join(f"\\0{byte:03o}" for byte in command.encode("utf-8", "surrogateescape"))
        return f"eval \"$(printf '%b' '{digits}')\""


def _text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "surrogateescape")
    return value or ""


class Prefix:
    """The bytes confirmed written in order from start. Requests travel together and may end in any order: a range that ends early waits
    until every range before it has ended too, so progress only ever counts bytes with no gap before them."""

    def __init__(self, start: int, progress: Progress) -> None:
        self.end: int = start
        self.progress: Progress = progress
        self.waiting: dict[int, int] = {}

    def written(self, place: int, length: int) -> None:
        self.waiting[place] = length
        grown = 0
        while self.end in self.waiting:
            length = self.waiting.pop(self.end)
            if length == 0:
                break  # the source ended earlier than its size said: nothing after this is in order
            self.end += length
            grown += length
        self.progress(grown)  # also 0: every request that ended is a moment to see a Stop, not only one that grew the bytes in order


async def _cut_to(remote: asyncssh.SFTPClientFile, offset: int) -> None:
    """A part cut to where it is continued. A server that won't cut it refuses the continuation: the file starts again from the
    beginning, and the part goes."""
    try:
        await remote.truncate(offset)
    except (asyncssh.SFTPOpUnsupported, asyncssh.SFTPFailure, asyncssh.SFTPPermissionDenied) as error:
        raise ContinuationRefused(NOT_CUT_TEXT) from error

class CreateRefused(Exception):
    """The server refused to make a file only where nothing is: the name may be taken, or making it there is not allowed."""


async def _opened(sftp: asyncssh.SFTPClient, target: str, mode: str) -> asyncssh.SFTPClientFile:
    """A file of the server opened in mode; with x (made only where nothing is) a refusal is told apart from any later failure."""
    try:
        return await sftp.open(target, mode)
    except (asyncssh.SFTPFailure, asyncssh.SFTPFileAlreadyExists, asyncssh.SFTPPermissionDenied) as error:
        if "x" in mode:
            raise CreateRefused() from error
        raise


KEX: str = "key exchange"  # what asyncssh calls the choice of the key exchange


class _KexWitness(asyncssh.SSHClient):
    """Notes the key exchange the connection agrees on: asyncssh forgets it once the keys are made, and the server information shows it
    (FP-03). A version of asyncssh that chooses another way leaves it unknown, and nothing else changes."""

    def __init__(self) -> None:
        self.kex: str = ""

    @override
    def connection_made(self, conn: asyncssh.SSHClientConnection) -> None:
        found: object = getattr(conn, "_choose_alg", None)
        if not callable(found):
            return
        choose = cast(Callable[[str, Sequence[bytes], Sequence[bytes]], bytes], found)

        def noted(kind: str, local: Sequence[bytes], remote: Sequence[bytes]) -> bytes:
            chosen = choose(kind, local, remote)
            if kind == KEX and isinstance(chosen, bytes):
                self.kex = chosen.decode("ascii", "replace")
            return chosen

        setattr(conn, "_choose_alg", noted)


class SshConnection(RemoteConnection):
    def __init__(self, connection: Connection, password: str | None = None, known_hosts: str | None = None) -> None:
        self.connection: Connection = connection
        self.password: str | None = password
        self.known_hosts: str = known_hosts or str(trust.known_hosts_path())
        self.log = sessionlog.log_for(connection.name)
        self.log.hide(password)  # never written, whatever the level (OR-19)
        self.capabilities = Capabilities()
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread: threading.Thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._closed: bool = False
        self._client: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._witness: _KexWitness = _KexWitness()

    def _await[T](self, work: Coroutine[object, object, T], seconds: float | None = ANSWER_SECONDS) -> T:
        if self._closed:
            work.close()
            raise ConnectionLost(LOST_TEXT)
        future = asyncio.run_coroutine_threadsafe(work, self._loop)
        try:
            return future.result(seconds)
        except (TimeoutError, FutureTimeout) as error:
            future.cancel()
            raise ConnectionLost("The server did not answer in time.") from error
        except CancelledError as error:
            raise ConnectionLost(LOST_TEXT) from error

    def _host_key_files(self) -> list[str]:
        files = [self.known_hosts]
        users = Path.home() / ".ssh" / "known_hosts"
        return [name for name in [*files, str(users)] if Path(name).exists()]

    async def _open(self) -> None:
        options: dict[str, object] = {"host": self.connection.host, "port": self.connection.port, "known_hosts": self._host_key_files() or b"", "connect_timeout": CONNECT_SECONDS, "keepalive_interval": KEEPALIVE_SECONDS, "keepalive_count_max": KEEPALIVE_TRIES, "client_factory": lambda: self._witness}
        if self.connection.user:
            options["username"] = self.connection.user
        if self.connection.key_file:
            options["client_keys"] = [self.connection.key_file]
        if self.password:
            options["password"] = self.password
        self._client = await asyncssh.connect(**options)
        self._sftp = await self._client.start_sftp_client(path_errors="surrogateescape")  # a name that is not valid UTF-8 stays reachable (OR-15)

    def _known_keys(self) -> list[asyncssh.SSHKey]:
        found: list[asyncssh.SSHKey] = []
        for name in self._host_key_files():
            found.extend(asyncssh.read_known_hosts(name).match(self.connection.host, "", self.connection.port)[0])
        return found

    async def _unknown_server(self) -> UnknownServer:
        if self._known_keys():
            raise ConnectionFailed(CHANGED_TEXT)
        key = await asyncssh.get_server_host_key(self.connection.host, self.connection.port)
        if key is None:
            raise ConnectionFailed(NOT_REACHED_TEXT)
        where = self.connection.host if self.connection.port == 22 else f"[{self.connection.host}]:{self.connection.port}"
        line = f"{where} {key.export_public_key('openssh').decode('ascii').strip()}"
        return UnknownServer("host key", key.get_fingerprint(), line)

    @override
    def connect(self) -> None:
        how = f"the key file {self.connection.key_file}" if self.connection.key_file else "a password" if self.password else "no password"
        self.log.asked(f"connect to {self.connection.host} port {self.connection.port} as {self.connection.user or '(no user)'} with {how}")
        try:
            self._await(self._open(), CONNECT_SECONDS * 2)
        except asyncssh.HostKeyNotVerifiable as error:
            self.log.failed("the server's host key is not trusted yet")
            raise self._await(self._unknown_server()) from error
        except ConnectionFailed as error:
            self.log.failed(str(error))
            raise
        except asyncssh.PermissionDenied as error:
            self.log.failed(LOGIN_TEXT)
            raise LoginRefused(LOGIN_TEXT) from error
        except (OSError, asyncssh.Error, RemoteActionFailed) as error:
            self.log.failed(f"{NOT_REACHED_TEXT} ({error})")
            raise ConnectionFailed(NOT_REACHED_TEXT) from error
        if self.log.wanted(NORMAL):
            try:
                info = self.server_info()
            except (ChronoFtpError, ValueError, AttributeError):
                info = ServerInfo()  # only the log misses it: the login stands
            self.log.answered(f"logged in: {info.software or 'SSH'}, {info.protocol}")
            for label, value in (*info.encryption, *info.identity):
                self.log.note(f"{label}: {value}", DEBUG)
            if info.offers:
                self.log.note(f"the server offers {', '.join(info.offers)}", DEBUG)
        handler = getattr(self._sftp, "_handler", None)  # asyncssh learns from the server's hello what it offers, and tells some only here
        durable = bool(getattr(handler, "_supports_fsync", False))
        hardlinks = bool(getattr(handler, "_supports_hardlink", False)) or self._sftp_now().version >= 6
        copies = bool(getattr(handler, "_supports_copy_data", False))
        atomic = bool(getattr(handler, "_supports_posix_rename", False))
        # SFTP itself can do these, with or without a shell
        self.capabilities = replace(self._detect(), resume=True, timestamps=True, rights=True, cut=True, durable=durable, server_copy=copies, symlinks=True, hardlinks=hardlinks, atomic_rename=atomic)

    def _detect(self) -> Capabilities:
        try:
            result = self._run(PROBE, PROBE_SECONDS)
        except RemoteActionFailed:
            self.log.note("no shell for this login: SFTP only")
            return Capabilities()  # SFTP without a shell
        lines = result.output.splitlines()
        lines = lines[lines.index(PROBE_MARK) + 1 :] if PROBE_MARK in lines else []  # a login banner may come first
        if result.status != 0 or len(lines) < 2 or not lines[1].isdigit():
            return Capabilities()  # a shell that is not POSIX: nothing can be assumed
        tools = frozenset(line.removeprefix("tool:") for line in lines[2:] if line.startswith("tool:"))
        return Capabilities(shell=True, system=lines[0], tools=tools, is_root=lines[1] == "0")

    @override
    def close(self) -> None:
        if self._closed:
            return
        client = self._client
        if client is not None:
            self.log.note("disconnect")
        self._client = None
        self._sftp = None

        async def finish() -> None:
            if client is not None:
                client.close()
                await client.wait_closed()
            pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        try:
            self._await(finish(), CLOSE_SECONDS)
        except (RemoteActionFailed, asyncssh.Error, OSError):
            pass  # it was gone already
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(CLOSE_SECONDS)
        if not self._loop.is_running():
            self._loop.close()

    @override
    def check(self) -> None:
        client = self._client
        if client is None or client.is_closed():
            raise ConnectionLost(LOST_TEXT)

    def _client_now(self) -> asyncssh.SSHClientConnection:
        if self._client is None:
            raise RemoteActionFailed("The connection is closed.")
        return self._client

    def _sftp_now(self) -> asyncssh.SFTPClient:
        if self._sftp is None:
            raise RemoteActionFailed("The connection is closed.")
        return self._sftp

    def _sftp_call[T](self, work: Coroutine[object, object, T], *paths: str) -> T:
        """One SFTP request, written to the session log with the paths it names and the server's answer (FP-02)."""
        self.log.asked(" ".join((_operation(work), *paths)))
        try:
            result = self._await(work)
        except LOST_ERRORS as error:
            self.log.failed(LOST_TEXT)
            raise ConnectionLost(LOST_TEXT) from error
        except (asyncssh.Error, OSError) as error:
            text = str(error) or "The server refused this action."
            self.log.failed(text)
            raise RemoteActionFailed(text) from error
        except ConnectionLost as error:
            self.log.failed(str(error))
            raise
        self.log.answered(_answer_of(result))
        return result

    @override
    def home(self) -> str:
        return _text(self._sftp_call(self._sftp_now().realpath("."), "."))

    @override
    def list_folder(self, path: str) -> list[RemoteEntry]:
        return self.list_folder_all(path)[0]

    @override
    def list_folder_all(self, path: str) -> tuple[list[RemoteEntry], list[str]]:
        names = self._sftp_call(self._sftp_now().readdir(path), path)
        entries: list[RemoteEntry] = []
        left_out: list[str] = []
        for item in names:
            name = _text(item.filename)
            if name in (".", ".."):
                continue
            if not safe_name(name):
                left_out.append(name)
                continue
            attributes = item.attrs
            kind = attributes.type
            is_link = kind == asyncssh.FILEXFER_TYPE_SYMLINK
            is_folder = kind == asyncssh.FILEXFER_TYPE_DIRECTORY
            target = ""
            if is_link:
                try:
                    attributes = self._sftp_call(self._sftp_now().stat(joined(path, name)), joined(path, name))  # what the link leads to: that is what a transfer reads
                    is_folder = attributes.type == asyncssh.FILEXFER_TYPE_DIRECTORY
                except RemoteActionFailed as error:
                    if isinstance(error, ConnectionLost):
                        raise
                    is_folder = False  # a broken link is shown as a file, with the link's own details
                try:
                    target = _text(self._sftp_call(self._sftp_now().readlink(joined(path, name)), joined(path, name)))
                except RemoteActionFailed as error:
                    if isinstance(error, ConnectionLost):
                        raise
            size_known = isinstance(attributes.size, int) and attributes.size >= 0
            size = attributes.size if isinstance(attributes.size, int) and attributes.size >= 0 else 0
            owner = _text(attributes.owner) or ("" if attributes.uid is None else str(attributes.uid))
            group = _text(attributes.group) or ("" if attributes.gid is None else str(attributes.gid))
            modified = None if attributes.mtime is None else float(attributes.mtime)
            entries.append(RemoteEntry(name, is_folder, size, modified, _modes(attributes.permissions), owner, group, is_link, target, size_known, special=_special(attributes)))
        log_listing(self.log, entries, left_out)
        return entries, left_out

    @override
    def rename(self, old: str, new: str) -> None:
        self._sftp_call(self._sftp_now().rename(old, new), old, new)

    @override
    def can_split(self, upload: bool) -> bool:
        return True  # SFTP reads and writes at any place, both ways

    @override
    def another_login(self) -> "SshConnection":
        """A login of its own (an SSH connection of its own, with what this one logged in with): one SSH connection shares one line, so
        a range over it would gain nothing."""
        other = SshConnection(self.connection, self.password, self.known_hosts)
        try:
            other.connect()
        except BaseException:
            other.close()
            raise
        return other

    @override
    def download_range(self, remote: str, local: str, start: int, end: int, progress: Progress, limit: Limit = 0) -> None:
        self._run_copy(self._copy_range(remote, local, False, start, end, progress, limit), f"download {remote} bytes {start} to {end} into {local}")

    @override
    def upload_range(self, local: str, remote: str, start: int, end: int, progress: Progress, limit: Limit = 0) -> None:
        self._run_copy(self._copy_range(local, remote, True, start, end, progress, limit), f"upload {local} bytes {start} to {end} to {remote}")

    @override
    def make_empty(self, remote: str) -> None:
        async def make() -> None:
            async with await _opened(self._sftp_now(), remote, "xb"):
                pass

        try:
            self._run_copy(make(), f"make the empty file {remote}")
        except CreateRefused as refused:
            if self.details(remote) is not None:
                raise NameTaken(NAME_TAKEN_TEXT) from refused.__cause__
            raise RemoteActionFailed(str(refused.__cause__) or "The server refused to make the file.") from refused.__cause__

    async def _copy_range(self, source: str, target: str, to_server: bool, start: int, end: int, progress: Progress, limit: Limit) -> None:
        """The bytes [start, end) of source written at the same place in target, which exists: many requests at once, as a whole copy
        (D-010). Prefix counts only bytes with no gap before them; on a failure nothing is cut back, as other ranges of the file may lie
        after this one: what is past the counted bytes is written again by the next try."""
        sftp = self._sftp_now()
        block = BLOCK_SIZE if limit_now(limit) else max(BLOCK_SIZE, min(sftp.limits.max_read_len, sftp.limits.max_write_len))  # with a limit
        depth = max(FEWEST_REQUESTS, min(IN_FLIGHT_BYTES // block, MOST_REQUESTS))  # small blocks, so it holds from the first one
        prefix = Prefix(start, progress)

        async def nothing() -> None:
            return None

        if to_server:
            async with sftp.open(target, "r+b") as remote:
                with LocalFile(source, "rb", False) as local:

                    async def write_at(place: int, data: bytes) -> tuple[int, int]:
                        await remote.write(data, place)
                        return place, len(data)

                    def next_request(place: int) -> asyncio.Task[tuple[int, int]] | None:
                        if place >= end:
                            return None
                        local.seek(place)
                        data = local.read(min(block, end - place))
                        return asyncio.create_task(write_at(place, data)) if data else None

                    await self._pipeline(next_request, start, block, depth, prefix, nothing, progress, limit)
        else:
            async with sftp.open(source, "rb") as remote:
                with LocalFile(target, "r+b", True) as local:
                    ended: list[bool] = []

                    async def read_at(place: int, length: int) -> tuple[int, int]:
                        got = b""
                        while len(got) < length:  # a server may answer a read with less than was asked
                            data = cast(bytes, await remote.read(length - len(got), place + len(got)))
                            if not data:
                                ended.append(True)  # the source ended before this range did
                                break
                            got += data
                        local.seek(place)  # no await between: no other request writes in between
                        local.write(got)
                        return place, len(got)

                    def next_request(place: int) -> asyncio.Task[tuple[int, int]] | None:
                        if place >= end or ended:
                            return None
                        return asyncio.create_task(read_at(place, min(block, end - place)))

                    await self._pipeline(next_request, start, block, depth, prefix, nothing, progress, limit)
        if prefix.end != end:
            raise RemoteActionFailed(SHORT_TEXT)  # the source ended earlier than its size said

    def _run_copy(self, work: Coroutine[object, object, None], what: str) -> None:
        """A copy of a range, or the making of a file, with the server's answers in this program's words; what: for the session log."""
        self.log.asked(what)
        try:
            self._await(work, None)
        except LOST_ERRORS as error:
            self.log.failed(LOST_TEXT)
            raise ConnectionLost(LOST_TEXT) from error
        except CreateRefused as error:
            self.log.failed(str(error.__cause__) or "refused")
            raise
        except (asyncssh.SFTPNoSpaceOnFilesystem, asyncssh.SFTPQuotaExceeded) as error:
            self.log.failed(str(error) or "no room")
            raise RefusedForNow(NO_ROOM_TEXT.format(reply=str(error) or "no room")) from error
        except asyncssh.SFTPFailure as error:
            self.log.failed(str(error) or "Failure")
            raise RefusedForNow(STOPPED_TAKING_TEXT.format(reply=str(error) or "Failure")) from error
        except (asyncssh.Error, OSError) as error:
            self.log.failed(str(error) or "The transfer did not finish.")
            raise RemoteActionFailed(str(error) or "The transfer did not finish.") from error
        except ChronoFtpError as error:
            self.log.failed(str(error))
            raise
        self.log.answered("done")

    @override
    def place_new(self, temporary: str, target: str) -> None:
        """SFTP's rename never replaces: its refusal, when the name was taken meanwhile, is that and nothing else."""
        try:
            self.rename(temporary, target)
        except ConnectionLost:
            raise
        except RemoteActionFailed as error:
            if self.details(target) is not None:
                raise NameTaken(NAME_TAKEN_TEXT) from error
            raise

    @override
    def resolved(self, path: str) -> str | None:
        try:
            return _text(self._sftp_call(self._sftp_now().realpath(path), path))
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return None

    @override
    def set_mode(self, path: str, mode: str) -> None:
        try:
            self._sftp_call(self._sftp_now().chmod(path, int(mode, 8)), path, mode)
        except (RemoteActionFailed, ValueError):
            pass  # the file is there; only its permissions could not be set

    @override
    def make_folder(self, path: str) -> None:
        self._sftp_call(self._sftp_now().mkdir(path), path)

    @override
    def delete(self, path: str, is_folder: bool) -> None:
        if not is_folder:
            self._sftp_call(self._sftp_now().remove(path), path)
        elif self.capabilities.has("rm"):
            result = self.run(f"rm -rf -- {shlex.quote(path)}")
            if result.status != 0:
                raise RemoteActionFailed(result.errors.strip() or "The server refused this action.")
        else:
            self._sftp_call(self._sftp_now().rmtree(path), path)

    async def _copy_pipelined(self, source: str, target: str, to_server: bool, progress: Progress, offset: int, append: bool, exclusive: bool = False, limit: Limit = 0) -> None:
        """Without a limit many requests travel at once, as deep as asyncssh's own copy, which is much faster on a distant server (D-010).
        They may end in any order, so a failed one can leave a gap with written bytes after it. Prefix counts only the bytes with no gap
        before them; on a failure or a stop the target is cut to exactly those, so a kept part never holds a gap. The copy is complete
        only when those bytes reach the whole size. With offset the target is first cut to offset and written on from there; with
        append the whole source goes after the target's end; on a failure or a stop a download is cut back to what it was, and an upload
        is left for its caller to set back, which knows what else may be in that file. A limit set meanwhile (limit) is kept from then on:
        one request at a time, with a pause after each."""
        sftp = self._sftp_now()
        block = max(BLOCK_SIZE, min(sftp.limits.max_read_len, sftp.limits.max_write_len))
        depth = max(FEWEST_REQUESTS, min(IN_FLIGHT_BYTES // block, MOST_REQUESTS))
        if to_server:
            async with await _opened(sftp, target, "r+b" if append or offset else "xb" if exclusive else "wb") as remote:
                if offset:
                    await _cut_to(remote, offset)
                start = ((await remote.stat()).size or 0) if append else offset
                prefix = Prefix(start, progress)
                read_up_to = start
                with LocalFile(source, "rb", False) as local:
                    local.seek(0 if append else offset)

                    async def write_at(place: int, data: bytes) -> tuple[int, int]:
                        await remote.write(data, place)
                        return place, len(data)

                    def next_request(place: int) -> asyncio.Task[tuple[int, int]] | None:
                        nonlocal read_up_to
                        data = local.read(block)
                        if not data:
                            return None
                        read_up_to = place + len(data)  # the end of what was read so far: what the server must hold, gap-free
                        return asyncio.create_task(write_at(place, data))

                    async def undo() -> None:
                        if not append:  # an append is set back by the caller, only where nothing but it can be in the file
                            await remote.truncate(prefix.end)

                    await self._pipeline(next_request, start, block, depth, prefix, undo, progress, limit)
                    if prefix.end != read_up_to:
                        await undo()
                        raise RemoteActionFailed(SHORT_TEXT)
        else:
            async with sftp.open(source, "rb") as remote:
                size = (await remote.stat()).size
                if size is None:
                    await self._copy_blocks(source, target, False, progress, limit, offset, append, exclusive)  # no size to plan requests by: one by one
                    return
                with LocalFile(target, "r+b" if append or offset else "xb" if exclusive else "wb", True) as local:
                    if offset:
                        local.truncate(offset)
                    shift = local.seek(0, 2) if append else 0  # where remote byte 0 lands here
                    start = 0 if append else offset
                    prefix = Prefix(start, progress)
                    ended: list[bool] = []  # the source ended before its size said: no more requests

                    async def read_at(place: int, length: int) -> tuple[int, int]:
                        got = b""
                        while len(got) < length:  # a server may answer a read with less than was asked
                            data = cast(bytes, await remote.read(length - len(got), place + len(got)))
                            if not data:
                                ended.append(True)  # the end of the source: a size that said more was wrong, or the file shrank
                                break
                            got += data
                        local.seek(place + shift)  # no await between: no other request writes in between
                        local.write(got)
                        return place, len(got)

                    def next_request(place: int) -> asyncio.Task[tuple[int, int]] | None:
                        if place >= size or ended:
                            return None
                        return asyncio.create_task(read_at(place, min(block, size - place)))

                    async def undo() -> None:
                        local.truncate((start if append else prefix.end) + shift)

                    await self._pipeline(next_request, start, block, depth, prefix, undo, progress, limit)
                    if prefix.end != size:
                        await undo()  # the source ended earlier than its size said: never a copy with a gap put in place
                        raise RemoteActionFailed(SHORT_TEXT)

    async def _pipeline(self, next_request: Callable[[int], "asyncio.Task[tuple[int, int]] | None"], start: int, block: int, depth: int, prefix: Prefix, undo: Callable[[], Coroutine[object, object, None]], progress: Progress, limit: Limit = 0) -> None:
        pending: set[asyncio.Task[tuple[int, int]]] = set()
        place = start
        more = True
        try:
            while True:
                while more and len(pending) < (1 if limit_now(limit) else depth):
                    task = next_request(place)
                    if task is None:
                        more = False
                        break
                    pending.add(task)
                    place += block
                if not pending:
                    return
                finished, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                failures = [error for task in finished if (error := task.exception()) is not None]
                for task in finished:
                    if task.exception() is None:
                        prefix.written(*task.result())  # every request that ended well counts, also one that ended with a failed one
                if failures:
                    raise failures[0]
                for pause in limit_pauses(limit, sum(task.result()[1] for task in finished)):
                    await asyncio.sleep(pause)
                    progress(0)  # a stop meanwhile ends it now
        except BaseException:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            try:
                await undo()  # nothing after the bytes with no gap stays (an appended download: nothing of it stays)
            except (asyncssh.Error, OSError, LocalActionFailed):
                pass  # the connection is gone: a part keeps its tail, which the next try cuts before it goes on
            raise

    async def _copy_blocks(self, source: str, target: str, to_server: bool, progress: Progress, limit: Limit, offset: int, append: bool, exclusive: bool = False) -> None:
        """With a limit: block after block, with a pause after each, so progress is always the bytes written in order. With offset the
        target is first cut to offset and written on from there; with append the whole source goes after the target's end; on a failure
        or a stop a download is cut back to what it was, and an upload is left for its caller to set back."""
        sftp = self._sftp_now()
        size = BLOCK_SIZE
        if to_server:
            async with await _opened(sftp, target, "r+b" if append or offset else "xb" if exclusive else "wb") as remote:
                if offset:
                    await _cut_to(remote, offset)
                place = ((await remote.stat()).size or 0) if append else offset
                with LocalFile(source, "rb", False) as local:
                    local.seek(0 if append else offset)
                    while block := local.read(size):
                        await remote.write(block, place)
                        place += len(block)
                        progress(len(block))
                        await self._keep_to(limit, len(block), progress)
        else:
            async with sftp.open(source, "rb") as remote:
                with LocalFile(target, "r+b" if append or offset else "xb" if exclusive else "wb", True) as local:
                    if offset:
                        local.truncate(offset)
                    before = local.seek(0, 2)
                    place = 0 if append else offset
                    try:
                        while block := cast(bytes, await remote.read(size, place)):
                            local.write(block)
                            place += len(block)
                            progress(len(block))
                            await self._keep_to(limit, len(block), progress)
                    except BaseException:
                        if append:
                            try:
                                local.truncate(before)  # an append is all or nothing
                            except LocalActionFailed:
                                pass  # the first problem is the one to tell
                        raise

    @staticmethod
    async def _keep_to(limit: Limit, amount: int, progress: Progress) -> None:
        """The pause after a block that keeps to the limit as it is now; a stop meanwhile ends it at once."""
        await asyncio.sleep(0)  # also without a limit, other work of this connection gets its turn
        for pause in limit_pauses(limit, amount):
            await asyncio.sleep(pause)
            progress(0)

    def _transfer(self, source: str, target: str, to_server: bool, progress: Progress, limit: Limit, offset: int, append: bool, exclusive: bool = False) -> None:
        """exclusive: the file is made only where nothing is; only a refusal of that very making can mean the name is taken. A failure
        while writing is that, whatever the server calls it: never a taken name. A limit may change on the way: it is asked again for every
        block."""
        if limit_now(limit):
            work = self._copy_blocks(source, target, to_server, progress, limit, offset, append, exclusive)
        else:
            work = self._copy_pipelined(source, target, to_server, progress, offset, append, exclusive, limit)
        how = " (appended)" if append else f" (from byte {offset})" if offset else ""
        self.log.asked(f"{'upload' if to_server else 'download'} {source} to {target}{how}")
        self.log.note(f"speed limit {limit_now(limit)} KB/s" if limit_now(limit) else "no speed limit: many requests at once", DEBUG)
        try:
            self._await(work, None)
        except LOST_ERRORS as error:
            self.log.failed(LOST_TEXT)
            raise ConnectionLost(LOST_TEXT) from error
        except CreateRefused as refused:
            error = refused.__cause__
            self.log.failed(str(error) or "refused to make the file")
            if self.details(target) is not None:
                raise NameTaken(NAME_TAKEN_TEXT) from error  # made only where nothing was: something has that name now
            raise RemoteActionFailed(str(error) or "The server refused to make the file.") from error  # not allowed here
        except (asyncssh.SFTPNoSpaceOnFilesystem, asyncssh.SFTPQuotaExceeded) as error:
            self.log.failed(str(error) or "no room")
            raise RefusedForNow(NO_ROOM_TEXT.format(reply=str(error) or "no room")) from error
        except asyncssh.SFTPFailure as error:  # what OpenSSH answers also for a full disk: it says no more; what arrived is still good
            self.log.failed(str(error) or "Failure")
            raise RefusedForNow(STOPPED_TAKING_TEXT.format(reply=str(error) or "Failure")) from error
        except (asyncssh.Error, OSError) as error:
            self.log.failed(str(error) or "The transfer did not finish.")
            raise RemoteActionFailed(str(error) or "The transfer did not finish.") from error
        except ChronoFtpError as error:
            self.log.failed(str(error))  # this computer's file, or a stop
            raise
        self.log.answered("done")

    @override
    def download(self, remote: str, local: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        self._transfer(remote, local, False, progress, limit, offset, append, exclusive)

    @override
    def upload(self, local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        self._transfer(local, remote, True, progress, limit, offset, append, exclusive)

    @override
    def set_rights(self, path: str, mode: str) -> None:
        self._sftp_call(self._sftp_now().chmod(path, int(mode, 8)), path, mode)

    @override
    def set_time(self, path: str, modified: float) -> None:
        """A date SFTP can't carry is refused like any refusal of the server, so the file still arrives with the server's own date."""
        if not math.isfinite(modified) or not 0 <= modified < HIGHEST_TIME:
            raise RemoteActionFailed(NO_TIME_TEXT)
        try:
            self._sftp_call(self._sftp_now().utime(path, (modified, modified)), path)
        except (OverflowError, ValueError, struct.error) as error:
            raise RemoteActionFailed(NO_TIME_TEXT) from error

    @override
    def file_size(self, path: str) -> int | None:
        size = self._sftp_call(self._sftp_now().stat(path), path).size
        return size if isinstance(size, int) and size >= 0 else None

    @override
    def details(self, path: str) -> RemoteEntry | None:
        """One file through a link as the listing shows it: what a transfer reads."""
        try:
            return self.details_or_refusal(path)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return None

    @override
    def name_taken(self, path: str) -> bool:
        """Whether anything has this name: the name itself (lstat), so a link that leads nowhere has it too. A refusal to tell is raised."""
        try:
            self._sftp_call(self._sftp_now().lstat(path), path)
        except ConnectionLost:
            raise
        except RemoteActionFailed as error:
            if isinstance(error.__cause__, asyncssh.SFTPNoSuchFile):
                return False
            raise
        return True

    @override
    def details_or_refusal(self, path: str) -> RemoteEntry | None:
        """None only where the server says there is no such file; a refusal (no permission to look) is raised as one."""
        try:
            attributes = self._sftp_call(self._sftp_now().stat(path), path)
        except ConnectionLost:
            raise
        except RemoteActionFailed as error:
            if isinstance(error.__cause__, asyncssh.SFTPNoSuchFile):
                return None
            raise
        size = attributes.size if isinstance(attributes.size, int) and attributes.size >= 0 else 0
        modified = None if attributes.mtime is None else float(attributes.mtime)
        name = path.rsplit("/", 1)[-1]
        return RemoteEntry(name, attributes.type == asyncssh.FILEXFER_TYPE_DIRECTORY, size, modified, _modes(attributes.permissions), size_known=isinstance(attributes.size, int) and attributes.size >= 0, special=_special(attributes))

    @override
    def make_durable(self, path: str) -> None:
        """fsync@openssh.com where the server offers it; a server without it can't be asked, which is no refusal."""

        async def flush() -> None:
            async with self._sftp_now().open(path, "rb") as file:
                await file.fsync()

        try:
            self._await(flush(), None)  # a large file may take a while to reach the disk: no answer limit while the connection lives
        except asyncssh.SFTPOpUnsupported:
            return
        except LOST_ERRORS as error:
            raise ConnectionLost(LOST_TEXT) from error
        except (asyncssh.Error, OSError) as error:
            raise RemoteActionFailed(str(error) or "The server could not confirm that the file is on its disk.") from error

    @override
    def cut(self, path: str, length: int) -> None:
        self._sftp_call(self._sftp_now().truncate(path, length), path, str(length))

    @override
    def remove_empty_folder(self, path: str) -> None:
        try:
            self._sftp_call(self._sftp_now().rmdir(path), path)
        except ConnectionLost:
            raise
        except RemoteActionFailed as error:
            raise RemoteActionFailed(NOT_EMPTY_TEXT) from error

    @override
    def run(self, command: str) -> CommandResult:
        return self._run(command, COMMAND_SECONDS)

    @override
    def make_link(self, target: str, path: str, hard: bool = False) -> None:
        sftp = self._sftp_now()
        if self.name_taken(path):
            raise NameTaken(NAME_TAKEN_TEXT)  # a link never replaces what has its name
        if hard:
            if not self.capabilities.hardlinks:
                raise RemoteActionFailed(NO_HARD_LINKS_TEXT)
            self._sftp_call(sftp.link(target, path), target, path)
        else:
            self._sftp_call(sftp.symlink(target, path), target, path)

    @override
    def replace_atomically(self, source: str, target: str) -> None:
        if not self.capabilities.atomic_rename:
            raise RemoteActionFailed(NO_ATOMIC_TEXT)
        self._sftp_call(self._sftp_now().posix_rename(source, target), source, target)

    @override
    def copy_file(self, source: str, target: str) -> None:
        """The server copies the file itself (copy-data): nothing travels through this computer. The copy is made only where nothing is,
        and keeps the rights of the original."""
        if not self.capabilities.server_copy:
            raise RemoteActionFailed(NO_COPY_TEXT)
        sftp = self._sftp_now()

        made_here: list[bool] = []  # the copy this call made itself: only that one is removed after a failure

        async def copy() -> None:
            async with await sftp.open(source, "rb", block_size=0) as original:
                rights = (await original.stat()).permissions
                async with await _opened(sftp, target, "xb") as made:
                    made_here.append(True)
                    await sftp.remote_copy(original, made)
            if rights is not None:
                try:
                    await sftp.chmod(target, rights & 0o7777)
                except asyncssh.SFTPError:
                    pass  # the copy is whole; only the rights of the original could not be given to it

        try:
            self._run_copy(copy(), f"copy-data {source} to {target}")
        except CreateRefused as refused:
            if self.name_taken(target):
                raise NameTaken(NAME_TAKEN_TEXT) from refused.__cause__
            raise RemoteActionFailed(str(refused.__cause__) or "The server refused to make the copy.") from refused.__cause__
        except RemoteActionFailed as error:
            if made_here:
                self._remove_half_copy(target, error)
            raise

    def _remove_half_copy(self, target: str, error: RemoteActionFailed) -> None:
        """A copy that failed halfway goes again; when it can't, because the connection is gone, the message says it is still there."""
        try:
            self._sftp_call(self._sftp_now().remove(target), target)
        except RemoteActionFailed as problem:
            text = f"{error} {HALF_COPY_LEFT_TEXT.format(path=target)}"
            raise (ConnectionLost(text) if isinstance(problem, ConnectionLost) or isinstance(error, ConnectionLost) else RemoteActionFailed(text)) from error

    @override
    def change_password(self, old: str, new: str) -> PasswordAnswer:
        """passwd asks in a terminal of its own; each question is answered as it comes, and the talk ends when passwd asks more than it
        does when all goes well. The passwords go to passwd only: they are never in what is returned, nor written anywhere."""
        if not self.capabilities.has("passwd"):
            raise RemoteActionFailed(NO_PASSWORD_TEXT)
        self.log.hide(old)  # neither is ever written, whatever passwd or the server answers (OR-19)
        self.log.hide(new)
        self.log.asked(PASSWD_COMMAND)
        talk = PasswordTalk(old, new)

        async def go() -> PasswordAnswer:
            process = await self._client_now().create_process(PASSWD_COMMAND, term_type="dumb", encoding="utf-8", errors="replace")
            heard: list[str] = []
            screen = ""
            try:
                while True:
                    chunk = await asyncio.wait_for(process.stdout.read(PASSWORD_READ_BYTES), PASSWORD_ANSWER_SECONDS)
                    if not chunk:
                        break
                    heard.append(chunk)
                    screen += chunk
                    answer = talk.reply(screen)
                    if answer is not None:
                        process.stdin.write(answer + "\n")
                        screen = ""
            except AskedTooOften:
                process.terminate()
                return PasswordAnswer(False, shown_answer("".join(heard), (old, new)) or PASSWORD_REFUSED_TEXT)
            except TimeoutError:
                process.terminate()
                unknown = PASSWORD_UNKNOWN_TEXT if talk.new_given == 2 else PASSWORD_NO_ANSWER_TEXT  # both asked: it may have changed
                return PasswordAnswer(False, shown_answer("".join(heard), (old, new)) or unknown)
            try:
                done = await asyncio.wait_for(process.wait(), PASSWORD_ANSWER_SECONDS)
            except TimeoutError:
                process.terminate()
                return PasswordAnswer(False, PASSWORD_UNKNOWN_TEXT)  # it never said it was done: the stored password stays
            text = shown_answer("".join(heard), (old, new))
            return PasswordAnswer(done.exit_status == 0, text if text or done.exit_status == 0 else PASSWORD_REFUSED_TEXT)

        try:
            answer = self._await(go(), PASSWORD_ANSWER_SECONDS * 4)
            self.log.answered("the password was changed" if answer.changed else "the password was not changed")
            return answer
        except (ConnectionLost, *LOST_ERRORS) as error:
            # both new passwords were typed: the server may have taken the new one before the line went
            lost = str(error) if isinstance(error, ConnectionLost) else LOST_TEXT
            raise ConnectionLost(f"{lost} {PASSWORD_UNKNOWN_TEXT}" if talk.new_given == 2 else lost) from error
        except asyncssh.ChannelOpenError as error:
            raise RemoteActionFailed(PASSWORD_NO_SESSION_TEXT) from error  # the server opened no session for passwd: no shell here
        except (asyncssh.Error, OSError) as error:
            raise RemoteActionFailed(PASSWORD_FAILED_TEXT.format(reason=str(error) or type(error).__name__)) from error

    @override
    def run_briefly(self, command: str, seconds: float) -> CommandResult:
        """The command is ended on the server after seconds, and the connection goes on: a slow directory service never holds the
        tab's thread."""

        async def go() -> CommandResult:
            try:
                result = await asyncio.wait_for(self._client_now().run(sendable(command), check=False, stdin=asyncssh.DEVNULL, encoding="utf-8", errors="surrogateescape"), seconds)
            except TimeoutError as error:
                raise RemoteActionFailed(SLOW_COMMAND_TEXT.format(seconds=round(seconds))) from error
            return CommandResult(result.exit_status if result.exit_status is not None else -1, _text(cast("bytes | str | None", result.stdout)), _text(cast("bytes | str | None", result.stderr)))

        self.log.asked(f"run {command}")
        try:
            result = self._await(go(), seconds + CLOSE_SECONDS)
        except LOST_ERRORS as error:
            self.log.failed(LOST_TEXT)
            raise ConnectionLost(LOST_TEXT) from error
        except (asyncssh.Error, OSError) as error:
            self.log.failed(str(error) or "refused")
            raise RemoteActionFailed(str(error) or "The server refused this command.") from error
        except RemoteActionFailed as error:
            self.log.failed(str(error))
            raise
        self._log_result(result)
        return result

    def _run(self, command: str, seconds: float) -> CommandResult:
        async def go() -> CommandResult:
            result = await self._client_now().run(sendable(command), check=False, stdin=asyncssh.DEVNULL, encoding="utf-8", errors="surrogateescape")
            return CommandResult(result.exit_status if result.exit_status is not None else -1, _text(cast("bytes | str | None", result.stdout)), _text(cast("bytes | str | None", result.stderr)))

        self.log.asked(f"run {command}")
        try:
            result = self._await(go(), seconds)
        except LOST_ERRORS as error:
            self.log.failed(LOST_TEXT)
            raise ConnectionLost(LOST_TEXT) from error
        except (asyncssh.Error, OSError) as error:
            self.log.failed(str(error) or "no shell")
            raise RemoteActionFailed(str(error) or "The server has no shell for this login.") from error
        except ConnectionLost as error:
            self.log.failed(str(error))
            raise
        self._log_result(result)
        return result

    def _log_result(self, result: CommandResult) -> None:
        if not self.log.wanted(NORMAL):
            return  # a large output is never split for nothing
        self.log.answered(f"exit status {result.status}")
        self.log.answered_lines(result.output.splitlines())
        self.log.answered_lines(result.errors.splitlines())

    @override
    def read_file(self, path: str, most: int, keep_going: KeepGoing) -> bytes | None:
        """Block by block, so a stop waits for one block at most; nothing is written on this computer."""
        sftp = self._sftp_now()

        async def read() -> bytes | None:
            async with sftp.open(path, "rb") as remote:
                parts: list[bytes] = []
                place = 0
                while keep_going():
                    block = cast(bytes, await remote.read(BLOCK_SIZE, place))
                    if not block:
                        return b"".join(parts)
                    place += len(block)
                    if place > most:
                        raise RemoteActionFailed(TOO_LARGE_TEXT.format(most=most))
                    parts.append(block)
                return None

        self.log.asked(f"read {path} into memory, at most {most} bytes")
        try:
            found = self._await(read())
        except LOST_ERRORS as error:
            self.log.failed(LOST_TEXT)
            raise ConnectionLost(LOST_TEXT) from error
        except (asyncssh.Error, OSError) as error:
            text = str(error) or "The server refused to read this file."
            self.log.failed(text)
            raise RemoteActionFailed(text) from error
        except ChronoFtpError as error:
            self.log.failed(str(error))
            raise
        self.log.answered("stopped" if found is None else f"{len(found)} bytes")
        return found

    @override
    def server_info(self) -> ServerInfo:
        """The SSH banner, the encryption agreed and the host key, as asyncssh learnt them on connecting (FP-03)."""
        client = self._client
        sftp = self._sftp
        if client is None or sftp is None:
            raise ConnectionLost(LOST_TEXT)

        def extra(name: str) -> str:
            value: object = client.get_extra_info(name)
            return str(value) if value else ""

        def both(sending: str, receiving: str) -> str:
            out, back = extra(sending), extra(receiving)
            return out if out == back else f"{out or 'none'} to the server, {back or 'none'} from it"

        encryption: list[tuple[str, str]] = [("Cipher", both("send_cipher", "recv_cipher"))]
        mac = both("send_mac", "recv_mac")
        encryption.append(("MAC", mac if mac and mac != "none" else "part of the cipher"))
        encryption.append(("Key exchange", self._witness.kex or "unknown"))
        encryption.append(("Compression", both("send_compression", "recv_compression") or "none"))
        identity: list[tuple[str, str]] = []
        key = client.get_server_host_key()
        if key is not None:
            identity.append(("Host key type", key.get_algorithm()))
            identity.append(("Fingerprint (SHA-256)", key.get_fingerprint("sha256")))
            try:
                identity.append(("Fingerprint (MD5)", key.get_fingerprint("md5")))
            except ValueError:
                pass  # a Python without MD5: the SHA-256 fingerprint says it all
        handler = getattr(sftp, "_handler", None)
        offers = tuple(name for flag, name in EXTENSIONS if getattr(handler, flag, False))
        return ServerInfo(f"SFTP version {sftp.version} over SSH-2", extra("server_version"), tuple(encryption), tuple(identity), offers)

    @override
    def free_space(self, path: str) -> FreeSpace | None:
        """statvfs@openssh.com where the server offers it, else df where a shell has it; None where neither can tell."""
        handler = getattr(self._sftp, "_handler", None)
        if getattr(handler, "_supports_statvfs", False):
            try:
                found = self._sftp_call(self._sftp_now().statvfs(path), path)
                unit = found.frsize or found.bsize
                if not isinstance(unit, int) or unit <= 0:
                    return None  # an answer without a block size tells nothing (OR-15)
                return FreeSpace(size_from(found.bavail * unit), size_from(found.blocks * unit), "statvfs@openssh.com")
            except ConnectionLost:
                raise
            except (RemoteActionFailed, TypeError):
                pass  # refused for this folder: df may still tell
        if self.capabilities.has("df"):
            try:
                result = self._run(f"df -Pk -- {shlex.quote(path)}", PROBE_SECONDS)
            except ConnectionLost:
                raise
            except RemoteActionFailed:
                return None
            return space_from_df(result.output) if result.status == 0 else None
        return None
