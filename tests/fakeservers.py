import asyncio
import hashlib
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Coroutine, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast, override

import asyncssh
import asyncssh.sftp
import pytest
from asyncssh.constants import FXP_EXTENDED_REPLY
from asyncssh.packet import SSHPacket, String
from chrono_ftp.connections import trust
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.filesystems import AbstractedFS
from pyftpdlib.handlers import DTPHandler, FTPHandler, TLS_FTPHandler
from pyftpdlib.servers import FTPServer

# --- Version ------------------------------------------------------------
__version__ = "fakeservers.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: An SFTP server that names check-file in its hello and computes checksums with it, or answers with another algorithm than
#          the one asked for; an FTP server that offers HASH and the older checksum commands, or answers them with no checksum (FE-01);
#          review round 1: both can take long over a checksum, as with a large file; review round 2: the path of a checksum command as
#          it came
# v0.0.37: An SSH server whose commands a test answers itself, so nothing runs on this computer: a stand-in for passwd that asks as
#          the real one does (FM-10), or never says it is done, or drops the line after the new password; an FTP server that copies a file itself with SITE CPFR and CPTO (FM-06),
#          or stops halfway
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


Commands = Callable[[asyncssh.SSHServerProcess[str]], Coroutine[object, object, None]]


SLOW_HASHING: threading.Event = threading.Event()  # set when a slow check-file has started on the server
CHECK_FILE_ALGORITHMS: tuple[str, ...] = ("md5", "sha1", "sha224", "sha256", "sha384", "sha512")


class _ExtendedReply:
    """What the fake SFTP server sends back to check-file-name: asyncssh sends what encode gives."""

    def __init__(self, data: bytes) -> None:
        self.data: bytes = data

    def encode(self, _version: int) -> bytes:
        return self.data


def _check_file_handler(answer: str) -> Callable[[object, SSHPacket], Coroutine[object, object, _ExtendedReply]]:
    """check-file-name as the draft says: the first algorithm of the list the server knows, the hash of the whole file. answer
    "other": always md5, whatever was asked, as a server that knows only that; "slow": the answer comes after a minute, as for a very
    large file."""

    async def check_file_name(handler: object, packet: SSHPacket) -> _ExtendedReply:
        path = packet.get_string()
        wanted = packet.get_string().decode("ascii")
        packet.get_uint64()
        packet.get_uint64()
        packet.get_uint32()
        packet.check_end()
        server = cast(asyncssh.SFTPServer, getattr(handler, "_server"))
        local = server.map_path(path)
        algorithm = "md5" if answer == "other" else next((name for name in wanted.split(",") if name in CHECK_FILE_ALGORITHMS), "")
        if not algorithm:
            raise asyncssh.SFTPOpUnsupported("No such algorithm")
        if not os.path.isfile(local):
            raise asyncssh.SFTPNoSuchFile("No such file")
        if answer == "slow":
            SLOW_HASHING.set()  # a test waits for this, never for time
            await asyncio.sleep(60)
        digest = hashlib.new(algorithm, Path(os.fsdecode(local)).read_bytes()).digest()
        return _ExtendedReply(String(algorithm) + digest)

    return check_file_name


@contextmanager
def _offering_check_file(answer: str) -> Generator[None]:
    """For one block, every fake SFTP server names check-file in its hello and answers check-file-name; asyncssh has no hook for an
    extension of its own, so its server class gets it for that block and loses it after."""
    handler_class = asyncssh.sftp.SFTPServerHandler
    names = ("_extensions", "_packet_handlers", "_return_types")
    before = {name: handler_class.__dict__.get(name) for name in names}
    handlers = cast(dict[object, object], getattr(handler_class, "_packet_handlers"))
    returns = cast(dict[object, object], getattr(handler_class, "_return_types"))
    extensions = cast(list[tuple[bytes, bytes]], getattr(handler_class, "_extensions"))
    setattr(handler_class, "_extensions", [*extensions, (b"check-file", b"")])
    setattr(handler_class, "_packet_handlers", {**handlers, b"check-file-name": _check_file_handler(answer)})
    setattr(handler_class, "_return_types", {**returns, b"check-file-name": FXP_EXTENDED_REPLY})
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                delattr(handler_class, name)
            else:
                setattr(handler_class, name, value)


@contextmanager
def ssh_server(root: Path, shell: bool = True, commands: Commands | None = None, check_file: bool | str = False) -> Generator[SshServer]:
    """Real SSH and SFTP on a free local port; every SFTP path is inside root. commands: the test answers every command itself, and
    nothing runs on this computer (the plain shell runs its commands here, on real paths). check_file: it names check-file in its hello
    and computes checksums with it; "other": it answers with md5 whatever was asked. At the end of its block it takes no new connection; once
    none is open any more (else at the end of the test, end_the_servers) it closes its event loop and its thread: nothing stays behind."""
    if check_file:
        with _offering_check_file(str(check_file)), ssh_server(root, shell, commands) as server:
            yield server
        return
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
        return await asyncssh.listen("127.0.0.1", 0, server_host_keys=[key], server_factory=Server, sftp_factory=Sftp, process_factory=commands or (_shell if shell else _no_shell), encoding="utf-8")

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


PROBE_ANSWER: str = "chrono-ftp-probe\nLinux\n1000\ntool:passwd\n"  # a shell with passwd, as the probe of SshConnection reads it


class Passwd:
    """A stand-in for passwd that asks as the real one does on Linux, without echo, and keeps the password of the account here. refuse:
    the first new password is refused as too simple, and asked again; as_root: no current password is asked. Nothing runs on this
    computer: the probe of the program and passwd are answered, any other command is refused."""

    def __init__(self, password: str, refuse: bool = False, as_root: bool = False, echo: bool = False, silent_end: bool = False, drop_at_end: bool = False) -> None:
        self.password: str = password
        self.drop_at_end: bool = drop_at_end  # the line drops right after the new password was typed twice
        self.silent_end: bool = silent_end  # after its last word it closes its output but never says it is done
        self.refuse: bool = refuse
        self.as_root: bool = as_root
        self.echo: bool = echo
        self.commands: list[str] = []

    async def __call__(self, process: asyncssh.SSHServerProcess[str]) -> None:
        command = process.command or ""
        self.commands.append(command)
        if command.startswith("echo chrono-ftp-probe"):
            process.stdout.write(PROBE_ANSWER)
            process.exit(0)
            return
        if command != "env LC_ALL=C passwd":
            process.stderr.write("Not here.\n")
            process.exit(127)
            return
        set_echo = cast(Callable[[bool], None] | None, getattr(process.channel, "set_echo", None))  # the line editor of a terminal has it
        if set_echo is not None:
            set_echo(self.echo)
        process.stdout.write("Changing password for anna.\n")
        if not self.as_root:
            process.stdout.write("Current password: ")
            if (await process.stdin.readline()).rstrip("\r\n") != self.password:
                process.stdout.write("\npasswd: Authentication token manipulation error\npasswd: password unchanged\n")
                process.exit(10)
                return
        process.stdout.write("New password: ")
        new = (await process.stdin.readline()).rstrip("\r\n")
        if self.refuse:
            process.stdout.write("\nBAD PASSWORD: The password is shorter than 8 characters\nNew password: ")
            new = (await process.stdin.readline()).rstrip("\r\n")
        process.stdout.write("\nRetype new password: ")
        again = (await process.stdin.readline()).rstrip("\r\n")
        if self.drop_at_end:
            self.password = new if new == again else self.password  # it took the new one, and then the line went
            process.channel.get_connection().abort()
            return
        if new != again:
            process.stdout.write("\nSorry, passwords do not match.\npasswd: Authentication token manipulation error\npasswd: password unchanged\n")
            process.exit(10)
            return
        self.password = new
        process.stdout.write("\npasswd: password updated successfully\n")
        if self.silent_end:
            process.stdout.write_eof()
            await asyncio.sleep(CLOSE_SECONDS * 6)  # ended by the end of the server
        process.exit(0)


def end_the_servers() -> None:
    """When a test is over (conftest): every fake SSH server whose block ended while a connection to it was still open ends now, after
    the window of the test closed its connections: the connections it still has, then its event loop and its thread."""
    while ENDING:
        ENDING.pop()()


AS_ROOT: bool = cast(Callable[[], int], getattr(os, "geteuid", lambda: -1))() == 0
NOT_AS_ROOT: pytest.MarkDecorator = pytest.mark.skipif(AS_ROOT, reason="as root every file can be read and written, so the refusal this test needs never comes")


@contextmanager
def ftp_server(root: Path, trusted: bool = True, certificate: Path | None = None, rights: str = "elradfmwMT", refuses_listing_options: bool = False, without: tuple[str, ...] = (), size_reply: str = "", drops_at_feat: bool = False, refuses_restart: bool = False, drops_data_after: int = 0, write_only: tuple[str, ...] = (), refuses_store: tuple[tuple[str, str], ...] = (), sizes_anywhere: bool = False, links_as_links: bool = False, full_modes: bool = False, noop_heard: Callable[[], None] | None = None, drops_at_noop: bool = False, command_heard: Callable[[str], None] | None = None, most_logins: int = 0, early_end_reply: str = "", copies: bool | str = False, hashes: str = "", checksum_commands: tuple[str, ...] = (), hash_reply: str = "", hash_seconds: float = 0.0, hash_heard: Callable[[str], None] | None = None) -> Generator[int]:
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
    early_end_reply: what it answers when the client ends a download's data connection before the end (instead of 426). copies: it
    copies a file itself with SITE CPFR and CPTO, and says so in SITE HELP; "halfway": each copy stops after its first byte, as on a full
    disk, and leaves that much behind. hashes: the HASH line of FEAT ("SHA-256*;SHA-1;MD5"), with OPTS HASH and HASH answered as the
    draft says; checksum_commands: older commands it offers in FEAT and answers ("XSHA256", "XMD5"); hash_reply: what it answers to
    every one of them instead of the checksum; hash_seconds: each answer comes this much later, as for a large file, while the
    server goes on serving its other connections; hash_heard: called with the path of every checksum command as it came."""
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

        def ftp_SITE_CPFR(self, path: str) -> None:
            self.copy_from = cast(AbstractedFS, self.fs).ftp2fs(path)
            self.respond("350 File or directory exists, ready for destination name" if os.path.isfile(self.copy_from) else "550 No such file")

        def ftp_SITE_CPTO(self, path: str) -> None:
            source = getattr(self, "copy_from", "")
            target = cast(AbstractedFS, self.fs).ftp2fs(path)
            if not source or os.path.lexists(target) or not cast(AbstractedFS, self.fs).validpath(target):
                self.respond("550 Copy refused")
                return
            if copies == "halfway":
                Path(target).write_bytes(Path(source).read_bytes()[:1])  # the disk filled up after the first byte
                self.respond("552 No room left on the device")
                return
            shutil.copyfile(source, target)
            self.respond("250 Copy successful")

        @override
        def ftp_FEAT(self, line: str) -> None:
            if drops_at_feat:
                self.close()
            else:
                self._extra_feats = [*([f"HASH {hashes}"] if hashes else []), *checksum_commands]
                super().ftp_FEAT(line)

        @override
        def ftp_OPTS(self, line: str) -> None:
            if refuses_listing_options and line.upper().startswith("MLST"):
                self.respond("500 Don't know this option")  # some servers offer MLSD and have no such option
            elif hashes and line.upper().startswith("HASH "):
                wanted = line[5:].strip().upper()
                if wanted not in [name.rstrip("*") for name in hashes.split(";")]:
                    self.respond("501 Unknown algorithm")
                    return
                self.hash_chosen = wanted
                self.respond(f"200 {wanted}")
            else:
                super().ftp_OPTS(line)

        def _digest(self, path: str, algorithm: str) -> tuple[str, int, str] | None:
            if hash_heard is not None:
                hash_heard(path)
            real = cast(AbstractedFS, self.fs).ftp2fs(path.strip('"'))
            if not os.path.isfile(real):
                self.respond("550 No such file")
                return None
            data = Path(real).read_bytes()
            return hashlib.new(algorithm, data).hexdigest(), len(data), Path(real).name

        def ftp_HASH(self, path: str) -> None:
            chosen = getattr(self, "hash_chosen", next(name.rstrip("*") for name in hashes.split(";") if name.endswith("*")))
            found = self._digest(path, chosen.replace("-", "").lower())
            if found is not None:
                self._answer_later(hash_reply or f"213 {chosen} 0-{found[1]} {found[0]} {found[2]}")

        def _answer_later(self, reply: str) -> None:
            if not hash_seconds:
                self.respond(reply)
                return

            def answer() -> None:
                if not self._closed:
                    self.respond(reply)

            self.ioloop.call_later(hash_seconds, answer)

        def _older(self, path: str, algorithm: str) -> None:
            found = self._digest(path, algorithm)
            if found is not None:
                self._answer_later(hash_reply or f"250 {found[0]}")

        def ftp_XSHA256(self, path: str) -> None:
            self._older(path, "sha256")

        def ftp_XMD5(self, path: str) -> None:
            self._older(path, "md5")

    class TlsHandler(TLS_FTPHandler):
        pass

    handler = Handler if certificate is None else TlsHandler
    if drops_data_after:
        Handler.dtp_handler = DroppingData  # a plain server: the TLS one keeps its own data handler
    if early_end_reply:
        Handler.dtp_handler = EarlyEnd
    known = (FTPHandler if certificate is None else TLS_FTPHandler).proto_cmds
    handler.proto_cmds = {name: ({**details, "perm": None} if sizes_anywhere and name == "SIZE" else details) for name, details in known.items() if name not in without}
    if copies:
        handler.proto_cmds = {**handler.proto_cmds, **{name: {"perm": None, "auth": True, "arg": True, "help": f"Syntax: {name} <SP> path"} for name in ("SITE CPFR", "SITE CPTO")}}
    if hashes or checksum_commands:
        handler.proto_cmds = {**handler.proto_cmds, **{name: {"perm": None, "auth": True, "arg": True, "help": f"Syntax: {name} <SP> path"} for name in ("HASH", *checksum_commands)}}
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
