"""One transfer in the queue, and what both ways of transferring share: the measure of its progress and its temporary files."""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from chrono_ftp.connections.base import RemoteConnection
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers.options import Action, Answer, Answers, Conflict

# --- Version ------------------------------------------------------------
__version__ = "job.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: A job can be side work instead of a transfer: a delete, a move, a date set or a copy on this computer (FF-10)
# v0.0.41: The size a converted text has at the target, for a later run of the job (FD-02)
# v0.0.13: Queue control (FC-02, FC-03): a job can be paused and knows when it continues a pause, keeps the answers given about its
#          files for a resume or a retry, has a speed limit of its own, can be deleted while it runs, and knows when it was done; what
#          rsync sent of a move that broke off; the tab it belongs to; each file sent, with its source as it was then; why it still
#          waits while other jobs run (FC-01); its own speed, measured over the last moments of transferring
# v0.0.12: Initial: moved here from queue.py; a job carries its options, shares the answers of its action and knows its total and time
#          left, counted over the time spent transferring only; what was counted of a file that starts again is taken back; a clock of
#          its own; counting starts again when another way does the whole job

PART_SUFFIX: str = ".chrono-ftp-part"  # a transfer lands here first: a stop or failure never touches the file already there
REPORT_SECONDS: float = 0.5  # the time left is shown fresh this often, also when the percentage stays the same
FIRST_ESTIMATE_SECONDS: float = 1.0  # before this the speed is not known well enough to promise a time
SPEED_SECONDS: float = 2.0  # the speed shown is what arrived over about this long
SOURCE_GONE_TEXT: str = "This file is no longer on this computer, so it was not sent."
FOLDER_IN_THE_WAY_TEXT: str = "There is a folder with this name on the server, so this file was not sent."


class State(Enum):
    WAITING = "Waiting"
    RUNNING = "Running"
    PAUSED = "Paused"
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
    tab: object | None = None  # the window's tab it belongs to, whatever connection that tab has now; the queue never looks at it
    limit: int | None = None  # KB per second for this job alone, 0 for none; None: the limit of the whole queue
    after_stop: State = State.STOPPED  # what a stop of the running job ends in: a pause keeps it for later, a resume meanwhile runs it again
    delete_asked: bool = False  # it leaves the queue as soon as it has stopped
    finished_at: float | None = None  # when it was done (monotonic): it leaves the list a while later
    decided: dict[str, Action] = field(default_factory=dict[str, Action])  # the answers about its files, kept for a resume or a retry
    shown: dict[str, tuple[int, bool, float | None]] = field(default_factory=dict[str, tuple[int, bool, float | None]])  # each file as it was then
    resuming: bool = False  # its last run ended in a pause: this run continues what that one kept
    partial_token: str = ""  # with resume off, rsync's folder of unfinished copies has a name of this job's own, kept only across a pause
    rsync_started: set[str] = field(default_factory=set[str])  # the files rsync named in this run: it names a file when it starts it
    sent_as: dict[str, tuple[int, float | None, bool]] = field(default_factory=dict[str, tuple[int, float | None, bool]])  # each file
    # sent in an earlier run: its source's size and date then, and whether that date was set there; sent again when either changed
    arrived_as: dict[str, int] = field(default_factory=dict[str, int])  # of those, a text whose converted copy has another size than
    # its source: the size it has at the target, which a later run compares with what is there (FD-02)
    rsync_sent: set[str] = field(default_factory=set[str])  # of those, the ones known to have arrived whole (rsyncway.arrived)
    paused_parts: dict[str, str] = field(default_factory=dict[str, str])  # the notes of parts a pause kept, with the resume setting they
    # would have had without the pause: a stop or a delete ends the pause, and they go back to it (filebyfile.end_pause_notes)
    moved_pending: dict[str, tuple[int, float | None]] = field(default_factory=dict[str, tuple[int, float | None]])  # a move whose originals
    # rsync sent before a pause, a stop or a failure: their size and date as read; they are removed (each checked again) when it goes on
    held: str = ""  # why it still waits while it could otherwise run (schedule.HELD_PLACE, schedule.HELD_ROOM); empty: only its turn
    speed: float | None = None  # bytes per second of late, while it runs; None while unknown
    side: object | None = None  # a transfers.sidework.SideJob: this item deletes, moves, sets dates or copies on this computer
    # instead of transferring (FF-10). The queue runs it as any other item; nothing else looks at it.

    @property
    def continue_later(self) -> bool:
        """A pause, not a stop: what arrived of the file on its way is kept, so a resume continues from there."""
        return self.after_stop is not State.STOPPED


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
        self.counted: int = 0  # every byte add was given in this run, also of a file that started again: what travelled
        self.sample: tuple[float, int] = (self.started, 0)  # the moment and the bytes counted then, from which the speed is measured
        job.speed = None

    def set_total(self, total: int) -> None:
        self.job.total = total
        self.started = self.clock()  # the counting itself is no part of the speed
        self.waited = 0.0
        self.sample = (self.started, self.counted)

    def paused(self, seconds: float) -> None:
        self.waited += max(0.0, seconds)
        self.sample = (self.clock(), self.counted)  # waiting for an answer is no part of the speed either
        self.job.speed = None

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
        self.counted += amount
        since, counted_then = self.sample
        if moment - since >= SPEED_SECONDS:
            job.speed = (self.counted - counted_then) / (moment - since)
            self.sample = (moment, self.counted)
        percent = min(100, 100 * job.done // job.total) if job.total else job.percent
        elapsed = moment - self.started - self.waited
        if job.total and job.done and elapsed >= FIRST_ESTIMATE_SECONDS:
            job.seconds_left = max(0.0, (job.total - job.done) * elapsed / job.done)
        if percent != job.percent or moment - self.reported >= REPORT_SECONDS:
            job.percent = percent
            self.reported = moment
            self.changed(job)
