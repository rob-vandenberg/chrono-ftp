"""One large file over several connections at once (FC-05). The file stays one item: its ranges go into the one part file at their own
places, each over a login of its own where the queue has room for one; whatever part ends first takes on what is left."""

import math
import threading
from collections.abc import Callable
from typing import Protocol

from chrono_ftp.connections.base import Limit, Progress, RemoteConnection, limit_now
from chrono_ftp.core.errors import ConnectionLost, NoMoreLogins, RemoteActionFailed, TransferInterrupted
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers.job import Stopped

# --- Version ------------------------------------------------------------
__version__ = "split.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial (FC-05): which file is split, its ranges still to do, the ranges confirmed, and the parts that move them; only drops
#          in a row end a part; a piece given back after the other parts ended is finished by the item's own login

SPLIT_BYTES: int = 64 * 1024 * 1024  # a file this large or larger is split, where the person chose it
MOST_PARTS: int = 4  # connections for one file at most, its own included
PIECES_PER_PART: int = 2  # the work is cut finer than the parts, so a part that ends early takes on what is left
SMALLEST_PIECE: int = 1024 * 1024
TRIES_PER_PART: int = 2  # a part whose line drops this often in a row, without a piece done between, leaves the rest to the others
NOT_FINISHED_TEXT: str = "The transfer did not finish."

Move = Callable[[RemoteConnection, int, int, Progress, Limit], None]  # one range [start, end) over a connection


class Logins(Protocol):
    """The logins the queue lends an item for its further parts: only where "Transfers at once" and the server leave room."""

    def more(self) -> RemoteConnection | None: ...

    def again(self, lost: RemoteConnection) -> RemoteConnection | None:
        """A new login in the place of one whose line dropped; None when there is none (its place is then given back)."""
        ...

    def done(self, connection: RemoteConnection) -> None: ...


def splits(options: TransferOptions, connection: RemoteConnection, logins: Logins | None, upload: bool, size: int) -> bool:
    """Whether this file goes in ranges: the person chose it, it is large enough, and the connection can."""
    return options.split_large and logins is not None and size >= SPLIT_BYTES and connection.can_split(upload)


class Spans:
    """The ranges of the file confirmed written, from any part. Every call may come from any part's thread."""

    def __init__(self, first: int, ranges: tuple[tuple[int, int], ...] = ()) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._spans: list[tuple[int, int]] = ([(0, first)] if first else []) + list(ranges)

    def add(self, place: int, amount: int) -> None:
        if amount <= 0:
            return
        with self._lock:
            spans = sorted([*self._spans, (place, place + amount)])
            merged: list[tuple[int, int]] = []
            for start, end in spans:
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            self._spans = merged

    def first(self) -> int:
        """The bytes confirmed in order from the start of the file."""
        with self._lock:
            return self._spans[0][1] if self._spans and self._spans[0][0] == 0 else 0

    def rest(self) -> tuple[tuple[int, int], ...]:
        """The ranges confirmed after those, for the part's note."""
        with self._lock:
            return tuple(span for span in self._spans if span[0] > 0)

    def gaps(self, start: int, end: int) -> list[tuple[int, int]]:
        """What is still to do between start and end."""
        with self._lock:
            found: list[tuple[int, int]] = []
            place = start
            for span_start, span_end in self._spans:
                if span_end <= place:
                    continue
                if span_start >= end:
                    break
                if span_start > place:
                    found.append((place, span_start))
                place = max(place, span_end)
            if place < end:
                found.append((place, end))
            return found


class _Ended(Exception):
    """Another part failed for good, or the item was stopped: this part ends quietly."""


def pieces_of(gaps: list[tuple[int, int]], parts: int) -> list[tuple[int, int]]:
    """The work cut into pieces, a few per part, in the order of the file."""
    if parts <= 1:
        return list(gaps)
    total = sum(end - start for start, end in gaps)
    size = max(SMALLEST_PIECE, math.ceil(total / (parts * PIECES_PER_PART)))
    pieces: list[tuple[int, int]] = []
    for start, end in gaps:
        while start < end:
            pieces.append((start, min(end, start + size)))
            start += size
    return pieces


def move_in_parts(first: RemoteConnection, logins: Logins, move: Move, spans: Spans, end: int, counted: Callable[[int], None], limit: Limit, stop: threading.Event) -> None:
    """Every range of the file not yet confirmed (spans) moved: over first on this thread, and over each further login the queue lends,
    up to MOST_PARTS in all. counted is told every byte confirmed (it raises Stopped when the item was stopped). The limit is shared by
    the parts. A part whose line drops logs in again, or leaves its range to the others; the item fails only when no part is left to
    finish it, or a part met a failure that would come again (a refusal, a full disk), which ends the others too."""
    extra: list[RemoteConnection] = []
    while len(extra) < MOST_PARTS - 1:
        more = logins.more()
        if more is None:
            break  # no room for more: fewer parts, never a failure
        extra.append(more)
    pieces = pieces_of(spans.gaps(0, end), 1 + len(extra))
    lock = threading.Lock()
    ended = threading.Event()
    failures: list[BaseException] = []
    dropped: list[BaseException] = []
    active = [1 + len(extra)]

    def part_limit() -> int:
        whole = limit_now(limit)
        return max(1, whole // max(1, active[0])) if whole else 0

    def take() -> tuple[int, int] | None:
        with lock:
            return pieces.pop(0) if pieces else None

    def part(connection: RemoteConnection, own: bool) -> None:
        tries = TRIES_PER_PART
        try:
            while not ended.is_set():
                piece = take()
                if piece is None:
                    return
                start, stop_at = piece
                place = start

                def progress(amount: int) -> None:
                    nonlocal place
                    if ended.is_set():
                        raise _Ended
                    with lock:
                        spans.add(place, amount)
                        place += amount
                        counted(amount)

                try:
                    move(connection, start, stop_at, progress, part_limit)
                    tries = TRIES_PER_PART  # only drops one after the other end a part: a long file may meet a few on its way
                except _Ended:
                    return
                except (ConnectionLost, TransferInterrupted, NoMoreLogins) as error:
                    with lock:
                        pieces.insert(0, (place, stop_at))  # what it did not confirm goes to whichever part is free
                        dropped.append(error)
                    tries -= 1
                    if tries < 0 or stop.is_set():
                        return
                    if not own:  # the item's own login logs in again by itself where it can; a further one is replaced
                        renewed = logins.again(connection)
                        with lock:
                            extra.remove(connection)  # it is closed: its place goes to the new login, or back to the queue
                            if renewed is not None:
                                extra.append(renewed)
                        if renewed is None:
                            return
                        connection = renewed
                except BaseException as error:
                    with lock:
                        failures.append(error)
                    ended.set()
                    return
        finally:
            with lock:
                active[0] -= 1

    threads = [threading.Thread(target=part, args=(connection, False), daemon=True) for connection in extra]
    try:
        for thread in threads:
            thread.start()
        part(first, True)
        for thread in threads:
            thread.join()
        while not failures and not stop.is_set():  # a piece a further part gave back after the others had ended: the item's own takes it
            with lock:
                left = sum(stop_at - start for start, stop_at in pieces)
                active[0] += 1
            if not left:
                with lock:
                    active[0] -= 1
                break
            part(first, True)
            with lock:
                if sum(stop_at - start for start, stop_at in pieces) >= left:
                    break  # its own login got nothing further either: the gaps say so below
    finally:
        ended.set()
        for thread in threads:
            thread.join()
        with lock:
            lent = list(extra)
        for connection in lent:
            logins.done(connection)
    if stop.is_set() or any(isinstance(error, Stopped) for error in failures):
        raise Stopped
    if failures:
        raise failures[0]
    if spans.gaps(0, end):
        raise dropped[-1] if dropped else RemoteActionFailed(NOT_FINISHED_TEXT)
