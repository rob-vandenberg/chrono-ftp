import asyncio
import shlex
import threading
from collections.abc import Coroutine
from concurrent.futures import CancelledError
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import cast, override

import asyncssh

from chrono_ftp.connections import trust
from chrono_ftp.connections.base import Capabilities, CommandResult, Progress, RemoteConnection, RemoteEntry, UnknownServer, joined, safe_name
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.errors import ConnectionFailed, ConnectionLost, LoginRefused, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "ssh.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Names that are not valid UTF-8 keep their real bytes, in paths and in commands
# v0.0.5: Initial: browse over SFTP and run commands over the same login (D-010); capabilities are detected (D-016)

CONNECT_SECONDS: float = 20.0
ANSWER_SECONDS: float = 60.0
CLOSE_SECONDS: float = 3.0
KEEPALIVE_SECONDS: float = 20.0
KEEPALIVE_TRIES: int = 3
COMMAND_SECONDS: float = 3600.0  # a recursive change on a big tree runs long
BLOCK_SIZE: int = 64 * 1024
TOOLS: tuple[str, ...] = ("rsync", "find", "chown", "chmod", "cp", "mv", "rm", "sha256sum")
PROBE_MARK: str = "chrono-ftp-probe"
PROBE: str = f"echo {PROBE_MARK}; uname -s; id -u; for tool in " + " ".join(TOOLS) + "; do command -v $tool >/dev/null 2>&1 && echo tool:$tool; done; exit 0"
PROBE_SECONDS: float = 15.0
CHANGED_TEXT: str = "This server's host key has changed since you trusted it, so Chrono FTP did not connect. If you know why, remove its line from known_hosts."
NOT_REACHED_TEXT: str = "The server could not be reached. Please check the host and the port."
LOGIN_TEXT: str = "The server did not accept this login. Please check the user, password or key file."


LOST_TEXT: str = "The connection to the server was lost."
LOST_ERRORS: tuple[type[BaseException], ...] = (asyncssh.ConnectionLost, asyncssh.SFTPConnectionLost, asyncssh.SFTPNoConnection, ConnectionError)


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


class SshConnection(RemoteConnection):
    def __init__(self, connection: Connection, password: str | None = None, known_hosts: str | None = None) -> None:
        self.connection: Connection = connection
        self.password: str | None = password
        self.known_hosts: str = known_hosts or str(trust.known_hosts_path())
        self.capabilities = Capabilities()
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread: threading.Thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._closed: bool = False
        self._client: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None

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
        options: dict[str, object] = {"host": self.connection.host, "port": self.connection.port, "known_hosts": self._host_key_files() or b"", "connect_timeout": CONNECT_SECONDS, "keepalive_interval": KEEPALIVE_SECONDS, "keepalive_count_max": KEEPALIVE_TRIES}
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
        try:
            self._await(self._open(), CONNECT_SECONDS * 2)
        except asyncssh.HostKeyNotVerifiable as error:
            raise self._await(self._unknown_server()) from error
        except ConnectionFailed:
            raise
        except asyncssh.PermissionDenied as error:
            raise LoginRefused(LOGIN_TEXT) from error
        except (OSError, asyncssh.Error, RemoteActionFailed) as error:
            raise ConnectionFailed(NOT_REACHED_TEXT) from error
        self.capabilities = self._detect()

    def _detect(self) -> Capabilities:
        try:
            result = self._run(PROBE, PROBE_SECONDS)
        except RemoteActionFailed:
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

    def _sftp_call[T](self, work: Coroutine[object, object, T]) -> T:
        try:
            return self._await(work)
        except LOST_ERRORS as error:
            raise ConnectionLost(LOST_TEXT) from error
        except (asyncssh.Error, OSError) as error:
            raise RemoteActionFailed(str(error) or "The server refused this action.") from error

    @override
    def home(self) -> str:
        return _text(self._sftp_call(self._sftp_now().realpath(".")))

    @override
    def list_folder(self, path: str) -> list[RemoteEntry]:
        names = self._sftp_call(self._sftp_now().readdir(path))
        entries: list[RemoteEntry] = []
        for item in names:
            name = _text(item.filename)
            if not safe_name(name):
                continue
            attributes = item.attrs
            kind = attributes.type
            is_link = kind == asyncssh.FILEXFER_TYPE_SYMLINK
            is_folder = kind == asyncssh.FILEXFER_TYPE_DIRECTORY
            target = ""
            if is_link:
                try:
                    is_folder = self._sftp_call(self._sftp_now().isdir(joined(path, name)))
                except RemoteActionFailed as error:
                    if isinstance(error, ConnectionLost):
                        raise
                    is_folder = False  # a broken link is shown as a file
                try:
                    target = _text(self._sftp_call(self._sftp_now().readlink(joined(path, name))))
                except RemoteActionFailed as error:
                    if isinstance(error, ConnectionLost):
                        raise
            size = attributes.size if isinstance(attributes.size, int) and attributes.size >= 0 else 0
            owner = _text(attributes.owner) or ("" if attributes.uid is None else str(attributes.uid))
            group = _text(attributes.group) or ("" if attributes.gid is None else str(attributes.gid))
            modified = None if attributes.mtime is None else float(attributes.mtime)
            entries.append(RemoteEntry(name, is_folder, size, modified, _modes(attributes.permissions), owner, group, is_link, target))
        return entries

    @override
    def rename(self, old: str, new: str) -> None:
        self._sftp_call(self._sftp_now().rename(old, new))

    @override
    def resolved(self, path: str) -> str | None:
        try:
            return _text(self._sftp_call(self._sftp_now().realpath(path)))
        except RemoteActionFailed:
            return None

    @override
    def set_mode(self, path: str, mode: str) -> None:
        try:
            self._sftp_call(self._sftp_now().chmod(path, int(mode, 8)))
        except (RemoteActionFailed, ValueError):
            pass  # the file is there; only its permissions could not be set

    @override
    def make_folder(self, path: str) -> None:
        self._sftp_call(self._sftp_now().mkdir(path))

    @override
    def delete(self, path: str, is_folder: bool) -> None:
        if not is_folder:
            self._sftp_call(self._sftp_now().remove(path))
        elif self.capabilities.has("rm"):
            result = self.run(f"rm -rf -- {shlex.quote(path)}")
            if result.status != 0:
                raise RemoteActionFailed(result.errors.strip() or "The server refused this action.")
        else:
            self._sftp_call(self._sftp_now().rmtree(path))

    async def _copy_limited(self, source: str, target: str, to_server: bool, progress: Progress, limit: int) -> None:
        """Read and write in blocks, with a pause after each block when there is a limit."""
        sftp = self._sftp_now()
        pause = BLOCK_SIZE / (limit * 1024) if limit else 0.0
        if to_server:
            async with sftp.open(target, "wb") as remote:
                with Path(source).open("rb") as local:
                    while block := local.read(BLOCK_SIZE):
                        await remote.write(block)
                        progress(len(block))
                        await asyncio.sleep(pause)
        else:
            async with sftp.open(source, "rb") as remote:
                with Path(target).open("wb") as local:
                    while block := cast(bytes, await remote.read(BLOCK_SIZE)):
                        local.write(block)
                        progress(len(block))
                        await asyncio.sleep(pause)

    async def _copy_fast(self, source: str, target: str, to_server: bool, progress: Progress) -> None:
        """Without a limit asyncssh keeps several requests in flight, which is much faster (D-010)."""
        reported = 0

        def step(_source: bytes, _target: bytes, done: int, _total: int) -> None:
            nonlocal reported
            progress(done - reported)
            reported = done

        sftp = self._sftp_now()
        await (sftp.put if to_server else sftp.get)(source, target, follow_symlinks=True, progress_handler=step)

    def _transfer(self, source: str, target: str, to_server: bool, progress: Progress, limit: int) -> None:
        work = self._copy_limited(source, target, to_server, progress, limit) if limit else self._copy_fast(source, target, to_server, progress)
        try:
            self._await(work, None)
        except LOST_ERRORS as error:
            raise ConnectionLost(LOST_TEXT) from error
        except (asyncssh.Error, OSError) as error:
            raise RemoteActionFailed(str(error) or "The transfer did not finish.") from error

    @override
    def download(self, remote: str, local: str, progress: Progress, limit: int = 0) -> None:
        self._transfer(remote, local, False, progress, limit)

    @override
    def upload(self, local: str, remote: str, progress: Progress, limit: int = 0) -> None:
        self._transfer(local, remote, True, progress, limit)

    @override
    def run(self, command: str) -> CommandResult:
        return self._run(command, COMMAND_SECONDS)

    def _run(self, command: str, seconds: float) -> CommandResult:
        async def go() -> CommandResult:
            result = await self._client_now().run(sendable(command), check=False, stdin=asyncssh.DEVNULL, encoding="utf-8", errors="surrogateescape")
            return CommandResult(result.exit_status if result.exit_status is not None else -1, _text(cast("bytes | str | None", result.stdout)), _text(cast("bytes | str | None", result.stderr)))

        try:
            return self._await(go(), seconds)
        except LOST_ERRORS as error:
            raise ConnectionLost(LOST_TEXT) from error
        except (asyncssh.Error, OSError) as error:
            raise RemoteActionFailed(str(error) or "The server has no shell for this login.") from error
