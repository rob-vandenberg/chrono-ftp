"""The rsync way of transferring (D-014), with the transfer options: when a question may come, the file by file way looks over the job
first, so every question comes before rsync writes anything, and rsync gets the exact list of what it may send."""

import os
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

from chrono_ftp.config import parts
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import RemoteConnection, joined, parent_of
from chrono_ftp.core.connection import SFTP
from chrono_ftp.core.errors import ConnectionLost, RemoteActionFailed
from chrono_ftp.core.transferoptions import RESUME_OFF
from chrono_ftp.transfers.filebyfile import durable
from chrono_ftp.transfers.job import Job, Meter, Stopped

# --- Version ------------------------------------------------------------
__version__ = "rsyncway.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: moved here from queue.py; resume in its own partial folder, dates, rights, count first, the exact list to send; a
#          move removes each source only when it is unchanged and arrived at its size and date, and never removes a folder through a
#          link; an upload that sets rights goes file by file, which leaves the rights of existing folders as they are; every source of
#          a move is looked at on its own, each reason one stays is counted, and a fetched copy is on the disk before its original goes;
#          a link to a file sends its content, as the file by file way does, in both directions; files that were not there at the look-
#          over go in a run of their own that never replaces, and one that turned up meanwhile is told; only when rsync could not log in
#          and nothing moved does the other way take over, a drop after that is a broken-off transfer; a new file is ours only when rsync
#          names it as sent: one it left because it turned up meanwhile is never counted as ours, nor is its source removed; an upload
#          move has the server put each file on its disk before its original goes, and where it can't, that is told; the folders of
#          unfinished copies it may keep are noted as ours; a move removes only the folders on its list that arrived and ended up empty;
#          whether anything moved is told by the files rsync names as sent; rsync runs in a process group of its own, which a stop,
#          a failure or its end ends whole, so no process it started outlives it; progress is read only from whole pieces that name
#          no file, and stays within 0 to 100

PROGRESS_PATTERN: re.Pattern[bytes] = re.compile(rb"(\d+)%(?:\s+\S+\s+(\d+):(\d\d):(\d\d))?")
LOGIN_FAILED_STATUSES: frozenset[int] = frozenset({12, 255})  # rsync could not start its ssh: the other way still works
READ_SIZE: int = 4096
STALL_SECONDS: int = 60
POLL_SECONDS: float = 0.2
DROPPED_TEXT: str = "The connection broke off during the transfer. Nothing arrived under a wrong name; the next try starts from the beginning."
DROPPED_KEPT_TEXT: str = "The connection broke off during the transfer. Nothing arrived under a wrong name; what arrived is kept, so the next try continues from there."
SENT_MARK: bytes = b"chrono-ftp-sent:"  # rsync names each file it sent on a line of its own that starts so
ESCAPE_PATTERN: re.Pattern[bytes] = re.compile(rb"\\#([0-7]{3})")  # how rsync writes a byte it can't show in a name
PARTIAL_FOLDER: str = ".chrono-ftp-partial"  # rsync keeps an unfinished file here, never under the real name, and removes it once used


def rsync_possible(job: Job) -> bool:
    """Not for an upload that sets rights: rsync would also change the rights of every folder that is there already, which the reference
    program and the file by file way leave as they are; only a folder the upload makes gets the chosen rights."""
    if job.upload and job.options.set_rights:
        return False
    return job.connection.protocol == SFTP and job.remote.capabilities.has("rsync") and shutil.which("rsync") is not None and not job.has_password


def rsync_arguments(job: Job, limit: int, remote_shell: list[str] | None = None, files_from: str = "", copy_links: bool = False, only_new: bool = False) -> list[str]:
    """files_from: a file with the exact paths to send, relative to the folder the job comes from; empty: all of it."""
    settings = job.connection
    options = job.options
    ssh = ["ssh", "-p", str(settings.port), "-o", "BatchMode=yes", "-o", f"UserKnownHostsFile={trust.known_hosts_path()} {Path.home() / '.ssh' / 'known_hosts'}"]
    if settings.key_file:
        ssh += ["-i", settings.key_file]
    login = f"{settings.user}@{settings.host}" if settings.user else settings.host
    arguments = ["rsync", "-a", "--protect-args", "--no-owner", "--no-group", "--info=progress2", f"--timeout={STALL_SECONDS}", "-e", shlex.join(remote_shell or ssh)]
    arguments.append("--no-perms")  # a new file gets the server's usual rights, a replaced one keeps its own
    arguments.append(f"--out-format={SENT_MARK.decode()}%n")  # which files it really sent: one it left is not ours, and none sent is nothing moved
    if not options.keep_timestamps:
        arguments.append("--no-times")
    if options.count_first:
        arguments.append("--no-inc-recursive")
    if options.resume != RESUME_OFF:
        arguments.append(f"--partial-dir={PARTIAL_FOLDER}")
    if limit:
        arguments.append(f"--bwlimit={limit}")
    if files_from and copy_links:
        arguments.append("--copy-links")  # the list holds links to files only (never one to a folder): their content goes, as file by file
    if files_from:
        # Each file of a replacing list was chosen, so rsync's quick look may not skip it; a file of a list of new ones was not there at the
        # look-over, so nobody was asked: one that turned up meanwhile is never replaced.
        arguments += [f"--files-from={files_from}", "--from0", "--ignore-existing" if only_new else "--ignore-times"]
        if job.upload:
            return [*arguments, "--", f"{str(Path(job.local).parent).rstrip('/')}/", f"{login}:{job.remote_path.rstrip('/')}/"]
        return [*arguments, "--", f"{login}:{parent_of(job.remote_path).rstrip('/')}/", f"{job.local.rstrip('/')}/"]
    if job.upload:
        return [*arguments, "--", job.local, f"{login}:{job.remote_path.rstrip('/')}/"]
    return [*arguments, "--", f"{login}:{job.remote_path}", f"{job.local.rstrip('/')}/"]


def with_rsync(job: Job, limit: int, program: str, remote_shell: list[str] | None, meter: Meter, files: list[str] | None, sources: dict[str, tuple[int, float | None]] | None, copy_links: bool = False, new_files: frozenset[str] = frozenset()) -> dict[str, int] | None:
    """None when rsync could not log in at all and nothing moved, so the other way takes over; else what the queue tells (the names of
    its notes): the sources a move kept, per reason, and the new files that appeared on the other side meanwhile. files: the exact list,
    or None for all; new_files: those of it that were not there at the look-over, sent in a run of their own that never replaces;
    sources: the size and date of each file as it was read."""
    job.way = "rsync"
    if job.options.resume != RESUME_OFF:  # rsync may keep unfinished copies in a folder of its own: noted as ours, for tidying
        if job.upload:
            parts.remember_partial(job.connection, parts.UP, job.remote_path, str(Path(job.local).parent), job.options.resume)
        else:
            parts.remember_partial(job.connection, parts.DOWN, job.local, parent_of(job.remote_path), job.options.resume)
    runs: list[tuple[list[str] | None, bool]] = [(None, False)]
    if files is not None:
        replacing = [name for name in files if name not in new_files]
        adding = [name for name in files if name in new_files]
        runs = [(listed, only_new) for listed, only_new in ((replacing, False), (adding, True)) if listed]
    moved = False
    sent = set[str]()
    for listed, only_new in runs:
        status, errors, moved_now, sent_now = _run_list(job, limit, program, remote_shell, meter, listed, copy_links, only_new)
        sent |= sent_now
        if status in LOGIN_FAILED_STATUSES:
            if not moved and not moved_now:
                return None
            raise ConnectionLost(DROPPED_TEXT if job.options.resume == RESUME_OFF else DROPPED_KEPT_TEXT)
        if status != 0:
            raise RemoteActionFailed(errors.strip().splitlines()[-1] if errors.strip() else "The transfer did not finish.")
        moved = moved or moved_now
    told: dict[str, int] = {}
    left = new_files - sent  # rsync did not send them: each was there by then, so what is there is someone else's
    if left:
        told["appeared"] = len(left)
    if job.options.delete_source and sources is not None:
        for name in left:
            sources.pop(name, None)  # it is not what was sent, whatever its size and date: its source stays
        told.update(_remove_arrived_sources(job, sources))
        _remove_empty_sources(job, [name for name in files or [] if name not in sources and name not in left])
    return told


def _run_list(job: Job, limit: int, program: str, remote_shell: list[str] | None, meter: Meter, files: list[str] | None, copy_links: bool, only_new: bool) -> tuple[int, str, bool, set[str]]:
    with tempfile.NamedTemporaryFile(prefix="chrono-ftp-list-") as listed:
        if files is not None:
            listed.write(b"".join(os.fsencode(name) + b"\0" for name in files))  # NUL apart: a name may hold any other character
            listed.flush()
        arguments = rsync_arguments(job, limit, remote_shell, listed.name if files is not None else "", copy_links, only_new)
        arguments[0] = program
        return _run(job, arguments, meter)


def _remove_arrived_sources(job: Job, sources: dict[str, tuple[int, float | None]]) -> dict[str, int]:
    """A move (FB-08): each source goes only when it still has the size and date it had when it was read, and what arrived has that size
    and, when dates are kept, that date. Anything else stays, so a file that changed meanwhile keeps its newer bytes. Every source is
    looked at, also after one could not be removed; each reason one stays is counted on its own."""
    connection = job.remote
    kept = {"kept_changed": 0, "kept_unchecked": 0, "kept_not_removed": 0, "removed_unflushed": 0}

    def arrived_whole(size: int, modified: float | None, found_size: int | None, found_modified: float | None) -> bool:
        if found_size is None or found_size != size:
            return False
        return not job.options.keep_timestamps or modified is None or (found_modified is not None and int(found_modified) == int(modified))

    for relative, (size, modified) in sources.items():
        if job.upload:
            source = Path(job.local).parent / relative
            try:
                now = source.stat()
            except OSError:
                continue  # gone already
            arrived = connection.fresh_details(joined(job.remote_path, relative))
            if arrived is None or arrived.is_folder or not arrived_whole(size, modified, arrived.size if arrived.size_known else None, arrived.modified):
                kept["kept_unchecked"] += 1
            elif (now.st_size, now.st_mtime) != (size, modified):
                kept["kept_changed"] += 1
            else:
                try:
                    connection.make_durable(joined(job.remote_path, relative))  # on the server's disk before the only other copy goes
                except RemoteActionFailed:  # also a connection that broke off meanwhile: unconfirmed is unchecked, and the original stays
                    kept["kept_unchecked"] += 1
                    continue
                try:
                    source.unlink()
                except OSError:
                    kept["kept_not_removed"] += 1
                    continue
                if not connection.capabilities.durable:
                    kept["removed_unflushed"] += 1  # checked by its size and date; this server can't say it is on its disk
        else:
            remote = joined(parent_of(job.remote_path), relative)
            target = Path(job.local) / relative
            now_there = connection.fresh_details(remote)
            if now_there is None:
                continue  # gone already
            try:
                here = target.stat()
            except OSError:
                here = None
            if here is None or not arrived_whole(size, modified, here.st_size, here.st_mtime):
                kept["kept_unchecked"] += 1
            elif (now_there.size, now_there.modified, now_there.is_folder, now_there.size_known) != (size, modified, False, True):
                kept["kept_changed"] += 1
            else:
                try:
                    durable(str(target))  # on the disk before the only other copy goes
                    connection.delete(remote, False)
                except ConnectionLost:
                    raise
                except (RemoteActionFailed, OSError):
                    kept["kept_not_removed"] += 1
    return kept


def _byte(match: re.Match[bytes]) -> bytes:
    return bytes([int(match.group(1), 8)])


def _sent_names(lines: list[bytes]) -> set[str]:
    """The files rsync names as sent, as the paths of the list it was given."""
    found = set[str]()
    for line in lines:
        at = line.find(SENT_MARK)
        if at >= 0:
            name = ESCAPE_PATTERN.sub(_byte, line[at + len(SENT_MARK):])
            found.add(os.fsdecode(name))
    return found


def _run(job: Job, arguments: list[str], meter: Meter) -> tuple[int, str, bool, set[str]]:
    """The status, the error text, whether any file arrived (from the files rsync names as sent, never from its progress: a percent of a
    file that did not arrive is no file the other way may leave out), and those files."""
    lines: list[bytes] = []
    pending = b""
    unfinished = b""  # the start of a piece of rsync's output that ends in a later read
    with tempfile.TemporaryFile() as errors_file:  # a file, so a talkative rsync can never block on a full pipe
        # A group of its own: rsync starts its ssh, so a stop, a failure or the end ends every process it started, not rsync alone.
        process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=errors_file, start_new_session=True)
        try:
            output = process.stdout
            assert output is not None
            descriptor = output.fileno()
            while True:
                if job.stop_asked.is_set():
                    raise Stopped
                if not select.select([descriptor], [], [], POLL_SECONDS)[0]:
                    continue
                block = os.read(descriptor, READ_SIZE)
                if not block:
                    break
                *ended, pending = (pending + block).split(b"\n")
                lines += ended
                progress, unfinished = progress_in(unfinished + block)
                if progress is not None:
                    job.percent, left = progress
                    job.seconds_left = left if left is not None else job.seconds_left
                    meter.changed(job)
            if sys.platform != "win32":
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)  # ended, not yet reaped: its group's number stays its own
        finally:
            _end_group(process)
        status = process.returncode
        errors_file.seek(0)
        sent = _sent_names([*lines, pending])
        return status, errors_file.read().decode("utf-8", "replace"), bool(sent), sent


def progress_in(text: bytes) -> tuple[tuple[int, int | None] | None, bytes]:
    """The last progress rsync wrote in text (percent, 0 to 100, and the seconds left where it says them), and the start of a piece that
    is not whole yet. Only whole pieces count (each ends at a carriage return or a new line), and never one that names a file sent: a
    name such as "Grade 100%.pdf" is no progress."""
    *whole, rest = re.split(rb"[\r\n]", text)
    found: tuple[int, int | None] | None = None
    for piece in whole:
        if SENT_MARK in piece:
            continue
        matches = PROGRESS_PATTERN.findall(piece)
        if matches:
            percent, hours, minutes, seconds = matches[-1]
            found = (min(100, int(percent)), int(hours) * 3600 + int(minutes) * 60 + int(seconds) if hours else None)
    return found, rest


def _end_group(process: "subprocess.Popen[bytes]") -> None:
    """Ends whatever is left of rsync's process group (rsync itself after a stop or a failure here, its ssh or other children always),
    and only then reaps rsync, so the group's number can't have gone to another process meanwhile. A group that ended is nothing to end."""
    if sys.platform == "win32":
        if process.poll() is None:
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    process.wait()
    if process.stdout is not None:
        process.stdout.close()


def _remove_empty_sources(job: Job, folders: list[str]) -> None:
    """rsync removed each file that arrived; of the folders on its list (relative to where the job comes from), each that arrived on the
    other side and ended up empty here goes too, deepest first. A folder that did not arrive (its item failed), or still holds a file,
    stays; so does every folder that was not on the list."""
    for relative in sorted(folders, key=lambda path: path.count("/"), reverse=True):
        if job.upload:
            source = Path(job.local).parent / relative
            there = job.remote.fresh_details(joined(job.remote_path, relative))
            if there is None or not there.is_folder or there.is_link or source.is_symlink():
                continue  # it did not arrive: its source stays
            try:
                source.rmdir()  # only when it ended up empty
            except OSError:
                pass
            continue
        target = Path(job.local) / relative
        if not target.is_dir() or target.is_symlink():
            continue  # it did not arrive: its source stays
        remote = joined(parent_of(job.remote_path), relative)
        here = job.remote.fresh_details(remote)
        if here is None or not here.is_folder or here.is_link:
            continue  # never through a link: what it leads to was never part of the move
        try:
            job.remote.remove_empty_folder(remote)  # only when it ended up empty
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            pass
