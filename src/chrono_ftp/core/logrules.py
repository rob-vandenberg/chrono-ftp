"""The rules of the session log (FP-02): its level, how large a log grows and how many older logs are kept. Shared by the settings, which
keep them, and the log, which follows them."""

from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "logrules.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: three levels and off, the size and the number of kept logs, each checked as input (FP-02, D-065)

OFF: int = 0
NORMAL: int = 1  # every request and the server's answer to it, in one line each
DETAILED: int = 2  # also every line of an answer: each entry of a listing, the whole output of a command, every line of a reply
DEBUG: int = 3  # also the connection itself: the encryption agreed, what the server offers, the data connections
LEVELS: tuple[int, ...] = (OFF, NORMAL, DETAILED, DEBUG)
LEAST_SIZE_MEGABYTES: int = 1
MOST_SIZE_MEGABYTES: int = 1000  # a number from a settings file is input (OR-15)
SIZE_MEGABYTES: int = 5  # a log grows to this size before it is rotated
MOST_KEPT: int = 99
KEPT: int = 3  # this many older logs stay of each connection


@dataclass(frozen=True)
class LogRules:
    level: int = OFF  # off until the person switches it on
    size_megabytes: int = SIZE_MEGABYTES
    kept: int = KEPT


def _whole(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def level_valid(value: object) -> bool:
    return _whole(value) and value in LEVELS


def size_valid(value: object) -> bool:
    return _whole(value) and isinstance(value, int) and LEAST_SIZE_MEGABYTES <= value <= MOST_SIZE_MEGABYTES


def kept_valid(value: object) -> bool:
    return _whole(value) and isinstance(value, int) and 0 <= value <= MOST_KEPT
