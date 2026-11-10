import errno
import os
import threading
from collections.abc import Callable, Generator
from pathlib import Path
from typing import cast, override

import pytest

from chrono_ftp.connections.base import Capabilities, LocalFile, RemoteConnection
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ConnectionFailed, NoMoreLogins
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers import filebyfile
from chrono_ftp.transfers.filebyfile import undo_broken_appends
from chrono_ftp.transfers.options import Answer, Answers, Choice, Conflict
from chrono_ftp.transfers.queue import ENDED, HELD_NEXT, HELD_PLACE, HELD_ROOM, Job, State, Stopped, TransferQueue
from chrono_ftp.transfers.schedule import Place, meet, places, share
from tests.fakeservers import NOT_AS_ROOT, PASSWORD, USER, ftp_server, ssh_server
from tests.test_connections import trusted_ssh
from tests.test_transfer_queue import REPLACING, SIZE, SLOW, ftp, source_file, until, upload

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_parallel.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial: several transfers at once (FC-01): two uploads at the same time, over FTP and SFTP; three items with room for two; a
#          login the server refuses waits and runs later, and the queue keeps to what the server takes; items on the same place wait for
#          each other; one question at a time, and Yes to all holds for the other item of the action; the number raised and lowered
#          while items run; one of two stopped; pause all and resume all; a dropped line, a full disk and a folder without rights on one
#          of two items; the places two items meet on; the share of the speed limit; a pause taken back while the run still ends never
#          starts the item twice; a login that could not be made for another reason teaches the queue nothing about the server; two
#          logins refused at the same moment never keep both items waiting; a later item never overtakes an earlier one on its place;
#          a new login after a broken-off file that the server refuses waits calmly; a refusal of a moment does not last the session; a
#          job waiting to look at broken appends sees its stop, and other logins never wait; Run Now with every place taken says it runs
#          next

__all__ = ["ftp"]  # the fixture of the queue tests

ASKING: TransferOptions = TransferOptions()  # every file that is there already is asked about


@pytest.fixture
def two_at_once() -> Generator[TransferQueue]:
    made = TransferQueue(lambda item: None)
    made.settings(True, 15.0, False, 2)
    yield made
    made.close()


def slow(queue: TransferQueue, *items: Job) -> None:
    """Each item in the queue at a speed that keeps it running until the test lets it go."""
    for item in items:
        item.limit = SLOW
        queue.add(item)


def let_go(queue: TransferQueue, *items: Job) -> None:
    for item in items:
        queue.set_limit(item, 0)


def running(*items: Job) -> Callable[[], bool]:
    """All of them transfer at this moment, each with something sent."""
    return lambda: all(item.state == State.RUNNING and item.done > 0 for item in items)


def download(connection: RemoteConnection, settings: Connection, folder: Path, name: str) -> Job:
    return Job(connection, settings, str(folder), f"/{name}", False, name, has_password=True, options=REPLACING)


# --- each function does what it should


def test_two_uploads_run_at_the_same_time_and_both_arrive_whole(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue) -> None:
    _connection, _settings, root = ftp
    first, second = upload(ftp, source_file(tmp_path, "a.bin")), upload(ftp, source_file(tmp_path, "b.bin"))
    slow(two_at_once, first, second)
    until(running(first, second))  # one FTP login does one thing at a time: both on their way means a login each
    let_go(two_at_once, first, second)
    until(lambda: first.state == second.state == State.DONE)
    assert (root / "a.bin").read_bytes() == (tmp_path / "a.bin").read_bytes() and (root / "b.bin").read_bytes() == (tmp_path / "b.bin").read_bytes()


def test_two_uploads_over_one_ssh_login_run_at_the_same_time_and_both_arrive_whole(tmp_path: Path, two_at_once: TransferQueue) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        ssh = trusted_ssh(server)
        items = [Job(ssh, ssh.connection, str(source_file(tmp_path, name)), "/", True, name, has_password=True, options=REPLACING) for name in ("a.bin", "b.bin")]
        slow(two_at_once, *items)
        until(running(*items))
        let_go(two_at_once, *items)
        until(lambda: all(item.state == State.DONE for item in items))
        ssh.close()
    assert all((root / name).read_bytes() == (tmp_path / name).read_bytes() for name in ("a.bin", "b.bin"))


def test_with_room_for_two_the_third_item_starts_when_one_ended(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue) -> None:
    first, second, third = (upload(ftp, source_file(tmp_path, name)) for name in ("a.bin", "b.bin", "c.bin"))
    slow(two_at_once, first, second, third)
    until(running(first, second))
    assert third.state == State.WAITING and third.held == HELD_NEXT  # only its turn: it is the next to start
    let_go(two_at_once, first)
    until(lambda: first.state == State.DONE and third.state == State.RUNNING)
    assert second.state == State.RUNNING
    let_go(two_at_once, second, third)
    until(lambda: second.state == third.state == State.DONE)


def test_a_login_the_server_refuses_waits_and_runs_later(tmp_path: Path, two_at_once: TransferQueue) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ftp_server(root, most_logins=2) as port:  # the tab's own login and one more
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        browsing = FtpConnection(settings, PASSWORD)
        browsing.connect()
        items = [Job(browsing, settings, str(source_file(tmp_path, name)), "/", True, name, has_password=True, options=REPLACING) for name in ("a.bin", "b.bin")]
        slow(two_at_once, *items)
        until(lambda: any(item.held == HELD_ROOM for item in items) and any(item.state == State.RUNNING and item.done > 0 for item in items))
        held = next(item for item in items if item.held == HELD_ROOM)
        assert held.state == State.WAITING and held.message == ""  # no failure: it waits for its turn
        let_go(two_at_once, *items)
        until(lambda: all(item.state == State.DONE for item in items))
        browsing.close()
    assert all((root / name).read_bytes() == (tmp_path / name).read_bytes() for name in ("a.bin", "b.bin"))


def test_a_refusal_of_a_moment_does_not_keep_the_server_one_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_at_once: TransferQueue) -> None:
    root = tmp_path / "server"
    root.mkdir()
    logins: list[int] = []
    real = FtpConnection.connect

    def counted(self: FtpConnection) -> None:
        logins.append(1)
        real(self)

    with ftp_server(root, most_logins=3) as port:  # the tab's own login and two more
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        browsing, other_program = FtpConnection(settings, PASSWORD), FtpConnection(settings, PASSWORD)
        browsing.connect()
        other_program.connect()  # holds a login for a moment
        items = [Job(browsing, settings, str(source_file(tmp_path, name)), "/", True, name, has_password=True, options=REPLACING) for name in ("a.bin", "b.bin")]
        slow(two_at_once, *items)
        until(lambda: any(item.held == HELD_ROOM for item in items) and any(item.state == State.RUNNING and item.done > 0 for item in items))
        other_program.close()  # gone again
        let_go(two_at_once, *items)
        until(lambda: all(item.state == State.DONE for item in items))
        monkeypatch.setattr(FtpConnection, "connect", counted)
        later = [Job(browsing, settings, str(source_file(tmp_path, name)), "/", True, name, has_password=True, options=REPLACING) for name in ("c.bin", "d.bin")]
        slow(two_at_once, *later)
        until(running(*later))  # two at once again: the items that went well let the queue try more
        let_go(two_at_once, *later)
        until(lambda: all(item.state == State.DONE for item in later))
        assert len(logins) == 2
        browsing.close()
    assert all((root / name).read_bytes() == (tmp_path / name).read_bytes() for name in ("a.bin", "b.bin", "c.bin", "d.bin"))


def test_items_on_the_same_place_wait_for_each_other_and_others_run_past_them(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue) -> None:
    connection, settings, root = ftp
    site = tmp_path / "site"
    site.mkdir()
    source_file(site, "big.bin")
    inside, other = source_file(tmp_path, "x.txt", 10), source_file(tmp_path, "y.txt", 10)
    first = Job(connection, settings, str(site), "/", True, "site", has_password=True, options=REPLACING, limit=SLOW)
    again = Job(connection, settings, str(site), "/", True, "site", has_password=True, options=REPLACING)  # the same folder dropped twice
    into = Job(connection, settings, str(inside), "/site", True, "x.txt", has_password=True, options=REPLACING)  # into the folder on its way
    past = Job(connection, settings, str(other), "/", True, "y.txt", has_password=True, options=REPLACING)
    for item in (first, again, into, past):
        two_at_once.add(item)
    until(lambda: first.done > 0 and past.state == State.DONE)  # the last one ran past the two that wait
    assert (again.state, again.held, into.state, into.held) == (State.WAITING, HELD_PLACE, State.WAITING, HELD_PLACE)
    let_go(two_at_once, first)
    until(lambda: all(item.state == State.DONE for item in (first, again, into)))
    assert (root / "site" / "big.bin").read_bytes() == (site / "big.bin").read_bytes() and (root / "site" / "x.txt").is_file()


def test_one_question_at_a_time_and_yes_to_all_holds_for_the_other_item_of_the_action(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, settings, root = ftp
    asked: list[tuple[Job, Conflict, Callable[[Answer], None]]] = []
    queue = TransferQueue(lambda item: None, ask=lambda item, conflict, reply: asked.append((item, conflict, reply)))
    queue.settings(True, 15.0, False, 2)
    answers = Answers()  # one action of the person: both items
    items = []
    for name in ("a.txt", "b.txt"):
        (root / name).write_text("old", encoding="utf-8")
        (tmp_path / name).write_text("new", encoding="utf-8")
        items.append(Job(connection, settings, str(tmp_path / name), "/", True, name, has_password=True, options=ASKING, answers=answers))
        queue.add(items[-1])
    until(lambda: len(asked) == 1 and all(item.waiting_for_answer for item in items))  # the other waits for its turn
    asked[0][2](Answer(Choice.YES_TO_ALL))
    until(lambda: all(item.state == State.DONE for item in items))
    queue.close()
    assert len(asked) == 1 and [(root / name).read_text(encoding="utf-8") for name in ("a.txt", "b.txt")] == ["new", "new"]


def test_the_next_question_comes_once_the_one_before_is_answered(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, settings, root = ftp
    asked: list[tuple[Job, Conflict, Callable[[Answer], None]]] = []
    queue = TransferQueue(lambda item: None, ask=lambda item, conflict, reply: asked.append((item, conflict, reply)))
    queue.settings(True, 15.0, False, 2)
    items = []
    for name in ("a.txt", "b.txt"):
        (root / name).write_text("old", encoding="utf-8")
        (tmp_path / name).write_text("new", encoding="utf-8")
        items.append(Job(connection, settings, str(tmp_path / name), "/", True, name, has_password=True, options=ASKING))  # two actions
        queue.add(items[-1])
    until(lambda: len(asked) == 1 and all(item.waiting_for_answer for item in items))
    asked[0][2](Answer(Choice.YES))
    until(lambda: len(asked) == 2)
    assert asked[1][0] is not asked[0][0]
    asked[1][2](Answer(Choice.NO))
    until(lambda: all(item.state == State.DONE for item in items))
    queue.close()
    replaced = (root / asked[0][1].name).read_text(encoding="utf-8")
    kept = (root / asked[1][1].name).read_text(encoding="utf-8")
    assert (replaced, kept) == ("new", "old")


# --- mistakes a person makes


def test_raising_the_number_starts_waiting_items_and_lowering_it_stops_none(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    queue = TransferQueue(lambda item: None)
    first, second, third = (upload(ftp, source_file(tmp_path, name)) for name in ("a.bin", "b.bin", "c.bin"))
    slow(queue, first, second, third)  # one at a time
    until(running(first))
    queue.settings(True, 15.0, False, 3)
    until(running(first, second, third))
    queue.settings(True, 15.0, False, 1)
    fourth = upload(ftp, source_file(tmp_path, "d.bin"))
    slow(queue, fourth)
    let_go(queue, first)
    until(lambda: first.state == State.DONE)
    assert (second.state, third.state, fourth.state) == (State.RUNNING, State.RUNNING, State.WAITING)  # none stopped, none more
    let_go(queue, second, third, fourth)
    until(lambda: all(item.state == State.DONE for item in (second, third, fourth)))
    queue.close()


def test_stopping_one_of_two_running_items_lets_the_other_arrive_whole(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue) -> None:
    _connection, _settings, root = ftp
    first, second = upload(ftp, source_file(tmp_path, "a.bin")), upload(ftp, source_file(tmp_path, "b.bin"))
    slow(two_at_once, first, second)
    until(running(first, second))
    two_at_once.stop(first)
    until(lambda: first.state == State.STOPPED)
    assert second.state == State.RUNNING
    let_go(two_at_once, second)
    until(lambda: second.state == State.DONE)
    assert not (root / "a.bin").exists() and (root / "b.bin").read_bytes() == (tmp_path / "b.bin").read_bytes()


def test_pause_all_pauses_every_running_item_and_resume_all_finishes_them(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue) -> None:
    _connection, _settings, root = ftp
    items = [upload(ftp, source_file(tmp_path, name)) for name in ("a.bin", "b.bin")]
    slow(two_at_once, *items)
    until(running(*items))
    two_at_once.pause_all()
    until(lambda: all(item.state == State.PAUSED for item in items))
    let_go(two_at_once, *items)
    two_at_once.resume_all()
    until(lambda: all(item.state == State.DONE for item in items))
    assert all((root / name).read_bytes() == (tmp_path / name).read_bytes() for name in ("a.bin", "b.bin"))


# --- problems from outside


def test_a_dropped_line_on_one_of_two_running_items_leaves_the_other_whole(tmp_path: Path, two_at_once: TransferQueue) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ftp_server(root, drops_data_after=SIZE // 4) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        browsing = FtpConnection(settings, PASSWORD)
        browsing.connect()
        items = [Job(browsing, settings, str(source_file(tmp_path, name)), "/", True, name, has_password=True, options=REPLACING) for name in ("a.bin", "b.bin")]
        slow(two_at_once, *items)
        until(running(*items))
        let_go(two_at_once, *items)
        until(lambda: all(item.state in ENDED for item in items))
        assert sorted(item.state.value for item in items) == [State.DONE.value, State.FAILED.value]
        broken = next(item for item in items if item.state == State.FAILED)
        two_at_once.retry(broken)
        until(lambda: broken.state == State.DONE)
        browsing.close()
    assert all((root / name).read_bytes() == (tmp_path / name).read_bytes() for name in ("a.bin", "b.bin"))


def test_a_full_disk_on_one_of_two_downloads_fails_only_that_one(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, settings, root = ftp
    here = tmp_path / "here"
    here.mkdir()
    for name in ("a.bin", "b.bin"):
        (root / name).write_bytes(os.urandom(SIZE))
    real = LocalFile.write

    def full_for_a(self: LocalFile, data: bytes) -> None:
        if "a.bin" in str(self.file.name):
            raise self._problem(OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)))  # as a disk without room says it
        real(self, data)

    monkeypatch.setattr(LocalFile, "write", full_for_a)  # only a's copy has no room
    full, fine = download(connection, settings, here, "a.bin"), download(connection, settings, here, "b.bin")
    two_at_once.add(full)
    two_at_once.add(fine)
    until(lambda: full.state in ENDED and fine.state in ENDED)
    assert (full.state, fine.state) == (State.FAILED, State.DONE) and os.strerror(errno.ENOSPC) in full.message
    assert (here / "b.bin").read_bytes() == (root / "b.bin").read_bytes()


@NOT_AS_ROOT
def test_a_folder_without_rights_fails_only_the_item_that_goes_there(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue) -> None:
    connection, settings, root = ftp
    (root / "locked").mkdir()
    (root / "locked").chmod(0o555)
    try:
        refused = Job(connection, settings, str(source_file(tmp_path, "a.bin")), "/locked", True, "a.bin", has_password=True, options=REPLACING)
        fine = upload(ftp, source_file(tmp_path, "b.bin"))
        two_at_once.add(refused)
        two_at_once.add(fine)
        until(lambda: refused.state in ENDED and fine.state in ENDED)
        assert (refused.state, fine.state) == (State.FAILED, State.DONE) and refused.message
        assert (root / "b.bin").read_bytes() == (tmp_path / "b.bin").read_bytes()
    finally:
        (root / "locked").chmod(0o755)


# --- the rules themselves


def test_two_items_meet_where_one_writes_on_the_same_place_or_inside_it_on_the_same_side() -> None:
    server = ("ftp", "h", 21, "u")
    other = ("ftp", "h", 21, "v")
    assert meet(Place(server, "/a", True), Place(server, "/a/b", False))
    assert meet(Place(None, "/x/a", False), Place(None, "/x", True))
    assert not meet(Place(server, "/a", False), Place(server, "/a", False))  # two that only read
    assert not meet(Place(server, "/a", True), Place(other, "/a", True))  # another login
    assert not meet(Place(server, "/ab", True), Place(server, "/a", True))  # a name that only starts alike
    assert not meet(Place(None, "/a", True), Place(server, "/a", True))  # this computer and a server


def test_an_upload_writes_its_target_and_a_move_also_its_source() -> None:
    settings = Connection(name="T", host="H", port=21, user="u", protocol=FTP)
    copy = Job(FtpConnection(settings), settings, "/home/a/site", "/srv/", True, "site")
    move = Job(FtpConnection(settings), settings, "/home/a", "/srv/site", False, "site", options=TransferOptions(delete_source=True))
    assert places(copy) == (Place(None, "/home/a/site", False), Place(("ftp", "h", 21, "u"), "/srv/site", True))
    assert places(move) == (Place(("ftp", "h", 21, "u"), "/srv/site", True), Place(None, "/home/a/site", True))


def test_the_speed_limit_of_the_queue_is_shared_by_the_items_that_follow_it(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    assert (share(0, 3), share(100, 1), share(100, 3), share(1, 3)) == (0, 100, 33, 1)
    queue = TransferQueue(lambda item: None)
    queue.settings(True, 15.0, False, 3)
    queue.limit = SLOW * 2
    own, first, second = (upload(ftp, source_file(tmp_path, name)) for name in ("a.bin", "b.bin", "c.bin"))
    own.limit = SLOW  # a limit of its own: it keeps that one, outside the queue's
    for item in (own, first, second):
        queue.add(item)
    until(running(own, first, second))
    assert (queue.limit_of(own), queue.limit_of(first), queue.limit_of(second)) == (SLOW, SLOW, SLOW)
    queue.stop(first)
    until(lambda: first.state == State.STOPPED)
    assert queue.limit_of(second) == SLOW * 2  # the one left that follows it has all of it, at once
    queue.close()


class Holding(TransferQueue):
    """A queue whose jobs run until they are stopped, without a server; the test sees each run start and each pass of the queue's
    own thread."""

    def __init__(self, changed: Callable[[Job], None]) -> None:
        self.runs: list[Job] = []
        self.passed: threading.Event = threading.Event()
        super().__init__(changed)

    @override
    def _transfer(self, job: Job) -> None:
        self.runs.append(job)
        job.stop_asked.wait()
        raise Stopped

    @override
    def _start_what_can(self) -> tuple[list[Job], list[Job]]:
        found = super()._start_what_can()
        self.passed.set()
        return found


def test_a_pause_taken_back_while_others_run_never_starts_the_item_twice() -> None:
    ending, go_on, watching = threading.Event(), threading.Event(), threading.Event()

    def changed(item: Job) -> None:
        if item.state == State.WAITING and watching.is_set() and threading.current_thread() is not threading.main_thread():
            ending.set()
            go_on.wait(10)  # the run that ends is held here, still among the running ones, while the queue's thread looks again

    queue = Holding(changed)
    queue.settings(True, 15.0, False, 2)
    item = Job(FtpConnection(Connection(name="T", host="h", protocol=FTP)), Connection(name="T", host="h", protocol=FTP), "/a", "/", True, "a")
    queue.add(item)
    until(lambda: len(queue.runs) == 1)
    watching.set()
    queue.pause(item)
    queue.resume(item)  # taken back before the pause landed: it runs again
    until(ending.is_set)
    queue.passed.clear()
    queue.settings(True, 15.0, False, 2)  # anything that wakes the queue's thread meanwhile
    until(queue.passed.is_set)
    assert len(queue.runs) == 1  # not started again while its last run is still ending
    go_on.set()
    until(lambda: len(queue.runs) == 2 and item.state == State.RUNNING)
    assert queue.jobs == [item] and queue._running == [item]  # the queue sees the run that goes on: its count and its place hold
    queue.stop(item)
    until(lambda: item.state == State.STOPPED)
    queue.close()


def test_a_login_that_could_not_be_made_for_another_reason_teaches_nothing_about_the_server(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, settings, root = ftp
    first, second = upload(ftp, source_file(tmp_path, "a.bin")), upload(ftp, source_file(tmp_path, "b.bin"))
    slow(two_at_once, first)
    until(running(first))
    real = FtpConnection.for_a_transfer
    failed: list[int] = []

    def once_unreachable(self: FtpConnection) -> FtpConnection:
        if not failed:
            failed.append(1)
            raise ConnectionFailed("The server could not be reached.")  # a passing network problem, no refusal of the login
        return real(self)

    monkeypatch.setattr(FtpConnection, "for_a_transfer", once_unreachable)
    slow(two_at_once, second)
    until(lambda: second.held == HELD_ROOM)  # it waits for the other all the same, calmly
    let_go(two_at_once, first, second)
    until(lambda: first.state == second.state == State.DONE)
    third, fourth = upload(ftp, source_file(tmp_path, "c.bin")), upload(ftp, source_file(tmp_path, "d.bin"))
    slow(two_at_once, third, fourth)
    until(running(third, fourth))  # both at once: the queue learnt no limit from it
    let_go(two_at_once, third, fourth)
    until(lambda: third.state == fourth.state == State.DONE)
    assert all((root / name).read_bytes() == (tmp_path / name).read_bytes() for name in ("a.bin", "b.bin", "c.bin", "d.bin")) and connection is not None and settings is not None


def test_two_logins_refused_at_the_same_moment_never_keep_both_items_waiting(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, two_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    _connection, _settings, root = ftp
    real = FtpConnection.for_a_transfer
    both = threading.Barrier(2, timeout=10)
    refused: list[int] = []

    def refused_together(self: FtpConnection) -> FtpConnection:
        if len(refused) < 2:
            refused.append(1)
            both.wait()  # each refusal comes while the other item still runs, as two 421 replies at once
            raise NoMoreLogins("The server takes no more connections at the moment.")
        return real(self)

    monkeypatch.setattr(FtpConnection, "for_a_transfer", refused_together)
    items = [upload(ftp, source_file(tmp_path, name, 1024)) for name in ("a.bin", "b.bin")]
    for item in items:
        two_at_once.add(item)
    until(lambda: all(item.state == State.DONE for item in items))
    assert all((root / name).read_bytes() == (tmp_path / name).read_bytes() for name in ("a.bin", "b.bin"))


def test_a_later_item_never_overtakes_an_earlier_one_that_waits_on_its_place(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, settings, root = ftp
    site = tmp_path / "site"
    site.mkdir()
    source_file(site, "big.bin")
    here = tmp_path / "here"
    (here / "site").mkdir(parents=True)
    (here / "site" / "old.txt").write_text("old", encoding="utf-8")
    queue = TransferQueue(lambda item: None)
    queue.settings(True, 15.0, False, 3)
    first = Job(connection, settings, str(site), "/", True, "site", has_password=True, options=REPLACING, limit=SLOW)
    fetch = Job(connection, settings, str(here), "/site", False, "site", has_password=True, options=REPLACING)  # waits for the first
    send = Job(connection, settings, str(here / "site"), "/other", True, "site", has_password=True, options=REPLACING)  # what fetch writes
    (root / "other").mkdir()
    for item in (first, fetch, send):
        queue.add(item)
    until(lambda: first.done > 0 and fetch.held == HELD_PLACE and send.held == HELD_PLACE)
    assert send.state == State.WAITING  # it never runs before what comes earlier on its place
    let_go(queue, first)
    until(lambda: all(item.state == State.DONE for item in (first, fetch, send)))
    queue.close()
    assert (root / "other" / "site" / "big.bin").read_bytes() == (site / "big.bin").read_bytes()


def test_a_new_login_after_a_broken_off_file_that_the_server_refuses_waits_calmly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "c.txt").write_text("old", encoding="utf-8")
    (tmp_path / "c.txt").write_text("new", encoding="utf-8")
    site = tmp_path / "site"
    site.mkdir()
    for name in ("a.bin", "b.bin"):
        source_file(site, name)
    real = FtpConnection._start_afresh
    outsiders: list[FtpConnection] = []
    asked: list[Callable[[Answer], None]] = []

    with ftp_server(root, drops_data_after=SIZE // 4, most_logins=3) as port:  # the tab's login and two more
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)

        def slot_taken(self: FtpConnection) -> None:
            real(self)
            while not outsiders:  # another program takes the login that was just let go, before the item logs in again
                other = FtpConnection(settings, PASSWORD)
                try:
                    other.connect()
                    outsiders.append(other)
                except ConnectionFailed:
                    threading.Event().wait(0.05)  # the server has not yet seen the old login go: it is free in a moment

        monkeypatch.setattr(FtpConnection, "_start_afresh", slot_taken)
        browsing = FtpConnection(settings, PASSWORD)
        browsing.connect()
        queue = TransferQueue(lambda item: None, ask=lambda _item, _conflict, reply: asked.append(reply))
        queue.settings(True, 15.0, False, 2)
        holding = Job(browsing, settings, str(tmp_path / "c.txt"), "/", True, "c.txt", has_password=True, options=ASKING)  # keeps its login
        queue.add(holding)
        until(lambda: len(asked) == 1)
        folder = Job(browsing, settings, str(site), "/", True, "site", has_password=True, options=REPLACING)
        queue.add(folder)
        until(lambda: folder.held == HELD_ROOM or folder.state in ENDED)
        assert (folder.state, folder.held, folder.message) == (State.WAITING, HELD_ROOM, "")  # it waits for its turn, nothing failed
        asked[0](Answer(Choice.YES))
        until(lambda: holding.state in ENDED and folder.state in ENDED)
        assert (holding.state, folder.state) == (State.DONE, State.DONE)
        queue.close()
        for other in outsiders:
            other.close()
        browsing.close()
    assert all((root / "site" / name).read_bytes() == (site / name).read_bytes() for name in ("a.bin", "b.bin"))


class Cuttable:
    """Only what looking at broken-off appends asks of a connection first: that it can cut a file back."""

    capabilities: Capabilities = Capabilities(cut=True)


def test_a_job_waiting_to_look_at_broken_appends_sees_its_stop_and_other_logins_never_wait() -> None:
    busy = Connection(name="A", host="a", user="u", protocol=FTP)
    free = Connection(name="B", host="b", user="u", protocol=FTP)
    connection = cast(RemoteConnection, Cuttable())
    with filebyfile._appends_lock:
        turn = filebyfile._appends_looked_at.setdefault(("a", busy.port, "u"), threading.Lock())
    turn.acquire()  # a job of the first login hangs on its server while it looks
    try:
        assert not any(undo_broken_appends(connection, free).values())  # another login goes on at once
        stop = threading.Event()
        stop.set()
        with pytest.raises(Stopped):
            undo_broken_appends(connection, busy, stop)  # one of the same login that waits ends at its stop
    finally:
        turn.release()


def test_run_now_with_every_place_taken_says_the_item_runs_next(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    queue = TransferQueue(lambda item: None)
    first, second, third = (upload(ftp, source_file(tmp_path, name)) for name in ("a.bin", "b.bin", "c.bin"))
    slow(queue, first, second, third)  # one at a time
    until(lambda: running(first)() and second.held == HELD_NEXT)
    assert queue.run_now(third)
    until(lambda: third.held == HELD_NEXT and second.held == "")
    let_go(queue, first)
    until(lambda: third.state == State.RUNNING and second.held == HELD_NEXT)
    let_go(queue, second, third)
    until(lambda: second.state == third.state == State.DONE)
    queue.close()
