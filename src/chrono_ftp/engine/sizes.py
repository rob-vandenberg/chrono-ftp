"""The size of folders, with the number of files and folders in them (FE-02, FE-03): on the server one command for all of them where
its shell has a find that prints sizes, otherwise by walking the listings; on this computer by walking the folders. Never through a
link to a folder; a folder that can't be read in full gives what could be read, marked as "at least"; stopped as soon as it is told to."""

import os
import re
import shlex
import stat
from collections.abc import Callable
from dataclasses import dataclass

from chrono_ftp.connections.base import FIND_PRINTF, Capabilities, KeepGoing, RemoteConnection, RemoteEntry, joined, size_from
from chrono_ftp.core.errors import ConnectionLost, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "sizes.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Initial: the size, files and folders of a selection, on the server with one command where the shell can, else by its
#          listings, and on this computer; what can't be read is marked, and a stop is seen at once (FE-02, FE-03); review round 1:
#          the answer is split on line ends only

BATCH_CHARACTERS: int = 16_000  # one command line holds the folders of a batch, up to about this long
COUNT_LINE: re.Pattern[str] = re.compile(r"([0-9]{1,18}) ([0-9]{1,18}) ([0-9]{1,20}) ([01])")


@dataclass(frozen=True)
class Count:
    """What is in a selection: the bytes of its files (folders themselves count none, as in the lists), the files (a link counts as
    one, never followed) and the folders (a selected folder counts itself). complete is False where something could not be read: the
    numbers are then at least these."""

    size: int = 0
    files: int = 0
    folders: int = 0
    complete: bool = True

    def plus(self, other: "Count") -> "Count":
        return Count(self.size + other.size, self.files + other.files, self.folders + other.folders, self.complete and other.complete)


Each = Callable[[str, Count], None]  # a name of the selection and its count, on the thread that does the work


def of_entry(entry: RemoteEntry) -> Count:
    """A file, link or special file as its listing tells it."""
    return Count(entry.size if entry.size_known else 0, 1, 0, entry.size_known)


def walked(entry: RemoteEntry) -> bool:
    """Only a real folder is walked; a link to one counts as a file, as the tools on the server count it."""
    return entry.is_folder and not entry.is_link


def can_count_on_server(capabilities: Capabilities) -> bool:
    return capabilities.has(FIND_PRINTF) and capabilities.has("awk")


def count_command(paths: list[str]) -> str:
    """One command line for many folders: one walk of find per folder, its sizes summed by awk on the server; one line per folder,
    "files folders bytes 0", the last 1 where find could not read everything. No name ever appears in what comes back. Every path goes
    through shlex.quote (OR-19); one that starts with a hyphen gets ./ before it, as find has no --."""
    lines: list[str] = []
    for path in paths:
        start = shlex.quote(f"./{path}" if path.startswith("-") else path)
        lines.append(
            f"{{ find {start} '(' -type d -printf 'd\\n' ')' -o -printf 'f %s\\n' 2>/dev/null || echo x; }} | "
            "awk '$1==\"d\"{d++} $1==\"f\"{f++; s+=$2} $1==\"x\"{x=1} END{printf \"%d %d %.0f %d\\n\", f, d, s, x}'"
        )
    return "; ".join(lines)


def counts_from_output(output: str, count: int) -> list[Count | None]:
    """The answer of count_command, one line per folder in order; a line that is no count is None, only for its own folder (OR-15)."""
    lines = output.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    found: list[Count | None] = []
    for number in range(count):
        match = COUNT_LINE.fullmatch(lines[number].strip()) if number < len(lines) and len(lines) == count else None
        size = size_from(int(match.group(3))) if match is not None else None
        if match is None or size is None:
            found.append(None)
            continue
        found.append(Count(size, int(match.group(1)), int(match.group(2)), match.group(4) == "0"))
    return found


def batches(paths: list[str]) -> list[list[str]]:
    groups: list[list[str]] = []
    group: list[str] = []
    length = 0
    for path in paths:
        size = len(shlex.quote(path)) + 300  # the command around each path
        if group and length + size > BATCH_CHARACTERS:
            groups.append(group)
            group, length = list[str](), 0
        group.append(path)
        length += size
    if group:
        groups.append(group)
    return groups


def remote_count_by_listing(connection: RemoteConnection, path: str, keep_going: KeepGoing) -> Count | None:
    """A folder walked through its listings, itself counted as a folder; None when stopped. Only a lost connection stops it on its own."""
    total = Count(folders=1)
    waiting = [path]
    while waiting:
        if not keep_going():
            return None
        folder = waiting.pop()
        try:
            entries = connection.list_folder(folder)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            total = total.plus(Count(complete=False))
            continue
        for entry in entries:
            if walked(entry):
                total = total.plus(Count(folders=1))
                waiting.append(joined(folder, entry.name))
            else:
                total = total.plus(of_entry(entry))
    return total


def remote_counts(connection: RemoteConnection, folder: str, entries: list[RemoteEntry], keep_going: KeepGoing, each: Each) -> bool:
    """The count of each entry of the selection, in turn: a file from its listing, a folder by one command for all where the shell can
    (OR-5), else by its listings. False when keep_going said stop before every entry was counted."""
    folders = [entry for entry in entries if walked(entry)]
    for entry in entries:
        if not walked(entry):
            each(entry.name, of_entry(entry))
    if can_count_on_server(connection.capabilities):
        by_path = {joined(folder, entry.name): entry.name for entry in folders}
        for group in batches(list(by_path)):
            if not keep_going():
                return False
            result = connection.run_stoppable(count_command(group), keep_going)
            if result is None:
                return False
            for path, found in zip(group, counts_from_output(result.output, len(group)), strict=True):
                each(by_path[path], found if found is not None else Count(complete=False))
        return True
    for entry in folders:
        found = remote_count_by_listing(connection, joined(folder, entry.name), keep_going)
        if found is None:
            return False
        each(entry.name, found)
    return True


def local_count(path: str, keep_going: KeepGoing) -> Count | None:
    """A file, link or folder on this computer, never through a link; None when stopped."""
    try:
        details = os.lstat(path)
    except OSError:
        return Count(complete=False)
    if not stat.S_ISDIR(details.st_mode):
        return Count(details.st_size, 1, 0)
    total = Count(folders=1)
    waiting = [path]
    while waiting:
        if not keep_going():
            return None
        folder = waiting.pop()
        try:
            with os.scandir(folder) as found:
                inside = list(found)
        except OSError:
            total = total.plus(Count(complete=False))
            continue
        for entry in inside:
            try:
                if entry.is_dir(follow_symlinks=False):
                    total = total.plus(Count(folders=1))
                    waiting.append(entry.path)
                else:
                    total = total.plus(Count(entry.stat(follow_symlinks=False).st_size, 1, 0))
            except OSError:
                total = total.plus(Count(files=1, complete=False))  # gone meanwhile, or its details can't be read
    return total


def local_counts(folder: str, names: list[str], keep_going: KeepGoing, each: Each) -> bool:
    """The count of each name in turn; False when stopped."""
    for name in names:
        found = local_count(os.path.join(folder, name), keep_going)
        if found is None:
            return False
        each(name, found)
    return True
