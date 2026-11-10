"""One transfer in the queue, and what both ways of transferring share: the measure of its progress and its temporary files."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from chrono_ftp.connections.base import RemoteConnection
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers.options import Answer, Answers, Conflict

# --- Version ------------------------------------------------------------
__version__ = "job.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: moved here from queue.py; a job carries its options, shares the answers of its action and knows its total and time
#          left, counted over the time spent transferring only; what was counted of a file that starts again is taken back; a clock of
#          its own; counting starts again when another way does the whole job

PART_SUFFIX: str = ".chrono-ftp-part"  # a transfer lands here first: a stop or failure never touches the file already there
REPORT_SECONDS: float = 0.5  # the time left is shown fresh this often, also when the percentage stays the same
FIRST_ESTIMATE_SECONDS: float = 1.0  # before this the speed is not known well enough to promise a time
SOURCE_GONE_TEXT: str = "This file is no longer on this computer, so it was not sent."
FOLDER_IN_THE_WAY_TEXT: str = "There is a folder with this name on the server, so this file was not sent."


class State(Enum):
    WAITING = "Waiting"
    RUNNING = "Running"
    DONE = "Done"
    FAILED = "Failed"
    STOPPED = "Stopped"


class Stopped(Exception):
    pass


@dataclass
class Job:
    remote: RemoteConnection
    connection: Connection
    local: str  # the local file or folder; for a download the folder it goes into
    remote_path: str  # the remote file or folder; for an upload the folder it goes into
    upload: bool
    name: str
    has_password: bool = False
    state: State = State.WAITING
    percent: int = 0
    message: str = ""
    way: str = ""
    stop_asked: threading.Event = field(default_factory=threading.Event)
    options: TransferOptions = field(default_factory=TransferOptions)
    answers: Answers = field(default_factory=Answers)  # shared by the jobs of one action of the person
    total: int = 0  # bytes of the whole job once counted (FB-07); 0 while unknown
    done: int = 0
    seconds_left: float | None = None
    waiting_for_answer: bool = False


Ask = Callable[[Conflict], Answer]  # blocks until the person answers; raises Stopped when the job is stopped meanwhile


class Meter:
    """The progress of one job: what is done of the total, and the time left. add raises Stopped once the person stopped the job."""

    def __init__(self, job: Job, changed: Callable[[Job], None], clock: Callable[[], float] = time.monotonic) -> None:
        self.job: Job = job
        self.changed: Callable[[Job], None] = changed
        self.clock: Callable[[], float] = clock
        self.started: float = clock()
        self.reported: float = 0.0
        self.waited: float = 0.0  # seconds spent waiting for an answer: no part of the speed

    def set_total(self, total: int) -> None:
        self.job.total = total
        self.started = self.clock()  # the counting itself is no part of the speed
        self.waited = 0.0

    def paused(self, seconds: float) -> None:
        self.waited += max(0.0, seconds)

    def start_again(self) -> None:
        """Another way does the whole job again: nothing of it is counted yet."""
        self.job.done = 0
        self.job.percent = 0
        self.job.seconds_left = None

    def take_back(self, amount: int) -> None:
        """A file that starts again from the beginning: what was counted of it is counted once more as it comes."""
        self.job.done = max(0, self.job.done - amount)

    def add(self, amount: int) -> None:
        job = self.job
        if job.stop_asked.is_set():
            raise Stopped
        job.done += amount
        moment = self.clock()
        percent = min(100, 100 * job.done // job.total) if job.total else job.percent
        elapsed = moment - self.started - self.waited
        if job.total and job.done and elapsed >= FIRST_ESTIMATE_SECONDS:
            job.seconds_left = max(0.0, (job.total - job.done) * elapsed / job.done)
        if percent != job.percent or moment - self.reported >= REPORT_SECONDS:
            job.percent = percent
            self.reported = moment
            self.changed(job)
