"""The rsync way of transferring (D-014), with the transfer options: when a question may come, the file by file way looks over the job
first, so every question comes before rsync writes anything, and rsync gets the exact list of what it may send."""

import os
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from chrono_ftp.config import parts
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import Limit, RemoteConnection, joined, limit_now, parent_of
from chrono_ftp.core.connection import SFTP
from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, RemoteActionFailed
from chrono_ftp.core.transferoptions import RESUME_OFF
from chrono_ftp.transfers.filebyfile import durable
from chrono_ftp.transfers.job import Job, Meter, Stopped
from chrono_ftp.transfers.options import Action

# --- Version ------------------------------------------------------------
__version__ = "rsyncway.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: A new speed limit takes effect at once: rsync ends and starts again with it, without the files it sent already (also in a
#          run of all of it), and goes on from its unfinished copy (FC-02); the files rsync sent are remembered with the job, so a resume
#          or a retry never asks about them again, and for a move that broke off their originals go when it goes on; rsync always keeps
#          its unfinished copy, so a pause keeps it; with resume off in a folder of the job's own, new for each run that is no resume;
#          after a restart the percentage goes on from what was done; rsync names a file when it starts it, so a file counts as sent
#          only once the run ended well or it is found whole in its place; with resume off the job's own folder of unfinished copies goes
#          at once when nothing will continue from it, once every process rsync started has ended; its pipe is watched without the limit
#          of select(); after a look-over the percentage goes on from what it reached; the speed rsync says it has is the job's (FC-01)
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
SPEED_PATTERN: re.Pattern[bytes] = re.compile(rb"\d+%\s+(\d+(?:[.,]\d+)?)([kKMGT]?)B/s")
SPEED_UNITS: dict[bytes, int] = {b"": 1, b"k": 1024, b"K": 1024, b"M": 1024**2, b"G": 1024**3, b"T": 1024**4}
LOGIN_FAILED_STATUSES: frozenset[int] = frozenset({12, 255})  # rsync could not start its ssh: the other way still works
READ_SIZE: int = 4096
STALL_SECONDS: int = 60
POLL_SECONDS: float = 0.2
DROPPED_TEXT: str = "The connection broke off during the transfer. Nothing arrived under a wrong name; the next try starts from the beginning."
DROPPED_KEPT_TEXT: str = "The connection broke off during the transfer. Nothing arrived under a wrong name; what arrived is kept, so the next try continues from there."
SENT_MARK: bytes = b"chrono-ftp-sent:"  # rsync names each file it sent on a line of its own that starts so
ESCAPE_PATTERN: re.Pattern[bytes] = re.compile(rb"\\#([0-7]{3})")  # how rsync writes a byte it can't show in a name
TOKEN_LENGTH: int = 8
GROUP_SECONDS: float = 5.0  # the most an ended rsync's own processes get to finish their end
RESTART_STATUS: int = -1  # no status rsync gives: it was ended to start again with a new speed limit
PARTIAL_FOLDER: str = ".chrono-ftp-partial"  # rsync keeps an unfinished file here, never under the real name, and removes it once used


def rsync_possible(job: Job) -> bool:
    """Not for an upload that sets rights: rsync would also change the rights of every folder that is there already, which the reference
    program and the file by file way leave as they are; only a folder the upload makes gets the chosen rights."""
    if job.upload and job.options.set_rights:
        return False
    return job.connection.protocol == SFTP and job.remote.capabilities.has("rsync") and shutil.which("rsync") is not None and not job.has_password


def partial_folder_of(job: Job) -> str:
    """rsync's folder of unfinished copies. Always given, so a pause keeps what arrived. With resume off it has a name of this job's own,
    new for every run that does not continue a pause, so nothing an earlier run left is ever continued."""
    if job.options.resume != RESUME_OFF:
        return PARTIAL_FOLDER
    return f"{PARTIAL_FOLDER}-{job.partial_token}"


def drop_own_partials(job: Job) -> None:
    """With resume off, the job's own folder of unfinished copies goes at once when a run ends otherwise than in a pause, or a pause ends
    without a resume: nothing will continue from it. It is ours by its token, here or on the server, in the folder the job writes to and
    below what it writes. What can't be removed now is tidied later, as any folder that can't be continued."""
    if job.options.resume != RESUME_OFF or not job.partial_token:
        return
    name = partial_folder_of(job)
    try:
        if job.upload:
            places = [joined(job.remote_path, name), joined(job.remote_path, job.name)]
            if job.remote.capabilities.shell:
                job.remote.run(f"find {' '.join(shlex.quote(place) for place in places)} -type d -name {shlex.quote(name)} -prune -exec rm -rf -- {{}} + 2>/dev/null")
            elif job.remote.details(places[0]) is not None:
                job.remote.delete(places[0], True)
        else:
            top = Path(job.local, job.name)
            found = [Path(job.local, name)]
            if top.is_dir() and not top.is_symlink():
                for place, folders, _files in os.walk(top):
                    found += [Path(place, folder) for folder in folders if folder == name]
            for folder in found:
                if folder.is_dir() and not folder.is_symlink():
                    shutil.rmtree(folder, ignore_errors=True)
    except (ChronoFtpError, OSError):
        return  # tried again when the job leaves the queue; else tidied later


def is_partial_folder(name: str) -> bool:
    return name == PARTIAL_FOLDER or (name.startswith(f"{PARTIAL_FOLDER}-") and len(name) == len(PARTIAL_FOLDER) + 1 + TOKEN_LENGTH)


def _excluded_pattern(name: str) -> str:
    """A path rsync sent, as a rule anchored at the top of the transfer that matches exactly it."""
    if any(character in name for character in "*?["):
        name = "".join(f"\\{character}" if character in "\\*?[" else character for character in name)  # escaped only where wildcards are
    return f"/{name}"


def rsync_arguments(job: Job, limit: int, remote_shell: list[str] | None = None, files_from: str = "", copy_links: bool = False, only_new: bool = False, excluded_from: str = "") -> list[str]:
    """files_from: a file with the exact paths to send, relative to the folder the job comes from; empty: all of it. excluded_from: a file
    with the paths sent already (after a restart for a new limit), left out of a run of all of it."""
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
    arguments.append(f"--partial-dir={partial_folder_of(job)}")
    if limit:
        arguments.append(f"--bwlimit={limit}")
    if files_from and copy_links:
        arguments.append("--copy-links")  # the list holds links to files only (never one to a folder): their content goes, as file by file
    if excluded_from and not files_from:
        arguments += ["--from0", f"--exclude-from={excluded_from}"]  # rsync reads the file where it meets it: --from0 must come first
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


def with_rsync(job: Job, limit: Limit, program: str, remote_shell: list[str] | None, meter: Meter, files: list[str] | None, sources: dict[str, tuple[int, float | None]] | None, copy_links: bool = False, new_files: frozenset[str] = frozenset()) -> dict[str, int] | None:
    """None when rsync could not log in at all and nothing moved, so the other way takes over; else what the queue tells (the names of
    its notes): the sources a move kept, per reason, and the new files that appeared on the other side meanwhile. files: the exact list,
    or None for all; new_files: those of it that were not there at the look-over, sent in a run of their own that never replaces;
    sources: the size and date of each file as it was read. limit: asked again while rsync runs; a new one starts rsync again with it."""
    job.way = "rsync"
    if job.options.resume == RESUME_OFF and not (job.resuming and job.partial_token):
        job.partial_token = uuid.uuid4().hex[:TOKEN_LENGTH]  # nothing an earlier run left is continued
    side, base, source_base = (parts.UP, job.remote_path, str(Path(job.local).parent)) if job.upload else (parts.DOWN, job.local, parent_of(job.remote_path))
    if job.options.resume != RESUME_OFF or parts.partial_note(job.connection, side, base) is None:
        parts.remember_partial(job.connection, side, base, source_base, job.options.resume)  # rsync's unfinished copies: ours, for tidying
    job.rsync_sent.clear()
    job.rsync_started.clear()
    try:
        return _with_rsync(job, limit, program, remote_shell, meter, files, sources, copy_links, new_files)
    except BaseException:
        _mark_sent(job, arrived(job, job.rsync_started - job.rsync_sent, new_files), sources)  # what arrived before the break, only that
        if job.options.delete_source and sources is not None:
            for name in job.rsync_sent & set(sources):
                job.moved_pending[name] = sources[name]  # arrived: its original goes, checked again, when the job goes on
        raise


def _with_rsync(job: Job, limit: Limit, program: str, remote_shell: list[str] | None, meter: Meter, files: list[str] | None, sources: dict[str, tuple[int, float | None]] | None, copy_links: bool, new_files: frozenset[str]) -> dict[str, int] | None:
    runs: list[tuple[list[str] | None, bool]] = [(None, False)]
    if files is not None:
        replacing = [name for name in files if name not in new_files]
        adding = [name for name in files if name in new_files]
        runs = [(listed, only_new) for listed, only_new in ((replacing, False), (adding, True)) if listed]
    moved = False
    sent = set[str]()
    for listed, only_new in runs:
        status, errors, moved_now, sent_now = _run_list(job, limit, program, remote_shell, meter, listed, copy_links, only_new, new_files, sources)
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
        told.update(remove_arrived_sources(job, sources))
        _remove_empty_sources(job, [name for name in files or [] if name not in sources and name not in left])
    return told


def _run_list(job: Job, limit: Limit, program: str, remote_shell: list[str] | None, meter: Meter, files: list[str] | None, copy_links: bool, only_new: bool, new_files: frozenset[str] = frozenset(), sources: dict[str, tuple[int, float | None]] | None = None) -> tuple[int, str, bool, set[str]]:
    """One run of rsync over files (None: all of it): its status, error text, whether a file may have arrived (rsync started one: then the
    other way never takes over after a broken line, as before), and the files known to have arrived. rsync
    names a file when it starts it, so a name counts as sent only when the run ended well, or, after any other end, once the file is
    found whole in its place (arrived). When the limit changes meanwhile, rsync ends and starts again with the new one: without the
    files that arrived, and from its unfinished copy of the file it was on."""
    sent = set[str]()
    started = set[str]()
    base = job.percent if job.total else 0  # what was done before (a look-over, a restart): the percentage goes on from there, never back
    while True:
        speed = limit_now(limit)
        left = None if files is None else [name for name in files if name not in sent]
        with tempfile.NamedTemporaryFile(prefix="chrono-ftp-list-") as listed, tempfile.NamedTemporaryFile(prefix="chrono-ftp-sent-") as done:
            if left is not None:
                listed.write(b"".join(os.fsencode(name) + b"\0" for name in left))  # NUL apart: a name may hold any other character
                listed.flush()
            elif sent:
                done.write(b"".join(os.fsencode(_excluded_pattern(name)) + b"\0" for name in sorted(sent)))  # never sent twice
                done.flush()
            arguments = rsync_arguments(job, speed, remote_shell, listed.name if left is not None else "", copy_links, only_new, done.name if left is None and sent else "")
            arguments[0] = program
            status, errors, _started, started_now = _run(job, arguments, meter, lambda: limit_now(limit) != speed, base)
        sent |= started_now if status == 0 else arrived(job, started_now, new_files)  # only a run that ended well vouches for all
        started |= started_now
        _mark_sent(job, sent, sources)
        if status != RESTART_STATUS:
            return status, errors, bool(started), sent  # whether it may have moved anything: a file it started may have arrived
        base = job.percent


def remove_arrived_sources(job: Job, sources: dict[str, tuple[int, float | None]]) -> dict[str, int]:
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


def _run(job: Job, arguments: list[str], meter: Meter, limit_changed: Callable[[], bool] = lambda: False, base: int = 0) -> tuple[int, str, bool, set[str]]:
    """The status, the error text, whether any file arrived (from the files rsync names as sent, never from its progress: a percent of a
    file that did not arrive is no file the other way may leave out), and those files. RESTART_STATUS when it was ended because the speed
    limit changed (limit_changed). Every file it names as sent is remembered with the job, also when it is stopped or paused: a resume or a
    retry never asks about it again. base: the percentage done before a restart; rsync's own counts only what is left, so it is shown
    as that share of the rest."""
    lines: list[bytes] = []
    pending = b""
    unfinished = b""  # the start of a piece of rsync's output that ends in a later read
    restart = False
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
                if limit_changed():
                    restart = True
                    break
                if not _readable(descriptor, POLL_SECONDS):
                    continue
                block = os.read(descriptor, READ_SIZE)
                if not block:
                    break
                *ended, pending = (pending + block).split(b"\n")
                lines += ended
                job.rsync_started.update(name for name in _sent_names(ended) if not name.endswith("/"))  # started, not yet known to have arrived
                text = unfinished + block
                progress, unfinished = progress_in(text)
                if progress is not None:
                    percent, left = progress
                    job.percent = base + (100 - base) * percent // 100
                    job.seconds_left = left if left is not None else job.seconds_left
                    job.speed = speed_in(text)
                    meter.changed(job)
            if sys.platform != "win32" and not restart:
                os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)  # ended, not yet reaped: its group's number stays its own
        finally:
            _end_group(process, False)
            *more, pending = (pending + _rest_of(process)).split(b"\n")  # what it wrote until it ended: every file it started
            lines += more
            job.rsync_started.update(name for name in _sent_names([*lines, pending]) if not name.endswith("/"))
        status = RESTART_STATUS if restart else process.returncode
        errors_file.seek(0)
        started = {name for name in _sent_names([*lines, pending]) if not name.endswith("/")}  # a folder it names is no file
        return status, errors_file.read().decode("utf-8", "replace"), bool(started), started


def _mark_sent(job: Job, names: set[str], sources: dict[str, tuple[int, float | None]] | None = None) -> None:
    """Files that arrived whole: a move removes their originals, and a resume or a retry does not ask about them again while their source
    and what arrived are unchanged (its size and date as read, from the look-over, or here for an upload); one whose source is not
    known that way is looked at again as any other."""
    job.rsync_sent |= names
    for name in names:
        read = sources.get(name) if sources is not None else None
        if read is None and job.upload:
            try:
                details = os.stat(Path(job.local).parent / name)
                read = (details.st_size, details.st_mtime)
            except OSError:
                read = None
        if read is not None:
            job.decided[name] = Action.SKIP
            job.sent_as[name] = (read[0], read[1], job.options.keep_timestamps)  # rsync sets the date wherever dates are kept


def arrived(job: Job, names: set[str], new_files: frozenset[str] = frozenset()) -> set[str]:
    """Of the files rsync started (names, relative to the folder the job comes from), the ones found whole in their place after rsync
    ended otherwise than well: a file of the size of its source, and, where dates are kept, of its date; without dates, one that was not
    there before (new_files) or differs from the file there that the person agreed to replace. Anything that can't be told is taken as
    not arrived: it is sent again, never left out."""
    found = set[str]()
    for name in names:
        try:
            now = _now(job, name)
        except (ChronoFtpError, OSError):
            continue
        if now is None:
            continue
        (source_size, source_date), target = now
        if target is None or target[0] != source_size:
            continue
        if job.options.keep_timestamps:
            if source_date is not None and target[1] is not None and int(target[1]) == int(source_date):
                found.add(name)
            continue
        shown = job.shown.get(name)
        if name in new_files or (shown is not None and (shown[0], shown[2]) != target):
            found.add(name)
    return found


def _now(job: Job, name: str) -> tuple[tuple[int, float | None], tuple[int, float | None] | None] | None:
    """The source of name (relative to the folder the job comes from) and what is in its place: their sizes and dates; None for the place
    when nothing whole is there, None for all when the source is not a file or can't be read."""
    if job.upload:
        source = os.stat(Path(job.local).parent / name)
        there = job.remote.fresh_details(joined(job.remote_path, name))
        target = None if there is None or there.is_folder or not there.size_known or there.special else (there.size, there.modified)
        return (source.st_size, float(source.st_mtime)), target
    remote = job.remote.fresh_details(joined(parent_of(job.remote_path), name))
    if remote is None or remote.is_folder or not remote.size_known:
        return None
    local = Path(job.local) / name
    target = (local.stat().st_size, float(local.stat().st_mtime)) if local.is_file() and not local.is_symlink() else None
    return (remote.size, remote.modified), target


def _readable(descriptor: int, seconds: float) -> bool:
    """Whether rsync's pipe has something to read within seconds. Through the system's own way of watching (not select(), which can't
    watch a descriptor numbered over 1024, as a long session may have)."""
    with selectors.DefaultSelector() as watch:
        watch.register(descriptor, selectors.EVENT_READ)
        return bool(watch.select(seconds))


def _rest_of(process: "subprocess.Popen[bytes]") -> bytes:
    """What an ended rsync still had in its pipe; the pipe is closed after."""
    output = process.stdout
    if output is None or output.closed:
        return b""
    found = b""
    try:
        descriptor = output.fileno()
        while _readable(descriptor, POLL_SECONDS):  # a pipe nothing holds any more ends at once
            block = os.read(descriptor, READ_SIZE)
            if not block:
                break
            found += block
    except (OSError, ValueError):
        pass
    finally:
        output.close()
    return found


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


def speed_in(text: bytes) -> float | None:
    """The last speed rsync wrote in text, in bytes per second; None where it wrote none. Only whole pieces, never a file's name."""
    found: float | None = None
    for piece in re.split(rb"[\r\n]", text)[:-1]:
        if SENT_MARK in piece:
            continue
        matches = SPEED_PATTERN.findall(piece)
        if matches:
            amount, unit = matches[-1]
            found = float(amount.replace(b",", b".")) * SPEED_UNITS[unit]
    return found


def _end_group(process: "subprocess.Popen[bytes]", close_output: bool = True) -> None:
    """Ends whatever is left of rsync's process group (rsync itself after a stop or a failure here, its ssh or other children always),
    waits until every process of the group has ended (a receiver here puts away the unfinished copy it keeps as it ends), and only then
    reaps rsync, so the group's number can't have gone to another process meanwhile. A group that ended is nothing to end."""
    if sys.platform == "win32":
        if process.poll() is None:
            process.terminate()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + GROUP_SECONDS
        while _others_in_group(process.pid) and time.monotonic() < deadline:
            threading.Event().wait(POLL_SECONDS / 20)  # what rsync started finishes its own end: it may still put away what it kept
    process.wait()
    if close_output and process.stdout is not None:
        process.stdout.close()


def _others_in_group(group: int) -> bool:
    """Whether a process of rsync's group other than rsync itself still runs (one that ended and waits to be reaped does not count).
    Read from /proc; where there is none, nothing is known to run."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return False
    for entry in entries:
        if not entry.isdigit() or int(entry) == group:
            continue
        try:
            fields = Path("/proc", entry, "stat").read_text(encoding="ascii", errors="replace").rsplit(")", 1)[-1].split()
        except OSError:
            continue  # it ended meanwhile, or is not ours to read
        if len(fields) > 2 and fields[2] == str(group) and fields[0] != "Z":
            return True
    return False


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
