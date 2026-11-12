import json
from dataclasses import asdict, dataclass, field
from typing import cast

from chrono_ftp.config.connections import config_folder, write_whole
from chrono_ftp.core.transferoptions import RESUME_MODES, TransferOptions, rights_valid

# --- Version ------------------------------------------------------------
__version__ = "settings.py 0.0.34"

# --- Version history ----------------------------------------------------
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


def keep_done_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MOST_KEEP_DONE_SECONDS


def at_once_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and FEWEST_AT_ONCE <= value <= MOST_AT_ONCE


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
