import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from chrono_ftp.connections.base import OLD_SUFFIX, KeepGoing, RemoteConnection, RemoteEntry, joined
from chrono_ftp.core.errors import ConnectionLost, LocalActionFailed, RemoteActionFailed
from chrono_ftp.engine import checksums, localfiles
from chrono_ftp.transfers.queue import PART_SUFFIX

# --- Version ------------------------------------------------------------
__version__ = "compare.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: The checksum step: files of the same size with other dates are compared by their checksums on both sides, through the best
#          way the connection offers, so it also works without a shell where the server computes checksums (FE-01, D-027); review
#          round 1: a note on the way reaches the caller
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
    CONTENT_NEWER_LOCAL = "Other content, newer on this computer"  # the checksum step found the same size, other content
    CONTENT_NEWER_REMOTE = "Other content, newer on the server"


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


@dataclass(frozen=True)
class Contents:
    """What the checksum step found: the rows as they are now, and how many had the same content and were left out."""

    found: list[Found]
    same: int
    unknown: int  # rows whose checksum could not be had on one side: they stay as they were


def compare_contents(connection: RemoteConnection, local_folder: str, remote_folder: str, found: list[Found], algorithm: str, keep_going: KeepGoing, note: Callable[[str], None] = lambda _text: None) -> Contents | None:
    """The second step of compare (D-020): every file of the same size with another date gets its checksum on both sides, on the
    server through the best way the connection offers (one command for all where the shell can), here read in pieces. The same
    content leaves the row out; other content makes it newer on the side with the later date. None when keep_going said stop."""
    rows = [row for row in found if row.difference == Difference.DATE]
    remote_paths = [joined(joined(remote_folder, row.folder) if row.folder else remote_folder, row.name) for row in rows]
    there: dict[str, str] = {}

    def noted(checksum: checksums.Checksum) -> None:
        if checksum.value:
            there[checksum.path] = checksum.value

    if rows and not checksums.remote_checksums(connection, remote_paths, algorithm, keep_going, noted, note):
        return None
    changed: dict[int, Found | None] = {}
    same = unknown = 0
    for row, remote_path in zip(rows, remote_paths, strict=True):
        if not keep_going():
            return None
        local_path = os.path.join(local_folder, row.folder, row.name) if row.folder else os.path.join(local_folder, row.name)
        try:
            here = checksums.local_checksum(local_path, algorithm, keep_going)
        except (OSError, ValueError):
            here = ""
        if here is None:
            return None
        remote_value = there.get(remote_path, "")
        if not here or not remote_value:
            unknown += 1
            continue
        if here == remote_value:
            same += 1
            changed[id(row)] = None
            continue
        newer_here = (row.local.modified if row.local is not None else None) or 0.0
        newer_there = (row.remote.modified if row.remote is not None else None) or 0.0
        changed[id(row)] = replace(row, difference=Difference.CONTENT_NEWER_LOCAL if newer_here > newer_there else Difference.CONTENT_NEWER_REMOTE)
    rows_now = [changed.get(id(row), row) for row in found]
    return Contents([row for row in rows_now if row is not None], same, unknown)
