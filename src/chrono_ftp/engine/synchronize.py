"""Synchronize (block FF): the plan that would make two folders match, worked out without a screen (D-022).

One side is "here" (always this computer, OR-1) and the other is "there" (the server, or a second folder on this computer, FF-14).
The plan is a list of rows: every row says what would happen, to which side, and whether it starts ticked. Nothing happens until the
person applies it (D-038). The scan itself reads both sides through a reader, so a fake server, a real one and a local folder are all
the same to it."""

import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from chrono_ftp.connections.base import OLD_SUFFIX, KeepGoing, RemoteConnection, RemoteEntry, joined
from chrono_ftp.core.errors import ConnectionLost, LocalActionFailed, RemoteActionFailed
from chrono_ftp.core.masks import EVERYTHING, Mask, parse, fits
from chrono_ftp.core.synchronizing import Criterion, Direction, Mode, Options
from chrono_ftp.engine import checksums, localfiles
from chrono_ftp.engine.sizes import Count

# --- Version ------------------------------------------------------------
__version__ = "synchronize.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: Initial: the directions and modes, the criteria, the timestamp precision the server reports, the checklist rows with their
#          counts, a row reversed, moved and renamed files, the deletes and the scope, and the masks (FF-01 to FF-09, D-038 changed,
#          D-091 to D-094)

HERE: str = "this computer"
THERE_SERVER: str = "the server"
THERE_FOLDER: str = "the other folder"
OUR_SUFFIXES: tuple[str, ...] = (OLD_SUFFIX, ".chrono-ftp-part")  # our own temporary files are no content of a folder
PRECISIONS: tuple[int, ...] = (60, 2, 1)  # the steps a listing may report dates in: minutes, two seconds, seconds
UNREADABLE_HERE_TEXT: str = "This folder on this computer can't be read."
UNREADABLE_THERE_TEXT: str = "This folder can't be read."
MOST_MOVE_CANDIDATES: int = 20_000  # beyond this many one-sided files, moves are not looked for: the pairing would take too long


class Action(Enum):
    """What a row would do. The text says which side it acts on; {here} and {there} are filled in by text_of."""

    COPY_THERE = "Copy to {there}"
    COPY_HERE = "Copy to {here}"
    DELETE_THERE = "Delete on {there}"
    DELETE_HERE = "Delete on {here}"
    TOUCH_THERE = "Set the date on {there}"
    TOUCH_HERE = "Set the date on {here}"
    MOVE_THERE = "Move on {there}"
    MOVE_HERE = "Move on {here}"
    NOTHING = "Leave as it is"


COPIES: frozenset[Action] = frozenset({Action.COPY_THERE, Action.COPY_HERE})
DELETES: frozenset[Action] = frozenset({Action.DELETE_THERE, Action.DELETE_HERE})
TOUCHES: frozenset[Action] = frozenset({Action.TOUCH_THERE, Action.TOUCH_HERE})
MOVES: frozenset[Action] = frozenset({Action.MOVE_THERE, Action.MOVE_HERE})
ON_THERE: frozenset[Action] = frozenset({Action.COPY_THERE, Action.DELETE_THERE, Action.TOUCH_THERE, Action.MOVE_THERE})
REVERSED: dict[Action, Action] = {  # Reverse of one row (FF-05): the other side, and a delete becomes the copy that saves the file
    Action.COPY_THERE: Action.COPY_HERE,
    Action.COPY_HERE: Action.COPY_THERE,
    Action.DELETE_THERE: Action.COPY_HERE,  # the file is only on the other side, so the copy that saves it comes here
    Action.DELETE_HERE: Action.COPY_THERE,  # and this one is only here, so it goes there
    Action.TOUCH_THERE: Action.TOUCH_HERE,
    Action.TOUCH_HERE: Action.TOUCH_THERE,
}
NO_REVERSE_WHY: str = "A move happens on one side only, so there is no other way round for it; untick it to leave the file where it is."

ONLY_HERE_TEXT: str = "Only on this computer"
ONLY_THERE_TEXT: str = "Only on {there}"
NEWER_HERE_TEXT: str = "Newer on this computer"
NEWER_THERE_TEXT: str = "Newer on {there}"
OTHER_SIZE_TEXT: str = "Another size"
OTHER_CONTENT_TEXT: str = "Another content"
OTHER_DATE_TEXT: str = "The same size, another date"
KIND_TEXT: str = "A file on one side, a folder on the other"
UNREADABLE_TEXT: str = "This folder can't be read"
MOVED_TEXT: str = "The same file under another name"


def text_of(text: str, on_a_server: bool = True) -> str:
    """A row's words, with "the server" or "the other folder" for the right side (FF-14)."""
    return text.format(here=HERE, there=THERE_SERVER if on_a_server else THERE_FOLDER)


@dataclass(frozen=True)
class Pair:
    """One name as both sides have it. Either side may be None; both are None for a folder that can't be read."""

    folder: str  # relative to the two folders, "" for the top
    name: str
    here: RemoteEntry | None
    there: RemoteEntry | None
    unreadable: bool = False


@dataclass(frozen=True)
class Row:
    """One line of the checklist (FF-04): what would happen, to which side, and whether it starts ticked."""

    folder: str
    name: str
    action: Action
    here: RemoteEntry | None
    there: RemoteEntry | None
    ticked: bool
    reason: str
    from_folder: str = ""  # a move (FF-07): where the file is now on the side that moves
    from_name: str = ""
    size: Count | None = None  # a folder on one side only: what is in it, counted in the background (FF-06)

    @property
    def path(self) -> str:
        return joined(self.folder, self.name) if self.folder else self.name

    @property
    def from_path(self) -> str:
        return joined(self.from_folder, self.from_name) if self.from_folder else self.from_name

    @property
    def is_folder(self) -> bool:
        entry = self.here or self.there
        return entry is not None and entry.is_folder

    @property
    def on_there(self) -> bool:
        return self.action in ON_THERE


Reader = Callable[[str], list[RemoteEntry] | None]  # the entries of one folder; None when it can't be read
Progress = Callable[[int], None]  # how many names were looked at so far (FF-13)


def local_reader(folder: str) -> list[RemoteEntry] | None:
    return localfiles.read_folder(folder)


def remote_reader(connection: RemoteConnection) -> Reader:
    """A reader over a connection: a folder that is refused fails only itself (OR-15); a lost connection stops it all."""

    def read(folder: str) -> list[RemoteEntry] | None:
        try:
            return connection.list_folder(folder)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return None

    return read


def joined_there(folder: str, name: str, on_a_server: bool) -> str:
    return joined(folder, name) if on_a_server else os.path.join(folder, name)


# --- the precision of the dates (FF-03) ------------------------------------------


SAME_FOLDER_WHY: str = "That is the folder on the left itself. Pick another one."
NESTED_WHY: str = "One of these two folders lies inside the other, so a copy would write a folder into itself and a mirror could delete it. Pick two folders that lie apart."


def why_not_these_two(here: str, there: str) -> str:
    """Why these two folders can't be synchronized with each other (FF-14): the same folder, or one inside the other, in either
    direction and after links are resolved. Empty when the pair is sound. The person can then never start such a run at all (G-2)."""
    try:
        first, second = os.path.realpath(here), os.path.realpath(there)
        if first == second:
            return SAME_FOLDER_WHY
        shared = os.path.commonpath([first, second])
    except (OSError, ValueError):
        return ""  # a folder that can't be resolved is left to the scan, which says calmly what it can't read (OR-15)
    return NESTED_WHY if shared in (first, second) else ""


def precision_of(moments: list[float]) -> int:
    """The step the listing reports dates in: 60 where every date is a whole minute, 2 where every date is an even second, else 1.
    Dates are then compared at that step, in place of a fixed margin, so there are fewer false differences. Without any date, 1."""
    whole = [round(moment) for moment in moments if moment is not None]
    if not whole:
        return 1
    return next(step for step in PRECISIONS if all(moment % step == 0 for moment in whole) or step == 1)


def same_moment(here: float | None, there: float | None, precision: int) -> bool:
    """The same date as far as both sides report it; an unknown date on either side is never a difference."""
    if here is None or there is None:
        return True
    return round(here) // precision == round(there) // precision


# --- the scan ---------------------------------------------------------------------


def _key(name: str, case_matters: bool) -> str:
    return name if case_matters else name.casefold()


def _skipped(name: str) -> bool:
    return name.endswith(OUR_SUFFIXES)


def _passes(mask: Mask, folder: str, entry: RemoteEntry) -> bool:
    """A mask is read relative to the two folders, so "docs/" and "*.log" mean the same on both sides (FF-09)."""
    if mask.empty:
        return True
    return fits(mask, entry.name, entry.is_folder, folder or ".", entry.size if entry.size_known else None, entry.modified)


def scan(here_folder: str, there_folder: str, here_read: Reader, there_read: Reader, options: Options, keep_going: KeepGoing = lambda: True, selected: tuple[str, ...] = (), on_a_server: bool = True, progress: Progress = lambda _count: None) -> list[Pair] | None:
    """Both folders walked side by side, into every folder they share. A name that is a link on either side is left out: a copy would
    write through it, into another tree. A folder that can't be read is told, never taken for empty. None when keep_going said stop."""
    mask = parse(options.mask) if options.mask.strip() else EVERYTHING
    chosen = {_key(name, options.case_matters) for name in selected} if options.selected_only else None
    pairs: list[Pair] = []
    waiting: list[tuple[str, str, str, Pair | None]] = [("", here_folder, there_folder, None)]
    seen = 0
    while waiting:
        if not keep_going():
            return None
        inside, here_path, there_path, came_from = waiting.pop()
        here_entries = here_read(here_path)
        there_entries = there_read(there_path)
        if here_entries is None or there_entries is None:
            if came_from is not None:
                pairs.append(replace(came_from, unreadable=True))  # told, never taken for empty, or all that is in it looks one-sided
                continue
            raise LocalActionFailed(UNREADABLE_HERE_TEXT if here_entries is None else UNREADABLE_THERE_TEXT)
        here_by = {_key(entry.name, options.case_matters): entry for entry in here_entries if not _skipped(entry.name)}
        there_by = {_key(entry.name, options.case_matters): entry for entry in there_entries if not _skipped(entry.name)}
        for key in sorted(here_by.keys() | there_by.keys()):
            if not keep_going():
                return None
            here_entry, there_entry = here_by.get(key), there_by.get(key)
            entry = here_entry or there_entry
            if entry is None:
                continue
            name = entry.name
            if chosen is not None and not inside and key not in chosen:
                continue
            if (here_entry is not None and here_entry.is_link) or (there_entry is not None and there_entry.is_link):
                continue
            if not _passes(mask, inside, entry):
                continue
            if options.existing_only and (here_entry is None or there_entry is None):
                continue
            seen += 1
            progress(seen)
            both_folders = here_entry is not None and there_entry is not None and here_entry.is_folder and there_entry.is_folder
            if both_folders and not options.no_subfolders:
                deeper = joined(inside, name) if inside else name
                here_name = here_entry.name if here_entry is not None else name
                there_name = there_entry.name if there_entry is not None else name
                waiting.append((deeper, os.path.join(here_path, here_name), joined_there(there_path, there_name, on_a_server), Pair(inside, name, here_entry, there_entry)))
                continue
            if both_folders:
                continue  # no subfolders: two folders of the same name are the same as far as this scan goes
            pairs.append(Pair(inside, name, here_entry, there_entry))
    return pairs


def precision_of_pairs(pairs: list[Pair]) -> int:
    """The step the other side reports its dates in, over everything the scan saw there (FF-03)."""
    return precision_of([pair.there.modified for pair in pairs if pair.there is not None and pair.there.modified is not None])


def read_whole(connection: RemoteConnection, here_folder: str, there_folder: str, options: Options, keep_going: KeepGoing = lambda: True, selected: tuple[str, ...] = (), progress: Progress = lambda _count: None) -> list[Pair] | None:
    """The scan of this computer against a server."""
    return scan(here_folder, there_folder, local_reader, remote_reader(connection), options, keep_going, selected, True, progress)


# --- the plan ---------------------------------------------------------------------


def _differs(here: RemoteEntry, there: RemoteEntry, options: Options, precision: int, checksums_by_path: dict[str, tuple[str, str]], folder: str, name: str) -> str:
    """Why these two are not the same, by the chosen criterion; empty when they count as the same (FF-02)."""
    if here.is_folder != there.is_folder:
        return KIND_TEXT
    if here.is_folder:
        return ""
    if options.criterion == Criterion.NAME:
        return ""
    if options.criterion == Criterion.SIZE:
        return "" if here.size == there.size else OTHER_SIZE_TEXT
    if options.criterion == Criterion.CHECKSUM:
        path = joined(folder, name) if folder else name
        found = checksums_by_path.get(path)
        if found is None:
            return "" if here.size == there.size else OTHER_SIZE_TEXT  # no checksum on one side: the size is all that is known
        return "" if found[0] == found[1] else OTHER_CONTENT_TEXT
    same_date, same_size = same_moment(here.modified, there.modified, precision), here.size == there.size
    if same_date and same_size:
        return ""
    return OTHER_DATE_TEXT if same_size else OTHER_SIZE_TEXT


def _newer_here(here: RemoteEntry, there: RemoteEntry, precision: int) -> bool | None:
    """True when the file on this computer is the newer one, False when the other is, None when neither is older."""
    if here.modified is None or there.modified is None or same_moment(here.modified, there.modified, precision):
        return None
    return here.modified > there.modified


def _one_sided(pair: Pair, options: Options, on_a_server: bool) -> Row | None:
    """A name on one side only: a copy the other way, or a delete where the mode or the direction asks for it."""
    to_there = options.direction in (Direction.TO_THERE, Direction.BOTH)
    to_here = options.direction in (Direction.TO_HERE, Direction.BOTH)
    if options.mode == Mode.TIMESTAMPS:
        return None  # only dates are set; nothing is copied and nothing is deleted
    if pair.there is None and pair.here is not None:
        if to_there:
            return Row(pair.folder, pair.name, Action.COPY_THERE, pair.here, None, True, ONLY_HERE_TEXT)
        if options.mode == Mode.MIRROR and to_here:
            return Row(pair.folder, pair.name, Action.DELETE_HERE, pair.here, None, options.delete_other_side, ONLY_HERE_TEXT)
        return None
    if pair.here is None and pair.there is not None:
        if to_here:
            return Row(pair.folder, pair.name, Action.COPY_HERE, None, pair.there, True, text_of(ONLY_THERE_TEXT, on_a_server))
        if options.mode == Mode.MIRROR and to_there:
            return Row(pair.folder, pair.name, Action.DELETE_THERE, None, pair.there, options.delete_other_side, text_of(ONLY_THERE_TEXT, on_a_server))
        return None
    return None


def _both_sides(pair: Pair, options: Options, precision: int, found: dict[str, tuple[str, str]], on_a_server: bool) -> Row | None:
    here, there = pair.here, pair.there
    if here is None or there is None:
        return None
    reason = _differs(here, there, options, precision, found, pair.folder, pair.name)
    if reason == KIND_TEXT:
        return Row(pair.folder, pair.name, Action.NOTHING, here, there, False, KIND_TEXT)  # shown, never acted on: the person decides
    newer_here = _newer_here(here, there, precision)
    to_there = options.direction in (Direction.TO_THERE, Direction.BOTH)
    to_here = options.direction in (Direction.TO_HERE, Direction.BOTH)
    if options.mode == Mode.TIMESTAMPS:
        if here.is_folder or here.size != there.size or newer_here is None:
            return None  # only files of the same size get the source's date, and only where the dates really differ (FF-01)
        if to_there and (not to_here or newer_here):
            return Row(pair.folder, pair.name, Action.TOUCH_THERE, here, there, True, OTHER_DATE_TEXT)
        if to_here:
            return Row(pair.folder, pair.name, Action.TOUCH_HERE, here, there, True, OTHER_DATE_TEXT)
        return None
    if not reason or here.is_folder:
        return None
    if newer_here is None:
        why = reason
    else:
        why = NEWER_HERE_TEXT if newer_here else text_of(NEWER_THERE_TEXT, on_a_server)
    if options.mode == Mode.MIRROR:
        # The target becomes an exact copy: a newer file on the target is replaced too (FF-01).
        if to_there and not to_here:
            return Row(pair.folder, pair.name, Action.COPY_THERE, here, there, True, why)
        if to_here and not to_there:
            return Row(pair.folder, pair.name, Action.COPY_HERE, here, there, True, why)
    if newer_here is True and to_there:
        return Row(pair.folder, pair.name, Action.COPY_THERE, here, there, True, why)
    if newer_here is False and to_here:
        return Row(pair.folder, pair.name, Action.COPY_HERE, here, there, True, why)
    if newer_here is None:
        # The same date, another size or content: the person decides which way it goes, so it starts unticked (D-038).
        if to_there:
            return Row(pair.folder, pair.name, Action.COPY_THERE, here, there, False, why)
        if to_here:
            return Row(pair.folder, pair.name, Action.COPY_HERE, here, there, False, why)
    return None


def plan(pairs: list[Pair], options: Options, precision: int = 1, found: dict[str, tuple[str, str]] | None = None, on_a_server: bool = True, move_sums: dict[str, str] | None = None) -> list[Row]:
    """The rows of the checklist, in the order the scan found them (FF-04)."""
    checksums_by_path = found or {}
    rows: list[Row] = []
    for pair in pairs:
        if pair.unreadable:
            rows.append(Row(pair.folder, pair.name, Action.NOTHING, pair.here, pair.there, False, UNREADABLE_TEXT))
            continue
        row = _one_sided(pair, options, on_a_server) if pair.here is None or pair.there is None else _both_sides(pair, options, precision, checksums_by_path, on_a_server)
        if row is not None:
            rows.append(row)
    return with_moves(rows, options, precision, move_sums, on_a_server)


# --- moved and renamed files (FF-07) ----------------------------------------------


def _move_size(row: Row) -> int | None:
    entry = row.here or row.there
    if entry is None or entry.is_folder or not entry.size_known:
        return None
    return entry.size


def move_candidates(rows: list[Row]) -> tuple[list[Row], list[Row]]:
    """The one-sided files a move could pair: the copies and the deletes of the same direction, per side (FF-07)."""
    copies = [row for row in rows if row.action in COPIES and _move_size(row) is not None]
    deletes = [row for row in rows if row.action in DELETES and _move_size(row) is not None]
    return copies, deletes


def with_moves(rows: list[Row], options: Options, precision: int, move_sums: dict[str, str] | None = None, on_a_server: bool = True) -> list[Row]:
    """A file only on the target, plus a file of the same size only on the source, becomes one row "Move on the server" in place of a
    copy and a delete (FF-07). It is ticked only when the dates match too, or the checksums where both were computed. Only a size that
    belongs to exactly one file on each side is paired: two files of the same size say nothing about which became which."""
    if options.mode == Mode.TIMESTAMPS:
        return rows
    sums = move_sums or {}
    for copy_action, delete_action, move_action in ((Action.COPY_THERE, Action.DELETE_THERE, Action.MOVE_THERE), (Action.COPY_HERE, Action.DELETE_HERE, Action.MOVE_HERE)):
        copies = [row for row in rows if row.action == copy_action and _move_size(row) is not None and (row.there is None if copy_action == Action.COPY_THERE else row.here is None)]
        deletes = [row for row in rows if row.action == delete_action and _move_size(row) is not None]
        if not copies or not deletes or len(copies) + len(deletes) > MOST_MOVE_CANDIDATES:
            continue
        by_size: dict[int, list[Row]] = {}
        for row in deletes:
            size = _move_size(row)
            if size is not None:
                by_size.setdefault(size, []).append(row)
        changed: dict[int, Row | None] = {}
        for row in copies:
            size = _move_size(row)
            if size is None or len(by_size.get(size, [])) != 1 or len([item for item in copies if _move_size(item) == size]) != 1:
                continue
            old = by_size[size][0]
            changed[id(old)] = Row(row.folder, row.name, move_action, row.here, row.there, _moves_surely(row, old, precision, sums), MOVED_TEXT, old.folder, old.name)
            changed[id(row)] = None  # the copy is replaced by the move row
        if changed:
            rows = [new for row in rows if (new := changed.get(id(row), row)) is not None]
    return rows


def _moves_surely(copy_row: Row, delete_row: Row, precision: int, sums: dict[str, str]) -> bool:
    """A move starts ticked only when the dates match as well, or the checksums where both were computed (FF-07)."""
    new = copy_row.here or copy_row.there
    old = delete_row.here or delete_row.there
    if new is None or old is None:
        return False
    mine, theirs = sums.get(copy_row.path, ""), sums.get(delete_row.path, "")
    if mine and theirs:
        return mine == theirs
    return same_moment(new.modified, old.modified, precision)


# --- what the checklist shows ------------------------------------------------------


@dataclass(frozen=True)
class Total:
    count: int = 0
    size: int = 0


def totals(rows: list[Row], ticked_only: bool = False) -> dict[Action, Total]:
    """The count and total size per action (FF-04). A folder counts the size it was given by the background count (FF-06)."""
    found: dict[Action, Total] = {}
    for row in rows:
        if row.action == Action.NOTHING or (ticked_only and not row.ticked):
            continue
        entry = row.here if row.action in (Action.COPY_THERE, Action.DELETE_HERE, Action.TOUCH_HERE) else row.there
        if row.action in MOVES:
            entry = row.here if row.here is not None else row.there  # a move's file is on one side only: that is the side with its size
        if row.size is not None:
            size = row.size.size
        elif entry is not None and entry.size_known and not entry.is_folder:
            size = entry.size
        else:
            size = 0
        was = found.get(row.action, Total())
        found[row.action] = Total(was.count + 1, was.size + size)
    return found


def can_turn(row: Row) -> bool:
    """Whether Reverse has a meaning for this row. A move happens on one side only: there is no other way round for it, so Reverse is
    greyed with that reason rather than doing nothing (OR-20)."""
    return row.action in REVERSED


def why_no_turn(row: Row) -> str:
    return "" if can_turn(row) else NO_REVERSE_WHY if row.action in MOVES else ""


def turned(row: Row) -> Row:
    """One row reversed (FF-05): a copy goes the other way, and a delete becomes the copy that keeps the file, which always comes from
    the side the file is still on. A row Reverse has no meaning for is left exactly as it is (can_turn)."""
    other = REVERSED.get(row.action)
    if other is None:
        return row
    if row.action in DELETES:  # the copy that would have saved the file, ticked: nothing is lost by pressing Reverse
        return replace(row, action=other, ticked=True)
    return replace(row, action=other)


ONE_FILE_TEXT: str = "1 file"
FILES_TEXT: str = "{count} files"
ONE_FOLDER_TEXT: str = "1 folder"
FOLDERS_TEXT: str = "{count} folders"
DELETE_START_TEXT: str = "{what} will be deleted on {side}."
FOLDER_GOES_WHOLE_TEXT: str = " A folder is deleted with everything in it"
INSIDE_KNOWN_TEXT: str = ", which is {count} more here."
INSIDE_AT_LEAST_TEXT: str = ", which is at least {count} more here."
INSIDE_UNKNOWN_TEXT: str = ", and what is in {which} was not counted, so how much that is isn't known."
ONE_FOLDER_UNKNOWN_TEXT: str = "it"
SOME_FOLDERS_UNKNOWN_TEXT: str = "{count} of them"
MASK_ALSO_TEXT: str = " A folder goes whole, so what the mask left out of this list goes with it."
CANNOT_UNDO_TEXT: str = " This can't be brought back. Go on?"


def delete_warning(rows: list[Row], on_a_server: bool = True, a_mask_is_on: bool = False) -> str:
    """What Apply says before it deletes (FF-08, G-2). It never says less than it does: the files and the folders are named apart, a
    folder is said to go with everything in it, the files inside the folders are named where they were counted (FF-06) and said to be
    unknown where they were not, and a mask in use is named too, since a folder goes whole. Empty when nothing is deleted."""
    deletes = [row for row in rows if row.action in DELETES]
    if not deletes:
        return ""
    files = [row for row in deletes if not row.is_folder]
    folders = [row for row in deletes if row.is_folder]
    counted = [row.size for row in folders if row.size is not None]
    inside = sum(count.files for count in counted)
    unknown = len(folders) - len(counted)
    whole: list[str] = []
    if files:
        whole.append(ONE_FILE_TEXT if len(files) == 1 else FILES_TEXT.format(count=len(files)))
    if folders:
        whole.append(ONE_FOLDER_TEXT if len(folders) == 1 else FOLDERS_TEXT.format(count=len(folders)))
    there_word = THERE_SERVER if on_a_server else THERE_FOLDER
    sides = {there_word if row.action == Action.DELETE_THERE else HERE for row in deletes}
    text = DELETE_START_TEXT.format(what=" and ".join(whole), side=" and ".join(sorted(sides)))
    if folders:
        text += FOLDER_GOES_WHOLE_TEXT
        if counted and not unknown:
            text += (INSIDE_AT_LEAST_TEXT if any(not count.complete for count in counted) else INSIDE_KNOWN_TEXT).format(count=ONE_FILE_TEXT if inside == 1 else FILES_TEXT.format(count=inside))
        elif unknown:
            text += INSIDE_UNKNOWN_TEXT.format(which=ONE_FOLDER_UNKNOWN_TEXT if len(folders) == 1 else SOME_FOLDERS_UNKNOWN_TEXT.format(count=unknown))
        else:
            text += "."
        if a_mask_is_on:
            text += MASK_ALSO_TEXT
    return text + CANNOT_UNDO_TEXT


def empty_folders_left_out(rows: list[Row], options: Options) -> list[Row]:
    """"Skip empty folders" (FF-09): a folder row with nothing in it under the mask is left out."""
    if not options.skip_empty_folders:
        return rows
    return [row for row in rows if not (row.is_folder and row.size is not None and row.size.files == 0)]


# --- the checksums the criterion needs (FF-02) --------------------------------------


def checksum_pairs(pairs: list[Pair], options: Options) -> list[Pair]:
    """The pairs whose checksums are worth asking for: a file on both sides of the same size. Another size is another content already."""
    if options.criterion != Criterion.CHECKSUM:
        return []
    return [pair for pair in pairs if pair.here is not None and pair.there is not None and not pair.here.is_folder and not pair.there.is_folder and pair.here.size == pair.there.size]


def checksums_of(connection: RemoteConnection, here_folder: str, there_folder: str, pairs: list[Pair], algorithm: str, keep_going: KeepGoing) -> dict[str, tuple[str, str]] | None:
    """The checksum of both sides for each of these pairs, by the best way the connection offers (D-088); a file whose checksum can't
    be had on one side is left out, so it is judged by its size alone. None when stopped."""
    wanted = [joined(joined(there_folder, pair.folder) if pair.folder else there_folder, pair.name) for pair in pairs]
    there: dict[str, str] = {}

    def noted(found: checksums.Checksum) -> None:
        if found.value:
            there[found.path] = found.value

    if wanted and not checksums.remote_checksums(connection, wanted, algorithm, keep_going, noted):
        return None
    both: dict[str, tuple[str, str]] = {}
    for pair, path in zip(pairs, wanted, strict=True):
        if not keep_going():
            return None
        local_path = os.path.join(here_folder, pair.folder, pair.name) if pair.folder else os.path.join(here_folder, pair.name)
        try:
            mine = checksums.local_checksum(local_path, algorithm, keep_going)
        except (OSError, ValueError):
            continue
        if mine is None:
            return None
        theirs = there.get(path, "")
        if mine and theirs:
            both[joined(pair.folder, pair.name) if pair.folder else pair.name] = (mine, theirs)
    return both
