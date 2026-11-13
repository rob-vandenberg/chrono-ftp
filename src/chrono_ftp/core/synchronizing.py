"""The choices of the Synchronize window as pure data (block FF): the direction, the mode, what two files are compared by, the scope
and the masks. They live here so the settings can keep them and the engine can work with them (OR-17)."""

from dataclasses import dataclass
from enum import Enum

# --- Version ------------------------------------------------------------
__version__ = "synchronizing.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: Initial: the directions, modes and criteria, and every choice of the Synchronize window, kept for next time (FF-01, FF-02,
#          FF-08, FF-09, FF-11, D-091, D-092)

MOST_SCAN_COUNTS: int = 50  # how many folder pairs the file count of their last scan is kept for (FF-13)
LONGEST_PAIR: int = 2000  # a key from a file is input (OR-15)
MOST_FILES_COUNTED: int = 100_000_000


class Direction(Enum):
    TO_THERE = "To the server"
    TO_HERE = "To this computer"
    BOTH = "Both ways"


class Mode(Enum):
    SYNCHRONIZE = "Synchronize"  # what is new or newer is copied
    MIRROR = "Mirror"  # the target becomes an exact copy: a newer file on the target is replaced too
    TIMESTAMPS = "Timestamps only"  # files of the same size get the source's date; nothing is copied


class Criterion(Enum):
    DATE = "By date"
    SIZE = "By size"
    CHECKSUM = "By checksum"
    NAME = "By name only"


@dataclass(frozen=True)
class Options:
    """Every choice of the Synchronize window (FF-01, FF-02, FF-08, FF-09). Each one is kept for next time (FF-11)."""

    direction: Direction = Direction.TO_THERE
    mode: Mode = Mode.SYNCHRONIZE
    criterion: Criterion = Criterion.DATE
    case_matters: bool = True  # off: README here and readme there are the same file (FF-02)
    delete_other_side: bool = False  # off at first: a delete row is shown all the same, and starts unticked (FF-08)
    existing_only: bool = False  # only names that are on both sides
    no_subfolders: bool = False  # the two folders themselves, nothing below them
    selected_only: bool = False  # only the names the person had selected
    mask: str = ""  # include and exclude patterns, relative to the two folders (FF-09)
    skip_empty_folders: bool = True


def scan_key(here: str, there: str) -> str:
    """The name under which the file count of a scan of these two folders is kept (FF-13)."""
    return f"{here}\n{there}"[:LONGEST_PAIR]


def remember_count(counts: tuple[tuple[str, int], ...], key: str, count: int) -> tuple[tuple[str, int], ...]:
    """The counts with this pair first, once, and no longer than it keeps."""
    if not key or count < 0 or count > MOST_FILES_COUNTED:
        return counts
    return ((key, count), *(item for item in counts if item[0] != key))[:MOST_SCAN_COUNTS]


def counted_before(counts: tuple[tuple[str, int], ...], key: str) -> int:
    """How many names the last scan of this folder pair looked at; 0 when it was never scanned."""
    return next((count for name, count in counts if name == key), 0)
