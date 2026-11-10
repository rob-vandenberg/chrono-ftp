"""What the transfer options do (FB-01 to FB-08): which of them a connection can do, when a part file is continued, and how an existing
file is handled: the overwrite question and its answers (FB-03)."""

from dataclasses import dataclass, replace
from enum import Enum

from chrono_ftp.connections.base import Capabilities
from chrono_ftp.core.connection import FTP
from chrono_ftp.core.transferoptions import RESUME_OFF, RESUME_ON, RESUME_SMART, SMART_RESUME_BYTES, TransferOptions

# --- Version ------------------------------------------------------------
__version__ = "options.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: options greyed out by what the connection can do; resume on, smart and off; the answers to the overwrite question;
#          whether Append can be offered, and why not; whether each size is known; the connection a question is about

NO_CUT: str = "no cut"  # the server can't cut a file back
SIZE_UNKNOWN: str = "size unknown"  # the size of the file there is unknown
KIND_UNKNOWN: str = "kind unknown"  # whether it is a file or a folder is unknown
RESUME: str = "resume"
KEEP_TIMESTAMPS: str = "keep_timestamps"
SET_RIGHTS: str = "set_rights"
NO_REST_TEXT: str = "This server can't continue an unfinished transfer: it does not offer REST."
NO_MFMT_TEXT: str = "This server can't set the date of a file: it does not offer MFMT."
NO_CHMOD_TEXT: str = "This server can't set permissions: it does not offer SITE CHMOD."
ONLY_ON_UPLOAD_TEXT: str = "Rights are set only on files that go to the server."


class Choice(Enum):
    """The answers of the overwrite question."""

    YES = "yes"
    NO = "no"
    YES_TO_ALL = "yes to all"
    NO_TO_ALL = "no to all"
    ONLY_IF_NEWER = "only if newer"
    RENAME = "rename"
    APPEND = "append"
    RESUME = "resume"
    STOP = "stop"


class Action(Enum):
    """What happens to one file whose target is there already."""

    REPLACE = "replace"
    SKIP = "skip"
    RENAME = "rename"
    APPEND = "append"
    RESUME = "resume"
    STOP = "stop"


BATCH_CHOICES: frozenset[Choice] = frozenset({Choice.YES_TO_ALL, Choice.NO_TO_ALL, Choice.ONLY_IF_NEWER})


@dataclass(frozen=True)
class Answer:
    choice: Choice
    new_name: str = ""  # for Rename: the name the file gets in the target folder


@dataclass
class Answers:
    """The answers that hold for more than one file. One action of the person (an upload of three items) shares one, so Yes to all also
    holds for the next item. Only the queue thread uses it."""

    batch: Choice | None = None


@dataclass(frozen=True)
class Conflict:
    """A file that is there already where a transfer would put it: what the question shows."""

    name: str
    folder: str  # the folder it goes into, as the person knows it
    upload: bool
    source_size: int
    source_modified: float | None
    target_size: int
    target_modified: float | None
    part_size: int | None  # an unfinished copy that can be continued; None when there is none
    no_append: str = ""  # why Append is not offered: NO_CUT, SIZE_UNKNOWN or KIND_UNKNOWN; empty where it is
    source_size_known: bool = True  # False where the server gave no size: the size is then 0 and means nothing
    target_size_known: bool = True
    connection: str = ""  # the connection (its tab) the question is about

    @property
    def append_possible(self) -> bool:
        return not self.no_append


def usable(options: TransferOptions, capabilities: Capabilities, protocol: str, upload: bool) -> tuple[TransferOptions, dict[str, str]]:
    """The options as this connection can do them, and why each one it can't do is greyed out (OR-7). A greyed option is off, so the
    screen never shows what will not happen. SFTP can do all of them; an FTP server says in FEAT and SITE HELP what it can."""
    reasons: dict[str, str] = {}
    if protocol == FTP:
        if not capabilities.resume:
            reasons[RESUME] = NO_REST_TEXT
        if upload and not capabilities.timestamps:
            reasons[KEEP_TIMESTAMPS] = NO_MFMT_TEXT  # a download sets the date on this computer, which always can
        if upload and not capabilities.rights:
            reasons[SET_RIGHTS] = NO_CHMOD_TEXT
    if not upload:
        reasons[SET_RIGHTS] = ONLY_ON_UPLOAD_TEXT
    result = options
    if RESUME in reasons:
        result = replace(result, resume=RESUME_OFF)
    if KEEP_TIMESTAMPS in reasons:
        result = replace(result, keep_timestamps=False)
    if SET_RIGHTS in reasons:
        result = replace(result, set_rights=False)
    return result, reasons


def resumable(options: TransferOptions, size: int) -> bool:
    """Whether a file of this size keeps its part file on a stop, and continues from it the next time."""
    return options.resume == RESUME_ON or (options.resume == RESUME_SMART and size >= SMART_RESUME_BYTES)


def is_newer(source_modified: float | None, target_modified: float | None) -> bool:
    """Whole seconds: FTP and many file systems keep no fractions. Without both dates nothing is known to be newer, so nothing is replaced."""
    if source_modified is None or target_modified is None:
        return False
    return int(source_modified) > int(target_modified)


def decided(options: TransferOptions, answers: Answers, conflict: Conflict) -> Action | None:
    """What happens without a question; None when the person is asked."""
    newer = is_newer(conflict.source_modified, conflict.target_modified)
    if options.only_newer and not newer:
        return Action.SKIP
    if answers.batch == Choice.YES_TO_ALL:
        return Action.REPLACE
    if answers.batch == Choice.NO_TO_ALL:
        return Action.SKIP
    if answers.batch == Choice.ONLY_IF_NEWER:
        return Action.REPLACE if newer else Action.SKIP
    if not options.ask_before_replacing:
        return Action.REPLACE
    return None


def action_of(answer: Answer, answers: Answers, conflict: Conflict) -> Action:
    """The person's answer; an answer for all is kept for the files that follow."""
    if answer.choice in BATCH_CHOICES:
        answers.batch = answer.choice
    match answer.choice:
        case Choice.YES | Choice.YES_TO_ALL:
            return Action.REPLACE
        case Choice.NO | Choice.NO_TO_ALL:
            return Action.SKIP
        case Choice.ONLY_IF_NEWER:
            return Action.REPLACE if is_newer(conflict.source_modified, conflict.target_modified) else Action.SKIP
        case Choice.RENAME:
            return Action.RENAME
        case Choice.APPEND:
            return Action.APPEND
        case Choice.RESUME:
            return Action.RESUME if conflict.part_size is not None else Action.REPLACE
        case Choice.STOP:
            return Action.STOP
