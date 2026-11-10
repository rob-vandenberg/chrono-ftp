import os
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from chrono_ftp.config import parts
from chrono_ftp.connections.base import Limit, Progress, RemoteConnection
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.transferoptions import RESUME_OFF, TransferOptions
from chrono_ftp.transfers import rsyncway
from chrono_ftp.transfers.job import Meter
from chrono_ftp.transfers.options import Answer, Choice, Conflict
from chrono_ftp.transfers.queue import LEFTOVER_SECONDS, PART_SUFFIX, SEEN_BEFORE_SECONDS, Job, Owned, State, TransferQueue, spared_partials, tidy, tidy_partials_here
from chrono_ftp.transfers.rsyncway import partial_folder_of
from tests.fakeservers import PASSWORD, USER, ftp_server, ssh_server
from tests.test_connections import trusted_ssh
from tests.test_transfers import fake_rsync, job, scripted

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_queue.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial: queue control (FC-02 to FC-04): closing waits for the thread, pause and resume keep what arrived, the order of the
#          line, retry, delete, the speed limit while a job runs (also with rsync), finished jobs leave the list, an error goes on or
#          pauses, and the queue tells once when it is done; chosen items move as a block, a pause never takes over a stop, closing keeps
#          a pause on its way, a deleted job is never retried; tidying never
#          takes what a paused job goes on from; a stop ends a pause; the queue is done only when it ended by itself; the percentage
#          goes on after a restart; a pause ended by a stop or a delete is never continued from with resume off (FTP and SFTP); closing
#          gives paused parts their own setting back; the percentage after a resume; a file sent before a pause is sent again when it
#          changed, and only then; stopping one item earlier does not keep the queue from ending by itself; on a server that can't
#          set dates a file sent before a pause is not sent or asked about again; a resumed job that still waits gives its pause back
#          when stopped or closed; the control calls say whether they acted; after a look-over rsync's percentage goes on; the percentage
#          after a resume is checked once paused again, and closing without a wait

REPLACING: TransferOptions = TransferOptions(ask_before_replacing=False)
SIZE: int = 1024 * 1024
SLOW: int = 64  # KB per second: a file of SIZE takes 16 seconds, so nothing ends before the test acts
CRAWL: int = 8  # KB per second: a file of SIZE would take more than two minutes


@pytest.fixture
def ftp(tmp_path: Path) -> Generator[tuple[FtpConnection, Connection, Path]]:
    root = tmp_path / "server"
    root.mkdir()
    with ftp_server(root) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        yield connection, settings, root
        connection.close()


@pytest.fixture
def queue() -> Generator[TransferQueue]:
    made = TransferQueue(lambda item: None)
    yield made
    made.close()


def until(condition: Callable[[], bool], seconds: float = 15.0) -> None:
    """Waits for the very thing a test checks, never for a time."""
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the queue never got there"
        threading.Event().wait(0.01)


def source_file(tmp_path: Path, name: str = "big.bin", size: int = SIZE) -> Path:
    path = tmp_path / name
    path.write_bytes(os.urandom(size))
    return path


def upload(ftp: tuple[FtpConnection, Connection, Path], source: Path, folder: str = "/", options: TransferOptions = REPLACING) -> Job:
    connection, settings, _root = ftp
    return Job(connection, settings, str(source), folder, True, source.name, has_password=True, options=options)


def offsets_of_uploads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Where each FTP upload starts: 0 from the beginning, more where it continues a part."""
    seen: list[int] = []
    real = FtpConnection.upload

    def noted(self: FtpConnection, local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        seen.append(offset)
        real(self, local, remote, progress, limit, offset, append, exclusive)

    monkeypatch.setattr(FtpConnection, "upload", noted)
    return seen


# --- closing


def test_after_close_the_job_has_stopped_and_no_call_reaches_the_window(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    closed = threading.Event()
    late: list[State] = []

    def changed(item: Job) -> None:
        if closed.is_set():
            late.append(item.state)

    queue = TransferQueue(changed)
    queue.limit = SLOW
    item = upload(ftp, source_file(tmp_path))
    queue.add(item)
    until(lambda: item.done > 0)
    queue.close()
    closed.set()
    assert item.state == State.STOPPED  # the thread is done with it, its part's note included, before close() returns
    queue.add(upload(ftp, source_file(tmp_path, "other.bin")))  # a closed queue takes nothing more
    assert late == [] and queue.jobs == [item]  # close() waited for every thread: none of them can call the window any more


# --- pause and resume (FC-02)


def test_a_paused_upload_keeps_what_arrived_and_continues_from_there(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    offsets = offsets_of_uploads(monkeypatch)
    _connection, _settings, root = ftp
    source = source_file(tmp_path)
    item = upload(ftp, source, options=TransferOptions(ask_before_replacing=False, resume=RESUME_OFF))  # a pause keeps it all the same
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.done > 0)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    part = root / f"big.bin{PART_SUFFIX}"
    assert part.is_file() and part.stat().st_size > 0 and not (root / "big.bin").exists()
    queue.set_limit(item, 0)
    queue.resume(item)
    until(lambda: item.state == State.DONE)
    assert offsets[0] == 0 and offsets[1] > 0  # it went on from what had arrived
    assert (root / "big.bin").read_bytes() == source.read_bytes() and not part.exists()


def test_a_paused_download_keeps_what_arrived_and_continues_from_there(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    connection, settings, root = ftp
    (root / "big.bin").write_bytes(os.urandom(SIZE))
    here = tmp_path / "here"
    here.mkdir()
    item = Job(connection, settings, str(here), "/big.bin", False, "big.bin", has_password=True, options=REPLACING, limit=SLOW)
    queue.add(item)
    until(lambda: item.done > 0)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    kept = here / f"big.bin{PART_SUFFIX}"
    assert kept.is_file() and kept.stat().st_size > 0
    queue.set_limit(item, 0)
    queue.resume(item)
    until(lambda: item.state == State.DONE)
    assert (here / "big.bin").read_bytes() == (root / "big.bin").read_bytes() and not kept.exists()


def test_pausing_and_resuming_twice_ends_with_the_whole_file(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    _connection, _settings, root = ftp
    source = source_file(tmp_path)
    item = upload(ftp, source)
    item.limit = SLOW
    queue.add(item)
    for _round in range(2):
        until(lambda: item.state == State.RUNNING and item.done > 0)
        queue.pause(item)
        queue.pause(item)  # a second press changes nothing
        until(lambda: item.state == State.PAUSED)
        queue.resume(item)
        queue.resume(item)
    queue.set_limit(item, 0)
    until(lambda: item.state == State.DONE)
    assert (root / "big.bin").read_bytes() == source.read_bytes()


def test_resume_right_after_pause_lets_the_job_run_on(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    _connection, _settings, root = ftp
    source = source_file(tmp_path)
    item = upload(ftp, source)
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.done > 0)
    queue.pause(item)
    queue.resume(item)  # changed the person's mind before the pause landed
    queue.set_limit(item, 0)
    until(lambda: item.state == State.DONE)
    assert (root / "big.bin").read_bytes() == source.read_bytes()


def test_a_paused_waiting_job_is_held_while_the_ones_after_it_run(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    first, held, last = (upload(ftp, source_file(tmp_path, name, 1000)) for name in ("a.bin", "b.bin", "c.bin"))
    first.limit = 1  # 1 KB per second: it runs until the test lets it go
    for item in (first, held, last):
        queue.add(item)
    until(lambda: first.state == State.RUNNING)
    queue.pause(held)
    queue.set_limit(first, 0)
    until(lambda: last.state == State.DONE)
    assert first.state == State.DONE and held.state == State.PAUSED
    queue.resume(held)
    until(lambda: held.state == State.DONE)


# --- the order of the line, and items that wait to be started


def test_items_wait_to_be_started_and_run_in_the_order_the_person_gave(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    order: list[str] = []
    queue = TransferQueue(lambda item: order.append(item.name) if item.state == State.RUNNING and item.name not in order else None)
    queue.settings(False, 15.0, False)
    first, second, third = (upload(ftp, source_file(tmp_path, name, 1000)) for name in ("a.bin", "b.bin", "c.bin"))
    for item in (first, second, third):
        queue.add(item)
    assert [item.state for item in queue.jobs] == [State.PAUSED] * 3  # nothing starts by itself
    assert queue.move(third, -1) and not queue.move(first, -1)  # a, c, b; the first has no place above it
    assert queue.can_move(second, -1) and not queue.can_move(second, 1)
    queue.run_now(second)  # b, a, c: and it waits now
    assert queue.jobs == [second, first, third] and second.state == State.WAITING
    until(lambda: second.state == State.DONE)
    assert first.state == State.PAUSED and third.state == State.PAUSED
    queue.resume_all()
    until(lambda: first.state == State.DONE and third.state == State.DONE)
    queue.close()
    assert order == ["b.bin", "a.bin", "c.bin"]


def test_a_save_from_edit_starts_by_itself_also_when_items_wait_to_be_started(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    queue.settings(False, 15.0, False)
    item = upload(ftp, source_file(tmp_path, "a.txt", 10))
    queue.add(item, by_itself=True)
    until(lambda: item.state == State.DONE)


# --- retry and delete


def test_retry_after_a_dropped_line_continues_where_it_broke_off(tmp_path: Path, queue: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    offsets = offsets_of_uploads(monkeypatch)
    root = tmp_path / "server"
    root.mkdir()
    source = source_file(tmp_path)
    with ftp_server(root, drops_data_after=300_000) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        item = Job(connection, settings, str(source), "/", True, "big.bin", has_password=True, options=REPLACING)
        queue.add(item)
        until(lambda: item.state == State.FAILED)
        assert "next try continues" in item.message
        queue.retry(item)
        until(lambda: item.state == State.DONE)
        connection.close()
    assert offsets[0] == 0 and offsets[-1] > 0 and "continued where it broke off" in item.message
    assert (root / "big.bin").read_bytes() == source.read_bytes()


def test_retry_after_a_wrong_folder_was_put_right(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    _connection, _settings, root = ftp
    item = upload(ftp, source_file(tmp_path, "a.txt", 10), "/missing")
    queue.add(item)
    until(lambda: item.state == State.FAILED)
    assert item.message
    (root / "missing").mkdir()
    queue.retry(item)
    until(lambda: item.state == State.DONE)
    assert (root / "missing" / "a.txt").is_file()


def test_only_a_failed_or_stopped_job_is_retried(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    item = upload(ftp, source_file(tmp_path, "a.txt", 10))
    queue.add(item)
    until(lambda: item.state == State.DONE)
    queue.retry(item)
    assert item.state == State.DONE


def test_deleting_a_waiting_job_takes_it_out_and_it_never_runs(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    removed: list[Job] = []
    queue = TransferQueue(lambda item: None, removed=removed.append)
    _connection, _settings, root = ftp
    first, gone = upload(ftp, source_file(tmp_path, "a.bin", 1000)), upload(ftp, source_file(tmp_path, "b.bin", 1000))
    first.limit = 1
    queue.add(first)
    queue.add(gone)
    until(lambda: first.state == State.RUNNING)
    queue.delete(gone)
    assert queue.jobs == [first] and removed == [gone] and gone.state == State.STOPPED
    queue.set_limit(first, 0)
    until(lambda: first.state == State.DONE)
    queue.close()
    assert not (root / "b.bin").exists()


def test_deleting_a_running_job_stops_it_first_and_leaves_no_half_file(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    removed: list[Job] = []
    queue = TransferQueue(lambda item: None, removed=removed.append)
    _connection, _settings, root = ftp
    item = upload(ftp, source_file(tmp_path))
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.done > 0)
    queue.delete(item)
    until(lambda: removed == [item])
    queue.close()
    assert item.state == State.STOPPED and queue.jobs == [] and not (root / "big.bin").exists()


def test_clear_finished_takes_out_what_ended_and_keeps_what_waits(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    done, failed, stopped, waiting = (upload(ftp, source_file(tmp_path, name, 10)) for name in ("a.txt", "b.txt", "c.txt", "d.txt"))
    failed.remote_path = "/missing"
    for item in (done, failed):
        queue.add(item)
    until(lambda: done.state == State.DONE and failed.state == State.FAILED)
    queue.settings(False, 15.0, False)
    queue.add(stopped)
    queue.stop(stopped)
    queue.add(waiting)
    queue.clear_finished()
    assert queue.jobs == [waiting]


# --- the speed limit while a job runs


def test_a_new_limit_of_a_job_holds_at_once_while_it_runs(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    item = upload(ftp, source_file(tmp_path))
    item.limit = CRAWL
    queue.add(item)
    until(lambda: item.done > 0)
    queue.set_limit(item, 0)
    until(lambda: item.state == State.DONE)  # at the old limit this would take minutes


def test_a_new_limit_of_the_whole_queue_holds_at_once_for_a_job_without_its_own(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    item = upload(ftp, source_file(tmp_path))
    queue.limit = CRAWL
    queue.add(item)
    until(lambda: item.done > 0)
    assert queue.limit_of(item) == CRAWL
    queue.limit = 0
    until(lambda: item.state == State.DONE)


def test_a_new_limit_starts_rsync_again_with_it(tmp_path: Path) -> None:
    log = tmp_path / "arguments"
    script = f'echo "$@" >> {log}\ncase "$*" in *--bwlimit*) while true; do printf \' 1%%\\r\'; sleep 0.05; done;; esac\nexit 0\n'
    queue = TransferQueue(lambda item: None, fake_rsync(tmp_path, script))
    item = job(scripted(), limit=16)
    queue.add(item)
    until(lambda: item.percent == 1)
    queue.set_limit(item, 0)
    until(lambda: item.state == State.DONE)
    queue.close()
    first, second = log.read_text(encoding="utf-8").splitlines()
    assert "--bwlimit=16" in first and "--bwlimit" not in second


# --- finished jobs, errors, and the end of the queue (FC-03, FC-04)


def test_a_done_job_leaves_the_list_after_a_while_and_a_failed_one_stays(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    removed: list[Job] = []
    queue = TransferQueue(lambda item: None, removed=removed.append)
    queue.settings(True, 0.2, False)
    failed, done = upload(ftp, source_file(tmp_path, "a.txt", 10), "/missing"), upload(ftp, source_file(tmp_path, "b.txt", 10))
    queue.add(failed)
    queue.add(done)
    until(lambda: removed == [done])
    queue.close()
    assert queue.jobs == [failed] and failed.state == State.FAILED


def test_an_error_goes_on_with_the_next_job(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    holding, failing, next_one = upload(ftp, source_file(tmp_path, "a.bin", 1000)), upload(ftp, source_file(tmp_path, "b.txt", 10), "/missing"), upload(ftp, source_file(tmp_path, "c.txt", 10))
    holding.limit = 1
    for item in (holding, failing, next_one):
        queue.add(item)
    until(lambda: holding.state == State.RUNNING)
    queue.set_limit(holding, 0)
    until(lambda: next_one.state == State.DONE)
    assert failing.state == State.FAILED


def test_an_error_pauses_the_queue_when_settings_say_so(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    queue.settings(True, 15.0, True)
    holding, failing, next_one = upload(ftp, source_file(tmp_path, "a.bin", 1000)), upload(ftp, source_file(tmp_path, "b.txt", 10), "/missing"), upload(ftp, source_file(tmp_path, "c.txt", 10))
    holding.limit = 1
    for item in (holding, failing, next_one):
        queue.add(item)
    until(lambda: holding.state == State.RUNNING)
    queue.set_limit(holding, 0)
    until(lambda: next_one.state == State.PAUSED)
    assert failing.state == State.FAILED and holding.state == State.DONE
    queue.resume_all()
    until(lambda: next_one.state == State.DONE)


def test_the_queue_tells_once_when_it_is_done_with_the_connections_it_used_and_the_failures(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    ended: list[tuple[list[RemoteConnection], int]] = []
    queue = TransferQueue(lambda item: None, finished=lambda used, failed, by_itself: ended.append((used, failed)))
    connection, _settings, _root = ftp
    good, bad = upload(ftp, source_file(tmp_path, "a.txt", 10)), upload(ftp, source_file(tmp_path, "b.txt", 10), "/missing")
    queue.add(good)
    queue.add(bad)
    until(lambda: bool(ended))
    queue.close()
    [(used, failed)] = ended
    assert used == [connection] and failed == 1


def test_the_queue_is_not_done_while_an_item_is_paused_nor_when_the_person_deleted_it(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    ended: list[tuple[int, bool]] = []
    queue = TransferQueue(lambda item: None, finished=lambda used, failed, by_itself: ended.append((failed, by_itself)))
    queue.settings(False, 15.0, False)
    held, other = upload(ftp, source_file(tmp_path, "a.txt", 10)), upload(ftp, source_file(tmp_path, "b.txt", 10))
    queue.add(held)
    queue.add(other)
    queue.run_now(other)
    until(lambda: other.state == State.DONE)
    queue.delete(held)  # the person ended what was left: the queue did not end by itself
    later = upload(ftp, source_file(tmp_path, "c.txt", 10))
    queue.add(later)
    queue.resume(later)
    until(lambda: len(ended) == 2)  # this time it ended by itself
    queue.close()
    assert ended == [(0, False), (0, True)]  # the person ended it first, then it ended by itself


def test_the_queue_is_not_done_after_the_person_stopped_it(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    ended: list[tuple[list[RemoteConnection], bool]] = []
    queue = TransferQueue(lambda item: None, finished=lambda used, failed, by_itself: ended.append((used, by_itself)))
    stopped = upload(ftp, source_file(tmp_path))
    stopped.limit = SLOW
    queue.add(stopped)
    until(lambda: stopped.done > 0)
    queue.stop(stopped)  # as Stop All does
    until(lambda: stopped.state == State.STOPPED)
    later = upload(ftp, source_file(tmp_path, "b.txt", 10))
    queue.add(later)
    until(lambda: len(ended) == 2)
    queue.close()
    assert [by_itself for _used, by_itself in ended] == [False, True] and later.state == State.DONE


def test_a_stop_ends_a_pause_so_a_retry_with_resume_off_starts_afresh(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    queue.settings(False, 15.0, False)
    item = upload(ftp, source_file(tmp_path, "a.txt", 10), options=TransferOptions(ask_before_replacing=False, resume=RESUME_OFF))
    item.resuming, item.partial_token = True, "a" * rsyncway.TOKEN_LENGTH  # as an rsync run that was paused leaves it
    queue.add(item)
    assert spared_partials(queue.jobs) == {partial_folder_of(item)}
    queue.stop(item)
    assert not item.resuming and spared_partials(queue.jobs) == frozenset()
    queue.delete(item)


def aged(path: Path) -> None:
    moment = time.time() - LEFTOVER_SECONDS - 60
    os.utime(path, (moment, moment))


def test_tidying_never_takes_what_a_paused_rsync_job_goes_on_from_on_the_server(ftp: tuple[FtpConnection, Connection, Path]) -> None:
    connection, settings, root = ftp
    paused_name = f"{rsyncway.PARTIAL_FOLDER}-{'a' * rsyncway.TOKEN_LENGTH}"
    left_name = f"{rsyncway.PARTIAL_FOLDER}-{'b' * rsyncway.TOKEN_LENGTH}"
    for name in (paused_name, left_name):
        (root / name).mkdir()
        (root / name / "big.bin").write_bytes(b"x" * 100)
        aged(root / name)
    parts.remember_partial(settings, parts.UP, "/", "/home/a", RESUME_OFF)
    owned = Owned(settings, TransferOptions(resume=RESUME_OFF), frozenset({paused_name}))
    tidy(connection, "/", connection.list_folder("/"), {}, owned)
    assert (root / paused_name / "big.bin").is_file()  # paused for over an hour: it stays
    assert not (root / left_name).exists()  # what nothing goes on from goes


def test_tidying_never_takes_what_a_paused_rsync_job_goes_on_from_here(tmp_path: Path) -> None:
    here = tmp_path / "here"
    paused_name = f"{rsyncway.PARTIAL_FOLDER}-{'a' * rsyncway.TOKEN_LENGTH}"
    left_name = f"{rsyncway.PARTIAL_FOLDER}-{'b' * rsyncway.TOKEN_LENGTH}"
    for name in (paused_name, left_name):
        (here / name).mkdir(parents=True)
        (here / name / "big.bin").write_bytes(b"x" * 100)
        aged(here / name)
    other = job(scripted(), upload=False, local=str(here), remote_path="/srv/other.bin", name="other.bin", options=TransferOptions(resume=RESUME_OFF))
    parts.remember_partial(other.connection, parts.DOWN, str(here), "/srv", RESUME_OFF)
    tidy_partials_here(other, frozenset({paused_name}))
    assert (here / paused_name / "big.bin").is_file() and not (here / left_name).exists()


def test_a_paused_part_survives_tidying_however_long_it_waits(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    connection, settings, root = ftp
    item = upload(ftp, source_file(tmp_path), options=TransferOptions(ask_before_replacing=False, resume=RESUME_OFF))
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.done > 0)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    part = root / f"big.bin{PART_SUFFIX}"
    aged(part)
    size = part.stat().st_size
    seen = {f"/big.bin{PART_SUFFIX}": (time.time() - SEEN_BEFORE_SECONDS - 1, size)}  # looked at before, unchanged
    tidy(connection, "/", connection.list_folder("/"), seen, Owned(settings, TransferOptions(resume=RESUME_OFF)))
    assert part.is_file() and part.stat().st_size == size
    queue.set_limit(item, 0)
    queue.resume(item)
    until(lambda: item.state == State.DONE)


def test_the_percentage_goes_on_after_rsync_starts_again_for_a_new_limit(tmp_path: Path) -> None:
    shown: list[int] = []
    script = 'case "$*" in *--bwlimit*) while true; do printf \' 40%%\\r\'; sleep 0.05; done;; esac\nprintf \' 50%%\\r\'; sleep 0.3\nexit 0\n'
    queue = TransferQueue(lambda item: shown.append(item.percent), fake_rsync(tmp_path, script))
    item = job(scripted(), limit=16)
    queue.add(item)
    until(lambda: item.percent == 40)
    queue.set_limit(item, 0)
    until(lambda: item.state == State.DONE)
    queue.close()
    after = shown[shown.index(40):]
    assert 70 in after and all(percent >= 40 for percent in after)  # half of the rest, never back to the start


def test_a_paused_part_is_noted_to_be_continued_whatever_the_resume_setting(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    connection, settings, _root = ftp
    item = upload(ftp, source_file(tmp_path), options=TransferOptions(ask_before_replacing=False, resume=RESUME_OFF))
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.done > 0)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    note = parts.lookup(parts.upload_key(settings, f"/big.bin{PART_SUFFIX}"))
    assert note is not None and note.resume == "on" and note.part_size > 0
    assert connection.file_size(f"/big.bin{PART_SUFFIX}") is not None


# --- review round 1


def held_jobs(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue, names: str) -> list[Job]:
    queue.settings(False, 15.0, False)
    made = [upload(ftp, source_file(tmp_path, f"{name}.txt", 10)) for name in names]
    for item in made:
        queue.add(item)
    return made


def names_of(queue: TransferQueue) -> str:
    return "".join(Path(item.local).stem for item in queue.jobs)


def test_chosen_items_move_together_and_a_block_at_the_edge_stays_as_it_is(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    a, b, c, d, e = held_jobs(ftp, tmp_path, queue, "abcde")
    assert not queue.can_move_together([a, b], -1) and not queue.move_together([a, b], -1)
    assert names_of(queue) == "abcde"  # never b before a
    assert queue.move_together([a, b, d], -1)
    assert names_of(queue) == "abdce"  # the block stays, d goes up past c
    assert queue.move_together([b, c], 1)
    assert names_of(queue) == "adbec"  # each past the one item below it that is not chosen
    assert queue.move_together([d, e], 1)
    assert names_of(queue) == "abdce"
    assert not queue.can_move_together([c, e], 1)  # e is at the bottom, and c right above it stays with it
    for item in queue.jobs:
        queue.delete(item)


def test_a_pause_never_takes_over_a_stop_on_its_way(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    item = upload(ftp, source_file(tmp_path))
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.done > 0)
    queue.stop(item)
    queue.pause(item)  # pressed while the stop was on its way
    until(lambda: item.state not in (State.WAITING, State.RUNNING))
    assert item.state == State.STOPPED


def test_closing_while_a_pause_is_on_its_way_keeps_what_arrived_with_its_own_resume_setting(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    _connection, settings, _root = ftp
    queue = TransferQueue(lambda item: None)
    item = upload(ftp, source_file(tmp_path), options=TransferOptions(ask_before_replacing=False, resume=RESUME_OFF))
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.done > 0)
    queue.pause(item)
    queue.close()
    note = parts.lookup(parts.upload_key(settings, f"/big.bin{PART_SUFFIX}"))
    assert item.state == State.PAUSED and note is not None and note.part_size > 0
    assert note.resume == RESUME_OFF  # the queue does not outlive the program: the part is one as any other, of its own setting


def test_a_deleted_job_is_never_retried(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    item = upload(ftp, source_file(tmp_path, "a.txt", 10), "/missing")
    queue.add(item)
    until(lambda: item.state == State.FAILED)
    queue.delete(item)
    queue.retry(item)  # a stale reference, for instance from a menu opened before
    assert item.state == State.FAILED and queue.jobs == []




@pytest.fixture(params=["ftp", "sftp"])
def either(request: pytest.FixtureRequest, tmp_path: Path) -> Generator[tuple[RemoteConnection, Connection, Path]]:
    """A server over FTP or over SFTP, with the folder it shows."""
    root = tmp_path / "server"
    root.mkdir()
    if request.param == "ftp":
        with ftp_server(root) as port:
            settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
            connection = FtpConnection(settings, PASSWORD)
            connection.connect()
            yield connection, settings, root
            connection.close()
        return
    with ssh_server(root) as server:
        ssh = trusted_ssh(server)
        yield ssh, ssh.connection, root
        ssh.close()


def offsets_of_every_upload(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seen: list[int] = []
    for kind in (FtpConnection, SshConnection):
        real = kind.upload

        def noted(self: RemoteConnection, local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False, real: Callable[..., None] = real) -> None:
            seen.append(offset)
            real(self, local, remote, progress, limit, offset, append, exclusive)

        monkeypatch.setattr(kind, "upload", noted)
    return seen


@pytest.mark.parametrize("ending", ["stop", "delete"])
def test_a_pause_ended_by_stop_or_delete_is_never_continued_from_with_resume_off(either: tuple[RemoteConnection, Connection, Path], tmp_path: Path, queue: TransferQueue, monkeypatch: pytest.MonkeyPatch, ending: str) -> None:
    offsets = offsets_of_every_upload(monkeypatch)
    connection, settings, root = either
    source = source_file(tmp_path)
    item = Job(connection, settings, str(source), "/", True, "big.bin", has_password=True, options=TransferOptions(ask_before_replacing=False, resume=RESUME_OFF), limit=SLOW)
    part_path = f"{item.remote_path.rstrip('/')}/big.bin{PART_SUFFIX}"
    queue.add(item)
    until(lambda: item.done > 0)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    if ending == "stop":
        queue.stop(item)
    else:
        queue.delete(item)
    note = parts.lookup(parts.upload_key(settings, part_path))
    assert note is not None and note.resume == RESUME_OFF  # tidied as any part of a job with resume off
    if ending == "delete":
        return
    queue.set_limit(item, 0)
    queue.retry(item)
    until(lambda: item.state == State.DONE)
    assert offsets[0] == 0 and offsets[-1] == 0  # started afresh, as resume off says
    assert (root / "big.bin").read_bytes() == source.read_bytes()


# --- review round 4


def test_closing_gives_a_paused_part_its_own_resume_setting_back(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    _connection, settings, _root = ftp
    queue = TransferQueue(lambda item: None)
    item = upload(ftp, source_file(tmp_path), options=TransferOptions(ask_before_replacing=False, resume=RESUME_OFF))
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.done > 0)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    queue.close()
    note = parts.lookup(parts.upload_key(settings, f"/big.bin{PART_SUFFIX}"))
    assert note is not None and note.resume == RESUME_OFF  # a later transfer never continues from it, and tidying takes it


def test_after_a_resume_the_percentage_is_the_share_that_really_arrived(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    _connection, _settings, root = ftp
    item = upload(ftp, source_file(tmp_path))
    item.limit = SLOW
    queue.add(item)
    until(lambda: item.percent >= 20)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    part = root / f"big.bin{PART_SUFFIX}"
    paused_at = item.percent
    queue.resume(item)
    until(lambda: item.state == State.RUNNING and item.percent > paused_at + 5)  # it goes on, from what was there already
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    assert abs(item.percent - 100 * part.stat().st_size // SIZE) <= 1  # what was there already is counted once, not twice


def tree_with_a_slow_file(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("first", encoding="utf-8")
    (tree / "big.bin").write_bytes(os.urandom(SIZE))
    return tree


@pytest.mark.parametrize("meanwhile", ["source changed", "target removed"])
def test_a_file_sent_before_a_pause_is_sent_again_when_it_changed_meanwhile(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue, meanwhile: str) -> None:
    connection, settings, root = ftp
    tree = tree_with_a_slow_file(tmp_path)
    item = Job(connection, settings, str(tree), "/", True, "tree", has_password=True, options=REPLACING, limit=SLOW)
    queue.add(item)
    until(lambda: (root / "tree" / "a.txt").is_file() and item.done > 10)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    if meanwhile == "source changed":
        (tree / "a.txt").write_text("edited overnight", encoding="utf-8")
        os.utime(tree / "a.txt", (time.time() + 100, time.time() + 100))
    else:
        (root / "tree" / "a.txt").unlink()
    queue.set_limit(item, 0)
    queue.resume(item)
    until(lambda: item.state == State.DONE)
    assert (root / "tree" / "a.txt").read_text(encoding="utf-8") == (tree / "a.txt").read_text(encoding="utf-8")
    assert (root / "tree" / "big.bin").read_bytes() == (tree / "big.bin").read_bytes()


def test_a_file_sent_before_a_pause_and_unchanged_is_not_sent_again(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, settings, root = ftp
    tree = tree_with_a_slow_file(tmp_path)
    item = Job(connection, settings, str(tree), "/", True, "tree", has_password=True, options=TransferOptions(), limit=SLOW)  # asks before replacing
    queue.add(item)
    until(lambda: (root / "tree" / "a.txt").is_file() and item.done > 10)
    queue.pause(item)
    until(lambda: item.state == State.PAUSED)
    sent: list[str] = []
    real = FtpConnection.upload

    def noted(self: FtpConnection, local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        sent.append(Path(local).name)
        real(self, local, remote, progress, limit, offset, append, exclusive)

    monkeypatch.setattr(FtpConnection, "upload", noted)
    queue.set_limit(item, 0)
    queue.resume(item)
    until(lambda: item.state == State.DONE)
    assert sent == ["big.bin"]  # a.txt is ours and unchanged: neither sent again nor asked about (no question could be answered here)


def test_stopping_one_item_while_others_run_still_lets_the_queue_end_by_itself(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    ended: list[bool] = []
    queue = TransferQueue(lambda item: None, finished=lambda used, failed, by_itself: ended.append(by_itself))
    first, second, third = upload(ftp, source_file(tmp_path, "a.bin", 1000)), upload(ftp, source_file(tmp_path, "b.txt", 10)), upload(ftp, source_file(tmp_path, "c.txt", 10))
    first.limit = 1
    for item in (first, second, third):
        queue.add(item)
    until(lambda: first.state == State.RUNNING)
    queue.stop(second)
    queue.set_limit(first, 0)
    until(lambda: bool(ended))
    queue.close()
    assert ended == [True] and third.state == State.DONE


# --- review round 5


def test_on_a_server_that_can_not_set_dates_a_file_sent_before_a_pause_is_neither_sent_nor_asked_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    tree = tree_with_a_slow_file(tmp_path)
    os.utime(tree / "a.txt", (1_600_000_000, 1_600_000_000))  # an old file: what the server shows is its own date, today
    questions: list[str] = []

    def asking(job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
        questions.append(conflict.name)
        reply(Answer(Choice.NO))

    with ftp_server(root, without=("MFMT",)) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        queue = TransferQueue(lambda item: None, ask=asking)
        item = Job(connection, settings, str(tree), "/", True, "tree", has_password=True, options=TransferOptions(keep_timestamps=True), limit=SLOW)
        try:
            queue.add(item)
            until(lambda: (root / "tree" / "a.txt").is_file() and item.done > 10)
            queue.pause(item)
            until(lambda: item.state == State.PAUSED)
            sent: list[str] = []
            real = FtpConnection.upload

            def noted(self: FtpConnection, local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
                sent.append(Path(local).name)
                real(self, local, remote, progress, limit, offset, append, exclusive)

            monkeypatch.setattr(FtpConnection, "upload", noted)
            queue.set_limit(item, 0)
            queue.resume(item)
            until(lambda: item.state == State.DONE)
        finally:
            queue.close()
            connection.close()
    assert questions == [] and sent == ["big.bin"]  # its date is the server's own: the size tells it is what was sent


# --- review round 6


@pytest.mark.parametrize("ending", ["stop", "close"])
def test_a_resumed_job_that_still_waits_gives_its_pause_back_when_stopped_or_closed(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, ending: str) -> None:
    _connection, settings, _root = ftp
    queue = TransferQueue(lambda item: None)
    paused = upload(ftp, source_file(tmp_path), options=TransferOptions(ask_before_replacing=False, resume=RESUME_OFF))
    paused.limit = SLOW
    queue.add(paused)
    until(lambda: paused.done > 0)
    queue.pause(paused)
    until(lambda: paused.state == State.PAUSED)
    other = upload(ftp, source_file(tmp_path, "other.bin"))
    other.limit = SLOW
    queue.add(other)
    until(lambda: other.state == State.RUNNING)
    queue.resume(paused)  # it waits now, behind the one that runs
    assert paused.state == State.WAITING
    if ending == "stop":
        queue.stop(paused)
    queue.close()
    note = parts.lookup(parts.upload_key(settings, f"/big.bin{PART_SUFFIX}"))
    assert note is not None and note.resume == RESUME_OFF  # its own setting again: nothing continues from it


def test_resume_retry_and_run_now_say_whether_they_acted(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path, queue: TransferQueue) -> None:
    failed = upload(ftp, source_file(tmp_path, "a.txt", 10), "/missing")
    queue.add(failed)
    until(lambda: failed.state == State.FAILED)
    assert not queue.resume(failed) and not queue.run_now(failed)
    assert queue.retry(failed)
    until(lambda: failed.state == State.FAILED)
    queue.delete(failed)


def test_after_a_look_over_rsync_s_percentage_goes_on_from_what_was_reached(tmp_path: Path) -> None:
    item = job(scripted())
    item.total, item.percent = 1000, 40  # what the look-over counted and reached
    program = fake_rsync(tmp_path, "printf ' 50%%\\r'; exit 0\n")
    status, _errors, _moved, _sent = rsyncway._run_list(item, 0, program, None, Meter(item, lambda changed: None), None, False, False)
    assert status == 0 and item.percent == 70  # half of the rest, never back to the start
