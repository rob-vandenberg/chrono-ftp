import json
import os
import shlex
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import cast
from pathlib import Path

from chrono_ftp.config import parts
from chrono_ftp.connections.base import LOCAL_READ_TEXT, LOCAL_WRITE_TEXT, OLD_SUFFIX, RemoteConnection, RemoteEntry, joined, parent_of
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.transferoptions import RESUME_OFF, TransferOptions
from chrono_ftp.core.errors import ChronoFtpError, ConnectionFailed, ConnectionLost, LoginRefused, NoMoreLogins, RemoteActionFailed
from chrono_ftp.transfers.filebyfile import FileWay, Shown, end_pause_notes, readable, undo_broken_appends
from chrono_ftp.transfers.job import PART_SUFFIX, Job, Meter, State, Stopped
from chrono_ftp.transfers.options import BATCH_CHOICES, Action, Answer, Choice, Conflict, resumable
from chrono_ftp.transfers.conversion import chooses_items
from chrono_ftp.transfers.rsyncway import PARTIAL_FOLDER, drop_own_partials, is_partial_folder, partial_folder_of, remove_arrived_sources, rsync_possible, with_rsync
from chrono_ftp.transfers.sidework import SideJob, run_side_job
from chrono_ftp.transfers.schedule import AT_ONCE, FEWEST_AT_ONCE, HELD_NEXT, HELD_PLACE, HELD_ROOM, MOST_AT_ONCE, Server, in_the_way, server_of, share

# --- Version ------------------------------------------------------------
__version__ = "queue.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: An item can be side work instead of a transfer: the deletes, moves, dates and local copies of Apply in the Synchronize
#          window run through the queue like any other item (FF-10)
# v0.0.41: Masks (block FD): a job with a file mask, hidden files or empty folders left out, text files or changed names is looked
#          over first, so rsync gets the exact list of what passes, and what it can't send goes file by file; what was left out is told
# v0.0.13: Queue control (FC-02 to FC-04): close() waits for the thread, and no call reaches the window once it returned; pause and
#          resume, keeping what arrived; move up and down, run now, retry, delete, also of a running job; a speed limit per job that
#          takes effect at once; pause all, resume all, clear finished; items that start by themselves or wait to be started; a job
#          that was done leaves the list after a while; on an error the queue goes on or pauses; the queue tells when it has nothing
#          left to do, with the connections it used and how many jobs failed; the answers of a job are kept for a resume or a retry;
#          chosen jobs move together, keeping their order; a pause is never taken over a stop on its way; closing keeps a pause on its
#          way; a move that broke off removes the originals of what arrived when it goes on; every folder of rsync's unfinished copies is
#          tidied, the ones paused jobs go on from excepted, however long they wait; a stop ends a pause; the queue tells it is done
#          and whether it ended by itself: whether the last job to leave was done or failed, not stopped or deleted by the person; every
#          run counts its progress from nothing; closing gives the parts of paused jobs their own resume setting back; with resume off a
#          job's own folder of rsync's unfinished copies goes as soon as nothing will continue from it, on this thread; one test
#          (holds_a_pause) for every place that ends a pause, also of a job resumed and still waiting; resume, retry and run now say
#          whether they acted; a pause that ends otherwise than in a
#          resume gives its parts their own resume setting back; several transfers at once, each on a thread and a login of its own
#          (FC-01): never more than the person allows, never two in the same place, and a server that takes no more logins makes a job
#          wait for another of that server to end; one question at a time, and an answer for all given meanwhile holds for the jobs of
#          its action that wait to ask; the speed limit of the queue is shared among the running jobs that follow it; closing waits for
#          every thread; what a running job writes in is never tidied by another; a new login refused in the middle of a job waits as
#          the first; a server's learnt number of logins grows again after a job that went well; the job that starts next says so;
#          further logins lent to a large file split in parts (FC-05), each taking a place of "Transfers at once" and a login of its server
# v0.0.12: The transfer options (FB-01 to FB-08): the overwrite question waits in this thread for the window's answer; tidy keeps a
#          part file that can be continued; the two ways moved into modules of their own (filebyfile.py, rsyncway.py); a move with
#          rsync is looked over first, so each source is removed only when it is unchanged; the notes of a transfer that went well;
#          tidying keeps only part files that can still be continued; time waiting for an answer is no transfer time; an item that
#          fails inside a folder fails alone and the job names it; every reason an original stayed, a size could not be checked or a
#          file started again is told; a part is kept by tidying only while its noted source is unchanged; an upload with links is
#          looked over, so rsync sends links to files as their content and never follows a link to a folder; a download with links too;
#          a broken-off append is undone before a job; after rsync could not log in the count starts again and each item fails once;
#          tidying touches only leftovers with our note; what setting back appends did is told, for one and for more; while a question
#          waits the transfer connection is kept alive, and logs in again after it where the server closed it all the same; an append
#          the server won't cut back, an old copy that stayed and a note that could not be removed are told; a part is judged by the resume
#          setting it was kept by; rsync is preceded by a look-over wherever a file and a folder would meet; an append whose end is unknown
#          is told, never cut; a part another Chrono FTP writes is kept; this computer's own problems in its words; rsync's folders of
#          unfinished copies are tidied on both sides once nothing in them can be continued; a note of a part no longer there goes; the
#          note of a move the server can't confirm says exactly what was checked; links and things that are neither file nor folder
#          left out are told; a pipe or device here is looked over too; a file agreed to be replaced is looked at again before rsync runs;
#          a download with a pipe, socket or device on the server is looked over too, and so is an upload that meets one there, and a
#          download that meets one here; an old file that changed in the last moment is named with where it was kept;
#          the other way after rsync could not log in carries each answer only for the file as the person saw it; an upload into a
#          folder that can't be read goes file by file
# v0.0.6: Leftovers of a dropped transfer are tidied; rsync stands aside for links in the target tree
# v0.0.5: Initial: rsync where it can work, else SFTP or FTP; progress, throttling, stop (D-014)

__all__ = ["AT_ONCE", "ENDED", "FEWEST_AT_ONCE", "HELD_NEXT", "HELD_PLACE", "HELD_ROOM", "LINE", "MOST_AT_ONCE", "holds_a_pause", "PART_SUFFIX", "Job", "Owned", "State", "Stopped", "TransferQueue", "tidy"]

LEFTOVER_SECONDS: int = 3600  # a temporary file untouched this long belongs to no running transfer, also not on another computer
SEEN_BEFORE_SECONDS: float = 30.0  # what is only a moment in another transfer's life is never there this long, unchanged
POLL_SECONDS: float = 0.2
KEEP_DONE_SECONDS: float = 15.0  # a job that was done stays in the list this long, unless Settings say otherwise (FC-03)
CLOSE_FIRST_SECONDS: float = 3.0  # closing waits this long for the running jobs to see their stop
CLOSE_LAST_SECONDS: float = 10.0  # and, once their connections are closed, this long for a call that hung on one
LINE: tuple[State, ...] = (State.WAITING, State.PAUSED)  # the jobs that are still to run, in the order of the list
ENDED: tuple[State, ...] = (State.DONE, State.FAILED, State.STOPPED)
KINDS_MARK: str = "//"  # between the folders and the rest in find's answer: no path find writes can be this
KEEPALIVE_SECONDS: float = 20.0  # while a question waits: under the idle limit of the servers that have the shortest
CHANGED_ASIDE_TEXT: str = "{name} changed on the server just before it was replaced, so that version was kept as {kept}, next to the new one."
DATES_TEXT: str = "The server kept its own date for {count} of these items."
NOTES: dict[str, tuple[str, str]] = {  # what a transfer that went well has to tell, for one and for more (OR-23)
    "unknown_dates": ("1 item was left as it was: its date is unknown, so it can't be told which is newer.", "{count} items were left as they were: their date is unknown, so it can't be told which is newer."),
    "kept_changed": ("1 original stayed where it was, because it changed while it was sent; the copy that arrived is whole.", "{count} originals stayed where they were, because they changed while they were sent; the copies that arrived are whole."),
    "kept_unchecked": ("1 original stayed where it was, because the copy that arrived could not be checked.", "{count} originals stayed where they were, because the copies that arrived could not be checked."),
    "kept_not_removed": ("1 original arrived whole, but could not be removed, so it stayed where it was.", "{count} originals arrived whole, but could not be removed, so they stayed where they were."),
    "sizes_unknown": ("The server could not tell the size of 1 file, so it could not be checked.", "The server could not tell the size of {count} files, so they could not be checked."),
    "appeared": ("1 item appeared on the other side meanwhile and was left as it is; transfer it again to be asked about it.", "{count} items appeared on the other side meanwhile and were left as they are; transfer them again to be asked about them."),
    "restarted": ("1 file started again from the beginning, because its unfinished copy could not be continued.", "{count} files started again from the beginning, because their unfinished copies could not be continued."),
    "removed_unflushed": ("1 original was removed once its copy had arrived at its size and the original had not changed; this server can't confirm the copy is on its disk.", "{count} originals were removed once their copies had arrived at their sizes and the originals had not changed; this server can't confirm the copies are on its disk."),
    "parts_left": ("1 unfinished copy from an earlier try could not be removed; it is noted as Chrono FTP's own and goes when its folder is tidied.", "{count} unfinished copies from earlier tries could not be removed; they are noted as Chrono FTP's own and go when their folders are tidied."),
    "kept_aside": ("1 file was replaced, but its old copy could not be removed and stays next to it, named with .chrono-ftp-old at the end.", "{count} files were replaced, but their old copies could not be removed and stay next to them, named with .chrono-ftp-old at the end."),
    "notes_left": ("1 note on this computer about a finished append could not be removed; it does no harm and goes the next time it is looked at.", "{count} notes on this computer about finished appends could not be removed; they do no harm and go the next time they are looked at."),
    "links_left": ("1 link to a folder, or that leads nowhere, was left out: links are not followed into other folders.", "{count} links to folders, or that lead nowhere, were left out: links are not followed into other folders."),
    "others_left": ("1 item that is neither a file nor a folder (a pipe, a socket or a device, for instance) was left out.", "{count} items that are neither a file nor a folder (pipes, sockets or devices, for instance) were left out."),
    "continued_by_size": ("1 file was continued where it broke off; this server can confirm only its size, and that is right.", "{count} files were continued where they broke off; this server can confirm only their sizes, and those are right."),
    "left_by_choice": ("1 item was left out, as the transfer settings ask.", "{count} items were left out, as the transfer settings ask."),
}
APPEND_NOTES: dict[str, tuple[str, str]] = {  # what setting back the appends that broke off earlier has to tell (OR-23)
    "whole": ("1 file that an append broke off in has the append's full length, so it was left as it is.", "{count} files that an append broke off in have the append's full length, so they were left as they are."),
    "maybe part": ("1 file that an append broke off in may hold part of it; as nothing tells whether it was written to since, it was left as it is.", "{count} files that an append broke off in may hold part of it; as nothing tells whether they were written to since, they were left as they are."),
    "set back": ("1 file that an append left half done has its old length back.", "{count} files that an append left half done have their old length back."),
    "refused": ("1 file that an append left half done could not be cut back by the server, so it may still hold part of that append.", "{count} files that an append left half done could not be cut back by the server, so they may still hold part of that append."),
    "left": ("1 file that an append left half done was changed since by something else, so it was left as it is; it may still hold part of that append.", "{count} files that an append left half done were changed since by something else, so they were left as they are; they may still hold part of that append."),
}

Asking = Callable[[Job, Conflict, Callable[[Answer], None]], None]  # hands a question to the window; the answer may come from any thread
Finished = Callable[[list[RemoteConnection], int, bool], None]  # the queue has nothing left to do: the connections it used, how many
# jobs failed, and whether it ended by itself (False: the person stopped or deleted what was still to run)


class NoRoom(Exception):
    """The server took no login for this job while other jobs of it hold one each: it waits until one of them ended."""


def _settled(seen: dict[str, tuple[float, int]], path: str, size: int, moment: float) -> bool:
    """True when this file was seen earlier, unchanged. Its date can't tell: a file moved aside keeps its old date, and the clock of a
    server is not ours. So a transfer running in another tab or on another computer is never touched."""
    first, known = seen.get(path, (moment, -1))
    if known != size:
        seen[path] = (moment, size)
        return False
    return moment - first >= SEEN_BEFORE_SECONDS


def continuable(login: Connection, path: str, size: int, options: TransferOptions) -> bool:
    """Whether a part file on the server can still be continued with these options (FB-02): it has a note, the source the note names is
    on this computer with exactly the size and date noted, some of the part's first bytes are confirmed, and the resume rule holds for a
    source of that size."""
    note = parts.lookup(parts.upload_key(login, path))
    if note is not None and parts.in_use(note):
        return True  # another Chrono FTP writes it now
    if note is None or min(size, note.part_size) <= 0 or not resumable(replace(options, resume=note.resume) if note.resume else options, note.size):
        return False
    try:
        where: object = json.loads(note.source)
        details = os.stat(cast(list[str], where)[1]) if isinstance(where, list) and len(cast(list[object], where)) == 2 and isinstance(cast(list[object], where)[1], str) else None
    except (OSError, ValueError):
        return False
    return details is not None and (details.st_size, details.st_mtime) == (note.size, note.modified)


class Owned:
    """Which leftovers of one login this program made itself, and so may tidy: a part file with its note, a file moved aside with its
    note. Anything else that only carries one of our suffixes may be a real file of someone: it is never touched."""

    def __init__(self, login: Connection, options: TransferOptions, spared: frozenset[str] = frozenset()) -> None:
        self.login: Connection = login
        self.options: TransferOptions = options
        self.spared: frozenset[str] = spared  # rsync's folders of unfinished copies that a job in the queue goes on from (spared_partials)

    def part(self, path: str) -> bool:
        return parts.lookup(parts.upload_key(self.login, path)) is not None

    def keep(self, path: str, size: int) -> bool:
        return continuable(self.login, path, size, self.options)  # the next transfer of that file continues from it (FB-02)

    def old(self, path: str, size: int) -> str | None:
        return parts.old_target(self.login, path, size)

    def partial(self, folder: str) -> parts.PartialNote | None:
        """The rsync job whose folder of unfinished copies in this server folder is ours, if any."""
        return parts.partial_note(self.login, parts.UP, folder)

    def removed(self, path: str) -> None:
        parts.forget(parts.upload_key(self.login, path))
        parts.forget_old(self.login, path)


def tidy(connection: RemoteConnection, folder: str, entries: list[RemoteEntry], seen: dict[str, tuple[float, int]], owned: Owned) -> list[RemoteEntry]:
    """What a dropped connection left behind in this folder, of what this program made itself (owned). An old file with nothing under its
    real name is the only copy, so it gets its name back; next to a new file it has served its purpose. A part file the next transfer of
    its file can continue stays (FB-02); any other goes once nothing can be writing it. A part file is never the only copy of anything.
    seen remembers the leftovers from one look at a folder to the next; what can't be tidied stays as it is."""
    present = {entry.name: entry for entry in entries}
    moment = time.time()
    tidied = False
    for path in parts.notes_in(owned.login, folder):  # a note of a part that is no longer there names nothing: it goes
        note = parts.lookup(parts.upload_key(owned.login, path))
        if path.rsplit("/", 1)[-1] not in present and note is not None and not parts.in_use(note):
            parts.forget(parts.upload_key(owned.login, path))
    for entry in entries:
        if entry.is_folder and not entry.is_link and is_partial_folder(entry.name):
            tidied = _tidy_partial_there(connection, folder, entry, owned, moment) or tidied
            continue
        if entry.is_folder or entry.is_link or not entry.name.endswith((OLD_SUFFIX, PART_SUFFIX)):
            continue
        path = joined(folder, entry.name)
        if not _settled(seen, path, entry.size, moment):
            continue
        try:
            if entry.name.endswith(OLD_SUFFIX):
                real = owned.old(path, entry.size if entry.size_known else -1)
                if real is None or parent_of(real) != folder:
                    continue  # not ours: someone's own file that only carries the suffix
                name = real.rsplit("/", 1)[-1]
                if name not in present:
                    connection.rename(path, real)
                elif not present[name].is_folder:
                    connection.delete(path, False)
                else:
                    continue
                owned.removed(path)
                tidied = True
            elif entry.modified is not None and moment - entry.modified > LEFTOVER_SECONDS and owned.part(path) and not owned.keep(path, entry.size):
                connection.delete(path, False)
                owned.removed(path)  # its note goes with it
                tidied = True
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            continue
    return connection.list_folder(folder) if tidied else entries


def _has_a_link(folder: Path, stop: threading.Event) -> bool:
    for place, folders, files in os.walk(folder):
        if stop.is_set():
            raise Stopped
        if any(os.path.islink(os.path.join(place, name)) for name in (*folders, *files)):
            return True
    return False


def source_check_command(path: str) -> str:
    """Finds a link, or something that is neither a file nor a folder (a pipe, a socket or a device), anywhere in path on the server: then
    a download is looked over first, which never fetches such a thing and never makes one here."""
    return f"find {shlex.quote(path)} \\( -type l -o -type p -o -type s -o -type c -o -type b \\) -print -quit"


def target_check_command(path: str) -> str:
    """Finds a pipe, a socket or a device anywhere in path on the server: then an upload is looked over first, which never replaces one."""
    return f"find {shlex.quote(path)} \\( -type p -o -type s -o -type c -o -type b \\) -print -quit"


def _has_a_link_or_other(folder: Path, stop: threading.Event) -> bool:
    """A link, or something that is neither a file nor a folder (a pipe, a device), anywhere in folder."""
    for place, folders, files in os.walk(folder):
        if stop.is_set():
            raise Stopped
        for name in (*folders, *files):
            path = os.path.join(place, name)
            if os.path.islink(path) or not (os.path.isfile(path) or os.path.isdir(path)):
                return True
    return False


def _has_a_special(top: Path, stop: threading.Event) -> bool:
    """Something that is neither a file nor a folder (a pipe, a socket or a device), also through a link, at top or anywhere below it."""
    if not os.path.lexists(top):
        return False
    if not top.is_dir():
        return not top.is_file() and top.exists()
    for place, folders, files in os.walk(top):
        if stop.is_set():
            raise Stopped
        for name in (*folders, *files):
            path = os.path.join(place, name)
            if os.path.exists(path) and not (os.path.isfile(path) or os.path.isdir(path)):
                return True
    return False


def _local_kinds(top: Path, stop: threading.Event) -> dict[str, bool]:
    """Every path below top, relative to it, and whether it is a folder (a link is not: rsync sends it as what it is)."""
    found: dict[str, bool] = {}
    for place, folders, files in os.walk(top):
        if stop.is_set():
            raise Stopped
        relative = os.path.relpath(place, top)
        for name in folders:
            path = os.path.normpath(os.path.join(relative, name))
            found[path] = not os.path.islink(os.path.join(place, name))
        for name in files:
            found[os.path.normpath(os.path.join(relative, name))] = False
    return found


def _server_kinds(job: Job, top: str) -> dict[str, bool] | None:
    """The same for a server folder, from one find there; None where the server can't tell."""
    if not job.remote.capabilities.has("find"):
        return None
    try:
        result = job.remote.run(f"cd {shlex.quote(top)} && find . -mindepth 1 -type d -print0 && printf '%s\\0' {KINDS_MARK} && find . -mindepth 1 ! -type d -print0")
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        return None
    items = result.output.split("\0")
    if result.status != 0 or KINDS_MARK not in items:
        return None
    middle = items.index(KINDS_MARK)  # every path find writes starts with ./, so none is the mark
    found = {os.path.normpath(name): True for name in items[:middle] if name}
    found.update({os.path.normpath(name): False for name in items[middle + 1:] if name})
    return found


def _tidy_partial_there(connection: RemoteConnection, folder: str, entry: RemoteEntry, owned: Owned, moment: float) -> bool:
    """rsync's folder of unfinished copies in a server folder: ours by the note of the job that wrote below it. It goes once nothing in
    it can be continued (resume off, or its source here gone) and nothing has written to it for long."""
    if entry.name in owned.spared:
        return False  # a paused job goes on from it, however long it waits
    note = owned.partial(folder)
    if note is None or entry.modified is None or moment - entry.modified <= LEFTOVER_SECONDS:
        return False
    path = joined(folder, entry.name)
    try:
        inside = connection.list_folder(path)
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        return False
    for item in inside:
        relative = os.path.relpath(joined(folder, item.name), note.base)
        if note.resume != RESUME_OFF and entry.name == PARTIAL_FOLDER and os.path.isfile(Path(note.source_base) / relative):
            return False  # the next rsync of that file continues from it
    try:
        connection.delete(path, True)
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        return False
    return True


def holds_a_pause(job: Job) -> bool:
    """Whether the job holds what a pause kept (part notes marked to be continued, rsync's own folder of unfinished copies): paused, or
    resumed and not yet run to its end. The one test for every place that ends a pause (stop, delete, close)."""
    if job.state == State.PAUSED:
        return True
    return job.state in (State.WAITING, State.RUNNING) and job.resuming and (bool(job.paused_parts) or bool(job.partial_token))


def spared_partials(jobs: list[Job]) -> frozenset[str]:
    """The folders of unfinished copies with a name of their own (resume off) that a job in the queue writes in now or goes on from: one
    that runs, or whose last run ended in a pause. However long it is paused, tidying never takes them, nor one another job writes in."""
    return frozenset(partial_folder_of(job) for job in jobs if job.partial_token and (job.resuming or job.state == State.RUNNING))


def tidy_partials_here(job: Job, spared: frozenset[str] = frozenset()) -> None:
    """rsync's folders of unfinished copies here, where a download job writes (its folder, and below what it fetches): ours by the note
    of the job that wrote them. Each goes once nothing in it can be continued (resume off, or its source on the server gone) and nothing
    has written to it for long."""
    note = parts.partial_note(job.connection, parts.DOWN, job.local)
    if note is None:
        return
    places = [job.local]
    top = Path(job.local, job.name)
    if top.is_dir() and not top.is_symlink():
        for place, folders, _files in os.walk(top):
            if job.stop_asked.is_set():
                raise Stopped
            found = [name for name in folders if is_partial_folder(name)]
            for name in found:
                folders.remove(name)
            if found:
                places.append(place)
    moment = time.time()
    own = partial_folder_of(job) if job.resuming else ""  # what a paused run of this very job kept: it goes on from it now
    for place in places:
        try:
            inside = sorted(os.listdir(place))
        except OSError:
            continue
        for name in inside:
            if is_partial_folder(name) and name != own and name not in spared:
                _tidy_partial_here(job, note, Path(place, name), moment)


def _tidy_partial_here(job: Job, note: parts.PartialNote, partial: Path, moment: float) -> None:
    """One folder of unfinished copies here: it goes once nothing has written to it for long and nothing in it can be continued (one of
    a job with resume off never is)."""
    place = str(partial.parent)
    try:
        if not partial.is_dir() or partial.is_symlink() or moment - partial.stat().st_mtime <= LEFTOVER_SECONDS:
            return
        names = os.listdir(partial)
    except OSError:
        return
    if note.resume != RESUME_OFF and partial.name == PARTIAL_FOLDER and any(job.remote.details(joined(note.source_base, os.path.relpath(os.path.join(place, name), note.base))) is not None for name in names):
        return  # the next rsync of that file continues from it
    shutil.rmtree(partial, ignore_errors=True)


def _summed(first: dict[str, int], second: dict[str, int]) -> dict[str, int]:
    return {name: first.get(name, 0) + second.get(name, 0) for name in {*first, *second}}


def note_text(name: str, count: int, table: dict[str, tuple[str, str]] = NOTES) -> str:
    one, many = table[name]
    return one if count == 1 else many.format(count=count)


class TransferQueue:
    """The jobs start in the order of the list, as many at once as at_once allows (FC-01), each on a thread of its own; this queue's own
    thread decides which job starts, never one that works where a running one does (schedule.in_the_way), nor one more on a server that
    took no more logins (its room, learnt from its refusals). Every call from the window (add, pause, resume, move, run now, retry, delete, the
    limits, clear finished) may come from any thread. The calls to the window: changed (a job changed), removed (a job left the list),
    finished (the queue has nothing left to do, see _idle), told (something to show in passing) and ask (the overwrite question, one at
    a time; without it nothing that is there already is replaced). They come from the queue's threads, never once close() returned.
    remote_shell: what rsync starts instead of ssh, for the tests."""

    def __init__(self, changed: Callable[[Job], None], program: str = "rsync", ask: Asking | None = None, remote_shell: list[str] | None = None, told: Callable[[Job, str], None] | None = None, removed: Callable[[Job], None] | None = None, finished: Finished | None = None) -> None:
        self.changed: Callable[[Job], None] = changed
        self.told: Callable[[Job, str], None] | None = told
        self.removed: Callable[[Job], None] | None = removed
        self.finished: Finished | None = finished
        self.limit: int = 0  # KB per second for every job without a limit of its own, 0 for none; a change takes effect at once
        self.program: str = program
        self.ask: Asking | None = ask
        self.remote_shell: list[str] | None = remote_shell
        self.start_by_themselves: bool = True  # off: an added job is paused until the person starts it
        self.keep_done_seconds: float = KEEP_DONE_SECONDS  # a job that was done leaves the list this long after
        self.pause_on_error: bool = False  # on: a failed job pauses every job still waiting, instead of going on with the next
        self.at_once: int = FEWEST_AT_ONCE  # how many jobs run at the same time; the window sets the one of Settings
        self._jobs: list[Job] = []
        self._lock: threading.RLock = threading.RLock()  # the list and the states the person changes
        self._wake: threading.Condition = threading.Condition(self._lock)
        self._gate: threading.RLock = threading.RLock()  # a call to the window ends before close() returns, and none comes after
        self._closed: bool = False
        self._running: list[Job] = []  # the jobs whose thread runs now, in the order they started
        self._workers: list[threading.Thread] = []  # the threads of the running jobs, each of one job
        self._turn: threading.Lock = threading.Lock()  # one question at a time: the others wait for their turn
        self._room: dict[Server, int] = {}  # the logins a server took at most while it refused one more: never more at once, until a job
        # of it ends well, which lets one more try
        self._after_an_end: dict[int, tuple[Server, int]] = {}  # by id, the jobs that got no login, with the round they tried in: they
        # try again once a job of that server ended, or nothing of it runs any more
        self._rounds: dict[Server, int] = {}  # per server, how often the jobs that got no login were let in again
        self._tried_in: dict[int, int] = {}  # by id of each running job, the round of its server it started in
        self._lent: dict[int, list[RemoteConnection]] = {}  # by id of a running job, the further logins of its split file (FC-05)
        self._lending: dict[int, int] = {}  # by id, the further logins being made for it now: their places are taken already
        self._used: list[RemoteConnection] = []  # the connections the jobs ran over since the queue last had nothing to do
        self._failed_since_idle: int = 0
        self._cleanups: list[Job] = []  # paused jobs whose pause ended without a resume: their own unfinished copies go, on this thread
        self._left: bool = False  # a job left the line (ran, or was stopped or deleted) since the queue last had nothing to do
        self._last_by_itself: bool = False  # the last job to leave the line ended by itself (done or failed), not by the person
        self._talking: dict[int, RemoteConnection] = {}  # by id of each running job, the connection it transfers over: kept alive while
        # a question waits, closed when closing finds the job hanging on it
        self._thread: threading.Thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # --- what the window asks for

    @property
    def jobs(self) -> list[Job]:
        """The jobs in the list, in their order: a copy, so it can be read while this thread changes the list."""
        with self._lock:
            return list(self._jobs)

    def has(self, job: Job) -> bool:
        with self._lock:
            return any(item is job for item in self._jobs)

    def add(self, job: Job, by_itself: bool = False) -> None:
        """by_itself: it starts in its turn also when items do not start by themselves (a save from Edit)."""
        with self._lock:
            if self._closed:
                return
            if not (self.start_by_themselves or by_itself):
                job.state = State.PAUSED  # it waits until the person starts it
            self._jobs.append(job)
            self._wake.notify_all()
        self._report(job)

    def stop(self, job: Job) -> None:
        """A waiting or paused job never runs; a running one ends as soon as it can, and what arrived is kept only where resume says so.
        A stop ends a pause: a retry then goes on only where the resume setting allows it."""
        with self._lock:
            if job.state in LINE and self.has(job):
                self._left, self._last_by_itself = True, False  # it left the line by the person's hand
            paused = holds_a_pause(job) and job.state != State.RUNNING  # a running one ends its pause as its run ends
            job.after_stop = State.STOPPED
            job.resuming = False
            job.stop_asked.set()
            if job.state in (State.WAITING, State.PAUSED):
                job.state = State.STOPPED
            self._wake.notify_all()
        if paused:
            end_pause_notes(job)  # the pause ended without a resume
            self._clean_up(job)
        if job.state == State.STOPPED:
            self._report(job)

    def pause(self, job: Job) -> None:
        """A waiting job is held; a running one ends as soon as it can and keeps what arrived of the file on its way, so a resume continues
        from there (D-044)."""
        with self._lock:
            if job.state == State.WAITING:
                job.state = State.PAUSED
            elif job.state == State.RUNNING and not job.stop_asked.is_set():  # never once a stop, a pause or a delete is on its way
                job.after_stop = State.PAUSED
                job.stop_asked.set()
            elif job.state == State.RUNNING and job.after_stop == State.WAITING:
                job.after_stop = State.PAUSED  # paused again before a resume landed: it ends paused after all
            else:
                return
            self._wake.notify_all()
        self._report(job)

    def resume(self, job: Job) -> bool:
        """A paused job waits for its turn again; one that is being paused runs on as soon as the pause has landed. Whether it acted."""
        with self._lock:
            if job.state == State.PAUSED:
                job.state = State.WAITING
                job.after_stop = State.STOPPED
                job.stop_asked.clear()
            elif job.state == State.RUNNING and job.after_stop == State.PAUSED:
                job.after_stop = State.WAITING
            else:
                return False
            self._wake.notify_all()
        self._report(job)
        return True

    def retry(self, job: Job) -> bool:
        """A failed or stopped job waits for its turn again, keeping its place. It continues where it can: from what arrived (D-044), and
        without asking again about the files it was told about already, each answer holding only for the file as it was shown."""
        with self._lock:
            if job.state not in (State.FAILED, State.STOPPED) or not self.has(job):
                return False
            job.state, job.message, job.percent, job.done, job.total, job.seconds_left, job.way = State.WAITING, "", 0, 0, 0, None, ""
            job.after_stop = State.STOPPED
            job.stop_asked.clear()
            self._wake.notify_all()
        self._report(job)
        return True

    def run_now(self, job: Job) -> bool:
        """To the front of the jobs that wait: it is the next to start, as soon as there is room, and never while a running job works in
        the same place (FC-01). Whether it acted."""
        with self._lock:
            if job.state not in LINE or not self.has(job):
                return False
            self._jobs.pop(self._index(job))
            first = next((index for index, item in enumerate(self._jobs) if item.state in LINE), len(self._jobs))
            self._jobs.insert(first, job)
            job.state = State.WAITING
            job.after_stop = State.STOPPED
            job.stop_asked.clear()
            self._wake.notify_all()
        self._report(job)
        return True

    def move(self, job: Job, step: int) -> bool:
        """One place up (step -1) or down (step 1) among the jobs that wait or are paused; False where there is no such place."""
        return self.move_together([job], step)

    def can_move(self, job: Job, step: int) -> bool:
        return self.can_move_together([job], step)

    def move_together(self, jobs: list[Job], step: int) -> bool:
        """The chosen jobs one place up (step -1) or down (step 1) among the jobs that wait or are paused, each past one job that is not
        chosen. Their order among themselves always stays; a block at the top (or the bottom) stays where it is, and so does every chosen
        job right behind it. False where none could move."""
        with self._lock:
            moves = self._moves(jobs, step)
            for job, other in moves:
                first, second = self._index(job), self._index(other)
                self._jobs[first], self._jobs[second] = other, job
            return bool(moves)

    def can_move_together(self, jobs: list[Job], step: int) -> bool:
        with self._lock:
            return bool(self._moves(jobs, step))

    def _moves(self, jobs: list[Job], step: int) -> list[tuple[Job, Job]]:
        """Which chosen job changes places with which job that is not chosen, edge first."""
        line = [item for item in self._jobs if item.state in LINE]
        chosen = [index for index, item in enumerate(line) if any(item is job for job in jobs)]
        if step > 0:
            chosen.reverse()
        stuck: set[int] = set()
        moves: list[tuple[Job, Job]] = []
        for index in chosen:
            target = index + step
            if not 0 <= target < len(line) or target in stuck:
                stuck.add(index)
                continue
            moves.append((line[index], line[target]))
            line[index], line[target] = line[target], line[index]
        return moves

    def delete(self, job: Job) -> None:
        """Out of the list. A running job is stopped first and leaves once it has stopped; what arrived of its file stays only where it can
        be continued (D-044)."""
        with self._lock:
            if not self.has(job):
                return
            if job.state in LINE:
                self._left, self._last_by_itself = True, False  # it left the line by the person's hand
            paused = holds_a_pause(job) and job.state != State.RUNNING  # a running one ends its pause as its run ends
            job.delete_asked = True
            self._after_an_end.pop(id(job), None)
            job.after_stop = State.STOPPED
            job.resuming = False
            job.stop_asked.set()
            running = job.state == State.RUNNING
            if not running:
                if job.state in LINE:
                    job.state = State.STOPPED  # it never ran to its end
                self._jobs.pop(self._index(job))
            self._wake.notify_all()
        if paused:
            end_pause_notes(job)  # a pause it had ends with it
            self._clean_up(job)
        if running:
            self._report(job)  # it leaves once it has stopped
        else:
            self._report_removed(job)

    def set_limit(self, job: Job, limit: int | None) -> None:
        """The speed limit of this one job in KB per second (0: none; None: the queue's), at once, also while it runs."""
        with self._lock:
            job.limit = None if limit is None else max(0, limit)
        self._report(job)

    def limit_of(self, job: Job) -> int:
        """Its own limit, or else its share of the queue's: the queue's limit is for all its running jobs that follow it together."""
        own = job.limit
        if own is not None:
            return own
        with self._lock:
            following = sum(1 for item in self._running if item.limit is None)
        return share(self.limit, following)

    def pause_all(self) -> None:
        for job in self.jobs:
            if job.state in (State.WAITING, State.RUNNING):
                self.pause(job)

    def resume_all(self) -> None:
        for job in self.jobs:
            self.resume(job)

    def clear_finished(self) -> None:
        """Every job that ended (done, failed or stopped) leaves the list."""
        for job in self.jobs:
            if job.state in ENDED and not job.delete_asked:
                self.delete(job)

    def settings(self, start_by_themselves: bool, keep_done_seconds: float, pause_on_error: bool, at_once: int | None = None) -> None:
        """at_once: how many jobs run at the same time (None: as it is). Fewer lets the running ones end, it never stops one; more starts
        waiting jobs at once."""
        with self._lock:
            self.start_by_themselves = start_by_themselves
            self.keep_done_seconds = max(0.0, keep_done_seconds)
            self.pause_on_error = pause_on_error
            if at_once is not None:
                self.at_once = min(MOST_AT_ONCE, max(FEWEST_AT_ONCE, at_once))
            self._wake.notify_all()

    def close(self) -> None:
        """No call reaches the window any more once this returns: every job is stopped (a question still open ends with it), and the
        threads have ended. Where a call to a server hangs, the connection it runs over is closed, which ends it; the connections have
        timeouts of their own, so closing never waits without end."""
        with self._gate:
            self._closed = True
        with self._lock:
            for job in self._jobs:
                job.after_stop = State.PAUSED if job.continue_later else State.STOPPED  # a pause on its way keeps what arrived
                job.stop_asked.set()
            self._wake.notify_all()
        self._join(CLOSE_FIRST_SECONDS)
        with self._lock:
            hanging = [thread for thread in self._workers if thread.is_alive()]
            talking = [*self._talking.values(), *(connection for lent in self._lent.values() for connection in lent)]
        closed: list[RemoteConnection] = []
        for connection in talking if hanging else []:
            if any(connection is other for other in closed):
                continue  # one connection several jobs share (SSH) is closed once
            closed.append(connection)
            try:
                connection.close()  # a call that hangs on it ends now
            except Exception:  # closing a connection that is gone already: nothing more to end
                pass
        self._join(CLOSE_LAST_SECONDS)
        for job in self.jobs:
            if holds_a_pause(job) or job.continue_later:
                end_pause_notes(job)  # the queue does not outlive the program: what a pause kept is a part as any other from now on

    def _join(self, seconds: float) -> None:
        """Waits at most this long in all for this queue's thread and the thread of every job."""
        deadline = time.monotonic() + seconds
        with self._lock:
            threads = [self._thread, *self._workers]
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

    # --- the calls to the window

    def _report(self, job: Job) -> None:
        with self._gate:
            if not self._closed:
                self.changed(job)

    def _report_removed(self, job: Job) -> None:
        with self._gate:
            if not self._closed and self.removed is not None:
                self.removed(job)

    def _tell(self, job: Job, text: str) -> None:
        with self._gate:
            if not self._closed and self.told is not None:
                self.told(job, text)

    # --- this queue's thread

    def _index(self, job: Job) -> int:
        return next(index for index, item in enumerate(self._jobs) if item is job)

    def _clean_up(self, job: Job) -> None:
        """What a pause kept on the server or here goes on this queue's thread, never on the caller's (the window's)."""
        with self._lock:
            self._cleanups.append(job)
            self._wake.notify_all()

    def _run(self) -> None:
        """This queue's own thread: it starts the jobs that can run, tidies what paused jobs kept, lets done jobs leave the list and tells
        when the queue is done. What a paused job kept goes before anything starts again, so a job retried meanwhile never meets it."""
        while True:
            started: list[Job] = []
            held: list[Job] = []
            with self._lock:
                closing = self._closed
                if closing:  # the queue does not outlive the program: what paused jobs kept goes now, as nothing goes on from it
                    while self._running:
                        self._wake.wait()  # what a running job's pause kept is known once it ended; close() ends the ones that hang
                    cleanups = [*self._cleanups, *(item for item in self._jobs if holds_a_pause(item))]
                    self._cleanups = []
                    gone, ended = list[Job](), None
                else:
                    cleanups, self._cleanups = self._cleanups, list[Job]()
                    gone = list[Job]() if cleanups else self._expired()
                    if not cleanups and not gone:
                        started, held = self._start_what_can()
                    ended = None if cleanups or gone or started else self._idle()
                    if not cleanups and not gone and not started and not held and ended is None:
                        self._wake.wait(self._seconds_to_expiry())
                        continue
            for item in cleanups:
                drop_own_partials(item)
            if closing:
                return
            for item in gone:
                self._report_removed(item)
            for item in held:
                self._report(item)
            if ended is not None:
                with self._gate:
                    if not self._closed and self.finished is not None:
                        self.finished(*ended)

    def _start_what_can(self) -> tuple[list[Job], list[Job]]:
        """Under the lock: every waiting job, in the order of the list, that may run now starts; one that waits for a reason says why.
        The jobs started, and the ones whose reason to wait changed."""
        started: list[Job] = []
        changed: list[Job] = []
        before: list[Job] = []  # the jobs earlier in the list that still wait: a later one never overtakes one on the same place
        first = True  # the first that only waits for its turn starts next (also one Run Now put first): it says so
        for job in self._jobs:
            if job.state != State.WAITING or any(job is item for item in self._running):
                continue  # one whose last run is still ending (a pause taken back, no room) starts once that thread has let go of it
            reason = self._why_it_waits(job, before)
            if not reason and self._places_taken() < self.at_once:
                job.held = ""
                self._begin(job)
                started.append(job)
                continue
            if not reason and first:
                reason, first = HELD_NEXT, False
            if reason != job.held:
                job.held = reason
                changed.append(job)
            if job.state == State.WAITING:
                before.append(job)
        return started, changed

    def _extra_of(self, job: Job) -> int:
        """Under the lock: the further logins a running job holds or is making for its split file."""
        return len(self._lent.get(id(job), [])) + self._lending.get(id(job), 0)

    def _places_taken(self) -> int:
        """Under the lock: the places of "Transfers at once" in use: each running job, and each further part of a split file."""
        return len(self._running) + sum(self._extra_of(job) for job in self._running)

    def _logins_on(self, server: Server) -> int:
        """Under the lock: the logins the running jobs hold on this server, their further parts included."""
        return sum(1 + self._extra_of(job) for job in self._running if server_of(job.connection) == server)

    def lend(self, job: Job) -> RemoteConnection | None:
        """A further login for a part of the job's split file, where a place of "Transfers at once" is free and the server takes one
        more; None otherwise. A refusal of the server is no failure: the file goes in fewer parts, and the queue keeps to it."""
        server = server_of(job.connection)
        with self._lock:
            if self._closed or job.stop_asked.is_set() or self._places_taken() >= self.at_once:
                return None
            room = self._room.get(server)
            if room is not None and self._logins_on(server) >= room:
                return None
            self._lending[id(job)] = self._lending.get(id(job), 0) + 1
        made: RemoteConnection | None = None
        try:
            made = job.remote.another_login()
        except ConnectionFailed as error:
            if isinstance(error, (NoMoreLogins, LoginRefused)):
                with self._lock:
                    room = self._logins_on(server) - 1  # the ones it holds now, without the one that was refused
                    self._room[server] = max(1, min(room, self._room.get(server, room)))
        except (ChronoFtpError, OSError):
            pass  # no further part: the file goes in fewer
        with self._lock:
            self._lending[id(job)] -= 1
            if made is not None:
                self._lent.setdefault(id(job), []).append(made)
            self._wake.notify_all()
        return made

    def give_back(self, job: Job, connection: RemoteConnection, again: bool = False) -> RemoteConnection | None:
        """A further login a split file no longer needs: closed, and its place is free again. again: its line dropped, and a new one takes
        its place where the server gives one (None: the place is free)."""
        try:
            connection.close()
        except Exception:  # gone already: nothing more to end
            pass
        made: RemoteConnection | None = None
        if again and not job.stop_asked.is_set():
            try:
                made = job.remote.another_login()
            except (ChronoFtpError, OSError):
                made = None
        with self._lock:
            lent = self._lent.get(id(job), [])
            if any(item is connection for item in lent):
                lent.remove(connection)
            if made is not None:
                lent.append(made)
            if not lent:
                self._lent.pop(id(job), None)
            self._wake.notify_all()
        return made

    def _why_it_waits(self, job: Job, before: list[Job]) -> str:
        """Under the lock: HELD_PLACE where a running job, or one before it in the list that still waits, works in the same place;
        HELD_ROOM where its server takes no more logins now; empty where it may run as soon as it is its turn."""
        if in_the_way(job, [*self._running, *before]) is not None:
            return HELD_PLACE
        server = server_of(job.connection)
        if id(job) in self._after_an_end:
            return HELD_ROOM
        room = self._room.get(server)
        if room is not None and self._logins_on(server) >= room:
            return HELD_ROOM
        return ""

    def _begin(self, job: Job) -> None:
        """Under the lock: the job runs, on a thread of its own."""
        job.state = State.RUNNING
        job.after_stop = State.STOPPED
        job.stop_asked.clear()
        self._running.append(job)
        if not any(remote is job.remote for remote in self._used):
            self._used.append(job.remote)
        self._tried_in[id(job)] = self._rounds.get(server_of(job.connection), 0)
        thread = threading.Thread(target=self._work, args=(job,), daemon=True)
        self._workers = [item for item in self._workers if item.is_alive()]
        self._workers.append(thread)
        thread.start()

    def _work(self, job: Job) -> None:
        """The thread of one job: it runs, and then makes room for the next. It leaves the running jobs only once everything it tells the
        window is told, so the queue is never done before the window heard how its last job ended."""
        try:
            self._run_one(job)
        finally:
            with self._lock:
                self._running = [item for item in self._running if item is not job]
                self._talking.pop(id(job), None)
                server = server_of(job.connection)
                ended = id(job) not in self._after_an_end  # a run that ended frees a login: the jobs that got none try again;
                left = any(server_of(item.connection) == server for item in self._running)
                if ended or not left:  # and so they do when nothing of that server runs any more (its room lets them in one by one)
                    if any(other == server for other, _round in self._after_an_end.values()):
                        self._rounds[server] = self._rounds.get(server, 0) + 1
                    self._after_an_end = {number: (other, tried) for number, (other, tried) in self._after_an_end.items() if other != server}
                self._tried_in.pop(id(job), None)
                if job.state == State.DONE and server in self._room:  # it went well: the server may take one more again, which is tried;
                    self._room[server] += 1  # a refusal lowers it once more, calmly. A refusal of a moment never lasts the session
                    if self._room[server] >= self.at_once:
                        del self._room[server]
                self._wake.notify_all()

    def _expired(self) -> list[Job]:
        """The jobs that were done long enough ago: they leave the list now."""
        moment = time.monotonic()
        gone = [job for job in self._jobs if job.state == State.DONE and job.finished_at is not None and moment - job.finished_at >= self.keep_done_seconds and not any(job is item for item in self._running)]
        self._jobs = [job for job in self._jobs if not any(job is item for item in gone)]
        return gone

    def _seconds_to_expiry(self) -> float | None:
        moments = [job.finished_at + self.keep_done_seconds - time.monotonic() for job in self._jobs if job.state == State.DONE and job.finished_at is not None and not any(job is item for item in self._running)]
        return max(0.0, min(moments)) if moments else None

    def _idle(self) -> tuple[list[RemoteConnection], int, bool] | None:
        """The queue just came to have nothing waiting, running or paused: the connections it used meanwhile, how many jobs failed (a
        failed job counts as ended), and whether it ended by itself: whether the last job to leave was done or failed, rather than
        stopped or deleted by the person. None when nothing left it since the last time."""
        if self._running or any(job.state in (*LINE, State.RUNNING) for job in self._jobs):
            return None
        if not self._left:
            return None  # nothing left the line since the last time: nothing happened
        ended = (self._used, self._failed_since_idle, self._last_by_itself)
        self._used, self._failed_since_idle, self._left = list[RemoteConnection](), 0, False
        return ended

    def _run_one(self, job: Job) -> None:
        self._report(job)
        state, message = State.DONE, ""
        no_room = False  # the server took no login for it: it waits for its turn again, as if it never ran
        try:
            if isinstance(job.side, SideJob):
                self._side_work(job, job.side)  # a delete, a move, a date set or a copy on this computer (FF-10)
            else:
                self._transfer(job)
        except Stopped:
            state = State.STOPPED
        except NoRoom:
            no_room = True
            state = State.STOPPED if job.stop_asked.is_set() else State.WAITING
        except ChronoFtpError as error:
            state, message = State.FAILED, str(error)
        except OSError as error:  # this computer's own problem, in its words, never a bare system message
            state, message = State.FAILED, (LOCAL_READ_TEXT if job.upload else LOCAL_WRITE_TEXT).format(reason=error.strerror or str(error))
        except Exception as error:  # one job may never end the queue of all tabs
            state, message = State.FAILED, str(error) or "The transfer did not finish."
        self._talking.pop(id(job), None)
        paused: list[Job] = []
        with self._lock:
            if state == State.STOPPED:
                state = job.after_stop  # a pause ends paused, a pause the person took back waits again; done is done
            if no_room:
                job.way, job.held = "", HELD_ROOM if state == State.WAITING else ""
            else:
                job.resuming = state in LINE  # the next run goes on from what this one kept
            if state == State.DONE:
                job.percent, job.finished_at = 100, time.monotonic()
            if state == State.FAILED:
                job.message = message
            job.state, job.seconds_left = state, None
            job.after_stop = State.STOPPED
            if state == State.WAITING:
                job.stop_asked.clear()
            if state not in LINE:
                self._left, self._last_by_itself = True, state in (State.DONE, State.FAILED)  # a stop or a delete is the person's
            if state == State.FAILED:
                self._failed_since_idle += 1
                if self.pause_on_error:
                    paused = [item for item in self._jobs if item.state == State.WAITING]
                    for item in paused:
                        item.state = State.PAUSED
            gone = job.delete_asked and self.has(job)
            if gone:
                self._jobs.pop(self._index(job))
        if state not in LINE:
            end_pause_notes(job)  # ended otherwise than in a pause: what an earlier pause kept counts as any other part again
            drop_own_partials(job)  # with resume off: nothing will continue from rsync's unfinished copies
        self._report(job)
        for item in paused:
            self._report(item)
        if gone:
            self._report_removed(job)

    def _asker(self, job: Job, meter: Meter) -> Callable[[Conflict], Answer]:
        def ask(conflict: Conflict) -> Answer:
            if self.ask is None:
                return Answer(Choice.NO)
            answered = threading.Event()
            given: list[Answer] = []

            def reply(answer: Answer) -> None:
                given.append(answer)
                answered.set()

            job.waiting_for_answer = True
            self._report(job)
            asked_at = time.monotonic()
            spoken = asked_at
            turn = False
            try:
                while not self._turn.acquire(timeout=POLL_SECONDS):  # one question at a time: this one waits for its turn
                    if job.stop_asked.is_set():
                        raise Stopped
                    spoken = self._keep_alive(job, spoken)
                turn = True
                if job.stop_asked.is_set():
                    raise Stopped  # stopped while it waited for its turn, also by Stop in another job's question
                if job.answers.batch is not None:
                    given.append(Answer(job.answers.batch))  # an answer for all came while it waited: it holds for this file too
                else:
                    with self._gate:
                        if self._closed:
                            raise Stopped  # nobody is there to answer
                        self.ask(job, conflict, reply)
                    while not answered.wait(POLL_SECONDS):
                        if job.stop_asked.is_set():
                            raise Stopped  # also when the window closed: close() stops every job
                        spoken = self._keep_alive(job, spoken)
                    if given[0].choice in BATCH_CHOICES:
                        job.answers.batch = given[0].choice  # before the next job's turn: it holds for the other jobs of the action at once
            finally:
                if turn:
                    self._turn.release()
                meter.paused(time.monotonic() - asked_at)  # the time left and the speed count only time spent transferring
                job.waiting_for_answer = False
                self._report(job)
            talking = self._talking.get(id(job))
            if talking is not None:
                talking.revive()  # a server that closed it all the same: logged in again, which needs nothing from the person
            return given[0]

        return ask

    def _keep_alive(self, job: Job, spoken: float) -> float:
        """A server closes a control connection that says nothing for long: while the person thinks, or another question is answered
        first, it hears from us now and then. When it last did."""
        talking = self._talking.get(id(job))
        if time.monotonic() - spoken < KEEPALIVE_SECONDS or talking is None:
            return spoken
        try:
            talking.check()
        except ChronoFtpError:
            pass  # gone: it is logged in again once the answer is there
        return time.monotonic()

    def _no_room(self, job: Job, refused: bool) -> None:
        """No login for this job. While other jobs of that server run, it waits until one of them ended (NoRoom); alone, it is a failure
        as any other. Where the server refused the login itself (refused: too many, or a login that works elsewhere turned away), it
        takes no more than those others now (the ones that got a login or are getting one): that is remembered, each job of that server
        that ends well lets one more try, and a later refusal lowers it again. A login that could not be made for another reason teaches nothing. Jobs that got no login themselves
        count as none of those others: they free nothing when they end. Refused together with such jobs only, it waits all the same and
        the server gets one login at a time; refused alone, it fails."""
        server = server_of(job.connection)
        with self._lock:
            others = [item for item in self._running if item is not job and server_of(item.connection) == server and id(item) not in self._after_an_end]
            now = self._tried_in.get(id(job), 0)
            together = refused and any(other == server and tried == now for other, tried in self._after_an_end.values())
            if not others and not together:
                return  # nothing of that server runs that will free a login, nor was refused in the same round: a failure as any other
            self._after_an_end[id(job)] = (server, now)
            if refused:
                room = (len(others) + sum(self._extra_of(item) for item in others)) or 1  # the ones still logging in count too; refused
                # together with others only: one at a time
                self._room[server] = min(room, self._room.get(server, room))
        raise NoRoom

    def _side_work(self, job: Job, side: SideJob) -> None:
        """An item that deletes, moves, sets dates or copies on this computer (FF-10): its progress counts the steps done, so the
        person sees it move like any other item."""
        job.done, job.total, job.percent, job.seconds_left = 0, side.count, 0, None
        job.way = side.work.value
        meter = Meter(job, self._report)
        meter.set_total(side.count)
        counted = [0]

        def told(done: int) -> None:
            meter.add(done - counted[0])
            counted[0] = done

        run_side_job(job, side, told)

    def _transfer(self, job: Job) -> None:
        job.done, job.total, job.percent, job.seconds_left = 0, 0, 0, None  # every run counts from nothing: what a resume continues from,
        meter = Meter(job, self._report)  # and every file it leaves as it is, is counted once, in this run
        ask = self._asker(job, meter)
        known: dict[str, Action] = job.decided  # what was decided, also in an earlier run of this job: nobody is asked twice
        shown: dict[str, Shown] = job.shown  # the file there as each of those decisions saw it: an answer holds only for that file
        limit = self._limiter(job)
        ways: list[FileWay] = []
        looked_at = undo_broken_appends(job.remote, job.connection, job.stop_asked)  # a file an earlier append broke off in is looked at first
        for name, count in looked_at.items():
            if count:
                self._tell(job, note_text(name, count, APPEND_NOTES))
        if not job.upload:
            tidy_partials_here(job, spared_partials(self.jobs))  # rsync's leftovers here that can't be continued go first
        earlier: dict[str, int] = {}
        if job.options.delete_source and job.moved_pending:  # a move that broke off: what rsync sent then has arrived, so its original
            pending = dict(job.moved_pending)  # goes now, each checked again as before, and before anything is looked over
            job.moved_pending.clear()
            try:
                earlier = remove_arrived_sources(job, pending)
            except BaseException:
                job.moved_pending.update(pending)  # looked at again when it goes on once more
                raise
        if rsync_possible(job) and self._target_readable(job) and not self._links_in_the_target(job):
            look: FileWay | None = None
            options = job.options
            links_here = self._links_in_the_source(job)  # sent as their content, as the file by file way does
            if links_here or options.ask_before_replacing or options.only_newer or options.delete_source or job.answers.batch is not None or chooses_items(options) or self._kinds_clash(job) or self._specials_in_the_target(job):
                job.way = "rsync"
                look = FileWay(job, job.remote, limit, meter, ask, [], known, shown)  # over SSH the browsing connection also transfers
                self._talking[id(job)] = job.remote
                look.run()
                ways.append(look)
            if look is not None:
                look.recheck_agreed()  # a file agreed to be replaced that changed meanwhile leaves rsync's list
            new_files = frozenset(look.recorded_new) if look else frozenset[str]()
            kept = with_rsync(job, limit, self.program, self.remote_shell, meter, look.recording if look else None, look.recorded_sources if look else None, links_here, new_files)
            if kept is not None:
                self._note(job, ways, _summed(kept, earlier))
                self._fail_for_items(ways)
                return
            meter.start_again()  # rsync could not log in: the other way counts every file again, the ones done by the look-over too
            for look_over in ways:
                look_over.links_left = look_over.others_left = look_over.left_by_choice = 0  # the other way meets them again and counts them itself
        job.way = job.connection.protocol.upper()
        try:
            connection = job.remote.for_a_transfer()  # FTP: a login of its own; SSH: the tab's login, whose channels run side by side
        except ConnectionFailed as error:
            self._no_room(job, isinstance(error, (NoMoreLogins, LoginRefused)))  # a server that takes no more logins now is no failure
            raise
        self._talking[id(job)] = connection
        try:
            way = FileWay(job, connection, limit, meter, ask, None, known, shown)
            way.logins = _Lending(self, job)  # a large file may go in parts over further logins, where there is room (FC-05)
            try:
                way.run()
            except NoMoreLogins:
                self._no_room(job, True)  # its login broke off and the server took no new one at once: it waits as for its first
                raise
            self._note(job, [*ways, way], earlier)
            self._fail_for_items([way])  # it met every item again, so the look-over's failures are its own once more
        finally:
            self._talking.pop(id(job), None)
            if connection is not job.remote:
                connection.close()

    def _limiter(self, job: Job) -> Callable[[], int]:
        """The speed limit of this job as it is at each moment: its own, else its share of the queue's."""
        return lambda: self.limit_of(job)

    def _fail_for_items(self, ways: list[FileWay]) -> None:
        """Items that failed on their own inside a folder: the rest went, and the job says which did not."""
        for way in ways:
            failure = way.failure()
            if failure is not None:
                raise failure

    def _note(self, job: Job, ways: list[FileWay], rsync_kept: dict[str, int]) -> None:
        """What the person should know of a transfer that went well: in the queue, reassuring, never alarming (OR-23)."""
        dates = sum(way.dates_not_kept for way in ways)
        notes = [DATES_TEXT.format(count=dates)] if dates else []
        for name in NOTES:
            count = sum(getattr(way, name, 0) for way in ways) + rsync_kept.get(name, 0)  # "appeared" is counted by rsync only
            if count:
                notes.append(note_text(name, count))
        notes += [CHANGED_ASIDE_TEXT.format(name=readable(name), kept=readable(kept)) for way in ways for name, kept in way.changed_aside]
        if notes:
            job.message = " ".join(["Done.", *notes])

    def _links_in_the_source(self, job: Job) -> bool:
        """Whether what is sent holds a link (or, on this computer, something that is neither a file nor a folder). Then the job is looked over first, and rsync gets the exact list with links to files only, which
        it sends as their content; a link to a folder is never on that list. Where the server can't tell, it is looked over all the same."""
        if job.upload:
            source = Path(job.local)
            if source.is_symlink() or (os.path.lexists(source) and not (source.is_file() or source.is_dir())):
                return True  # rsync would send the link, or the pipe or device, as what it is: the look-over says what it is instead
            return source.is_dir() and _has_a_link_or_other(source, job.stop_asked)
        if not job.remote.capabilities.has("find"):
            return True
        try:
            result = job.remote.run(source_check_command(job.remote_path))
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return True
        return result.status != 0 or bool(result.output.strip())

    def _kinds_clash(self, job: Job) -> bool:
        """Whether rsync on its own would replace a file by a folder or a folder by a file somewhere in the tree: then the job is looked
        over, which leaves each such item alone, and rsync gets the exact list. Found from one find on the server and a walk here;
        where the server can't tell, the look-over is always safe."""
        if job.upload:
            source = Path(job.local)
            there = next((entry for entry in job.remote.list_folder(job.remote_path) if entry.name == job.name), None)
            if there is None:
                return False
            if source.is_symlink() or not source.is_dir() or not there.is_folder:
                return there.is_folder != (source.is_dir() and not source.is_symlink())
            here, server = _local_kinds(source, job.stop_asked), _server_kinds(job, joined(job.remote_path, job.name))
        else:
            target = Path(job.local, job.name)
            if not os.path.lexists(target):
                return False
            found = job.remote.details(job.remote_path)
            if found is None or not found.is_folder or not target.is_dir():
                return found is not None and found.is_folder != target.is_dir()
            here, server = _local_kinds(target, job.stop_asked), _server_kinds(job, job.remote_path)
        if server is None:
            return True
        return any(server[path] != folder for path, folder in here.items() if path in server)

    def _specials_in_the_target(self, job: Job) -> bool:
        """Whether an upload meets a pipe, a socket or a device on the server, or a download one here: rsync would replace it by a file.
        Then the job is looked over, which leaves each such item alone and says so; where the server can't tell, it is looked over all
        the same."""
        if not job.upload:
            return _has_a_special(Path(job.local, job.name), job.stop_asked)  # what the download would meet here
        there = next((entry for entry in job.remote.list_folder(job.remote_path) if entry.name == job.name), None)
        if there is None or not there.is_folder or there.is_link:
            return there is not None and there.special
        if not job.remote.capabilities.has("find"):
            return True
        try:
            result = job.remote.run(target_check_command(joined(job.remote_path, job.name)))
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return True
        return result.status != 0 or bool(result.output.strip())

    def _target_readable(self, job: Job) -> bool:
        """Whether the server folder an upload goes into can be read. One that takes files but can't be read (a drop box) can't be looked
        at for rsync: the file by file way, which asks of each name on its own, does the job. Never a failure of the job here."""
        if not job.upload:
            return True
        try:
            job.remote.list_folder(job.remote_path)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return False
        return True

    def _links_in_the_target(self, job: Job) -> bool:
        """rsync would replace a link by a file, also deep inside a folder: the file by file way keeps it."""
        if not job.upload:
            target = Path(job.local, job.name)
            return target.is_symlink() or _has_a_link(target, job.stop_asked)
        there = next((entry for entry in job.remote.list_folder(job.remote_path) if entry.name == job.name), None)
        if there is None or not there.is_folder or there.is_link:
            return there is not None and there.is_link
        if not job.remote.capabilities.has("find"):
            return True
        try:
            result = job.remote.run(f"find {shlex.quote(joined(job.remote_path, job.name))} -type l -print -quit")
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return True  # no answer: the file by file way is always safe
        return result.status != 0 or bool(result.output.strip())


class _Lending:
    """The further logins the queue lends one job for the parts of a split file (split.Logins)."""

    def __init__(self, queue: TransferQueue, job: Job) -> None:
        self.queue: TransferQueue = queue
        self.job: Job = job

    def more(self) -> RemoteConnection | None:
        return self.queue.lend(self.job)

    def again(self, lost: RemoteConnection) -> RemoteConnection | None:
        return self.queue.give_back(self.job, lost, True)

    def done(self, connection: RemoteConnection) -> None:
        self.queue.give_back(self.job, connection)
