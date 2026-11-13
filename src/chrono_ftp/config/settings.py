import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import cast

from chrono_ftp.config.connections import config_folder, write_whole
from chrono_ftp.core.filecolours import MOST_RULES, ColourRule, colour_valid
from chrono_ftp.core.logrules import KEPT, OFF, SIZE_MEGABYTES, kept_valid, level_valid, size_valid
from chrono_ftp.core.masks import LONGEST_MASK, problem
from chrono_ftp.core.presets import LONGEST_NAME, MOST_PRESETS, STANDARD_NAME, Preset, PresetRules
from chrono_ftp.core.transferoptions import NAME_CASES, RESUME_MODES, TRANSFER_MODES, TransferOptions, rights_valid

# --- Version ------------------------------------------------------------
__version__ = "settings.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Masks (block FD): the file mask, hidden files, empty folders, the transfer mode with its text mask and BOM, and the names of
#          the transfer options (FD-01 to FD-03); the transfer presets with their rules (FD-04); the history of masks typed (FD-05,
#          FD-06); each checked on load, a bad one gives its standard, and an older file loads as before
# v0.0.40: Selection and keys (block FG): the two-pane selection mode, the key set, the size format, natural sort, the path in the
#          title bar and moving by a right-button drag; each checked on load, a bad one gives its standard
# v0.0.39: Looks (block FQ): the rules of file colours (FQ-01); the largest file and the mask of thumbnails (FQ-02); which toolbars
#          are hidden, their icon size and the buttons with their text under them (FQ-03); each checked on load, a bad one gives its standard
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
THUMBNAIL_MEGABYTES: int = 5  # a larger file gets no thumbnail (FQ-02)
FEWEST_THUMBNAIL_MEGABYTES: int = 1
MOST_THUMBNAIL_MEGABYTES: int = 100
THUMBNAIL_MASK: str = "*.jpg; *.jpeg; *.png; *.gif; *.bmp; *.webp; *.svg; *.ico"  # the pictures Chrono FTP can show
SMALL: str = "small"  # the icon sizes of the toolbars (FQ-03)
MEDIUM: str = "medium"  # the sizes the toolbars had before block FQ
LARGE: str = "large"
ICON_SIZES: tuple[str, ...] = (SMALL, MEDIUM, LARGE)
MOST_TOOLBAR_NAMES: int = 200  # a list from a file is input (OR-15)
LONGEST_TOOLBAR_NAME: int = 100
CLASSIC_KEYS: str = "classic"  # the classic two-pane key set: Alt+F7 finds files, Ctrl+F4 sorts by extension (FG-04)
FILE_MANAGER_KEYS: str = "file manager"  # the key set of a file manager: Ctrl+F finds files, Ctrl+F4 is free
KEY_SETS: tuple[str, ...] = (CLASSIC_KEYS, FILE_MANAGER_KEYS)
TITLE_SHORT: str = "short"  # the last folder of the right side after the name and version in the title bar (FG-12)
TITLE_FULL: str = "full"
TITLE_NONE: str = "none"
TITLE_PATHS: tuple[str, ...] = (TITLE_SHORT, TITLE_FULL, TITLE_NONE)
BYTES: str = "bytes"  # sizes as 1,234,567 (FG-06)
KILOBYTES: str = "kilobytes"  # 1,206 KB
SHORT: str = "short"  # 1.2 MB, as the lists showed sizes before block FG
SIZE_FORMATS: tuple[str, ...] = (BYTES, KILOBYTES, SHORT)
MOST_MASKS_KEPT: int = 20  # the masks typed last, offered again in every mask field (FD-05, FD-06)


@dataclass(frozen=True)
class ToolbarChoices:
    """What the person chose for the toolbars (FQ-03); the standard is every toolbar shown, medium icons, no text under a button that has none."""

    hidden: tuple[str, ...] = ()  # the names of the hidden toolbars
    icon_size: str = MEDIUM
    text_under: tuple[str, ...] = ()  # "toolbar/button": the buttons with their text under the icon


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
    file_colours: tuple[ColourRule, ...] = ()  # the first rule that fits a name colours its row (FQ-01); none at first
    thumbnail_megabytes: int = THUMBNAIL_MEGABYTES
    thumbnail_mask: str = THUMBNAIL_MASK
    toolbars: ToolbarChoices = field(default_factory=ToolbarChoices)
    two_pane_selection: bool = False  # a click or an arrow key moves without selecting; Insert or Space marks (FG-03)
    key_set: str = CLASSIC_KEYS
    size_format: str = SHORT  # how the lists show sizes (FG-06)
    natural_sort: bool = True  # "file2" before "file10" (FG-08)
    title_path: str = TITLE_SHORT  # the folder of the right side in the title bar (FG-12)
    drag_move: bool = True  # a right-button drag offers Move Here (FG-14)
    presets: tuple[Preset, ...] = ()  # named sets of transfer options with their rules (FD-04); the standard set is transfer_options
    mask_history: tuple[str, ...] = ()  # the masks typed last, the newest first


def remember_mask(history: tuple[str, ...], mask: str) -> tuple[str, ...]:
    """The history with this mask first, once, and no longer than it keeps."""
    cleaned = mask.strip()
    if not cleaned or len(cleaned) > LONGEST_MASK:
        return history
    return (cleaned, *(item for item in history if item != cleaned))[:MOST_MASKS_KEPT]


def keep_done_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MOST_KEEP_DONE_SECONDS


def at_once_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and FEWEST_AT_ONCE <= value <= MOST_AT_ONCE


def refresh_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and (value == 0 or FEWEST_REFRESH_SECONDS <= value <= MOST_REFRESH_SECONDS)


def thumbnail_megabytes_valid(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and FEWEST_THUMBNAIL_MEGABYTES <= value <= MOST_THUMBNAIL_MEGABYTES


def _file_colours(data: object) -> tuple[ColourRule, ...]:
    """Each rule on its own: one that is damaged is left out, the others stay in their order (OR-15)."""
    found: list[ColourRule] = []
    for item in cast(list[object], data)[:MOST_RULES] if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        values = cast(dict[str, object], item)
        mask, text, background = values.get("mask"), values.get("text", ""), values.get("background", "")
        if isinstance(mask, str) and mask.strip() and len(mask) <= LONGEST_MASK and isinstance(text, str) and isinstance(background, str) and colour_valid(text) and colour_valid(background):
            found.append(ColourRule(mask, text, background))
    return tuple(found)


def _names(data: object) -> tuple[str, ...]:
    if not isinstance(data, list):
        return ()
    names = {item for item in cast(list[object], data)[:MOST_TOOLBAR_NAMES] if isinstance(item, str) and 0 < len(item) <= LONGEST_TOOLBAR_NAME}
    return tuple(sorted(names))


def _toolbars(data: object) -> ToolbarChoices:
    """Each value on its own: a bad one gives its standard value (OR-15). A name the program does not know is kept and does nothing."""
    found = cast(dict[str, object], data) if isinstance(data, dict) else {}
    size = found.get("icon_size")
    return ToolbarChoices(_names(found.get("hidden")), size if isinstance(size, str) and size in ICON_SIZES else MEDIUM, _names(found.get("text_under")))


def _transfer_options(data: object) -> TransferOptions:
    """Each value on its own: a bad one gives its standard value, the others stay (OR-15). A move is never a default."""
    standard = TransferOptions()
    found = cast(dict[str, object], data) if isinstance(data, dict) else {}

    def flag(name: str, default: bool) -> bool:
        value = found.get(name)
        return value if isinstance(value, bool) else default

    resume = found.get("resume")
    rights = found.get("rights")

    def text(name: str, default: str) -> str:
        value = found.get(name)
        return value if isinstance(value, str) and len(value) <= LONGEST_MASK and not problem(value) else default  # a mask with a fault: its standard

    def choice(name: str, choices: tuple[str, ...], default: str) -> str:
        value = found.get(name)
        return value if isinstance(value, str) and value in choices else default

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
        file_mask=text("file_mask", standard.file_mask),
        skip_hidden=flag("skip_hidden", standard.skip_hidden),
        skip_empty_folders=flag("skip_empty_folders", standard.skip_empty_folders),
        transfer_mode=choice("transfer_mode", TRANSFER_MODES, standard.transfer_mode),
        text_mask=text("text_mask", standard.text_mask),
        remove_bom=flag("remove_bom", standard.remove_bom),
        name_case=choice("name_case", NAME_CASES, standard.name_case),
        replace_characters=flag("replace_characters", standard.replace_characters),
    )


def _presets(data: object) -> tuple[Preset, ...]:
    """Each preset on its own: one that is damaged is left out, as is a second of the same name; the others stay in their order (OR-15)."""
    found: list[Preset] = []
    names: set[str] = {STANDARD_NAME.casefold()}
    for item in cast(list[object], data)[:MOST_PRESETS] if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        values = cast(dict[str, object], item)
        name = values.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > LONGEST_NAME or name.strip().casefold() in names:
            continue
        rules = cast(dict[str, object], values.get("rules")) if isinstance(values.get("rules"), dict) else {}

        def rule(key: str, rules: dict[str, object] = rules) -> str:
            value = rules.get(key)
            return value if isinstance(value, str) and len(value) <= LONGEST_MASK else ""

        names.add(name.strip().casefold())
        found.append(Preset(name.strip(), _transfer_options(values.get("options")), PresetRules(rule("host"), rule("user"), rule("remote_folder"), rule("local_folder"))))
    return tuple(found)


def _mask_history(data: object) -> tuple[str, ...]:
    history: tuple[str, ...] = ()
    for item in reversed(cast(list[object], data)[:MOST_MASKS_KEPT]) if isinstance(data, list) else []:
        if isinstance(item, str):
            history = remember_mask(history, item)
    return history


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

    def choice(name: str, choices: tuple[str, ...], default: str) -> str:
        value = data.get(name)
        return value if isinstance(value, str) and value in choices else default

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
        file_colours=_file_colours(data.get("file_colours")),
        thumbnail_megabytes=number("thumbnail_megabytes", thumbnail_megabytes_valid, THUMBNAIL_MEGABYTES),
        thumbnail_mask=mask if isinstance(mask := data.get("thumbnail_mask"), str) and len(mask) <= LONGEST_MASK else THUMBNAIL_MASK,
        toolbars=_toolbars(data.get("toolbars")),
        two_pane_selection=flag("two_pane_selection", False),
        key_set=choice("key_set", KEY_SETS, CLASSIC_KEYS),
        size_format=choice("size_format", SIZE_FORMATS, SHORT),
        natural_sort=flag("natural_sort", True),
        title_path=choice("title_path", TITLE_PATHS, TITLE_SHORT),
        drag_move=flag("drag_move", True),
        presets=_presets(data.get("presets")),
        mask_history=_mask_history(data.get("mask_history")),
    )


def save(settings: Settings) -> None:
    data = asdict(settings)
    options = data["transfer_options"]
    if isinstance(options, dict):
        options.pop("delete_source", None)  # a move is chosen for one transfer, never kept
    for preset in cast(list[dict[str, object]], data["presets"]):
        kept = preset.get("options")
        if isinstance(kept, dict):
            cast(dict[str, object], kept).pop("delete_source", None)
    _write(FILE_NAME, data)


def load_window() -> dict[str, str]:
    """The sizes of the window, its parts and its columns, each as Qt hands them out."""
    return {name: value for name, value in _read(WINDOW_FILE_NAME).items() if isinstance(value, str)}


def save_window(state: dict[str, str]) -> None:
    _write(WINDOW_FILE_NAME, state)
