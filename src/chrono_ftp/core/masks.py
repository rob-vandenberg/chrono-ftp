"""File masks: the one engine that says whether a file or folder fits a mask (block FD, FD-01). Every place that matches names asks here:
the transfer masks, the text mask, selecting by mask, the filter of a side, the file colours and the thumbnails.

The syntax follows the reference program:
1. Patterns are separated by `;` or `,`. `*` stands for any characters, `?` for one, `[abc]` for one of a set; case does not matter.
2. A `|` divides what is included (before it) from what is excluded (after it): `*.txt | old*` is every .txt file that does not start
   with "old". Without patterns of its kind before the `|`, everything of that kind is included: `| *.bak` is every file but the .bak ones.
3. A pattern that ends in `/` is for folders (`build/`); any other pattern is for files. An included folder includes the folders inside it.
4. A pattern with a `/` in it also matches the path of the folder the file is in (`/var/log/*.log`, `*/cache/*`).
5. Conditions follow a pattern, or stand alone for any name: `>1M` larger than 1 MB, `<=100K`, `>2025-01-01` changed after that day,
   `<2025-01-01 12:00`, `>7D` changed within the last 7 days (S seconds, N minutes, H hours, D days, Y years; `DS` from the start of that
   day), `>=today`, `>=yesterday`. Sizes take K, M, G or T (1024 each). Conditions are for files only.
A mask that can't be read gives a calm message that points at the fault (MaskError), never a crash (OR-15)."""

import calendar
import fnmatch
import re
import time
from dataclasses import dataclass
from functools import lru_cache

# --- Version ------------------------------------------------------------
__version__ = "masks.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: The one mask engine (FD-01): include and exclude divided by |, folder patterns, patterns with a path, size and date conditions
#          with absolute and relative dates; a fault gives a calm message that points at it; matches keeps the meaning it had for the
#          file colours and the thumbnails (D-081); a whole-value rule ending in /* fits that folder itself too (D-084)
# v0.0.39: Initial: simple name patterns, separated by ; or ,; a pattern that ends in / is for folders; case does not matter (D-069)

SEPARATORS: tuple[str, ...] = (";", ",")
EXCLUDE_MARK: str = "|"
FOLDER_MARK: str = "/"
LONGEST_MASK: int = 1000  # a mask from a file is input (OR-15)
SIZE_UNITS: dict[str, int] = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
TIME_UNITS: dict[str, int] = {"S": 1, "N": 60, "H": 3600, "D": 86400}  # and Y, counted in calendar years
YEAR_UNIT: str = "Y"
LARGEST_NUMBER: int = 10**15  # larger sizes and times mean nothing and are refused (OR-15)
MOST_YEARS: int = 1000
DIGITS: re.Pattern[str] = re.compile(r"[0-9]+")
DATE_PATTERN: re.Pattern[str] = re.compile(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})(?:[ T]([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?)?")
RELATIVE_PATTERN: re.Pattern[str] = re.compile(r"([0-9]+)\s*([SNHDYsnhdy])([Ss]?)")
SIZE_PATTERN: re.Pattern[str] = re.compile(r"([0-9]+)\s*([KMGTkmgt]?)")
WORDS: dict[str, str] = {"today": "0DS", "yesterday": "1DS"}
HELP_TEXT: str = ("Patterns such as *.txt, separated by ; — a | divides what is included from what is left out (*.txt | old*) — a pattern "
                  "ending in / is for folders (build/) — one with a / matches the path too (/var/log/*.log) — conditions for files: "
                  ">1M, <=100K, >2025-01-01, >7D (changed within 7 days), >=today. Case does not matter.")
FAULT_TEXT: str = "This mask can't be read at “{part}”: {why}"
TOO_LONG_TEXT: str = f"This mask is longer than {LONGEST_MASK} characters."
SECOND_BAR_WHY: str = "a mask has one | at most, between what is included and what is left out."
SIZE_WHY: str = "a size is a whole number, perhaps followed by K, M, G or T, such as >1M."
DATE_WHY: str = "a date is written as 2025-01-31 or 2025-01-31 14:30, or as a time ago such as 7D (S, N, H, D or Y)."
NOTHING_WHY: str = "a condition needs a size or a date after it, such as >1M or >2025-01-31."
TWICE_WHY: str = "this condition is given twice for the same pattern."
FOLDER_CONDITION_WHY: str = "sizes and dates are conditions of files, not of a folder (a pattern ending in /)."


class MaskError(ValueError):
    """A mask that can't be read: the calm text, and where the fault is in the mask (start and end, for the field to select it)."""

    def __init__(self, text: str, start: int = 0, end: int = 0) -> None:
        super().__init__(text)
        self.start: int = start
        self.end: int = end


@dataclass(frozen=True)
class Bound:
    """One condition: a size, a moment, or a time ago (a number, its unit, and whether it counts from the start of that unit)."""

    inclusive: bool
    size: int | None = None
    moment: float | None = None
    ago: tuple[int, str, bool] | None = None


@dataclass(frozen=True)
class Pattern:
    name: str  # an fnmatch pattern, folded for case; "" fits any name
    folder: str  # the pattern of the path of the folder an item is in, folded; "" fits any folder
    for_folders: bool
    larger: Bound | None = None
    smaller: Bound | None = None
    newer: Bound | None = None
    older: Bound | None = None

    @property
    def has_conditions(self) -> bool:
        return any(bound is not None for bound in (self.larger, self.smaller, self.newer, self.older))


@dataclass(frozen=True)
class Mask:
    include_files: tuple[Pattern, ...] = ()
    include_folders: tuple[Pattern, ...] = ()
    exclude_files: tuple[Pattern, ...] = ()
    exclude_folders: tuple[Pattern, ...] = ()

    @property
    def empty(self) -> bool:
        """A mask without any pattern: everything fits it."""
        return not (self.include_files or self.include_folders or self.exclude_files or self.exclude_folders)


EVERYTHING: Mask = Mask()


class _Why(Exception):
    """Why one condition can't be read: the second half of the calm text."""


def _number(text: str) -> int | None:
    """A whole number of a size the program can hold, or None."""
    if DIGITS.fullmatch(text) is None or len(text) > 16:
        return None
    value = int(text)
    return value if value <= LARGEST_NUMBER else None


def _moment(text: str) -> float | None:
    """An absolute date in this computer's time zone, or None."""
    found = DATE_PATTERN.fullmatch(text)
    if found is None:
        return None
    year, month, day, hour, minute, second = (int(value) if value else 0 for value in found.groups())
    if not (1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1] and hour < 24 and minute < 60 and second < 60) or year < 1:
        return None
    try:
        return time.mktime((year, month, day, hour, minute, second, 0, 0, -1))
    except (OverflowError, ValueError):
        return None


def _condition(text: str) -> tuple[str, Bound]:
    """One value after > or <: ("size", bound) or ("date", bound). The reference program's order: a plain number is a size; else a date,
    a time ago, or a size with its unit. Raises _Why."""
    stripped = text.strip()
    inclusive = stripped.startswith("=")
    value = stripped[1:].strip() if inclusive else stripped
    if not value:
        raise _Why(NOTHING_WHY)
    plain = _number(value)
    if plain is not None:
        return "size", Bound(inclusive, size=plain)
    moment = _moment(value)
    if moment is not None:
        return "date", Bound(inclusive, moment=moment)
    relative = RELATIVE_PATTERN.fullmatch(WORDS.get(value.casefold(), value))
    if relative is not None:
        number = _number(relative.group(1))
        unit = relative.group(2).upper()
        if number is None or (unit == YEAR_UNIT and number > MOST_YEARS):
            raise _Why(DATE_WHY)
        return "date", Bound(inclusive, ago=(number, unit, bool(relative.group(3))))
    sized = SIZE_PATTERN.fullmatch(value)
    if sized is not None:
        number = _number(sized.group(1))
        if number is not None and number * SIZE_UNITS.get(sized.group(2).upper(), 1) <= LARGEST_NUMBER:
            return "size", Bound(inclusive, size=number * SIZE_UNITS.get(sized.group(2).upper(), 1))
    why = DATE_WHY if any(character in value for character in "-:") or value.casefold() in WORDS else SIZE_WHY
    raise _Why(why)


def _pattern(text: str, start: int, end: int) -> Pattern | None:
    """One pattern with its conditions; None for an empty one (two separators in a row)."""
    pieces = re.split(r"([<>])", text[start:end])
    name = pieces[0].strip()
    for_folders = name.endswith(FOLDER_MARK)
    name = name.rstrip(FOLDER_MARK) if for_folders else name
    folder = ""
    if FOLDER_MARK in name:
        folder, name = name.rsplit(FOLDER_MARK, 1)
        folder = folder.rstrip(FOLDER_MARK) or FOLDER_MARK
    bounds: dict[str, Bound] = {}
    place = start + len(pieces[0])
    for index in range(1, len(pieces) - 1, 2):
        mark, value = pieces[index], pieces[index + 1]
        value_end = place + 1 + len(value)
        try:
            kind, bound = _condition(value)
        except _Why as why:
            raise MaskError(FAULT_TEXT.format(part=(mark + value).strip(), why=str(why)), place, value_end) from None
        if for_folders:
            raise MaskError(FAULT_TEXT.format(part=(mark + value).strip(), why=FOLDER_CONDITION_WHY), place, value_end)
        slot = ("larger" if mark == ">" else "smaller") if kind == "size" else ("newer" if mark == ">" else "older")
        if slot in bounds:
            raise MaskError(FAULT_TEXT.format(part=(mark + value).strip(), why=TWICE_WHY), place, value_end)
        bounds[slot] = bound
        place = value_end
    if not name and not folder and not bounds and not for_folders:
        return None
    return Pattern(name.casefold(), folder.casefold() if folder else "", for_folders, bounds.get("larger"), bounds.get("smaller"), bounds.get("newer"), bounds.get("older"))


@lru_cache(maxsize=256)
def parse(text: str) -> Mask:
    """The mask a text stands for; raises MaskError, with a calm text and the place of the fault, where it can't be read."""
    if len(text) > LONGEST_MASK:
        raise MaskError(TOO_LONG_TEXT, LONGEST_MASK, len(text))
    include_files: list[Pattern] = []
    include_folders: list[Pattern] = []
    exclude_files: list[Pattern] = []
    exclude_folders: list[Pattern] = []
    including = True
    start = 0
    for place in range(len(text) + 1):
        character = text[place] if place < len(text) else ";"
        if character not in (*SEPARATORS, EXCLUDE_MARK):
            continue
        pattern = _pattern(text, start, place)
        if pattern is not None:
            if including:
                (include_folders if pattern.for_folders else include_files).append(pattern)
            else:
                (exclude_folders if pattern.for_folders else exclude_files).append(pattern)
        if character == EXCLUDE_MARK:
            if not including:
                raise MaskError(FAULT_TEXT.format(part=EXCLUDE_MARK, why=SECOND_BAR_WHY), place, place + 1)
            including = False
        start = place + 1
    return Mask(tuple(include_files), tuple(include_folders), tuple(exclude_files), tuple(exclude_folders))


def problem(text: str) -> str:
    """Empty when the mask can be read; else the calm text of its fault."""
    try:
        parse(text)
    except MaskError as error:
        return str(error)
    return ""


def _local_moment(year: int, month: int, day: int, hour: int, minute: int, second: int) -> float:
    """A moment in this computer's time zone; one the system can't hold is long ago."""
    try:
        return time.mktime((year, month, day, hour, minute, second, 0, 0, -1))
    except (OverflowError, ValueError):
        return float("-inf")


def _limit(bound: Bound, now: float) -> float:
    """The moment a date condition stands for, now."""
    if bound.moment is not None:
        return bound.moment
    assert bound.ago is not None
    number, unit, from_start = bound.ago
    if unit == YEAR_UNIT:
        moment = time.localtime(now)
        year = max(1, moment.tm_year - number)
        if from_start:
            return _local_moment(year, 1, 1, 0, 0, 0)
        day = min(moment.tm_mday, calendar.monthrange(year, moment.tm_mon)[1])
        return _local_moment(year, moment.tm_mon, day, moment.tm_hour, moment.tm_min, moment.tm_sec)
    moment_then = now - number * TIME_UNITS[unit]
    if not from_start:
        return moment_then
    try:
        then = time.localtime(moment_then)
    except (OverflowError, OSError, ValueError):
        return float("-inf")
    if unit == "D":
        return _local_moment(then.tm_year, then.tm_mon, then.tm_mday, 0, 0, 0)  # from the start of that day, hour, minute or second
    if unit == "H":
        return _local_moment(then.tm_year, then.tm_mon, then.tm_mday, then.tm_hour, 0, 0)
    if unit == "N":
        return _local_moment(then.tm_year, then.tm_mon, then.tm_mday, then.tm_hour, then.tm_min, 0)
    return float(int(moment_then))


def _within(value: float | None, low: Bound | None, high: Bound | None, of: str, now: float) -> bool:
    if low is None and high is None:
        return True
    if value is None:
        return False  # a condition on what is not known is not met
    if low is not None:
        limit = float(low.size) if of == "size" and low.size is not None else _limit(low, now)
        if value < limit or (value == limit and not low.inclusive):
            return False
    if high is not None:
        limit = float(high.size) if of == "size" and high.size is not None else _limit(high, now)
        if value > limit or (value == limit and not high.inclusive):
            return False
    return True


def _fits_pattern(pattern: Pattern, name: str, folder: str | None, size: int | None, modified: float | None, now: float) -> bool:
    if pattern.name and not fnmatch.fnmatchcase(name, pattern.name):
        return False
    if pattern.folder:
        if folder is None:
            return False  # where the item is, is not known here: a pattern with a path does not fit
        place = folder.casefold().rstrip(FOLDER_MARK) or FOLDER_MARK
        if not fnmatch.fnmatchcase(place, pattern.folder):
            return False
    if not _within(None if size is None else float(size), pattern.larger, pattern.smaller, "size", now):
        return False
    return _within(modified, pattern.newer, pattern.older, "date", now)


def _any_fits(patterns: tuple[Pattern, ...], name: str, folder: str | None, size: int | None, modified: float | None, now: float, upwards: bool) -> bool:
    """upwards: for an included folder, a folder above it that fits includes it too."""
    if any(_fits_pattern(pattern, name, folder, size, modified, now) for pattern in patterns):
        return True
    while upwards and folder is not None and folder.rstrip(FOLDER_MARK):
        parent, _slash, above = folder.rstrip(FOLDER_MARK).rpartition(FOLDER_MARK)
        folder = parent or (FOLDER_MARK if folder.startswith(FOLDER_MARK) else None)
        if any(_fits_pattern(pattern, above.casefold(), folder, None, None, now) for pattern in patterns):
            return True
    return False


def fits(mask: Mask, name: str, is_folder: bool, folder: str | None = None, size: int | None = None, modified: float | None = None, now: float | None = None) -> bool:
    """Whether a file or folder passes a mask: included (by a pattern of its kind, or because there is none of its kind before the |) and
    not excluded. folder: the path of the folder it is in, for patterns with a path; size and modified for the conditions."""
    moment = time.time() if now is None else now
    folded = name.casefold()
    includes = mask.include_folders if is_folder else mask.include_files
    excludes = mask.exclude_folders if is_folder else mask.exclude_files
    if includes and not _any_fits(includes, folded, folder, size, modified, moment, is_folder):
        return False
    return not _any_fits(excludes, folded, folder, size, modified, moment, False)


def named(mask: Mask, name: str, is_folder: bool, folder: str | None = None, size: int | None = None, modified: float | None = None, now: float | None = None) -> bool:
    """Whether a pattern of the mask names this file or folder itself, and none excludes it: a mask without a pattern of its kind names
    nothing of that kind. What selecting, colours and thumbnails ask: `*.txt` names no folder."""
    moment = time.time() if now is None else now
    folded = name.casefold()
    includes = mask.include_folders if is_folder else mask.include_files
    excludes = mask.exclude_folders if is_folder else mask.exclude_files
    return _any_fits(includes, folded, folder, size, modified, moment, False) and not _any_fits(excludes, folded, folder, size, modified, moment, False)


def matches(mask: str, name: str, is_folder: bool, size: int | None = None, modified: float | None = None) -> bool:
    """Whether a pattern of the mask names this file or folder (named). A pattern ending in / names folders only (`build/`, `.*/`); any
    other pattern files only, so `*` colours files and leaves folders as they are. Upper and lower case are the same. A mask that can't be
    read names nothing (OR-15)."""
    try:
        parsed = parse(mask)
    except MaskError:
        return False
    return named(parsed, name, is_folder, None, size, modified)


def mask_parts(mask: str) -> list[str]:
    """The patterns of a mask separated by ; or ,, each without the spaces around it."""
    return [part.strip() for part in re.split(r"[;,]", mask[:LONGEST_MASK]) if part.strip()]


def _fits_whole(value: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(value, pattern):
        return True
    folder = value.rstrip(FOLDER_MARK) or FOLDER_MARK
    if fnmatch.fnmatchcase(folder, pattern.rstrip(FOLDER_MARK) or FOLDER_MARK):
        return True
    below = FOLDER_MARK + "*"  # a folder rule ending in /* fits that folder itself too
    return pattern.endswith(below) and fnmatch.fnmatchcase(folder, pattern[: -len(below)] or FOLDER_MARK)


def matches_whole(mask: str, value: str) -> bool:
    """Whether a whole value (a host name, a user, a folder path) fits one of the patterns of a mask separated by ; or ,, where * also
    stands for slashes, and a pattern ending in /* fits that folder itself and everything below it; case does not matter. An empty
    mask fits everything."""
    patterns = [part.casefold() for part in mask_parts(mask)]
    folded = value.casefold()
    return not patterns or any(_fits_whole(folded, pattern) for pattern in patterns)
