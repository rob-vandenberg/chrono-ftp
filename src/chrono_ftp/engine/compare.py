import os
from dataclasses import dataclass
from enum import Enum

from chrono_ftp.connections.base import OLD_SUFFIX, RemoteConnection, RemoteEntry, joined
from chrono_ftp.core.errors import ConnectionLost, LocalActionFailed, RemoteActionFailed
from chrono_ftp.engine import localfiles
from chrono_ftp.transfers.queue import PART_SUFFIX

# --- Version ------------------------------------------------------------
__version__ = "compare.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial: what differs between a local and a remote folder, by size and date, so it works on every connection

UNREADABLE_TEXT: str = "This folder on this computer can't be read."
DATE_MARGIN_SECONDS: float = 2.0  # some servers keep dates in steps of two seconds


class Difference(Enum):
    ONLY_LOCAL = "Only on this computer"
    ONLY_REMOTE = "Only on the server"
    NEWER_LOCAL = "Newer on this computer"
    NEWER_REMOTE = "Newer on the server"
    SIZE = "Other size, same date"
    DATE = "Same size, other date"
    KIND = "A file on one side, a folder on the other"
    UNREADABLE = "This folder can't be read"


LEFT_ALONE: frozenset[Difference] = frozenset({Difference.KIND, Difference.UNREADABLE})  # shown, never copied
OUR_SUFFIXES: tuple[str, ...] = (OLD_SUFFIX, PART_SUFFIX)  # our own temporary files are no content of the folder


@dataclass(frozen=True)
class Found:
    """One thing that differs. A folder on one side only is one row: it is copied whole."""

    folder: str  # relative to the two folders compared, "" for the top
    name: str
    difference: Difference
    local: RemoteEntry | None
    remote: RemoteEntry | None


def difference_of(local: RemoteEntry, remote: RemoteEntry) -> Difference | None:
    same_size = local.size == remote.size
    if local.modified is None or remote.modified is None or abs(local.modified - remote.modified) <= DATE_MARGIN_SECONDS:
        return None if same_size else Difference.SIZE
    if same_size:
        return Difference.DATE  # a copy gets a new date on many servers: the content is probably the same, so it is not chosen by itself
    return Difference.NEWER_LOCAL if local.modified > remote.modified else Difference.NEWER_REMOTE


def compare_folders(connection: RemoteConnection, local_folder: str, remote_folder: str, inside: str = "") -> list[Found]:
    """A name that is a link on either side is left out: a copy would write through it, into another tree. A folder that can't be read is
    told, never taken for empty, or all that is in it would look one-sided."""
    read = localfiles.read_folder(local_folder)
    if read is None:
        raise LocalActionFailed(UNREADABLE_TEXT)
    here = {entry.name: entry for entry in read if not entry.name.endswith(OUR_SUFFIXES)}
    there = {entry.name: entry for entry in connection.list_folder(remote_folder) if not entry.name.endswith(OUR_SUFFIXES)}
    found: list[Found] = []
    for name in sorted(here.keys() | there.keys()):
        local, remote = here.get(name), there.get(name)
        if (local is not None and local.is_link) or (remote is not None and remote.is_link):
            continue
        if remote is None:
            found.append(Found(inside, name, Difference.ONLY_LOCAL, local, None))
        elif local is None:
            found.append(Found(inside, name, Difference.ONLY_REMOTE, None, remote))
        elif local.is_folder and remote.is_folder:
            try:
                found += compare_folders(connection, os.path.join(local_folder, name), joined(remote_folder, name), joined(inside, name) if inside else name)
            except ConnectionLost:
                raise
            except (RemoteActionFailed, LocalActionFailed):
                found.append(Found(inside, name, Difference.UNREADABLE, local, remote))
        elif local.is_folder != remote.is_folder:
            found.append(Found(inside, name, Difference.KIND, local, remote))
        elif (difference := difference_of(local, remote)) is not None:
            found.append(Found(inside, name, difference, local, remote))
    return found
