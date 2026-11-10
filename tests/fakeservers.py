import asyncio
import os
import re
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast, override

import asyncssh
import pytest
from chrono_ftp.connections import trust
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.filesystems import AbstractedFS
from pyftpdlib.handlers import DTPHandler, FTPHandler, TLS_FTPHandler
from pyftpdlib.servers import FTPServer

# --- Version ------------------------------------------------------------
__version__ = "fakeservers.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: The SSH server leaves nothing behind: its event loop and its thread end with its block, or at the end of the test when a
#          window still held a connection to it then (end_the_servers);
#          an FTP server that takes only so many logins at once, as servers that limit them do (FC-01); one that answers a download the
#          client ended early with a reply of choice (FC-05)
# v0.0.12: An FTP server without some commands (REST, MFMT, SITE CHMOD), as old servers are; one with a garbled SIZE reply; one that
#          drops the line at FEAT; one that offers REST and refuses to continue; a data connection that drops once; folders that take
#          files but can't be read; one that answers a STOR of some files with a reply of choice; SIZE where nothing can be read; links
#          listed as links with their own size; NOOP heard, and a connection closed at the first one; tests that need a refusal by the
#          rights of a file skip as root; an FTP server whose unix.mode holds the kind of each item; the FTP server's thread ends
#          before the server is closed
# v0.0.10: An FTP server without the listing option
# v0.0.5: Initial: a real SSH server and a real FTP server inside the test process

USER: str = "anna"
PASSWORD: str = "not-a-real-password"


class _Server(asyncssh.SSHServer):
    @override
    def begin_auth(self, username: str) -> bool:
        return True

    @override
    def password_auth_supported(self) -> bool:
        return True

    @override
    def validate_password(self, username: str, password: str) -> bool:
        return username == USER and password == PASSWORD


async def _shell(process: asyncssh.SSHServerProcess[str]) -> None:
    command = process.command or ""
    result = await asyncio.to_thread(subprocess.run, ["/bin/sh", "-c", command], capture_output=True, text=True, check=False)
    process.stdout.write(result.stdout)
    process.stderr.write(result.stderr)
    process.exit(result.returncode)


async def _no_shell(process: asyncssh.SSHServerProcess[str]) -> None:
    process.stderr.write("This service allows sftp connections only.\n")
    process.exit(1)


class SshServer:
    def __init__(self, port: int, key: asyncssh.SSHKey) -> None:
        self.port: int = port
        self.key: asyncssh.SSHKey = key


CLOSE_SECONDS: float = 5.0  # the end of a fake server never waits longer than this
ENDING: list[Callable[[], None]] = []  # the ends of fake SSH servers that still had a connection open when their block ended


@contextmanager
def ssh_server(root: Path, shell: bool = True) -> Generator[SshServer]:
    """Real SSH and SFTP on a free local port; every SFTP path is inside root. At the end of its block it takes no new connection; once
    none is open any more (else at the end of the test, end_the_servers) it closes its event loop and its thread: nothing stays behind."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    key = asyncssh.generate_private_key("ssh-ed25519")
    connections: list[asyncssh.SSHServerConnection] = []

    class Server(_Server):
        @override
        def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
            connections.append(conn)

    class Sftp(asyncssh.SFTPServer):
        def __init__(self, channel: asyncssh.SSHServerChannel[bytes]) -> None:
            super().__init__(channel, chroot=str(root).encode())

    async def start() -> asyncssh.SSHAcceptor:
        return await asyncssh.listen("127.0.0.1", 0, server_host_keys=[key], server_factory=Server, sftp_factory=Sftp, process_factory=_shell if shell else _no_shell, encoding="utf-8")

    acceptor = asyncio.run_coroutine_threadsafe(start(), loop).result(10)
    try:
        yield SshServer(acceptor.get_port(), key)
    finally:

        async def stop_listening() -> int:
            acceptor.close()  # no new connection; the listener is gone once the ones it has are closed too
            return sum(1 for connection in connections if not connection.is_closed())

        async def stop() -> None:
            for connection in connections:
                connection.close()
            await asyncio.wait_for(asyncio.gather(*(connection.wait_closed() for connection in connections), return_exceptions=True), CLOSE_SECONDS)
            await asyncio.wait_for(acceptor.wait_closed(), CLOSE_SECONDS)
            pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            await asyncio.wait_for(loop.shutdown_default_executor(), CLOSE_SECONDS)  # the thread that ran the shell's commands

        def finish() -> None:
            try:
                asyncio.run_coroutine_threadsafe(stop(), loop).result(CLOSE_SECONDS * 3)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                thread.join(CLOSE_SECONDS)
                assert not thread.is_alive(), "the fake SSH server did not stop"
                loop.close()

        if asyncio.run_coroutine_threadsafe(stop_listening(), loop).result(CLOSE_SECONDS):
            ENDING.append(finish)  # a window still holds a connection to it: it closes that at the test's end, and then this ends
        else:
            finish()


def end_the_servers() -> None:
    """When a test is over (conftest): every fake SSH server whose block ended while a connection to it was still open ends now, after
    the window of the test closed its connections: the connections it still has, then its event loop and its thread."""
    while ENDING:
        ENDING.pop()()


AS_ROOT: bool = cast(Callable[[], int], getattr(os, "geteuid", lambda: -1))() == 0
NOT_AS_ROOT: pytest.MarkDecorator = pytest.mark.skipif(AS_ROOT, reason="as root every file can be read and written, so the refusal this test needs never comes")


@contextmanager
def ftp_server(root: Path, trusted: bool = True, certificate: Path | None = None, rights: str = "elradfmwMT", refuses_listing_options: bool = False, without: tuple[str, ...] = (), size_reply: str = "", drops_at_feat: bool = False, refuses_restart: bool = False, drops_data_after: int = 0, write_only: tuple[str, ...] = (), refuses_store: tuple[tuple[str, str], ...] = (), sizes_anywhere: bool = False, links_as_links: bool = False, full_modes: bool = False, noop_heard: Callable[[], None] | None = None, drops_at_noop: bool = False, command_heard: Callable[[str], None] | None = None, most_logins: int = 0, early_end_reply: str = "") -> Generator[int]:
    """Real FTP on a free local port; with a certificate it offers TLS. trusted: the person already said yes to plain FTP on this port.
    without: commands this server does not know, so it does not offer them in FEAT or SITE HELP either. size_reply: what it answers to
    SIZE instead of the size. drops_at_feat: the line drops when the client asks what the server can do. refuses_restart: it offers
    REST, and then refuses a transfer that starts at a position. drops_data_after: the first data connection that carries a file drops
    after about this many bytes, with the reply 426, as a line that breaks does. write_only: folders that take files but can't be read.
    refuses_store: (start of a file name, reply): a file whose name starts so is answered with that reply instead of being taken.
    sizes_anywhere: SIZE is answered also in a folder that can't be read. links_as_links: a link is listed as a link, with its own size
    and date, as some servers do. full_modes: unix.mode holds the kind as well as the rights (0010644 for a pipe). noop_heard: called at every NOOP. drops_at_noop: the first NOOP closes the connection, as a server
    that closed an idle connection all the same. command_heard: called with the name of every command. most_logins: it takes no more
    connections at once than this (0: any number); one more is answered with 421 and closed, as servers that limit them do.
    early_end_reply: what it answers when the client ends a download's data connection before the end (instead of 426)."""
    authorizer = DummyAuthorizer()
    authorizer.add_user(USER, PASSWORD, str(root), perm=rights)
    for folder in write_only:
        authorizer.override_perm(USER, str(root / folder), "wfdT", recursive=True)
    dropped: list[bool] = []
    noop_dropped: list[bool] = []

    class LinksAsLinks(AbstractedFS):
        @override
        def format_mlsx(self, basedir: str, listing: list[str], perms: str, facts: list[str], ignore_err: bool = True) -> Generator[bytes]:
            for name in listing:
                path = os.path.join(basedir, name)
                if not os.path.islink(path):
                    yield from super().format_mlsx(basedir, [name], perms, facts, ignore_err)
                    continue
                details = os.lstat(path)
                moment = time.strftime("%Y%m%d%H%M%S", time.gmtime(details.st_mtime))
                yield f"modify={moment};size={details.st_size};type=OS.unix=slink:{os.readlink(path)}; {name}\r\n".encode()

    class FullModes(AbstractedFS):
        @override
        def format_mlsx(self, basedir: str, listing: list[str], perms: str, facts: list[str], ignore_err: bool = True) -> Generator[bytes]:
            for name in listing:
                details = os.stat(os.path.join(basedir, name))
                for line in super().format_mlsx(basedir, [name], perms, facts, ignore_err):
                    yield re.sub(rb"unix\.mode=[0-7a-z]+;", f"unix.mode={details.st_mode:07o};".encode(), line)

    class DroppingData(DTPHandler):
        def _drop_now(self) -> bool:
            if drops_data_after and not dropped and self.get_transmitted_bytes() >= drops_data_after:
                dropped.append(True)
                self._resp = ("426 Connection closed; transfer aborted.", lambda *args: None)
                self.close()
                return True
            return False

        @override
        def send(self, data: bytes) -> int:
            sent = super().send(data)
            self._drop_now()
            return sent

        @override
        def handle_read(self) -> None:
            if not self._drop_now():
                super().handle_read()

        handle_read_event = handle_read  # the base class names its reader twice

        @override
        def use_sendfile(self) -> bool:
            return False  # every byte goes through send, so the drop can come

    class EarlyEnd(DTPHandler):
        @override
        def handle_close(self) -> None:
            if not self._closed and not self.receive and len(self.producer_fifo) != 0:
                self._resp = (early_end_reply, lambda *args: None)  # the client ended it early: this server answers so
                self.close()
                return
            super().handle_close()

    class Handler(FTPHandler):
        def _refused_restart(self) -> bool:
            if refuses_restart and self._restart_position:
                self._restart_position = 0
                self.respond("550 This server does not continue files.")
                return True
            return False

        @override
        def ftp_STOR(self, file: str, mode: str = "w") -> str | None:
            refusal = next((reply for start, reply in refuses_store if Path(file).name.startswith(start)), "")
            if refusal:
                self.respond(refusal)
                return None
            return None if self._refused_restart() else super().ftp_STOR(file, mode)

        @override
        def ftp_RETR(self, file: str) -> str | None:
            return None if self._refused_restart() else super().ftp_RETR(file)

        @override
        def ftp_SIZE(self, path: str) -> None:
            if size_reply:
                self.respond(size_reply)
            elif sizes_anywhere:
                real = path if path.startswith(str(root)) else cast(AbstractedFS, self.fs).ftp2fs(path)  # without a permission to check, it comes as sent
                self.respond(f"213 {os.path.getsize(real)}" if os.path.isfile(real) else "550 Not a file.")
            else:
                super().ftp_SIZE(path)

        @override
        def pre_process_command(self, line: str, cmd: str, arg: str) -> None:
            if command_heard is not None:
                command_heard(cmd)
            super().pre_process_command(line, cmd, arg)

        @override
        def ftp_NOOP(self, line: str) -> None:
            if noop_heard is not None:
                noop_heard()
            if drops_at_noop and not noop_dropped:
                noop_dropped.append(True)
                self.close()
                return
            super().ftp_NOOP(line)

        @override
        def ftp_FEAT(self, line: str) -> None:
            if drops_at_feat:
                self.close()
            else:
                super().ftp_FEAT(line)

        @override
        def ftp_OPTS(self, line: str) -> None:
            if refuses_listing_options and line.upper().startswith("MLST"):
                self.respond("500 Don't know this option")  # some servers offer MLSD and have no such option
            else:
                super().ftp_OPTS(line)

    class TlsHandler(TLS_FTPHandler):
        pass

    handler = Handler if certificate is None else TlsHandler
    if drops_data_after:
        Handler.dtp_handler = DroppingData  # a plain server: the TLS one keeps its own data handler
    if early_end_reply:
        Handler.dtp_handler = EarlyEnd
    known = (FTPHandler if certificate is None else TLS_FTPHandler).proto_cmds
    handler.proto_cmds = {name: ({**details, "perm": None} if sizes_anywhere and name == "SIZE" else details) for name, details in known.items() if name not in without}
    if links_as_links:
        handler.abstracted_fs = LinksAsLinks
    if full_modes:
        handler.abstracted_fs = FullModes
    handler.authorizer = authorizer
    handler.auth_failed_timeout = 0
    if certificate is not None:
        TlsHandler.certfile = str(certificate)
    server = FTPServer(("127.0.0.1", 0), handler)
    server.max_cons_per_ip = most_logins
    port = cast(int, cast(socket.socket, server.socket).getsockname()[1])
    if trusted and certificate is None:
        trust.remember_certificate(f"127.0.0.1:{port} plain")
    stop = threading.Event()

    def serve() -> None:
        while not stop.is_set():
            server.ioloop.loop(0.05, blocking=False)  # one round of the loop at a time, so a stop is seen within one round

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(10)  # its loop is over: closing everything can't meet it halfway
        assert not thread.is_alive(), "the fake FTP server did not stop"
        server.close_all()


def self_signed_certificate(folder: Path, name: str = "server.pem") -> Path:
    path = folder / name
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(path), "-out", str(path) + ".crt", "-days", "2", "-subj", "/CN=127.0.0.1"], capture_output=True, check=True)
    path.write_bytes(path.read_bytes() + Path(str(path) + ".crt").read_bytes())
    return path
