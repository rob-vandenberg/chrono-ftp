import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import cast

from chrono_ftp.config.connections import config_folder, write_whole
from chrono_ftp.core.logrules import KEPT, OFF, SIZE_MEGABYTES, kept_valid, level_valid, size_valid
from chrono_ftp.core.transferoptions import RESUME_MODES, TransferOptions, rights_valid

# --- Version ------------------------------------------------------------
__version__ = "settings.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Preferences (FP-01): the confirmations before closing a tab with running transfers, before deleting and before a drop; what
#          a double-click on a file does; the automatic refresh of the right side; the sort order and columns per tab; the session log's
#          level, size and kept logs (FP-02); each checked on load, and a file without them gives their standard values
# v0.0.34: Written through write_whole: escaped text, and no .tmp file left behind on any failure
# v0.0.31: The connection that was last live, so the connections window starts on it (D-053)
# v0.0.13: The queue: whether items start by themselves, how long a finished item stays in the list, and whether an error pauses the
#          queue (FC-02, FC-03); each value is checked on load; how many transfers run at once (FC-01); splitting large files (FC-05)
# v0.0.12: The default transfer options and whether the transfer window shows (FB-01, FB-09); each value is checked on load
# v0.0.6: Hidden files; the window's sizes are remembered
# v0.0.5: Initial: the settings of the program; a bad value gives the standard one (OR-15)

FILE_NAME: str = "settings.json"
WINDOW_FILE_NAME: str = "window.json"
TREE_LEFT: str = "left"
TREE_ABOVE: str = "above"
TREE_HIDDEN: str = "hidden"
TREE_PLACES: tuple[str, ...] = (TREE_LEFT, TREE_ABOVE, TREE_HIDDEN)
KEEP_DONE_SECONDS: int = 15  # a finished item stays in the queue this long (FC-03)
MOST_KEEP_DONE_SECONDS: int = 3600  # a number from a file is input (OR-15)
FEWEST_AT_ONCE: int = 1
MOST_AT_ONCE: int = 9
AT_ONCE: int = 2  # transfers at once, until the person chooses otherwise (FC-01)
OPEN: str = "open"  # a double-click on a file opens it: in its program here, through Edit on the server
TRANSFER: str = "transfer"  # a double-click on a file sends it to the other side
DOUBLE_CLICKS: tuple[str, ...] = (OPEN, TRANSFER)
FEWEST_REFRESH_SECONDS: int = 5  # the automatic refresh of the right side asks the server no more often than this
MOST_REFRESH_SECONDS: int = 3600


@dataclass(frozen=True)
class Settings:
    tree_place: str = TREE_LEFT
    show_hidden: bool = True
    transfer_options: TransferOptions = field(default_factory=TransferOptions)
    show_transfer_window: bool = True
    start_by_themselves: bool = True  # off: an item added to the queue waits, paused, until the person starts it
    keep_done_seconds: int = KEEP_DONE_SECONDS
    pause_on_error: bool = False  # on: a failed item pauses the items still waiting; off: the queue goes on with the next
    transfers_at_once: int = AT_ONCE
    last_connection: str = ""  # the name of the connection that was last live; the connections window starts on it (D-053)
    confirm_close_running: bool = True  # ask before a tab with running transfers closes (FP-01)
    confirm_delete: bool = True  # ask before deleting, on both sides
    confirm_drop: bool = True  # ask before a drop transfers: the transfer window, or a question where that window is switched off
    double_click: str = OPEN
    refresh_seconds: int = 0  # the right side reads its folder again this often; 0: never by itself
    columns_per_tab: bool = False  # each tab keeps its own sort order and column widths; the folders are per tab always (OR-4)
    log_level: int = OFF  # the session log (FP-02); off until the person switches it on
    log_size_megabytes: int = SIZE_MEGABYTES
    logs_kept: int = KEPT


def keep_done_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MOST_KEEP_DONE_SECONDS


def at_once_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and FEWEST_AT_ONCE <= value <= MOST_AT_ONCE


def refresh_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and (value == 0 or FEWEST_REFRESH_SECONDS <= value <= MOST_REFRESH_SECONDS)


def _transfer_options(data: object) -> TransferOptions:
    """Each value on its own: a bad one gives its standard value, the others stay (OR-15). A move is never a default."""
    standard = TransferOptions()
    found = cast(dict[str, object], data) if isinstance(data, dict) else {}

    def flag(name: str, default: bool) -> bool:
        value = found.get(name)
        return value if isinstance(value, bool) else default

    resume = found.get("resume")
    rights = found.get("rights")
    return TransferOptions(
        resume=resume if isinstance(resume, str) and resume in RESUME_MODES else standard.resume,
        ask_before_replacing=flag("ask_before_replacing", standard.ask_before_replacing),
        only_newer=flag("only_newer", standard.only_newer),
        keep_timestamps=flag("keep_timestamps", standard.keep_timestamps),
        set_rights=flag("set_rights", standard.set_rights),
        rights=rights if isinstance(rights, str) and rights_valid(rights) else standard.rights,
        add_x_to_folders=flag("add_x_to_folders", standard.add_x_to_folders),
        ignore_rights_errors=flag("ignore_rights_errors", standard.ignore_rights_errors),
        count_first=flag("count_first", standard.count_first),
        split_large=flag("split_large", standard.split_large),
    )


def _read(name: str) -> dict[str, object]:
    try:
        data: object = json.loads((config_folder() / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return cast(dict[str, object], data) if isinstance(data, dict) else {}


def _write(name: str, data: object) -> None:
    write_whole(config_folder() / name, data, private=False)


def load() -> Settings:
    data = _read(FILE_NAME)
    place = data.get("tree_place")
    hidden = data.get("show_hidden")
    window = data.get("show_transfer_window")
    start = data.get("start_by_themselves")
    keep = data.get("keep_done_seconds")
    pause = data.get("pause_on_error")
    at_once = data.get("transfers_at_once")
    last = data.get("last_connection")

    def flag(name: str, default: bool) -> bool:
        value = data.get(name)
        return value if isinstance(value, bool) else default

    def number(name: str, valid: Callable[[object], bool], default: int) -> int:
        value = data.get(name)
        return value if isinstance(value, int) and valid(value) else default  # a bad value gives its standard one (OR-15)

    click = data.get("double_click")
    return Settings(
        place if isinstance(place, str) and place in TREE_PLACES else TREE_LEFT,
        hidden if isinstance(hidden, bool) else True,
        _transfer_options(data.get("transfer_options")),
        window if isinstance(window, bool) else True,
        start if isinstance(start, bool) else True,
        keep if isinstance(keep, int) and keep_done_valid(keep) else KEEP_DONE_SECONDS,
        pause if isinstance(pause, bool) else False,
        at_once if isinstance(at_once, int) and at_once_valid(at_once) else AT_ONCE,
        last if isinstance(last, str) else "",  # a name that is no longer there selects nothing (OR-15)
        confirm_close_running=flag("confirm_close_running", True),
        confirm_delete=flag("confirm_delete", True),
        confirm_drop=flag("confirm_drop", True),
        double_click=click if isinstance(click, str) and click in DOUBLE_CLICKS else OPEN,
        refresh_seconds=number("refresh_seconds", refresh_valid, 0),
        columns_per_tab=flag("columns_per_tab", False),
        log_level=number("log_level", level_valid, OFF),
        log_size_megabytes=number("log_size_megabytes", size_valid, SIZE_MEGABYTES),
        logs_kept=number("logs_kept", kept_valid, KEPT),
    )


def save(settings: Settings) -> None:
    data = asdict(settings)
    options = data["transfer_options"]
    if isinstance(options, dict):
        options.pop("delete_source", None)  # a move is chosen for one transfer, never kept
    _write(FILE_NAME, data)


def load_window() -> dict[str, str]:
    """The sizes of the window, its parts and its columns, each as Qt hands them out."""
    return {name: value for name, value in _read(WINDOW_FILE_NAME).items() if isinstance(value, str)}


def save_window(state: dict[str, str]) -> None:
    _write(WINDOW_FILE_NAME, state)
