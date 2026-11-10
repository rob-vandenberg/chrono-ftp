"""The transfer options (FB-01 to FB-08) against the fake SFTP and FTP servers, both directions, file by file. The rsync way is in
test_transfer_rsync.py."""

import json
import os
import select
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import override

import pytest

from chrono_ftp.config import parts
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.parts import PartRecord, download_key, download_source, upload_key, upload_source
from chrono_ftp.connections.base import Capabilities, RemoteConnection, RemoteEntry
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core.errors import RemoteActionFailed
from chrono_ftp.core.connection import FTP, SFTP, Connection
from chrono_ftp.core.transferoptions import RESUME_OFF, RESUME_ON, RESUME_SMART, SMART_RESUME_BYTES, TransferOptions, folder_rights
from chrono_ftp.transfers.job import PART_SUFFIX, Meter
from chrono_ftp.transfers.filebyfile import FileWay, CHANGED_HERE_TEXT, CHANGED_THERE_TEXT, NO_APPEND_TEXT, NOT_A_NAME_TEXT
from chrono_ftp.transfers.options import Action, Answer, Answers, Choice, Conflict, decided, KEEP_TIMESTAMPS, RESUME, SET_RIGHTS, is_newer, resumable, usable
from chrono_ftp.transfers.queue import LEFTOVER_SECONDS, Job, Owned, State, TransferQueue, continuable, note_text, tidy
from tests.fakeservers import PASSWORD, USER, ftp_server, ssh_server
from tests.test_connections import trusted_ssh
from tests.test_transfers import LEFTOVER_LOGIN, Leftovers, long_known, ours, run_all, wait_for

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_options.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: every transfer option against real fake servers, stop and resume included; resume only from a noted, unchanged
#          source; dates no clock can hold; a move keeps a source that changed; every arrival checked for its size; unknown dates told;
#          a part without our note is never touched; a continued FTP upload is told; a new file here takes its name by a link; tidying by
#          the resume setting a part was kept by; a size the server never gave, and the connection, in the question; a stop at a chosen
#          speed; the waiting question's queue is closed also when the test fails; a stopped transfer waits after its first bytes until
#          the stop comes, so no pause of the computer can let it end first; a helper process of a test is read against a deadline and
#          never outlives its test; a transfer held for a stop that never came fails the test with a clear text; a move is never saved;
#          the time a question waits is no transfer time; Only new and updated holds also after Yes to all; a part whose source changed
#          its size, or without confirmed bytes, is no part to keep; the dates kept by every way of a job are counted together; a saved
#          value of another type is never taken

OLD_TIME: float = 1_600_000_000.0  # September 2020: clearly older than any file a test makes now
NEWER_TIME: float = 1_700_000_000.0
BIG: int = 1024 * 1024  # takes four seconds at the limit below, so a stop comes halfway
SLOW: int = 256  # KB per second
WAIT_SECONDS: float = 10.0
SMART_SMALL: int = 90 * 1024  # under the 100 KB of smart resume
SMALL_LIMIT: int = 32  # KB per second: two seconds after the first block of 64 KB
Server = tuple[RemoteConnection, Connection, Path]


@pytest.fixture(params=[SFTP, FTP])
def server(request: pytest.FixtureRequest, tmp_path: Path) -> Generator[Server]:
    root = tmp_path / "server"
    root.mkdir()
    if request.param == SFTP:
        with ssh_server(root) as running:
            connection = trusted_ssh(running)
            yield connection, connection.connection, root
            connection.close()
    else:
        with ftp_server(root) as port:
            settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
            ftp = FtpConnection(settings, PASSWORD)
            ftp.connect()
            yield ftp, settings, root
            ftp.close()


class Person:
    """Answers the overwrite question as the test says, in turn, and notes every question. Without answers left: No."""

    def __init__(self, *answers: Answer) -> None:
        self.answers: list[Answer] = list(answers)
        self.asked: list[Conflict] = []

    def __call__(self, job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
        self.asked.append(conflict)
        reply(self.answers.pop(0) if self.answers else Answer(Choice.NO))


def transfer(server: Server, local: Path, remote_path: str, upload: bool, name: str, options: TransferOptions | None = None, person: Person | None = None, answers: Answers | None = None) -> Job:
    """One job, file by file (a password keeps rsync out), run to its end."""
    connection, settings, _root = server
    queue = TransferQueue(lambda item: None, ask=person)
    job = Job(connection, settings, str(local), remote_path, upload, name, has_password=True, options=options or TransferOptions(), answers=answers or Answers())
    run_all(queue, job)
    queue.close()
    return job


def up(server: Server, source: Path, options: TransferOptions | None = None, person: Person | None = None, folder: str = "/") -> Job:
    return transfer(server, source, folder, True, source.name, options, person)


def down(server: Server, remote: str, into: Path, options: TransferOptions | None = None, person: Person | None = None) -> Job:
    return transfer(server, into, remote, False, remote.rsplit("/", 1)[-1], options, person)


def write(path: Path, text: str, moment: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if moment is not None:
        os.utime(path, (moment, moment))
    return path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- the overwrite question (FB-03)


@pytest.mark.parametrize("upload", [True, False])
def test_an_existing_file_deep_in_a_folder_is_asked_about_with_both_sizes_and_dates(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    write(source / "tree" / "deep" / "a.txt", "new content", NEWER_TIME)
    write(source / "tree" / "b.txt", "b")
    write(target / "tree" / "deep" / "a.txt", "old", OLD_TIME)
    person = Person(Answer(Choice.YES))
    job = up(server, here / "tree", person=person) if upload else down(server, "/tree", here, person=person)
    assert job.state == State.DONE, job.message
    assert [(item.name, item.upload, item.source_size, item.target_size) for item in person.asked] == [("a.txt", upload, 11, 3)]
    assert person.asked[0].source_modified == NEWER_TIME and person.asked[0].target_modified == OLD_TIME
    assert read(target / "tree" / "deep" / "a.txt") == "new content"
    assert read(target / "tree" / "b.txt") == "b"


@pytest.mark.parametrize("upload", [True, False])
def test_no_leaves_the_file_as_it_is_and_the_rest_goes_on(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    for name in ("a.txt", "b.txt"):
        write(source / "tree" / name, "new")
        write(target / "tree" / name, "old")
    person = Person(Answer(Choice.NO), Answer(Choice.YES))
    job = up(server, here / "tree", person=person) if upload else down(server, "/tree", here, person=person)
    assert job.state == State.DONE, job.message
    assert len(person.asked) == 2
    assert sorted(read(target / "tree" / name) for name in ("a.txt", "b.txt")) == ["new", "old"]
    assert not any(path.name.endswith(PART_SUFFIX) for path in (target / "tree").iterdir())


@pytest.mark.parametrize("choice, expected", [(Choice.YES_TO_ALL, "new"), (Choice.NO_TO_ALL, "old")])
def test_an_answer_for_all_holds_for_every_item_of_the_same_action(server: Server, tmp_path: Path, choice: Choice, expected: str) -> None:
    connection, settings, root = server
    for name in ("a.txt", "b.txt"):
        write(tmp_path / name, "new")
        write(root / name, "old")
    person = Person(Answer(choice))
    queue = TransferQueue(lambda item: None, ask=person)
    shared = Answers()
    jobs = [Job(connection, settings, str(tmp_path / name), "/", True, name, has_password=True, answers=shared) for name in ("a.txt", "b.txt")]
    run_all(queue, *jobs)
    queue.close()
    assert all(job.state == State.DONE for job in jobs)
    assert len(person.asked) == 1
    assert [read(root / name) for name in ("a.txt", "b.txt")] == [expected, expected]


def test_only_if_newer_replaces_only_where_the_new_one_is_newer(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "tree" / "older-there.txt", "new", NEWER_TIME)
    write(tmp_path / "tree" / "newer-there.txt", "new", OLD_TIME)
    write(root / "tree" / "older-there.txt", "old", OLD_TIME)
    write(root / "tree" / "newer-there.txt", "old", NEWER_TIME)
    person = Person(Answer(Choice.ONLY_IF_NEWER))
    job = up(server, tmp_path / "tree", person=person)
    assert job.state == State.DONE, job.message
    assert len(person.asked) == 1
    assert read(root / "tree" / "older-there.txt") == "new"
    assert read(root / "tree" / "newer-there.txt") == "old"


@pytest.mark.parametrize("upload", [True, False])
def test_rename_keeps_both_files(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    write(source / "a.txt", "new")
    write(target / "a.txt", "old")
    person = Person(Answer(Choice.RENAME, "a (2).txt"))
    job = up(server, here / "a.txt", person=person) if upload else down(server, "/a.txt", here, person=person)
    assert job.state == State.DONE, job.message
    assert read(target / "a.txt") == "old"
    assert read(target / "a (2).txt") == "new"


@pytest.mark.parametrize("upload", [True, False])
def test_append_adds_the_new_file_to_the_end_of_the_one_there(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    write(source / "log.txt", "second\n")
    write(target / "log.txt", "first\n")
    person = Person(Answer(Choice.APPEND))
    job = up(server, here / "log.txt", person=person) if upload else down(server, "/log.txt", here, person=person)
    if upload and isinstance(server[0], FtpConnection):  # FTP can't cut a file back: Append is not offered, and nothing is written
        assert not person.asked[0].append_possible
        assert job.state == State.FAILED and job.message == NO_APPEND_TEXT
        assert read(target / "log.txt") == "first\n"
        return
    assert person.asked[0].append_possible
    assert job.state == State.DONE, job.message
    assert read(target / "log.txt") == "first\nsecond\n"


def test_a_question_waits_and_a_closed_window_never_leaves_it_waiting(server: Server, tmp_path: Path) -> None:
    connection, settings, root = server
    write(tmp_path / "a.txt", "new")
    write(root / "a.txt", "old")
    asked = threading.Event()
    queue = TransferQueue(lambda item: None, ask=lambda job, conflict, reply: asked.set())  # nobody ever answers
    job = Job(connection, settings, str(tmp_path / "a.txt"), "/", True, "a.txt", has_password=True)
    try:
        queue.add(job)
        assert asked.wait(10)
        assert job.waiting_for_answer
    finally:
        queue.close()  # also when the test fails: no question is left waiting
    finished = threading.Event()
    threading.Thread(target=lambda: wait_for((job,), finished), daemon=True).start()
    assert finished.wait(10)
    assert job.state == State.STOPPED and not job.waiting_for_answer
    assert read(root / "a.txt") == "old"


def test_without_anyone_to_ask_nothing_is_replaced(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "a.txt", "new")
    write(root / "a.txt", "old")
    job = up(server, tmp_path / "a.txt")
    assert job.state == State.DONE and read(root / "a.txt") == "old"


def test_stop_in_the_question_stops_the_transfer(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "a.txt", "new")
    write(root / "a.txt", "old")
    job = up(server, tmp_path / "a.txt", person=Person(Answer(Choice.STOP)))
    assert job.state == State.STOPPED and read(root / "a.txt") == "old"


# --- only new and updated (FB-04)


@pytest.mark.parametrize("upload", [True, False])
def test_only_new_and_updated_skips_a_target_that_is_not_older_without_asking(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    write(source / "tree" / "same.txt", "new", OLD_TIME)
    write(source / "tree" / "updated.txt", "new", NEWER_TIME)
    write(source / "tree" / "fresh.txt", "new")
    write(target / "tree" / "same.txt", "old", OLD_TIME)
    write(target / "tree" / "updated.txt", "old", OLD_TIME)
    person = Person()
    options = TransferOptions(only_newer=True, ask_before_replacing=False)
    job = up(server, here / "tree", options, person) if upload else down(server, "/tree", here, options, person)
    assert job.state == State.DONE, job.message
    assert person.asked == []
    assert [read(target / "tree" / name) for name in ("same.txt", "updated.txt", "fresh.txt")] == ["old", "new", "new"]


# --- keep timestamps (FB-05)


@pytest.mark.parametrize("upload", [True, False])
def test_dates_are_kept_for_files_and_for_folders_after_their_contents(server: Server, tmp_path: Path, upload: bool) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    write(source / "tree" / "deep" / "a.txt", "a", OLD_TIME)
    for folder in (source / "tree" / "deep", source / "tree"):
        os.utime(folder, (OLD_TIME, OLD_TIME))
    job = up(server, here / "tree") if upload else down(server, "/tree", here)
    assert job.state == State.DONE, job.message
    assert (target / "tree" / "deep" / "a.txt").stat().st_mtime == OLD_TIME
    folders_kept = not (upload and isinstance(connection, FtpConnection))  # this FTP server sets the date of files only
    assert ((target / "tree").stat().st_mtime == OLD_TIME) == folders_kept
    assert ((target / "tree" / "deep").stat().st_mtime == OLD_TIME) == folders_kept
    assert ("2 of these items" in job.message) == (not folders_kept)


@pytest.mark.parametrize("upload", [True, False])
def test_without_keep_dates_a_file_gets_the_moment_it_arrived(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    here.mkdir()
    write(source / "a.txt", "a", OLD_TIME)
    options = TransferOptions(keep_timestamps=False)
    job = up(server, here / "a.txt", options) if upload else down(server, "/a.txt", here, options)
    assert job.state == State.DONE, job.message
    assert (target / "a.txt").stat().st_mtime > NEWER_TIME


# --- rights on upload (FB-06)


def test_rights_on_upload_with_x_added_to_new_folders_and_existing_folders_keep_theirs(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "tree" / "a.txt", "a")
    write(tmp_path / "tree" / "new" / "b.txt", "b")
    write(root / "tree" / "old.txt", "x")
    (root / "tree" / "old.txt").chmod(0o600)
    (root / "tree").chmod(0o711)
    write(tmp_path / "tree" / "old.txt", "new")
    options = TransferOptions(set_rights=True, rights="640", ask_before_replacing=False)
    job = up(server, tmp_path / "tree", options)
    assert job.state == State.DONE, job.message
    assert stat.S_IMODE((root / "tree" / "a.txt").stat().st_mode) == 0o640
    assert stat.S_IMODE((root / "tree" / "old.txt").stat().st_mode) == 0o640  # the chosen rights win over those of the replaced file
    assert stat.S_IMODE((root / "tree" / "new").stat().st_mode) == 0o750  # made by the upload
    assert stat.S_IMODE((root / "tree").stat().st_mode) == 0o711  # there already: as in the reference program, it keeps its own


def test_folder_rights_get_x_wherever_they_have_r() -> None:
    assert folder_rights(TransferOptions(rights="644")) == "0755"
    assert folder_rights(TransferOptions(rights="604")) == "0705"
    assert folder_rights(TransferOptions(rights="644", add_x_to_folders=False)) == "0644"


@pytest.mark.parametrize("ignore, state", [(True, State.DONE), (False, State.FAILED)])
def test_a_refused_change_of_rights_fails_the_file_unless_ignored(tmp_path: Path, ignore: bool, state: State) -> None:
    root = tmp_path / "server"
    root.mkdir()
    write(tmp_path / "a.txt", "a")
    with ftp_server(root, rights="elradfmwT") as port:  # no M: SITE CHMOD is refused
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        job = transfer((connection, settings, root), tmp_path / "a.txt", "/", True, "a.txt", TransferOptions(set_rights=True, rights="600", ignore_rights_errors=ignore))
        connection.close()
    assert job.state == state, job.message
    assert (root / "a.txt").exists() == ignore
    assert [path.name for path in root.iterdir() if path.name.endswith(PART_SUFFIX)] == []


# --- count the total first (FB-07)


@pytest.mark.parametrize("upload", [True, False])
def test_counting_first_gives_the_whole_total_and_off_starts_without_it(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source = here if upload else root
    write(source / "tree" / "a.txt", "aaaa")
    write(source / "tree" / "deep" / "b.txt", "bbbbbb")
    counted = up(server, here / "tree") if upload else down(server, "/tree", here)
    assert counted.total == 10 and counted.done == 10
    options = TransferOptions(count_first=False, ask_before_replacing=False)
    uncounted = up(server, here / "tree", options) if upload else down(server, "/tree", here, options)
    assert uncounted.state == State.DONE and uncounted.total == 0 and uncounted.done == 10


# --- move (FB-08)


@pytest.mark.parametrize("upload", [True, False])
def test_a_move_deletes_each_source_once_it_arrived_and_keeps_what_was_refused(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    write(source / "tree" / "gone" / "a.txt", "a")
    write(source / "tree" / "kept" / "b.txt", "new")
    write(target / "tree" / "kept" / "b.txt", "old")
    options = TransferOptions(delete_source=True)
    job = up(server, here / "tree", options, Person(Answer(Choice.NO))) if upload else down(server, "/tree", here, options, Person(Answer(Choice.NO)))
    assert job.state == State.DONE, job.message
    assert read(target / "tree" / "gone" / "a.txt") == "a"
    assert not (source / "tree" / "gone").exists()  # emptied, so it went too
    assert read(source / "tree" / "kept" / "b.txt") == "new"  # refused: it stays where it was
    assert read(target / "tree" / "kept" / "b.txt") == "old"


# --- resume (FB-02)


def holds_bytes(part: Path, start: bytes = b"") -> bool:
    """The part holds bytes; with start, it begins with exactly those, so they are bytes of the transfer the test waits for."""
    try:
        with part.open("rb") as file:
            found = file.read(max(len(start), 1))
    except OSError:
        return False
    return len(found) > 0 and found.startswith(start)


def stopped_halfway(server: Server, local: Path, remote_path: str, upload: bool, name: str, options: TransferOptions, part: Path | None = None, start: bytes = b"", limit: int = SLOW) -> Job:
    """Stopped once the transfer is under way, with some bytes written: the transfer waits after them until the stop comes. Afterwards
    part (where the bytes land) holds them, starting with start."""
    connection, settings, _root = server
    queue = TransferQueue(lambda item: None)
    queue.limit = limit
    job = Job(connection, settings, str(local), remote_path, upload, name, has_password=True, options=options)
    with held_after_its_first_bytes(job):  # it waits after its first bytes until the stop: never done before it, however slow the computer
        try:
            queue.add(job)
            deadline = time.monotonic() + WAIT_SECONDS
            # Bytes counted are bytes written in order; the part, here or on the server, holds them on the disk once the stop closes it.
            while job.done == 0 and job.state in (State.WAITING, State.RUNNING):
                assert time.monotonic() < deadline, "the transfer did not get under way"
                threading.Event().wait(0.01)
            queue.stop(job)
            finished = threading.Event()
            threading.Thread(target=lambda: wait_for((job,), finished), daemon=True).start()
            assert finished.wait(WAIT_SECONDS)
        finally:
            queue.close()  # also when the test fails: no thread of it runs on
    return job


CHILD_SECONDS: float = 5.0  # a helper process says its line, or ends, within this: well inside the 20 seconds a test may take


class Child:
    """A helper process of a test, started with bufsize=0, so a line is read byte by byte and never waits in a buffer."""

    def __init__(self, process: "subprocess.Popen[bytes]") -> None:
        self.process: subprocess.Popen[bytes] = process

    def says(self, expected: bytes) -> None:
        """Its next line is expected; it fails the test when that line does not come within CHILD_SECONDS, or is another."""
        output = self.process.stdout
        assert output is not None
        deadline = time.monotonic() + CHILD_SECONDS
        line = b""
        nothing: list[int] = []
        while not line.endswith(b"\n"):
            ready, _, _ = select.select([output], nothing, nothing, max(deadline - time.monotonic(), 0))
            if not ready:
                pytest.fail(f"the helper process did not say {expected!r} within {CHILD_SECONDS} s (it said {line!r})")
            byte = output.read(1)
            if not byte:
                pytest.fail(f"the helper process ended before it said {expected!r} (it said {line!r})")
            line += byte
        assert line == expected, f"the helper process said {line!r}, not {expected!r}"

    def ended(self) -> None:
        """It ends on its own within CHILD_SECONDS; else it is killed, and the test fails."""
        try:
            self.process.wait(CHILD_SECONDS)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            pytest.fail(f"the helper process did not end within {CHILD_SECONDS} s, so it was killed")


@contextmanager
def child(script: str, arguments: list[str], environment: dict[str, str] | None = None) -> Generator[Child]:
    """Runs script in another Python. When the block ends, also on a failure, its input is closed (which ends a script that reads it to
    the end), and it ends, or is killed and reaped: it never outlives the test."""
    process = subprocess.Popen([sys.executable, "-c", script, *arguments], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0, env=environment)
    running = Child(process)
    try:
        yield running
    finally:
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.close()
        except OSError:
            pass  # it ended already
        try:
            running.ended()
        finally:
            process.stdout.close()


@contextmanager
def held_after_its_first_bytes(job: Job) -> Generator[None]:
    """Once some of job's bytes are on their way, its transfer waits there until the test stops it: no pause of the computer can let it
    end first, and no limit has to be slow enough for that. A stop that never comes fails the transfer and the test, with that text."""
    real_add = Meter.add
    never_stopped = f"the test did not stop the held transfer within {WAIT_SECONDS} s"
    missed: list[str] = []

    def held(meter: Meter, amount: int) -> None:
        real_add(meter, amount)
        if meter.job is job and job.done > 0:
            if not job.stop_asked.wait(WAIT_SECONDS):
                missed.append(never_stopped)
                raise AssertionError(never_stopped)  # the transfer ends here, failed with this text
            real_add(meter, 0)  # the stop is there now: this ends the transfer

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Meter, "add", held)
        yield
    assert missed == [], never_stopped


SAYS_ONE_LINE: str = "import sys\nprint('started', flush=True)\nsys.stdin.read()\n"  # ends once its input is closed
NEVER_ENDS: str = "import time\nprint('started', flush=True)\ntime.sleep(60)\n"


def test_a_helper_process_that_stays_silent_fails_its_test_with_a_clear_text_and_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[subprocess.Popen[bytes]] = []
    with pytest.raises(pytest.fail.Exception, match=r"did not say b'never\\n' within 0.5 s"):
        with child(SAYS_ONE_LINE, []) as running:
            started.append(running.process)
            running.says(b"started\n")  # it runs: only from here on the time is short
            monkeypatch.setattr(sys.modules[__name__], "CHILD_SECONDS", 0.5)
            running.says(b"never\n")
    assert started[0].returncode == 0  # it ended when its input was closed


def test_a_helper_process_that_never_ends_is_killed_and_fails_its_test_with_a_clear_text(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[subprocess.Popen[bytes]] = []
    with pytest.raises(pytest.fail.Exception, match="did not end within 0.5 s, so it was killed"):
        with child(NEVER_ENDS, []) as running:
            started.append(running.process)
            running.says(b"started\n")  # it runs: only from here on the time is short
            monkeypatch.setattr(sys.modules[__name__], "CHILD_SECONDS", 0.5)
    assert started[0].returncode == -9  # killed, and reaped: it is gone


def test_a_held_transfer_whose_stop_never_comes_fails_with_a_clear_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing is sent: the meter of a job that is never stopped is fed one byte by hand."""
    monkeypatch.setattr(sys.modules[__name__], "WAIT_SECONDS", 0.1)
    settings = Connection(name="T", host="127.0.0.1", user=USER, protocol=FTP)
    job = Job(FtpConnection(settings, PASSWORD), settings, "/nowhere", "/", True, "a.txt")
    never_stopped = "the test did not stop the held transfer within 0.1 s"
    with pytest.raises(AssertionError, match=never_stopped):  # the test fails at the end of the block
        with held_after_its_first_bytes(job):
            with pytest.raises(AssertionError, match=never_stopped):  # the transfer fails at the wait itself
                Meter(job, lambda item: None).add(1)


@pytest.mark.parametrize("upload", [True, False])
def test_a_stopped_transfer_continues_where_it_stopped(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    here.mkdir()
    content = os.urandom(BIG)
    (source / "big.bin").write_bytes(content)
    options = TransferOptions(resume=RESUME_ON)
    part = target / ("big.bin" + PART_SUFFIX)
    job = stopped_halfway(server, here / "big.bin" if upload else here, "/" if upload else "/big.bin", upload, "big.bin", options, part)
    assert job.state == State.STOPPED
    assert 0 < part.stat().st_size < BIG and not (target / "big.bin").exists()  # never a half file under the real name
    part.write_bytes(b"X" + part.read_bytes()[1:])  # marked: only a transfer that continues the part keeps the mark
    again = up(server, here / "big.bin", options) if upload else down(server, "/big.bin", here, options)
    assert again.state == State.DONE, again.message
    assert (target / "big.bin").read_bytes() == b"X" + content[1:]
    assert not part.exists()
    by_size = upload and server[1].protocol == FTP  # FTP can't confirm the first part it kept: that limit is told
    assert again.message == (f"Done. {note_text('continued_by_size', 1)}" if by_size else "")


@pytest.mark.parametrize("upload", [True, False])
def test_with_resume_off_a_stopped_transfer_leaves_no_part_file(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    source, target = (here, root) if upload else (root, here)
    here.mkdir()
    (source / "big.bin").write_bytes(os.urandom(BIG))
    job = stopped_halfway(server, here / "big.bin" if upload else here, "/" if upload else "/big.bin", upload, "big.bin", TransferOptions(resume=RESUME_OFF))
    assert job.state == State.STOPPED
    assert list(target.iterdir()) == []  # neither the file nor a part of it


def part_key(server: Server, here: Path, name: str, upload: bool) -> str:
    """The key of the note of this part file: on the server for an upload, here for a download."""
    if upload:
        return upload_key(server[1], f"/{name}{PART_SUFFIX}")
    return download_key(os.path.realpath(here / name) + PART_SUFFIX)


def note_part(server: Server, here: Path, name: str, upload: bool, size: int, modified: float, part_size: int, source: str = "") -> str:
    """A note as the program makes it when it keeps a part; source: another name of the same folder, for a part of another file."""
    origin = source or name
    whose = upload_source(str(here / origin)) if upload else download_source(server[1], f"/{origin}")
    key = part_key(server, here, name, upload)
    parts.remember(key, PartRecord(whose, size, modified, part_size))
    return key


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_is_continued_only_from_the_very_source_its_note_names_unchanged(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    write(source / "changed.txt", "the new version", NEWER_TIME)
    write(target / ("changed.txt" + PART_SUFFIX), "XXX")
    note_part(server, here, "changed.txt", upload, 15, OLD_TIME, 3)  # noted with another date: it changed since
    write(source / "other.txt", "abcdef", OLD_TIME)
    write(target / ("other.txt" + PART_SUFFIX), "XYZ")
    note_part(server, here, "other.txt", upload, 6, OLD_TIME, 3, source="elsewhere.txt")  # bytes of another file, same size and date
    write(source / "unnoted.txt", "abcdef", OLD_TIME)
    write(target / ("unnoted.txt" + PART_SUFFIX), "XYZ")  # no note: nothing is known about the part, so it is not continued
    options = TransferOptions(resume=RESUME_ON)
    for name in ("changed.txt", "other.txt", "unnoted.txt"):
        job = up(server, here / name, options) if upload else down(server, f"/{name}", here, options)
        assert job.state == State.DONE, job.message
        assert parts.lookup(part_key(server, here, name, upload)) is None  # arrived: the note goes
    assert [read(target / name) for name in ("changed.txt", "other.txt", "unnoted.txt")] == ["the new version", "abcdef", "abcdef"]
    assert sorted(path.name for path in target.iterdir() if path.name.endswith(PART_SUFFIX)) == ["unnoted.txt" + PART_SUFFIX]  # not ours: never touched
    assert read(target / ("unnoted.txt" + PART_SUFFIX)) == "XYZ"


def test_the_notes_of_part_files_are_input_and_a_bad_one_is_left_out() -> None:
    path = saved_settings.config_folder() / parts.FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    good = {"source": "s", "size": 5, "modified": OLD_TIME, "part_size": 2}
    bad_ones = [
        {**good, "size": -1}, {**good, "size": True}, {**good, "modified": "x"}, {**good, "source": 3}, {**good, "part_size": 6}, {**good, "part_size": -1},
        {key: value for key, value in good.items() if key != "part_size"},
    ]
    for text in ("{broken", "[1, 2]", "null", '{"k": {"source": "s", "size": 1, "modified": NaN, "part_size": 0}}', "[" * 100_000, *(json.dumps({"k": bad}) for bad in bad_ones)):
        path.write_text(text, encoding="utf-8")
        assert parts.lookup("k") is None
    path.write_text(json.dumps({"k": good, "bad": {**good, "size": 2**70}}), encoding="utf-8")
    assert parts.lookup("k") == PartRecord("s", 5, OLD_TIME, 2) and parts.lookup("bad") is None


def test_resume_in_the_question_continues_the_part_next_to_the_file_there(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "a.txt", "0123456789")
    write(root / "a.txt", "old")
    write(root / ("a.txt" + PART_SUFFIX), "XYZ")
    note_part(server, tmp_path, "a.txt", True, 10, (tmp_path / "a.txt").stat().st_mtime, 3)
    person = Person(Answer(Choice.RESUME))
    job = up(server, tmp_path / "a.txt", TransferOptions(), person)  # asked for, a part is continued however small
    assert job.state == State.DONE, job.message
    assert person.asked[0].part_size == 3
    assert read(root / "a.txt") == "XYZ3456789"


def test_smart_resume_keeps_part_files_of_large_files_only() -> None:
    smart = TransferOptions()
    assert resumable(smart, SMART_RESUME_BYTES) and not resumable(smart, SMART_RESUME_BYTES - 1)
    assert resumable(TransferOptions(resume=RESUME_ON), 1)
    assert not resumable(TransferOptions(resume=RESUME_OFF), 10 * SMART_RESUME_BYTES)


def test_tidying_keeps_only_a_part_file_that_can_still_be_continued() -> None:
    entries = [RemoteEntry("kept" + PART_SUFFIX, False, 5, 1.0), RemoteEntry("dead" + PART_SUFFIX, False, 5, 1.0)]
    remote = Leftovers(entries)
    class Keeping(Owned):
        @override
        def keep(self, path: str, size: int) -> bool:
            return path.startswith("/srv/kept")

    ours(entries)
    tidy(remote, "/srv", entries, long_known(entries), Keeping(LEFTOVER_LOGIN, TransferOptions()))
    assert remote.done == ["delete /srv/dead.chrono-ftp-part"]


def test_tidying_judges_a_part_by_the_resume_setting_it_was_kept_by(tmp_path: Path) -> None:
    """A 50 KB file sent with resume Always for this one transfer, while Settings say Smart (which keeps no part under 100 KB): its
    part stays when the folder is looked at long after."""
    source = tmp_path / "small.bin"
    source.write_bytes(b"s" * (50 * 1024))
    details = source.stat()
    entries = [RemoteEntry("small.bin" + PART_SUFFIX, False, 1000, time.time() - LEFTOVER_SECONDS - 60)]
    for resume, kept in ((RESUME_ON, True), (RESUME_SMART, False)):
        parts.remember(upload_key(LEFTOVER_LOGIN, "/srv/small.bin" + PART_SUFFIX), PartRecord(upload_source(str(source)), details.st_size, details.st_mtime, 1000, resume))
        remote = Leftovers(entries)
        tidy(remote, "/srv", entries, long_known(entries), Owned(LEFTOVER_LOGIN, TransferOptions(resume=RESUME_SMART)))
        assert remote.done == ([] if kept else ["delete /srv/small.bin.chrono-ftp-part"]), resume


def test_a_part_file_can_be_continued_only_with_a_note_that_passes_the_resume_rule(tmp_path: Path) -> None:
    login = Connection(name="T", host="h", user="u")
    path = "/srv/a.bin" + PART_SUFFIX
    key = upload_key(login, path)
    small, big = tmp_path / "small.bin", tmp_path / "big.bin"
    small.write_bytes(b"s" * 1000)
    big.write_bytes(b"b" * SMART_RESUME_BYTES)
    assert not continuable(login, path, 5, TransferOptions(resume=RESUME_ON))  # no note
    parts.remember(key, PartRecord(upload_source(str(small)), 1000, small.stat().st_mtime, 5))
    assert continuable(login, path, 5, TransferOptions(resume=RESUME_ON))
    assert not continuable(login, path, 5, TransferOptions())  # smart: a source of 1000 bytes is no candidate
    parts.remember(key, PartRecord(upload_source(str(big)), SMART_RESUME_BYTES, big.stat().st_mtime, 5))  # the part's one note: replaced
    assert continuable(login, path, 5, TransferOptions()) and not continuable(login, path, 5, TransferOptions(resume=RESUME_OFF))
    big.write_bytes(b"c" * SMART_RESUME_BYTES)
    os.utime(big, (NEWER_TIME, NEWER_TIME))
    assert not continuable(login, path, 5, TransferOptions())  # its source changed since


def test_only_new_and_updated_holds_also_after_yes_to_all() -> None:
    """A hard rule: an older file never replaces a newer one, whatever answer for all was given."""
    older = Conflict("a.txt", "/", True, 5, OLD_TIME, 5, NEWER_TIME, None)
    for batch in (Choice.YES_TO_ALL, Choice.ONLY_IF_NEWER, None):
        assert decided(TransferOptions(only_newer=True), Answers(batch), older) == Action.SKIP, batch


@pytest.mark.parametrize("why", ["its-source-has-another-size", "no-byte-confirmed"])
def test_a_part_that_can_not_be_continued_is_no_part_to_keep(tmp_path: Path, why: str) -> None:
    login = Connection(name="T", host="h", user="u")
    path = "/srv/a.bin" + PART_SUFFIX
    source = tmp_path / "a.bin"
    source.write_bytes(b"s" * 1000)
    moment = source.stat().st_mtime
    noted_size = 999 if why == "its-source-has-another-size" else 1000  # the same date either way
    parts.remember(upload_key(login, path), PartRecord(upload_source(str(source)), noted_size, moment, 0 if why == "no-byte-confirmed" else 5))
    assert not continuable(login, path, 5, TransferOptions(resume=RESUME_ON))


def test_the_dates_a_server_kept_are_counted_over_every_way_of_a_job(tmp_path: Path) -> None:
    """The look-over for rsync and the way after it each kept some dates: the note counts them all."""
    connection = FtpConnection(Connection(name="T", host="127.0.0.1", user=USER, protocol=FTP), PASSWORD)
    item = Job(connection, connection.connection, str(tmp_path), "/", True, "a")
    ways = [FileWay(item, connection, 0, Meter(item, lambda changed: None), lambda conflict: Answer(Choice.NO)) for _ in range(2)]
    ways[0].dates_not_kept, ways[1].dates_not_kept = 1, 2
    queue = TransferQueue(lambda changed: None)
    try:
        queue._note(item, ways, {})
    finally:
        queue.close()  # its thread ends
    assert item.message == "Done. The server kept its own date for 3 of these items."


# --- what a connection can do (FB-01, OR-7)


def test_an_ftp_server_says_what_it_can_do_for_a_transfer(tmp_path: Path) -> None:
    for without, expected in (((), (True, True, True)), (("REST", "MFMT", "SITE CHMOD"), (False, False, False))):
        with ftp_server(tmp_path, without=without) as port:
            connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
            connection.connect()
            found = connection.capabilities
            connection.close()
        assert (found.resume, found.timestamps, found.rights) == expected


def test_what_a_connection_can_not_do_is_off_and_says_why() -> None:
    wanted = TransferOptions(set_rights=True)
    options, reasons = usable(wanted, Capabilities(), FTP, True)
    assert set(reasons) == {RESUME, KEEP_TIMESTAMPS, SET_RIGHTS}
    assert all("does not offer" in text for text in reasons.values())
    assert (options.resume, options.keep_timestamps, options.set_rights) == (RESUME_OFF, False, False)
    options, reasons = usable(wanted, Capabilities(resume=True, timestamps=True, rights=True), SFTP, True)
    assert reasons == {} and options == wanted
    options, reasons = usable(wanted, Capabilities(), FTP, False)
    assert KEEP_TIMESTAMPS not in reasons and options.keep_timestamps  # a download sets the date on this computer
    assert set(reasons) == {RESUME, SET_RIGHTS} and not options.set_rights


# --- the defaults in the settings


def test_the_default_options_are_kept_and_a_bad_value_gives_its_standard_one() -> None:
    chosen = TransferOptions(resume=RESUME_ON, only_newer=True, set_rights=True, rights="0750", count_first=False, delete_source=True)
    saved_settings.save(saved_settings.Settings(transfer_options=chosen, show_transfer_window=False))
    loaded = saved_settings.load()
    assert loaded.transfer_options == replace(chosen, delete_source=False)  # a move is chosen per transfer, never kept
    assert not loaded.show_transfer_window
    path = saved_settings.config_folder() / saved_settings.FILE_NAME
    bad = {"transfer_options": {"resume": "sometimes", "rights": "999", "only_newer": "yes", "count_first": False, "delete_source": True}, "show_transfer_window": 3}
    path.write_text(json.dumps(bad), encoding="utf-8")
    loaded = saved_settings.load()
    assert loaded.transfer_options == TransferOptions(count_first=False)
    assert loaded.show_transfer_window
    path.write_text(json.dumps({"transfer_options": ["not", "a", "mapping"]}), encoding="utf-8")
    assert saved_settings.load().transfer_options == TransferOptions()


def test_a_saved_value_of_another_type_is_never_taken() -> None:
    path = saved_settings.config_folder() / saved_settings.FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"transfer_options": {"rights": 755, "resume": 5}, "show_transfer_window": 0}), encoding="utf-8")
    loaded = saved_settings.load()
    assert loaded.transfer_options.rights == TransferOptions().rights and loaded.transfer_options.resume == TransferOptions().resume
    assert loaded.show_transfer_window is True


def test_a_move_is_never_written_into_the_saved_settings() -> None:
    saved_settings.save(saved_settings.Settings(transfer_options=TransferOptions(delete_source=True)))
    written = json.loads((saved_settings.config_folder() / saved_settings.FILE_NAME).read_text(encoding="utf-8"))
    assert "delete_source" not in written["transfer_options"]


def test_the_time_a_question_waits_is_no_transfer_time(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The person thinks for a while: that time is set aside, so the speed and the time left count only time spent transferring."""
    root = server[2]
    write(root / "a.txt", "old")
    write(tmp_path / "a.txt", "new")
    paused: list[float] = []
    held: list[float] = []
    real_paused = Meter.paused

    def noted(meter: Meter, seconds: float) -> None:
        paused.append(seconds)
        real_paused(meter, seconds)

    class Thinking(Person):
        @override
        def __call__(self, job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
            started = time.monotonic()
            threading.Event().wait(0.3)  # the person thinks
            held.append(time.monotonic() - started)
            super().__call__(job, conflict, reply)

    monkeypatch.setattr(Meter, "paused", noted)
    job = up(server, tmp_path / "a.txt", TransferOptions(), Thinking(Answer(Choice.YES)))
    assert job.state == State.DONE, job.message
    assert len(paused) == 1 and paused[0] >= held[0]


# --- review round 1: what arrives is the source as it was read; dates no clock can hold; unknown dates are told


def test_sftp_refuses_a_date_it_can_not_carry_as_a_normal_refusal(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    write(root / "a.txt", "a")
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        for moment in (-100_000.0, 5e9, float("nan")):
            with pytest.raises(RemoteActionFailed):
                connection.set_time("/a.txt", moment)
        connection.close()


@pytest.mark.parametrize("moment", [-100_000.0, 5e9])
def test_a_date_no_server_clock_can_hold_still_lets_the_file_arrive(server: Server, tmp_path: Path, moment: float) -> None:
    connection, _settings, root = server
    write(tmp_path / "tree" / "a.txt", "a", moment)
    os.utime(tmp_path / "tree", (moment, moment))
    job = up(server, tmp_path / "tree")
    assert job.state == State.DONE, job.message
    assert read(root / "tree" / "a.txt") == "a"
    assert not any(path.name.endswith(PART_SUFFIX) for path in (root / "tree").iterdir())
    if isinstance(connection, SshConnection):
        assert "kept its own date for 2 of these items" in job.message  # the file and its folder


def test_an_unexpected_error_never_leaves_the_temporary_file_behind(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server

    def broken(_self: RemoteConnection, path: str, modified: float) -> None:
        raise ValueError("nobody expected this")

    monkeypatch.setattr(type(connection), "set_time", broken)
    write(tmp_path / "a.txt", "a")
    job = up(server, tmp_path / "a.txt", TransferOptions(resume=RESUME_ON))
    assert job.state == State.FAILED
    assert list(root.iterdir()) == []
    assert parts.lookup(part_key(server, tmp_path, "a.txt", True)) is None


@pytest.mark.parametrize("upload", [True, False])
def test_a_file_that_grows_while_it_is_sent_never_gets_the_real_name(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    write(source / "log.txt", "first line\n")
    kind = type(connection)
    sending = kind.upload if upload else kind.download

    def growing(self: RemoteConnection, first: str, second: str, progress: Callable[[int], None], limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        sending(self, first, second, progress, limit, offset, append, exclusive)
        with (source / "log.txt").open("a", encoding="utf-8") as file:
            file.write("a line written meanwhile\n")
        if not upload:
            with Path(second).open("a", encoding="utf-8") as file:
                file.write("more than was listed\n")  # what arrived is not what the listing said

    monkeypatch.setattr(kind, "upload" if upload else "download", growing)
    options = TransferOptions(delete_source=True)
    job = up(server, here / "log.txt", options) if upload else down(server, "/log.txt", here, options)
    assert job.state == State.FAILED and job.message == (CHANGED_HERE_TEXT if upload else CHANGED_THERE_TEXT).format(name="log.txt")
    assert not (target / "log.txt").exists() and not (target / ("log.txt" + PART_SUFFIX)).exists()
    assert read(source / "log.txt") == "first line\na line written meanwhile\n"  # the move never took it


@pytest.mark.parametrize("upload", [True, False])
def test_a_move_keeps_a_source_that_changed_after_it_arrived_and_says_so(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    write(source / "a.txt", "as read", OLD_TIME)
    kind = type(connection)
    renaming = kind.rename
    real_link = os.link
    arrived = os.path.realpath(target / "a.txt")

    def changed_meanwhile(self: RemoteConnection, old: str, new: str) -> None:
        renaming(self, old, new)
        write(source / "a.txt", "changed afterwards")

    def changed_on_the_server(old: str, new: str) -> None:
        real_link(old, new)  # a new file takes its name with a link, which never replaces anything
        if new == arrived:  # only the one file this test is about
            write(source / "a.txt", "changed afterwards")

    with monkeypatch.context() as patch:  # only this patch ends here; the protection of the settings and passwords stays
        if upload:
            patch.setattr(kind, "rename", changed_meanwhile)
        else:
            patch.setattr(os, "link", changed_on_the_server)
        options = TransferOptions(delete_source=True)
        job = up(server, here / "a.txt", options) if upload else down(server, "/a.txt", here, options)
    assert job.state == State.DONE, job.message
    assert job.message == "Done. 1 original stayed where it was, because it changed while it was sent; the copy that arrived is whole."
    assert read(target / "a.txt") == "as read"
    assert read(source / "a.txt") == "changed afterwards"


def unknown_dates(monkeypatch: pytest.MonkeyPatch, connection: RemoteConnection) -> None:
    """The server lists no dates, as some do."""
    kind = type(connection)
    listing = kind.list_folder

    def without_dates(self: RemoteConnection, path: str) -> list[RemoteEntry]:
        return [replace(entry, modified=None) for entry in listing(self, path)]

    monkeypatch.setattr(kind, "list_folder", without_dates)


def test_only_newer_never_replaces_without_knowing_and_says_how_many_it_left(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    for name in ("a.txt", "b.txt"):
        write(tmp_path / "tree" / name, "new")
        write(root / "tree" / name, "old")
    unknown_dates(monkeypatch, connection)
    job = up(server, tmp_path / "tree", TransferOptions(only_newer=True), Person())
    assert job.state == State.DONE
    assert job.message.startswith("Done.")  # over FTP also a note on the date of the folder, which this server can't set
    assert "2 items were left as they were: their date is unknown, so it can't be told which is newer." in job.message
    assert [read(root / "tree" / name) for name in ("a.txt", "b.txt")] == ["old", "old"]


def test_only_newer_with_the_question_still_asks_about_a_newer_file(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "tree" / "newer.txt", "new", NEWER_TIME)
    write(tmp_path / "tree" / "older.txt", "new", OLD_TIME)
    write(root / "tree" / "newer.txt", "old", OLD_TIME)
    write(root / "tree" / "older.txt", "old", NEWER_TIME)
    person = Person(Answer(Choice.NO))
    job = up(server, tmp_path / "tree", TransferOptions(only_newer=True, ask_before_replacing=True), person)
    assert job.state == State.DONE, job.message
    assert [item.name for item in person.asked] == ["newer.txt"]  # the older one is skipped without a question
    assert [read(root / "tree" / name) for name in ("newer.txt", "older.txt")] == ["old", "old"]


def test_newer_means_a_later_whole_second() -> None:
    assert not is_newer(100.9, 100.1) and not is_newer(100.0, 100.0)
    assert is_newer(101.0, 100.9) and not is_newer(None, 1.0) and not is_newer(1.0, None)


def test_rename_to_a_name_that_is_not_safe_fails_and_changes_nothing(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "a.txt", "new")
    write(root / "a.txt", "old")
    job = up(server, tmp_path / "a.txt", person=Person(Answer(Choice.RENAME, "../escape.txt")))
    assert job.state == State.FAILED and job.message == NOT_A_NAME_TEXT
    assert sorted(path.name for path in root.iterdir()) == ["a.txt"] and read(root / "a.txt") == "old"


@pytest.mark.parametrize("upload", [True, False])
def test_smart_resume_keeps_no_part_of_a_small_file_that_was_stopped(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    (source / "small.bin").write_bytes(os.urandom(SMART_SMALL))
    connection, settings, _root = server
    queue = TransferQueue(lambda item: None)
    job = Job(connection, settings, str(here / "small.bin" if upload else here), "/" if upload else "/small.bin", upload, "small.bin", has_password=True)
    with held_after_its_first_bytes(job):  # it waits after its first bytes until the stop: never done before it
        try:
            queue.add(job)
            deadline = time.monotonic() + WAIT_SECONDS
            while job.done == 0 and job.state in (State.WAITING, State.RUNNING):
                assert time.monotonic() < deadline, "the transfer did not get under way"
                threading.Event().wait(0.01)
            queue.stop(job)
            finished = threading.Event()
            threading.Thread(target=lambda: wait_for((job,), finished), daemon=True).start()
            assert finished.wait(WAIT_SECONDS)
        finally:
            queue.close()  # also when the test fails: no thread of it runs on
    assert job.state == State.STOPPED
    assert [path.name for path in target.iterdir() if path.name.startswith("small")] == []


def test_the_question_says_when_the_server_never_gave_the_size_of_the_file_there(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, settings, root = server
    write(root / "a.txt", "old")
    write(tmp_path / "a.txt", "new")
    kind = type(connection)
    listing = kind.list_folder

    def without_sizes(self: RemoteConnection, path: str) -> list[RemoteEntry]:
        return [replace(entry, size=0, size_known=False) for entry in listing(self, path)]

    monkeypatch.setattr(kind, "list_folder", without_sizes)
    person = Person(Answer(Choice.NO))
    up(server, tmp_path / "a.txt", TransferOptions(), person)
    assert [(conflict.target_size_known, conflict.source_size_known, conflict.connection) for conflict in person.asked] == [(False, True, settings.name)]
