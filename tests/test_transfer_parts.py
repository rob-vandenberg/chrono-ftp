"""Part files and moves (FB-02, FB-08): a note belongs to its part file; a refused continuation starts again; every reason an original stays
is told; dates no clock can hold; the connection lost in the middle of the small steps."""

import asyncio
import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast, override

import asyncssh
import pytest

from chrono_ftp.config import parts
from chrono_ftp.config.parts import PartRecord, download_key, download_source, upload_key, upload_source
from chrono_ftp.config.settings import config_folder
from chrono_ftp.connections.base import LOCAL_READ_TEXT, NOT_EMPTY_TEXT, OLD_SUFFIX, SHORT_TEXT, CommandResult, RemoteConnection, RemoteEntry
from chrono_ftp.connections.ftp import INTERRUPTED_TEXT, FtpConnection
from chrono_ftp.connections.ssh import STOPPED_TAKING_TEXT, SshConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ConnectionLost, RemoteActionFailed
from chrono_ftp.core.transferoptions import RESUME_OFF, RESUME_ON, TransferOptions
from chrono_ftp.transfers import filebyfile as filebyfile_module
from chrono_ftp.transfers.filebyfile import OLD_NOT_NOTED_TEXT, PART_GONE_TEXT, FileWay, local_size
from chrono_ftp.transfers.job import PART_SUFFIX, Job, Meter, State, Stopped
from chrono_ftp.transfers.options import Answer, Choice, Conflict
from chrono_ftp.transfers.queue import APPEND_NOTES, NOTES, Owned, TransferQueue, note_text, tidy
from chrono_ftp.transfers.rsyncway import _remove_arrived_sources
from tests.fakeservers import NOT_AS_ROOT, PASSWORD, USER, ftp_server, ssh_server
from tests.test_connections import trusted_ssh
from tests.test_engine import Scripted
from tests.test_transfers import wait_for
from tests.test_transfer_options import BIG, OLD_TIME, SMALL_LIMIT, SMART_SMALL, WAIT_SECONDS, Person, Server, child, down, holds_bytes, read, server, stopped_halfway, up, write

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_parts.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: a note per part file, a refused continuation, a cut tail, every reason an original stays, impossible dates, the lost
#          connection in the small steps, a folder that is not empty, counting what vanishes; a request that fails out of order in a
#          pipelined transfer leaves no gap in the kept part; the notes wait for another process and are on the disk with their folder;
#          what a set-back tells, for one and for more; a copy not confirmed on the server's disk keeps its original; the lock test waits
#          for the system's list of locks; a part or an old file without its stored note; an old copy that stays; an append broken off
#          out of order at its full length; a failed swap leaves no note; the date test's server tells one date; tests that need a
#          refusal skip as root; half a line in the journal; a part that took its real name; an FTP move tells what was checked; a part
#          that can't be removed stays ours; a part kept by Always continues under Smart; a source that vanishes here; a part noted
#          before its first byte; this computer's problems in its words, also in the queue; a failure while writing is no taken name;
#          a refused upload leaves no note; an arriving file waits for no note on the disk; an old part that can't be removed; a part
#          here that vanished; a part the server won't cut; an FTP part not as it was left; Stop while counting; a size that lies and a
#          Stop while nothing grows; numbers no float holds in the note files; a continued FTP upload that did not fit; an Edit save
#          after a broken-off one; the process holding the lock never outlives its test; a finished copy kept until it takes its real
#          name; a part that could not be removed after a refused continuation keeps its note; every damaged note is left out, and only
#          the newest notes are kept

__all__ = ["server"]  # the fixture: SFTP and FTP


def sources_of(here: Path, upload: bool, root: Path) -> tuple[Path, Path]:
    return (here, root) if upload else (root, here)


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_never_continues_with_the_bytes_of_another_file_of_the_same_name(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    source_root, target = sources_of(here, upload, root)
    first, second = os.urandom(BIG), os.urandom(BIG)
    write(source_root / "a" / "big.bin", "")
    write(source_root / "b" / "big.bin", "")
    (source_root / "a" / "big.bin").write_bytes(first)
    (source_root / "b" / "big.bin").write_bytes(second)
    part = target / ("big.bin" + PART_SUFFIX)  # one part file for both: the same name lands in the same folder
    options = TransferOptions(resume=RESUME_ON)
    for folder, content in (("a", first), ("b", second)):
        start = content[:1000]  # the stop comes once the part holds bytes of this very file
        if upload:
            stopped = stopped_halfway(server, here / folder / "big.bin", "/", True, "big.bin", options, part, start)
        else:
            stopped = stopped_halfway(server, here, f"/{folder}/big.bin", False, "big.bin", options, part, start)
        assert stopped.state == State.STOPPED and holds_bytes(part, start)
    again = up(server, here / "a" / "big.bin", options) if upload else down(server, "/a/big.bin", here, options)
    assert again.state == State.DONE, again.message
    assert (target / "big.bin").read_bytes() == first  # never the start of b with the rest of a


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_continues_from_its_confirmed_bytes_and_anything_after_them_goes(server: Server, tmp_path: Path, upload: bool) -> None:
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    source, target = sources_of(here, upload, root)
    content = os.urandom(BIG)
    (source / "big.bin").write_bytes(content)
    part = target / ("big.bin" + PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    stopped = stopped_halfway(server, here / "big.bin" if upload else here, "/" if upload else "/big.bin", upload, "big.bin", options, part)
    assert stopped.state == State.STOPPED
    with part.open("ab") as file:
        file.write(b"\0" * 1000)  # what a failed request may leave after the confirmed bytes: a gap of nothing
    again = up(server, here / "big.bin", options) if upload else down(server, "/big.bin", here, options)
    assert again.state == State.DONE, again.message
    assert (target / "big.bin").read_bytes() == content


@pytest.mark.parametrize("upload", [True, False])
def test_a_refused_continuation_starts_again_at_once_and_says_so(tmp_path: Path, upload: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    source, target = sources_of(here, upload, root)
    content = os.urandom(BIG)
    (source / "big.bin").write_bytes(content)
    part = target / ("big.bin" + PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    with ftp_server(root, refuses_restart=True) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        served = (connection, settings, root)
        stopped = stopped_halfway(served, here / "big.bin" if upload else here, "/" if upload else "/big.bin", upload, "big.bin", options, part)
        assert stopped.state == State.STOPPED and holds_bytes(part)
        again = up(served, here / "big.bin", options) if upload else down(served, "/big.bin", here, options)
        first_retry_message = again.message
        retried = up(served, here / "big.bin", options) if upload else down(served, "/big.bin", here, options)
        connection.close()
    assert again.state == State.DONE and first_retry_message == f"Done. {note_text('restarted', 1)}"
    assert (target / "big.bin").read_bytes() == content and not part.exists()
    assert retried.state == State.DONE  # nothing was left to poison the next try


def test_a_finished_copy_is_kept_for_the_next_try_when_the_old_file_can_not_be_set_aside_yet(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every byte arrived, then the connection was lost while a free aside name was looked for: nothing took the real name yet, so the
    whole copy stays, noted, and the next try continues from its end."""
    connection, settings, root = server
    write(root / "a.txt", "old")
    content = b"the whole new content"
    (tmp_path / "a.txt").write_bytes(content)

    def lost(self: RemoteConnection, path: str) -> bool:
        raise ConnectionLost("The connection was lost.")

    options = TransferOptions(resume=RESUME_ON, ask_before_replacing=False)
    with monkeypatch.context() as patch:
        patch.setattr(type(connection), "name_taken", lost)
        job = up(server, tmp_path / "a.txt", options)
    assert job.state == State.FAILED, job.message
    part = root / ("a.txt" + PART_SUFFIX)
    note = parts.lookup(upload_key(settings, "/a.txt" + PART_SUFFIX))
    assert part.read_bytes() == content and note is not None and note.part_size == len(content) and read(root / "a.txt") == "old"
    again = up(server, tmp_path / "a.txt", options)
    assert again.state == State.DONE and (root / "a.txt").read_bytes() == content and not part.exists(), again.message


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_that_can_not_be_removed_after_a_refused_continuation_keeps_its_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    """The server refuses to continue, and the old part can't be removed: it stays ours (tidying removes it later, and that is told),
    and the file goes from the beginning under the next free name."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    source, target = sources_of(here, upload, root)
    content = os.urandom(BIG)
    (source / "big.bin").write_bytes(content)
    part = target / ("big.bin" + PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    with ftp_server(root, refuses_restart=True) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        served = (connection, settings, root)
        stopped = stopped_halfway(served, here / "big.bin" if upload else here, "/" if upload else "/big.bin", upload, "big.bin", options, part)
        assert stopped.state == State.STOPPED and holds_bytes(part)
        with monkeypatch.context() as patch:
            if upload:
                real_delete = FtpConnection.delete

                def refused(self: FtpConnection, path: str, folder: bool) -> None:
                    if path.endswith(PART_SUFFIX):
                        raise RemoteActionFailed("Refused.")
                    real_delete(self, path, folder)

                patch.setattr(FtpConnection, "delete", refused)
            else:
                real_remove, real_unlink = os.remove, Path.unlink

                def kept(path: object, *args: object, **named: object) -> None:
                    if str(path).endswith(PART_SUFFIX):
                        raise PermissionError(13, "Refused")
                    real_remove(str(path))

                def kept_path(self: Path, missing_ok: bool = False) -> None:
                    if self.name.endswith(PART_SUFFIX):
                        raise PermissionError(13, "Refused")
                    real_unlink(self, missing_ok)

                patch.setattr(os, "remove", kept)
                patch.setattr(Path, "unlink", kept_path)
            again = up(served, here / "big.bin", options) if upload else down(served, "/big.bin", here, options)
        connection.close()
    key = upload_key(settings, "/big.bin" + PART_SUFFIX) if upload else download_key(str(part))
    assert again.state == State.DONE and note_text("parts_left", 1) in again.message, again.message
    assert (target / "big.bin").read_bytes() == content and part.exists() and parts.lookup(key) is not None


def test_an_upload_continues_with_rest_and_stor_also_where_appending_is_not_allowed(tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    content = os.urandom(BIG)
    (here / "big.bin").write_bytes(content)
    part = root / ("big.bin" + PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    with ftp_server(root, rights="elrdfmwMT") as port:  # no a: APPE is refused
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        served = (connection, settings, root)
        assert stopped_halfway(served, here / "big.bin", "/", True, "big.bin", options, part).state == State.STOPPED
        part.write_bytes(b"X" + part.read_bytes()[1:])  # marked: only a continued transfer keeps the mark
        again = up(served, here / "big.bin", options)
        connection.close()
    assert again.state == State.DONE and again.message == f"Done. {note_text('continued_by_size', 1)}", again.message  # a known limit, told
    assert (root / "big.bin").read_bytes() == b"X" + content[1:]


def test_a_download_move_looks_up_each_file_without_reading_its_folder_again(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        write(root / "tree" / name, name)
    listed: list[str] = []
    kind = type(connection)
    listing = kind.list_folder_all  # every listing goes through it

    def counted(self: RemoteConnection, path: str) -> tuple[list[RemoteEntry], list[str]]:
        listed.append(path)
        return listing(self, path)

    monkeypatch.setattr(kind, "list_folder_all", counted)
    job = down(server, "/tree", here, TransferOptions(delete_source=True))
    assert job.state == State.DONE, job.message
    assert sorted(listed) == ["/", "/tree", "/tree"]  # where the job is, counting it, fetching it: never once per file
    assert not (root / "tree").exists()


@pytest.mark.parametrize("upload", [True, False])
def test_an_arrival_the_server_can_not_measure_is_kept_and_its_original_stays(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    source, target = sources_of(here, upload, root)
    write(source / "a.txt", "content")
    kind = type(connection)
    listing = kind.list_folder

    def no_size(_self: RemoteConnection, _path: str) -> int | None:
        return None

    def listed_without_sizes(self: RemoteConnection, path: str) -> list[RemoteEntry]:
        return [replace(entry, size=0, size_known=False) for entry in listing(self, path)]

    if upload:
        monkeypatch.setattr(kind, "file_size", no_size)
    else:
        monkeypatch.setattr(kind, "list_folder", listed_without_sizes)
    job = up(server, here / "a.txt", TransferOptions(delete_source=True)) if upload else down(server, "/a.txt", here, TransferOptions(delete_source=True))
    assert job.state == State.DONE, job.message
    assert read(target / "a.txt") == "content" and read(source / "a.txt") == "content"
    assert job.message == f"Done. {note_text('kept_unchecked', 1)} {note_text('sizes_unknown', 1)}"


@pytest.mark.parametrize("upload", [True, False])
@NOT_AS_ROOT
def test_an_original_that_can_not_be_removed_says_its_copy_arrived_whole(tmp_path: Path, upload: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    source, target = sources_of(here, upload, root)
    write(source / "a.txt", "content")
    if upload:
        here.chmod(0o555)  # the original can't be removed from this folder
    try:
        with ftp_server(root, rights="elrafmwMT") as port:  # no d: the server refuses to delete
            settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
            connection = FtpConnection(settings, PASSWORD)
            connection.connect()
            served = (connection, settings, root)
            job = up(served, here / "a.txt", TransferOptions(delete_source=True)) if upload else down(served, "/a.txt", here, TransferOptions(delete_source=True))
            connection.close()
    finally:
        here.chmod(0o755)
    assert job.state == State.DONE and job.message == f"Done. {note_text('kept_not_removed', 1)}"
    assert read(target / "a.txt") == "content" and read(source / "a.txt") == "content"


def test_an_append_that_did_not_add_exactly_the_source_keeps_the_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as running:  # an append to a server needs one that can cut a file back: SFTP
        connection = trusted_ssh(running)
        appended_more_than_sent(connection, (connection, connection.connection, root), tmp_path, root, monkeypatch)
        connection.close()


def appended_more_than_sent(connection: RemoteConnection, server: Server, tmp_path: Path, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "log.txt", "second\n")
    write(root / "log.txt", "first\n")
    kind = type(connection)
    sending = kind.upload

    def more_than_sent(self: RemoteConnection, local: str, remote: str, progress: Callable[[int], None], limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        sending(self, local, remote, progress, limit, offset, append, exclusive)
        with (root / "log.txt").open("a", encoding="utf-8") as file:
            file.write("someone else wrote too\n")

    monkeypatch.setattr(kind, "upload", more_than_sent)
    job = up(server, tmp_path / "log.txt", TransferOptions(delete_source=True), Person(Answer(Choice.APPEND)))
    assert job.state == State.DONE and job.message == f"Done. {note_text('kept_unchecked', 1)}"
    assert read(tmp_path / "log.txt") == "second\n"


def test_a_fetched_copy_is_on_the_disk_before_its_original_goes(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    write(root / "a.txt", "content")
    happened: list[str] = []
    real_fsync = os.fsync
    kind = type(connection)
    deleting = kind.delete

    def synced(descriptor: int) -> None:
        happened.append("on the disk")
        real_fsync(descriptor)

    def deleted(self: RemoteConnection, path: str, is_folder: bool) -> None:
        happened.append(f"deleted {path}")
        deleting(self, path, is_folder)

    monkeypatch.setattr(os, "fsync", synced)
    monkeypatch.setattr(kind, "delete", deleted)
    job = down(server, "/a.txt", here, TransferOptions(delete_source=True))
    assert job.state == State.DONE, job.message
    assert happened.index("deleted /a.txt") > happened.index("on the disk")


@NOT_AS_ROOT
def test_rsync_looks_at_every_source_of_a_move_and_tells_each_reason(tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    for name in ("changed.txt", "unchecked.txt", "fine.txt"):
        write(here / "tree" / name, name, OLD_TIME)
    write(here / "locked" / "stays.txt", "stays.txt", OLD_TIME)
    for name in ("changed.txt", "fine.txt"):
        write(root / "tree" / name, name, OLD_TIME)  # arrived; unchecked.txt did not
    write(root / "locked" / "stays.txt", "stays.txt", OLD_TIME)
    write(here / "tree" / "changed.txt", "changed afterwards", OLD_TIME + 5)
    (here / "locked").chmod(0o555)
    try:
        with ssh_server(root) as running:
            connection = trusted_ssh(running)
            job = Job(connection, connection.connection, str(here / "tree"), "/", True, "tree", options=TransferOptions(delete_source=True))
            sources: dict[str, tuple[int, float | None]] = {f"tree/{name}": (len(name), OLD_TIME) for name in ("changed.txt", "unchecked.txt", "fine.txt")}
            sources["locked/stays.txt"] = (len("stays.txt"), OLD_TIME)
            kept = _remove_arrived_sources(job, sources)
            connection.close()
    finally:
        (here / "locked").chmod(0o755)
    assert kept == {"kept_changed": 1, "kept_unchecked": 1, "kept_not_removed": 1, "removed_unflushed": 0}  # the server put fine.txt on its disk
    assert not (here / "tree" / "fine.txt").exists()  # looked at after the one that could not be removed
    assert (here / "locked" / "stays.txt").exists()


@pytest.mark.parametrize("moment", [1e300, -1e300, float("nan")])
def test_a_download_with_a_date_no_clock_can_hold_arrives_and_says_so(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, moment: float) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    write(root / "a.txt", "content")
    kind = type(connection)
    listing = kind.list_folder

    looked_up = kind.details
    looked_up_now = kind.fresh_details

    def listed_with_that_date(self: RemoteConnection, path: str) -> list[RemoteEntry]:
        return [replace(entry, modified=moment) for entry in listing(self, path)]

    def with_that_date(self: RemoteConnection, path: str) -> RemoteEntry | None:
        found = looked_up(self, path)
        return replace(found, modified=moment) if found is not None else None  # the server says the same date every time it is asked

    def now_with_that_date(self: RemoteConnection, path: str) -> RemoteEntry | None:
        found = looked_up_now(self, path)
        return replace(found, modified=moment) if found is not None else None

    monkeypatch.setattr(kind, "list_folder", listed_with_that_date)
    monkeypatch.setattr(kind, "details", with_that_date)
    monkeypatch.setattr(kind, "fresh_details", now_with_that_date)
    job = down(server, "/a.txt", here)
    assert job.state == State.DONE and "kept its own date for 1 of these items" in job.message, job.message
    assert read(here / "a.txt") == "content"


class Lost(Scripted):
    """A connection that drops at its next step."""

    def __init__(self) -> None:
        super().__init__(CommandResult(0, "", ""))

    @override
    def set_time(self, path: str, modified: float) -> None:
        raise ConnectionLost("gone")

    @override
    def set_rights(self, path: str, mode: str) -> None:
        raise ConnectionLost("gone")

    @override
    def delete(self, path: str, is_folder: bool) -> None:
        raise ConnectionLost("gone")


def test_a_lost_connection_in_the_small_steps_ends_the_job_and_is_never_taken_for_a_refusal() -> None:
    remote = Lost()
    job = Job(remote, Connection(name="T", host="h"), "/a", "/srv", True, "a", options=TransferOptions(ignore_rights_errors=True))
    way = FileWay(job, remote, 0, Meter(job, lambda item: None), lambda conflict: Answer(Choice.NO))
    with pytest.raises(ConnectionLost):
        way._time("/srv/a", OLD_TIME)
    with pytest.raises(ConnectionLost):
        way._rights("/srv/a", "644")
    with pytest.raises(ConnectionLost):
        way._clear_part("key", lambda: remote.delete("/srv/a.part", False), True)
    assert way.dates_not_kept == 0


def test_a_size_question_on_a_dropped_ftp_line_is_a_lost_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "a.txt", "abc")
    with ftp_server(tmp_path) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()

        def dropped(*_args: object) -> int:
            raise EOFError

        monkeypatch.setattr("ftplib.FTP.size", dropped)
        with pytest.raises(ConnectionLost):
            connection.file_size("/a.txt")
        connection.close()


@pytest.mark.parametrize("over_ftp", [True, False])
def test_a_folder_that_is_not_empty_stays_and_says_so(tmp_path: Path, over_ftp: bool) -> None:
    write(tmp_path / "full" / "a.txt", "a")
    if over_ftp:
        with ftp_server(tmp_path) as port:
            connection: RemoteConnection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
            connection.connect()
            with pytest.raises(RemoteActionFailed, match=NOT_EMPTY_TEXT):
                connection.remove_empty_folder("/full")
            connection.close()
    else:
        with ssh_server(tmp_path) as running:
            ssh: SshConnection = trusted_ssh(running)
            with pytest.raises(RemoteActionFailed, match=NOT_EMPTY_TEXT):
                ssh.remove_empty_folder("/full")
            ssh.close()
    assert (tmp_path / "full" / "a.txt").exists()


def test_counting_an_upload_counts_what_it_sends_and_skips_what_is_gone(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    write(tree / "real.txt", "12345")
    (tree / "link.txt").symlink_to("real.txt")  # sent as the file it leads to
    (tree / "gone.txt").symlink_to("not-there.txt")  # leads nowhere: nothing to count, no failure
    (tree / "folder-link").symlink_to(tmp_path)  # a link to a folder is not followed
    assert local_size(tree) == 10
    assert local_size(tree / "link.txt") == 5 and local_size(tree / "gone.txt") == 0


def test_the_notes_say_one_and_more_in_words() -> None:
    assert note_text("kept_not_removed", 1).startswith("1 original arrived whole, but could not be removed, so it stayed")
    assert note_text("kept_not_removed", 2).startswith("2 originals arrived whole, but could not be removed, so they stayed")


SMALL_BLOCK: int = 64 * 1024  # a server that announces small reads and writes, as OpenSSH does: many requests travel at once


def small_requests(patch: pytest.MonkeyPatch) -> None:
    small = asyncssh.SFTPLimits(0, SMALL_BLOCK, SMALL_BLOCK, 0)

    def announced(_client: asyncssh.SFTPClient) -> asyncssh.SFTPLimits:
        return small

    patch.setattr(asyncssh.SFTPClient, "limits", property(announced))


@pytest.mark.parametrize("upload", [True, False])
def test_a_request_that_fails_out_of_order_never_leaves_a_gap_in_a_kept_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    source, target = sources_of(here, upload, root)
    content = os.urandom(16 * SMALL_BLOCK)
    (source / "big.bin").write_bytes(content)
    part = target / ("big.bin" + PART_SUFFIX)
    method = "write" if upload else "read"
    real = getattr(asyncssh.SFTPClientFile, method)
    calls: list[int] = []
    later_done: list[int] = []  # the requests after the failing one that ended first: the gap had bytes after it
    signal: list[asyncio.Event] = []

    async def third_fails_after_a_later_one(self: asyncssh.SFTPClientFile, *args: object) -> object:
        if not signal:
            signal.append(asyncio.Event())  # made on asyncssh's own loop
        calls.append(1)
        number = len(calls)
        if number == 3:  # the third request waits until a later one has ended, and then fails
            await asyncio.wait_for(signal[0].wait(), WAIT_SECONDS)
            raise asyncssh.SFTPConnectionLost("The line dropped for this one request.")
        result = await real(self, *args)
        if number > 3:
            later_done.append(number)
            signal[0].set()
        return result

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served = (connection, connection.connection, root)
        options = TransferOptions(resume=RESUME_ON)
        with monkeypatch.context() as patch:
            small_requests(patch)
            patch.setattr(asyncssh.SFTPClientFile, method, third_fails_after_a_later_one)
            failed = up(served, here / "big.bin", options) if upload else down(served, "/big.bin", here, options)
        assert failed.state == State.FAILED
        assert later_done  # a later block really was written: the gap existed
        assert part.read_bytes() == content[: 2 * SMALL_BLOCK]  # cut to the bytes with no gap before them, nothing after
        again = up(served, here / "big.bin", options) if upload else down(served, "/big.bin", here, options)
        connection.close()
    assert again.state == State.DONE, again.message
    assert (target / "big.bin").read_bytes() == content


def test_a_download_whose_source_ends_early_is_never_put_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "big.bin").write_bytes(os.urandom(16 * SMALL_BLOCK))
    real = asyncssh.SFTPClientFile.read
    calls: list[int] = []

    async def third_ends_early(self: asyncssh.SFTPClientFile, size: int = -1, offset: int | None = None) -> bytes:
        calls.append(1)
        if len(calls) == 3:
            return b""  # the source ended here, while the requests after it still bring bytes
        return cast(bytes, await real(self, size, offset))

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            small_requests(patch)
            patch.setattr(asyncssh.SFTPClientFile, "read", third_ends_early)
            job = down((connection, connection.connection, root), "/big.bin", here, TransferOptions(resume=RESUME_ON))
        connection.close()
    assert job.state == State.FAILED and job.message == SHORT_TEXT
    assert list(here.iterdir()) == []  # neither the file with a gap nor its part


# --- the notes on this computer, and what is told


@pytest.mark.skipif(not os.path.exists("/proc/locks"), reason="the system says who waits for a lock in /proc/locks only on Linux")
def test_the_notes_wait_for_another_chrono_ftp_that_is_changing_them(tmp_path: Path) -> None:
    """Another process holds the lock: a change here waits for it, so neither change is lost. The other process sees the change wait in
    the system's own list of locks, so no clock decides."""
    folder = config_folder()
    folder.mkdir(parents=True, exist_ok=True)
    login = Connection(name="T", host="h", user="u")
    changed = threading.Thread(target=lambda: parts.remember_append(login, "/a", 5, 9))
    with child(HOLDER, [str(folder / parts.LOCK_FILE_NAME)]) as holder:  # when the block ends, the other one lets go and ends
        holder.says(b"locked\n")
        changed.start()
        holder.says(b"waited for\n")  # the change here waits for the lock
        assert changed.is_alive() and not (folder / parts.APPENDS_FILE_NAME).exists()  # and has stored nothing yet
    changed.join(WAIT_SECONDS)
    assert not changed.is_alive()
    parts.append_ended(login, "/a")
    assert [(path, note.start, note.most) for path, note in parts.broken_appends(login).items()] == [("/a", 5, 9)]


HOLDER: str = """import fcntl, os, sys, time
with open(sys.argv[1], "a") as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    print("locked", flush=True)
    mark = ":" + str(os.fstat(lock.fileno()).st_ino) + " "
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with open("/proc/locks") as locks:
            if any("->" in line and mark in line for line in locks):
                print("waited for", flush=True)
                break
        time.sleep(0.01)
    else:
        print("nobody waited", flush=True)
    sys.stdin.read()
"""


def test_a_note_is_on_the_disk_with_its_folder_before_it_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    synced: list[str] = []
    real_fsync = os.fsync

    def noted(descriptor: int) -> None:
        synced.append(os.readlink(f"/proc/self/fd/{descriptor}"))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", noted)
    assert parts.remember_append(Connection(name="T", host="h", user="u"), "/a", 5, 9)
    folder = str(config_folder())
    assert synced == [os.path.join(folder, parts.APPENDS_FILE_NAME + ".tmp"), folder]


def test_what_a_set_back_tells_has_a_form_for_one_and_for_more() -> None:
    assert note_text("set back", 1, APPEND_NOTES) == "1 file that an append left half done has its old length back."
    assert note_text("set back", 2, APPEND_NOTES) == "2 files that an append left half done have their old length back."
    assert note_text("left", 3, APPEND_NOTES) == "3 files that an append left half done were changed since by something else, so they were left as they are; they may still hold part of that append."
    assert not any("(s)" in text for table in (NOTES, APPEND_NOTES) for pair in table.values() for text in pair)


def test_a_move_whose_copy_can_not_be_confirmed_on_the_servers_disk_keeps_its_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The line broke while the server put the file on its disk: whether it is there safely is unknown, so the original stays, and the
    text says just that."""
    root = tmp_path / "server"
    root.mkdir()
    write(tmp_path / "a.txt", "a")

    def no_answer(self: SshConnection, path: str) -> None:
        raise ConnectionLost("The connection to the server was lost.")

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        monkeypatch.setattr(SshConnection, "make_durable", no_answer)
        job = up((connection, connection.connection, root), tmp_path / "a.txt", TransferOptions(delete_source=True))
        connection.close()
    assert job.state == State.DONE and job.message == f"Done. {note_text('kept_unchecked', 1)}"
    assert read(tmp_path / "a.txt") == "a" and read(root / "a.txt") == "a"


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_whose_note_can_not_be_stored_is_not_kept_and_the_text_says_the_next_try_starts_afresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    (source / "big.bin").write_bytes(os.urandom(BIG))
    def not_stored(key: str, record: PartRecord) -> bool:
        return False  # this computer's settings folder takes nothing

    monkeypatch.setattr(parts, "remember", not_stored)
    with ftp_server(root, drops_data_after=BIG // 4) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        options = TransferOptions(resume=RESUME_ON)
        broken = up((connection, settings, root), here / "big.bin", options) if upload else down((connection, settings, root), "/big.bin", here, options)
        connection.close()
    assert broken.state == State.FAILED and broken.message == f"{INTERRUPTED_TEXT} {PART_GONE_TEXT}"
    assert not (target / ("big.bin" + PART_SUFFIX)).exists()  # without its note it would be nobody's: it goes


def test_an_old_file_whose_note_can_not_be_stored_is_never_moved_aside(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = server[2]
    write(root / "a.txt", "old")
    write(tmp_path / "a.txt", "new")
    def not_stored(login: Connection, aside: str, target: str, size: int) -> bool:
        return False

    monkeypatch.setattr(parts, "remember_old", not_stored)
    job = up(server, tmp_path / "a.txt", TransferOptions(ask_before_replacing=False))
    assert job.state == State.FAILED and job.message == OLD_NOT_NOTED_TEXT.format(name="a.txt"), job.message
    assert sorted(path.name for path in root.iterdir()) == ["a.txt"] and read(root / "a.txt") == "old"


def test_a_replaced_file_whose_old_copy_can_not_be_removed_is_done_and_says_where_the_old_one_is(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    write(root / "a.txt", "old")
    write(tmp_path / "a.txt", "new")
    kind = type(connection)
    real_delete = kind.delete

    def keeps_the_old_one(self: RemoteConnection, path: str, is_folder: bool) -> None:
        if path.endswith(OLD_SUFFIX):
            raise RemoteActionFailed("Not allowed.")
        real_delete(self, path, is_folder)

    with monkeypatch.context() as patch:
        patch.setattr(kind, "delete", keeps_the_old_one)
        job = up(server, tmp_path / "a.txt", TransferOptions(ask_before_replacing=False))
    assert job.state == State.DONE and job.message == f"Done. {note_text('kept_aside', 1)}", job.message
    assert read(root / "a.txt") == "new" and read(root / ("a.txt" + OLD_SUFFIX)) == "old"
    assert parts.old_target(server[1], "/a.txt" + OLD_SUFFIX, 3) == "/a.txt"  # still ours: tidying removes it next to the new file


def test_an_append_that_fails_out_of_order_is_set_back_also_at_its_full_length(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The third request fails only after the last one was written: the file has its full length with a gap in it. The append broke
    off just now, so it is set back to its old length, never left as if it went whole."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    original = b"the original, as it was before\n"
    (root / "log.bin").write_bytes(original)
    content = os.urandom(8 * SMALL_BLOCK)
    (here / "log.bin").write_bytes(content)
    real = asyncssh.SFTPClientFile.write
    last = len(original) + 7 * SMALL_BLOCK  # where the last request writes
    calls: list[int] = []
    signal: list[asyncio.Event] = []

    async def third_fails_after_the_last(self: asyncssh.SFTPClientFile, data: bytes, offset: int | None = None) -> int:
        if not signal:
            signal.append(asyncio.Event())  # made on asyncssh's own loop
        calls.append(1)
        if len(calls) == 3:
            await asyncio.wait_for(signal[0].wait(), WAIT_SECONDS)
            raise asyncssh.SFTPFailure("The server's disk failed for this one request.")
        result = await real(self, data, offset)
        if offset == last:
            signal[0].set()
        return result

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            small_requests(patch)
            patch.setattr(asyncssh.SFTPClientFile, "write", third_fails_after_the_last)
            job = up((connection, connection.connection, root), here / "log.bin", person=Person(Answer(Choice.APPEND)))
        connection.close()
    assert signal and signal[0].is_set()  # the last block really was written: the file had its full length, with a gap
    assert job.state == State.FAILED
    assert (root / "log.bin").read_bytes() == original
    assert parts.broken_appends(connection.connection) == {}


def test_a_swap_that_fails_before_the_old_file_moved_leaves_no_note_of_it(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, settings, root = server
    write(root / "a.txt", "old")
    write(tmp_path / "a.txt", "new")
    kind = type(connection)
    real_rename = kind.rename

    def refuses_to_move_the_old_one(self: RemoteConnection, old: str, new: str) -> None:
        if new.endswith(OLD_SUFFIX):
            raise RemoteActionFailed("Not allowed.")
        real_rename(self, old, new)

    with monkeypatch.context() as patch:
        patch.setattr(kind, "rename", refuses_to_move_the_old_one)
        job = up(server, tmp_path / "a.txt", TransferOptions(ask_before_replacing=False))
    assert job.state == State.FAILED
    assert read(root / "a.txt") == "old" and sorted(path.name for path in root.iterdir()) == ["a.txt"]
    assert parts.old_target(settings, "/a.txt" + OLD_SUFFIX, -1) is None  # a real a.txt.chrono-ftp-old made later is never taken for ours


def test_half_a_line_at_the_end_of_the_journal_never_swallows_the_next_note() -> None:
    """The computer stopped while a line was written: the next note starts on a line of its own, and the half line is left out."""
    login = Connection(name="T", host="h", user="u")
    assert parts.remember_old(login, "/srv/a.txt.chrono-ftp-old", "/srv/a.txt", 3)
    journal = config_folder() / parts.OLDS_FILE_NAME
    with journal.open("a", encoding="utf-8") as file:
        file.write('["+", "half a li')  # cut off
    assert parts.remember_old(login, "/srv/b.txt.chrono-ftp-old", "/srv/b.txt", 4)
    parts._olds_cache = None  # read from the disk, as another Chrono FTP would
    assert parts.old_target(login, "/srv/a.txt.chrono-ftp-old", 3) == "/srv/a.txt"
    assert parts.old_target(login, "/srv/b.txt.chrono-ftp-old", 4) == "/srv/b.txt"


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_that_took_its_real_name_never_gets_a_note(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    """Something fails after the copy took its real name: no note is ever stored for the temporary name, which holds nothing of ours."""
    connection, settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    write(source / "a.txt", "new")
    write(target / "a.txt", "old")

    def fails_after_placing(*args: object) -> bool:
        raise OSError(5, "Input/output error")

    with monkeypatch.context() as patch:
        if upload:
            patch.setattr(parts, "forget_old", fails_after_placing)  # right after the swap
        else:
            patch.setattr(filebyfile_module, "durable", fails_after_placing)  # right after the copy took its name
        options = TransferOptions(resume=RESUME_ON, ask_before_replacing=False, delete_source=not upload)
        job = up(server, here / "a.txt", options) if upload else down(server, "/a.txt", here, options)
    assert job.state == State.FAILED
    assert read(target / "a.txt") == "new"
    key = upload_key(settings, "/a.txt" + PART_SUFFIX) if upload else download_key(os.path.realpath(here / "a.txt") + PART_SUFFIX)
    assert parts.lookup(key) is None
    assert not (target / ("a.txt" + PART_SUFFIX)).exists()


def test_an_ftp_upload_move_says_its_originals_were_removed_after_a_check_of_size_and_date_only(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    write(tmp_path / "a.txt", "a")
    with ftp_server(root) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        assert not ftp.capabilities.durable  # FTP can't be asked to put a file on its disk
        job = up((ftp, settings, root), tmp_path / "a.txt", TransferOptions(delete_source=True))
        ftp.close()
    assert job.state == State.DONE and job.message == f"Done. {note_text('removed_unflushed', 1)}", job.message
    assert "size" in note_text("removed_unflushed", 1) and "date" not in note_text("removed_unflushed", 1)  # only what was checked
    assert read(root / "a.txt") == "a" and not (tmp_path / "a.txt").exists()


def test_a_part_that_can_not_be_removed_stays_ours_and_tidying_removes_it_later(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The transfer failed and its part could not be removed: its note says it is ours, with nothing to continue from."""
    connection, settings, root = server
    write(tmp_path / "a.txt", "content")
    kind = type(connection)
    real_delete = kind.delete

    def keeps_parts(self: RemoteConnection, path: str, is_folder: bool) -> None:
        if path.endswith(PART_SUFFIX):
            raise RemoteActionFailed("Not allowed.")
        real_delete(self, path, is_folder)

    def refused(self: RemoteConnection, path: str, mode: str) -> None:
        raise RemoteActionFailed("The server refused the rights.")

    with monkeypatch.context() as patch:
        patch.setattr(kind, "delete", keeps_parts)
        patch.setattr(kind, "set_rights", refused)
        job = up(server, tmp_path / "a.txt", TransferOptions(resume=RESUME_OFF, set_rights=True, ignore_rights_errors=False))
    assert job.state == State.FAILED
    part = "/a.txt" + PART_SUFFIX
    note = parts.lookup(upload_key(settings, part))
    assert note is not None and note.part_size == 0 and not note.running and (root / ("a.txt" + PART_SUFFIX)).exists()
    os.utime(root / ("a.txt" + PART_SUFFIX), (OLD_TIME, OLD_TIME))
    entries = connection.list_folder("/")
    tidy(connection, "/", entries, {f"/{entry.name}": (time.time() - 3600, entry.size) for entry in entries}, Owned(settings, TransferOptions()))
    assert not (root / ("a.txt" + PART_SUFFIX)).exists() and parts.lookup(upload_key(settings, part)) is None


def test_a_part_kept_by_resume_always_continues_although_settings_say_smart(tmp_path: Path) -> None:
    """The text said the next try continues: it does, judged by the setting the part was kept by, as tidying judges it. Over SFTP, which
    stops between its small blocks."""
    root = tmp_path / "server"
    root.mkdir()
    content = os.urandom(SMART_SMALL)  # under the size Smart keeps a part for
    (tmp_path / "small.bin").write_bytes(content)
    part = root / ("small.bin" + PART_SUFFIX)
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served = (connection, connection.connection, root)
        stopped = stopped_halfway(served, tmp_path / "small.bin", "/", True, "small.bin", TransferOptions(resume=RESUME_ON), part, limit=SMALL_LIMIT)
        assert stopped.state == State.STOPPED and part.exists()
        part.write_bytes(b"X" + part.read_bytes()[1:])  # marked: only a continued transfer keeps the mark
        again = up(served, tmp_path / "small.bin", TransferOptions())  # Settings: Smart
        connection.close()
    assert again.state == State.DONE, again.message
    assert (root / "small.bin").read_bytes() == b"X" + content[1:]


def test_a_source_that_vanishes_here_is_told_in_this_computers_words(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "a.txt", "a")
    real_stat = Path.stat
    seen: list[bool] = []

    def gone_at_the_second_look(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name == "a.txt" and self.parent == tmp_path:
            seen.append(True)
            if len(seen) > 1:  # the first look found it; then it went
                raise FileNotFoundError(2, "No such file or directory")
        return real_stat(self, follow_symlinks=follow_symlinks)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "stat", gone_at_the_second_look)
        job = up(server, tmp_path / "a.txt", TransferOptions())
    assert job.state == State.FAILED and job.message == LOCAL_READ_TEXT.format(reason="No such file or directory"), job.message


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_is_noted_as_ours_before_its_first_byte(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    """Were this Chrono FTP to stop at once while the part is written, its note is already there: the part is never nobody's."""
    connection, settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    source = here if upload else root
    write(source / "a.txt", "content")
    kind = type(connection)
    method = "upload" if upload else "download"
    real = getattr(kind, method)
    seen: list[PartRecord | None] = []

    def looks_first(self: RemoteConnection, first: str, second: str, *rest: object) -> None:
        temporary = second
        seen.append(parts.lookup(upload_key(settings, temporary) if upload else download_key(temporary)))
        real(self, first, second, *rest)

    with monkeypatch.context() as patch:
        patch.setattr(kind, method, looks_first)
        job = up(server, here / "a.txt", TransferOptions()) if upload else down(server, "/a.txt", here, TransferOptions())
    assert job.state == State.DONE, job.message
    assert len(seen) == 1 and seen[0] is not None and seen[0].running and seen[0].owner == parts.OWNER
    assert parts.lookup(upload_key(settings, "/a.txt" + PART_SUFFIX) if upload else download_key(str(here / "a.txt") + PART_SUFFIX)) is None  # gone once it arrived


def test_a_problem_of_this_computer_that_reaches_the_queue_is_told_in_its_words(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "a.txt", "a")

    def this_computer_says_no(way: FileWay) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(FileWay, "run", this_computer_says_no)
    job = up(server, tmp_path / "a.txt", TransferOptions())
    assert job.state == State.FAILED and job.message == LOCAL_READ_TEXT.format(reason="Permission denied"), job.message


def test_a_failure_while_writing_is_never_a_taken_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The third write fails: that is the server's refusal of this file, told as it is. One temporary file was made, for this one try,
    and nothing stays behind that is not ours by its note."""
    root = tmp_path / "server"
    root.mkdir()
    (tmp_path / "big.bin").write_bytes(os.urandom(4 * SMALL_BLOCK))
    real_write = asyncssh.SFTPClientFile.write
    real_open = asyncssh.SFTPClient.open
    writes: list[int] = []
    made: list[str] = []

    async def third_fails(self: asyncssh.SFTPClientFile, data: bytes, offset: int | None = None) -> int:
        writes.append(1)
        if len(writes) == 3:
            raise asyncssh.SFTPFailure("The disk said no.")
        return await real_write(self, data, offset)

    async def counted_open(self: asyncssh.SFTPClient, path: object, pflags_or_mode: object = "r", *args: object, **kwargs: object) -> asyncssh.SFTPClientFile:
        if isinstance(pflags_or_mode, str) and "x" in pflags_or_mode:
            made.append(str(path))
        return await real_open(self, path, pflags_or_mode, *args, **kwargs)

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            small_requests(patch)
            patch.setattr(asyncssh.SFTPClientFile, "write", third_fails)
            patch.setattr(asyncssh.SFTPClient, "open", counted_open)
            job = up((connection, connection.connection, root), tmp_path / "big.bin", TransferOptions())
        connection.close()
    said = STOPPED_TAKING_TEXT.format(reply="The disk said no.")
    assert job.state == State.FAILED and job.message.startswith(said), job.message  # no claim of a reason it does not know
    assert made == ["/big.bin" + PART_SUFFIX]  # one temporary, never the next name and the next
    leftovers = [path.name for path in root.iterdir()]
    assert all(parts.lookup(upload_key(connection.connection, f"/{name}")) is not None for name in leftovers), leftovers


@NOT_AS_ROOT
def test_a_refused_upload_leaves_no_note_and_a_kept_part_keeps_its_own(server: Server, tmp_path: Path) -> None:
    connection, settings, root = server
    (root / "closed").mkdir()
    kept = upload_key(settings, "/kept.bin" + PART_SUFFIX)
    parts.remember(kept, PartRecord('["here", "/elsewhere"]', 10, 1.0, 5))
    for number in range(20):
        write(tmp_path / f"f{number}.txt", "x")
    (root / "closed").chmod(0o555)  # takes no file
    try:
        jobs = [up(server, tmp_path / f"f{number}.txt", TransferOptions(), folder="/closed") for number in range(20)]
    finally:
        (root / "closed").chmod(0o755)
    assert all(job.state == State.FAILED for job in jobs)
    stored = json.loads((config_folder() / parts.FILE_NAME).read_text(encoding="utf-8"))
    assert list(stored) == [kept]  # no note for a temporary that never was; the real one stays


def test_a_file_that_arrives_costs_its_notes_no_wait_for_the_disk(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The note of a part is written before its first byte and goes when the file arrived, both without waiting for the disk: every file
    would pay for it. Only a kept part's note waits for it."""
    write(tmp_path / "a.txt", "a")
    synced: list[str] = []
    real_fsync = os.fsync
    settings_folder = str(config_folder())

    def noted(descriptor: int) -> None:
        path = os.readlink(f"/proc/self/fd/{descriptor}") if os.path.exists("/proc/self/fd") else ""
        if path.startswith(settings_folder):
            synced.append(path)
        real_fsync(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", noted)
        job = up(server, tmp_path / "a.txt", TransferOptions())
    assert job.state == State.DONE, job.message
    assert synced == []


def test_an_old_part_that_can_not_be_removed_stays_ours_and_is_told(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A part of ours that can't be continued (another source) and can't be removed: its note stays, with nothing to continue from,
    the new copy goes under the next free name, and the queue says so."""
    connection, settings, root = server
    write(root / ("a.txt" + PART_SUFFIX), "bytes of another file")
    key = upload_key(settings, "/a.txt" + PART_SUFFIX)
    parts.remember(key, PartRecord('["here", "/somewhere/else.txt"]', 100, 1.0, 21))
    write(tmp_path / "a.txt", "mine")
    kind = type(connection)
    real_delete = kind.delete

    def keeps_the_old_part(self: RemoteConnection, path: str, is_folder: bool) -> None:
        if path == "/a.txt" + PART_SUFFIX:
            raise RemoteActionFailed("Not allowed.")
        real_delete(self, path, is_folder)

    with monkeypatch.context() as patch:
        patch.setattr(kind, "delete", keeps_the_old_part)
        job = up(server, tmp_path / "a.txt", TransferOptions())
    assert job.state == State.DONE and job.message == f"Done. {note_text('parts_left', 1)}", job.message
    assert read(root / "a.txt") == "mine" and read(root / ("a.txt" + PART_SUFFIX)) == "bytes of another file"
    note = parts.lookup(key)
    assert note is not None and note.part_size == 0 and not note.running  # ours, nothing to continue from: tidying removes it later


def test_a_part_here_that_vanished_before_it_was_continued_is_never_said_to_be_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    content = os.urandom(BIG)
    (root / "big.bin").write_bytes(content)
    os.utime(root / "big.bin", (OLD_TIME, OLD_TIME))  # a date the listing tells exactly
    part = here / ("big.bin" + PART_SUFFIX)
    part.write_bytes(content[:1000])
    key = download_key(str(part))
    real = FtpConnection.download

    def gone_first(self: FtpConnection, remote: str, local: str, progress: Callable[[int], None], limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        Path(local).unlink()  # something removed it just before it was opened
        real(self, remote, local, progress, limit, offset, append, exclusive)

    with ftp_server(root) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        parts.remember(key, PartRecord(download_source(settings, "/big.bin"), BIG, OLD_TIME, 1000, RESUME_ON))
        with monkeypatch.context() as patch:
            patch.setattr(FtpConnection, "download", gone_first)
            job = down((ftp, settings, root), "/big.bin", here, TransferOptions(resume=RESUME_ON))
        ftp.close()
    assert job.state == State.FAILED and job.message.endswith(PART_GONE_TEXT), job.message
    assert parts.lookup(key) is None and not part.exists()


def test_a_part_the_server_wont_cut_is_a_refused_continuation_and_starts_again_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    content = os.urandom(BIG)
    (tmp_path / "big.bin").write_bytes(content)
    part = root / ("big.bin" + PART_SUFFIX)

    async def refused(self: asyncssh.SFTPClientFile, size: int | None = None) -> None:
        raise asyncssh.SFTPOpUnsupported("Not supported.")

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served = (connection, connection.connection, root)
        options = TransferOptions(resume=RESUME_ON)
        assert stopped_halfway(served, tmp_path / "big.bin", "/", True, "big.bin", options, part).state == State.STOPPED
        with monkeypatch.context() as patch:
            patch.setattr(asyncssh.SFTPClientFile, "truncate", refused)
            again = up(served, tmp_path / "big.bin", options)
        connection.close()
    assert again.state == State.DONE and again.message == f"Done. {note_text('restarted', 1)}", again.message
    assert (root / "big.bin").read_bytes() == content and not part.exists()


def test_an_ftp_part_that_is_not_as_it_was_left_is_never_continued(tmp_path: Path) -> None:
    """While the person thinks, the part on the server shrinks: REST would write after a gap. It starts again from the beginning."""
    root = tmp_path / "server"
    root.mkdir()
    write(root / "a.txt", "old")
    content = b"0123456789" * 20_000
    (tmp_path / "a.txt").write_bytes(content)
    part = root / ("a.txt" + PART_SUFFIX)
    part.write_bytes(content[:150_000])
    details = (tmp_path / "a.txt").stat()

    class ShrinksMeanwhile(Person):
        @override
        def __call__(self, job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
            part.write_bytes(content[:10])
            super().__call__(job, conflict, reply)

    heard: list[str] = []
    with ftp_server(root, command_heard=heard.append) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        parts.remember(upload_key(settings, "/a.txt" + PART_SUFFIX), PartRecord(upload_source(str(tmp_path / "a.txt")), len(content), details.st_mtime, 150_000, RESUME_ON))
        job = up((ftp, settings, root), tmp_path / "a.txt", TransferOptions(resume=RESUME_ON), ShrinksMeanwhile(Answer(Choice.RESUME)))
        ftp.close()
    assert "REST" not in heard  # never asked to continue a part that is not as it was left, whatever the server would do with it
    assert job.state == State.DONE and job.message == f"Done. {note_text('restarted', 1)}", job.message
    assert (root / "a.txt").read_bytes() == content and not part.exists()


def test_counting_first_stops_at_stop(tmp_path: Path) -> None:
    for number in range(5):
        write(tmp_path / "tree" / f"f{number}.txt", "x")
    stop = threading.Event()
    stop.set()
    with pytest.raises(Stopped):
        local_size(tmp_path / "tree", stop)
    assert local_size(tmp_path / "tree") == 5


def test_a_server_that_lies_about_a_size_never_keeps_a_download_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """11 bytes said to be 10^13: the first short read is the end of the source, and nothing more is asked for."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "liar.bin").write_bytes(b"eleven byte")
    real_stat = asyncssh.SFTPClientFile.stat

    async def huge(self: asyncssh.SFTPClientFile) -> asyncssh.SFTPAttrs:
        attributes = await real_stat(self)
        attributes.size = 10**13
        return attributes

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with monkeypatch.context() as patch:
            patch.setattr(asyncssh.SFTPClientFile, "stat", huge)
            job = down((connection, connection.connection, root), "/liar.bin", here, TransferOptions())
        connection.close()
    assert job.state == State.FAILED and SHORT_TEXT in job.message, job.message
    assert not (here / "liar.bin").exists()


def test_stop_is_seen_when_requests_end_also_while_the_bytes_in_order_do_not_grow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first request never ends; later ones do. The bytes in order never grow, and Stop still ends the download."""
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (root / "big.bin").write_bytes(os.urandom(16 * SMALL_BLOCK))
    real_read = asyncssh.SFTPClientFile.read
    later: list[int] = []

    async def first_never_ends(self: asyncssh.SFTPClientFile, size: int = -1, offset: int | None = None) -> bytes:
        if offset == 0:
            await asyncio.Event().wait()  # never set: this request hangs until it is cancelled
        later.append(1)
        return cast(bytes, await real_read(self, size, offset))

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        settings = connection.connection
        with monkeypatch.context() as patch:
            small_requests(patch)
            patch.setattr(asyncssh.SFTPClientFile, "read", first_never_ends)
            queue = TransferQueue(lambda item: None)
            job = Job(connection, settings, str(here), "/big.bin", False, "big.bin", has_password=True, options=TransferOptions(resume=RESUME_OFF))
            try:
                queue.add(job)
                deadline = time.monotonic() + WAIT_SECONDS
                while len(later) < 3:
                    assert time.monotonic() < deadline, "no later request ended"
                    threading.Event().wait(0.01)
                queue.stop(job)
                finished = threading.Event()
                threading.Thread(target=lambda: wait_for((job,), finished), daemon=True).start()
                assert finished.wait(WAIT_SECONDS), "Stop was not seen"
            finally:
                queue.close()
        connection.close()
    assert job.state == State.STOPPED


def test_numbers_no_float_holds_in_the_note_files_never_make_reading_fail(tmp_path: Path) -> None:
    """A damaged note file with numbers of 400 digits: those notes are left out, also from the file, and every other note still reads."""
    huge = int("9" * 400)
    login = Connection(name="T", host="h", user="u")
    folder = config_folder()
    folder.mkdir(parents=True, exist_ok=True)
    good_part, bad_part = upload_key(login, "/good" + PART_SUFFIX), upload_key(login, "/bad" + PART_SUFFIX)
    (folder / parts.FILE_NAME).write_text(json.dumps({
        bad_part: {"source": "x", "size": 5, "modified": huge, "part_size": 1},
        good_part: {"source": "x", "size": 5, "modified": 1.0, "part_size": 1},
    }), encoding="utf-8")
    good_append, bad_append = parts.append_key(login, "/good.log"), parts.append_key(login, "/bad.log")
    (folder / parts.APPENDS_FILE_NAME).write_text(json.dumps({
        bad_append: [1, 2, "gone-owner", parts.BROKEN, 1, huge],
        good_append: [1, 2, "gone-owner", parts.RUNNING, -1, None],
    }), encoding="utf-8")
    (folder / parts.OLDS_FILE_NAME).write_text(json.dumps(["+", parts.append_key(login, "/a.old"), json.dumps(["/a", huge])]) + "\n", encoding="utf-8")
    (folder / parts.PARTIALS_FILE_NAME).write_text(json.dumps({"x": [huge, "y", "on"]}), encoding="utf-8")
    assert parts.lookup(bad_part) is None and parts.lookup(good_part) is not None
    assert list(parts.broken_appends(login)) == ["/good.log"]
    assert parts.old_target(login, "/a.old", 5) is None  # a size no file has: never ours
    assert parts.partial_note(login, parts.UP, "/") is None
    assert list(json.loads((folder / parts.FILE_NAME).read_text(encoding="utf-8"))) == [good_part]  # the file without it
    assert list(json.loads((folder / parts.APPENDS_FILE_NAME).read_text(encoding="utf-8"))) == [good_append]


def test_a_continued_ftp_upload_that_did_not_fit_is_told_only_as_started_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    content = b"0123456789" * 20_000
    (tmp_path / "a.txt").write_bytes(content)
    (root / ("a.txt" + PART_SUFFIX)).write_bytes(content[:150_000])
    details = (tmp_path / "a.txt").stat()
    real_size = FtpConnection.file_size
    lied: list[bool] = []

    def wrong_once(self: FtpConnection, path: str) -> int | None:
        size = real_size(self, path)
        if path.endswith(PART_SUFFIX) and size == len(content) and not lied:
            lied.append(True)
            return len(content) + 1  # the continued copy does not fit: it starts again from the beginning
        return size

    with ftp_server(root) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        parts.remember(upload_key(settings, "/a.txt" + PART_SUFFIX), PartRecord(upload_source(str(tmp_path / "a.txt")), len(content), details.st_mtime, 150_000, RESUME_ON))
        with monkeypatch.context() as patch:
            patch.setattr(FtpConnection, "file_size", wrong_once)
            job = up((ftp, settings, root), tmp_path / "a.txt", TransferOptions(resume=RESUME_ON))
        ftp.close()
    assert lied and job.state == State.DONE and job.message == f"Done. {note_text('restarted', 1)}", job.message


def test_an_edit_save_after_a_broken_off_save_of_a_large_file_never_continues_its_old_part(tmp_path: Path) -> None:
    """The first save of a large edited copy was broken off and its part kept; the person saved again (other bytes, same size, a later
    date). The note names the copy as it was: the part no longer fits it, so the new save starts from the beginning."""
    root = tmp_path / "server"
    root.mkdir()
    copy = tmp_path / "edit" / "big.bin"
    copy.parent.mkdir()
    copy.write_bytes(b"A" * BIG)
    os.utime(copy, (OLD_TIME, OLD_TIME))
    part = root / ("big.bin" + PART_SUFFIX)
    as_an_edit_saves = TransferOptions(ask_before_replacing=False, only_newer=False, set_rights=False)  # what an Edit save sends with
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served = (connection, connection.connection, root)
        assert stopped_halfway(served, copy, "/", True, "big.bin", as_an_edit_saves, part).state == State.STOPPED
        assert part.exists() and parts.lookup(upload_key(connection.connection, "/big.bin" + PART_SUFFIX)) is not None
        copy.write_bytes(b"B" * BIG)  # saved again in the editor
        again = up(served, copy, as_an_edit_saves)
        connection.close()
    assert again.state == State.DONE, again.message
    assert (root / "big.bin").read_bytes() == b"B" * BIG  # never the old part's A at its start


def test_a_running_part_costs_the_kept_notes_no_rewrite() -> None:
    """A part being written is a line in a journal of its own: the file of kept notes, however many it holds, is not written again."""
    for number in range(50):
        parts.remember(f"kept-{number}", PartRecord('["here", "/x"]', 10, 1.0, 5))
    kept = config_folder() / parts.FILE_NAME
    before = (kept.stat().st_mtime_ns, kept.read_bytes())
    assert parts.claim("new", PartRecord('["here", "/y"]', 10, 1.0, 0))
    claimed = parts.lookup("new")
    assert claimed is not None and claimed.running and claimed.owner == parts.OWNER
    parts.forget("new")
    assert parts.lookup("new") is None
    assert (kept.stat().st_mtime_ns, kept.read_bytes()) == before


# --- a damaged note is input: it is left out, never trusted

GOOD_RECORD: dict[str, object] = {"source": "[\"here\", \"/a\"]", "size": 10, "modified": 1.0, "part_size": 5, "resume": "on", "owner": "me", "running": True}


@pytest.mark.parametrize("damage", [
    {"size": True}, {"size": -1}, {"size": parts.HIGHEST_SIZE + 1}, {"part_size": 11}, {"source": 5}, {"size": None}, {"part_size": None},
    {"modified": True}, {"modified": float("nan")}, {"modified": float("inf")}, {"modified": 10**400}, {"modified": None},
])
def test_a_part_note_with_a_damaged_field_is_left_out(damage: dict[str, object]) -> None:
    assert parts._record({**GOOD_RECORD, **damage}) is None
    assert parts._record(GOOD_RECORD) is not None


def test_a_size_in_a_note_is_a_whole_number_a_file_can_have() -> None:
    assert [parts._whole(value) for value in (True, -1, parts.HIGHEST_SIZE + 1, "5", 5.0)] == [None] * 5
    assert parts._whole(0) == 0 and parts._whole(parts.HIGHEST_SIZE) == parts.HIGHEST_SIZE


def test_a_part_note_takes_only_fields_of_their_own_type_and_only_a_mapping_is_a_note() -> None:
    odd = parts._record({**GOOD_RECORD, "resume": 5, "owner": 5, "running": "yes"})
    assert odd is not None and (odd.resume, odd.owner, odd.running) == ("", "", False)
    assert parts._record(["not", "a", "note"]) is None
    assert parts._records(["a", "list"]) == {} and parts._records({5: GOOD_RECORD}) == {}


GOOD_SPAN: list[object] = [5, 9, "me", parts.BROKEN, 7, 1.0]


@pytest.mark.parametrize("damage", [
    GOOD_SPAN[:5], [*GOOD_SPAN, "more"], [9, 5, "me", parts.BROKEN, 7, 1.0], [5, 9, 5, parts.BROKEN, 7, 1.0], [5, 9, "me", "lost", 7, 1.0],
    [5, 9, "me", parts.BROKEN, "x", 1.0], [5, 9, "me", parts.BROKEN, 7, float("nan")], "not a list",
])
def test_an_append_note_with_a_damaged_field_is_left_out(damage: object) -> None:
    assert parts._spans({"/log.bin": damage}) == {}
    assert list(parts._spans({"/log.bin": GOOD_SPAN})) == ["/log.bin"]


def test_append_notes_are_read_only_from_a_mapping_with_names() -> None:
    assert parts._spans([GOOD_SPAN]) == {} and parts._spans({5: GOOD_SPAN}) == {}


def test_only_the_newest_part_notes_are_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parts, "HIGHEST_RECORDS", 3)
    login = Connection(name="T", host="h", user="u")
    for number in range(5):
        assert parts.remember(upload_key(login, f"/f{number}{PART_SUFFIX}"), PartRecord(upload_source(f"/f{number}"), 10, 1.0, 5))
    kept = [number for number in range(5) if parts.lookup(upload_key(login, f"/f{number}{PART_SUFFIX}")) is not None]
    assert kept == [2, 3, 4]

