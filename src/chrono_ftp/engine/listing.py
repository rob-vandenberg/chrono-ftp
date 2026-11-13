"""The logic of a file list (block FG), apart from the screen: finding a name as it is typed, the natural order of names, the size
formats, the selection totals, the lines for the clipboard and the folders above a path."""

import posixpath
import re
from pathlib import PurePath

from chrono_ftp.config.settings import BYTES, KILOBYTES, SHORT
from chrono_ftp.connections.base import RemoteEntry, shown_name

# --- Version ------------------------------------------------------------
__version__ = "listing.py 0.0.40"

# --- Version history ----------------------------------------------------
# v0.0.40: Initial: type-ahead matching (FG-01), the extension of a name (FG-02), size formats (FG-06), natural sort (FG-08), the
#          selection totals (FG-13), clipboard lines (FG-11), the parent folders of a path (FG-16), the folders of the history (FG-10);
#          review: a superscript or circled digit after a number no longer stops natural sort; the totals say "at least" where the
#          server gave no size

KILOBYTE: int = 1024
AT_LEAST_TEXT: str = "at least"  # a size the server did not tell is not counted
HISTORY_SHOWN: int = 10  # the folders the drop-down of Back or Forward lists (FG-10)
_DIGITS = re.compile(r"(\d+)")


def size_text(size: int, style: str = SHORT) -> str:
    """A size as the person chose to see it (FG-06); a size from a server is input, so a negative one shows as 0 (OR-15)."""
    amount = max(size, 0)
    if style == BYTES:
        return f"{amount:,}"
    if style == KILOBYTES:
        return f"{-(-amount // KILOBYTE):,} KB"  # rounded up, so a file of a few bytes never shows as 0 KB
    value = float(amount)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < KILOBYTE:
            return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= KILOBYTE
    return f"{value:.1f} TB"


def natural_key(name: str) -> tuple[tuple[int, int, str], ...]:
    """The order a person expects (FG-08): "file2" before "file10"; letters without regard to case, numbers by their value."""
    parts: list[tuple[int, int, str]] = []
    for piece in _DIGITS.split(shown_name(name).casefold()):
        if not piece:
            continue
        if piece.isdecimal():  # what the split took as a number; a superscript or circled digit is a character (OR-15)
            parts.append((0, int(piece), piece))  # a number comes before letters at the same place, as in the reference program
        else:
            parts.append((1, 0, piece))
    return tuple(parts)


def plain_key(name: str) -> tuple[tuple[int, int, str], ...]:
    """The order of the characters only, without regard to case: natural sort switched off."""
    return ((1, 0, shown_name(name).casefold()),)


def extension_of(name: str) -> str:
    """The extension of a file name, with its dot, as the person reads it: ".txt" of "a.b.txt"; "" for "README" and for ".bashrc"."""
    stem = name.lstrip(".")
    return "." + stem.rsplit(".", 1)[1].casefold() if "." in stem else ""


def same_extension(entries: list[RemoteEntry], extension: str) -> list[RemoteEntry]:
    """The files with this extension, a file without one when it is ""; folders never (FG-02, as the reference program's Select Same
    Extension)."""
    return [entry for entry in entries if not entry.is_folder and extension_of(entry.name) == extension]


def matches_typed(name: str, typed: str) -> bool:
    """A name fits what was typed when it starts with it: without regard to case while only small letters are typed, with regard to case
    once a capital is typed."""
    shown = shown_name(name)
    if typed != typed.casefold():
        return shown.startswith(typed)
    return shown.casefold().startswith(typed)


def type_ahead(names: list[str | None], typed: str, start: int, skip_start: bool = False, backwards: bool = False) -> int:
    """The row that fits what was typed (FG-01), from start on and round the end; None stands for a row that is no name (the parent entry).
    skip_start: Tab, the next one after start. -1 when none fits."""
    count = len(names)
    if not typed or count == 0:
        return -1
    step = -1 if backwards else 1
    first = start if 0 <= start < count else 0
    position = (first + step) % count if skip_start else first
    for _ in range(count):
        name = names[position]
        if name is not None and matches_typed(name, typed):
            return position
        position = (position + step) % count
    return -1


def summary_text(chosen: list[RemoteEntry], entries: list[RemoteEntry], style: str = SHORT) -> str:
    """The selection totals of a side (FG-13): the size of what is selected of the size of all files, and how many entries of how many."""
    return f"{_files_size(chosen, style)} of {_files_size(entries, style)}, {len(chosen)} of {len(entries)}"


def _files_size(entries: list[RemoteEntry], style: str) -> str:
    """The size of the files; "at least" where the server gave no size for one of them, so the line never claims what it can't know."""
    files = [entry for entry in entries if not entry.is_folder]
    text = size_text(sum(max(entry.size, 0) for entry in files if entry.size_known), style)
    return text if all(entry.size_known for entry in files) else f"{AT_LEAST_TEXT} {text}"


def quoted(text: str) -> str:
    """A line for the clipboard (FG-11): in double quotes where it holds a space, as the reference program does."""
    return f'"{text}"' if " " in text else text


def clipboard_names(names: list[str], folder: str, full: bool, local: bool) -> str:
    """The selected names, or their full paths, one per line, each quoted where it holds a space."""

    def path_of(name: str) -> str:
        return str(PurePath(folder, name)) if local else posixpath.join(folder, name)

    return "\n".join(quoted(shown_name(path_of(name) if full else name)) for name in names)


def parent_folders(path: str, local: bool) -> list[str]:
    """The folder and every folder above it, nearest first, up to the root (FG-16)."""
    if not path:
        return []
    found = [path]
    walker = path
    while True:
        above = str(PurePath(walker).parent) if local else posixpath.dirname(walker.rstrip("/")) or "/"
        if above == walker:
            return found
        found.append(above)
        walker = above


def history_shown(steps: list[str]) -> list[str]:
    """The folders the drop-down of Back or Forward lists: the nearest first, at most HISTORY_SHOWN (FG-10). steps is the history in its
    own order, the nearest last."""
    return list(reversed(steps))[:HISTORY_SHOWN]
