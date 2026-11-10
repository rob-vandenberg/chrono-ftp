"""What this computer noted about each part file it kept (FB-02). A note belongs to one part file, found by where that part is: on a server
(the login and the path) or on this computer (the path). It says which source the part's bytes came from, the size and date that source
had (each read from the source's own side, so no two clocks are compared), and how many bytes at the start of the part were confirmed
written. Writing into a part replaces its note. The file is input: every note is checked on reading, and a bad one is left out (OR-15)."""

import copy
import json
import math
import os
import sys
import atexit
import threading
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TextIO, cast

if sys.platform != "win32":
    import fcntl

from chrono_ftp.config.connections import config_folder
from chrono_ftp.core.connection import Connection

# --- Version ------------------------------------------------------------
__version__ = "parts.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: A part written by several connections at once (FC-05) notes the ranges after its first bytes that were confirmed too; ranges
#          that don't fit the part's size are left out on reading
# v0.0.12: Initial: per part file, the source it holds bytes of, that source's size and date, and the bytes confirmed written; kept in
#          memory and read again only when the file changed on the disk; the length of a file before an append, until it is safe; the
#          files moved aside; written to the disk before they take their name, one change at a time also across processes; an append's
#          note also holds the most the file can have with all of the append; the files moved aside are noted in a journal, one small
#          line on the disk per event, written afresh now and then; an append's note names the Chrono FTP that appends, and an append that
#          still runs, here or in another one, is never taken for broken off; whether a note was stored is told; an append's note says how
#          it ended: broken off, with the file's size and date then, or whole; a part's note keeps the resume setting it was kept by; half
#          a line at the journal's end never swallows the next one; a Chrono FTP removes its lock file when it ends, and those of ended
#          ones go when the notes are read; a part is noted as running before its first byte, with the Chrono FTP that writes it; that note
#          and its removal are written without waiting for the disk, only a kept part's note is; rsync jobs whose folders of unfinished
#          copies are ours; every number read from a note file is checked so that none can make reading fail (a number no float holds is
#          no date), and a file is written again without what could not be read in it; a mark of how an append ended never stands over
#          a newer note of another append; a running part's note is a line in a journal of its own, so a file costs no rewrite of all notes

FILE_NAME: str = "parts.json"
APPENDS_FILE_NAME: str = "appends.json"
OLDS_FILE_NAME: str = "olds.log"  # a journal: one line per file moved aside or forgotten
CLAIMS_FILE_NAME: str = "claims.log"  # a journal of the parts being written now: one line per claim or forget, written afresh now and then
PARTIALS_FILE_NAME: str = "partials.json"  # where rsync may keep unfinished copies in a folder of its own, per job
JOURNAL_SLACK: int = 200  # lines beyond twice the notes that hold before the journal is written afresh
LOCK_FILE_NAME: str = "notes.lock"
OWNERS_FOLDER: str = "running"  # one lock file per Chrono FTP that runs, held while it runs
OWNER: str = uuid.uuid4().hex  # this Chrono FTP
OWNER_SUFFIX: str = ".lock"
HIGHEST_RECORDS: int = 1000  # the oldest go first: a part this old is rarely continued, and the file stays small
HIGHEST_SIZE: int = 2**63 - 1

_lock: threading.Lock = threading.Lock()
_file_locked: bool = False  # this process holds the lock across processes now (under _lock)
_owner_file: tuple[Path, TextIO] | None = None  # this Chrono FTP's lock file, in the settings folder it was made in
_running: set[str] = set()  # the appends running in this Chrono FTP now
_marked: dict[str, "AppendNote"] = {}  # how appends of this Chrono FTP ended, also where that could not be stored


@dataclass(frozen=True)
class PartRecord:
    source: str  # which source the bytes came from: see upload_source and download_source
    size: int  # the source's size when the part was kept
    modified: float  # the source's date then, from the source's own side
    part_size: int  # the bytes at the start of the part that were confirmed written, in order
    resume: str = ""  # the resume setting the part was kept by: it is judged by that, never by what Settings say later
    owner: str = ""  # the Chrono FTP writing it now (running); empty once it is kept
    running: bool = False  # being written: noted before its first byte, so even a Chrono FTP that stops at once leaves no part of nobody's
    ranges: tuple[tuple[int, int], ...] = ()  # split over several connections (FC-05): further ranges [start, end) confirmed written,
    # each after part_size, in order and apart


def ranges_valid(ranges: tuple[tuple[int, int], ...], part_size: int, size: int) -> bool:
    """Each range lies after the first bytes and within the size, in order, none touching the next."""
    end = part_size
    for start, stop in ranges:
        if not end < start < stop <= size:
            return False
        end = stop
    return True


def _ranges(value: object, part_size: int, size: int) -> tuple[tuple[int, int], ...]:
    """Ranges from a note file, which is input: anything that is not a list of fitting ranges counts as none (only the first bytes)."""
    if not isinstance(value, list):
        return ()
    found: list[tuple[int, int]] = []
    for item in cast(list[object], value):
        pair = cast(list[object], item) if isinstance(item, list) else []
        if len(pair) != 2:
            return ()
        start, stop = _whole(pair[0]), _whole(pair[1])
        if start is None or stop is None:
            return ()
        found.append((start, stop))
    ranges = tuple(found)
    return ranges if ranges_valid(ranges, part_size, size) else ()


def upload_key(connection: Connection, remote_part: str) -> str:
    """A part on a server: the login and its path there."""
    return json.dumps(["up", connection.host, connection.port, connection.user, remote_part], ensure_ascii=True)


def download_key(local_part: str) -> str:
    """A part on this computer: its path, whatever server it came from."""
    return json.dumps(["down", os.path.abspath(local_part)], ensure_ascii=True)


def upload_source(local: str) -> str:
    return json.dumps(["here", os.path.abspath(local)], ensure_ascii=True)


def download_source(connection: Connection, remote: str) -> str:
    return json.dumps(["there", connection.host, connection.port, connection.user, remote], ensure_ascii=True)


def _whole(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= HIGHEST_SIZE else None


def _moment(value: object) -> float | None:
    """A date from a note file, which is input: a number a float can hold and that is finite; anything else (also a number of 400
    digits, which no float holds) is no date."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        moment = float(value)
    except OverflowError:
        return None
    return moment if math.isfinite(moment) else None


def _record(value: object) -> PartRecord | None:
    if not isinstance(value, dict):
        return None
    data = cast(dict[str, object], value)
    source, size, part_size, modified = data.get("source"), _whole(data.get("size")), _whole(data.get("part_size")), data.get("modified")
    if not isinstance(source, str) or size is None or part_size is None or part_size > size:
        return None
    moment = _moment(modified)
    if moment is None:
        return None
    resume, owner, running = data.get("resume", ""), data.get("owner", ""), data.get("running", False)
    return PartRecord(source, size, moment, part_size, resume if isinstance(resume, str) else "", owner if isinstance(owner, str) else "", running is True, _ranges(data.get("ranges"), part_size, size))


_cache: dict[str, tuple[tuple[int, int], object]] = {}  # per file: its change time and size when read, and what was read


def _stamp_of(path: Path) -> tuple[int, int]:
    try:
        details = path.stat()
    except OSError:
        return (-1, -1)
    return (details.st_mtime_ns, details.st_size)


def _read[T](name: str, checked: Callable[[object], T]) -> T:
    """The file, read and checked; read again only when it changed on the disk (another program, another Chrono FTP), else the copy in
    memory. What is handed out is a copy, so no caller changes what is kept."""
    path = config_folder() / name
    stamp = _stamp_of(path)
    cached = _cache.get(name)
    if cached is None or cached[0] != stamp:
        try:
            data: object = json.loads(path.read_text(encoding="utf-8")) if stamp != (-1, -1) else {}
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            data = None
        cached = (stamp, checked(data))
        _cache[name] = cached
        if isinstance(data, dict) and isinstance(cached[1], dict) and len(cast(dict[object, object], cached[1])) < len(cast(dict[object, object], data)):
            cleaner = _CLEANERS.get(name)
            if cleaner is not None:
                with _file_lock():
                    if _stamp_of(path) == stamp:  # unchanged since it was read: nothing another Chrono FTP wrote is lost
                        cleaner(cached[1])  # what could not be read is left out of the file too, so it never comes back
    return copy.copy(cast(T, cached[1]))


def _write(name: str, data: dict[str, object], checked: object, durable: bool = True) -> bool:
    """data goes to the file; durable: on the disk before it takes the file's name (else the system writes it on its own, a moment
    later); checked is what reading it back gives, kept in memory for the file as it now is. False when it could not be stored: the caller
    decides what that means (a part is then simply not continued; an append does not start)."""
    path = config_folder() / name
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            file.write(json.dumps(data))
            file.flush()
            if durable:
                os.fsync(file.fileno())
        os.replace(temporary, path)
        if durable:
            _sync_folder(path.parent)
        details = path.stat()
    except OSError:
        _cache.pop(name, None)
        return False
    _cache[name] = ((details.st_mtime_ns, details.st_size), copy.copy(checked))
    return True


def _sync_folder(folder: Path) -> None:
    try:
        descriptor = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        return  # a system that can't open a folder like this keeps its folders on its own
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@contextmanager
def _locked() -> Generator[None]:
    """One change of the notes at a time: in this process (the threads) and across processes (another Chrono FTP), so no change is lost.
    Without a lock file (a folder that can't be written), only this process is kept in order: its change can't be stored anyway."""
    with _lock, _file_lock():
        yield


@contextmanager
def _file_lock() -> Generator[None]:
    """The lock across processes, taken once: called again while this process holds it (a note read while a change is made), it is
    already held. Called with _lock held."""
    global _file_locked
    if _file_locked:
        yield
        return
    handle = None
    try:
        folder = config_folder()
        folder.mkdir(parents=True, exist_ok=True)
        handle = (folder / LOCK_FILE_NAME).open("a")
        if sys.platform != "win32":
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    except OSError:
        pass
    _file_locked = True
    try:
        yield
    finally:
        _file_locked = False
        if handle is not None:
            handle.close()  # also lets go of the lock


def _load() -> dict[str, PartRecord]:
    return _read(FILE_NAME, _records)


def _records(data: object) -> dict[str, PartRecord]:
    found: dict[str, PartRecord] = {}
    if not isinstance(data, dict):
        return found
    for name, value in cast(dict[object, object], data).items():
        record = _record(value)
        if isinstance(name, str) and record is not None:
            found[name] = record
    return found


def _save(records: dict[str, PartRecord], durable: bool = True) -> bool:
    kept = dict(list(records.items())[-HIGHEST_RECORDS:])
    return _write(FILE_NAME, {name: _record_data(record) for name, record in kept.items()}, kept, durable)


def append_key(connection: Connection, remote: str) -> str:
    return json.dumps([connection.host, connection.port, connection.user, remote], ensure_ascii=True)


RUNNING: str = "running"  # under way, or its end is not known (the connection or the program stopped)
BROKEN: str = "broken"  # it broke off, and the file's size and date then are noted
DONE: str = "done"  # it went whole; only its note could not be removed


@dataclass(frozen=True)
class AppendNote:
    start: int  # the file's length before the append
    most: int  # the most it can have with all of the append
    owner: str  # the Chrono FTP that appends
    state: str = RUNNING
    size_then: int = -1  # BROKEN: the file's size when it broke off
    date_then: float | None = None  # BROKEN: the file's date then, as the server tells it


def _appends() -> dict[str, AppendNote]:
    found = _read(APPENDS_FILE_NAME, _spans)
    for key, mark in list(_marked.items()):
        stored = found.get(key)
        if stored is not None and (stored.owner, stored.start, stored.most) == (mark.owner, mark.start, mark.most):
            found[key] = mark  # the same append: this Chrono FTP knows better how it ended, also when it could not store that
        else:
            del _marked[key]  # a newer note of another append (another Chrono FTP, or a new start) stands: this mark is stale
    return found


def _spans(data: object) -> dict[str, AppendNote]:
    found: dict[str, AppendNote] = {}
    if isinstance(data, dict):
        for name, value in cast(dict[object, object], data).items():
            fields = cast(list[object], value) if isinstance(value, list) else []
            if not isinstance(name, str) or len(fields) != 6:
                continue
            start, most, owner, state, size_then, date_then = fields
            start, most, size_then = _whole(start), _whole(most), -1 if size_then == -1 else _whole(size_then)
            date = _moment(date_then)
            if date_then is not None and date is None:
                continue  # a date that is no date: the note can't be trusted
            if start is not None and most is not None and start <= most and isinstance(owner, str) and state in (RUNNING, BROKEN, DONE) and size_then is not None:
                found[name] = AppendNote(start, most, owner, cast(str, state), size_then, date)
    return found


def _stored_spans(spans: dict[str, AppendNote]) -> bool:
    data: dict[str, object] = {name: [note.start, note.most, note.owner, note.state, note.size_then, note.date_then] for name, note in spans.items()}
    return _write(APPENDS_FILE_NAME, data, spans)


def _hold_owner() -> bool:
    """This Chrono FTP holds a lock file of its own for as long as it runs: another one sees from it that an append of this one may be
    running, and never sets it back. False when it can't be held here."""
    global _owner_file
    folder = config_folder() / OWNERS_FOLDER
    if _owner_file is not None and _owner_file[0] == folder:
        return True
    if sys.platform != "win32":  # elsewhere no such lock: an append is set back by the Chrono FTP that made it, or after it ended
        final = folder / f"{OWNER}{OWNER_SUFFIX}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handle = (folder / f"{OWNER}.new").open("a")  # held before it has its name: under that name it is never free
        except OSError:
            return False
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.rename(folder / f"{OWNER}.new", final)
        except OSError:
            handle.close()
            return False
        if _owner_file is not None:
            _let_go_of_owner()  # a lock in another settings folder (only the tests change it)
        _owner_file = (folder, handle)
        _tidy_owners(folder)
    return True


def _let_go_of_owner() -> None:
    """This Chrono FTP ends: its lock file goes, so they never pile up."""
    global _owner_file
    if _owner_file is None:
        return
    folder, handle = _owner_file
    _owner_file = None
    try:
        (folder / f"{OWNER}{OWNER_SUFFIX}").unlink()
    except OSError:
        pass
    handle.close()


atexit.register(_let_go_of_owner)


def _tidy_owners(folder: Path) -> None:
    """The lock files of every Chrono FTP that ended without removing its own (it stopped at once): each that nobody holds goes."""
    try:
        names = [path for path in folder.iterdir() if path.name.endswith(OWNER_SUFFIX) and path.name != f"{OWNER}{OWNER_SUFFIX}"]
    except OSError:
        return
    for path in names:
        _owner_gone(path.name.removesuffix(OWNER_SUFFIX))


def _owner_gone(owner: str) -> bool:
    """Whether the Chrono FTP that made a note has ended: its lock file is free, or gone."""
    if sys.platform != "win32":
        path = config_folder() / OWNERS_FOLDER / f"{owner}{OWNER_SUFFIX}"
        try:
            descriptor = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False  # held: it still runs
        finally:
            os.close(descriptor)  # also lets go of the lock
        try:
            path.unlink()
        except OSError:
            pass
    return True


def remember_append(connection: Connection, remote: str, length: int, most: int) -> bool:
    """An append to a file on a server is about to start: its length before it, the most it can have once all of the source is added
    (most), and which Chrono FTP appends, so a broken-off append can be undone later, only where nothing but that append can be in the
    file, and never while it runs. False when the note could not be stored: then the append must not start."""
    with _locked():
        if not _hold_owner():
            return False
        found = _appends()
        key = append_key(connection, remote)
        found.pop(key, None)  # the newest last, so the oldest go first
        found[key] = AppendNote(length, most, OWNER)
        _marked.pop(key, None)
        if not _stored_spans(dict(list(found.items())[-HIGHEST_RECORDS:])):
            return False
        _running.add(key)
        return True


def mark_append(connection: Connection, remote: str, state: str, size: int = -1, date: float | None = None) -> bool:
    """How the append ended, as far as it is known: it broke off (with the file's size and date then), or it went whole. Kept in memory
    also when it can't be stored, so this Chrono FTP never acts on it otherwise. False when it could not be stored."""
    with _locked():
        found = _appends()
        key = append_key(connection, remote)
        if key not in found:
            return True
        note = found[key]
        found[key] = _marked[key] = AppendNote(note.start, note.most, note.owner, state, size, date)
        return _stored_spans(found)


def append_ended(connection: Connection, remote: str) -> None:
    """The append is no longer running here, however it ended: from now on its note, if it stays, may be acted on."""
    with _lock:
        _running.discard(append_key(connection, remote))


def forget_append(connection: Connection, remote: str) -> bool:
    """False when the note stays because it could not be stored without it."""
    with _locked():
        found = _appends()
        key = append_key(connection, remote)
        if found.pop(key, None) is None:
            return True
        if not _stored_spans(found):
            return False
        _marked.pop(key, None)
        return True


def broken_appends(connection: Connection) -> dict[str, AppendNote]:
    """The files of this login that hold part of an append that broke off, or whose append went whole but whose note stayed. An append
    that still runs, here or in another Chrono FTP, is left out."""
    with _lock:
        _tidy_owners(config_folder() / OWNERS_FOLDER)
        found: dict[str, AppendNote] = {}
        for key, note in _appends().items():
            if key in _running or (note.owner != OWNER and not _owner_gone(note.owner)):
                continue
            try:
                fields: object = json.loads(key)
            except ValueError:
                continue
            if isinstance(fields, list):
                items = cast(list[object], fields)
                if items[:3] == [connection.host, connection.port, connection.user] and len(items) == 4 and isinstance(items[3], str):
                    found[items[3]] = note
        return found


def lookup(key: str) -> PartRecord | None:
    """The part's note: a running one from the claims journal stands over a kept one."""
    with _lock:
        claims, _lines, _whole_end = _claims()
        return claims[key] if key in claims else _load().get(key)


def _claims() -> tuple[dict[str, PartRecord], int, bool]:
    """The running parts, from their journal (one small line per claim or forget), read again only when it changed on the disk; and its
    lines, and whether it ends with a whole line."""
    global _claims_cache
    path = config_folder() / CLAIMS_FILE_NAME
    stamp = _journal_stamp(path)
    if _claims_cache is None or _claims_cache[0] != stamp:
        try:
            text = path.read_text(encoding="utf-8") if stamp[0] != -1 else ""
        except (OSError, UnicodeDecodeError):
            text = ""
        found: dict[str, PartRecord] = {}
        lines = 0
        for line in text.splitlines():
            lines += 1
            try:
                event: object = json.loads(line)
            except (ValueError, RecursionError):
                continue  # half a line, left by a computer that stopped mid-write
            items = cast(list[object], event) if isinstance(event, list) else []
            if len(items) == 3 and items[0] == "+" and isinstance(items[1], str) and (record := _record(items[2])) is not None:
                found.pop(items[1], None)
                found[items[1]] = record
            elif len(items) == 2 and items[0] == "-" and isinstance(items[1], str):
                found.pop(items[1], None)
        _claims_cache = (stamp, found, lines, text == "" or text.endswith("\n"))
    return dict(_claims_cache[1]), _claims_cache[2], _claims_cache[3]


def _claim_logged(event: list[object]) -> bool:
    """One line added to the claims journal, without waiting for the disk (see claim); now and then it is written afresh with only the
    claims that still run."""
    global _claims_cache
    found, lines, whole_end = _claims()
    key = cast(str, event[1])
    if event[0] == "+":
        found.pop(key, None)
        found[key] = cast(PartRecord, event[2])
    else:
        found.pop(key, None)
    path = config_folder() / CLAIMS_FILE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if lines >= 2 * len(found) + JOURNAL_SLACK:
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text("".join(json.dumps(["+", name, _record_data(record)], ensure_ascii=True) + "\n" for name, record in found.items()), encoding="utf-8")
            os.replace(temporary, path)
            lines = len(found)
        else:
            line = ["+", key, _record_data(cast(PartRecord, event[2]))] if event[0] == "+" else ["-", key]
            with path.open("a", encoding="utf-8") as file:
                file.write(("" if whole_end else "\n") + json.dumps(line, ensure_ascii=True) + "\n")  # half a line before never swallows this
            lines += 1
    except OSError:
        _claims_cache = None
        return False
    _claims_cache = (_journal_stamp(path), found, lines, True)
    return True


def notes_in(connection: Connection, folder: str) -> list[str]:
    """The server paths in folder that have a note of this login: kept parts, and parts being written."""
    with _lock:
        keys = [*_load(), *_claims()[0]]
    found: list[str] = []
    for key in keys:
        try:
            fields: object = json.loads(key)
        except (ValueError, RecursionError):
            continue
        items = cast(list[object], fields) if isinstance(fields, list) else []
        if items[:4] == ["up", connection.host, connection.port, connection.user] and len(items) == 5 and isinstance(items[4], str):
            path = items[4]
            if path.rsplit("/", 1)[0] == folder.rstrip("/") or (folder == "/" and path.count("/") == 1):
                found.append(path)
    return found


def _record_data(record: PartRecord) -> dict[str, object]:
    return {"source": record.source, "size": record.size, "modified": record.modified, "part_size": record.part_size, "resume": record.resume, "owner": record.owner, "running": record.running, "ranges": [list(item) for item in record.ranges]}


def remember(key: str, record: PartRecord) -> bool:
    """The part's one note: whatever was noted for this part before is replaced. False when it was not stored: then the part is nobody's
    and is not kept."""
    if not math.isfinite(record.modified) or not 0 <= record.part_size <= record.size or not ranges_valid(record.ranges, record.part_size, record.size):
        forget(key)
        return False
    with _locked():
        records = _load()
        records.pop(key, None)  # the newest last, so the oldest go first
        records[key] = record
        if not _save(records):
            return False
        if key in _claims()[0]:
            _claim_logged(["-", key])  # kept now: no longer running
        return True


def claim(key: str, record: PartRecord) -> bool | None:
    """A part about to be written, noted before its first byte as running by this Chrono FTP: True. False when another Chrono FTP that
    still runs writes it now (its note says so): then another name is taken. None when the note could not be stored (this computer's
    settings folder takes nothing): the part is then never kept.
    Written, but not put on the disk at once: every file would pay for it. Only a note of a part that is kept is (remember). When the
    whole computer stops, a running note that never reached the disk is lost: its part then has no note, so it is never touched; it is
    left alone, on the safe side, and the person may remove it."""
    with _locked():
        claims = _claims()[0]
        there = claims[key] if key in claims else _load().get(key)
        if there is not None and in_use(there):
            return False
        if not _hold_owner():
            return None
        return True if _claim_logged(["+", key, replace(record, owner=OWNER, running=True)]) else None  # one small line: see the note above


def in_use(record: PartRecord) -> bool:
    """Whether another Chrono FTP that still runs is writing this part now."""
    return record.running and record.owner != OWNER and not _owner_gone(record.owner)


def forget(key: str) -> None:
    """Not put on the disk at once (see claim): a note that a stop of the computer brings back names a part that is gone or that went
    whole; tidying acts only on files it finds, of our suffix, by their note."""
    with _locked():
        if key in _claims()[0]:
            _claim_logged(["-", key])
        records = _load()
        if records.pop(key, None) is not None:  # only a part that was kept has a note here: rare
            _save(records, durable=False)


_claims_cache: tuple[tuple[int, int, int], dict[str, PartRecord], int, bool] | None = None  # the claims journal as read
_olds_cache: tuple[tuple[int, int, int], dict[str, str], int, bool] | None = None  # the journal as read: its file, notes, lines, whole end
_olds_unsynced: bool = False  # a forget was written but is not on the disk yet: the next note or settle_olds puts it there


def _journal_stamp(path: Path) -> tuple[int, int, int]:
    try:
        details = path.stat()
    except OSError:
        return (-1, -1, -1)
    return (details.st_ino, details.st_mtime_ns, details.st_size)


def _replayed(text: str) -> tuple[dict[str, str], int]:
    """The notes a journal holds, line by line: ["+", key, name] notes a file, ["-", key] forgets it. A line that can't be read (the
    last one, cut off when the computer stopped) is left out."""
    found: dict[str, str] = {}
    lines = 0
    for line in text.splitlines():
        lines += 1
        try:
            event: object = json.loads(line)
        except ValueError:
            continue
        items = cast(list[object], event) if isinstance(event, list) else []
        if len(items) == 3 and items[0] == "+" and isinstance(items[1], str) and isinstance(items[2], str):
            found.pop(items[1], None)  # the newest last, so the oldest go first
            found[items[1]] = items[2]
        elif len(items) == 2 and items[0] == "-" and isinstance(items[1], str):
            found.pop(items[1], None)
    return found, lines


def _olds() -> tuple[dict[str, str], int]:
    """The notes of files moved aside, read again only when the journal changed on the disk."""
    found, lines, _whole_end = _journal()
    return found, lines


def _journal() -> tuple[dict[str, str], int, bool]:
    """The notes, the journal's lines, and whether it ends with a whole line (a computer that stopped mid-write leaves half a line)."""
    global _olds_cache
    path = config_folder() / OLDS_FILE_NAME
    stamp = _journal_stamp(path)
    if _olds_cache is None or _olds_cache[0] != stamp:
        try:
            text = path.read_text(encoding="utf-8") if stamp[0] != -1 else ""
        except (OSError, UnicodeDecodeError):
            text = ""
        found, lines = _replayed(text)
        _olds_cache = (stamp, found, lines, text == "" or text.endswith("\n"))
    return dict(_olds_cache[1]), _olds_cache[2], _olds_cache[3]


def _logged(event: list[str], synced: bool = True) -> bool:
    """One event added to the end of the journal: a small write, not the whole file; synced: on the disk before it counts (it also puts
    every earlier line there). Now and then the journal is written afresh with only the notes that still hold. False when it could not
    be stored."""
    global _olds_cache, _olds_unsynced
    found, lines, whole_end = _journal()
    path = config_folder() / OLDS_FILE_NAME
    if lines >= 2 * len(found) + JOURNAL_SLACK:
        return _compacted(found, event)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", encoding="utf-8") as file:
            file.write(("" if whole_end else "\n") + json.dumps(event, ensure_ascii=True) + "\n")  # half a line before never swallows this one
            file.flush()
            if synced:
                _data_sync(file.fileno())
                _olds_unsynced = False
            else:
                _olds_unsynced = True
        if new:
            _sync_folder(path.parent)  # the journal's name is on the disk too
    except OSError:
        _olds_cache = None
        return False
    replayed, _lines = _replayed(json.dumps(event, ensure_ascii=True))
    if event[0] == "+":
        found.pop(event[1], None)
        found.update(replayed)
    else:
        found.pop(event[1], None)
    _olds_cache = (_journal_stamp(path), found, lines + 1, True)
    return True


def _compacted(found: dict[str, str], event: list[str]) -> bool:
    """The journal written afresh: the notes that hold, with this event, and nothing else."""
    global _olds_cache, _olds_unsynced
    replayed, _lines = _replayed(json.dumps(event, ensure_ascii=True))
    found.pop(event[1], None)
    found.update(replayed)
    kept = dict(list(found.items())[-HIGHEST_RECORDS:])
    path = config_folder() / OLDS_FILE_NAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            file.write("".join(json.dumps(["+", key, name], ensure_ascii=True) + "\n" for key, name in kept.items()))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _sync_folder(path.parent)
    except OSError:
        _olds_cache = None
        return False
    _olds_cache = (_journal_stamp(path), kept, len(kept), True)
    _olds_unsynced = False
    return True


def _data_sync(descriptor: int) -> None:
    sync: Callable[[int], None] = getattr(os, "fdatasync", os.fsync)  # the data, without the dates where the system can
    sync(descriptor)


def remember_old(connection: Connection, aside: str, target: str, size: int) -> bool:
    """A file of a server this program moves aside (to aside) while a new one takes its place (target): it is ours, so tidying may give it
    its name back or remove it. size: the file's size, or -1 when the server can't tell it. On the disk before it counts. False when
    the note could not be stored."""
    with _locked():
        return _logged(["+", append_key(connection, aside), json.dumps([target, size])])


def forget_old(connection: Connection, aside: str) -> bool:
    """The file moved aside is gone. This line reaches the disk with the next note, or with settle_olds: a note it leaves after the
    computer stopped names a file that is no longer there, and its size keeps a file that later takes that name from being taken for it."""
    with _locked():
        if append_key(connection, aside) not in _olds()[0]:
            return True
        return _logged(["-", append_key(connection, aside)], synced=False)


def settle_olds() -> None:
    """Every forget on the disk: after a transfer."""
    global _olds_unsynced
    with _locked():
        if not _olds_unsynced:
            return
        try:
            with (config_folder() / OLDS_FILE_NAME).open("a", encoding="utf-8") as file:
                _data_sync(file.fileno())
        except OSError:
            return  # the lines are still in the system's hands: it writes them on its own
        _olds_unsynced = False


def old_target(connection: Connection, aside: str, size: int) -> str | None:
    """The name a file this program moved aside had, or None when the file is not ours: no note, or one of another size (size: what
    the folder listing says now, or -1 when it can't tell)."""
    with _lock:
        noted = _olds()[0].get(append_key(connection, aside))
    try:
        fields: object = json.loads(noted) if noted is not None else None
    except ValueError:
        return None
    items = cast(list[object], fields) if isinstance(fields, list) else []
    if len(items) != 2 or not isinstance(items[0], str) or not isinstance(items[1], int):
        return None
    if items[1] >= 0 and size >= 0 and items[1] != size:
        return None
    return items[0]

UP: str = "up"
DOWN: str = "down"


@dataclass(frozen=True)
class PartialNote:
    """An rsync job that may keep unfinished copies in its own folder (PARTIAL_FOLDER) in each folder it writes to, below base: the folder
    the job goes into, on the server (UP) or here (DOWN). source_base: where the files come from, the other side's folder that maps to
    base. resume: the setting the copies were kept by."""

    base: str
    source_base: str
    resume: str


def _partial_key(connection: Connection, side: str, base: str) -> str:
    return json.dumps([side, connection.host, connection.port, connection.user, base], ensure_ascii=True)


def _partials(data: object) -> dict[str, PartialNote]:
    found: dict[str, PartialNote] = {}
    if isinstance(data, dict):
        for key, value in cast(dict[object, object], data).items():
            fields = cast(list[object], value) if isinstance(value, list) else []
            if isinstance(key, str) and len(fields) == 3 and all(isinstance(field, str) for field in fields):
                base, source_base, resume = cast(list[str], fields)
                found[key] = PartialNote(base, source_base, resume)
    return found


def remember_partial(connection: Connection, side: str, base: str, source_base: str, resume: str) -> bool:
    """rsync is about to write below base with its own folder for unfinished copies: those folders are ours."""
    with _locked():
        found = _read(PARTIALS_FILE_NAME, _partials)
        key = _partial_key(connection, side, base)
        found.pop(key, None)
        found[key] = PartialNote(base, source_base, resume)
        kept = dict(list(found.items())[-HIGHEST_RECORDS:])
        return _write(PARTIALS_FILE_NAME, {name: [note.base, note.source_base, note.resume] for name, note in kept.items()}, kept)


def partial_note(connection: Connection, side: str, folder: str) -> PartialNote | None:
    """The rsync job whose unfinished copies may be in folder's folder of its own: the one with the nearest base at or above folder."""
    with _lock:
        found = _read(PARTIALS_FILE_NAME, _partials)
    best: PartialNote | None = None
    for key, note in found.items():
        try:
            fields: object = json.loads(key)
        except ValueError:
            continue
        if not isinstance(fields, list) or cast(list[object], fields)[:4] != [side, connection.host, connection.port, connection.user]:
            continue
        base = note.base.rstrip("/") or "/"
        if (folder == base or folder.startswith(base.rstrip("/") + "/")) and (best is None or len(base) > len(best.base)):
            best = note
    return best


_CLEANERS: dict[str, Callable[[object], object]] = {  # each note file written again without what could not be read in it
    FILE_NAME: lambda found: _save(cast(dict[str, PartRecord], found), durable=False),
    APPENDS_FILE_NAME: lambda found: _stored_spans(cast(dict[str, AppendNote], found)),
    PARTIALS_FILE_NAME: lambda found: _write(PARTIALS_FILE_NAME, {name: [note.base, note.source_base, note.resume] for name, note in cast(dict[str, PartialNote], found).items()}, found),
}
