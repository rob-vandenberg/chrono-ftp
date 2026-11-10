"""Opt-in tests against real SFTP servers (D-026): resume, dates and rights (FB-02, FB-05, FB-06). Run with `pytest -m live`.

Which servers: the environment variable CHRONO_FTP_LIVE_SERVERS, a space-separated list of user@host:port; no server names live in the
project (G-1). The login is by key only, CHRONO_FTP_LIVE_KEY or ~/.ssh/id_ed25519. The server must be known in ~/.ssh/known_hosts, or its
host key fingerprint must be one the person gave in CHRONO_FTP_LIVE_FINGERPRINTS (space-separated, as Chrono FTP shows them); a test never
trusts a key on its own. Every test works in a throwaway folder of its own in the account's home and removes it again, also when it fails
(OR-26)."""

import os
import secrets
import threading
from collections.abc import Generator
from pathlib import Path

import pytest

from chrono_ftp.connections import trust
from chrono_ftp.connections.base import Progress, RemoteConnection, UnknownServer, joined
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.transferoptions import RESUME_ON, TransferOptions
from chrono_ftp.transfers.job import PART_SUFFIX
from chrono_ftp.transfers.queue import Job, State, TransferQueue
from tests.test_transfers import run_all, wait_for

# --- Version ------------------------------------------------------------
__version__ = "test_live_transfers.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: resume both ways, dates and rights on a throwaway folder of a real server; rsync for dates only, as an upload that
#          sets rights goes file by file; the counting stand-ins take a transfer's exclusive too

SERVERS: list[str] = os.environ.get("CHRONO_FTP_LIVE_SERVERS", "").split()
KEY: str = os.environ.get("CHRONO_FTP_LIVE_KEY", str(Path.home() / ".ssh" / "id_ed25519"))
FINGERPRINTS: list[str] = os.environ.get("CHRONO_FTP_LIVE_FINGERPRINTS", "").split()
OLD_TIME: float = 1_600_000_000.0
BIG: int = 4 * 1024 * 1024
SLOW: int = 512  # KB per second: the stop comes long before the end
FOLDER_PREFIX: str = "chrono-ftp-live-"

pytestmark = pytest.mark.live


def login(address: str) -> Connection:
    user, _at, place = address.rpartition("@")
    host, _colon, port = place.rpartition(":")
    return Connection(name="live", host=host, user=user, port=int(port), key_file=KEY)


@pytest.fixture(params=SERVERS or [pytest.param("", marks=pytest.mark.skip(reason="CHRONO_FTP_LIVE_SERVERS is not set"))])
def live(request: pytest.FixtureRequest) -> Generator[tuple[RemoteConnection, str]]:
    """A connection and a throwaway folder that did not exist before; it goes again afterwards, with all it holds."""
    connection = SshConnection(login(str(request.param)))
    try:
        connection.connect()
    except UnknownServer as unknown:
        connection.close()
        if unknown.fingerprint not in FINGERPRINTS:
            raise
        trust.remember_host_key(unknown.to_remember)  # only in the trust store of this test, which goes with it
        connection = SshConnection(login(str(request.param)))
        connection.connect()
    folder = joined(connection.home(), FOLDER_PREFIX + secrets.token_hex(6))
    connection.make_folder(folder)  # refused when it exists: never a folder that is not ours
    try:
        yield connection, folder
    finally:
        try:
            connection.delete(folder, True)
        finally:
            connection.close()


def offsets_of(connection: RemoteConnection) -> list[int]:
    """Where each transfer of this connection starts from now on."""
    seen: list[int] = []
    upload, download = connection.upload, connection.download

    def counted_upload(local: str, remote: str, progress: Progress, limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        seen.append(offset)
        upload(local, remote, progress, limit, offset, append, exclusive)

    def counted_download(remote: str, local: str, progress: Progress, limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        seen.append(offset)
        download(remote, local, progress, limit, offset, append, exclusive)

    setattr(connection, "upload", counted_upload)
    setattr(connection, "download", counted_download)
    return seen


def run(connection: RemoteConnection, local: Path, remote_path: str, upload: bool, name: str, options: TransferOptions, limit: int = 0, stop: bool = False, rsync: bool = False) -> Job:
    """File by file unless rsync: a job with a password never goes through rsync."""
    queue = TransferQueue(lambda item: None)
    queue.limit = limit
    job = Job(connection, connection_of(connection), str(local), remote_path, upload, name, has_password=not rsync, options=options)
    if not stop:
        run_all(queue, job)
    else:
        queue.add(job)
        while job.done == 0 and job.state in (State.WAITING, State.RUNNING):
            threading.Event().wait(0.01)
        queue.stop(job)
        finished = threading.Event()
        threading.Thread(target=lambda: wait_for((job,), finished), daemon=True).start()
        assert finished.wait(15)
    queue.close()
    return job


def connection_of(connection: RemoteConnection) -> Connection:
    assert isinstance(connection, SshConnection)
    return connection.connection


def entry(connection: RemoteConnection, folder: str, name: str) -> tuple[int, float | None, str]:
    found = next(item for item in connection.list_folder(folder) if item.name == name)
    return found.size, found.modified, found.permissions


def test_a_stopped_upload_and_download_continue_where_they_stopped(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    connection, folder = live
    content = os.urandom(BIG)
    (tmp_path / "big.bin").write_bytes(content)
    options = TransferOptions(resume=RESUME_ON)
    assert run(connection, tmp_path / "big.bin", folder, True, "big.bin", options, SLOW, stop=True).state == State.STOPPED
    part_size = entry(connection, folder, "big.bin" + PART_SUFFIX)[0]
    assert 0 < part_size < BIG
    offsets = offsets_of(connection)
    assert run(connection, tmp_path / "big.bin", folder, True, "big.bin", options).state == State.DONE
    assert offsets == [part_size] and entry(connection, folder, "big.bin")[0] == BIG
    back = tmp_path / "back"
    back.mkdir()
    assert run(connection, back, joined(folder, "big.bin"), False, "big.bin", options, SLOW, stop=True).state == State.STOPPED
    local_part_size = (back / ("big.bin" + PART_SUFFIX)).stat().st_size
    assert 0 < local_part_size < BIG
    offsets.clear()
    assert run(connection, back, joined(folder, "big.bin"), False, "big.bin", options).state == State.DONE
    assert offsets == [local_part_size]
    assert (back / "big.bin").read_bytes() == content


def test_dates_are_kept_for_files_and_folders_both_ways(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    connection, folder = live
    tree = tmp_path / "tree"
    (tree / "deep").mkdir(parents=True)
    (tree / "deep" / "a.txt").write_text("a", encoding="utf-8")
    for path in (tree / "deep" / "a.txt", tree / "deep", tree):
        os.utime(path, (OLD_TIME, OLD_TIME))
    job = run(connection, tree, folder, True, "tree", TransferOptions())
    assert job.state == State.DONE, job.message
    assert entry(connection, joined(folder, "tree/deep"), "a.txt")[1] == OLD_TIME
    assert entry(connection, joined(folder, "tree"), "deep")[1] == OLD_TIME
    assert entry(connection, folder, "tree")[1] == OLD_TIME
    back = tmp_path / "back"
    back.mkdir()
    job = run(connection, back, joined(folder, "tree"), False, "tree", TransferOptions())
    assert job.state == State.DONE, job.message
    assert [path.stat().st_mtime for path in (back / "tree", back / "tree" / "deep", back / "tree" / "deep" / "a.txt")] == [OLD_TIME] * 3


def test_rights_on_upload_with_x_for_folders(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    connection, folder = live
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("a", encoding="utf-8")
    job = run(connection, tree, folder, True, "tree", TransferOptions(set_rights=True, rights="640"))
    assert job.state == State.DONE, job.message
    assert entry(connection, joined(folder, "tree"), "a.txt")[2] == "0640"
    assert entry(connection, folder, "tree")[2] == "0750"


def test_rsync_keeps_dates_where_the_server_has_it(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    connection, folder = live
    if not connection.capabilities.has("rsync"):
        pytest.skip("this server has no shell with rsync")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.txt").write_text("a", encoding="utf-8")
    os.utime(tree / "a.txt", (OLD_TIME, OLD_TIME))
    job = run(connection, tree, folder, True, "tree", TransferOptions(), rsync=True)
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert entry(connection, joined(folder, "tree"), "a.txt")[1] == OLD_TIME
