"""Checksums of files on both sides (FE-01): one function picks the best way the connection offers, detected and never assumed
(D-016): the server's *sum tools through its shell, one command for many files; without a shell SFTP's check-file or FTP's HASH and
the older checksum commands, where the server offers them. A file on this computer is read in pieces, never whole into memory."""

import hashlib
import os
import shlex
import stat
from collections.abc import Callable
from dataclasses import dataclass

from chrono_ftp.connections.base import CHECKSUM_ALGORITHMS, Capabilities, KeepGoing, RemoteConnection, RemoteEntry, checksum_from, joined
from chrono_ftp.core.errors import ConnectionFailed, ConnectionLost, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "checksums.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Initial: checksums through the shell, SFTP's check-file or FTP's HASH, whichever the connection offers, and of files on this
#          computer read in pieces; the files inside folders; stopped as soon as it is told to (FE-01, FE-02); review round 1: a
#          file the server may not read says so calmly, never in the shell's words; the answer is split on line ends only; checksums
#          through the protocol run on a login of their own where the connection does one thing at a time, and a stop ends the file
#          that runs

PREFERRED: tuple[str, ...] = ("sha256", "sha1", "md5", "sha512", "sha384", "sha224")  # compare takes the first of these the connection offers
LOCAL_PIECE_BYTES: int = 1024 * 1024  # a file on this computer is read this much at a time
BATCH_CHARACTERS: int = 16_000  # one command line on the server holds the paths of a batch, up to about this long
BATCH_FILES: int = 100  # and at most this many: a stop is seen between batches
SHELL: str = "shell"
PROTOCOL: str = "protocol"
NO_WAY_ON_FTP_TEXT: str = "This FTP server computes no checksums: it names neither HASH nor XSHA256 or the like in FEAT."
NO_WAY_WITHOUT_SHELL_TEXT: str = "This server has no shell for this login and offers no check-file extension, so it computes no checksums."
NO_WAY_TEXT: str = "This server has none of md5sum, sha1sum and the like, and offers no check-file extension, so it computes no checksums."
NOT_THIS_ALGORITHM_TEXT: str = "This server can't compute {algorithm} checksums."
NOT_A_FILE_TEXT: str = "not a regular file, so it has no checksum"
GONE_TEXT: str = "it is no longer there"
NOT_READABLE_TEXT: str = "Chrono FTP may not read this file"
READ_FAILED_TEXT: str = "the server could not read this file"
TAB_WAITS_TEXT: str = "The server takes no further login now, so the checksums go over this tab's connection, and the tab waits for them."
UNREADABLE_TEXT: str = "it could not be read: {reason}"
ODD_ANSWER_TEXT: str = "the server's answer could not be read"
OK_MARK: str = "ok"
NO_MARK: str = "no"


@dataclass(frozen=True)
class Checksum:
    """The checksum of one file, or why there is none."""

    path: str
    value: str = ""  # lower-case hex; empty when it failed
    problem: str = ""


Each = Callable[[Checksum], None]  # called once for every file, in the order asked, on the thread that does the work


def tool_of(algorithm: str) -> str:
    return f"{algorithm}sum"


def algorithms_of(capabilities: Capabilities) -> tuple[str, ...]:
    """What this connection can compute on the server, by its shell or its protocol, in the order of CHECKSUM_ALGORITHMS."""
    return tuple(algorithm for algorithm in CHECKSUM_ALGORITHMS if way_of(capabilities, algorithm))


def way_of(capabilities: Capabilities, algorithm: str) -> str:
    """SHELL where the shell has the tool (one command for many files), else PROTOCOL where the protocol offers it, else ""."""
    if capabilities.has(tool_of(algorithm)):
        return SHELL
    return PROTOCOL if algorithm in capabilities.checksums else ""


def why_no_checksum(capabilities: Capabilities, on_ftp: bool) -> str:
    """Why this connection computes no checksums, for the hover text of what is greyed (OR-20); empty when it computes some."""
    if algorithms_of(capabilities):
        return ""
    if on_ftp:
        return NO_WAY_ON_FTP_TEXT
    return NO_WAY_TEXT if capabilities.shell else NO_WAY_WITHOUT_SHELL_TEXT


def preferred_algorithm(capabilities: Capabilities) -> str:
    """The algorithm compare uses: the first of PREFERRED the connection offers; empty where it offers none."""
    offered = algorithms_of(capabilities)
    return next((algorithm for algorithm in PREFERRED if algorithm in offered), "")


# --- on the server --------------------------------------------------------------


def shell_command(paths: list[str], algorithm: str) -> str:
    """One command line for many files: each file is read by the tool through its standard input, so no name ever appears in what
    comes back, whatever bytes it holds; each gives one line, "ok <hex>" or "no <reason>". Only a regular file is read: a pipe would
    keep the tool waiting. Every path goes through shlex.quote (OR-19)."""
    tool = tool_of(algorithm)
    quoted = " ".join(shlex.quote(path) for path in paths)
    return (
        f"for f in {quoted}; do "
        f'if [ ! -e "$f" ]; then echo {NO_MARK} {shlex.quote(GONE_TEXT)}; '
        f'elif [ ! -f "$f" ]; then echo {NO_MARK} {shlex.quote(NOT_A_FILE_TEXT)}; '
        f'elif [ ! -r "$f" ]; then echo {NO_MARK} {shlex.quote(NOT_READABLE_TEXT)}; '
        f'elif r=$( {{ {tool} < "$f"; }} 2>/dev/null ); then printf \'{OK_MARK} %s\\n\' "${{r%% *}}"; '
        f'else echo {NO_MARK} {shlex.quote(READ_FAILED_TEXT)}; fi; done'
    )


def checksums_from_output(output: str, paths: list[str], algorithm: str) -> list[Checksum]:
    """The answer of shell_command, one line per path in order; a line that is no checksum fails only its own file (OR-15). Only a
    line end splits it: a carriage return or another separator in a reason never shifts the answers against their files."""
    lines = output.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    found: list[Checksum] = []
    for number, path in enumerate(paths):
        line = lines[number] if number < len(lines) and len(lines) == len(paths) else ""
        mark, _space, rest = line.partition(" ")
        if mark == OK_MARK and (value := checksum_from(rest, algorithm)) is not None:
            found.append(Checksum(path, value))
        elif mark == NO_MARK:
            found.append(Checksum(path, problem=rest.strip() or ODD_ANSWER_TEXT))
        else:
            found.append(Checksum(path, problem=ODD_ANSWER_TEXT))
    return found


def batches(paths: list[str]) -> list[list[str]]:
    """The paths in groups that fit one command line each."""
    groups: list[list[str]] = []
    group: list[str] = []
    length = 0
    for path in paths:
        size = len(shlex.quote(path)) + 1
        if group and (length + size > BATCH_CHARACTERS or len(group) >= BATCH_FILES):
            groups.append(group)
            group, length = list[str](), 0
        group.append(path)
        length += size
    if group:
        groups.append(group)
    return groups


def remote_checksums(connection: RemoteConnection, paths: list[str], algorithm: str, keep_going: KeepGoing, each: Each, note: Callable[[str], None] = lambda _text: None) -> bool:
    """The checksum of each file on the server, by the best way the connection offers; a refusal fails only its own file, a lost
    connection stops it all (ConnectionLost). False when keep_going said stop before every file was done; a stop ends the file that
    runs as well. Through the protocol, a connection that does one thing at a time gets a login of its own, so its tab never waits;
    where the server takes no further login, note says calmly that the tab waits, and the tab's own login is used."""
    way = way_of(connection.capabilities, algorithm)
    if not way:
        raise RemoteActionFailed(NOT_THIS_ALGORITHM_TEXT.format(algorithm=algorithm))
    if way == SHELL:
        for group in batches(paths):
            if not keep_going():
                return False
            result = connection.run_stoppable(shell_command(group, algorithm), keep_going)
            if result is None:
                return False
            for found in checksums_from_output(result.output, group, algorithm):
                each(found)
        return True
    try:
        own = connection.login_of_its_own()
    except ConnectionFailed:
        own = None
        note(TAB_WAITS_TEXT)
    login = own or connection
    try:
        for path in paths:
            if not keep_going():
                return False
            try:
                value = login.checksum(path, algorithm, keep_going)
            except ConnectionLost:
                raise
            except RemoteActionFailed as error:
                each(Checksum(path, problem=str(error).strip() or ODD_ANSWER_TEXT))
                continue
            if value is None:
                return False
            each(Checksum(path, value))
        return True
    finally:
        if own is not None:
            own.close()


def remote_files(connection: RemoteConnection, folder: str, entries: list[RemoteEntry], keep_going: KeepGoing) -> tuple[list[str], list[Checksum]] | None:
    """The files these entries stand for, as paths relative to folder: a file itself, and every file inside a folder, never through a
    link to a folder. Folders that can't be read come back as problems. None when stopped."""
    files: list[str] = []
    problems: list[Checksum] = []
    waiting: list[tuple[str, RemoteEntry]] = [(entry.name, entry) for entry in reversed(entries)]  # the last one is taken first
    while waiting:
        if not keep_going():
            return None
        relative, entry = waiting.pop()
        if entry.is_folder and not entry.is_link:
            try:
                inside = connection.list_folder(joined(folder, relative))
            except ConnectionLost:
                raise
            except RemoteActionFailed as error:
                problems.append(Checksum(relative, problem=str(error).strip() or ODD_ANSWER_TEXT))
                continue
            waiting.extend((joined(relative, child.name), child) for child in sorted(inside, key=lambda child: child.name, reverse=True))
        elif entry.is_folder or entry.special:
            problems.append(Checksum(relative, problem=NOT_A_FILE_TEXT))
        else:
            files.append(relative)
    return files, problems


# --- on this computer -------------------------------------------------------------


def local_checksum(path: str, algorithm: str, keep_going: KeepGoing) -> str | None:
    """Read in pieces, never whole into memory; None when keep_going said stop. A file that can't be read raises OSError, a pipe or
    device is refused before it is opened (it would keep the reading waiting)."""
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise IsADirectoryError(NOT_A_FILE_TEXT)
    digest = hashlib.new(algorithm)
    with open(path, "rb") as file:
        while piece := file.read(LOCAL_PIECE_BYTES):
            if not keep_going():
                return None
            digest.update(piece)
    return digest.hexdigest()


def local_checksums(folder: str, relatives: list[str], algorithm: str, keep_going: KeepGoing, each: Each) -> bool:
    """Each file in turn; a file that can't be read fails only itself. False when stopped."""
    for relative in relatives:
        if not keep_going():
            return False
        try:
            value = local_checksum(os.path.join(folder, relative), algorithm, keep_going)
        except IsADirectoryError:
            each(Checksum(relative, problem=NOT_A_FILE_TEXT))
            continue
        except (OSError, ValueError) as error:
            each(Checksum(relative, problem=UNREADABLE_TEXT.format(reason=getattr(error, "strerror", None) or error)))
            continue
        if value is None:
            return False
        each(Checksum(relative, value))
    return True


def local_files(folder: str, names: list[str], keep_going: KeepGoing) -> tuple[list[str], list[Checksum]] | None:
    """As remote_files, on this computer: never through a link to a folder; a folder that can't be read is a problem. None when stopped."""
    files: list[str] = []
    problems: list[Checksum] = []
    waiting = list(reversed(names))  # the last one is taken first
    while waiting:
        if not keep_going():
            return None
        relative = waiting.pop()
        path = os.path.join(folder, relative)
        try:
            mode = os.lstat(path).st_mode
        except OSError as error:
            problems.append(Checksum(relative, problem=UNREADABLE_TEXT.format(reason=error.strerror or error)))
            continue
        if stat.S_ISDIR(mode):
            try:
                inside = sorted(os.listdir(path))
            except OSError as error:
                problems.append(Checksum(relative, problem=UNREADABLE_TEXT.format(reason=error.strerror or error)))
                continue
            waiting.extend(os.path.join(relative, name) for name in reversed(inside))
        else:
            files.append(relative)  # a link to a file is read through, as the tools on the server do
    return files, problems
