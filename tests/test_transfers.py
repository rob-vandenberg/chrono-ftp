import os
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import override

import pytest

from chrono_ftp.connections.base import OLD_SUFFIX, Capabilities, RemoteConnection, RemoteEntry
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.core.errors import RemoteActionFailed
from chrono_ftp.config import parts
from chrono_ftp.config.parts import PartRecord
from chrono_ftp.transfers.queue import LEFTOVER_SECONDS, PART_SUFFIX, Job, Owned, State, TransferQueue, tidy
from chrono_ftp.transfers import rsyncway
from chrono_ftp.transfers.job import Meter
from chrono_ftp.transfers.rsyncway import progress_in, rsync_arguments, rsync_possible
from tests.fakeservers import PASSWORD, USER, ftp_server
from tests.test_connections import trusted_ssh
from tests.fakeservers import ssh_server
from tests.test_engine import Scripted
from chrono_ftp.connections.base import CommandResult

# --- Version ------------------------------------------------------------
__version__ = "test_transfers.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: The ways moved out of queue.py; tests that replace a file say so in their options, as nothing is replaced without asking;
#          tidying only what has our note, of the size noted; a file moved aside under a further name; no process rsync started
#          outlives it; rsync's group is ended before rsync is reaped, and a group that is gone already is no error; a job stopped while
#          waiting never runs; rsync's progress and time left, never from the name of a file
# v0.0.6: Leftovers and links in the target
# v0.0.5: Initial

# the ftp fixture is further down: pytest finds it by name
SETTINGS: Connection = Connection(name="T", host="host.example.com", user="anna", port=2022, key_file="/keys/my key")
REPLACING: TransferOptions = TransferOptions(ask_before_replacing=False)  # these tests replace what is there, as a save from Edit does


def scripted(rsync: bool = True) -> RemoteConnection:
    remote = Scripted(CommandResult(0, "", ""))
    remote.capabilities = Capabilities(shell=True, tools=frozenset({"rsync"} if rsync else set[str]()))
    return remote


def job(remote: RemoteConnection, **changes: object) -> Job:
    values: dict[str, object] = {"remote": remote, "connection": SETTINGS, "local": "/home/a/file.txt", "remote_path": "/srv", "upload": True, "name": "file.txt", "options": REPLACING}
    return Job(**{**values, **changes})  # pyrefly: ignore[bad-argument-type]


def run_all(queue: TransferQueue, *jobs: Job) -> None:
    for item in jobs:
        queue.add(item)
    finished = threading.Event()
    watcher = threading.Thread(target=lambda: wait_for(jobs, finished), daemon=True)
    watcher.start()
    assert finished.wait(10)


def wait_for(jobs: tuple[Job, ...], finished: threading.Event) -> None:
    while any(item.state in (State.WAITING, State.RUNNING) for item in jobs):
        threading.Event().wait(0.01)
    finished.set()


def test_rsync_gets_the_port_key_and_limit() -> None:
    arguments = rsync_arguments(job(scripted()), 500)
    assert arguments[0] == "rsync" and "--bwlimit=500" in arguments
    ssh = shlex.split(arguments[arguments.index("-e") + 1])
    assert ssh[:3] == ["ssh", "-p", "2022"]
    assert ssh[ssh.index("-i") + 1] == "/keys/my key"
    assert "BatchMode=yes" in ssh
    assert arguments[-3:] == ["--", "/home/a/file.txt", "anna@host.example.com:/srv/"]


def test_no_limit_means_no_limit_argument() -> None:
    assert not any(item.startswith("--bwlimit") for item in rsync_arguments(job(scripted()), 0))


def test_a_download_goes_into_the_local_folder() -> None:
    arguments = rsync_arguments(job(scripted(), upload=False, local="/home/a", remote_path="/srv/file.txt"), 0)
    assert arguments[-3:] == ["--", "anna@host.example.com:/srv/file.txt", "/home/a/"]


def test_a_stopped_upload_leaves_the_file_on_the_server_whole(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, settings, root = ftp
    (root / "big.bin").write_bytes(b"old" * 1000)
    source = tmp_path / "big.bin"
    source.write_bytes(b"n" * 4 * 1024 * 1024)
    queue = TransferQueue(lambda item: None)
    queue.limit = 256
    item = Job(connection, settings, str(source), "/", True, "big.bin", has_password=True, options=REPLACING)
    queue.add(item)
    while item.percent == 0:
        threading.Event().wait(0.01)
    queue.stop(item)
    finished = threading.Event()
    threading.Thread(target=lambda: wait_for((item,), finished), daemon=True).start()
    assert finished.wait(10)
    queue.close()
    assert item.state == State.STOPPED
    assert (root / "big.bin").read_bytes() == b"old" * 1000


def test_a_failed_download_leaves_the_local_file_whole(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, settings, _root = ftp
    local = tmp_path / "local"
    local.mkdir()
    (local / "gone.txt").write_text("keep me", encoding="utf-8")
    queue = TransferQueue(lambda item: None)
    item = Job(connection, settings, str(local), "/gone.txt", False, "gone.txt", has_password=True)
    run_all(queue, item)
    queue.close()
    assert (local / "gone.txt").read_text(encoding="utf-8") == "keep me"
    assert [path.name for path in local.iterdir()] == ["gone.txt"]


def test_an_upload_replaces_the_old_file_only_when_it_is_complete(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, settings, root = ftp
    (root / "a.txt").write_text("old", encoding="utf-8")
    source = tmp_path / "a.txt"
    source.write_text("new", encoding="utf-8")
    queue = TransferQueue(lambda item: None)
    item = Job(connection, settings, str(source), "/", True, "a.txt", has_password=True, options=REPLACING)
    run_all(queue, item)
    queue.close()
    assert item.state == State.DONE, item.message
    assert (root / "a.txt").read_text(encoding="utf-8") == "new"
    assert [path.name for path in root.iterdir() if "part" in path.name] == []


def test_a_talkative_rsync_can_not_block(tmp_path: Path) -> None:
    queue = TransferQueue(lambda item: None, fake_rsync(tmp_path, "head -c 300000 /dev/zero >&2; exit 0"))
    item = job(scripted())
    run_all(queue, item)
    queue.close()
    assert item.state == State.DONE


def test_a_silent_rsync_can_be_stopped(tmp_path: Path) -> None:
    queue = TransferQueue(lambda item: None, fake_rsync(tmp_path, "sleep 30"))
    item = job(scripted())
    queue.add(item)
    while item.state != State.RUNNING:
        threading.Event().wait(0.01)
    queue.stop(item)
    finished = threading.Event()
    threading.Thread(target=lambda: wait_for((item,), finished), daemon=True).start()
    assert finished.wait(5)
    queue.close()
    assert item.state == State.STOPPED


def ended(pid: int) -> bool:
    """The process the test started is gone, or has ended and waits only to be reaped by whoever took it over."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return True
    return state in ("Z", "X")


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="the state of a process is read from /proc, which Linux has")
@pytest.mark.parametrize("ending", ["stopped", "finished"])
def test_no_process_rsync_started_outlives_it(tmp_path: Path, ending: str) -> None:
    """rsync starts ssh; here a child of the stand-in plays that part, and notes its process number for the test."""
    noted = tmp_path / "child.pid"
    after = "wait" if ending == "stopped" else "exit 0"
    queue = TransferQueue(lambda item: None, fake_rsync(tmp_path, f"sleep 30 >/dev/null 2>&1 &\necho $! > {shlex.quote(str(noted))}\n{after}\n"))
    item = job(scripted())
    try:
        queue.add(item)
        deadline = time.monotonic() + 5
        while not noted.exists() or not noted.read_text(encoding="utf-8").strip():
            assert time.monotonic() < deadline, "the stand-in for rsync did not start its child"
            threading.Event().wait(0.01)
        if ending == "stopped":
            queue.stop(item)
        finished = threading.Event()
        threading.Thread(target=lambda: wait_for((item,), finished), daemon=True).start()
        assert finished.wait(5)
    finally:
        queue.close()
    child = int(noted.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while not ended(child):
        if time.monotonic() >= deadline:
            os.kill(child, signal.SIGTERM)  # never left behind, also when the test fails
            pytest.fail(f"the child of rsync ({child}) still ran after rsync was {ending}")
        threading.Event().wait(0.01)
    assert item.state == (State.STOPPED if ending == "stopped" else State.DONE), item.message


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="the state of a process is read from /proc, which Linux has")
def test_rsyncs_group_is_ended_while_rsync_is_not_yet_reaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Until rsync is reaped its number stays its group's own: the group that is ended is never another program's."""
    states: list[str] = []
    if sys.platform == "win32":
        pytest.skip("Windows has no process groups to end")
    else:
        real_killpg = os.killpg

        def noted(group: int, number: int) -> None:
            states.append(Path(f"/proc/{group}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0])
            real_killpg(group, number)

        monkeypatch.setattr(rsyncway.os, "killpg", noted)
    item = job(scripted())
    status, _errors, _moved, _sent = rsyncway._run(item, [fake_rsync(tmp_path, "exit 3")], Meter(item, lambda changed: None))
    assert status == 3 and states == ["Z"]  # ended, not reaped: still its own


def test_a_group_of_rsync_that_is_gone_already_is_no_error() -> None:
    process = subprocess.Popen(["true"], stdout=subprocess.PIPE, start_new_session=True)
    process.wait()  # reaped, and its group gone with it
    rsyncway._end_group(process)
    assert process.returncode == 0


def test_rsyncs_progress_and_time_left_are_read_from_its_whole_pieces() -> None:
    assert progress_in(b"  1,000  12%  1.50MB/s    1:02:03\r") == ((12, 3723), b"")
    assert progress_in(b"  5  7%  1.00MB/s\r") == ((7, None), b"")  # no time said yet
    assert progress_in(b" 1 10%  1MB/s  0:00:20\r 2 55%  1MB/s  0:00:09\r") == ((55, 9), b"")  # the last one counts
    assert progress_in(b" 100 100%  1MB/s  0:00:00 (xfr#1)\n") == ((100, 0), b"")
    assert progress_in(b" 1 10%  1MB/s  0:00:20  2 55%  1MB/s  0:00:09\r") == ((55, 9), b"")  # also within one piece
    assert progress_in(b" 3 4") == (None, b" 3 4")  # not whole yet: kept for the next read
    assert progress_in(b" 3 4" + b"5%\r") == ((45, None), b"")


def test_the_name_of_a_file_is_never_taken_for_progress() -> None:
    assert progress_in(b"chrono-ftp-sent:Grade 100%.pdf\n") == (None, b"")
    assert progress_in(b"chrono-ftp-sent:9999%.txt\n 3 40%  1MB/s\r") == ((40, None), b"")
    assert progress_in(b"chrono-ftp-sent:Grade 1") == (None, b"chrono-ftp-sent:Grade 1")  # split over two reads
    assert progress_in(b"chrono-ftp-sent:Grade 1" + b"00%.pdf\n") == (None, b"")
    assert progress_in(b" 9999%\r") == ((100, None), b"")  # never above 100


def test_rsync_is_not_used_with_a_password_or_without_rsync_on_the_server() -> None:
    assert rsync_possible(job(scripted()))
    assert not rsync_possible(job(scripted(), has_password=True))
    assert not rsync_possible(job(scripted(rsync=False)))
    assert not rsync_possible(job(scripted(), connection=Connection(name="T", host="h", protocol=FTP)))


def fake_rsync(tmp_path: Path, script: str) -> str:
    program = tmp_path / "fake-rsync"
    program.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    program.chmod(0o755)
    return str(program)


def test_progress_of_rsync_reaches_the_job(tmp_path: Path) -> None:
    seen: list[int] = []
    queue = TransferQueue(lambda item: seen.append(item.percent), fake_rsync(tmp_path, r"printf '   100  12%%  1MB/s\r   200  50%%  1MB/s\r'; exit 0"))
    item = job(scripted())
    run_all(queue, item)
    queue.close()
    assert item.state == State.DONE and item.percent == 100
    assert item.way == "rsync"
    assert 50 in seen


def test_a_refusal_of_rsync_is_shown_in_its_words(tmp_path: Path) -> None:
    queue = TransferQueue(lambda item: None, fake_rsync(tmp_path, "echo 'rsync: Permission denied' >&2; exit 23"))
    item = job(scripted())
    run_all(queue, item)
    queue.close()
    assert item.state == State.FAILED and "Permission denied" in item.message


def test_when_rsync_cannot_log_in_the_other_way_takes_over(tmp_path: Path) -> None:
    source = tmp_path / "file.txt"
    source.write_text("x", encoding="utf-8")
    queue = TransferQueue(lambda item: None, fake_rsync(tmp_path, "exit 255"))
    item = job(scripted(), local=str(source))
    run_all(queue, item)
    queue.close()
    assert item.state == State.DONE and item.way == "SFTP"


def test_a_running_rsync_can_be_stopped(tmp_path: Path) -> None:
    queue = TransferQueue(lambda item: None, fake_rsync(tmp_path, r"while true; do printf ' 1%%\r'; sleep 0.05; done"))
    item = job(scripted())
    queue.add(item)
    while item.state != State.RUNNING:
        threading.Event().wait(0.01)
    queue.stop(item)
    finished = threading.Event()
    threading.Thread(target=lambda: wait_for((item,), finished), daemon=True).start()
    assert finished.wait(10)
    queue.close()
    assert item.state == State.STOPPED


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


def test_a_folder_goes_up_and_comes_back_whole_over_ftp(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, settings, root = ftp
    tree = tmp_path / "tree"
    (tree / "deep").mkdir(parents=True)
    (tree / "a.txt").write_text("aaa", encoding="utf-8")
    (tree / "deep" / "b.txt").write_text("bbb", encoding="utf-8")
    queue = TransferQueue(lambda item: None)
    up = Job(connection, settings, str(tree), "/", True, "tree", has_password=True)
    run_all(queue, up)
    assert up.state == State.DONE, up.message
    assert (root / "tree" / "deep" / "b.txt").read_text(encoding="utf-8") == "bbb"
    back = tmp_path / "back"
    back.mkdir()
    down = Job(connection, settings, str(back), "/tree", False, "tree", has_password=True)
    run_all(queue, down)
    queue.close()
    assert down.state == State.DONE, down.message
    assert (back / "tree" / "deep" / "b.txt").read_text(encoding="utf-8") == "bbb"
    assert (back / "tree" / "a.txt").read_text(encoding="utf-8") == "aaa"


def test_browsing_goes_on_during_an_ftp_transfer(ftp: tuple[FtpConnection, Connection, Path]) -> None:
    connection, _settings, _root = ftp
    other = connection.for_a_transfer()
    assert other is not connection
    other.close()


def test_a_job_stopped_while_waiting_never_starts(tmp_path: Path) -> None:
    states: list[tuple[Job, State]] = []
    queue = TransferQueue(lambda changed: states.append((changed, changed.state)))
    item, after = job(scripted(rsync=False)), job(scripted(rsync=False))
    item.stop_asked.set()
    queue.add(item)
    queue.stop(item)
    run_all(queue, after)  # the queue met the stopped job first
    queue.close()
    assert item.state == State.STOPPED and not any(changed is item and state == State.RUNNING for changed, state in states)


def test_one_broken_job_does_not_end_the_queue(tmp_path: Path) -> None:
    class Broken(Scripted):
        @override
        def for_a_transfer(self) -> RemoteConnection:
            raise ValueError("unexpected")

    source = tmp_path / "file.txt"
    source.write_text("x", encoding="utf-8")
    queue = TransferQueue(lambda item: None)
    first = job(Broken(CommandResult(0, "", "")), local=str(source))
    second = job(scripted(rsync=False), local=str(source))
    run_all(queue, first, second)
    queue.close()
    assert first.state == State.FAILED
    assert second.state == State.DONE


def test_an_upload_refused_a_rename_keeps_the_old_file(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "data.txt").write_text("old", encoding="utf-8")
    source = tmp_path / "data.txt"
    source.write_text("new", encoding="utf-8")
    with ftp_server(root, rights="elrdmwMT") as port:  # no rename right
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        queue = TransferQueue(lambda item: None)
        item = Job(connection, settings, str(source), "/", True, "data.txt", has_password=True, options=REPLACING)
        run_all(queue, item)
        queue.close()
        connection.close()
    assert item.state == State.FAILED
    assert (root / "data.txt").read_text(encoding="utf-8") == "old"


def upload_over_sftp(tmp_path: Path, root: Path, name: str, content: str) -> Job:
    source = tmp_path / name
    source.write_text(content, encoding="utf-8")
    with ssh_server(root) as server:
        connection = trusted_ssh(server)
        queue = TransferQueue(lambda item: None)
        item = Job(connection, connection.connection, str(source), "/", True, name, has_password=True, options=REPLACING)
        run_all(queue, item)
        queue.close()
        connection.close()
    return item


def test_a_replaced_file_keeps_its_permissions(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "secret.txt").write_text("old", encoding="utf-8")
    (root / "secret.txt").chmod(0o600)
    item = upload_over_sftp(tmp_path, root, "secret.txt", "new")
    assert item.state == State.DONE, item.message
    assert (root / "secret.txt").read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE((root / "secret.txt").stat().st_mode) == 0o600
    assert sorted(path.name for path in root.iterdir()) == ["secret.txt"]


def test_a_link_on_the_server_stays_a_link(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "real.txt").write_text("old", encoding="utf-8")
    (root / "linked.txt").symlink_to(root / "real.txt")
    (root / "linked.txt").unlink()
    (root / "linked.txt").symlink_to("real.txt")
    item = upload_over_sftp(tmp_path, root, "linked.txt", "new")
    assert item.state == State.DONE, item.message
    assert (root / "linked.txt").is_symlink()
    assert (root / "real.txt").read_text(encoding="utf-8") == "new"


def test_rsync_steps_aside_for_a_link_on_the_server(tmp_path: Path) -> None:
    class Linked(Scripted):
        @override
        def list_folder(self, path: str) -> list[RemoteEntry]:
            return [RemoteEntry("file.txt", False, is_link=True)]

    remote = Linked(CommandResult(0, "", ""))
    remote.capabilities = Capabilities(shell=True, tools=frozenset({"rsync"}))
    source = tmp_path / "file.txt"
    source.write_text("x", encoding="utf-8")
    queue = TransferQueue(lambda item: None, fake_rsync(tmp_path, "exit 0"))
    item = job(remote, local=str(source))
    run_all(queue, item)
    queue.close()
    assert item.way == "SFTP"


def test_a_leftover_of_an_earlier_drop_does_not_block_an_upload(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("old", encoding="utf-8")
    (root / "a.txt.chrono-ftp-old").write_text("older", encoding="utf-8")
    item = upload_over_sftp(tmp_path, root, "a.txt", "new")
    assert item.state == State.DONE, item.message
    assert (root / "a.txt").read_text(encoding="utf-8") == "new"


def test_a_local_file_replaced_by_a_download_keeps_its_permissions(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, settings, root = ftp
    (root / "a.txt").write_text("new", encoding="utf-8")
    local = tmp_path / "local"
    local.mkdir()
    (local / "a.txt").write_text("old", encoding="utf-8")
    (local / "a.txt").chmod(0o600)
    queue = TransferQueue(lambda item: None)
    item = Job(connection, settings, str(local), "/a.txt", False, "a.txt", has_password=True, options=REPLACING)
    run_all(queue, item)
    queue.close()
    assert (local / "a.txt").read_text(encoding="utf-8") == "new"
    assert stat.S_IMODE((local / "a.txt").stat().st_mode) == 0o600


def test_after_a_stopped_ftp_transfer_the_answers_still_fit(ftp: tuple[FtpConnection, Connection, Path], tmp_path: Path) -> None:
    connection, _settings, root = ftp
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * 1024 * 1024)

    class Stop(Exception):
        pass

    def stop(_done: int) -> None:
        raise Stop

    with pytest.raises(Stop):
        connection.upload(str(source), "/big.bin", stop)
    connection.make_folder("/after")
    assert (root / "after").is_dir()
    with pytest.raises(RemoteActionFailed):
        connection.delete("/not-there.txt", False)


def test_a_file_never_takes_the_place_of_a_folder(tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "site").mkdir(parents=True)
    (root / "site" / "index.html").write_text("x", encoding="utf-8")
    item = upload_over_sftp(tmp_path, root, "site", "a file")
    assert item.state == State.FAILED
    assert (root / "site" / "index.html").exists()
    assert sorted(path.name for path in root.iterdir()) == ["site"]


def test_a_file_never_takes_the_place_of_a_folder_behind_a_link(tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "site").mkdir(parents=True)
    (root / "link").symlink_to("site")
    item = upload_over_sftp(tmp_path, root, "link", "a file")
    assert item.state == State.FAILED
    assert (root / "site").is_dir() and (root / "link").is_symlink()


class Leftovers(Scripted):
    """A folder as a dropped connection left it; it notes what is done to it."""

    def __init__(self, entries: list[RemoteEntry]) -> None:
        super().__init__(CommandResult(0, "", ""))
        self.entries: list[RemoteEntry] = entries
        self.done: list[str] = []

    @override
    def list_folder(self, path: str) -> list[RemoteEntry]:
        return self.entries

    @override
    def rename(self, old: str, new: str) -> None:
        self.done.append(f"rename {old} {new}")

    @override
    def delete(self, path: str, is_folder: bool) -> None:
        self.done.append(f"delete {path}")


def long_known(entries: list[RemoteEntry]) -> dict[str, tuple[float, int]]:
    return {f"/srv/{entry.name}": (time.time() - 3600, entry.size) for entry in entries}


LEFTOVER_LOGIN: Connection = Connection(name="T", host="h", user="u")


def ours(entries: list[RemoteEntry]) -> Owned:
    """Every leftover among these entries made by this program: each has its note."""
    for entry in entries:
        path = f"/srv/{entry.name}"
        if entry.name.endswith(OLD_SUFFIX):
            parts.remember_old(LEFTOVER_LOGIN, path, path.removesuffix(OLD_SUFFIX), entry.size)
        elif entry.name.endswith(PART_SUFFIX):
            parts.remember(parts.upload_key(LEFTOVER_LOGIN, path), PartRecord('["here", "/nowhere"]', 10, 1.0, 1))
    return Owned(LEFTOVER_LOGIN, TransferOptions())


def test_a_leftover_seen_for_the_first_time_is_left_alone_it_may_be_a_moment_in_another_transfer() -> None:
    entries = [RemoteEntry("a.txt" + OLD_SUFFIX, False, 5, 1.0)]
    remote = Leftovers(entries)
    seen: dict[str, tuple[float, int]] = {}
    tidy(remote, "/srv", entries, seen, ours(entries))
    tidy(remote, "/srv", entries, seen, ours(entries))
    assert remote.done == []


def test_a_part_file_that_grows_is_being_written_whatever_its_date_says() -> None:
    old = time.time() - 2 * LEFTOVER_SECONDS
    entries = [RemoteEntry("a" + PART_SUFFIX, False, 900, old)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, {"/srv/a" + PART_SUFFIX: (time.time() - 3600, 500)}, ours(entries))
    assert remote.done == []


def test_an_old_file_alone_is_the_only_copy_and_gets_its_name_back() -> None:
    entries = [RemoteEntry("a.txt" + OLD_SUFFIX, False, 5, 1.0)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, long_known(entries), ours(entries))
    assert remote.done == ["rename /srv/a.txt.chrono-ftp-old /srv/a.txt"]


def test_an_old_file_next_to_the_new_one_goes() -> None:
    entries = [RemoteEntry("a.txt", False, 5, 1.0), RemoteEntry("a.txt" + OLD_SUFFIX, False, 5, 1.0)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, long_known(entries), ours(entries))
    assert remote.done == ["delete /srv/a.txt.chrono-ftp-old"]


def test_a_part_file_goes_only_when_nothing_can_be_writing_it() -> None:
    now = time.time()
    entries = [RemoteEntry("dead" + PART_SUFFIX, False, 5, now - LEFTOVER_SECONDS - 60), RemoteEntry("busy" + PART_SUFFIX, False, 5, now - 5), RemoteEntry("no-date" + PART_SUFFIX, False, 5, None), RemoteEntry("plain.txt", False, 5, 1.0)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, long_known(entries), ours(entries))
    assert remote.done == ["delete /srv/dead.chrono-ftp-part"]


def test_a_tidy_that_is_refused_leaves_the_folder_as_it_is() -> None:
    class Refusing(Leftovers):
        @override
        def delete(self, path: str, is_folder: bool) -> None:
            raise RemoteActionFailed("no")

    entries = [RemoteEntry("a.txt", False, 5, 1.0), RemoteEntry("a.txt" + OLD_SUFFIX, False, 5, 1.0)]
    assert tidy(Refusing(entries), "/srv", entries, long_known(entries), ours(entries)) == entries


def test_tidying_never_touches_a_file_that_only_carries_our_suffix() -> None:
    """A real file of someone named like a leftover has no note of ours: it stays as it is, alone or next to its namesake."""
    old = time.time() - 2 * LEFTOVER_SECONDS
    entries = [RemoteEntry("alone.txt" + OLD_SUFFIX, False, 5, 1.0), RemoteEntry("b.txt", False, 5, 1.0), RemoteEntry("b.txt" + OLD_SUFFIX, False, 5, 1.0), RemoteEntry("c.bin" + PART_SUFFIX, False, 5, old)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, long_known(entries), Owned(LEFTOVER_LOGIN, TransferOptions()))
    assert remote.done == []


def test_a_file_moved_aside_under_a_further_name_gets_its_own_name_back() -> None:
    """When name.chrono-ftp-old was taken, the old file waited under name~2.chrono-ftp-old: its note knows the real name."""
    entries = [RemoteEntry("a.txt~2" + OLD_SUFFIX, False, 5, 1.0)]
    parts.remember_old(LEFTOVER_LOGIN, "/srv/a.txt~2" + OLD_SUFFIX, "/srv/a.txt", 5)
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, long_known(entries), Owned(LEFTOVER_LOGIN, TransferOptions()))
    assert remote.done == ["rename /srv/a.txt~2.chrono-ftp-old /srv/a.txt"]
    assert parts.old_target(LEFTOVER_LOGIN, "/srv/a.txt~2" + OLD_SUFFIX, 5) is None


def test_rsync_stands_aside_for_a_link_deep_in_the_target(tmp_path: Path) -> None:
    (tmp_path / "site" / "deep").mkdir(parents=True)
    (tmp_path / "site" / "deep" / "link").symlink_to("/")
    queue = TransferQueue(lambda item: None)
    item = job(scripted(), local=str(tmp_path), remote_path="/srv/site", upload=False, name="site")
    assert queue._links_in_the_target(item)
    (tmp_path / "site" / "deep" / "link").unlink()
    assert not queue._links_in_the_target(item)
    queue.close()


def test_an_upload_looks_for_links_on_the_server_before_rsync_may_run() -> None:
    class Found(Leftovers):
        output: str = "/srv/site/link\n"

        @override
        def run(self, command: str) -> CommandResult:
            return CommandResult(0, self.output, "")

    remote = Found([RemoteEntry("site", True)])
    remote.capabilities = Capabilities(shell=True, tools=frozenset({"rsync", "find"}))
    queue = TransferQueue(lambda item: None)
    assert queue._links_in_the_target(job(remote, local="/home/a/site", remote_path="/srv", name="site"))
    remote.output = ""
    assert not queue._links_in_the_target(job(remote, local="/home/a/site", remote_path="/srv", name="site"))
    queue.close()


def test_a_source_that_is_gone_fails_and_never_shows_done(tmp_path: Path, ftp: tuple[FtpConnection, Connection, Path]) -> None:
    connection, settings, _root = ftp
    queue = TransferQueue(lambda item: None)
    item = Job(connection, settings, str(tmp_path / "gone.txt"), "/", True, "gone.txt")
    run_all(queue, item)
    queue.close()
    assert item.state == State.FAILED


def test_tidying_forgets_the_note_of_a_part_that_is_no_longer_there() -> None:
    entries = [RemoteEntry("a.txt", False, 5, 1.0)]
    gone = parts.upload_key(LEFTOVER_LOGIN, "/srv/gone.txt" + PART_SUFFIX)
    elsewhere = parts.upload_key(LEFTOVER_LOGIN, "/other/gone.txt" + PART_SUFFIX)
    for key in (gone, elsewhere):
        parts.remember(key, PartRecord('["here", "/nowhere"]', 10, 1.0, 1))
    tidy(Leftovers(entries), "/srv", entries, long_known(entries), Owned(LEFTOVER_LOGIN, TransferOptions()))
    assert parts.lookup(gone) is None  # it names nothing any more
    assert parts.lookup(elsewhere) is not None  # another folder: not looked at here
