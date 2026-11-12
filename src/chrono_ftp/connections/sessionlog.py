"""The session log (FP-02): per connection, what the program asks the server and what it answers, written to the state folder of
Chrono FTP. Passwords and keys are never written, whatever the level (OR-19): every line is cleaned right before it is written or shown."""

import hashlib
import os
import re
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from chrono_ftp.core.logrules import DEBUG, DETAILED, MOST_KEPT, NORMAL, OFF, LogRules

# --- Version ------------------------------------------------------------
__version__ = "sessionlog.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: a log per connection in three levels, off by default, rotated by size and count, shown live; passwords, keys and
#          the FTP PASS line are never written (FP-02, D-065); review: a watcher gets the lines of one write together, so a long
#          listing is one call; work the program does by itself can be written as a summary, its details only at Debug

MEGABYTE: int = 1024 * 1024
FOLDER_NAME: str = "logs"
SUFFIX: str = ".log"
HIDDEN: str = "***"
KEY_TEXT: str = "[a private key, not written]"
REQUEST: str = ">"
ANSWER: str = "<"
NOTE: str = "."
PROBLEM: str = "!"
NOT_WRITTEN_TEXT: str = "The session log could not be written: {reason}. The connection goes on without it."
NAME_CHARACTERS: re.Pattern[str] = re.compile(r"[^A-Za-z0-9._-]+")
PASSWORD_COMMAND: re.Pattern[str] = re.compile(r"^(\s*(?:PASS|ACCT)\b)\s*.*$", re.IGNORECASE)  # FTP sends the password in plain text after it
PRIVATE_KEY: re.Pattern[str] = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(-----END [A-Z0-9 ]*PRIVATE KEY-----|$)", re.DOTALL)
SECRET_WORDS: re.Pattern[str] = re.compile(r"(?i)\b(password|passphrase|secret)(\s*[=:]\s*)\S+")
Watcher = Callable[[list[str]], None]  # gets the lines of each write together, cleaned, on the thread that wrote them


_rules: LogRules = LogRules()
_logs: dict[str, "SessionLog"] = {}
_logs_lock: threading.Lock = threading.Lock()


def state_folder() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "chrono-ftp"


def log_folder() -> Path:
    return state_folder() / FOLDER_NAME


def set_rules(rules: LogRules) -> None:
    """The settings of every log, from now on (the Log page of Settings)."""
    global _rules
    _rules = rules


def rules() -> LogRules:
    return _rules


def file_name(connection_name: str) -> str:
    """A name the file system takes for any connection name; the short hash keeps two names that clean up alike apart."""
    readable = NAME_CHARACTERS.sub("_", connection_name).strip("._")[:60] or "connection"
    digest = hashlib.sha256(connection_name.encode("utf-8", "surrogateescape")).hexdigest()[:6]
    return f"{readable}-{digest}{SUFFIX}"


def rotated(path: Path, number: int) -> Path:
    return path.with_name(f"{path.name}.{number}")


def log_for(connection_name: str) -> "SessionLog":
    """The one log of this connection, shared by every login of it (browsing, transfers, the parts of a split file)."""
    with _logs_lock:
        found = _logs.get(connection_name)
        if found is None:
            found = SessionLog(connection_name)
            _logs[connection_name] = found
        return found


def cleaned(line: str, secrets: tuple[str, ...]) -> str:
    """A line as it may be written: a password typed or stored, the text after FTP's PASS, a private key and a value named a password
    are replaced, whatever the level (OR-19)."""
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            line = line.replace(secret, HIDDEN)
    line = PASSWORD_COMMAND.sub(lambda found: f"{found.group(1).strip()} {HIDDEN}", line)
    line = PRIVATE_KEY.sub(KEY_TEXT, line)  # the whole text at once, so the lines of a key between its BEGIN and END go too
    return SECRET_WORDS.sub(lambda found: f"{found.group(1)}{found.group(2)}{HIDDEN}", line)


class SessionLog:
    """The log of one connection. Every method may be called from any thread, and none ever raises: a log that can't be written says so
    once in problem, and the connection goes on (OR-15)."""

    def __init__(self, connection_name: str | None) -> None:
        self.connection_name: str | None = connection_name  # None: a log that writes nothing, for a connection made without one
        self.problem: str = ""
        self._secrets: tuple[str, ...] = ()
        self._watchers: list[Watcher] = []
        self._summarising: threading.local = threading.local()  # per thread: the work of this thread is written as a summary
        self._lock: threading.Lock = threading.Lock()

    @property
    def path(self) -> Path | None:
        """Where the log is written; read each time, so it follows the state folder of the moment."""
        return None if self.connection_name is None else log_folder() / file_name(self.connection_name)

    def wanted(self, level: int) -> bool:
        if getattr(self._summarising, "on", False):
            level = DEBUG  # work the program did by itself: its details only at Debug, its summary is written apart
        return _rules.level >= level > OFF and self.connection_name is not None  # cheap: asked for every request, also with the log off

    @contextmanager
    def summary_only(self) -> Generator[None]:
        """What this thread asks the server inside the block is written only at Debug; the caller writes one summary line itself, so
        work the program repeats by itself (the automatic refresh) never pushes the real history out of the log."""
        self._summarising.on = True
        try:
            yield
        finally:
            self._summarising.on = False

    def summary(self, text: str) -> None:
        """One line for work done under summary_only, at Normal and above; written from outside that block (another thread)."""
        self.write(NOTE, [text], NORMAL)

    def hide(self, secret: str | None) -> None:
        """A password this connection uses: it is never written, wherever it would appear."""
        if secret:
            with self._lock:
                if secret not in self._secrets:
                    self._secrets = (*self._secrets, secret)

    def watch(self, watcher: Watcher) -> None:
        with self._lock:
            self._watchers.append(watcher)

    def unwatch(self, watcher: Watcher) -> None:
        with self._lock:
            if watcher in self._watchers:
                self._watchers.remove(watcher)

    def asked(self, text: str, level: int = NORMAL) -> None:
        self.write(REQUEST, [text], level)

    def answered(self, text: str, level: int = NORMAL) -> None:
        self.write(ANSWER, [text], level)

    def answered_lines(self, lines: list[str], level: int = DETAILED) -> None:
        self.write(ANSWER, lines, level)

    def note(self, text: str, level: int = NORMAL) -> None:
        self.write(NOTE, [text], level)

    def failed(self, text: str, level: int = NORMAL) -> None:
        self.write(PROBLEM, [text], level)

    def write(self, mark: str, texts: list[str], level: int) -> None:
        if not self.wanted(level):
            return
        path = self.path
        if path is None:
            return
        moment = time.strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            whole = cleaned("\n".join(texts), self._secrets)  # cleaned as one text: a key over several lines is seen whole
            lines = [f"{moment} {mark} {part}" for part in (whole.splitlines() or [""])]
            data = "".join(line + "\n" for line in lines).encode("utf-8", "replace")  # a name that is not valid UTF-8 shows replaced
            try:
                self._rotate_for(path, len(data))
                descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.write(descriptor, data)
                finally:
                    os.close(descriptor)
                self.problem = ""
            except OSError as error:
                self.problem = NOT_WRITTEN_TEXT.format(reason=error.strerror or str(error))
            watchers = list(self._watchers)
        for watcher in watchers:
            try:
                watcher(lines)
            except Exception:  # a window that went meanwhile: the log itself never fails on it
                pass

    def _rotate_for(self, path: Path, adding: int) -> None:
        """Before a line that would take the log past its size: the oldest goes, each older one moves one number on, and the log
        becomes .1. With no older logs kept, the log starts afresh."""
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return
        if size == 0 or size + adding <= _rules.size_megabytes * MEGABYTE:
            return
        kept = _rules.kept
        for number in range(MOST_KEPT, kept, -1):
            rotated(path, number).unlink(missing_ok=True)  # also what a larger number of kept logs left behind
        for number in range(kept - 1, 0, -1):
            older = rotated(path, number)
            if older.exists():
                older.replace(rotated(path, number + 1))
        if kept:
            path.replace(rotated(path, 1))
        else:
            path.unlink()

    def tail(self, most_bytes: int) -> str:
        """The end of the log as it is on the disk, for the Log window; empty when there is none or it can't be read."""
        path = self.path
        if path is None:
            return ""
        try:
            with path.open("rb") as file:
                size = file.seek(0, os.SEEK_END)
                file.seek(max(0, size - most_bytes))
                data = file.read()
        except OSError:
            return ""
        text = data.decode("utf-8", "replace")
        return text.split("\n", 1)[1] if size > most_bytes and "\n" in text else text  # a cut first line is left out


QUIET: SessionLog = SessionLog(None)  # for a connection made without a log (a test's stand-in): it writes nothing
