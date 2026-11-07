import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import cast

from chrono_ftp.core.connection import DEFAULT_PORTS, HIGHEST_PORT, LOWEST_PORT, PROTOCOLS, SFTP, Connection

# --- Version ------------------------------------------------------------
__version__ = "connections.py 0.0.8"

# --- Version history ----------------------------------------------------
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
    for field in ("user", "key_file", "colour", "start_folder"):
        value = values.get(field, "")
        if not isinstance(value, str):
            return None
        texts[field] = value
    start = Connection(name=name, host=host, user=texts["user"], port=port, protocol=protocol, key_file=texts["key_file"], start_folder=texts["start_folder"])
    return start if not texts["colour"] else Connection(**{**asdict(start), "colour": texts["colour"]})


def _set_aside(path: Path) -> Path | None:
    copy = path.with_name(path.name + SET_ASIDE_SUFFIX)
    number = 1
    while copy.exists():
        number += 1
        copy = path.with_name(f"{path.name}{SET_ASIDE_SUFFIX}{number}")
    try:
        copy.write_bytes(path.read_bytes())
    except OSError:
        return None  # it can't even be read: the file itself stays as it is
    return copy


def load() -> LoadedList:
    path = list_path()
    if not path.exists():
        return LoadedList((), None)
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return LoadedList((), _set_aside(path))
    entries: object = cast(dict[str, object], data).get("connections") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return LoadedList((), _set_aside(path))
    found = [_connection_from(entry) for entry in cast(list[object], entries)]
    usable = tuple(connection for connection in found if connection is not None)
    return LoadedList(usable, _set_aside(path) if len(usable) != len(found) else None)


def remember_user(name: str, user: str) -> None:
    """The user name a server accepted, for the connection of this name. A connection removed in the meantime stays removed. A file that
    can't be read in full is left exactly as it is: the rest of the list may only be in there."""
    try:
        data: object = json.loads(list_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return
    entries: object = cast(dict[str, object], data).get("connections") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return
    found = [_connection_from(entry) for entry in cast(list[object], entries)]
    usable = [connection for connection in found if connection is not None]
    if len(usable) == len(found) and any(connection.name == name for connection in usable):
        save([replace(connection, user=user) if connection.name == name else connection for connection in usable])


def save(connections: Sequence[Connection]) -> None:
    """Written to a new file and moved into place in one step, so a crash leaves the old list whole."""
    path = list_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    text = json.dumps({"connections": [asdict(connection) for connection in connections]}, indent=2, ensure_ascii=False)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
