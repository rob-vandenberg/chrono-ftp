import calendar
import codecs
import encodings.utf_8
import ftplib
import hashlib
import re
import socket
import ssl
import threading
import time
from collections.abc import Buffer
from pathlib import Path
from typing import override

from chrono_ftp.connections import trust
from chrono_ftp.connections.base import Capabilities, Progress, RemoteConnection, RemoteEntry, UnknownServer, joined, safe_name
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.errors import ChronoFtpError, ConnectionFailed, ConnectionLost, LoginRefused, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "ftp.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: A name that is not valid UTF-8 keeps its real bytes, so it no longer ends the connection
# v0.0.5: Initial: ftplib, TLS where the server offers it, MLSD for listings (D-011)

SECONDS: float = 30.0
BLOCK_SIZE: int = 64 * 1024
NOT_REACHED_TEXT: str = "The server could not be reached. Please check the host and the port."
LOGIN_TEXT: str = "The server did not accept this login. Please check the user and password."
NO_LISTING_TEXT: str = "This server does not offer the modern folder listing (MLSD) that Chrono FTP needs."
PLAIN: str = "plain"
CHANGED_TEXT: str = "This server's certificate has changed since you trusted it, so Chrono FTP did not connect."
NOT_ENCRYPTED_TEXT: str = "This server does not offer encryption, so your password and files would travel readable over the network."
NO_LONGER_ENCRYPTED_TEXT: str = "This server used to offer encryption and no longer does, so Chrono FTP did not connect."
NAMES_ENCODING: str = "chrono-ftp-names"
SIZE_PATTERN: re.Pattern[str] = re.compile(r"[0-9]{1,18}")
LOST_TEXT: str = "The connection to the server was lost."
FTP_ERRORS: tuple[type[BaseException], ...] = (ftplib.Error, OSError, EOFError, ValueError)
LOST_ERRORS: tuple[type[BaseException], ...] = (EOFError, ConnectionError, TimeoutError)


class _NamesDecoder(encodings.utf_8.IncrementalDecoder):
    def __init__(self, errors: str = "surrogateescape") -> None:
        super().__init__("surrogateescape")


class _NamesEncoder(codecs.IncrementalEncoder):
    @override
    def encode(self, input: str, final: bool = False) -> bytes:
        return input.encode("utf-8", "surrogateescape")


def _encode_names(text: str, errors: str = "surrogateescape") -> tuple[bytes, int]:
    return text.encode("utf-8", "surrogateescape"), len(text)


def _decode_names(input: Buffer, errors: str = "surrogateescape") -> tuple[str, int]:
    data = bytes(input)
    return data.decode("utf-8", "surrogateescape"), len(data)


def _names_codec(name: str) -> codecs.CodecInfo | None:
    """ftplib reads with a strict encoding and has no way to change that: this one keeps the real bytes of a name that is not valid UTF-8."""
    if name.replace("_", "-") != NAMES_ENCODING:
        return None
    return codecs.CodecInfo(
        _encode_names,
        _decode_names,
        incrementalencoder=_NamesEncoder,
        incrementaldecoder=_NamesDecoder,
        name=NAMES_ENCODING,
    )


codecs.register(_names_codec)


class _TlsFtp(ftplib.FTP_TLS):
    """Some servers want the data connection to continue the TLS session of the control connection."""

    trusted_certificate: bytes | None = None  # set when the person trusted this certificate: every data connection must show the same one

    @override
    def ntransfercmd(self, cmd: str, rest: int | str | None = None) -> tuple[socket.socket, int | None]:
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:  # pyrefly: ignore[missing-attribute]
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)  # pyrefly: ignore[missing-attribute]
            if self.trusted_certificate is not None and conn.getpeercert(binary_form=True) != self.trusted_certificate:
                conn.close()
                raise ssl.SSLError("The data connection showed another certificate.")
        return conn, size


def _time_of(fact: str) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(fact[:14], "%Y%m%d%H%M%S")))
    except ValueError:
        return None


def entry_from(name: str, facts: dict[str, str]) -> RemoteEntry | None:
    """One MLSD line. The line is input: a bad size gives size 0, never a crash (OR-15)."""
    kind = facts.get("type", "").lower()
    if kind in ("cdir", "pdir") or not safe_name(name):
        return None
    size_text = facts.get("size", "0")
    size = int(size_text) if SIZE_PATTERN.fullmatch(size_text) else 0
    is_link = kind.startswith("os.unix=slink") or kind.startswith("os.unix=symlink")
    permissions = facts.get("unix.mode", "")
    return RemoteEntry(name, kind == "dir", size, _time_of(facts.get("modify", "")), permissions, facts.get("unix.owner", ""), facts.get("unix.group", ""), is_link)


class FtpConnection(RemoteConnection):
    def __init__(self, connection: Connection, password: str | None = None) -> None:
        self.connection: Connection = connection
        self.password: str = password or ""
        self.capabilities = Capabilities()
        self.encrypted: bool = False
        self.must_encrypt: bool = False
        self._ftp: ftplib.FTP | None = None
        self._lock: threading.Lock = threading.Lock()  # one control connection does one thing at a time

    def _prefix(self) -> str:
        return f"{self.connection.host}:{self.connection.port} "

    def _certificate_line(self, certificate: bytes) -> str:
        return self._prefix() + hashlib.sha256(certificate).hexdigest()

    def _unverified_context(self) -> ssl.SSLContext:
        """Only to read a certificate and compare it with the one the person trusted; no password goes out before that."""
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context

    def _new_session(self, session: ftplib.FTP) -> ftplib.FTP:
        session.encoding = NAMES_ENCODING
        session.connect(self.connection.host, self.connection.port)
        return session

    def _peer_of(self, session: ftplib.FTP) -> bytes:
        certificate = session.sock.getpeercert(binary_form=True) if isinstance(session.sock, ssl.SSLSocket) else None
        if not certificate:
            raise ConnectionFailed(NOT_REACHED_TEXT)
        return certificate

    def _with_trusted_certificate(self) -> ftplib.FTP:
        """The certificate can't be verified: it must be exactly the one the person trusted, checked before any login."""
        session = self._new_session(_TlsFtp(context=self._unverified_context(), timeout=SECONDS))
        if isinstance(session, ftplib.FTP_TLS):
            session.auth()
        certificate = self._peer_of(session)
        line = self._certificate_line(certificate)
        if trust.certificate_is_trusted(line):
            if isinstance(session, _TlsFtp):
                session.trusted_certificate = certificate
            return session
        session.close()
        if any(not known.endswith(PLAIN) for known in trust.lines_for(self._prefix())):
            raise ConnectionFailed(CHANGED_TEXT)
        raise UnknownServer("certificate", line.split()[-1], line)

    def _without_tls(self) -> ftplib.FTP:
        """Never on its own: the password would travel readable, so the person decides, once per server (OR-19)."""
        known = trust.lines_for(self._prefix())
        if self.must_encrypt or any(not line.endswith(PLAIN) for line in known):
            raise ConnectionFailed(NO_LONGER_ENCRYPTED_TEXT)
        line = self._prefix() + PLAIN
        if line not in known:
            raise UnknownServer("connection", "not encrypted", line, NOT_ENCRYPTED_TEXT)
        self.encrypted = False
        return self._new_session(ftplib.FTP(timeout=SECONDS))

    def _open(self) -> ftplib.FTP:
        session = self._new_session(_TlsFtp(context=ssl.create_default_context(), timeout=SECONDS))
        assert isinstance(session, ftplib.FTP_TLS)
        try:
            session.auth()
        except ftplib.error_perm:
            session.close()
            return self._without_tls()
        except ssl.SSLCertVerificationError:
            session.close()
            return self._with_trusted_certificate()
        return session

    @override
    def connect(self) -> None:
        try:
            session = self._open()
            session.login(self.connection.user or "anonymous", self.password)
            self.encrypted = isinstance(session, ftplib.FTP_TLS)
            if isinstance(session, ftplib.FTP_TLS):
                session.prot_p()
            session.set_pasv(True)
            session.voidcmd("TYPE I")
        except ftplib.error_perm as error:
            raise LoginRefused(LOGIN_TEXT) from error
        except FTP_ERRORS as error:
            raise ConnectionFailed(NOT_REACHED_TEXT) from error
        self._ftp = session
        self.capabilities = Capabilities()  # FTP has no shell

    @override
    def close(self) -> None:
        session = self._ftp
        self._ftp = None
        if session is not None:
            try:
                session.quit()
            except FTP_ERRORS:
                session.close()

    def _drop(self) -> None:
        session = self._ftp
        self._ftp = None
        if session is not None:
            session.close()

    def _session(self) -> ftplib.FTP:
        if self._ftp is None:
            raise RemoteActionFailed("The connection is closed.")
        return self._ftp

    @override
    def check(self) -> None:
        if not self._lock.acquire(blocking=False):
            return  # busy, so it is alive
        try:
            self._session().voidcmd("NOOP")
        except FTP_ERRORS as error:
            raise ConnectionLost(LOST_TEXT) from error
        finally:
            self._lock.release()

    @override
    def home(self) -> str:
        with self._lock:
            try:
                return self._session().pwd()
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed("The server did not answer.") from error

    @override
    def list_folder(self, path: str) -> list[RemoteEntry]:
        with self._lock:
            try:
                found = list(self._session().mlsd(path, ["type", "size", "modify", "unix.mode", "unix.owner", "unix.group"]))
            except ssl.SSLError as error:
                self._drop()  # another certificate on the data connection: nothing on this connection can be trusted
                raise ConnectionLost(LOST_TEXT) from error
            except ftplib.error_perm as error:
                text = str(error)
                raise RemoteActionFailed(NO_LISTING_TEXT if text.startswith("500") or text.startswith("502") else text) from error
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed("The server did not answer.") from error
        return [entry for name, facts in found if (entry := entry_from(name, facts)) is not None]

    def _do(self, *commands: tuple[str, str]) -> None:
        with self._lock:
            session = self._session()
            try:
                for method, argument in commands:
                    getattr(session, method)(argument)
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed(str(error) or "The server refused this action.") from error

    @override
    def rename(self, old: str, new: str) -> None:
        with self._lock:
            try:
                self._session().rename(old, new)
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed(str(error) or "The server refused this action.") from error

    @override
    def make_folder(self, path: str) -> None:
        self._do(("mkd", path))

    @override
    def delete(self, path: str, is_folder: bool) -> None:
        if not is_folder:
            self._do(("delete", path))
            return
        for entry in self.list_folder(path):
            self.delete(joined(path, entry.name), entry.is_folder and not entry.is_link)
        self._do(("rmd", path))

    def _start_afresh(self) -> None:
        """A transfer broken off leaves its reply on the way: a new login, so no later answer can belong to the wrong command."""
        self._drop()
        try:
            self.connect()
        except (ChronoFtpError, *FTP_ERRORS):
            pass  # the next call tells that the connection is closed

    @override
    def set_mode(self, path: str, mode: str) -> None:
        with self._lock:
            try:
                self._session().voidcmd(f"SITE CHMOD {mode} {path}")
            except FTP_ERRORS:
                pass  # many servers can't: the file is there, only its permissions could not be set

    @override
    def download(self, remote: str, local: str, progress: Progress, limit: int = 0) -> None:
        pause = BLOCK_SIZE / (limit * 1024) if limit else 0.0
        with self._lock, Path(local).open("wb") as file:

            def take(block: bytes) -> None:
                file.write(block)
                progress(len(block))
                time.sleep(pause)

            try:
                self._session().retrbinary(f"RETR {remote}", take, BLOCK_SIZE)
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed(str(error) or "The transfer did not finish.") from error
            except BaseException:
                self._start_afresh()
                raise

    @override
    def upload(self, local: str, remote: str, progress: Progress, limit: int = 0) -> None:
        pause = BLOCK_SIZE / (limit * 1024) if limit else 0.0

        def sent(block: bytes) -> None:
            progress(len(block))
            time.sleep(pause)

        with self._lock, Path(local).open("rb") as file:
            try:
                self._session().storbinary(f"STOR {remote}", file, BLOCK_SIZE, sent)
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed(str(error) or "The transfer did not finish.") from error
            except BaseException:
                self._start_afresh()
                raise

    @override
    def for_a_transfer(self) -> "FtpConnection":
        """FTP does one thing at a time, so a transfer gets a login of its own and browsing goes on."""
        other = FtpConnection(self.connection, self.password)
        other.must_encrypt = self.encrypted  # a transfer never travels with less protection than the browsing
        other.connect()
        return other
