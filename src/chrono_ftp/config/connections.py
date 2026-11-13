import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import cast

from chrono_ftp.core.connection import DEFAULT_PORTS, HIGHEST_PORT, LOWEST_PORT, PROTOCOLS, SFTP, Connection

# --- Version ------------------------------------------------------------
__version__ = "connections.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: The name a tab of this connection was given, kept with the connection; an older file without it still loads (FI-01)
# v0.0.34: A local starting folder and "Remember the last folders"; an older file without them still loads (FH-06); set_aside_copy is
#          shared with the bookmarks file; written escaped, so a folder whose name is not valid UTF-8 comes back exactly, and no
#          .tmp file is left behind on any failure (write_whole, shared by every settings file); a set-aside copy is private, and the
#          same damage is set aside once
# v0.0.9: An empty colour stays empty
# v0.0.8: A user name the server accepted is kept (D-040)
# v0.0.5: Initial: the connection list in one file, written atomically; a damaged file is kept aside (D-017, OR-15)

FILE_NAME: str = "connections.json"
SET_ASIDE_SUFFIX: str = ".damaged"


@dataclass(frozen=True)
class LoadedList:
    connections: tuple[Connection, ...]
    set_aside: Path | None  # the copy of a file that could not be read in full


def config_folder() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "chrono-ftp"


def list_path() -> Path:
    return config_folder() / FILE_NAME


def _connection_from(entry: object) -> Connection | None:
    """A connection from one entry of the file; None when it can't be used. The file is input, so nothing in it is trusted (OR-15)."""
    if not isinstance(entry, dict):
        return None
    values = cast(dict[str, object], entry)
    name, host, protocol, port = values.get("name"), values.get("host"), values.get("protocol", SFTP), values.get("port")
    if not isinstance(name, str) or not name or not isinstance(host, str) or not host or not isinstance(protocol, str) or protocol not in PROTOCOLS:
        return None
    if port is None:
        port = DEFAULT_PORTS[protocol]
    if not isinstance(port, int) or isinstance(port, bool) or not LOWEST_PORT <= port <= HIGHEST_PORT:
        return None
    texts: dict[str, str] = {}
    for field in ("user", "key_file", "colour", "start_folder", "local_start_folder", "tab_name"):
        value = values.get(field, "")
        if not isinstance(value, str):
            return None
        texts[field] = value
    remember = values.get("remember_folders", False)  # not in a file of before 0.0.34: off
    if not isinstance(remember, bool):
        return None
    return Connection(
        name=name,
        host=host,
        user=texts["user"],
        port=port,
        protocol=protocol,
        key_file=texts["key_file"],
        colour=texts["colour"],
        start_folder=texts["start_folder"],
        local_start_folder=texts["local_start_folder"],
        remember_folders=remember,
        tab_name=texts["tab_name"],
    )


def set_aside_copy(path: Path) -> Path | None:
    """A copy of a file that could not be read in full, readable by its owner only, as the file. A copy with the same content that is
    there already is the copy: the same damage is set aside once, however often it is read."""
    try:
        content = path.read_bytes()
    except OSError:
        return None  # it can't even be read: the file itself stays as it is
    copy = path.with_name(path.name + SET_ASIDE_SUFFIX)
    number = 1
    while copy.exists():
        try:
            if copy.read_bytes() == content:
                return copy
        except OSError:
            pass
        number += 1
        copy = path.with_name(f"{path.name}{SET_ASIDE_SUFFIX}{number}")
    try:
        descriptor = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return None
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
    except OSError:
        copy.unlink(missing_ok=True)  # half a copy is no copy
        return None
    return copy


def load() -> LoadedList:
    path = list_path()
    if not path.exists():
        return LoadedList((), None)
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return LoadedList((), set_aside_copy(path))
    entries: object = cast(dict[str, object], data).get("connections") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return LoadedList((), set_aside_copy(path))
    found = [_connection_from(entry) for entry in cast(list[object], entries)]
    usable = tuple(connection for connection in found if connection is not None)
    return LoadedList(usable, set_aside_copy(path) if len(usable) != len(found) else None)


def _whole_list() -> list[Connection] | None:
    """The list as it is in the file now; None when the file can't be read in full: the rest of the list may only be in there."""
    try:
        data: object = json.loads(list_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    entries: object = cast(dict[str, object], data).get("connections") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    found = [_connection_from(entry) for entry in cast(list[object], entries)]
    usable = [connection for connection in found if connection is not None]
    return usable if len(usable) == len(found) else None


def remember_user(name: str, user: str) -> None:
    """The user name a server accepted, for the connection of this name. A connection removed in the meantime stays removed. A file that
    can't be read in full is left exactly as it is."""
    usable = _whole_list()
    if usable is not None and any(connection.name == name for connection in usable):
        save([replace(connection, user=user) if connection.name == name else connection for connection in usable])


def remember_tab_name(name: str, tab_name: str) -> bool:
    """The name the person gave the tab of this connection, kept with the connection (FI-01). False when nothing was written: the
    connection is gone, or the file can't be read in full, and is then left exactly as it is."""
    usable = _whole_list()
    kept = next((connection for connection in usable or [] if connection.name == name), None)
    if usable is None or kept is None:
        return False
    save([replace(connection, tab_name=tab_name) if connection is kept else connection for connection in usable])
    return True


def remember_folders(name: str, local: str, remote: str) -> Connection | None:
    """The last folders of a tab become the starting folders of its connection, when that connection, as the file has it now, remembers
    its last folders. An empty folder keeps the one there was. Returns the connection as saved; None when nothing was written."""
    usable = _whole_list()
    kept = next((connection for connection in usable or [] if connection.name == name), None)
    if usable is None or kept is None or not kept.remember_folders:
        return None
    changed = replace(kept, local_start_folder=local or kept.local_start_folder, start_folder=remote or kept.start_folder)
    if changed != kept:
        save([changed if connection is kept else connection for connection in usable])
    return changed


def write_whole(path: Path, data: object, private: bool = True) -> None:
    """A settings file written to a new file and moved into place in one step, so a crash leaves the old one whole. Text is written
    escaped (ensure_ascii), so a name that is not valid UTF-8 comes back exactly as it was. Whatever fails, no new file is left behind."""
    text = json.dumps(data, indent=2, ensure_ascii=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def save(connections: Sequence[Connection]) -> None:
    """The whole list, written as write_whole writes: a crash leaves the old list whole."""
    write_whole(list_path(), {"connections": [asdict(connection) for connection in connections]})
