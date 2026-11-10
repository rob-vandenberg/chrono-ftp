"""Which waiting jobs may start now, when several transfers run at once (FC-01): never more than the person allows, never two on the same
place, never more logins on a server than it takes; and how the speed limit of the whole queue is shared among the running jobs."""

import os
from dataclasses import dataclass

from chrono_ftp.config.settings import AT_ONCE, FEWEST_AT_ONCE, MOST_AT_ONCE
from chrono_ftp.connections.base import joined, normalized
from chrono_ftp.core.connection import Connection
from chrono_ftp.transfers.job import Job

# --- Version ------------------------------------------------------------
__version__ = "schedule.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial (FC-01): how many transfers run at once; the places a job reads and writes, and whether two jobs meet there; the
#          server a job logs in to; the share of the queue's speed limit for each running job; the job that starts next

__all__ = ["AT_ONCE", "FEWEST_AT_ONCE", "HELD_NEXT", "HELD_PLACE", "HELD_ROOM", "MOST_AT_ONCE", "Place", "Server", "in_the_way", "meet", "places", "server_of", "share"]

HELD_PLACE: str = "place"  # it waits: a running job works in the same place
HELD_ROOM: str = "room"  # it waits: the server takes no more logins now; it tries again when another job of that server ends
HELD_NEXT: str = "next"  # nothing keeps it but the number at once: it is the first to start when a running job ends

Server = tuple[str, str, int, str]  # protocol, host, port, user: one login on one server


def server_of(connection: Connection) -> Server:
    return (connection.protocol, connection.host.lower(), connection.port, connection.user)


@dataclass(frozen=True)
class Place:
    """A file or folder a job works on: on this computer (server None) or on a server, and whether the job writes there (its target,
    and the source of a move, which goes) or only reads."""

    server: Server | None
    path: str
    written: bool


def places(job: Job) -> tuple[Place, Place]:
    """Where the job reads and where it writes."""
    server = server_of(job.connection)
    move = job.options.delete_source
    if job.upload:
        return Place(None, os.path.abspath(job.local), move), Place(server, normalized(joined(job.remote_path, job.name)), True)
    return Place(server, normalized(job.remote_path), move), Place(None, os.path.abspath(os.path.join(job.local, job.name)), True)


def _within(path: str, top: str, separator: str) -> bool:
    """path is top, or lies inside it."""
    return path == top or path.startswith(top if top.endswith(separator) else top + separator)


def meet(first: Place, second: Place) -> bool:
    """The same place, or one inside the other, on the same side, and at least one of the two writes there: two that only read never
    meet."""
    if first.server != second.server or not (first.written or second.written):
        return False
    separator = os.sep if first.server is None else "/"
    return _within(first.path, second.path, separator) or _within(second.path, first.path, separator)


def in_the_way(job: Job, running: list[Job]) -> Job | None:
    """The running job that works where this one would: it waits until that one ended."""
    mine = places(job)
    return next((other for other in running if other is not job and any(meet(one, two) for one in mine for two in places(other))), None)


def share(total: int, following: int) -> int:
    """The part of the queue's speed limit (KB per second, 0 for none) for one of the running jobs that follow it: an equal part each,
    never 0, which would mean no limit."""
    if not total or following <= 1:
        return total
    return max(1, total // following)
