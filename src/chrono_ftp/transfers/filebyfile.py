"""The way of transferring file by file over SFTP or FTP, with every option (FB-01 to FB-08). It also looks over a transfer for rsync
first: then it asks the overwrite questions and notes what rsync is to send, so rsync never writes a file the person refused."""

import errno
import math
import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from chrono_ftp.config import parts
from chrono_ftp.config.parts import AppendNote, PartRecord, download_key, download_source, upload_key, upload_source
from chrono_ftp.connections.base import LOCAL_READ_TEXT, LOCAL_WRITE_TEXT, OLD_SUFFIX, Limit, LocalFile, RemoteConnection, RemoteEntry, Swapped, joined, own_name, parent_of, safe_name, shown_name
from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, ContinuationRefused, RefusedForNow, LocalActionFailed, LocalReadFailed, LocalWriteFailed, NameTaken, RemoteActionFailed, TransferInterrupted
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.transferoptions import BINARY, RESUME_ON, file_rights, folder_rights
from chrono_ftp.transfers.job import FOLDER_IN_THE_WAY_TEXT, PART_SUFFIX, SOURCE_GONE_TEXT, Ask, Job, Meter, Stopped
from chrono_ftp.transfers.conversion import Passing, changes_names, convert_fetched, is_text, renamed as renamed_for, upload_copy
from chrono_ftp.transfers.split import Logins, Move, Spans, move_in_parts, splits
from chrono_ftp.transfers.options import NO_CUT, SIZE_UNKNOWN, TEXT_NO_APPEND, Action, Choice, Conflict, action_of, decided, resumable

# --- Version ------------------------------------------------------------
__version__ = "filebyfile.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Masks (block FD): what the file mask, "skip hidden files" and "skip empty folders" leave out is neither counted nor sent, and
#          told (FD-01); a text file goes whole, converted into a copy of our own on this computer, never continued, split or appended,
#          and never by rsync (FD-02); names take their converted form in the target, and a renamed item goes file by file (FD-03); a
#          look-over that converts makes a missing folder at once, so what it sends itself has its place; a text sent in an earlier run
#          is compared with the size it really has at the target, so a retry or a resume never asks about it again; two items of one
#          transfer whose names become one never write over each other: the later one is left out and its source stays (D-083); an
#          upload's part is kept, and told as kept, only where the server holds something of it then (a fault since 0.0.12); what the
#          person picked always goes, the mask deciding only what goes from inside a picked folder
# v0.0.13: The speed limit is handed on as it is asked, so a new one takes effect at once (FC-02); a pause keeps what arrived of the file
#          on its way, noted to be continued, whatever the resume setting; a pause that is ended otherwise than by a resume gives those
#          notes their own resume setting back, and is never continued from; a file sent in an earlier run is sent again when its source
#          or what is there changed since, comparing dates only where the date was really set; appends that broke off are looked at
#          by one job of a login at a time while other jobs run (FC-01), each note checked again right before it is acted on, so one
#          another job set back or started anew meanwhile is left alone; a job that waits for its turn there sees its stop; a large file
#          goes in parts over several logins where the person chose it (FC-05), its confirmed ranges noted with its part
# v0.0.12: Initial: moved here from queue.py; resume, the overwrite question for every file, only newer (unknown dates counted), dates,
#          rights, count first, move. A part is noted per part file with its source and the bytes confirmed written, and continued
#          only from there; a part is kept only after a stop, a lost connection or a local disk problem, and a refused continuation
#          starts again from the beginning at once. Every arrival is checked for its size where the server can tell it; a source goes
#          only when what arrived was checked and the source is unchanged, and each reason it stays is told. A link whose target can't
#          be seen is never written through; one item that fails inside a folder fails alone; a folder that is there already keeps its
#          rights, as in the reference program; counting reads each folder once and keeps nothing; Append only where a broken-off
#          append is undone; a resumed file starts again only when the continuation itself is refused; a local write problem keeps the
#          part, as does a local read problem; the left-out items are named up to five; no date set through a local link; a folder that
#          can't be read still takes
#          an upload; a temporary or moved-aside name is ours only with our note, else a free one is taken, so no file of someone else is
#          written to or removed; an append notes the length the file really has and the most the append can add, and sets back only
#          where nothing but the append can be in the file; it starts only once that note is stored; in a folder that can't be read a
#          name is looked up before it is taken; a link that leads nowhere fails alone before rsync; a copy not confirmed on the
#          server's disk keeps its original; a continued FTP upload is told; only what is known to be a file is replaced; a new file never
#          replaces what took its name meanwhile, on the server or here; a completed append is never cut, and an append that can't be
#          set back says why; a part, or an old file moved aside, is kept only with its stored note; an old copy that stays is told; a
#          folder that can't be read is handled so at every level; a link is fetched by the size of the file it leads to; a fetched copy
#          is put in place only while the server's file still has the size and date it was read with; counting first skips a folder that
#          can't be read; an append that just broke off is set back also at its full length, and a later look sets back only a file
#          unchanged since; a failed swap forgets the note of an old file that never moved; a local link that leads nowhere is never
#          written through; a part's note keeps the resume setting it was kept by; every check of a change reads the server this moment;
#          a part that may have taken its real name never gets a note; a refusal for now keeps a continued part; a move tells where
#          the server could not put a file on its disk; a file is replaced only while it is still the one agreed to, and the aside name
#          is free that moment; a part is noted as running before its first byte and made only where nothing is, another Chrono FTP's
#          part is never touched, and one that can't be removed stays ours; a name a listing leaves out is told; an append whose end is
#          unknown is never cut; continuing judges a part by the setting it was kept by; a file that can't be read here is said so; a
#          note stays only for a temporary file that really exists; a rename is remembered only once done, and never acted on without a
#          name; a download replaces the file here only while it is the one agreed to; a name a server listing can't show is never sent;
#          an old part that can't be removed stays ours and is told; a part is kept only while it is there; counting first stops at Stop;
#          a continued FTP upload is told only once its size fits; a rename onto a server link stays out of rsync's list; a picked link
#          to a folder, broken link or pipe fails saying so, and those left out inside a folder are counted; a file agreed to be
#          replaced is looked at again right before rsync runs; a pipe, socket or device on the server is never fetched, nor replaced;
#          nor one here by a download; an old file that changed in the very last moment is kept and told;
#          an answer carried over to the other way holds only for the file as the person saw it; every name of our own fits the
#          length a folder holds, also next to a long name; a finished copy is kept for the next try until it takes its real name; a
#          part that could not be removed after a refused continuation keeps its note; Append holds only for the file as shown; a
#          picked item no longer on the server fails saying so, and nothing is made here; one the server refuses to tell about says
#          that instead

FILE_IN_THE_WAY_TEXT: str = "There is a file with this name on the server, so this folder was not sent."
FILE_HERE_TEXT: str = "There is a file with this name on this computer, so this folder was not fetched."
FOLDER_HERE_TEXT: str = "There is a folder with this name on this computer, so this file was not fetched."
NOT_THERE_TEXT: str = "The folder {folder} is not on the server."
CHANGED_HERE_TEXT: str = "{name} changed on this computer while it was being sent, so the copy was not put in place. Please send it once more."
CHANGED_THERE_TEXT: str = "{name} changed on the server while it was being fetched, so the copy was not put in place. Please fetch it once more."
SERVER_SIZE_TEXT: str = "The server holds {name} at another size than was sent, so the copy was not put in place. Please send it once more."
NOT_A_NAME_TEXT: str = "A name can't hold a slash."
NO_APPEND_TEXT: str = "This server can't undo an append that breaks off, so nothing was appended."
SAME_NAME_TEXT: str = "Another item of this transfer already has the name {name} there, so this one was not written over it and its original stays where it is."
NO_TEXT_APPEND_TEXT: str = "A text file whose line endings are converted always comes whole, so nothing was appended."
MOST_NAMED: int = 5  # the job's message names this many items that were left out, and counts the rest
MOST_FAILED: int = 100  # so many are kept with their reason; the rest only counted
PART_KEPT_TEXT: str = "What arrived is kept, so the next try continues from there."
PART_GONE_TEXT: str = "The next try starts from the beginning."
APPEND_LEFT_TEXT: str = "The connection broke off during the append to {name}, which may now hold part of it. Once the connection is back, Chrono FTP looks at it and tells what it finds."
APPEND_CHANGED_TEXT: str = "The append to {name} broke off, and something else wrote to that file meanwhile, so it was left as it is; it may hold part of the append."
APPEND_GONE_TEXT: str = "The append to {name} broke off, and the file is no longer on the server."
APPEND_NOT_CUT_TEXT: str = "The append to {name} broke off, and the server would not cut the file back, so it may hold part of the append."
APPEND_UNREAD_TEXT: str = "The append to {name} broke off, and the server can't tell the file's length now; it is set back before the next transfer if it can be."
KIND_UNKNOWN_TEXT: str = "Something named {name} is already in this folder on the server, and whether it is a file or a folder can't be seen, so it was left as it is and nothing was sent."
CHANGED_TO_REPLACE_TEXT: str = "{name} changed on the server meanwhile, so it was not replaced. Look at it, then send yours again."
APPEARED_THERE_TEXT: str = "{name} appeared on the server while it was being sent, so it was left as it is and nothing replaced it."
CHANGED_HERE_NOT_REPLACED_TEXT: str = "{name} changed on this computer meanwhile, so it was not replaced. Look at it, then fetch it again."
CHANGED_HERE_REPLACE_TEXT: str = "{name} changed on this computer meanwhile, so it was not replaced; what was fetched is kept as {kept}."
APPEARED_HERE_TEXT: str = "{name} appeared on this computer while it was being fetched, so it was left as it is; what was fetched is kept as {kept}."
OLD_NOT_NOTED_TEXT: str = "Chrono FTP could not note on this computer where the old {name} would wait, so it was not replaced; nothing on the server changed."
APPEND_NOT_NOTED_TEXT: str = "Chrono FTP could not note the file's length on this computer before the append, so nothing was appended: a broken-off append could not be undone."
UNKNOWN_THERE_TEXT: str = "This folder on the server can't be read, and whether {name} is there already could not be found out, so it was not sent."
NO_FREE_NAME_TEXT: str = "Every temporary name for {name} is taken by other files, so it was not sent."
LOCAL_LINK_NOWHERE_TEXT: str = "{name} on this computer is a link that leads nowhere, so it was left as it is and nothing was fetched."
UNCHECKABLE_NAME_TEXT: str = "This name holds a character (a backslash) that a server's listing can't show safely, so whether it is there already can't be checked on the server, and it was not sent."
UNSAFE_NAME_TEXT: str = "This name holds a character (a slash, a backslash or a NUL) that can't be handled safely here, so it was left out."
UNREADABLE_THERE_TEXT: str = "{name} can't be read on the server, so nothing was fetched."
GONE_THERE_TEXT: str = "{name} is no longer on the server, so nothing was fetched."
LINK_NOWHERE_TEXT: str = "This is a link that leads nowhere, so there is nothing to fetch."
LINK_TO_FOLDER_UP_TEXT: str = "{name} is a link to a folder. Links are not followed into other folders, so nothing was sent."
LINK_TO_FOLDER_DOWN_TEXT: str = "{name} is a link to a folder. Links are not followed into other folders, so nothing was fetched."
LINK_NOWHERE_UP_TEXT: str = "{name} is a link that leads nowhere, so nothing was sent."
NOT_A_FILE_DOWN_TEXT: str = "{name} on the server is neither a file nor a folder (a pipe, a socket or a device, for instance), so nothing was fetched."
NOT_A_FILE_THERE_TEXT: str = "{name} on the server is neither a file nor a folder (a pipe, a socket or a device, for instance), so it was left as it is and nothing was sent."
NOT_A_FILE_HERE_TEXT: str = "{name} on this computer is neither a file nor a folder (a pipe, a socket or a device, for instance), so it was left as it is and nothing was fetched."
NOT_A_FILE_UP_TEXT: str = "{name} is neither a file nor a folder (a pipe, a socket or a device, for instance), so nothing was sent."
NO_HARD_LINKS: frozenset[int] = frozenset({errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EXDEV, errno.EMLINK, errno.ENOSYS})  # a disk without hard links
MOST_NAMES: int = 100  # temporary names tried: name.chrono-ftp-part, name~2.chrono-ftp-part, …
UNSEEN_LINK_TEXT: str = "{name} on the server is a link, and where it leads can't be seen over this connection, so it was left as it is. You can upload to the file it leads to instead."
ONE_LEFT_TEXT: str = "Partly done: {name} was left out. {reason}"
SOME_LEFT_TEXT: str = "Partly done: {count} items were left out ({names}). {name}: {reason}"
MORE_TEXT: str = "{names} and {count} more"


Shown = tuple[int, bool, float | None]  # the file there as a decision saw it: its size, whether that is known, its date


def shown_of(conflict: Conflict) -> Shown:
    return (conflict.target_size, conflict.target_size_known, conflict.target_modified)


def _own_there(target: str, extra: str) -> str:
    """A server path of our own next to target, within the length a folder holds."""
    return joined(parent_of(target), own_name(target.rsplit("/", 1)[-1], extra))


def _own_here(target: str, extra: str) -> str:
    """A path of our own on this computer next to target, within the length a folder holds."""
    folder, name = os.path.split(target)
    return os.path.join(folder, own_name(name, extra))


def readable(name: str) -> str:
    """A name as the queue can show it: a line break or another control character becomes a question mark."""
    return "".join(character if character.isprintable() else "?" for character in shown_name(name))



def _there(check: Callable[[], bool]) -> bool:
    """Whether a part is still there to be kept; where that can't be asked now (the connection is gone), it is taken to be: its note
    then names a part that may be there, never a part of nobody's."""
    try:
        return check()
    except (ChronoFtpError, OSError):
        return True

def end_pause_notes(job: Job) -> None:
    """A pause that ends otherwise than in a resume (a stop, a delete, a run that does not continue it): the parts it kept are noted again
    with the resume setting they would have had, so with resume off nothing continues from them and tidying takes them as any other."""
    for key, resume in job.paused_parts.items():
        note = parts.lookup(key)
        if note is not None and note.resume == RESUME_ON and resume != RESUME_ON and not note.running:
            parts.remember(key, replace(note, resume=resume))
    job.paused_parts.clear()


class SourceChanged(RemoteActionFailed):
    """The source changed while it was transferred: what arrived is thrown away, and trying again at once would meet the same change."""


class TemporaryTaken(Exception):
    """The temporary name chosen can't be used after all (something took it, or another Chrono FTP writes it): the next free one."""


class PartDoesNotFit(RemoteActionFailed):
    """A continued part came out at another size than its source: it is thrown away, and the file starts again from the beginning."""


def local_size(path: Path, stop: threading.Event | None = None, passing: Passing | None = None) -> int:
    """What an upload of path sends: a link to a file counts as the file it leads to, a link to a folder is not followed, and what is gone
    by the time it is counted counts nothing (its transfer then fails on its own). Stop (stop) ends the count at once. passing: what the
    transfer leaves out inside a picked folder is not counted (FD-01); what was picked always goes, and counts."""
    def size_of(item: str, picked: bool = False) -> int:
        try:
            details = os.stat(item)
        except OSError:
            return 0
        if not picked and passing is not None and not passing.passes(os.path.basename(item), False, os.path.dirname(item), details.st_size, details.st_mtime):
            return 0
        return details.st_size if os.path.isfile(item) else 0

    if not os.path.isdir(path) or os.path.islink(path):
        return size_of(str(path), True)
    total = 0
    for place, folders, files in os.walk(path):
        if stop is not None and stop.is_set():
            raise Stopped
        if passing is not None:
            folders[:] = [name for name in folders if passing.passes(name, True, place, None, None)]
        total += sum(size_of(os.path.join(place, name)) for name in files)
    return total


def durable(path: str) -> None:
    """The file and its folder are on the disk, not only in a cache: a move removes the source only after this (FB-08)."""
    with open(path, "rb") as file:
        os.fsync(file.fileno())
    try:
        folder = os.open(os.path.dirname(path) or ".", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return  # a system that can't open a folder like this keeps its folders on its own
    try:
        os.fsync(folder)
    except OSError:
        pass
    finally:
        os.close(folder)


class FileWay:
    """One job, file by file. recording: for rsync, the paths (relative to the job's base) it is to send; this way then sends only what
    rsync can't do (a rename, an append, a continued part file). known: decisions taken before, so nobody is asked twice."""

    def __init__(self, job: Job, connection: RemoteConnection, limit: Limit, meter: Meter, ask: Ask, recording: list[str] | None = None, known: dict[str, Action] | None = None, shown: dict[str, Shown] | None = None) -> None:
        self.job: Job = job
        self.connection: RemoteConnection = connection
        self.options = job.options
        self.limit: Limit = limit  # asked again for every block
        self.meter: Meter = meter
        self.ask: Ask = ask
        self.recording: list[str] | None = recording
        self.known: dict[str, Action] = known if known is not None else {}
        self.shown: dict[str, Shown] = shown if shown is not None else {}  # the file there as each decision saw it, shared like known
        self.dates_not_kept: int = 0
        self.unknown_dates: int = 0  # left as they were by "only newer": without both dates nothing is known to be newer (FB-04)
        self.kept_changed: int = 0  # a move left these sources: they changed while they were sent (FB-08)
        self.kept_unchecked: int = 0  # a move left these sources: what arrived could not be checked
        self.kept_not_removed: int = 0  # a move left these sources: they arrived whole, but could not be removed
        self.sizes_unknown: int = 0  # arrived, but the server could not tell their size, so it was not checked
        self.restarted: int = 0  # a part the server would not continue: started again from the beginning
        self.continued_by_size: int = 0  # continued on a server that can't confirm the first part: checked by its size
        self.removed_unflushed: int = 0  # a move removed these originals after a check of size and date only: the server can't flush
        self.parts_left: int = 0  # unfinished copies of earlier tries that could not be removed: ours by their notes, tidied later
        self.kept_aside: int = 0  # replaced, but the old copy could not be removed and stays under its aside name
        self.changed_aside: list[tuple[str, str]] = []  # replaced, but the old file changed in the last moment: kept under this name
        self.notes_left: int = 0  # notes on this computer that could not be removed (harmless: they are looked at again later)
        self.links_left: int = 0  # links to folders, or that lead nowhere, left out inside a folder: links are not followed
        self.others_left: int = 0  # things that are neither a file nor a folder (a pipe, a device), left out inside a folder
        self.unreadable: set[str] = set()  # server folders that take files but can't be read: each name is looked up on its own
        self.recorded_sources: dict[str, tuple[int, float | None]] = {}  # for rsync: the size and date of each file it is to send, as read
        self.recorded_new: set[str] = set()  # for rsync: the files it is to send that were not there at the look-over
        self.agreed_there: dict[str, tuple[str, RemoteEntry]] = {}  # for rsync: each server file the person agreed to replace, as shown
        self.agreed_here: dict[str, tuple[str, tuple[int, float]]] = {}  # for rsync: each file here agreed to be replaced: size and date
        self.new_name: str = ""  # the name the person gave in the question, for Rename
        self.can_resume: bool = connection.capabilities.resume  # without it a part file is never offered or continued
        self.failed: list[tuple[str, str]] = []  # items inside a folder that failed on their own, with why (OR-15); the first MOST_FAILED
        self.failed_count: int = 0  # all of them
        self.dated: bool = False  # the date of the file just sent was set to its source's
        self.attempt_counted: int = 0  # what the meter counted of the file now on its way, taken back when it starts again
        self.logins: Logins | None = None  # the further logins the queue lends for a large file split in parts (FC-05); None: never split
        self.passing: Passing = Passing(self.options)  # what the transfer leaves out (FD-01)
        self.left_by_choice: int = 0  # items the file mask, "skip hidden files" or "skip empty folders" left out: told, never in silence
        self.converts: bool = self.options.transfer_mode != BINARY or changes_names(self.options)  # some items go by this way, also beside rsync
        self.converted_size: int = 0  # the size of the text fetched last, once converted
        self.holds: dict[str, bool] = {}  # for "skip empty folders": whether a folder holds a file that goes, each looked at once

    def run(self) -> None:
        if self.passing.problem:
            raise LocalActionFailed(self.passing.problem)  # a mask that can't be read sends nothing (OR-15)
        try:
            if self.job.upload:
                self._upload()
            else:
                self._download()
        finally:
            parts.settle_olds()

    def failure(self) -> RemoteActionFailed | None:
        """The items that failed on their own, as one failure of the job; None when every item went."""
        if not self.failed:
            return None
        name, reason = self.failed[0]
        if self.failed_count == 1:
            return RemoteActionFailed(ONE_LEFT_TEXT.format(name=readable(name), reason=reason))
        names = ", ".join(readable(item) for item, _reason in self.failed[:MOST_NAMED])
        if self.failed_count > MOST_NAMED:
            names = MORE_TEXT.format(names=names, count=self.failed_count - MOST_NAMED)
        return RemoteActionFailed(SOME_LEFT_TEXT.format(count=self.failed_count, names=names, name=readable(name), reason=reason))

    def recheck_agreed(self) -> None:
        """Right before rsync runs: each file the person agreed to replace must still be the one the question showed (its size and date).
        One that changed meanwhile leaves rsync's list and fails on its own, as on the file by file way, and for a move its source stays.
        A change after this look, until rsync writes that file, is not seen."""
        for relative, (target, old) in self.agreed_there.items():
            try:
                self._still_the_one(target, old)
            except ConnectionLost:
                raise
            except RemoteActionFailed as error:
                self._not_for_rsync(relative, str(error))
        for relative, (target, seen) in self.agreed_here.items():
            try:
                now = os.stat(target)
            except FileNotFoundError:
                continue  # gone meanwhile: nothing is replaced, the name is simply taken
            if (now.st_size, now.st_mtime) != seen:
                self._not_for_rsync(relative, CHANGED_HERE_NOT_REPLACED_TEXT.format(name=readable(os.path.basename(target))))

    def _not_for_rsync(self, relative: str, reason: str) -> None:
        assert self.recording is not None
        self.recording.remove(relative)
        self.recorded_sources.pop(relative, None)  # a move keeps its source
        self.recorded_new.discard(relative)
        self._left_out(relative, reason)

    def _left_out(self, relative: str, reason: str) -> None:
        self.failed_count += 1
        if len(self.failed) < MOST_FAILED:
            self.failed.append((relative, reason))

    def _each(self, relative: str, work: Callable[[], None]) -> None:
        """One item inside a folder: what fails there fails only that item, and the rest of the folder goes on (OR-15). A stop or a lost
        connection ends the whole job."""
        try:
            work()
        except ConnectionLost:
            raise
        except (RemoteActionFailed, LocalActionFailed) as error:
            self._left_out(relative, str(error))
        except OSError as error:  # a file or folder of this computer that could not be read or written
            reason = error.strerror or str(error)
            self._left_out(relative, (LOCAL_READ_TEXT if self.job.upload else LOCAL_WRITE_TEXT).format(reason=reason))

    # --- what both directions share

    def _decide(self, relative: str, conflict: Conflict) -> Action:
        if self.known.get(relative) == Action.SKIP and relative in self.job.sent_as and not self._still_as_sent(relative, conflict):
            del self.known[relative]  # sent in an earlier run, but its source or what is there changed since: it is decided afresh
            self.shown.pop(relative, None)
            del self.job.sent_as[relative]
            self.job.arrived_as.pop(relative, None)
        if relative in self.known:
            action = self.known[relative]
            if action != Action.SKIP and self.shown.get(relative, shown_of(conflict)) != shown_of(conflict):
                self._changed_since_decided(conflict)  # an answer holds only for the file as the person saw it
            return action
        action = decided(self.options, self.job.answers, conflict)
        if action is None:
            answer = self.ask(conflict)
            action = action_of(answer, self.job.answers, conflict)
            if action == Action.RENAME and not safe_name(answer.new_name):
                raise RemoteActionFailed(NOT_A_NAME_TEXT)
            if action == Action.RENAME:
                self.new_name = answer.new_name
        if action == Action.STOP:
            raise Stopped
        unknown = conflict.source_modified is None or conflict.target_modified is None
        if action == Action.SKIP and unknown and (self.options.only_newer or self.job.answers.batch == Choice.ONLY_IF_NEWER):
            self.unknown_dates += 1  # never replaced without knowing, and never in silence
        if action != Action.RENAME:
            self.known[relative] = action  # a rename is known only once it is done (as SKIP): its name belongs to this way
            self.shown[relative] = shown_of(conflict)
        return action

    def _still_as_sent(self, relative: str, conflict: Conflict) -> bool:
        """A file sent in an earlier run of this job (paused, stopped, failed): the source still has the size and date it was sent with,
        and what is there is still what was sent (its size, and its date where that date was really set: a server that can't set dates
        keeps its own)."""
        size, date, dated = self.job.sent_as[relative]
        there = self.job.arrived_as.get(relative, size)  # a converted text has its own size at the target
        if (conflict.source_size, conflict.source_modified) != (size, date) or not conflict.target_size_known or conflict.target_size != there:
            return False
        if dated and date is not None and conflict.target_modified is not None:  # only a date that was really set tells anything
            return int(conflict.target_modified) == int(date)
        return True

    def _changed_since_decided(self, conflict: Conflict) -> None:
        """The file there changed after the decision about it (the person's answer, carried over to the other way after rsync could not
        log in): it is left as it is now, and for a move the source stays."""
        name = readable(conflict.name)
        if self.job.upload:
            raise RemoteActionFailed(CHANGED_TO_REPLACE_TEXT.format(name=name))
        raise LocalActionFailed(CHANGED_HERE_NOT_REPLACED_TEXT.format(name=name))

    def _continuable(self, actual: int | None, key: str, source: str, size: int | None, modified: float | None) -> int | None:
        """Where a part file is continued from, or None. A part is continued only when all of these hold:
        1. the connection can continue a file, and the source's size and date are known;
        2. the part has a note (its notes are kept per part file, and every new start of that part replaces or removes it), and the note
           names this very source, with exactly the size and date the source has now (each read from the source's own side, so no two
           clocks are compared);
        3. it continues at the smaller of the part's size now and the bytes the note says were confirmed written in order. Those first
           bytes are known to be bytes of this source: a gap a failed request may have left lies after them, and the transfer first cuts
           the part to where it continues.
        It does not prove that the bytes are the same: a change of the source that keeps both its size and its date is not seen."""
        if not self.can_resume or actual is None or size is None or modified is None:
            return None
        note = parts.lookup(key)
        if note is None or (note.source, note.size, note.modified) != (source, size, modified):
            return None
        offset = min(actual, note.part_size)
        return offset if 0 < offset <= size else None

    def _offset(self, action: Action, part_offset: int | None, size: int, key: str) -> int:
        """Where a transfer starts: where a part can be continued, else at the beginning. Resume asked for by the person needs no size limit;
        else the part is judged by the resume setting it was kept by, as tidying judges it, so "the next try continues" holds."""
        if part_offset is None:
            return 0
        note = parts.lookup(key)
        by = replace(self.options, resume=note.resume) if note is not None and note.resume else self.options
        if key in self.job.paused_parts and not self.job.resuming:
            by = replace(self.options, resume=self.job.paused_parts[key])  # a pause that was ended counts no more
        return part_offset if action == Action.RESUME or resumable(by, size) else 0

    @staticmethod
    def _let_go(key: str, whose: PartRecord, remove: Callable[[], None], still_there: Callable[[], bool]) -> None:
        """A part that is not kept goes. Where it can't be removed, it stays ours: its note says so, with nothing to continue from, so
        tidying removes it later. Its note stays only while it is really there: never for one that was never made, nor for one that took
        its real name."""
        try:
            remove()  # once it took its real name nothing is left under this one
        except (ChronoFtpError, OSError):
            try:
                there = still_there()  # also never made at all (the server refused to make it): then no note
            except (ChronoFtpError, OSError):
                there = True  # it can't be told: kept as ours, never as nobody's
            if there:
                parts.remember(key, replace(whose, part_size=0))  # the first error is the one to report; this part stays, still ours
                return
        parts.forget(key)

    def _clear_part(self, key: str, remove: Callable[[], None], part_there: bool) -> bool:
        """A part of ours that is not continued goes, with its note, before anything is written. When it can't be removed, it stays ours:
        its note says so, with nothing to continue from, so tidying removes it later, and the queue tells it. The new transfer then takes
        the next free name, as the part still has this one."""
        if not part_there:
            parts.forget(key)
            return True
        try:
            remove()
        except ConnectionLost:
            raise
        except (RemoteActionFailed, OSError):
            note = parts.lookup(key)
            if note is not None:
                parts.remember(key, replace(note, part_size=0, running=False, owner=""))
            self.parts_left += 1
            return False
        parts.forget(key)
        return True

    def _kept_record(self, key: str, whose: PartRecord, written: int, ranges: tuple[tuple[int, int], ...] = ()) -> PartRecord:
        """The note of a part that is kept. After a pause it is noted to be continued whatever the resume setting, as the person asked for
        it to go on later (a pause is a stop the person means to continue)."""
        if self.job.continue_later:
            self.job.paused_parts[key] = whose.resume
        return replace(whose, part_size=written, resume=RESUME_ON if self.job.continue_later else whose.resume, ranges=ranges if written else ())

    @staticmethod
    def _kept_after(error: BaseException) -> bool:
        """A part is kept only after a stop, a lost connection or a problem of this computer's disk: what can go well the next time. A
        refusal of the server, or anything else that would happen again, drops it."""
        return isinstance(error, (Stopped, ConnectionLost, TransferInterrupted, RefusedForNow, OSError, LocalWriteFailed, LocalReadFailed))

    @staticmethod
    def _told(error: BaseException, kept: bool, written: int) -> BaseException:
        """A failure after which a part could have been kept says whether it was: the next try continues, or starts from the beginning.
        When nothing was written yet there is nothing to tell."""
        if written and isinstance(error, (ConnectionLost, TransferInterrupted, RefusedForNow, LocalWriteFailed, LocalReadFailed)) and str(error):
            return type(error)(f"{error} {PART_KEPT_TEXT if kept else PART_GONE_TEXT}")
        return error

    def _time(self, path: str, modified: float) -> bool:
        """Whether the date was set."""
        try:
            self.connection.set_time(path, modified)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            self.dates_not_kept += 1  # the file arrived whole; only its date is the server's own
            return False
        return True

    def _rights(self, path: str, mode: str) -> None:
        try:
            self.connection.set_rights(path, mode)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            if not self.options.ignore_rights_errors:
                raise

    # --- upload

    # --- a large file in parts (FC-05)

    @staticmethod
    def _earlier_ranges(key: str, offset: int, size: int, actual: int) -> tuple[tuple[int, int], ...]:
        """The ranges after the first bytes that an earlier split run of this part confirmed, where it is continued (offset) and they are
        all still in the part (actual: its size now). Anything that does not fit counts as nothing: those bytes come again."""
        note = parts.lookup(key)
        if not offset or note is None:
            return ()
        ranges = tuple(item for item in note.ranges if item[1] <= actual)
        return ranges if parts.ranges_valid(ranges, offset, size) else ()

    def _part_size_there(self, temporary: str) -> int:
        try:
            found = self.connection.fresh_details(temporary)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return 0
        return found.size if found is not None and found.size_known else 0

    def _part_holds_bytes(self, temporary: str) -> bool:
        """Whether the server holds something of a part now. Bytes sent are not yet bytes there: after a broken-off FTP upload the server may
        still be taking in what was on its way, so a part it holds nothing of is no part to keep, nor to tell as kept. A size the server
        can't tell counts as something."""
        found = self.connection.fresh_details(temporary)
        return found is not None and (not found.size_known or found.size > 0)

    def _counted(self, amount: int) -> None:
        """A part confirmed these bytes: counted once, from whichever part (each call under the parts' lock)."""
        self.attempt_counted += amount
        self.meter.add(amount)

    def _in_parts(self, spans: Spans, offset: int, size: int, move: Move) -> None:
        """What earlier runs confirmed after the first bytes is counted once; the rest goes over the parts."""
        before = sum(end - start for start, end in spans.rest())
        if before:
            self._counted(before)
        logins = self.logins
        assert logins is not None
        move_in_parts(self.connection, logins, move, spans, size, self._counted, self.limit, self.job.stop_asked)

    def _fetch_in_parts(self, remote: str, temporary: str, offset: int, size: int, spans: Spans) -> None:
        """The part file here is made only where nothing is; a continued one is cut to the size, never shorter, so the ranges stay."""
        if not offset:
            with LocalFile(temporary, "xb", True):
                pass
        elif os.path.getsize(temporary) > size:
            os.truncate(temporary, size)
        self._in_parts(spans, offset, size, lambda connection, start, end, progress, limit: connection.download_range(remote, temporary, start, end, progress, limit))

    def _send_in_parts(self, source: Path, temporary: str, offset: int, size: int, spans: Spans) -> None:
        """The part file there is made only where nothing is; each part writes its ranges into it."""
        if not offset:
            self.connection.make_empty(temporary)
        self._in_parts(spans, offset, size, lambda connection, start, end, progress, limit: connection.upload_range(str(source), temporary, start, end, progress, limit))

    def _owner(self, relative: str) -> str:
        """Which item of the person's action writes a place: this job and the item's path in it."""
        return f"{id(self.job)}\0{relative}"

    def _not_written_by_another(self, place: str, relative: str, claim: bool = False, renamed: bool = False) -> None:
        """Two items of one transfer whose names become one (D-083): the later one is never written over what this transfer wrote; it is
        left out, and its source stays. claim: this item writes the place from now on. renamed: a name the person gave in the question,
        who was asked about what is there (also what this transfer wrote): it is never left out, and takes the place."""
        owner = self._owner(relative)
        if renamed:
            if claim:
                self.job.answers.take(place, owner)
            return
        free = self.job.answers.claim(place, owner) if claim else not self.job.answers.taken(place, owner)
        if not free:
            name = readable(place.rsplit("/", 1)[-1])
            raise (RemoteActionFailed if self.job.upload else LocalActionFailed)(SAME_NAME_TEXT.format(name=name))

    def _upload(self) -> None:
        job = self.job
        source = Path(job.local)
        if self.options.count_first and self.recording is None:
            self.meter.set_total(local_size(source, job.stop_asked, self.passing))  # what was picked always goes; the mask decides inside it
        try:
            present = self.connection.list_folder(job.remote_path)
        except ConnectionLost:
            raise
        except RemoteActionFailed as error:
            there = self.connection.details(job.remote_path)
            if there is None or not there.is_folder:
                raise RemoteActionFailed(NOT_THERE_TEXT.format(folder=job.remote_path)) from error
            present = list[RemoteEntry]()  # a folder that takes files but can't be read (an FTP incoming folder): nothing in it can be seen
            self.unreadable.add(job.remote_path)
        self._upload_into(source, joined(job.remote_path, renamed_for(self.options, source.name)), {entry.name: entry for entry in present}, source.name)

    def _goes_here(self, item: Path) -> bool:
        """Whether an item of this computer goes (FD-01). What can't be looked at goes, and then fails on its own with its own text."""
        if self.passing.everything:
            return True
        try:
            details = item.stat()
        except OSError:
            return True
        folder = item.is_dir() and not item.is_symlink()
        if not self.passing.passes(item.name, folder, str(item.parent), details.st_size, details.st_mtime):
            return False
        return not (folder and self.passing.skip_empty_folders and not self._holds_here(item))

    def _holds_here(self, folder: Path) -> bool:
        """Whether a folder of this computer holds a file that goes, at any depth; each folder is looked at once."""
        key = str(folder)
        if key in self.holds:
            return self.holds[key]
        try:
            items = sorted(folder.iterdir())
        except OSError:
            self.holds[key] = True  # it can't be read: it goes, and fails on its own
            return True
        found = False
        for item in items:
            if self.job.stop_asked.is_set():
                raise Stopped
            try:
                details = item.stat()
            except OSError:
                continue
            if item.is_dir():
                found = not item.is_symlink() and self.passing.passes(item.name, True, key, None, None) and self._holds_here(item)
            else:
                found = item.is_file() and self.passing.passes(item.name, False, key, details.st_size, details.st_mtime)
            if found:
                break
        self.holds[key] = found
        return found

    def _upload_into(self, source: Path, target: str, present: dict[str, RemoteEntry], relative: str, by_hand: bool = False) -> None:
        """present: what the folder this goes into already holds, read once per folder. by_hand: this way sends it, also beside rsync (a
        folder whose name changes in the target, and all inside it)."""
        if self.job.stop_asked.is_set():
            raise Stopped
        name_there = target.rsplit("/", 1)[-1]
        if not safe_name(name_there):
            raise RemoteActionFailed(UNCHECKABLE_NAME_TEXT)  # a listing never shows it: whether the server has it can't be seen
        old = present.get(name_there)
        if not os.path.lexists(source):
            raise RemoteActionFailed(SOURCE_GONE_TEXT)
        by_hand = by_hand or name_there != source.name
        if source.is_file():
            self._upload_file(source, target, present, relative, by_hand=by_hand)
            return
        if source.is_symlink() or not source.is_dir():
            self._not_sent(source, relative)  # links are not followed into other trees
            return
        if old is None and parent_of(target) in self.unreadable:
            old = self._looked_up(target)  # nothing in this folder can be seen: whether the name is taken is asked of this one name
        if old is not None and old.kind_known and not old.is_folder:
            raise RemoteActionFailed(FILE_IN_THE_WAY_TEXT)
        self._not_written_by_another(target, relative, True)
        inside: dict[str, RemoteEntry] = {}
        own = self.recording is None or by_hand  # this way makes and finishes the folder
        if old is None and (own or self.converts):
            self.connection.make_folder(target)  # beside rsync only where this way sends something into it during the look-over
        elif old is not None:
            inside = self._contents(target)  # where its kind can't be seen, a file in its place fails each item on its own
        if self.recording is not None and not by_hand:
            self.recording.append(relative)
        for item in sorted(source.iterdir()):
            if not self._goes_here(item):
                self.left_by_choice += 1
                continue
            self._each(f"{relative}/{item.name}", lambda item=item: self._upload_into(item, joined(target, renamed_for(self.options, item.name)), inside, f"{relative}/{item.name}", by_hand))
        if not own or (old is not None and old.is_link):
            return  # rsync sets the folder itself; a link to a folder is not ours to change
        if self.options.set_rights and old is None:
            self._rights(target, folder_rights(self.options))  # only a folder this upload made: one that was there keeps its rights
        if self.options.keep_timestamps:
            self._time(target, source.stat().st_mtime)  # after its contents, which change the date of a folder (FB-05)
        if self.options.delete_source:
            try:
                source.rmdir()  # only when it ended up empty: what was skipped or refused stays (FB-08)
            except OSError:
                pass

    def _not_sent(self, source: Path, relative: str) -> None:
        """A link to a folder, a link that leads nowhere, or something that is neither a file nor a folder: never sent. Picked on its
        own it fails, saying what it is; inside a folder it is left out, and the end of the job says how many were (never in silence)."""
        link = source.is_symlink()
        if "/" in relative:
            if link and (source.is_dir() or not source.exists()):
                self.links_left += 1
            else:
                self.others_left += 1
            return
        name = readable(source.name)
        if link and source.is_dir():
            raise RemoteActionFailed(LINK_TO_FOLDER_UP_TEXT.format(name=name))
        if link and not source.exists():
            raise RemoteActionFailed(LINK_NOWHERE_UP_TEXT.format(name=name))
        raise RemoteActionFailed(NOT_A_FILE_UP_TEXT.format(name=name))

    def _contents(self, folder: str) -> dict[str, RemoteEntry]:
        """What a server folder holds; a folder that takes files but can't be read holds nothing that can be seen, and each name in it is
        looked up on its own, at every level."""
        try:
            return {entry.name: entry for entry in self.connection.list_folder(folder)}
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            self.unreadable.add(folder)
            return {}

    def _upload_file(self, source: Path, target: str, present: dict[str, RemoteEntry], relative: str, renamed: bool = False, by_hand: bool = False) -> None:
        """renamed: under the name the person gave, which only this way can do. Its relative path holds a NUL, which no name holds.
        by_hand: its name changes in the target, so this way sends it, also beside rsync."""
        folder, name = parent_of(target), target.rsplit("/", 1)[-1]
        old = present.get(name)
        if old is not None and old.special:
            raise RemoteActionFailed(NOT_A_FILE_THERE_TEXT.format(name=readable(name)))  # never replaced as a file, never asked about as one
        if old is not None and old.is_folder and not old.is_link:
            raise RemoteActionFailed(FOLDER_IN_THE_WAY_TEXT)
        if old is not None and old.is_link:
            real = self.connection.resolved(target)
            if real is None or real == target:
                raise RemoteActionFailed(UNSEEN_LINK_TEXT.format(name=readable(name)))  # before anything is written: never a half file
            parent, real_name = real.rsplit("/", 1)
            listing = {entry.name: entry for entry in self.connection.list_folder(parent or "/")}
            if real_name in listing and listing[real_name].is_folder:
                raise RemoteActionFailed(FOLDER_IN_THE_WAY_TEXT)
            self._upload_file(source, real, listing, relative, renamed, by_hand)  # the file it points at is replaced safely; the link stays a link
            return
        if old is None and folder in self.unreadable:
            old = self._looked_up(target)  # nothing in this folder can be seen: whether the name is taken is asked of this one name
        if old is not None and not old.kind_known:
            raise RemoteActionFailed(KIND_UNKNOWN_TEXT.format(name=readable(name)))  # only what is known to be a file is replaced
        self._not_written_by_another(target, relative, renamed=renamed)  # never asked about: what this transfer wrote is not replaced by it
        try:
            details = source.stat()
        except OSError as error:
            raise LocalReadFailed(LOCAL_READ_TEXT.format(reason=error.strerror or str(error))) from error  # gone, or not to be read here
        size = details.st_size
        text = is_text(self.options, source.name)  # a text goes whole: never continued, split or appended (FD-02)
        temporary, part = self._temporary_up(target, present)
        part_there = part is not None
        actual = part.size if part is not None and part.size_known else None
        key = upload_key(self.job.connection, temporary)
        part_offset = None if text else self._continuable(actual, key, upload_source(str(source)), size, details.st_mtime)
        action = Action.REPLACE
        if old is not None:
            blocked = TEXT_NO_APPEND if text else NO_CUT if not self.connection.capabilities.cut else SIZE_UNKNOWN if not old.size_known else ""
            conflict = Conflict(name, folder, True, size, details.st_mtime, old.size, old.modified, part_offset, blocked, True, old.size_known, self.job.connection.name)
            action = self._decide(relative, conflict)
        if action == Action.SKIP:
            self.meter.add(size)
            return
        if action == Action.RENAME:
            if not safe_name(self.new_name):
                raise RemoteActionFailed(NOT_A_NAME_TEXT)  # never a rename without a name: that would be the folder itself
            self._upload_file(source, joined(folder, self.new_name), present, f"{relative}\0{self.new_name}", True, by_hand)
            self.known[relative] = Action.SKIP  # done: a second look (after rsync could not log in) leaves it
            return
        self._not_written_by_another(target, relative, True, renamed)
        if action == Action.APPEND:
            assert old is not None
            if text or not self.connection.capabilities.cut or not old.size_known:
                raise RemoteActionFailed(NO_APPEND_TEXT)  # the question never offers it here; nothing is written
            if self._still_the_one(target, old) is None:  # appended only to the file as the person saw it
                raise RemoteActionFailed(CHANGED_TO_REPLACE_TEXT.format(name=readable(name)))  # gone meanwhile: nothing to add to
            length = self._append_up(source, target)
            self.known[relative] = Action.SKIP
            after = self.connection.file_size(target)
            grew = after is not None and after == length + size  # exactly the source more: nothing lost, nothing twice
            self._remove_source(source, size, details.st_mtime, grew, target)
            return
        offset = self._offset(action, part_offset, size, key)
        if self.recording is not None and not offset and not renamed and not by_hand and not text:
            self.recording.append(relative)  # rsync sends it
            self.recorded_sources[relative] = (size, details.st_mtime)
            if old is None:
                self.recorded_new.add(relative)  # nobody was asked about it: rsync must never replace one that turns up meanwhile
            else:
                self.agreed_there[relative] = (target, old)  # looked at again right before rsync runs
            return
        sending, sent_size = (upload_copy(source, self.options.remove_bom, self.job.stop_asked) if text else (str(source), size))

        def send(offset: int, resumed_by_choice: bool, part_there: bool) -> None:
            if text:
                self._send(source, target, temporary, old, size, details.st_mtime, offset, resumed_by_choice, part_there, present, True, sending, sent_size)
            else:
                self._send(source, target, temporary, old, size, details.st_mtime, offset, resumed_by_choice, part_there, present)

        try:
            tried: set[str] = set()
            while True:
                try:
                    try:
                        send(offset, action == Action.RESUME, part_there)
                    except (ContinuationRefused, PartDoesNotFit):
                        self.restarted += 1  # the server would not continue it, or the continued file did not fit: from the beginning, at once
                        self.meter.take_back(self.attempt_counted)
                        # A part that could not be removed is still there, with its note: it is cleared again, or the next free name is taken.
                        still = _there(lambda: self.connection.fresh_details(temporary) is not None)
                        send(0, False, still)
                    break
                except TemporaryTaken:
                    tried.add(temporary)  # something took it meanwhile, or another Chrono FTP writes it: the next free name
                    temporary, _part = self._temporary_up(target, present, tried)
                    offset, part_there = 0, False
        finally:
            if text:
                Path(sending).unlink(missing_ok=True)  # the converted copy of our own
        if sent_size > size:
            self.meter.take_back(sent_size - size)  # the total counted the source: the converted copy is another size
        elif sent_size < size:
            self.meter.add(size - sent_size)
        self.known[relative] = Action.SKIP
        self.job.sent_as[relative] = (size, details.st_mtime, self.dated)  # looked at again by a later run: sent once more if it changed
        self._note_arrived(relative, size, sent_size)

    def _forget_old_unless_aside(self, aside: str) -> None:
        """The swap failed: where nothing waits under the aside name (the old file was never moved, or was moved back), its note goes, so
        no file that later takes that name is taken for ours. Where that can't be seen now, the note stays, with the old file's size."""
        try:
            there = self.connection.fresh_details(aside)
        except RemoteActionFailed:
            return
        if there is None:
            parts.forget_old(self.job.connection, aside)

    def _taken_here(self, path: str, present: dict[str, RemoteEntry]) -> RemoteEntry | None:
        """What a server folder holds under this name: from its listing, or, in a folder that can't be read, asked of the name itself."""
        folder, name = parent_of(path), path.rsplit("/", 1)[-1]
        if folder in self.unreadable:
            return self._looked_up(path)
        return present.get(name)

    def _looked_up(self, path: str) -> RemoteEntry | None:
        try:
            return self.connection.details(path)
        except ConnectionLost:
            raise
        except RemoteActionFailed as error:
            raise RemoteActionFailed(UNKNOWN_THERE_TEXT.format(name=readable(path.rsplit("/", 1)[-1]))) from error

    def _temporary_up(self, target: str, present: dict[str, RemoteEntry], skip: set[str] | None = None) -> tuple[str, RemoteEntry | None]:
        """Where the upload lands first, and the part already there when it is ours. A name that holds a file this program did not make
        (no note: it may be a real file that only carries our suffix) is never written to nor removed, nor one that another Chrono FTP
        writes now: the next free name is taken. The choice is the same on every try, so an own part is found again."""
        for number in range(1, MOST_NAMES):
            path = _own_there(target, PART_SUFFIX if number == 1 else f"~{number}{PART_SUFFIX}")
            note = parts.lookup(upload_key(self.job.connection, path))
            if (skip is not None and path in skip) or (note is not None and parts.in_use(note)):
                continue
            found = self._taken_here(path, present)
            if found is None:
                return path, None
            if not found.is_folder and note is not None:
                return path, found  # ours: its note says so
        raise RemoteActionFailed(NO_FREE_NAME_TEXT.format(name=readable(target.rsplit("/", 1)[-1])))

    def _free_aside(self, target: str, present: dict[str, RemoteEntry]) -> str:
        """Where the old file waits while the new one takes its place: a name nothing holds, asked of the server right before it is used
        (the listing is older), so nothing that took it meanwhile is ever overwritten."""
        for number in range(1, MOST_NAMES):
            path = _own_there(target, OLD_SUFFIX if number == 1 else f"~{number}{OLD_SUFFIX}")
            if self._taken_here(path, present) is None and not self.connection.name_taken(path):
                return path
        raise RemoteActionFailed(NO_FREE_NAME_TEXT.format(name=readable(target.rsplit("/", 1)[-1])))

    def _still_the_one(self, target: str, old: RemoteEntry) -> RemoteEntry | None:
        """Right before it is replaced: the file there is still the very one the person agreed to replace, as the server has it this
        moment (a file, of the size and date shown); None when it is gone. Anything else changed it meanwhile: nothing is replaced."""
        now = self.connection.fresh_details(target)
        if now is None:
            return None
        name = readable(target.rsplit("/", 1)[-1])
        if now.is_folder or not now.kind_known:
            raise RemoteActionFailed(CHANGED_TO_REPLACE_TEXT.format(name=name))
        if now.size_known and old.size_known and now.size != old.size:
            raise RemoteActionFailed(CHANGED_TO_REPLACE_TEXT.format(name=name))
        if now.modified is not None and old.modified is not None and now.modified != old.modified:
            raise RemoteActionFailed(CHANGED_TO_REPLACE_TEXT.format(name=name))
        return replace(old, size=now.size, size_known=now.size_known) if not old.size_known else old

    def _send(self, source: Path, target: str, temporary: str, old: RemoteEntry | None, size: int, modified: float, offset: int, resumed_by_choice: bool, part_there: bool, present: dict[str, RemoteEntry], text: bool = False, sending: str = "", sent_size: int = -1) -> None:
        """sending: what is sent, sent_size bytes: the source itself (the default), or a text's converted copy of our own (FD-02). size
        and modified are the source's, which a move removes only while it is unchanged."""
        sending = sending or str(source)
        sent_size = size if sent_size < 0 else sent_size
        key = upload_key(self.job.connection, temporary)
        self.dated = False
        keeps = not text and (resumed_by_choice or resumable(self.options, size))
        split = not text and splits(self.options, self.connection, self.logins, True, size)
        earlier = self._earlier_ranges(key, offset, size, self._part_size_there(temporary) if split and offset else 0)
        if not offset:
            if not self._clear_part(key, lambda: self.connection.delete(temporary, False), part_there):
                raise TemporaryTaken(temporary)  # it still has this name: the next free one
        whose = PartRecord(upload_source(str(source)), size, modified, offset, RESUME_ON if resumed_by_choice else self.options.resume, ranges=earlier)
        spans: Spans | None = None  # split in parts: the ranges confirmed
        if parts.claim(key, whose) is False:
            raise TemporaryTaken(temporary)  # another Chrono FTP writes it now: another name
        written = offset  # the bytes at the start of the part known to be there: the server got them in order

        def step(amount: int) -> None:
            nonlocal written
            written += amount
            self.attempt_counted += amount
            self.meter.add(amount)

        verified = False
        placing = False  # from here on the part may have taken its real name: it is no part any more, and never gets a note
        try:
            self.attempt_counted = offset
            if offset:
                self.meter.add(offset)
            try:
                if split:
                    spans = Spans(offset, earlier)
                    self._send_in_parts(source, temporary, offset, size, spans)
                else:
                    self.connection.upload(sending, temporary, step, self.limit, offset, False, not offset)  # a new part only where nothing is
            except NameTaken as taken:
                parts.forget(key)  # the note of a name that is not ours
                raise TemporaryTaken(temporary) from taken
            now = source.stat()
            if (now.st_size, now.st_mtime) != (size, modified):
                raise SourceChanged(CHANGED_HERE_TEXT.format(name=readable(source.name)))
            arrived = self.connection.file_size(temporary)
            if arrived is None:
                self.sizes_unknown += 1  # the server can't tell: it took every byte without an error, but nobody can check it
            elif arrived != sent_size:
                raise (PartDoesNotFit if offset else RemoteActionFailed)(SERVER_SIZE_TEXT.format(name=readable(source.name)))
            verified = arrived is not None
            if offset and self.job.connection.protocol == FTP:
                self.continued_by_size += 1  # FTP can't confirm the first part it kept: a size that fits is all that is known
            if self.options.set_rights:
                self._rights(temporary, file_rights(self.options))
                old = replace(old, permissions="") if old is not None else None  # the chosen rights, not the old file's
            if self.options.keep_timestamps:
                self.dated = self._time(temporary, modified)  # a rename keeps the date
            if old is not None:
                old = self._still_the_one(target, old)  # None: gone meanwhile, so the new file simply takes the name
            if old is None:
                placing = True  # only now may the part take its real name: before, a failure keeps the whole copy for the next try
                try:
                    self.connection.place_new(temporary, target)
                except NameTaken as taken:
                    raise RemoteActionFailed(APPEARED_THERE_TEXT.format(name=readable(target.rsplit("/", 1)[-1]))) from taken
            else:
                aside = self._free_aside(target, present)
                if not parts.remember_old(self.job.connection, aside, target, old.size if old.size_known else -1):
                    raise LocalActionFailed(OLD_NOT_NOTED_TEXT.format(name=readable(target.rsplit("/", 1)[-1])))  # ours only with its note
                placing = True
                try:
                    swapped = self.connection.swap_in(temporary, target, old, aside)
                except BaseException:
                    self._forget_old_unless_aside(aside)
                    raise
                if swapped != Swapped.NOT_REMOVED:
                    parts.forget_old(self.job.connection, aside)  # gone, or someone else's newer version: never ours to tidy away
                if swapped == Swapped.NOT_REMOVED:
                    self.kept_aside += 1  # its note stays: tidying removes it later, next to the new file
                elif swapped == Swapped.CHANGED:
                    self.changed_aside.append((target.rsplit("/", 1)[-1], aside.rsplit("/", 1)[-1]))
        except TemporaryTaken:
            raise
        except BaseException as error:
            if spans is not None:
                written = spans.first()  # the bytes in order from the start, and the ranges after them, from every part
            kept = (keeps or self.job.continue_later) and not text and not placing and self._kept_after(error) and written > 0 and _there(lambda: self._part_holds_bytes(temporary)) and parts.remember(key, self._kept_record(key, whose, min(written, size), spans.rest() if spans is not None else ()))
            if not kept:
                self._let_go(key, whose, lambda: self.connection.delete(temporary, False), lambda: self.connection.fresh_details(temporary) is not None)
            told = self._told(error, kept, written)
            if told is error:
                raise
            raise told from error
        parts.forget(key)
        name = target.rsplit("/", 1)[-1]
        present[name] = RemoteEntry(name, False, sent_size)  # taken now: a later file of this folder never picks it as a free name
        self._remove_source(source, size, modified, verified, target)

    def _append_up(self, source: Path, target: str) -> int:
        """The file's real length now, and the most it can have with all of the source, are noted first (an append starts only once that
        note is stored). When it breaks off while the connection holds, the file is set back to that length at once. When the connection
        is lost, nothing tells whether someone writes to the file before it is back: the next look (undo_broken_appends) then leaves it as
        it is and tells what it holds (all of the append, none, or maybe part of it). The length it had before."""
        login = self.job.connection
        length = self.connection.file_size(target)
        if length is None:
            raise RemoteActionFailed(NO_APPEND_TEXT)  # without its length now, a broken-off append could not be undone
        most = length + source.stat().st_size
        if not parts.remember_append(login, target, length, most):
            raise LocalActionFailed(APPEND_NOT_NOTED_TEXT)
        name = readable(target.rsplit("/", 1)[-1])
        try:
            self.connection.upload(str(source), target, self.meter.add, self.limit, 0, True)
        except BaseException as error:
            try:
                outcome = set_back(self.connection, login, target, AppendNote(length, most, parts.OWNER), True)
            except ConnectionLost as problem:
                raise ConnectionLost(APPEND_LEFT_TEXT.format(name=name)) from problem
            except RemoteActionFailed as problem:
                self._note_the_break(login, target)
                raise RemoteActionFailed(APPEND_UNREAD_TEXT.format(name=name)) from problem
            told = {LEFT: APPEND_CHANGED_TEXT, GONE: APPEND_GONE_TEXT, REFUSED: APPEND_NOT_CUT_TEXT}.get(outcome)
            if told is not None:
                raise RemoteActionFailed(told.format(name=name)) from error
            raise error
        finally:
            parts.append_ended(login, target)
        if not parts.forget_append(login, target):
            parts.mark_append(login, target, parts.DONE)  # it went whole: its note never sets it back, and goes the next time it is looked at
            self.notes_left += 1
        return length

    def _note_the_break(self, login: Connection, target: str) -> None:
        """The file's size and date as the append broke off, where the server tells them: a later look sets it back only while both are
        the same, so nothing written to it since is ever cut."""
        try:
            found = self.connection.fresh_details(target)
        except RemoteActionFailed:
            return
        if found is not None and found.size_known and found.modified is not None:
            parts.mark_append(login, target, parts.BROKEN, found.size, found.modified)

    def _remove_source(self, source: Path, size: int, modified: float, verified: bool, target: str) -> None:
        """A move (FB-08): only when what arrived was checked, and only when the source has exactly the size and date it had when it was
        read, so a file that changed or grew meanwhile keeps its newer bytes. Each reason a source stays is counted and told."""
        if not self.options.delete_source:
            return
        if not verified:
            self.kept_unchecked += 1
            return
        try:
            now = source.stat()
        except OSError:
            return  # gone already: there is nothing left to remove
        if (now.st_size, now.st_mtime) != (size, modified):
            self.kept_changed += 1
            return
        try:
            self.connection.make_durable(target)  # on the server's disk before the only other copy goes, where the server can be asked
        except RemoteActionFailed:  # also a connection that broke off meanwhile: unconfirmed is unchecked, and the original stays
            self.kept_unchecked += 1
            return
        try:
            source.unlink()
        except OSError:
            self.kept_not_removed += 1
            return
        if not self.connection.capabilities.durable:
            self.removed_unflushed += 1  # checked by its size and date; this server can't say it is on its disk

    def _remote_size(self, path: str, entry: RemoteEntry | None, picked: bool = False) -> int:
        """The size of what there is to fetch, each folder read once and nothing kept. Links to folders are not followed: a loop would
        never end."""
        if self.job.stop_asked.is_set():
            raise Stopped
        if entry is not None and ((entry.is_folder and entry.is_link) or entry.special):
            return 0
        if entry is not None and not picked and not self._goes_there(parent_of(path), entry):
            return 0  # left out by the transfer settings: neither counted nor fetched (FD-01); what was picked always goes
        if entry is not None and not entry.is_folder:
            return entry.size
        try:
            inside = self.connection.list_folder(path)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            return 0  # a folder that can't be read counts as nothing: fetching it fails that item alone, with its own text
        return sum(self._remote_size(joined(path, item.name), item) for item in inside)

    def _goes_there(self, folder: str, entry: RemoteEntry) -> bool:
        """Whether an item of the server goes (FD-01); folder: the server folder it is in. What can't be looked at goes, and then fails on
        its own with its own text."""
        if self.passing.everything:
            return True
        folder_kind = entry.is_folder and not entry.is_link
        size = entry.size if entry.size_known else None
        if not self.passing.passes(entry.name, folder_kind, folder, size, entry.modified):
            return False
        return not (folder_kind and self.passing.skip_empty_folders and not self._holds_there(joined(folder, entry.name)))

    def _holds_there(self, path: str) -> bool:
        """Whether a server folder holds a file that goes, at any depth; each folder is read once for it."""
        if path in self.holds:
            return self.holds[path]
        try:
            inside = self.connection.list_folder(path)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            self.holds[path] = True  # it can't be read: it goes, and fails on its own
            return True
        found = False
        for item in inside:
            if self.job.stop_asked.is_set():
                raise Stopped
            if item.special or (item.is_folder and item.is_link):
                continue
            size = item.size if item.size_known else None
            if item.is_folder:
                found = self.passing.passes(item.name, True, path, None, None) and self._holds_there(joined(path, item.name))
            else:
                found = self.passing.passes(item.name, False, path, size, item.modified)
            if found:
                break
        self.holds[path] = found
        return found

    def _download(self) -> None:
        job = self.job
        parent, name = parent_of(job.remote_path), job.remote_path.rsplit("/", 1)[-1]
        entry = next((item for item in self.connection.list_folder(parent) if item.name == name), None)
        if entry is None:
            try:
                found = self.connection.details_or_refusal(job.remote_path)  # a name the listing leaves out is asked of itself
            except ConnectionLost:
                raise
            except RemoteActionFailed as error:
                raise RemoteActionFailed(UNREADABLE_THERE_TEXT.format(name=readable(name))) from error  # there or not: it can't be read
            if found is None:
                raise RemoteActionFailed(GONE_THERE_TEXT.format(name=readable(name)))  # gone meanwhile: nothing is made here
            entry = replace(found, name=name)
        if self.options.count_first and self.recording is None:
            self.meter.set_total(self._remote_size(job.remote_path, entry, True))  # what was picked always goes; the mask decides inside it
        self._download_item(job.remote_path, entry, os.path.join(job.local, renamed_for(self.options, job.name)), job.name)

    def _download_item(self, remote: str, entry: RemoteEntry | None, local: str, relative: str, by_hand: bool = False) -> None:
        """by_hand: this way fetches it, also beside rsync (an item whose name changes here, and all inside it)."""
        if self.job.stop_asked.is_set():
            raise Stopped
        if entry is not None and entry.is_folder and entry.is_link:
            if "/" not in relative:
                raise RemoteActionFailed(LINK_TO_FOLDER_DOWN_TEXT.format(name=readable(entry.name)))  # picked on its own: never a silent Done
            self.links_left += 1  # inside a folder: left out, and the end of the job says how many
            return
        if entry is not None and entry.special:  # reading it could wait forever, and nothing like it is made here
            if "/" not in relative:
                raise RemoteActionFailed(NOT_A_FILE_DOWN_TEXT.format(name=readable(entry.name)))
            self.others_left += 1
            return
        by_hand = by_hand or os.path.basename(local) != remote.rsplit("/", 1)[-1]
        if entry is not None and not entry.is_folder:
            self._download_file(remote, entry, local, relative, by_hand=by_hand)
            return
        if os.path.lexists(local) and not os.path.isdir(local):
            raise LocalActionFailed(FILE_HERE_TEXT)
        self._not_written_by_another(local, relative, True)
        own = self.recording is None or by_hand  # this way makes and finishes the folder
        if self.recording is not None and not by_hand:
            self.recording.append(relative)
        if own or self.converts:
            Path(local).mkdir(parents=True, exist_ok=True)  # beside rsync only where this way fetches something into it during the look-over
        inside, left_out = self.connection.list_folder_all(remote)
        for name in left_out:
            self._left_out(f"{relative}/{name}", UNSAFE_NAME_TEXT)  # never in silence: this one item is told, the rest goes
        for item in inside:
            if not (item.special or (item.is_folder and item.is_link)) and not self._goes_there(remote, item):
                self.left_by_choice += 1
                continue
            self._each(f"{relative}/{item.name}", lambda item=item: self._download_item(joined(remote, item.name), item, os.path.join(local, renamed_for(self.options, item.name)), f"{relative}/{item.name}", by_hand))
        if not own:
            return
        if self.options.keep_timestamps and entry is not None and entry.modified is not None and not os.path.islink(local):
            self._local_time(local, entry.modified)  # never through a link on this computer: what it leads to is not this folder
        if self.options.delete_source:
            try:
                self.connection.remove_empty_folder(remote)  # only when it ended up empty (FB-08)
            except ConnectionLost:
                raise
            except RemoteActionFailed:
                pass

    def _local_time(self, path: str, modified: float) -> bool:
        """Whether the date was set."""
        try:
            os.utime(path, (modified, modified))
        except (OverflowError, OSError, ValueError):
            self.dates_not_kept += 1  # a date from the server is input (OR-15)
            return False
        return True

    def _download_file(self, remote: str, entry: RemoteEntry, local: str, relative: str, renamed: bool = False, by_hand: bool = False) -> None:
        if entry.is_link and not renamed:
            if self.connection.details(remote) is None:
                raise RemoteActionFailed(LINK_NOWHERE_TEXT)  # also before rsync, which would fail on it, and with it everything else
            real = self.connection.file_size(remote)  # a listing may give a link's own size: what is fetched is the file it leads to
            entry = replace(entry, size=real if real is not None else 0, size_known=real is not None)
        if os.path.islink(local) and not os.path.exists(local):
            raise LocalActionFailed(LOCAL_LINK_NOWHERE_TEXT.format(name=readable(os.path.basename(local))))  # nothing goes where it would lead
        target = os.path.realpath(local)  # a link stays a link: what it points at gets the new content
        if os.path.isdir(target):
            raise LocalActionFailed(FOLDER_HERE_TEXT)
        if os.path.lexists(target) and not os.path.isfile(target):  # never replaced as a file, never written into, never asked about
            raise LocalActionFailed(NOT_A_FILE_HERE_TEXT.format(name=readable(os.path.basename(local))))
        self._not_written_by_another(target, relative, renamed=renamed)  # never asked about: what this transfer wrote is not replaced by it
        temporary = self._temporary_down(target)
        part_there = os.path.isfile(temporary)
        key = download_key(temporary)
        size = entry.size if entry.size_known else None
        text = is_text(self.options, entry.name)  # a text comes whole: never continued, split or appended (FD-02)
        part_offset = None if text else self._continuable(os.path.getsize(temporary) if part_there else None, key, download_source(self.job.connection, remote), size, entry.modified)
        exists = os.path.lexists(target)
        seen: tuple[int, float] | None = None  # the file here as the question showed it
        action = Action.REPLACE
        if exists:
            here = os.stat(target)
            seen = (here.st_size, here.st_mtime)
            conflict = Conflict(entry.name, os.path.dirname(local), False, entry.size, entry.modified, here.st_size, here.st_mtime, part_offset, TEXT_NO_APPEND if text else "", source_size_known=entry.size_known, connection=self.job.connection.name)
            action = self._decide(relative, conflict)
        if action == Action.SKIP:
            self.meter.add(entry.size)
            return
        if action == Action.RENAME:
            if not safe_name(self.new_name):
                raise RemoteActionFailed(NOT_A_NAME_TEXT)  # never a rename without a name: that would be the folder itself
            self._download_file(remote, entry, os.path.join(os.path.dirname(local), self.new_name), f"{relative}\0{self.new_name}", True, by_hand)
            self.known[relative] = Action.SKIP
            return
        self._not_written_by_another(target, relative, True, renamed)
        if action == Action.APPEND:
            if text:
                raise LocalActionFailed(NO_TEXT_APPEND_TEXT)  # the question never offers it for a text; nothing is written
            try:
                now = os.stat(target)
            except FileNotFoundError:
                now = None
            if now is None or (now.st_size, now.st_mtime) != seen:  # appended only to the file as the person saw it
                raise LocalActionFailed(CHANGED_HERE_NOT_REPLACED_TEXT.format(name=readable(os.path.basename(target))))
            before = os.path.getsize(target)
            self.connection.download(remote, target, self.meter.add, self.limit, 0, True)
            self.known[relative] = Action.SKIP
            grew = entry.size_known and os.path.getsize(target) == before + entry.size  # exactly the source more
            if grew and self.options.delete_source:
                durable(target)
            self._remove_remote(remote, entry, grew)
            return
        offset = self._offset(action, part_offset, entry.size, key)
        if self.recording is not None and not offset and not renamed and not by_hand and not text:
            self.recording.append(relative)
            self.recorded_sources[relative] = (entry.size, entry.modified)
            if not exists:
                self.recorded_new.add(relative)  # nobody was asked about it: rsync must never replace one that turns up meanwhile
            elif seen is not None:
                self.agreed_here[relative] = (target, seen)  # looked at again right before rsync runs
            return
        tried: set[str] = set()
        while True:
            try:
                try:
                    self._fetch(remote, entry, target, temporary, seen, offset, action == Action.RESUME, part_there, text)
                except (ContinuationRefused, PartDoesNotFit):
                    self.restarted += 1  # the server would not continue it, or the continued file did not fit: from the beginning, at once
                    self.meter.take_back(self.attempt_counted)
                    self._fetch(remote, entry, target, temporary, seen, 0, False, os.path.lexists(temporary), text)  # a part not removed stays noted
                break
            except TemporaryTaken:
                tried.add(temporary)  # something took it meanwhile, or another Chrono FTP writes it: the next free name
                temporary = self._temporary_down(target, tried)
                offset, part_there = 0, False
        self.known[relative] = Action.SKIP
        self.job.sent_as[relative] = (entry.size, entry.modified, self.dated)  # looked at again by a later run: fetched once more if it changed
        self._note_arrived(relative, entry.size, self.converted_size if text else entry.size)

    def _note_arrived(self, relative: str, size: int, there: int) -> None:
        """What the target really got, where a conversion made it another size than its source."""
        if there != size:
            self.job.arrived_as[relative] = there
        else:
            self.job.arrived_as.pop(relative, None)

    def _temporary_down(self, target: str, skip: set[str] | None = None) -> str:
        """Where a download lands first: a name nothing holds, or our own part (it has its note). A file this program did not make (a real
        file that only carries our suffix, perhaps fetched a moment ago) is never written to nor removed, nor one that another Chrono FTP
        writes now."""
        for number in range(1, MOST_NAMES):
            path = _own_here(target, PART_SUFFIX if number == 1 else f"~{number}{PART_SUFFIX}")
            note = parts.lookup(download_key(path))
            if (skip is not None and path in skip) or (note is not None and parts.in_use(note)):
                continue
            if not os.path.lexists(path) or (os.path.isfile(path) and note is not None):
                return path
        raise LocalActionFailed(NO_FREE_NAME_TEXT.format(name=readable(os.path.basename(target))))

    def _fetch(self, remote: str, entry: RemoteEntry, target: str, temporary: str, seen: tuple[int, float] | None, offset: int, resumed_by_choice: bool, part_there: bool, text: bool = False) -> None:
        """seen: the size and date of the file here that the person agreed to replace; None where there was none. text: it arrives
        whole as it is on the server, and only then are its line endings converted, before it takes its real name (FD-02)."""
        exists = seen is not None
        key = download_key(temporary)
        self.dated = False
        keeps = not text and (resumed_by_choice or resumable(self.options, entry.size)) and entry.size_known and entry.modified is not None and math.isfinite(entry.modified)
        split = not text and entry.size_known and splits(self.options, self.connection, self.logins, False, entry.size)
        earlier = self._earlier_ranges(key, offset, entry.size, os.path.getsize(temporary) if split and offset and os.path.isfile(temporary) else 0)
        if not offset:
            if not self._clear_part(key, lambda: os.remove(temporary), part_there):
                raise TemporaryTaken(temporary)  # it still has this name: the next free one
        whose = PartRecord(download_source(self.job.connection, remote), entry.size, entry.modified if entry.modified is not None and math.isfinite(entry.modified) else 0.0, offset, RESUME_ON if resumed_by_choice else self.options.resume, ranges=earlier)
        spans: Spans | None = None  # split in parts: the ranges confirmed
        if parts.claim(key, whose) is False:
            raise TemporaryTaken(temporary)  # another Chrono FTP writes it now: another name
        written = offset  # the bytes at the start of the part known to be there: written in order
        placing = False  # from here on the part may have taken its real name: it is no part any more, and never gets a note

        def step(amount: int) -> None:
            nonlocal written
            written += amount
            self.attempt_counted += amount
            self.meter.add(amount)

        try:
            self.attempt_counted = offset
            if offset:
                self.meter.add(offset)
            try:
                if split:
                    spans = Spans(offset, earlier)
                    self._fetch_in_parts(remote, temporary, offset, entry.size, spans)
                else:
                    self.connection.download(remote, temporary, step, self.limit, offset, False, not offset)  # a new part only where nothing is
            except NameTaken as taken:
                parts.forget(key)  # the note of a name that is not ours
                raise TemporaryTaken(temporary) from taken
            if entry.size_known and os.path.getsize(temporary) != entry.size:
                raise (PartDoesNotFit if offset else SourceChanged)(CHANGED_THERE_TEXT.format(name=readable(entry.name)))
            if not self._unchanged_there(remote, entry):
                raise SourceChanged(CHANGED_THERE_TEXT.format(name=readable(entry.name)))  # read while it was rewritten: never put in place
            if not entry.size_known:
                self.sizes_unknown += 1  # the server gave no size: everything it sent is here, but nobody can check it
            if text:
                self.converted_size = convert_fetched(temporary, readable(entry.name), self.options.remove_bom, self.job.stop_asked)  # checked as it came, then converted
            if exists:
                shutil.copymode(target, temporary)  # the new file keeps the old one's permissions
            if self.options.keep_timestamps and entry.modified is not None:
                self.dated = self._local_time(temporary, entry.modified)
            placing = True
            if seen is not None:
                self._replace_here(temporary, target, seen)  # the person chose to replace it
            else:
                self._place_new_here(temporary, target)
            if self.options.delete_source:
                durable(target)
        except TemporaryTaken:
            raise
        except BaseException as error:
            if spans is not None:
                written = spans.first()  # the bytes in order from the start, and the ranges after them, from every part
            kept = (keeps or self.job.continue_later) and not text and not placing and self._kept_after(error) and written > 0 and entry.modified is not None and os.path.isfile(temporary) and parts.remember(key, self._kept_record(key, whose, min(written, entry.size), spans.rest() if spans is not None else ()))
            if not kept:
                self._let_go(key, whose, lambda: Path(temporary).unlink(missing_ok=True), lambda: os.path.lexists(temporary))
            told = self._told(error, kept, written)
            if told is error:
                raise
            raise told from error
        parts.forget(key)
        self._remove_remote(remote, entry, entry.size_known)

    def _unchanged_there(self, remote: str, entry: RemoteEntry) -> bool:
        """After the fetch, the server's file still has the size and date it had when it was read, each told the same way as then: a file
        rewritten meanwhile, even at the same size, gives a copy of neither version."""
        found = self.connection.fresh_details(remote)
        if found is None:
            return False
        size = self.connection.file_size(remote) if entry.is_link else found.size if found.size_known else None
        if entry.size_known and size is not None and size != entry.size:
            return False
        dates = (entry.modified, found.modified)
        return any(date is None or not math.isfinite(date) for date in dates) or found.modified == entry.modified  # no date: nothing to compare

    @staticmethod
    def _replace_here(temporary: str, target: str, seen: tuple[int, float]) -> None:
        """The fetched file takes the place of the file here, but only while that is still the one the person agreed to replace (its
        size and date as the question showed them). Changed meanwhile, it stays as it is, and what was fetched is kept under a free name.
        A change in the very last moment is not seen: this computer can't replace a file only while it is unchanged."""
        try:
            now = os.stat(target)
            same = (now.st_size, now.st_mtime) == seen
        except FileNotFoundError:
            FileWay._place_new_here(temporary, target)  # gone meanwhile: the name is simply taken, never by replacing
            return
        if not same:
            kept = FileWay._free_here(target)
            FileWay._place_new_here(temporary, kept)
            raise LocalActionFailed(CHANGED_HERE_REPLACE_TEXT.format(name=readable(os.path.basename(target)), kept=readable(os.path.basename(kept))))
        os.replace(temporary, target)

    @staticmethod
    def _place_new_here(temporary: str, target: str) -> None:
        """A fetched file takes a name nothing had at the start, and never replaces what took it meanwhile: a hard link fails on a taken
        name. Where the disk has no hard links, the name is looked up right before (a file that takes it in that last moment is replaced
        all the same, a limit of such disks)."""
        try:
            os.link(temporary, target)
        except FileExistsError:
            kept = FileWay._free_here(target)
            os.link(temporary, kept)
            os.unlink(temporary)
            raise LocalActionFailed(APPEARED_HERE_TEXT.format(name=readable(os.path.basename(target)), kept=readable(os.path.basename(kept)))) from None
        except OSError as error:
            if error.errno not in NO_HARD_LINKS:
                raise
            if os.path.lexists(target):
                kept = FileWay._free_here(target)
                os.rename(temporary, kept)
                raise LocalActionFailed(APPEARED_HERE_TEXT.format(name=readable(os.path.basename(target)), kept=readable(os.path.basename(kept)))) from None
            os.rename(temporary, target)
            return
        os.unlink(temporary)

    @staticmethod
    def _free_here(target: str) -> str:
        stem, extension = os.path.splitext(target)
        for number in range(2, MOST_NAMES):
            path = _own_here(stem, f"~{number}{extension}")
            if not os.path.lexists(path):
                return path
        raise LocalActionFailed(NO_FREE_NAME_TEXT.format(name=readable(os.path.basename(target))))

    def _remove_remote(self, remote: str, entry: RemoteEntry, verified: bool) -> None:
        """A move (FB-08): only when what arrived was checked, and only when the server still has the file at exactly the size and date it
        had when it was read, looked up for this one file. Each reason a source stays is counted and told."""
        if not self.options.delete_source:
            return
        if not verified:
            self.kept_unchecked += 1
            return
        now = self.connection.fresh_details(remote)
        if now is None:
            return  # gone already
        if (now.size, now.modified, now.is_folder, now.size_known) != (entry.size, entry.modified, False, True):
            self.kept_changed += 1
            return
        try:
            self.connection.delete(remote, False)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            self.kept_not_removed += 1


SET_BACK: str = "set back"
UNCHANGED: str = "unchanged"
LEFT: str = "left"
GONE: str = "gone"
REFUSED: str = "refused"
WHOLE: str = "whole"
MAYBE_PART: str = "maybe part"


def set_back(connection: RemoteConnection, login: Connection, path: str, note: AppendNote, just_now: bool = False) -> str:
    """A file an append left half done gets its noted length back, only where nothing but our own append can be in it:
    1. just_now (it broke off this moment): its length lies from the noted length up to the most the whole append makes it, that one
       included, as a failed request may have left a gap anywhere in it;
    2. a note of how it broke off with the file's size and date then: both are the same now, within that span;
    3. a note whose end is unknown (the connection or the program stopped): never, as nothing tells whether someone wrote to the file
       since. At the full length the append arrived (WHOLE); at its old length none of it did (UNCHANGED); anything else may hold part of
       it, and is left as it is and told (MAYBE_PART).
    An append that went whole is never cut. Anything else changed the file since: it is left as it is. A file that is gone, or that the
    server won't cut, can't be helped by trying again. In all these the note goes. SET_BACK, UNCHANGED, WHOLE, MAYBE_PART, LEFT, GONE or
    REFUSED; raises when it can't be reached now, or its length can't be told (the note then stays)."""
    try:
        now = connection.file_size(path)
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        now = None  # gone, or no size to tell: the details say which
    found = connection.fresh_details(path) if now is None or note.state == parts.BROKEN else None
    if now is None:
        if found is not None:
            raise RemoteActionFailed(NO_APPEND_TEXT)  # there, with no length to tell: the next look may tell it
        parts.forget_append(login, path)
        return GONE
    if note.state == parts.DONE:
        outcome = UNCHANGED
    elif just_now or note.state == parts.BROKEN:
        same = just_now or (found is not None and (now, found.modified) == (note.size_then, note.date_then))
        outcome = LEFT if not same or not note.start <= now <= note.most else SET_BACK if now > note.start else UNCHANGED
    else:
        outcome = UNCHANGED if now == note.start else WHOLE if now == note.most else MAYBE_PART if note.start < now < note.most else LEFT
    if outcome == SET_BACK:
        try:
            connection.cut(path, note.start)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            outcome = REFUSED
    parts.forget_append(login, path)
    return outcome


def undo_broken_appends(connection: RemoteConnection, login: Connection, stop: threading.Event | None = None) -> dict[str, int]:
    """The files of this login that an append broke off in are looked at (set_back), and their notes go; a file that can't be reached yet
    keeps its note for the next time. Called before each transfer, by one job of the login at a time (other jobs may run meanwhile;
    one that waits for its turn ends at its stop); each note is
    looked up again right before it is acted on, so one another job set back, or an append it started since, is left alone. How many ended
    each way that is to be told: SET_BACK, LEFT, REFUSED, WHOLE and MAYBE_PART."""
    counts = dict.fromkeys((SET_BACK, LEFT, REFUSED, WHOLE, MAYBE_PART), 0)
    if not connection.capabilities.cut:
        return counts
    with _appends_lock:
        turn = _appends_looked_at.setdefault((login.host, login.port, login.user), threading.Lock())
    while not turn.acquire(timeout=TURN_SECONDS):
        if stop is not None and stop.is_set():
            raise Stopped
    try:
        return _undo_broken_appends(connection, login, counts)
    finally:
        turn.release()


TURN_SECONDS: float = 0.2  # how often a job that waits for its turn to look at the appends sees whether it was stopped
_appends_lock: threading.Lock = threading.Lock()  # for the table below
_appends_looked_at: dict[tuple[str, int, str], threading.Lock] = {}  # per login: one job at a time looks at its appends that broke off


def _undo_broken_appends(connection: RemoteConnection, login: Connection, counts: dict[str, int]) -> dict[str, int]:
    for path, note in parts.broken_appends(login).items():
        if parts.broken_appends(login).get(path) != note:
            continue  # set back meanwhile, or an append of it runs now
        try:
            outcome = set_back(connection, login, path, note)
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            continue  # gone, or not allowed now: the note stays
        if outcome in counts:
            counts[outcome] += 1
    return counts
