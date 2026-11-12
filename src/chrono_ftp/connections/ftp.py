import calendar
import codecs
import encodings.utf_8
import ftplib
import hashlib
import re
import socket
import ssl
import stat
import threading
import time
from collections.abc import Buffer, Callable
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast, override

from cryptography import x509

from chrono_ftp.connections import sessionlog, trust
from chrono_ftp.connections.base import FreeSpace, ServerInfo, log_listing, HALF_COPY_LEFT_TEXT, NO_COPY_TEXT, GONE_TEXT, LocalFile, NAME_TAKEN_TEXT, NO_ROOM_TEXT, NO_TIME_TEXT, NOT_EMPTY_TEXT, Capabilities, Limit, Progress, RemoteConnection, RemoteEntry, UnknownServer, joined, limit_pauses, parent_of, safe_name
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.logrules import DEBUG
from chrono_ftp.core.errors import ChronoFtpError, ConnectionFailed, ConnectionLost, ContinuationRefused, LocalActionFailed, LoginRefused, NameTaken, NoMoreLogins, RefusedForNow, RemoteActionFailed, TransferInterrupted

# --- Version ------------------------------------------------------------
__version__ = "ftp.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: The session log: every command and every reply of the control connection, the PASS line never with its password, each
#          listing's entries at the detailed level and the data connections at the debug level (FP-02); what the connection tells about
#          itself (greeting, SYST, TLS and its certificate, FEAT), and the free space of a folder through AVBL where FEAT offers it (FP-03)
# v0.0.37: A file is copied on the server itself with SITE CPFR and CPTO, where SITE HELP offers both (FM-06); never onto a name
#          that is taken; a copy that failed halfway is removed, and where it can't be, the message says so
# v0.0.13: The speed limit is asked again for every block, so a new limit takes effect at once; its pauses are short steps, so a stop
#          is never kept waiting; a passing refusal of a new connection (421: too many) is told apart from a server that can't be reached;
#          after a broken-off transfer the new login comes only when the connection is next needed, so a stop ends at once, and a server
#          that takes no more logins at that moment says so (NoMoreLogins) instead of the connection being lost; one range of a file
#          fetched with REST, the data connection ended after it, and a login of its own for it (FC-05); any answer to that early end
#          is no failure of a range that arrived whole
# v0.0.12: What the server can do for a transfer is read from FEAT and SITE HELP; resume with REST and APPE, append, dates with MFMT,
#          rights with SITE CHMOD, sizes and empty folders (FB-02, FB-05, FB-06, FB-08); a SIZE reply that is no number counts as
#          unknown; a name with a line break fails only that item, in plain words; a resume of an upload is REST with STOR; the details
#          of one file by MLST; a size the server does not give is unknown; a refused RMD says why; a dropped data connection is an
#          interrupted transfer of that one file (only a lost control connection stops the job), 450 and 452 a refusal of that one file;
#          every step on this computer's file goes through LocalFile, so a problem there is this computer's, and a broken-off append of
#          a download is cut back; whether a name is taken in a folder that can't be read; a login the server closed while idle is
#          made again; in a folder that can't be read, a file is told by its size, and anything else's kind is unknown; SIZE is asked in
#          binary mode, so a server that tells sizes only there tells them, also of the file a link leads to; one name is looked up by
#          MLST or RNFR, never by a listing, and a transfer reads each folder once and keeps it up with its own changes; a name is checked
#          right before a new file takes it, with no RNFR twice; every check of whether a file changed asks the server this moment
#          (MLST, or SIZE and MDTM, or a fresh listing), never a kept listing; only a refusal of a continuation itself is told as one, and
#          450 and 452 as a refusal for now; a temporary file's name is asked of the server right before it is stored; a listing says
#          which names it left out; binary mode is asked only when the session is not in it, and whether a name is taken by one RNFR;
#          a part is continued only while it has exactly the length it is continued from; a kept listing is changed in place; a
#          pipe, socket or device is marked as such where the listing tells it; a unix.mode that holds the kind gives the rights alone
# v0.0.10: A server without the OPTS MLST option still lists: a refusal of that option is no failure
# v0.0.6: A name that is not valid UTF-8 keeps its real bytes, so it no longer ends the connection
# v0.0.5: Initial: ftplib, TLS where the server offers it, MLSD for listings (D-011)

SECONDS: float = 30.0
BLOCK_SIZE: int = 64 * 1024
NOT_REACHED_TEXT: str = "The server could not be reached. Please check the host and the port."
SHORT_RANGE_TEXT: str = "The file on the server ended earlier than its size said while it was being fetched."
NO_MORE_LOGINS_TEXT: str = "The server takes no more connections at the moment. Please try again a little later."
LOGIN_TEXT: str = "The server did not accept this login. Please check the user and password."
NO_LISTING_TEXT: str = "This server does not offer the modern folder listing (MLSD) that Chrono FTP needs."
PLAIN: str = "plain"
CHANGED_TEXT: str = "This server's certificate has changed since you trusted it, so Chrono FTP did not connect."
NOT_ENCRYPTED_TEXT: str = "This server does not offer encryption, so your password and files would travel readable over the network."
NO_LONGER_ENCRYPTED_TEXT: str = "This server used to offer encryption and no longer does, so Chrono FTP did not connect."
NAMES_ENCODING: str = "chrono-ftp-names"
DETAILS_COMMAND: str = "OPTS MLST type;size;modify;unix.mode;unix.owner;unix.group;"
SIZE_PATTERN: re.Pattern[str] = re.compile(r"[0-9]{1,18}")
LOST_TEXT: str = "The connection to the server was lost."
INTERRUPTED_TEXT: str = "The data connection for this file broke off on the way."
PART_NOT_AS_LEFT_TEXT: str = "The unfinished copy on the server is not as it was left, so it starts again from the beginning."
BUSY_TEXT: str = "The server can't use this file right now ({reply}). The other files go on; you can send this one again later."
SITE_COMMANDS: frozenset[str] = frozenset({"CHMOD", "CHOWN", "CHGRP", "CPFR", "CPTO", "SYMLINK", "UTIME", "MKDIR", "RMDIR", "UMASK", "IDLE", "MD5", "SHA1"})
AVBL_REPLY: re.Pattern[str] = re.compile(r"213\s+([0-9]{1,18})\s*")
FTP_ERRORS: tuple[type[BaseException], ...] = (ftplib.Error, OSError, EOFError, ValueError)
LOST_ERRORS: tuple[type[BaseException], ...] = (EOFError, ConnectionError, TimeoutError)


ODD_NAME_TEXT: str = "This name holds a line break, which FTP can't carry, so this item was left as it is."


def _refusal(error: BaseException, fallback: str) -> str:
    """The server's own words; ftplib's refusal of a line break in a name is said plainly, as only that item fails (OR-15)."""
    if isinstance(error, ValueError) and "newline" in str(error):
        return ODD_NAME_TEXT
    return str(error) or fallback


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


class _LoggedFtp(ftplib.FTP):
    """Every command and every reply of the control connection goes to the session log (FP-02); the log itself takes the password out of
    the PASS line, whatever the level (OR-19)."""

    session_log: sessionlog.SessionLog = sessionlog.QUIET

    @override
    def putcmd(self, line: str) -> None:
        self.session_log.asked(line)
        super().putcmd(line)

    @override
    def getmultiline(self) -> str:
        reply = super().getmultiline()
        lines = reply.splitlines()
        self.session_log.answered(lines[0] if lines else "")
        self.session_log.answered_lines(lines[1:])
        return reply

    @override
    def ntransfercmd(self, cmd: str, rest: int | str | None = None) -> tuple[socket.socket, int | None]:
        self.session_log.note(f"data connection for {cmd}" + (f" from byte {rest}" if rest else ""), DEBUG)
        return super().ntransfercmd(cmd, rest)


class _TlsFtp(_LoggedFtp, ftplib.FTP_TLS):
    """Some servers want the data connection to continue the TLS session of the control connection."""

    trusted_certificate: bytes | None = None  # set when the person trusted this certificate: every data connection must show the same one

    @override
    def ntransfercmd(self, cmd: str, rest: int | str | None = None) -> tuple[socket.socket, int | None]:
        self.session_log.note(f"data connection for {cmd}" + (f" from byte {rest}" if rest else ""), DEBUG)
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:  # pyrefly: ignore[missing-attribute]
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)  # pyrefly: ignore[missing-attribute]
            if self.trusted_certificate is not None and conn.getpeercert(binary_form=True) != self.trusted_certificate:
                conn.close()
                raise ssl.SSLError("The data connection showed another certificate.")
        return conn, size


def certificate_lines(certificate: bytes) -> list[tuple[str, str]]:
    """What the TLS certificate says of itself; a certificate that can't be read gives its fingerprint alone (OR-15)."""
    digest = hashlib.sha256(certificate).hexdigest()
    fingerprint = ("Fingerprint (SHA-256)", ":".join(digest[place : place + 2] for place in range(0, len(digest), 2)))
    try:
        read = x509.load_der_x509_certificate(certificate)
        return [
            ("Issued to", read.subject.rfc4514_string()),
            ("Issued by", read.issuer.rfc4514_string()),
            ("Valid from", read.not_valid_before_utc.strftime("%Y-%m-%d %H:%M UTC")),
            ("Valid until", read.not_valid_after_utc.strftime("%Y-%m-%d %H:%M UTC")),
            fingerprint,
        ]
    except ValueError:
        return [fingerprint]


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
    size_text = facts.get("size", "")
    size_known = SIZE_PATTERN.fullmatch(size_text) is not None
    size = int(size_text) if size_known else 0
    is_link = kind.startswith("os.unix=slink") or kind.startswith("os.unix=symlink")
    mode = facts.get("unix.mode", "")
    special = (kind.startswith("os.") and not is_link) or _special_mode(mode)  # a server's own type that is no link: no plain file
    permissions = _rights_only(mode)
    return RemoteEntry(name, kind == "dir", size, _time_of(facts.get("modify", "")), permissions, facts.get("unix.owner", ""), facts.get("unix.group", ""), is_link, size_known=size_known, special=special)


def _rights_only(mode: str) -> str:
    """The rights of a unix.mode, as a server that gives the rights alone writes them: 0100644 (with the kind) is 0644. A mode that holds
    only rights, or is no number, stays as the server wrote it."""
    try:
        value = int(mode, 8)
    except ValueError:
        return mode
    return format(value & 0o7777, "04o") if value > 0o7777 else mode


def _special_mode(mode: str) -> bool:
    """A unix.mode that holds the kind as well (a pipe, a socket or a device); one with the rights alone tells nothing of it."""
    try:
        value = int(mode, 8)
    except ValueError:
        return False
    return stat.S_IFMT(value) in (stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK)


class _RangeDone(Exception):
    """The range of a split download is here: the data connection ends now."""


class FtpConnection(RemoteConnection):
    def __init__(self, connection: Connection, password: str | None = None) -> None:
        self.connection: Connection = connection
        self.password: str = password or ""
        self.capabilities = Capabilities()
        self.encrypted: bool = False
        self.must_encrypt: bool = False
        self._ftp: ftplib.FTP | None = None
        self._afresh: bool = False  # dropped after a broken-off transfer: the next call logs in again
        self._asked: ftplib.FTP | None = None  # the session that was asked for its details
        self._lock: threading.Lock = threading.Lock()  # one control connection does one thing at a time
        self._mlst: bool | None = None  # whether the server knows MLST: learnt from its first answer
        self._binary: bool = False  # the session is in binary mode now: SIZE needs it, a listing leaves it
        self._listings: dict[str, dict[str, RemoteEntry]] | None = None  # a transfer's folders (by name), each read once and kept up with its own changes
        self._features: frozenset[str] = frozenset()  # what FEAT named, for the server information and AVBL (FP-03)
        self._site: tuple[str, ...] = ()  # the SITE commands SITE HELP named
        self.log = sessionlog.log_for(connection.name)
        self.log.hide(password)  # never written, whatever the level (OR-19)

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

    def _new_session(self, session: _LoggedFtp) -> ftplib.FTP:
        session.encoding = NAMES_ENCODING
        session.session_log = self.log
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
        return self._new_session(_LoggedFtp(timeout=SECONDS))

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
        self.log.note(f"connect to {self.connection.host} port {self.connection.port} as {self.connection.user or 'anonymous'}")
        try:
            session = self._open()
            session.login(self.connection.user or "anonymous", self.password)
            self.encrypted = isinstance(session, ftplib.FTP_TLS)
            if isinstance(session, ftplib.FTP_TLS):
                session.prot_p()
            session.set_pasv(True)
            session.voidcmd("TYPE I")
            self._binary = True
        except ftplib.error_perm as error:
            raise LoginRefused(LOGIN_TEXT) from error
        except ftplib.error_temp as error:
            raise NoMoreLogins(NO_MORE_LOGINS_TEXT) from error
        except FTP_ERRORS as error:
            raise ConnectionFailed(NOT_REACHED_TEXT) from error
        try:
            abilities = self._abilities(session)  # FTP has no shell
        except FTP_ERRORS as error:
            session.close()
            raise ConnectionFailed(NOT_REACHED_TEXT) from error
        self._ftp = session
        self.capabilities = abilities
        self.log.note("logged in, " + ("encrypted with TLS" if self.encrypted else "not encrypted"))

    def _abilities(self, session: ftplib.FTP) -> Capabilities:
        """What this server offers for a transfer, as it says itself: nothing is assumed (D-016). A refusal of a question means no."""
        try:
            features = {line.strip().split(" ")[0].upper() for line in session.sendcmd("FEAT").splitlines()[1:-1] if line.strip()}
        except ftplib.Error:
            features = set[str]()
        try:
            site = session.sendcmd("SITE HELP").upper()
        except ftplib.Error:
            site = ""
        offered = site.split()
        self._features = frozenset(features)
        self._site = tuple(sorted({word for word in offered if word.isalpha() and word != "HELP"} & SITE_COMMANDS))
        return Capabilities(resume="REST" in features, timestamps="MFMT" in features, rights="CHMOD" in offered, server_copy="CPFR" in offered and "CPTO" in offered)

    @override
    def close(self) -> None:
        session = self._ftp
        self._ftp = None
        self._afresh = False
        if session is not None:
            try:
                session.quit()
            except FTP_ERRORS:
                session.close()

    def _drop(self) -> None:
        session = self._ftp
        self._ftp = None
        self._binary = False
        if session is not None:
            session.close()

    def _ask_for_details(self, session: ftplib.FTP) -> None:
        """Rights and owner often come only when asked for. A server without this option simply sends what it has: that is no failure."""
        if session is self._asked:
            return
        try:
            session.sendcmd(DETAILS_COMMAND)
        except ftplib.error_perm:
            pass  # a passing refusal (4xx) goes up and is asked again next time
        self._asked = session

    def _session(self) -> ftplib.FTP:
        """The control connection; after a broken-off transfer a new login first. A server that takes no more logins now says so
        (NoMoreLogins); any other failure of that login is a lost connection."""
        if self._ftp is None and self._afresh:
            try:
                self.connect()
            except NoMoreLogins:
                raise  # the next call tries again
            except ChronoFtpError as error:
                self._afresh = False
                raise ConnectionLost(LOST_TEXT) from error
            self._afresh = False
        if self._ftp is None:
            raise RemoteActionFailed("The connection is closed.")
        return self._ftp

    @override
    def check(self) -> None:
        if not self._lock.acquire(blocking=False):
            return  # busy, so it is alive
        try:
            self._session().voidcmd("NOOP")
        except NoMoreLogins as error:
            raise ConnectionLost(LOST_TEXT) from error  # (a check says only whether it is there)
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
        return self.list_folder_all(path)[0]

    @override
    def list_folder_all(self, path: str) -> tuple[list[RemoteEntry], list[str]]:
        with self._lock:
            try:
                session = self._session()
                self._ask_for_details(session)
                self._binary = False  # a listing goes in ASCII mode, and leaves the session so
                found = list(session.mlsd(path))
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
        entries = [entry for name, facts in found if (entry := entry_from(name, facts)) is not None]
        left_out = [name for name, facts in found if facts.get("type", "").lower() not in ("cdir", "pdir") and name not in (".", "..") and not safe_name(name)]
        log_listing(self.log, entries, left_out)
        return entries, left_out

    def _do(self, *commands: tuple[str, str]) -> None:
        with self._lock:
            session = self._session()
            try:
                for method, argument in commands:
                    getattr(session, method)(argument)
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed(_refusal(error, "The server refused this action.")) from error

    @override
    def rename(self, old: str, new: str) -> None:
        with self._lock:
            try:
                self._session().rename(old, new)
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed(_refusal(error, "The server refused this action.")) from error
        moved = self._kept(old)
        self._keep(old, None)
        name = new.rsplit("/", 1)[-1]
        self._keep(new, replace(moved, name=name) if moved is not None else RemoteEntry(name, False, size_known=False))

    def _listed(self, folder: str, fresh: bool = False) -> list[RemoteEntry]:
        """A folder's listing: for a transfer read once and then kept up with what it changes there itself, so placing many files costs
        no listing each; for browsing, and where fresh is asked for, read afresh."""
        if not fresh and self._listings is not None and folder in self._listings:
            return list(self._listings[folder].values())
        found = self.list_folder(folder)
        if self._listings is not None:
            self._listings[folder] = {entry.name: entry for entry in found}
        return found

    def _kept(self, path: str) -> RemoteEntry | None:
        parent, name = parent_of(path), path.rsplit("/", 1)[-1]
        if self._listings is None or parent not in self._listings:
            return None
        return self._listings[parent].get(name)

    def _keep(self, path: str, entry: RemoteEntry | None) -> None:
        """What this connection changed at path, in a listing it keeps: entry, or None for gone."""
        parent, name = parent_of(path), path.rsplit("/", 1)[-1]
        if self._listings is None or parent not in self._listings:
            return
        if entry is None:
            self._listings[parent].pop(name, None)
        else:
            self._listings[parent][name] = entry  # in place: placing many files in one folder costs no rebuild of its listing each

    @override
    def make_folder(self, path: str) -> None:
        self._do(("mkd", path))
        self._keep(path, RemoteEntry(path.rsplit("/", 1)[-1], True))

    @override
    def delete(self, path: str, is_folder: bool) -> None:
        if not is_folder:
            self._do(("delete", path))
            self._keep(path, None)
            return
        for entry in self.list_folder(path):
            self.delete(joined(path, entry.name), entry.is_folder and not entry.is_link)
        self._do(("rmd", path))

    def _start_afresh(self) -> None:
        """A transfer broken off leaves its reply on the way: the control connection goes, and the next call logs in again (_session), so
        no later answer can belong to the wrong command, and a stop never waits for a login."""
        self._drop()
        self._afresh = True

    @override
    def set_mode(self, path: str, mode: str) -> None:
        with self._lock:
            try:
                self._session().voidcmd(f"SITE CHMOD {mode} {path}")
            except FTP_ERRORS:
                pass  # many servers can't: the file is there, only its permissions could not be set

    @override
    def set_rights(self, path: str, mode: str) -> None:
        self._do(("voidcmd", f"SITE CHMOD {mode} {path}"))

    @override
    def copy_file(self, source: str, target: str) -> None:
        """SITE CPFR names the original and SITE CPTO the copy; the server copies it itself, nothing travels through this computer. RNFR
        asks right before whether the name is taken, as a copy never replaces anything."""
        if not self.capabilities.server_copy:
            raise RemoteActionFailed(NO_COPY_TEXT)
        if self._exists(target):
            raise NameTaken(NAME_TAKEN_TEXT)
        try:
            self._do(("sendcmd", f"SITE CPFR {source}"), ("voidcmd", f"SITE CPTO {target}"))
        except RemoteActionFailed as error:
            self._remove_half_copy(target, error)  # the name was free before: whatever has it now is this copy, unfinished
            raise
        self._keep(target, RemoteEntry(target.rsplit("/", 1)[-1], False, size_known=False))

    def _remove_half_copy(self, target: str, error: RemoteActionFailed) -> None:
        try:
            if self._exists(target):
                self._do(("delete", target))
        except RemoteActionFailed as problem:
            text = f"{error} {HALF_COPY_LEFT_TEXT.format(path=target)}"
            raise (ConnectionLost(text) if isinstance(problem, ConnectionLost) or isinstance(error, ConnectionLost) else RemoteActionFailed(text)) from error

    @override
    def set_time(self, path: str, modified: float) -> None:
        try:
            moment = time.strftime("%Y%m%d%H%M%S", time.gmtime(modified))
        except (OverflowError, OSError, ValueError) as error:
            raise RemoteActionFailed(NO_TIME_TEXT) from error  # a date from input that no clock can hold (OR-15)
        self._do(("voidcmd", f"MFMT {moment} {path}"))

    @override
    def file_size(self, path: str) -> int | None:
        """SIZE where the server knows it; else the size MLST or the folder listing gives; None when nothing tells it."""
        with self._lock:
            try:
                self._binary_mode()  # many servers tell a size only in binary mode, and a listing leaves ASCII on
                size = self._session().size(path)
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except (ftplib.error_perm, ftplib.error_reply, ValueError):
                size = None  # refused, or a reply that is no number ("213 abc"): the size is unknown here, the listing tells it
            except FTP_ERRORS as error:
                raise RemoteActionFailed(_refusal(error, GONE_TEXT)) from error
        if not isinstance(size, int) or size < 0:
            return super().file_size(path)  # no size, or one no file can have: unknown here
        return size

    @override
    def details(self, path: str) -> RemoteEntry | None:
        """MLST of this one file; a refusal of it (not there, or not to be read) is looked into with RNFR, never with a listing. Where the
        server has no MLST, the folder's listing tells it (for a transfer read once per folder): what is there, never whether it changed."""
        return self._details(path, False)

    @override
    def fresh_details(self, path: str) -> RemoteEntry | None:
        """As the server has it this moment, for every check of whether something changed: MLST; where the server has no MLST, SIZE and
        MDTM of a file, else a fresh listing of its folder."""
        if self._mlst is False:
            sized = self._sized(path)
            if sized is not None:
                return sized
        return self._details(path, True)

    def _sized(self, path: str) -> RemoteEntry | None:
        """A file's size and date by SIZE and MDTM, each asked this moment; None where the server can't tell both (also for a folder)."""
        with self._lock:
            try:
                self._binary_mode()  # many servers tell a size only in binary mode
                size = self._session().size(path)
                reply = self._session().sendcmd(f"MDTM {path}")
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS:
                return None
        moment = _time_of(reply[4:].strip()) if reply.startswith("213") else None
        if not isinstance(size, int) or size < 0 or moment is None:
            return None
        return RemoteEntry(path.rsplit("/", 1)[-1], False, size, moment)

    def _details(self, path: str, fresh: bool) -> RemoteEntry | None:
        name = path.rsplit("/", 1)[-1]
        reply: str | None = None
        if self._mlst is not False:
            with self._lock:
                try:
                    reply = self._session().sendcmd(f"MLST {path}")
                    self._mlst = True
                except LOST_ERRORS as error:
                    raise ConnectionLost(LOST_TEXT) from error
                except ftplib.error_perm as error:
                    if not str(error).startswith(("500", "502")):
                        self._mlst = True
                    elif self._mlst is None:
                        self._mlst = False  # the server does not know it: the listing tells it from now on
                except FTP_ERRORS as error:
                    raise RemoteActionFailed(_refusal(error, GONE_TEXT)) from error
            if reply is None and self._mlst:
                probed = self._probe(path)  # not there (no listing needed), or there but not to be read with MLST
                if probed is None or probed.kind_known:
                    return probed
                try:
                    listed = next((entry for entry in self._listed(parent_of(path), fresh) if entry.name == name), None)
                except ConnectionLost:
                    raise
                except RemoteActionFailed:
                    return probed  # its folder can't be read either: whether it is a file or a folder stays unknown
                return listed if listed is not None else probed
        if reply is None:
            try:
                return next((entry for entry in self._listed(parent_of(path), fresh) if entry.name == name), None)
            except ConnectionLost:
                raise
            except RemoteActionFailed:
                return self._probe(path)  # a folder that can't be read: whether the name is taken is all that can be learnt
        lines = reply.splitlines()[1:-1]
        if not lines:
            return None
        facts_text = lines[0].strip().partition(" ")[0]
        facts = {key.lower(): value for key, _equals, value in (fact.partition("=") for fact in facts_text.split(";") if fact)}
        return entry_from(name, facts)

    def _binary_mode(self) -> None:
        """Binary mode for SIZE, asked only when the session is not in it already. Called under the lock."""
        if not self._binary:
            self._session().voidcmd("TYPE I")
            self._binary = True

    def _exists(self, path: str) -> bool:
        """Whether something has this name this moment: RNFR alone answers 350 for it, also in a folder that can't be read. One command."""
        with self._lock:
            try:
                self._session().sendcmd(f"RNFR {path}")
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except ftplib.error_perm:
                return False
            except FTP_ERRORS as error:
                raise RemoteActionFailed(_refusal(error, GONE_TEXT)) from error
        return True

    @override
    def name_taken(self, path: str) -> bool:
        """RNFR this moment, or what the kept listing knows of it (no command): never MLST and RNFR for the same check."""
        return self._kept(path) is not None or self._exists(path)

    def _probe(self, path: str) -> RemoteEntry | None:
        """RNFR answers 350 for a name that is there, also in a folder that can't be read; no RNTO follows, so nothing is renamed. SIZE
        then tells a file by its size, where the server answers it there; else whether it is a file or a folder is unknown."""
        name = path.rsplit("/", 1)[-1]
        with self._lock:
            try:
                self._session().sendcmd(f"RNFR {path}")
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except ftplib.error_perm:
                return None
            except FTP_ERRORS as error:
                raise RemoteActionFailed(_refusal(error, GONE_TEXT)) from error
            try:
                self._binary_mode()  # many servers tell a size only in binary mode
                size = self._session().size(path)
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS:
                size = None  # refused (a folder, or no SIZE here): nothing more can be learnt
        if isinstance(size, int) and size >= 0:
            return RemoteEntry(name, False, size)  # only a file has a size
        return RemoteEntry(name, False, size_known=False, kind_known=False)

    @override
    def revive(self) -> None:
        """A control connection the server closed while it was idle (a question took long) logs in again with what it has."""
        try:
            self.check()
            if self._ftp is not None:
                return
        except RemoteActionFailed:
            pass  # gone: a new login follows
        with self._lock:
            self._start_afresh()
            self._session()  # logged in again now; NoMoreLogins where the server takes no more at this moment

    @override
    def remove_empty_folder(self, path: str) -> None:
        try:
            self._do(("rmd", path))
        except ConnectionLost:
            raise
        except RemoteActionFailed as error:
            raise RemoteActionFailed(NOT_EMPTY_TEXT) from error

    @override
    def download(self, remote: str, local: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        with self._lock, LocalFile(local, "ab" if append else "r+b" if offset else "xb" if exclusive else "wb", True) as file:  # every step here is this computer's
            if offset:
                file.truncate(offset)  # a continued part: whatever is after offset goes
                file.seek(offset)
            before = file.seek(0, 2)

            def take(block: bytes) -> None:
                file.write(block)
                progress(len(block))
                for pause in limit_pauses(limit, len(block)):
                    time.sleep(pause)
                    progress(0)  # a stop meanwhile ends it now

            try:
                self._data(lambda: self._session().retrbinary(f"RETR {remote}", take, BLOCK_SIZE, rest=offset or None), bool(offset))  # REST: from offset
            except BaseException:
                if append:
                    try:
                        file.truncate(before)  # an append is all or nothing: the file is the one it was
                    except LocalActionFailed:
                        pass  # the first problem is the one to tell
                raise

    @override
    def upload(self, local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        """An append can't be undone here (FTP can't cut a file back), so the overwrite question does not offer it over FTP. exclusive: FTP
        can't make a file only where nothing is, so the name is asked of the server right before (RNFR), as for a new file's name."""
        if exclusive and not offset and not append and self._exists(remote):
            raise NameTaken(NAME_TAKEN_TEXT)
        if offset and not append and self.file_size(remote) != offset:
            raise ContinuationRefused(PART_NOT_AS_LEFT_TEXT)  # REST would write after a gap, or into bytes that are not ours: from the start
        def sent(block: bytes) -> None:
            progress(len(block))  # sent: the server holds what it got of it in order, so at most this
            for pause in limit_pauses(limit, len(block)):
                time.sleep(pause)
                progress(0)  # a stop meanwhile ends it now

        try:
            with self._lock, LocalFile(local, "rb", False) as file:  # a read that fails is this computer's problem, never a broken line
                file.seek(offset)
                if append:
                    self._data(lambda: self._session().storbinary(f"APPE {remote}", file, BLOCK_SIZE, sent))  # after what is there
                else:
                    self._data(lambda: self._session().storbinary(f"STOR {remote}", file, BLOCK_SIZE, sent, rest=offset or None), bool(offset))  # REST: from offset
        finally:
            self._keep(remote, RemoteEntry(remote.rsplit("/", 1)[-1], False, size_known=False))  # there now, of a size only SIZE tells

    def _data(self, transfer: Callable[[], object], continued: bool = False) -> None:
        """One transfer over a data connection. Only a control connection that is gone stops the whole job. A data connection that drops
        (425, 426, a reset, a broken TLS session) interrupts this one file; 450 and 452 are the server's refusal of this one file, now; a
        5xx is its refusal, of the continuation itself where it continues a part (continued). After anything but a clean end the control connection starts afresh, so no later answer belongs to the
        wrong command; when that fails, the login is gone."""
        self._binary = True  # ftplib asks for binary mode itself before every file
        try:
            transfer()
        except EOFError as error:
            self._start_afresh()
            raise ConnectionLost(LOST_TEXT) from error  # the control connection closed
        except ftplib.error_temp as error:
            reply = str(error).strip()
            if reply.startswith(("450", "452")):
                raise RefusedForNow((NO_ROOM_TEXT if reply.startswith("452") else BUSY_TEXT).format(reply=reply)) from error
            self._start_afresh()
            if reply.startswith("421"):
                raise ConnectionLost(LOST_TEXT) from error
            raise TransferInterrupted(INTERRUPTED_TEXT) from error  # the next call logs in again; where that fails, it says so
        except (ssl.SSLError, OSError) as error:  # a reset or a timeout of the data connection
            self._start_afresh()
            raise TransferInterrupted(INTERRUPTED_TEXT) from error
        except FTP_ERRORS as error:
            raise (ContinuationRefused if continued else RemoteActionFailed)(_refusal(error, "The transfer did not finish.")) from error
        except BaseException:
            self._start_afresh()
            raise

    @override
    def can_split(self, upload: bool) -> bool:
        return not upload and self.capabilities.resume  # FTP can't write at a place in the middle; a download starts at one with REST

    @override
    def another_login(self) -> "FtpConnection":
        return self.for_a_transfer()

    @override
    def download_range(self, remote: str, local: str, start: int, end: int, progress: Progress, limit: Limit = 0) -> None:
        """REST start, and the data connection is ended once the range is there; the server's answer to that is read, so the control
        connection goes on with the next command."""
        wanted = end - start
        got = 0
        with self._lock, LocalFile(local, "r+b", True) as file:
            file.seek(start)

            def take(block: bytes) -> None:
                nonlocal got
                block = block[: wanted - got]
                file.write(block)
                got += len(block)
                progress(len(block))
                for pause in limit_pauses(limit, len(block)):
                    time.sleep(pause)
                    progress(0)  # a stop meanwhile ends it now
                if got >= wanted:
                    raise _RangeDone

            def fetch() -> None:
                session = self._session()
                try:
                    session.retrbinary(f"RETR {remote}", take, BLOCK_SIZE, rest=start or None)
                except _RangeDone:
                    try:
                        session.voidresp()  # the answer to the data connection we ended: 426, 226, or any other: the range is here
                    except (ftplib.error_temp, ftplib.error_perm, ftplib.error_reply):
                        pass  # whatever the server says to an end it did not choose is no failure of the range
                    except (OSError, EOFError, ftplib.error_proto):
                        self._start_afresh()  # no clear answer: the next call logs in again, so no later answer belongs to the wrong command

            self._data(fetch, True)
        if got != wanted:
            raise RemoteActionFailed(SHORT_RANGE_TEXT)

    @override
    def server_info(self) -> ServerInfo:
        """The greeting, SYST, TLS with its certificate, and FEAT (FP-03)."""
        with self._lock:
            try:
                session = self._session()
                try:
                    system = session.sendcmd("SYST").removeprefix("215").strip()
                except ftplib.Error:
                    system = ""  # a server may refuse to say
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS as error:
                raise RemoteActionFailed(_refusal(error, "The server did not answer.")) from error
            welcome = " ".join(line[4:].strip() for line in (session.welcome or "").splitlines() if line[4:].strip())
            sock = session.sock
        software = ", ".join(part for part in (welcome, system) if part)
        encryption: list[tuple[str, str]] = []
        identity: list[tuple[str, str]] = []
        if isinstance(sock, ssl.SSLSocket):
            cipher = sock.cipher()
            encryption.append(("TLS version", sock.version() or ""))
            if cipher is not None:
                encryption.append(("Cipher", f"{cipher[0]} ({cipher[2]} bits)"))
            certificate = sock.getpeercert(binary_form=True)
            if certificate:
                identity.extend(certificate_lines(certificate))
        protocol = "FTP over TLS" if isinstance(sock, ssl.SSLSocket) else "FTP, not encrypted"
        offers = tuple(sorted(self._features)) + tuple(f"SITE {command}" for command in self._site)
        return ServerInfo(protocol, software, tuple(encryption), tuple(identity), offers)

    @override
    def free_space(self, path: str) -> FreeSpace | None:
        """AVBL where FEAT offers it; FTP has no other way to tell."""
        if "AVBL" not in self._features:
            return None
        with self._lock:
            try:
                reply = self._session().sendcmd(f"AVBL {path}")
            except LOST_ERRORS as error:
                raise ConnectionLost(LOST_TEXT) from error
            except FTP_ERRORS:
                return None  # refused for this folder
        found = AVBL_REPLY.fullmatch(reply.strip())
        return FreeSpace(int(found.group(1)), None, "AVBL") if found is not None else None

    @override
    def for_a_transfer(self) -> "FtpConnection":
        """FTP does one thing at a time, so a transfer gets a login of its own and browsing goes on. It keeps the listings it read."""
        other = FtpConnection(self.connection, self.password)
        other.must_encrypt = self.encrypted  # a transfer never travels with less protection than the browsing
        other._listings = {}
        other.connect()
        return other

    @override
    def place_new(self, temporary: str, target: str) -> None:
        """FTP's rename replaces what has the name: right before it, RNFR asks whether something has it, which needs no listing. Something
        that takes it in that last moment is replaced all the same, a limit of FTP."""
        if self.name_taken(target):
            raise NameTaken(NAME_TAKEN_TEXT)
        self.rename(temporary, target)
