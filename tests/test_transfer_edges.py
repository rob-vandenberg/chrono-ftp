"""The edges of a transfer (FB-02, FB-03, OR-15): an append that breaks off, a resumed file that fails later, a data connection that drops,
a disk on this computer that is full, many items left out, links on this computer, and a server path in its one form."""

import errno
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO, cast, override

import asyncssh
import pyftpdlib.handlers.ftp.data as data_handlers
import pytest

from chrono_ftp.connections import base as base_module
from chrono_ftp.connections.base import LOCAL_READ_TEXT, LOCAL_WRITE_TEXT, CommandResult, RemoteConnection, normalized
from chrono_ftp.config import parts
from chrono_ftp.config.parts import upload_key
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.connections.ftp import BUSY_TEXT, INTERRUPTED_TEXT, NO_ROOM_TEXT, FtpConnection
from chrono_ftp.core.connection import FTP, SFTP, Connection
from chrono_ftp.core.errors import ConnectionLost, RemoteActionFailed
from chrono_ftp.core.transferoptions import RESUME_OFF, RESUME_ON, TransferOptions
from chrono_ftp.config.settings import config_folder
from chrono_ftp.transfers.filebyfile import APPEND_CHANGED_TEXT, APPEND_GONE_TEXT, APPEND_LEFT_TEXT, APPEND_NOT_CUT_TEXT, APPEND_NOT_NOTED_TEXT, PART_GONE_TEXT, PART_KEPT_TEXT, LEFT, UNCHANGED, FileWay, set_back, undo_broken_appends
from chrono_ftp.transfers.job import PART_SUFFIX, Job, Meter, State
from chrono_ftp.transfers.options import Answer, Choice, Conflict
from chrono_ftp.transfers import queue as queue_module
from chrono_ftp.transfers.queue import TransferQueue, note_text
from tests.fakeservers import NOT_AS_ROOT, PASSWORD, USER, ftp_server, ssh_server
from tests.test_connections import trusted_ssh
from tests.test_engine import Scripted
from tests.test_transfer_options import BIG, NEWER_TIME, OLD_TIME, SLOW, WAIT_SECONDS, Person, Server, child, down, holds_bytes, read, server, stopped_halfway, up, write
from tests.test_transfer_parts import small_requests
from tests.test_transfers import run_all, wait_for

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_edges.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: A broken-off FTP upload whose bytes the server has not taken in yet keeps no part and never says it did; the test of a
#          source that can't be read here allows that outcome, as the server may not have taken in what was sent when the job ends
# v0.0.12: Initial: a broken-off append is undone, a later step of a resumed file, a dropped data connection, a full disk here, many
#          items left out, dates through a local link, a lost connection while resolving a link, the one form of a server path; a source
#          that can't be read here; the ailing disk typed without ignore comments; whether a part was kept, told truly; an append whose
#          connection drops; one file of a folder interrupted or refused; this computer's file problems; the server's disk before a move;
#          the notes kept in memory; FTP's steps on a part here are this computer's too; an append set back only where nothing but it can
#          be in the file, else left and told; an append that can't be noted does not start; a long question on FTP, answered once the
#          server heard NOOPs; a completed append is never cut; an append another Chrono FTP runs; a set-back that can't be done says
#          why; a note that could not be removed is told; a file written to after its append ended; tests that need a refusal skip as
#          root; a refusal for now keeps a continued part; lock files go with their Chrono FTP; an append whose end is unknown is told,
#          never cut; the answering thread is checked after clean-up; a stale mark never stands over another Chrono FTP's note; a busy
#          FTP server; the other Chrono FTP never outlives its test; a wait for the NOOPs that runs out says so; a broken-off append is
#          set back only where nothing else can be in the file, and never where none of it arrived

__all__ = ["server"]  # the fixture: SFTP and FTP

ORIGINAL: str = "the original, as it was before\n"  # 31 bytes


def appended_then_stopped(served: Server, local: Path, remote_path: str, upload: bool, target: Path) -> Job:
    """An append (the person's answer) that is stopped once some of it was written."""
    connection, settings, _root = served
    queue = TransferQueue(lambda item: None, ask=Person(Answer(Choice.APPEND)))
    queue.limit = SLOW
    job = Job(connection, settings, str(local), remote_path, upload, "log.bin", has_password=True)
    try:
        queue.add(job)
        deadline = time.monotonic() + WAIT_SECONDS
        while target.stat().st_size <= len(ORIGINAL) and job.state in (State.WAITING, State.RUNNING):
            assert time.monotonic() < deadline, "the append did not get under way"
            threading.Event().wait(0.01)
        queue.stop(job)
        finished = threading.Event()
        threading.Thread(target=lambda: wait_for((job,), finished), daemon=True).start()
        assert finished.wait(WAIT_SECONDS)
    finally:
        queue.close()
    return job


# An FTP upload is left out: FTP can't cut a file back, so Append is not offered there (test_transfer_options.py tests that).
@pytest.mark.parametrize(("server", "upload"), [(SFTP, True), (SFTP, False), (FTP, False)], indirect=["server"])
def test_an_append_that_is_stopped_leaves_the_file_as_it_was(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    (source / "log.bin").write_bytes(os.urandom(BIG))
    write(target / "log.bin", ORIGINAL)
    job = appended_then_stopped(server, here / "log.bin" if upload else here, "/" if upload else "/log.bin", upload, target / "log.bin")
    assert job.state == State.STOPPED
    assert read(target / "log.bin") == ORIGINAL  # the old file or the whole append: never half of it


@pytest.mark.parametrize("upload", [True, False])
def test_a_resumed_file_that_fails_in_a_later_step_fails_as_it_would_without_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    (source / "big.bin").write_bytes(os.urandom(BIG))
    part = target / ("big.bin" + PART_SUFFIX)
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served = (connection, connection.connection, root)
        resume = TransferOptions(resume=RESUME_ON)
        assert stopped_halfway(served, here / "big.bin" if upload else here, "/" if upload else "/big.bin", upload, "big.bin", resume, part).state == State.STOPPED
        transfers: list[int] = []
        kind = type(connection)
        method = "upload" if upload else "download"
        real = getattr(kind, method)

        def counted(self: RemoteConnection, *args: object) -> None:
            transfers.append(1)
            real(self, *args)

        def refused(self: RemoteConnection, old: str, new: str) -> None:
            raise RemoteActionFailed("The server refused the rename.")

        real_link = os.link
        arrived = os.path.realpath(here / "big.bin")

        def not_allowed_here(old: str, new: str) -> None:
            if new == arrived:  # only the one file this test is about
                raise OSError(errno.EACCES, "Permission denied")
            real_link(old, new)

        with monkeypatch.context() as patch:
            patch.setattr(kind, method, counted)
            if upload:
                patch.setattr(kind, "rename", refused)
            else:
                patch.setattr(os, "link", not_allowed_here)
            job = up(served, here / "big.bin", resume) if upload else down(served, "/big.bin", here, resume)
        connection.close()
    assert job.state == State.FAILED
    assert transfers == [1]  # continued once; the later step's failure did not start it again from the beginning
    assert note_text("restarted", 1) not in job.message


class AilingFile:
    """A file on a disk that fills up after room bytes, or that can't be read past readable bytes, as a full or damaged disk does."""

    def __init__(self, file: BinaryIO, room: int, readable: int, cuttable: bool = True) -> None:
        self.file: BinaryIO = file
        self.room: int = room
        self.readable: int = readable
        self.cuttable: bool = cuttable

    def write(self, data: bytes) -> int:
        if self.file.tell() + len(data) > self.room:
            raise OSError(errno.ENOSPC, "No space left on device")
        return self.file.write(data)

    def read(self, size: int = -1) -> bytes:
        if self.file.tell() >= self.readable:
            raise OSError(errno.EIO, "Input/output error")
        return self.file.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.file.seek(offset, whence)

    def tell(self) -> int:
        return self.file.tell()

    def truncate(self, size: int | None = None) -> int:
        if not self.cuttable:
            raise OSError(errno.EIO, "Input/output error")
        return self.file.truncate(size)

    def fileno(self) -> int:
        return self.file.fileno()

    def flush(self) -> None:
        self.file.flush()

    def close(self) -> None:
        self.file.close()

    def __enter__(self) -> "AilingFile":
        return self

    def __exit__(self, *_details: object) -> None:
        self.file.close()


class AilingPath:
    """What the connection modules call Path while a test runs: a path whose files ail as told."""

    def __init__(self, path: str, room: int, readable: int, cuttable: bool) -> None:
        self.path: Path = Path(path)
        self.room: int = room
        self.readable: int = readable
        self.cuttable: bool = cuttable

    def open(self, mode: str = "rb") -> AilingFile:
        return AilingFile(cast(BinaryIO, self.path.open(mode)), self.room, self.readable, self.cuttable)


def ailing_disk(room: int = 2**62, readable: int = 2**62, cuttable: bool = True) -> Callable[[str], AilingPath]:
    def made(path: str) -> AilingPath:
        return AilingPath(path, room, readable, cuttable)

    return made


def test_a_full_disk_here_is_this_computers_problem_and_keeps_the_part(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    (root / "big.bin").write_bytes(os.urandom(BIG))
    module = base_module  # every file of this computer in a transfer, SFTP and FTP, goes through its LocalFile
    with monkeypatch.context() as patch:
        small_requests(patch)  # blocks smaller than the room, so some of them arrive before the disk is full
        patch.setattr(module, "Path", ailing_disk(room=BIG // 4))
        job = down(server, "/big.bin", here, TransferOptions(resume=RESUME_ON))
    assert job.state == State.FAILED
    assert job.message == f"{LOCAL_WRITE_TEXT.format(reason='No space left on device')} {PART_KEPT_TEXT}"
    assert holds_bytes(here / ("big.bin" + PART_SUFFIX))  # kept for the next try once there is room
    again = down(server, "/big.bin", here, TransferOptions(resume=RESUME_ON))
    assert again.state == State.DONE and (here / "big.bin").read_bytes() == (root / "big.bin").read_bytes()


@pytest.mark.parametrize("upload", [True, False])
def test_a_dropped_ftp_data_connection_is_a_broken_off_transfer_and_the_next_try_continues(tmp_path: Path, upload: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    content = os.urandom(BIG)
    (source / "big.bin").write_bytes(content)
    part = target / ("big.bin" + PART_SUFFIX)
    with ftp_server(root, drops_data_after=BIG // 4) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        served = (connection, settings, root)
        options = TransferOptions(resume=RESUME_ON)
        broken = up(served, here / "big.bin", options) if upload else down(served, "/big.bin", here, options)
        assert broken.state == State.FAILED and broken.message == f"{INTERRUPTED_TEXT} {PART_KEPT_TEXT}"
        assert holds_bytes(part)
        part.write_bytes(b"X" + part.read_bytes()[1:])  # marked: only a continued transfer keeps the mark
        again = up(served, here / "big.bin", options) if upload else down(served, "/big.bin", here, options)
        connection.close()
    assert again.state == State.DONE, again.message
    assert (target / "big.bin").read_bytes() == b"X" + content[1:]


def test_a_resumed_download_that_does_not_fit_starts_again_and_counts_each_byte_once(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    content = os.urandom(BIG)
    (root / "big.bin").write_bytes(content)
    part = here / ("big.bin" + PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    assert stopped_halfway(server, here, "/big.bin", False, "big.bin", options, part).state == State.STOPPED
    kind = type(connection)
    real = kind.download

    def too_much_when_continued(self: RemoteConnection, remote: str, local: str, progress: Callable[[int], None], limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        real(self, remote, local, progress, limit, offset, append, exclusive)
        if offset:
            with open(local, "ab") as file:
                file.write(b"more than the source has")  # the continued part does not fit its source

    monkeypatch.setattr(kind, "download", too_much_when_continued)
    job = down(server, "/big.bin", here, options)
    assert job.state == State.DONE and job.message == f"Done. {note_text('restarted', 1)}"
    assert (here / "big.bin").read_bytes() == content
    assert job.done == BIG  # what was counted of the first try was taken back


def test_many_items_left_out_are_named_up_to_five_and_counted() -> None:
    remote = Scripted(CommandResult(0, "", ""))
    job = Job(remote, Connection(name="T", host="h"), "/a", "/srv", True, "a")
    way = FileWay(job, remote, 0, Meter(job, lambda item: None), lambda conflict: Answer(Choice.NO))
    for number in range(150):
        way._left_out(f"tree/{number}.txt", "Refused.")
    failure = way.failure()
    assert failure is not None and len(way.failed) == 100
    assert str(failure) == "Partly done: 150 items were left out (tree/0.txt, tree/1.txt, tree/2.txt, tree/3.txt, tree/4.txt and 145 more). tree/0.txt: Refused."


def test_a_download_never_sets_a_date_through_a_link_to_a_folder_here(server: Server, tmp_path: Path) -> None:
    root = server[2]
    here, elsewhere = tmp_path / "here", tmp_path / "elsewhere"
    here.mkdir()
    elsewhere.mkdir()
    write(root / "tree" / "a.txt", "a", OLD_TIME)
    os.utime(root / "tree", (OLD_TIME, OLD_TIME))
    (here / "tree").symlink_to(elsewhere)
    job = down(server, "/tree", here)
    assert job.state == State.DONE, job.message
    assert read(elsewhere / "a.txt") == "a" and elsewhere.stat().st_mtime != OLD_TIME


def test_a_lost_connection_while_resolving_a_link_is_never_taken_for_an_unknown_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "a.txt", "a")
    with ssh_server(tmp_path) as running:
        connection = trusted_ssh(running)

        async def dropped(_self: asyncssh.SFTPClient, *_args: object) -> bytes:
            raise asyncssh.SFTPConnectionLost("gone")

        with monkeypatch.context() as patch:
            patch.setattr(asyncssh.SFTPClient, "realpath", dropped)
            with pytest.raises(ConnectionLost):
                connection.resolved("/a.txt")
        connection.close()


def test_a_server_path_keeps_its_steps_up_for_the_server() -> None:
    assert [normalized(path) for path in ("/a/", "//a//b/", "/a/./b", "/a/b/..", "/../a", "/")] == ["/a", "/a/b", "/a/b", "/a/b/..", "/../a", "/"]


def test_a_source_that_can_not_be_read_here_is_this_computers_problem(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    content = os.urandom(BIG)
    (tmp_path / "big.bin").write_bytes(content)
    module = base_module  # every file of this computer in a transfer, SFTP and FTP, goes through its LocalFile
    options = TransferOptions(resume=RESUME_ON)
    with monkeypatch.context() as patch:
        small_requests(patch)
        patch.setattr(module, "Path", ailing_disk(readable=BIG // 4))
        failed = up(server, tmp_path / "big.bin", options)
    assert failed.state == State.FAILED
    part = root / ("big.bin" + PART_SUFFIX)
    told = [f" {PART_KEPT_TEXT}"] if holds_bytes(part) else ["", f" {PART_GONE_TEXT}"]  # kept only where the server holds something of it
    assert failed.message in [f"{LOCAL_READ_TEXT.format(reason='Input/output error')}{text}" for text in told]  # this computer's disk, not the line
    assert not (root / "big.bin").exists()
    again = up(server, tmp_path / "big.bin", options)
    assert again.state == State.DONE and (root / "big.bin").read_bytes() == content


def test_a_part_the_server_holds_nothing_of_yet_is_neither_kept_nor_told(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The server takes in the data connection only once the test lets it, as a busy server may: when the read here fails, nothing sent
    is on the server's disk yet, so there is no part to keep, and the job never says that what arrived is kept."""
    root = tmp_path / "server"
    root.mkdir()
    content = os.urandom(BIG)
    (tmp_path / "big.bin").write_bytes(content)
    release = threading.Event()
    real_readable = data_handlers.DTPHandler.readable

    def readable(handler: data_handlers.DTPHandler) -> bool:
        return release.is_set() and bool(real_readable(handler))

    with ftp_server(root) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        try:
            with monkeypatch.context() as patch:
                patch.setattr(data_handlers.DTPHandler, "readable", readable)
                patch.setattr(base_module, "Path", ailing_disk(readable=BIG // 16))
                failed = up((ftp, settings, root), tmp_path / "big.bin", TransferOptions(resume=RESUME_ON))
                release.set()
            assert failed.state == State.FAILED
            assert failed.message == f"{LOCAL_READ_TEXT.format(reason='Input/output error')} {PART_GONE_TEXT}"  # nothing of it there: no part kept
            assert not (root / ("big.bin" + PART_SUFFIX)).exists()
            again = up((ftp, settings, root), tmp_path / "big.bin", TransferOptions(resume=RESUME_ON))
            assert again.state == State.DONE and (root / "big.bin").read_bytes() == content
        finally:
            release.set()
            ftp.close()


# --- review round 5


@pytest.mark.parametrize("resume", [RESUME_ON, RESUME_OFF])
def test_a_broken_off_transfer_says_truly_whether_its_part_was_kept(tmp_path: Path, resume: str) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "big.bin").write_bytes(os.urandom(BIG))
    part = here / ("big.bin" + PART_SUFFIX)
    with ftp_server(root, drops_data_after=BIG // 4) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        broken = down((connection, settings, root), "/big.bin", here, TransferOptions(resume=resume))
        connection.close()
    kept = resume == RESUME_ON
    assert broken.state == State.FAILED and broken.message == f"{INTERRUPTED_TEXT} {PART_KEPT_TEXT if kept else PART_GONE_TEXT}"
    assert holds_bytes(part) == kept


def test_an_append_whose_connection_drops_is_looked_at_once_the_connection_is_back_and_never_cut_on_its_length_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing tells whether someone wrote to the file between the drop and the next look: it is left as it is, and that is told."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    write(root / "log.bin", ORIGINAL)
    (here / "log.bin").write_bytes(os.urandom(BIG))
    write(here / "other.txt", "other")
    real_write = asyncssh.SFTPClientFile.write
    calls: list[int] = []

    async def line_drops(self: asyncssh.SFTPClientFile, data: bytes, offset: int | None = None) -> int:
        calls.append(1)
        if len(calls) > 3:
            raise asyncssh.SFTPConnectionLost("The line dropped.")
        return await real_write(self, data, offset)

    async def dead_file(self: asyncssh.SFTPClientFile, size: int | None = None) -> None:
        raise asyncssh.SFTPConnectionLost("The line dropped.")

    async def dead_client(self: asyncssh.SFTPClient, path: object, size: int) -> None:
        raise asyncssh.SFTPConnectionLost("The line dropped.")

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served = (connection, connection.connection, root)
        with monkeypatch.context() as patch:
            small_requests(patch)
            patch.setattr(asyncssh.SFTPClientFile, "write", line_drops)
            patch.setattr(asyncssh.SFTPClientFile, "truncate", dead_file)
            patch.setattr(asyncssh.SFTPClient, "truncate", dead_client)
            broken = up(served, here / "log.bin", person=Person(Answer(Choice.APPEND)))
        assert broken.state == State.FAILED and broken.message == APPEND_LEFT_TEXT.format(name="log.bin")
        assert (root / "log.bin").stat().st_size > len(ORIGINAL)  # it holds part of the append, as the text says
        assert [(path, note.start, note.most) for path, note in parts.broken_appends(connection.connection).items()] == [("/log.bin", len(ORIGINAL), len(ORIGINAL) + BIG)]
        told: list[str] = []
        queue = TransferQueue(lambda item: None, told=lambda job, text: told.append(text))
        later = Job(connection, connection.connection, str(here), "/", True, "other.txt", has_password=True)
        run_all(queue, later)  # the connection works again: before anything else, the file is looked at
        queue.close()
        connection.close()
    assert later.state == State.DONE, later.message
    assert (root / "log.bin").stat().st_size > len(ORIGINAL) and (root / "log.bin").read_bytes().startswith(ORIGINAL.encode())  # left as it is
    assert told == [MAYBE_PART_TEXT] and parts.broken_appends(served[1]) == {}


def test_a_dropped_data_connection_or_a_full_server_fails_only_that_file_of_a_folder(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (tmp_path / "tree").mkdir()
    (tmp_path / "tree" / "a.bin").write_bytes(os.urandom(BIG))  # its data connection drops
    write(tmp_path / "tree" / "b.txt", "b")  # the server has no room for it
    write(tmp_path / "tree" / "c.txt", "c")
    with ftp_server(root, drops_data_after=BIG // 4, refuses_store=(("b.txt", "452 Insufficient storage space."),)) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        job = up((connection, settings, root), tmp_path / "tree", TransferOptions(resume=RESUME_ON))
        connection.close()
    assert job.state == State.FAILED
    assert job.message.startswith("Partly done: 2 items were left out (tree/a.bin, tree/b.txt). tree/a.bin: ")
    assert INTERRUPTED_TEXT in job.message
    assert read(root / "tree" / "c.txt") == "c"  # the rest of the folder went on


def test_a_server_without_room_is_named(tmp_path: Path) -> None:
    write(tmp_path / "a.txt", "a")
    root = tmp_path / "server"
    root.mkdir()
    with ftp_server(root, refuses_store=(("a.txt", "452 Insufficient storage space."),)) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        job = up((connection, settings, root), tmp_path / "a.txt")
        connection.close()
    assert job.state == State.FAILED and job.message == NO_ROOM_TEXT.format(reply="452 Insufficient storage space.")


@NOT_AS_ROOT
def test_an_unreadable_file_in_a_folder_upload_is_this_computers_problem(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "tree" / "secret.txt", "s")
    write(tmp_path / "tree" / "open.txt", "o")
    (tmp_path / "tree" / "secret.txt").chmod(0)
    try:
        job = up(server, tmp_path / "tree")
    finally:
        (tmp_path / "tree" / "secret.txt").chmod(0o644)
    assert job.state == State.FAILED
    assert job.message == f"Partly done: tree/secret.txt was left out. {LOCAL_READ_TEXT.format(reason='Permission denied')}"
    assert read(root / "tree" / "open.txt") == "o"


@NOT_AS_ROOT
def test_a_part_that_can_not_be_opened_here_is_kept_and_never_taken_for_a_refused_continuation(server: Server, tmp_path: Path) -> None:
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    (root / "big.bin").write_bytes(os.urandom(BIG))
    part = here / ("big.bin" + PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    assert stopped_halfway(server, here, "/big.bin", False, "big.bin", options, part).state == State.STOPPED
    kept = part.read_bytes()
    part.chmod(0o444)  # this computer won't let the part be written
    try:
        job = down(server, "/big.bin", here, options)
    finally:
        part.chmod(0o644)
    assert job.state == State.FAILED
    assert job.message == f"{LOCAL_WRITE_TEXT.format(reason='Permission denied')} {PART_KEPT_TEXT}"
    assert part.read_bytes() == kept  # no restart from the beginning: the part is as it was


def test_an_upload_move_has_the_server_put_the_file_on_its_disk_before_the_original_goes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    write(tmp_path / "a.txt", "a")
    flushed: list[bool] = []
    real_fsync = asyncssh.SFTPClientFile.fsync

    async def noted(self: asyncssh.SFTPClientFile) -> None:
        flushed.append((tmp_path / "a.txt").exists())  # the original is still here when the server is asked
        await real_fsync(self)

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            patch.setattr(asyncssh.SFTPClientFile, "fsync", noted)
            job = up((connection, connection.connection, root), tmp_path / "a.txt", TransferOptions(delete_source=True))
        connection.close()
    assert job.state == State.DONE, job.message
    assert flushed == [True] and read(root / "a.txt") == "a" and not (tmp_path / "a.txt").exists()


def test_the_notes_of_part_files_are_read_again_only_when_the_file_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    parts.remember("k", parts.PartRecord("s", 5, OLD_TIME, 2))
    reads: list[str] = []
    real_loads = parts.json.loads

    def counted(text: str) -> object:
        reads.append(text)
        return real_loads(text)

    monkeypatch.setattr(parts.json, "loads", counted)
    for _number in range(100):
        assert parts.lookup("k") == parts.PartRecord("s", 5, OLD_TIME, 2)
    assert reads == []  # from memory: the file did not change
    path = parts.config_folder() / parts.FILE_NAME
    path.write_text('{"k": {"source": "t", "size": 7, "modified": 1.0, "part_size": 3}}', encoding="utf-8")  # another program wrote it
    os.utime(path, (OLD_TIME, OLD_TIME))
    assert parts.lookup("k") == parts.PartRecord("t", 7, 1.0, 3) and len(reads) == 1


@NOT_AS_ROOT
def test_one_unreadable_file_is_this_computers_problem_in_plain_words(server: Server, tmp_path: Path) -> None:
    write(tmp_path / "secret.txt", "s")
    (tmp_path / "secret.txt").chmod(0)
    try:
        job = up(server, tmp_path / "secret.txt")
    finally:
        (tmp_path / "secret.txt").chmod(0o644)
    assert job.state == State.FAILED and job.message == LOCAL_READ_TEXT.format(reason="Permission denied")  # never "[Errno 13] …"


def test_a_part_here_that_can_not_be_cut_back_is_this_computers_problem_over_ftp_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "big.bin").write_bytes(os.urandom(BIG))
    part = here / ("big.bin" + PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    with ftp_server(root) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        served = (connection, settings, root)
        assert stopped_halfway(served, here, "/big.bin", False, "big.bin", options, part).state == State.STOPPED
        kept = part.read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(base_module, "Path", ailing_disk(cuttable=False))  # this computer can't cut the part back to where it goes on
            job = down(served, "/big.bin", here, options)
        connection.close()
    assert job.state == State.FAILED
    assert job.message == f"{LOCAL_WRITE_TEXT.format(reason='Input/output error')} {PART_KEPT_TEXT}"
    assert part.read_bytes() == kept  # never taken for a refused continuation: no restart, the part is as it was


# --- an append is set back to the length the file really had, and never grown


HALF: str = "half of the append"
SOURCE_SIZE: int = 40  # the append's source: the most it could add
LEFT_TEXT: str = "1 file that an append left half done was changed since by something else, so it was left as it is; it may still hold part of that append."
MAYBE_PART_TEXT: str = "1 file that an append broke off in may hold part of it; as nothing tells whether it was written to since, it was left as it is."
WHOLE_TEXT: str = "1 file that an append broke off in has the append's full length, so it was left as it is."
SET_BACK_TEXT: str = "1 file that an append left half done has its old length back."


@pytest.mark.parametrize(("now", "after", "said"), [
    ("short now", "short now", LEFT_TEXT),  # someone cut it since: shorter than before the append
    (ORIGINAL + HALF, ORIGINAL + HALF, MAYBE_PART_TEXT),  # its end unknown: maybe part of our append, maybe someone's; never cut
    (ORIGINAL + HALF + "and more from another program\n", ORIGINAL + HALF + "and more from another program\n", LEFT_TEXT),  # longer than the whole append could make it
    (ORIGINAL + "w" * SOURCE_SIZE, ORIGINAL + "w" * SOURCE_SIZE, WHOLE_TEXT),  # the append went whole, only its note stayed: never cut
], ids=["shorter", "within-the-append", "longer-than-the-append", "complete"])
def test_a_broken_off_append_is_set_back_only_where_nothing_but_it_can_be_in_the_file(tmp_path: Path, now: str, after: str, said: str) -> None:
    """Before the next transfer the queue looks at a file an append broke off in, whose end is unknown (the connection or the program
    stopped): it is never cut, as nothing tells whether someone wrote to it since. At the full length the append arrived; between, it may
    hold part of it; shorter or longer, something else changed it. The note goes; the status bar says which."""
    assert len(ORIGINAL + HALF) < len(ORIGINAL) + SOURCE_SIZE < len(ORIGINAL + HALF + "and more from another program\n")
    write(tmp_path / "log.bin", now)
    with ssh_server(tmp_path) as running:
        connection = trusted_ssh(running)
        parts.remember_append(connection.connection, "/log.bin", len(ORIGINAL), len(ORIGINAL) + SOURCE_SIZE)
        parts.append_ended(connection.connection, "/log.bin")  # it broke off earlier and runs no more
        told: list[str] = []
        queue = TransferQueue(lambda item: None, told=lambda job, text: told.append(text))
        write(tmp_path / "here" / "other.txt", "other")
        job = Job(connection, connection.connection, str(tmp_path / "here"), "/", True, "other.txt", has_password=True)
        run_all(queue, job)
        queue.close()
        connection.close()
    assert job.state == State.DONE, job.message
    assert read(tmp_path / "log.bin") == after
    assert parts.broken_appends(connection.connection) == {}
    assert told == ([said] if said else [])


def test_an_append_that_breaks_off_while_another_program_writes_on_leaves_the_file_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Our append and then more than it could have added: what is in the file is no longer only ours to take away."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    write(root / "log.bin", ORIGINAL)
    write(here / "log.bin", "the appended part")
    real_write = asyncssh.SFTPClientFile.write

    async def another_writer_then_failure(self: asyncssh.SFTPClientFile, data: bytes, offset: int | None = None) -> int:
        await real_write(self, data, offset)
        with (root / "log.bin").open("a", encoding="utf-8") as file:
            file.write("another program wrote this after our bytes\n")
        raise asyncssh.SFTPFailure("The server's disk failed.")

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            patch.setattr(asyncssh.SFTPClientFile, "write", another_writer_then_failure)
            job = up((connection, connection.connection, root), here / "log.bin", person=Person(Answer(Choice.APPEND)))
        connection.close()
    assert job.state == State.FAILED and job.message == APPEND_CHANGED_TEXT.format(name="log.bin"), job.message
    assert read(root / "log.bin") == ORIGINAL + "the appended part" + "another program wrote this after our bytes\n"
    assert parts.broken_appends(connection.connection) == {}


def test_an_append_that_fails_after_another_writer_grew_the_file_keeps_what_that_writer_wrote(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The listing is older than the append: the length noted is the one the file has when the append starts, so the set-back never
    takes away what another program added in between."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    write(root / "log.bin", ORIGINAL)
    write(here / "log.bin", "the appended part, which never arrives whole")
    grown = ORIGINAL + "written by another program meanwhile\n"

    class GrowsTheFileFirst(Person):
        @override
        def __call__(self, job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
            write(root / "log.bin", grown)  # while the person thinks, another program writes on
            super().__call__(job, conflict, reply)

    real_write = asyncssh.SFTPClientFile.write

    async def fails(self: asyncssh.SFTPClientFile, data: bytes, offset: int | None = None) -> int:
        await real_write(self, data[: len(data) // 2], offset)
        raise asyncssh.SFTPFailure("The server's disk failed.")

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            patch.setattr(asyncssh.SFTPClientFile, "write", fails)
            job = up((connection, connection.connection, root), here / "log.bin", person=GrowsTheFileFirst(Answer(Choice.APPEND)))
        connection.close()
    assert job.state == State.FAILED
    assert read(root / "log.bin") == grown
    assert parts.broken_appends(connection.connection) == {}


@NOT_AS_ROOT
def test_an_append_whose_length_can_not_be_noted_here_does_not_start(tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    write(root / "log.bin", ORIGINAL)
    write(here / "log.bin", "more")
    folder = config_folder()
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        folder.chmod(0o500)  # from here on this computer's settings folder can't be written
        try:
            job = up((connection, connection.connection, root), here / "log.bin", person=Person(Answer(Choice.APPEND)))
        finally:
            folder.chmod(0o700)
            connection.close()
    assert job.state == State.FAILED and job.message == APPEND_NOT_NOTED_TEXT
    assert read(root / "log.bin") == ORIGINAL


# --- a question that takes long on FTP


class AnswersOnceHeard(Person):
    """Answers only once the server heard what the test waits for while the question was open: no clock decides. Its thread is joined
    by the test."""

    def __init__(self, heard: threading.Event, *answers: Answer) -> None:
        super().__init__(*answers)
        self.heard: threading.Event = heard
        self.threads: list[threading.Thread] = []
        self.unheard: list[str] = []  # the text of a wait that ran out, for the test to fail with

    @override
    def __call__(self, job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
        def later() -> None:
            if not self.heard.wait(WAIT_SECONDS):
                self.unheard.append(f"the server did not hear what the answer waits for within {WAIT_SECONDS} s")
            Person.__call__(self, job, conflict, reply)  # answered all the same: the transfer never waits for a reply that won't come

        thread = threading.Thread(target=later)
        self.threads.append(thread)
        thread.start()

    def join(self) -> bool:
        """Every answering thread ended; False when one did not (asserted by the test after its clean-up, so it hides no other failure)."""
        for thread in self.threads:
            thread.join(WAIT_SECONDS)
        return not any(thread.is_alive() for thread in self.threads)


def test_an_answer_waiting_for_what_the_server_never_heard_says_so_and_still_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    ending = WAIT_SECONDS  # the answering thread is waited for as long as ever; only what it waits for is short
    monkeypatch.setattr(sys.modules[__name__], "WAIT_SECONDS", 0.1)
    settings = Connection(name="T", host="127.0.0.1", user=USER, protocol=FTP)
    job = Job(FtpConnection(settings, PASSWORD), settings, "/nowhere", "/", True, "a.txt")
    replies: list[Answer] = []
    person = AnswersOnceHeard(threading.Event(), Answer(Choice.YES))  # never set: the server hears nothing
    person(job, Conflict("a.txt", "/", True, 3, None, 3, None, None), replies.append)
    for thread in person.threads:
        thread.join(ending)
    assert not any(thread.is_alive() for thread in person.threads)
    assert person.unheard == ["the server did not hear what the answer waits for within 0.1 s"]
    assert replies == [Answer(Choice.YES)]  # answered all the same: nothing waits for a reply that won't come


@pytest.mark.parametrize("kept_alive", [True, False], ids=["kept-alive", "closed-anyway"])
def test_a_long_question_on_ftp_never_costs_the_transfer_its_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kept_alive: bool) -> None:
    """While the question waits, the queue speaks to the server now and then (the answer comes once three NOOPs were heard). Where the
    server closed the connection all the same (here at the first NOOP), the transfer logs in again by itself once the answer is there."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    write(root / "a.txt", "old")
    write(here / "a.txt", "new")
    monkeypatch.setattr(queue_module, "KEEPALIVE_SECONDS", 0.05)  # only how fast the NOOPs come; nothing waits for a moment
    afresh: list[bool] = []
    real_afresh = FtpConnection._start_afresh

    def counted(self: FtpConnection) -> None:
        afresh.append(True)
        real_afresh(self)

    monkeypatch.setattr(FtpConnection, "_start_afresh", counted)
    noops: list[bool] = []
    heard = threading.Event()

    def noop() -> None:
        noops.append(True)
        if len(noops) >= (3 if kept_alive else 1):
            heard.set()

    person = AnswersOnceHeard(heard, Answer(Choice.YES))
    with ftp_server(root, noop_heard=noop, drops_at_noop=not kept_alive) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        try:
            job = up((ftp, settings, root), here / "a.txt", person=person)
        finally:
            ftp.close()
            ended = person.join()
    assert ended
    assert person.unheard == [], person.unheard[0]
    assert heard.is_set()
    assert job.state == State.DONE, job.message
    assert read(root / "a.txt") == "new"
    assert afresh == ([] if kept_alive else [True])


OTHER_CHRONO_FTP: str = """import sys
from chrono_ftp.config import parts
from chrono_ftp.core.connection import Connection
login = Connection(name="T", host=sys.argv[1], port=int(sys.argv[2]), user=sys.argv[3])
assert parts.remember_append(login, "/log.bin", int(sys.argv[4]), int(sys.argv[5]))
print("appending", flush=True)
sys.stdin.read()
"""


def test_an_append_another_chrono_ftp_is_running_is_never_set_back(tmp_path: Path) -> None:
    """Its note looks like one of a broken-off append, but its Chrono FTP still runs: nothing is cut until that one has ended."""
    write(tmp_path / "log.bin", ORIGINAL + HALF)  # the other one is writing its append right now
    write(tmp_path / "here" / "other.txt", "other")
    told: list[str] = []
    with ssh_server(tmp_path) as running:
        connection = trusted_ssh(running)
        login = connection.connection
        source = Path(parts.__file__).resolve().parents[2]  # where the program's package is, for the other Chrono FTP
        with child(OTHER_CHRONO_FTP, [login.host, str(login.port), login.user, str(len(ORIGINAL)), str(len(ORIGINAL) + SOURCE_SIZE)], {**os.environ, "PYTHONPATH": str(source)}) as other:  # it ends with the block
            other.says(b"appending\n")
            queue = TransferQueue(lambda item: None, told=lambda job, text: told.append(text))
            first = Job(connection, login, str(tmp_path / "here"), "/", True, "other.txt", has_password=True)
            run_all(queue, first)
            assert read(tmp_path / "log.bin") == ORIGINAL + HALF and told == []  # its append runs: left alone
        second = Job(connection, login, str(tmp_path / "here"), "/", True, "other.txt", has_password=True, options=TransferOptions(ask_before_replacing=False))
        run_all(queue, second)
        queue.close()
        connection.close()
    assert first.state == State.DONE and second.state == State.DONE, (first.message, second.message)
    assert read(tmp_path / "log.bin") == ORIGINAL + HALF and told == [MAYBE_PART_TEXT]  # it ended mid-append: told, never cut on length alone
    assert parts.broken_appends(login) == {}


@pytest.mark.parametrize("problem", ["cut-refused", "file-gone"])
def test_an_append_that_can_not_be_set_back_says_why_and_its_note_goes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, problem: str) -> None:
    """Trying again can't help a file the server won't cut, nor one that is gone: the text says which, and the note is not kept."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    write(root / "log.bin", ORIGINAL)
    write(here / "log.bin", "the appended part")
    real_write = asyncssh.SFTPClientFile.write

    async def fails_halfway(self: asyncssh.SFTPClientFile, data: bytes, offset: int | None = None) -> int:
        await real_write(self, data[: len(data) // 2], offset)
        if problem == "file-gone":
            (root / "log.bin").unlink()
        raise asyncssh.SFTPFailure("The server's disk failed.")

    def refused(self: SshConnection, path: str, length: int) -> None:
        raise RemoteActionFailed("Not allowed.")

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            patch.setattr(asyncssh.SFTPClientFile, "write", fails_halfway)
            patch.setattr(SshConnection, "cut", refused)
            job = up((connection, connection.connection, root), here / "log.bin", person=Person(Answer(Choice.APPEND)))
        connection.close()
    said = APPEND_NOT_CUT_TEXT if problem == "cut-refused" else APPEND_GONE_TEXT
    assert job.state == State.FAILED and job.message == said.format(name="log.bin"), job.message
    assert parts.broken_appends(connection.connection) == {}
    if problem == "cut-refused":
        assert read(root / "log.bin").startswith(ORIGINAL) and len(read(root / "log.bin")) > len(ORIGINAL)  # as the text says


def test_a_finished_append_whose_note_can_not_be_removed_is_done_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    write(root / "log.bin", ORIGINAL)
    write(here / "log.bin", "the appended part")

    def not_removed(login: Connection, remote: str) -> bool:
        return False

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            patch.setattr(parts, "forget_append", not_removed)
            job = up((connection, connection.connection, root), here / "log.bin", person=Person(Answer(Choice.APPEND)))
        assert job.state == State.DONE and job.message == f"Done. {note_text('notes_left', 1)}", job.message
        assert not any(undo_broken_appends(connection, connection.connection).values())  # the note left over never cuts the finished file
        connection.close()
    assert read(root / "log.bin") == ORIGINAL + "the appended part"
    assert parts.broken_appends(connection.connection) == {}


@pytest.mark.parametrize("how", ["broke-off-and-edited", "went-whole-then-edited"])
def test_a_file_written_to_after_its_append_ended_is_never_set_back_even_within_the_span(tmp_path: Path, how: str) -> None:
    """Its note says how the append ended: broken off with the file's size and date then, or whole. Someone edited the file since, to a
    length that lies within the span of the append: it is left as it is."""
    write(tmp_path / "log.bin", ORIGINAL + HALF)
    write(tmp_path / "here" / "other.txt", "other")
    told: list[str] = []
    with ssh_server(tmp_path) as running:
        connection = trusted_ssh(running)
        login = connection.connection
        parts.remember_append(login, "/log.bin", len(ORIGINAL), len(ORIGINAL) + SOURCE_SIZE)
        parts.append_ended(login, "/log.bin")
        if how == "broke-off-and-edited":
            then = connection.details("/log.bin")
            assert then is not None and then.modified is not None
            parts.mark_append(login, "/log.bin", parts.BROKEN, then.size, then.modified)
        else:
            parts.mark_append(login, "/log.bin", parts.DONE)
        edited = ORIGINAL + "edited by the person"  # within the span, other bytes, a later date
        write(tmp_path / "log.bin", edited, NEWER_TIME)
        queue = TransferQueue(lambda item: None, told=lambda job, text: told.append(text))
        job = Job(connection, login, str(tmp_path / "here"), "/", True, "other.txt", has_password=True)
        run_all(queue, job)
        queue.close()
        connection.close()
    assert job.state == State.DONE, job.message
    assert read(tmp_path / "log.bin") == edited
    assert told == ([LEFT_TEXT] if how == "broke-off-and-edited" else [])
    assert parts.broken_appends(login) == {}


@pytest.mark.parametrize("protocol", [FTP, SFTP])
def test_a_refusal_for_now_while_a_part_is_continued_keeps_the_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protocol: str) -> None:
    """The server has no room right now (FTP 452, SFTP no space): that is no refusal to continue. The part is still good, so it stays
    with its note, and the item fails with the truthful text; only a refusal of the continuation itself starts again."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "big.bin").write_bytes(os.urandom(BIG))
    part = root / ("big.bin" + PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    reply = "452 No room left on the disk."
    if protocol == FTP:
        with ftp_server(root) as port:
            settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
            connection = FtpConnection(settings, PASSWORD)
            connection.connect()
            assert stopped_halfway((connection, settings, root), here / "big.bin", "/", True, "big.bin", options, part).state == State.STOPPED
            connection.close()
        kept = part.read_bytes()
        note = parts.lookup(upload_key(settings, "/big.bin" + PART_SUFFIX))
        assert note is not None
        with ftp_server(root, refuses_store=(("big.bin", reply),)) as port:
            again = replace(settings, port=port)
            parts.remember(upload_key(again, "/big.bin" + PART_SUFFIX), note)  # the same server, now on another port
            connection = FtpConnection(again, PASSWORD)
            connection.connect()
            job = up((connection, connection.connection, root), here / "big.bin", options)
            connection.close()
        said = NO_ROOM_TEXT.format(reply=reply)
    else:
        async def no_room(self: asyncssh.SFTPClientFile, data: bytes, offset: int | None = None) -> int:
            raise asyncssh.SFTPNoSpaceOnFilesystem("No space left on device")

        with ssh_server(root) as running:
            connection = trusted_ssh(running)
            assert stopped_halfway((connection, connection.connection, root), here / "big.bin", "/", True, "big.bin", options, part).state == State.STOPPED
            kept = part.read_bytes()
            with monkeypatch.context() as patch:
                patch.setattr(asyncssh.SFTPClientFile, "write", no_room)
                job = up((connection, connection.connection, root), here / "big.bin", options)
            connection.close()
        said = NO_ROOM_TEXT.format(reply="No space left on device")
    assert job.state == State.FAILED and job.message == f"{said} {PART_KEPT_TEXT}", job.message
    assert part.read_bytes() == kept and not (root / "big.bin").exists()  # the part as it was: the next try goes on from it


def test_the_lock_file_of_a_chrono_ftp_goes_when_it_ends_and_those_of_ended_ones_when_notes_are_read(tmp_path: Path) -> None:
    folder = config_folder() / parts.OWNERS_FOLDER
    folder.mkdir(parents=True)
    (folder / f"left-by-one-that-stopped-at-once{parts.OWNER_SUFFIX}").write_text("")  # nobody holds it
    with child(OTHER_CHRONO_FTP, ["h", "22", "u", "0", "1"], {**os.environ, "PYTHONPATH": str(Path(parts.__file__).resolve().parents[2])}) as other:
        other.says(b"appending\n")
        held = [path.name for path in folder.iterdir()]
        assert len(held) == 1 and held[0].endswith(parts.OWNER_SUFFIX)  # its own, held; the one nobody held is gone
        assert parts.broken_appends(Connection(name="T", host="h", port=22, user="u")) == {}  # it still runs: its append is left alone
        assert [path.name for path in folder.iterdir()] == held
    assert list(folder.iterdir()) == []  # it ended and took its lock file with it


def test_a_mark_of_how_an_append_ended_never_stands_over_a_newer_note_of_another_chrono_ftp(tmp_path: Path) -> None:
    login = Connection(name="T", host="h", port=22, user="u")
    assert parts.remember_append(login, "/log.bin", 5, 9)
    parts.append_ended(login, "/log.bin")
    parts.mark_append(login, "/log.bin", parts.DONE)  # this Chrono FTP's append went whole
    with child(OTHER_CHRONO_FTP, ["h", "22", "u", "100", "200"], {**os.environ, "PYTHONPATH": str(Path(parts.__file__).resolve().parents[2])}) as other:
        other.says(b"appending\n")  # another Chrono FTP appends to the same file now, from 100
    notes = parts.broken_appends(login)
    assert [(path, note.start, note.most, note.state) for path, note in notes.items()] == [("/log.bin", 100, 200, parts.RUNNING)]  # its note stands


def test_a_busy_ftp_server_refuses_one_file_for_now_and_the_other_files_go_on(tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    write(here / "box" / "busy.txt", "later")
    write(here / "box" / "ok.txt", "now")
    reply = "450 The file is in use."
    with ftp_server(root, refuses_store=(("busy.txt", reply),)) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        job = up((ftp, settings, root), here / "box", TransferOptions())
        ftp.close()
    assert job.state == State.FAILED and job.message == f"Partly done: box/busy.txt was left out. {BUSY_TEXT.format(reply=reply)}", job.message
    assert read(root / "box" / "ok.txt") == "now"
    assert sorted(path.name for path in (root / "box").iterdir()) == ["ok.txt"]  # nothing of busy.txt arrived, so no part either


@pytest.mark.parametrize("now", ["its-size-with-another-date", "none-of-it-arrived"])
def test_a_broken_off_append_is_cut_back_only_where_nothing_else_can_be_in_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, now: str) -> None:
    """The note says how it broke off: the file's size and date then. Another date at that size is someone else's writing: left as it is.
    Where none of the append arrived, there is nothing to undo: no cut, and nothing alarming is told."""
    root = tmp_path / "server"
    if now == "its-size-with-another-date":
        write(root / "log.bin", "0123456", NEWER_TIME)
        note = parts.AppendNote(5, 9, "", parts.BROKEN, 7, OLD_TIME)
    else:
        write(root / "log.bin", "01234", OLD_TIME)
        note = parts.AppendNote(5, 9, "", parts.BROKEN, 5, OLD_TIME)
    cuts: list[int] = []

    def noted(self: SshConnection, path: str, length: int) -> None:
        cuts.append(length)

    monkeypatch.setattr(SshConnection, "cut", noted)
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        outcome = set_back(connection, connection.connection, "/log.bin", note)
        connection.close()
    assert outcome == (LEFT if now == "its-size-with-another-date" else UNCHANGED) and cuts == []

