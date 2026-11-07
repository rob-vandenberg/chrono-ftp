import asyncio
import socket
import subprocess
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast, override

import asyncssh
from chrono_ftp.connections import trust
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler, TLS_FTPHandler
from pyftpdlib.servers import FTPServer

# --- Version ------------------------------------------------------------
__version__ = "fakeservers.py 0.0.5"

# --- Version history ----------------------------------------------------
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


@contextmanager
def ssh_server(root: Path, shell: bool = True) -> Generator[SshServer]:
    """Real SSH and SFTP on a free local port; every SFTP path is inside root."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    key = asyncssh.generate_private_key("ssh-ed25519")

    class Sftp(asyncssh.SFTPServer):
        def __init__(self, channel: asyncssh.SSHServerChannel[bytes]) -> None:
            super().__init__(channel, chroot=str(root).encode())

    async def start() -> asyncssh.SSHAcceptor:
        return await asyncssh.listen("127.0.0.1", 0, server_host_keys=[key], server_factory=_Server, sftp_factory=Sftp, process_factory=_shell if shell else _no_shell, encoding="utf-8")

    acceptor = asyncio.run_coroutine_threadsafe(start(), loop).result(10)
    try:
        yield SshServer(acceptor.get_port(), key)
    finally:

        async def stop() -> None:
            acceptor.close()
            await asyncio.sleep(0)

        asyncio.run_coroutine_threadsafe(stop(), loop).result(5)


@contextmanager
def ftp_server(root: Path, trusted: bool = True, certificate: Path | None = None, rights: str = "elradfmwMT") -> Generator[int]:
    """Real FTP on a free local port; with a certificate it offers TLS. trusted: the person already said yes to plain FTP on this port."""
    authorizer = DummyAuthorizer()
    authorizer.add_user(USER, PASSWORD, str(root), perm=rights)

    class Handler(FTPHandler):
        pass

    class TlsHandler(TLS_FTPHandler):
        pass

    handler = Handler if certificate is None else TlsHandler
    handler.authorizer = authorizer
    handler.auth_failed_timeout = 0
    if certificate is not None:
        TlsHandler.certfile = str(certificate)
    server = FTPServer(("127.0.0.1", 0), handler)
    port = cast(int, cast(socket.socket, server.socket).getsockname()[1])
    if trusted and certificate is None:
        trust.remember_certificate(f"127.0.0.1:{port} plain")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.close_all()


def self_signed_certificate(folder: Path, name: str = "server.pem") -> Path:
    path = folder / name
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(path), "-out", str(path) + ".crt", "-days", "2", "-subj", "/CN=127.0.0.1"], capture_output=True, check=True)
    path.write_bytes(path.read_bytes() + Path(str(path) + ".crt").read_bytes())
    return path
