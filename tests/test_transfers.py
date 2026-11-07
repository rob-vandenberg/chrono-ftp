import shlex
import stat
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import override

import pytest

from chrono_ftp.connections.base import OLD_SUFFIX, Capabilities, RemoteConnection, RemoteEntry
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import RemoteActionFailed
from chrono_ftp.transfers.queue import LEFTOVER_SECONDS, PART_SUFFIX, Job, State, TransferQueue, rsync_arguments, rsync_possible, tidy
from tests.fakeservers import PASSWORD, USER, ftp_server
from tests.test_connections import trusted_ssh
from tests.fakeservers import ssh_server
from tests.test_engine import Scripted
from chrono_ftp.connections.base import CommandResult

# --- Version ------------------------------------------------------------
__version__ = "test_transfers.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Leftovers and links in the target
# v0.0.5: Initial

# the ftp fixture is further down: pytest finds it by name
SETTINGS: Connection = Connection(name="T", host="host.example.com", user="anna", port=2022, key_file="/keys/my key")


def scripted(rsync: bool = True) -> RemoteConnection:
    remote = Scripted(CommandResult(0, "", ""))
    remote.capabilities = Capabilities(shell=True, tools=frozenset({"rsync"} if rsync else set[str]()))
    return remote


def job(remote: RemoteConnection, **changes: object) -> Job:
    values: dict[str, object] = {"remote": remote, "connection": SETTINGS, "local": "/home/a/file.txt", "remote_path": "/srv", "upload": True, "name": "file.txt"}
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
    item = Job(connection, settings, str(source), "/", True, "big.bin", has_password=True)
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
    item = Job(connection, settings, str(source), "/", True, "a.txt", has_password=True)
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
    queue = TransferQueue(lambda item: None)
    item = job(scripted(rsync=False))
    item.stop_asked.set()
    queue.add(item)
    queue.stop(item)
    queue.close()
    assert item.state == State.STOPPED


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
        item = Job(connection, settings, str(source), "/", True, "data.txt", has_password=True)
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
        item = Job(connection, connection.connection, str(source), "/", True, name, has_password=True)
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
    item = Job(connection, settings, str(local), "/a.txt", False, "a.txt", has_password=True)
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


def test_a_leftover_seen_for_the_first_time_is_left_alone_it_may_be_a_moment_in_another_transfer() -> None:
    entries = [RemoteEntry("a.txt" + OLD_SUFFIX, False, 5, 1.0)]
    remote = Leftovers(entries)
    seen: dict[str, tuple[float, int]] = {}
    tidy(remote, "/srv", entries, seen)
    tidy(remote, "/srv", entries, seen)
    assert remote.done == []


def test_a_part_file_that_grows_is_being_written_whatever_its_date_says() -> None:
    old = time.time() - 2 * LEFTOVER_SECONDS
    entries = [RemoteEntry("a" + PART_SUFFIX, False, 900, old)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, {"/srv/a" + PART_SUFFIX: (time.time() - 3600, 500)})
    assert remote.done == []


def test_an_old_file_alone_is_the_only_copy_and_gets_its_name_back() -> None:
    entries = [RemoteEntry("a.txt" + OLD_SUFFIX, False, 5, 1.0)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, long_known(entries))
    assert remote.done == ["rename /srv/a.txt.chrono-ftp-old /srv/a.txt"]


def test_an_old_file_next_to_the_new_one_goes() -> None:
    entries = [RemoteEntry("a.txt", False, 5, 1.0), RemoteEntry("a.txt" + OLD_SUFFIX, False, 5, 1.0)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, long_known(entries))
    assert remote.done == ["delete /srv/a.txt.chrono-ftp-old"]


def test_a_part_file_goes_only_when_nothing_can_be_writing_it() -> None:
    now = time.time()
    entries = [RemoteEntry("dead" + PART_SUFFIX, False, 5, now - LEFTOVER_SECONDS - 60), RemoteEntry("busy" + PART_SUFFIX, False, 5, now - 5), RemoteEntry("no-date" + PART_SUFFIX, False, 5, None), RemoteEntry("plain.txt", False, 5, 1.0)]
    remote = Leftovers(entries)
    tidy(remote, "/srv", entries, long_known(entries))
    assert remote.done == ["delete /srv/dead.chrono-ftp-part"]


def test_a_tidy_that_is_refused_leaves_the_folder_as_it_is() -> None:
    class Refusing(Leftovers):
        @override
        def delete(self, path: str, is_folder: bool) -> None:
            raise RemoteActionFailed("no")

    entries = [RemoteEntry("a.txt", False, 5, 1.0), RemoteEntry("a.txt" + OLD_SUFFIX, False, 5, 1.0)]
    assert tidy(Refusing(entries), "/srv", entries, long_known(entries)) == entries


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
