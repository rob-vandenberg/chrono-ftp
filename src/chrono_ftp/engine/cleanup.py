"""Clean up stored data (FP-04): what Chrono FTP keeps on this computer, each kind with where it is and its size, and its removal, which
takes exactly the kinds the person ticked and nothing else."""

import os
import shutil
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from chrono_ftp.config import bookmarks, connections, parts, settings
from chrono_ftp.config.connections import SET_ASIDE_SUFFIX, config_folder
from chrono_ftp.connections import sessionlog, trust
from chrono_ftp.core import credentials
from chrono_ftp.core.errors import CredentialError
from chrono_ftp.engine import terminal

# --- Version ------------------------------------------------------------
__version__ = "cleanup.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: saved connections with their passwords, trusted servers, bookmarks, window places and settings, notes of unfinished
#          transfers with leftover edit copies, session logs and the terminal's colour schemes; each with its size, removed only when
#          ticked (FP-04, D-067)

CONNECTIONS: str = "connections"
TRUST: str = "trust"
BOOKMARKS: str = "bookmarks"
SETTINGS: str = "settings"
NOTES: str = "notes"
LOGS: str = "logs"
SCHEMES: str = "schemes"
EDIT_FOLDER_PREFIX: str = "chrono-ftp-edit-"  # the temporary folder of a server file open in a program here (Edit)
SCHEME_PREFIX: str = "chrono-ftp-"
SCHEME_SUFFIX: str = ".colorscheme"
NOTE_FILES: tuple[str, ...] = (parts.FILE_NAME, parts.APPENDS_FILE_NAME, parts.OLDS_FILE_NAME, parts.CLAIMS_FILE_NAME, parts.PARTIALS_FILE_NAME)
BUSY_TEXT: str = "Transfers are in the queue. Their notes are needed until they are done, so this can be cleaned up once the queue is empty."
NOT_REMOVED_TEXT: str = "{path} could not be removed: {reason}"
PASSWORDS_TEXT: str = "The password store could not be reached for {names}, so the saved connections were kept; you can try again later."


@dataclass(frozen=True)
class StoredKind:
    """One kind of stored data: what it is, the files it is in now, and their size together."""

    key: str
    title: str
    explanation: str
    paths: tuple[Path, ...]
    size: int
    passwords: tuple[str, ...] = ()  # the connections whose password goes from the credential store with it
    busy: str = ""  # why it can't be cleaned up now; empty when it can


def _set_aside(path: Path) -> list[Path]:
    """A file and the copies set aside of it when it was damaged (config.connections.set_aside_copy)."""
    found = [path] if path.exists() else []
    try:
        found += sorted(other for other in path.parent.iterdir() if other.name.startswith(path.name + SET_ASIDE_SUFFIX))
    except OSError:
        pass  # the folder is not there or can't be read: nothing of it
    return found


def _size(paths: Iterable[Path]) -> int:
    total = 0
    for path in paths:
        try:
            if path.is_dir() and not path.is_symlink():
                for folder, _folders, files in os.walk(path):
                    for name in files:
                        try:
                            total += os.lstat(os.path.join(folder, name)).st_size
                        except OSError:
                            pass  # gone meanwhile
            else:
                total += path.lstat().st_size
        except OSError:
            pass  # gone meanwhile, or can't be read: it counts nothing
    return total


def edit_folders(in_use: Iterable[str]) -> list[Path]:
    """The temporary folders of earlier edits that nothing uses now: the ones of files open for editing stay."""
    keep = {Path(path).resolve() for path in in_use}
    try:
        found = [entry for entry in Path(tempfile.gettempdir()).iterdir() if entry.name.startswith(EDIT_FOLDER_PREFIX)]
    except OSError:
        return []
    return sorted(path for path in found if path.is_dir() and not path.is_symlink() and path.resolve() not in keep and os.access(path, os.W_OK))


def _schemes() -> list[Path]:
    try:
        folder = terminal.scheme_folder()
        return sorted(path for path in folder.iterdir() if path.name.startswith(SCHEME_PREFIX) and path.name.endswith(SCHEME_SUFFIX))
    except OSError:
        return []


def _logs() -> list[Path]:
    folder = sessionlog.log_folder()
    return [folder] if folder.is_dir() else []


def stored_kinds(edits_in_use: Iterable[str], transfers_queued: bool) -> list[StoredKind]:
    """What Chrono FTP keeps now, kind by kind, in the order of the window. edits_in_use: the folders of files open for editing, which
    stay; transfers_queued: the notes of unfinished transfers are in use."""
    folder = config_folder()
    try:
        names = tuple(connection.name for connection in connections.load().connections)
    except OSError:
        names = ()
    connection_files = _set_aside(folder / connections.FILE_NAME)
    trust_files = [path for path in (trust.known_hosts_path(), trust.certificates_path()) if path.exists()]
    bookmark_files = _set_aside(folder / bookmarks.FILE_NAME)
    settings_files = [path for name in (settings.FILE_NAME, settings.WINDOW_FILE_NAME) for path in _set_aside(folder / name)]
    note_files = [path for name in NOTE_FILES for path in _set_aside(folder / name)] + edit_folders(edits_in_use)
    log_files = _logs()
    scheme_files = _schemes()
    return [
        StoredKind(CONNECTIONS, "Saved connections", f"{len(names)} connections, with their passwords in the password store", tuple(connection_files), _size(connection_files), names),
        StoredKind(TRUST, "Trusted servers", "The host keys and certificates you said yes to", tuple(trust_files), _size(trust_files)),
        StoredKind(BOOKMARKS, "Bookmarks and location profiles", "Also the paths typed in Open folder", tuple(bookmark_files), _size(bookmark_files)),
        StoredKind(SETTINGS, "Window places and settings", "The sizes and places of the windows, and every setting", tuple(settings_files), _size(settings_files)),
        StoredKind(NOTES, "Notes of unfinished transfers and leftover edit copies", "What lets a transfer go on later, and copies of server files edited earlier", tuple(note_files), _size(note_files), busy=BUSY_TEXT if transfers_queued else ""),
        StoredKind(LOGS, "Session logs", "What was asked of the servers and what they answered", tuple(log_files), _size(log_files)),
        StoredKind(SCHEMES, "Terminal colour schemes", "The colours of the terminal for connections with a colour", tuple(scheme_files), _size(scheme_files)),
    ]


def clean(kinds: Iterable[StoredKind]) -> list[str]:
    """Removes the files of exactly these kinds, and for saved connections their passwords. Returns what could not be removed, in plain
    words; the rest goes all the same."""
    problems: list[str] = []
    for kind in kinds:
        if kind.busy:
            continue  # never while in use, even when asked
        kept: list[str] = []
        for name in kind.passwords:  # first: once the list is gone, nothing would find these names again
            try:
                credentials.store.remove(name)
            except CredentialError:
                kept.append(name)
        if kept:
            problems.append(PASSWORDS_TEXT.format(names=", ".join(kept)))
            continue  # the list stays, so a later Clean Up can still remove those passwords
        for path in kind.paths:
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as error:
                problems.append(NOT_REMOVED_TEXT.format(path=path, reason=error.strerror or str(error)))
    return problems
