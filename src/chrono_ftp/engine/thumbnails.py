"""Which thumbnails to fetch, and the fetching itself without a screen (FQ-02): only files in view, within the size limit and the image
mask, in the order they are in view; a file is read into memory only, never onto the disk, and the reading gives way the moment the
person wants the connection."""

import os
from collections.abc import Callable, Collection
from dataclasses import dataclass

from chrono_ftp.connections.base import KeepGoing, RemoteConnection, RemoteEntry, joined
from chrono_ftp.core.errors import LocalReadFailed
from chrono_ftp.core.masks import matches

# --- Version ------------------------------------------------------------
__version__ = "thumbnails.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Initial: the files of the view that get a thumbnail, the key a thumbnail is kept under, and a file read into memory on this
#          computer or on a server, stopped when told (FQ-02, D-070)

MEGABYTE: int = 1024 * 1024
BLOCK: int = 64 * 1024
NOT_READ_TEXT: str = "This file could not be read: {reason}"


@dataclass(frozen=True)
class Key:
    """What a thumbnail is kept under: a changed file (size or date) is fetched again."""

    place: str  # "" on this computer; the login on a server
    folder: str
    name: str
    size: int
    modified: float | None


def key_of(place: str, folder: str, entry: RemoteEntry) -> Key:
    return Key(place, folder, entry.name, entry.size, entry.modified)


def fits(entry: RemoteEntry, limit_bytes: int, mask: str) -> bool:
    """A plain file of a known size within the limit, whose name fits the mask. A folder, a pipe or device, a file of unknown size and
    a link that leads to a folder are never fetched."""
    if entry.is_folder or entry.special or not entry.kind_known or not entry.size_known:
        return False
    return 0 < entry.size <= limit_bytes and matches(mask, entry.name, False)


def to_fetch(place: str, folder: str, in_view: list[RemoteEntry], limit_bytes: int, mask: str, known: Collection[Key]) -> list[Key]:
    """The thumbnails still to fetch, in the order of the rows in view: what is not in view is not fetched (FQ-02)."""
    return [key_of(place, folder, entry) for entry in in_view if fits(entry, limit_bytes, mask) and key_of(place, folder, entry) not in known]


def read_local(path: str, most: int, keep_going: KeepGoing) -> bytes | None:
    """A file on this computer read into memory, block by block; None when told to stop. Larger than most bytes, or not readable: refused
    (LocalReadFailed), so it keeps its normal icon."""
    parts: list[bytes] = []
    got = 0
    try:
        with open(path, "rb") as file:
            while keep_going():
                block = file.read(BLOCK)
                if not block:
                    return b"".join(parts)
                got += len(block)
                if got > most:
                    raise LocalReadFailed(NOT_READ_TEXT.format(reason="larger than the limit"))
                parts.append(block)
    except OSError as error:
        raise LocalReadFailed(NOT_READ_TEXT.format(reason=error.strerror or error)) from error
    return None


def reader_for(remote: RemoteConnection | None, folder: str, most: int) -> Callable[[str, KeepGoing], bytes | None]:
    """How one side reads a file of its folder: on this computer without a connection, else through the connection."""
    if remote is None:
        return lambda name, keep_going: read_local(os.path.join(folder, name), most, keep_going)
    server = remote
    return lambda name, keep_going: server.read_file(joined(folder, name), most, keep_going)
