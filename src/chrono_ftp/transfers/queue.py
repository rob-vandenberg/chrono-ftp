import json
import os
import queue
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
from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, RemoteActionFailed
from chrono_ftp.transfers.filebyfile import FileWay, Shown, readable, undo_broken_appends
from chrono_ftp.transfers.job import PART_SUFFIX, Job, Meter, State, Stopped
from chrono_ftp.transfers.options import Action, Answer, Choice, Conflict, resumable
from chrono_ftp.transfers.rsyncway import PARTIAL_FOLDER, rsync_possible, with_rsync

# --- Version ------------------------------------------------------------
__version__ = "queue.py 0.0.12"

# --- Version history ----------------------------------------------------
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

__all__ = ["PART_SUFFIX", "Job", "Owned", "State", "Stopped", "TransferQueue", "tidy"]

LEFTOVER_SECONDS: int = 3600  # a temporary file untouched this long belongs to no running transfer, also not on another computer
SEEN_BEFORE_SECONDS: float = 30.0  # what is only a moment in another transfer's life is never there this long, unchanged
POLL_SECONDS: float = 0.2
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
}
APPEND_NOTES: dict[str, tuple[str, str]] = {  # what setting back the appends that broke off earlier has to tell (OR-23)
    "whole": ("1 file that an append broke off in has the append's full length, so it was left as it is.", "{count} files that an append broke off in have the append's full length, so they were left as they are."),
    "maybe part": ("1 file that an append broke off in may hold part of it; as nothing tells whether it was written to since, it was left as it is.", "{count} files that an append broke off in may hold part of it; as nothing tells whether they were written to since, they were left as they are."),
    "set back": ("1 file that an append left half done has its old length back.", "{count} files that an append left half done have their old length back."),
    "refused": ("1 file that an append left half done could not be cut back by the server, so it may still hold part of that append.", "{count} files that an append left half done could not be cut back by the server, so they may still hold part of that append."),
    "left": ("1 file that an append left half done was changed since by something else, so it was left as it is; it may still hold part of that append.", "{count} files that an append left half done were changed since by something else, so they were left as they are; they may still hold part of that append."),
}

Asking = Callable[[Job, Conflict, Callable[[Answer], None]], None]  # hands a question to the window; the answer may come from any thread


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

    def __init__(self, login: Connection, options: TransferOptions) -> None:
        self.login: Connection = login
        self.options: TransferOptions = options

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
        if entry.is_folder and not entry.is_link and entry.name == PARTIAL_FOLDER:
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
    note = owned.partial(folder)
    if note is None or entry.modified is None or moment - entry.modified <= LEFTOVER_SECONDS:
        return False
    path = joined(folder, PARTIAL_FOLDER)
    try:
        inside = connection.list_folder(path)
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        return False
    for item in inside:
        relative = os.path.relpath(joined(folder, item.name), note.base)
        if note.resume != RESUME_OFF and os.path.isfile(Path(note.source_base) / relative):
            return False  # the next rsync of that file continues from it
    try:
        connection.delete(path, True)
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        return False
    return True


def tidy_partials_here(job: Job) -> None:
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
            if PARTIAL_FOLDER in folders:
                folders.remove(PARTIAL_FOLDER)
                places.append(place)
    moment = time.time()
    for place in places:
        partial = Path(place, PARTIAL_FOLDER)
        try:
            if not partial.is_dir() or partial.is_symlink() or moment - partial.stat().st_mtime <= LEFTOVER_SECONDS:
                continue
            names = os.listdir(partial)
        except OSError:
            continue
        if note.resume != RESUME_OFF and any(job.remote.details(joined(note.source_base, os.path.relpath(os.path.join(place, name), note.base))) is not None for name in names):
            continue  # the next rsync of that file continues from it
        shutil.rmtree(partial, ignore_errors=True)


def note_text(name: str, count: int, table: dict[str, tuple[str, str]] = NOTES) -> str:
    one, many = table[name]
    return one if count == 1 else many.format(count=count)


class TransferQueue:
    """The jobs run one after the other on one thread. changed is called from that thread: the window hands it on to its own. ask hands
    the overwrite question to the window; without it nothing that is there already is replaced. remote_shell: what rsync starts instead
    of ssh, for the tests. told hands the window what it should show in passing, from that thread."""

    def __init__(self, changed: Callable[[Job], None], program: str = "rsync", ask: Asking | None = None, remote_shell: list[str] | None = None, told: Callable[[Job, str], None] | None = None) -> None:
        self.changed: Callable[[Job], None] = changed
        self.told: Callable[[Job, str], None] | None = told
        self.limit: int = 0  # KB per second, 0 for no limit; a running job keeps the limit it started with
        self.program: str = program
        self.ask: Asking | None = ask
        self.remote_shell: list[str] | None = remote_shell
        self.jobs: list[Job] = []
        self._talking: RemoteConnection | None = None  # the connection the running job transfers over: kept alive while a question waits
        self._waiting: queue.Queue[Job | None] = queue.Queue()
        self._thread: threading.Thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def add(self, job: Job) -> None:
        self.jobs.append(job)
        self.changed(job)
        self._waiting.put(job)

    def stop(self, job: Job) -> None:
        job.stop_asked.set()
        if job.state == State.WAITING:
            job.state = State.STOPPED
            self.changed(job)

    def close(self) -> None:
        """Also a question still open ends: its job is stopped, so this thread never waits for a window that is gone."""
        for job in self.jobs:
            job.stop_asked.set()
        self._waiting.put(None)

    def _run(self) -> None:
        while (job := self._waiting.get()) is not None:
            if job.stop_asked.is_set():
                continue
            job.state = State.RUNNING
            self.changed(job)
            try:
                self._transfer(job)
                job.state, job.percent = State.DONE, 100
            except Stopped:
                job.state = State.STOPPED
            except ChronoFtpError as error:
                job.state, job.message = State.FAILED, str(error)
            except OSError as error:  # this computer's own problem, in its words, never a bare system message
                job.state, job.message = State.FAILED, (LOCAL_READ_TEXT if job.upload else LOCAL_WRITE_TEXT).format(reason=error.strerror or str(error))
            except Exception as error:  # one job may never end the queue of all tabs
                job.state, job.message = State.FAILED, str(error) or "The transfer did not finish."
            self._talking = None
            job.seconds_left = None
            self.changed(job)

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
            self.changed(job)
            asked_at = time.monotonic()
            spoken = asked_at
            try:
                self.ask(job, conflict, reply)
                while not answered.wait(POLL_SECONDS):
                    if job.stop_asked.is_set():
                        raise Stopped  # also when the window closed: close() stops every job
                    if time.monotonic() - spoken >= KEEPALIVE_SECONDS:
                        spoken = time.monotonic()
                        self._keep_alive()
            finally:
                meter.paused(time.monotonic() - asked_at)  # the time left counts only time spent transferring
                job.waiting_for_answer = False
                self.changed(job)
            if self._talking is not None:
                self._talking.revive()  # a server that closed it all the same: logged in again, which needs nothing from the person
            return given[0]

        return ask

    def _keep_alive(self) -> None:
        """A server closes a control connection that says nothing for long: while the person thinks, it hears from us now and then."""
        if self._talking is None:
            return
        try:
            self._talking.check()
        except ChronoFtpError:
            pass  # gone: it is logged in again once the answer is there

    def _transfer(self, job: Job) -> None:
        meter = Meter(job, self.changed)
        ask = self._asker(job, meter)
        known: dict[str, Action] = {}  # what was decided while looking over the job for rsync: nobody is asked twice
        shown: dict[str, Shown] = {}  # the file there as each of those decisions saw it: an answer holds only for that file
        ways: list[FileWay] = []
        looked_at = undo_broken_appends(job.remote, job.connection)  # a file an earlier append broke off in is looked at first
        for name, count in looked_at.items():
            if count and self.told is not None:
                self.told(job, note_text(name, count, APPEND_NOTES))
        if not job.upload:
            tidy_partials_here(job)  # rsync's leftovers here that can't be continued go first
        if rsync_possible(job) and self._target_readable(job) and not self._links_in_the_target(job):
            look: FileWay | None = None
            options = job.options
            links_here = self._links_in_the_source(job)  # sent as their content, as the file by file way does
            if links_here or options.ask_before_replacing or options.only_newer or options.delete_source or job.answers.batch is not None or self._kinds_clash(job) or self._specials_in_the_target(job):
                job.way = "rsync"
                look = FileWay(job, job.remote, self.limit, meter, ask, [], known, shown)  # over SSH the browsing connection also transfers
                self._talking = job.remote
                look.run()
                ways.append(look)
            if look is not None:
                look.recheck_agreed()  # a file agreed to be replaced that changed meanwhile leaves rsync's list
            new_files = frozenset(look.recorded_new) if look else frozenset[str]()
            kept = with_rsync(job, self.limit, self.program, self.remote_shell, meter, look.recording if look else None, look.recorded_sources if look else None, links_here, new_files)
            if kept is not None:
                self._note(job, ways, kept)
                self._fail_for_items(ways)
                return
            meter.start_again()  # rsync could not log in: the other way counts every file again, the ones done by the look-over too
            for look_over in ways:
                look_over.links_left = look_over.others_left = 0  # the other way meets them again and counts them itself
        job.way = job.connection.protocol.upper()
        connection = job.remote.for_a_transfer()
        self._talking = connection
        try:
            way = FileWay(job, connection, self.limit, meter, ask, None, known, shown)
            way.run()
            self._note(job, [*ways, way], {})
            self._fail_for_items([way])  # it met every item again, so the look-over's failures are its own once more
        finally:
            self._talking = None
            if connection is not job.remote:
                connection.close()

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
