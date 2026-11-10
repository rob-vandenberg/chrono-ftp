"""What keeps a transfer safe (FB-02, FB-08, OR-15): an old part never becomes the start of a new file, links, one odd item in a folder,
replies that are input, the measure of progress, and what a connection says it can do."""

import hashlib
import os
import socket
import stat
import sys
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from collections.abc import Sequence
from typing import override

import asyncssh

import pytest

from chrono_ftp.config import parts
from chrono_ftp.connections.base import HASH_LENGTH, NAME_BYTES, OLD_SUFFIX, Swapped, own_name, CommandResult, RemoteConnection, RemoteEntry
from chrono_ftp.connections.ssh import SshConnection, _modes, _special
from chrono_ftp.connections.ftp import _rights_only
from chrono_ftp.connections.ftp import ODD_NAME_TEXT, FtpConnection, entry_from
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ConnectionFailed, LocalActionFailed, RemoteActionFailed
from chrono_ftp.core.transferoptions import RESUME_ON, TransferOptions
from chrono_ftp.transfers.filebyfile import NOT_A_FILE_HERE_TEXT, NOT_A_FILE_THERE_TEXT, NOT_A_FILE_DOWN_TEXT, LINK_NOWHERE_UP_TEXT, LINK_TO_FOLDER_DOWN_TEXT, LINK_TO_FOLDER_UP_TEXT, NOT_A_FILE_UP_TEXT, NOT_A_NAME_TEXT, UNCHECKABLE_NAME_TEXT, UNSEEN_LINK_TEXT, FileWay
from chrono_ftp.transfers.job import FIRST_ESTIMATE_SECONDS, PART_SUFFIX, Job, Meter, State
from chrono_ftp.transfers.queue import CHANGED_ASIDE_TEXT, Owned, note_text, source_check_command, tidy
from chrono_ftp.transfers.rsyncway import _remove_empty_sources
from tests.fakeservers import NOT_AS_ROOT, PASSWORD, USER, ftp_server, ssh_server
from tests.test_connections import trusted_ssh
from tests.test_engine import Scripted
from chrono_ftp.transfers.options import Action, Answer, Answers, Choice, Conflict
from chrono_ftp.transfers.filebyfile import UNREADABLE_THERE_TEXT, GONE_THERE_TEXT, NO_APPEND_TEXT, CHANGED_HERE_NOT_REPLACED_TEXT, APPEARED_HERE_TEXT, APPEARED_THERE_TEXT, CHANGED_HERE_REPLACE_TEXT, CHANGED_THERE_TEXT, CHANGED_TO_REPLACE_TEXT, KIND_UNKNOWN_TEXT, LOCAL_LINK_NOWHERE_TEXT, UNSAFE_NAME_TEXT
from tests.test_transfer_options import BIG, NEWER_TIME, OLD_TIME, Child, Person, child, stopped_halfway, Server, down, note_part, read, server, transfer, up, write

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_safety.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: a stale part and its note go first; links to files and links that can't be followed; one odd item; a garbled SIZE;
#          the meter, with a clock of its own; the capabilities of FTP and of SFTP without a shell; real files named like our part or
#          moved-aside files; a folder that can't be read asks before it replaces; a rename onto a name this job just sent; something of
#          unknown kind is never replaced; a subfolder that can't be read; a file that appears at the target meanwhile; a link listed with
#          its own size; a download read while its file was rewritten; one listing per folder over FTP; counting first past a folder that
#          can't be read; a local link that leads nowhere; a file rewritten during its fetch, seen by asking the server this moment; a file
#          replaced only while it is the one agreed to; a foreign temporary name; two senders; a part left by a stopped process; a name
#          a listing leaves out; a rename remembered only once done; a download that replaces only the file agreed to; a name a server
#          listing can't show is never sent; a move removes only folders on its list; the other process never outlives its test; a
#          picked link to a folder, broken link or pipe fails saying so, and those inside a folder are left out and counted; a pipe on
#          the server is never fetched, over SFTP and over FTP where the listing tells its kind, nor replaced by an upload; a unix.mode
#          with the kind gives the rights alone; a download never replaces a pipe here; a save in the very last moment is kept aside
#          and told; a name of our own fits next to a long name, and such a name is sent, replaced and continued; a move keeps a
#          source rewritten at its size with a new date; every look at whether a file is still the agreed one counts its date and kind;
#          the bounds of a name of our own; Append holds only for the file as the person saw it; folders keep no source date with New
#          dates; an SFTP size that is not given; a socket; rights with setuid, setgid and sticky; a part of the full length, and one
#          without bytes; a picked item no longer on the server, and one the server refuses to tell about

__all__ = ["server"]  # the fixture: SFTP and FTP

SOURCE_SIZE: int = 100_000


@pytest.mark.parametrize("upload", [True, False])
def test_an_old_part_is_never_the_start_of_a_new_source_even_when_the_next_try_is_refused(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    (target / ("big.bin" + PART_SUFFIX)).write_bytes(b"A" * (SOURCE_SIZE // 2))  # a part of the source as it was, with its note
    note_part(server, here, "big.bin", upload, SOURCE_SIZE, OLD_TIME, SOURCE_SIZE // 2)
    (source / "big.bin").write_bytes(b"B" * SOURCE_SIZE)  # the source changed since: same size, another date
    os.utime(source / "big.bin", (NEWER_TIME, NEWER_TIME))
    kind = type(connection)
    method = "upload" if upload else "download"
    real = getattr(kind, method)
    refused: list[bool] = []

    def refused_once(self: RemoteConnection, *args: object, **kwargs: object) -> None:
        if not refused:
            refused.append(True)
            raise RemoteActionFailed("Refused before the first byte.")
        real(self, *args, **kwargs)

    monkeypatch.setattr(kind, method, refused_once)
    options = TransferOptions(resume=RESUME_ON)
    first = up(server, here / "big.bin", options) if upload else down(server, "/big.bin", here, options)
    assert first.state == State.FAILED
    assert not (target / ("big.bin" + PART_SUFFIX)).exists()  # gone before anything was written
    again = up(server, here / "big.bin", options) if upload else down(server, "/big.bin", here, options)
    assert again.state == State.DONE, again.message
    assert (target / "big.bin").read_bytes() == b"B" * SOURCE_SIZE


def test_a_link_to_a_file_is_fetched_with_the_size_of_that_file(tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "tree").mkdir(parents=True)
    here.mkdir()
    for folder in (root, root / "tree"):
        (folder / "real.txt").write_bytes(b"r" * 500)
        (folder / "link.txt").symlink_to("real.txt")
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served = (connection, connection.connection, root)
        fetched = down(served, "/link.txt", here)
        moved = down(served, "/tree", here, TransferOptions(delete_source=True))
        connection.close()
    assert fetched.state == State.DONE, fetched.message
    assert (here / "link.txt").read_bytes() == b"r" * 500
    assert moved.state == State.DONE and moved.total == 1000, moved.message
    assert (here / "tree" / "link.txt").read_bytes() == b"r" * 500
    assert not (root / "tree").exists()  # the link went, and the file it led to, and then their emptied folder
    assert (root / "real.txt").exists() and (root / "link.txt").is_symlink()


def test_an_upload_never_writes_through_a_link_whose_target_can_not_be_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    write(root / "real.txt", "old")
    (root / "link.txt").symlink_to("real.txt")
    write(tmp_path / "link.txt", "new content")
    listing = FtpConnection.list_folder

    def links_shown(self: FtpConnection, path: str) -> list[RemoteEntry]:
        """Many FTP servers mark a link in their listing (os.unix=slink); the test server follows it instead."""
        return [replace(entry, is_link=entry.name == "link.txt") for entry in listing(self, path)]

    monkeypatch.setattr(FtpConnection, "list_folder", links_shown)
    with ftp_server(root) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        job = up((connection, settings, root), tmp_path / "link.txt", TransferOptions(ask_before_replacing=False))
        connection.close()
    assert job.state == State.FAILED and job.message == UNSEEN_LINK_TEXT.format(name="link.txt")
    assert read(root / "real.txt") == "old" and (root / "link.txt").is_symlink()
    assert sorted(path.name for path in root.iterdir()) == ["link.txt", "real.txt"]


def test_an_upload_over_a_link_that_leads_nowhere_yet_keeps_the_link(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "link.txt").symlink_to("later.txt")
    write(tmp_path / "link.txt", "new content")
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        job = up((connection, connection.connection, root), tmp_path / "link.txt", TransferOptions(ask_before_replacing=False))
        connection.close()
    assert job.state == State.DONE, job.message
    assert (root / "link.txt").is_symlink() and read(root / "later.txt") == "new content"


def test_one_name_ftp_can_not_carry_fails_alone_and_the_rest_of_the_folder_goes(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    for name in ("a.txt", "odd\nname.txt", "z.txt"):
        write(tmp_path / "tree" / name, name)
    with ftp_server(root) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        connection = FtpConnection(settings, PASSWORD)
        connection.connect()
        job = up((connection, settings, root), tmp_path / "tree")
        connection.close()
    assert job.state == State.FAILED
    assert job.message == f"Partly done: tree/odd?name.txt was left out. {ODD_NAME_TEXT}"
    assert sorted(path.name for path in (root / "tree").iterdir()) == ["a.txt", "z.txt"]


@pytest.mark.parametrize("reply", ["213 abc", "213 -5", "213"])
def test_a_size_reply_that_is_no_number_counts_as_unknown_and_the_listing_tells(tmp_path: Path, reply: str) -> None:
    write(tmp_path / "a.txt", "12345")
    with ftp_server(tmp_path, size_reply=reply) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        assert connection.file_size("/a.txt") == 5
        connection.close()


def test_a_refused_size_command_gives_the_size_of_the_listing(tmp_path: Path) -> None:
    write(tmp_path / "a.txt", "12345")
    with ftp_server(tmp_path, without=("SIZE",)) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        assert connection.file_size("/a.txt") == 5
        with pytest.raises(RemoteActionFailed):
            connection.file_size("/missing.txt")
        connection.close()


def test_a_line_that_drops_while_asking_what_the_server_can_do_is_a_normal_failure_to_connect(tmp_path: Path) -> None:
    with ftp_server(tmp_path, drops_at_feat=True) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        with pytest.raises(ConnectionFailed):
            connection.connect()
        connection.close()


@pytest.mark.parametrize("command, unable", [("REST", "resume"), ("MFMT", "timestamps"), ("SITE CHMOD", "rights")])
def test_an_ftp_server_without_one_command_lacks_only_that_ability(tmp_path: Path, command: str, unable: str) -> None:
    with ftp_server(tmp_path, without=(command,)) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        found = connection.capabilities
        connection.close()
    abilities = {"resume": found.resume, "timestamps": found.timestamps, "rights": found.rights}
    assert abilities == {name: name != unable for name in abilities}


def test_sftp_without_a_shell_can_still_resume_keep_dates_and_set_rights(tmp_path: Path) -> None:
    with ssh_server(tmp_path, shell=False) as running:
        connection = trusted_ssh(running)
        found = connection.capabilities
        connection.close()
    assert not found.shell and (found.resume, found.timestamps, found.rights) == (True, True, True)


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now


def test_the_meter_shows_the_percentage_and_the_time_left_of_transferring_only() -> None:
    clock = Clock()  # the meter's own clock: no other code sees a clock that stands still
    seen: list[int] = []
    job = Job(Scripted(CommandResult(0, "", "")), Connection(name="T", host="h"), "/a", "/srv", True, "a")
    meter = Meter(job, lambda item: seen.append(item.percent), clock)
    clock.now += 50  # counting first: no part of the speed
    meter.set_total(1000)
    clock.now += FIRST_ESTIMATE_SECONDS / 2
    meter.add(100)
    assert job.percent == 10 and job.seconds_left is None  # too soon to promise a time
    clock.now += FIRST_ESTIMATE_SECONDS * 1.5  # 2 seconds of transferring for 200 bytes
    meter.add(100)
    assert job.percent == 20 and job.seconds_left == pytest.approx(8 * FIRST_ESTIMATE_SECONDS)
    clock.now += 100  # waiting for the person's answer
    meter.paused(100)
    clock.now += FIRST_ESTIMATE_SECONDS * 2  # 4 seconds of transferring for 400 bytes
    meter.add(200)
    assert job.percent == 40 and job.seconds_left == pytest.approx(6 * FIRST_ESTIMATE_SECONDS)
    assert seen[-1] == 40


def test_rsync_never_removes_a_folder_through_a_link_in_a_move(tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / "real" / "empty").mkdir(parents=True)
    (root / "linked").symlink_to("real")
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        (tmp_path / "here" / "linked" / "empty").mkdir(parents=True)  # as if it arrived
        job = Job(connection, connection.connection, str(tmp_path / "here"), "/linked", False, "linked", options=TransferOptions(delete_source=True))
        _remove_empty_sources(job, [])  # the look-over never follows a link into a folder: nothing below it is on the list
        connection.close()
    assert (root / "real" / "empty").is_dir() and (root / "linked").is_symlink()



# --- files that only carry our suffixes: never ours to write to or remove


@pytest.mark.parametrize("move", [False, True], ids=["copy", "move"])
@pytest.mark.parametrize("order", ["part-name-first", "plain-first", "one-folder"])
@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_a_real_file_named_like_our_part_file_is_never_written_to_nor_removed(server: Server, tmp_path: Path, upload: bool, order: str, move: bool) -> None:
    """a.bin and a real file a.bin.chrono-ftp-part go together: the part of a.bin takes another free name, whatever comes first."""
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    real = "a.bin" + PART_SUFFIX
    write(source / "box" / "a.bin", "the file itself")
    write(source / "box" / real, "someone's own file")
    (target / "box").mkdir()
    options = TransferOptions(resume=RESUME_ON, delete_source=move)
    names = {"part-name-first": ["box/" + real, "box/a.bin"], "plain-first": ["box/a.bin", "box/" + real], "one-folder": ["box"]}[order]
    for name in names:
        job = up(server, here / name, options, folder="/" + name.rpartition("/")[0]) if upload else down(server, "/" + name, here / name.rpartition("/")[0], options)
        assert job.state == State.DONE, job.message
    assert sorted(path.name for path in (target / "box").iterdir()) == ["a.bin", real]  # nothing left under another name either
    assert read(target / "box" / "a.bin") == "the file itself"
    assert read(target / "box" / real) == "someone's own file"
    left = sorted(path.name for path in (source / "box").iterdir()) if (source / "box").exists() else []
    assert left == ([] if move else ["a.bin", real])


def test_a_real_file_named_like_our_part_file_waiting_at_the_target_is_never_continued_nor_removed(server: Server, tmp_path: Path) -> None:
    """Already there before the upload, with no note of ours: the upload of a.bin goes under another temporary name."""
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    write(root / ("a.bin" + PART_SUFFIX), "someone's own file")
    write(here / "a.bin", "the file itself, which is longer")
    job = up(server, here / "a.bin", TransferOptions(resume=RESUME_ON))
    assert job.state == State.DONE, job.message
    assert sorted(path.name for path in root.iterdir()) == ["a.bin", "a.bin" + PART_SUFFIX]
    assert read(root / ("a.bin" + PART_SUFFIX)) == "someone's own file"


def test_a_real_file_named_like_our_moved_aside_file_stays_when_a_file_is_replaced(server: Server, tmp_path: Path) -> None:
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    write(root / "a.txt", "old")
    write(root / ("a.txt" + OLD_SUFFIX), "someone's own file")
    write(here / "a.txt", "new")
    job = up(server, here / "a.txt", TransferOptions(ask_before_replacing=False))
    assert job.state == State.DONE, job.message
    assert sorted(path.name for path in root.iterdir()) == ["a.txt", "a.txt" + OLD_SUFFIX]
    assert (read(root / "a.txt"), read(root / ("a.txt" + OLD_SUFFIX))) == ("new", "someone's own file")


# --- a server folder that takes files but can't be read


@pytest.mark.parametrize("answer", [Choice.NO, Choice.YES])
def test_an_upload_into_a_folder_that_can_not_be_read_asks_before_it_replaces_a_file_there(tmp_path: Path, answer: Choice) -> None:
    """Nothing in the folder can be listed, so the name itself is looked up before anything takes it; the server tells its size, so it
    is known to be a file."""
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "incoming" / "a.txt", "someone's file")
    write(here / "a.txt", "mine")
    person = Person(Answer(answer))
    with ftp_server(root, write_only=("incoming",), sizes_anywhere=True) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        job = up((ftp, settings, root), here / "a.txt", TransferOptions(), person, folder="/incoming")
        ftp.close()
    assert job.state == State.DONE, job.message
    assert [conflict.name for conflict in person.asked] == ["a.txt"]
    assert read(root / "incoming" / "a.txt") == ("mine" if answer == Choice.YES else "someone's file")
    assert sorted(path.name for path in (root / "incoming").iterdir()) == ["a.txt"]


def test_an_upload_into_a_folder_that_can_not_be_read_goes_as_ever_when_the_name_is_free(tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "incoming").mkdir(parents=True)
    write(here / "a.txt", "mine")
    person = Person()
    with ftp_server(root, write_only=("incoming",)) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        job = up((ftp, settings, root), here / "a.txt", TransferOptions(), person, folder="/incoming")
        ftp.close()
    assert job.state == State.DONE, job.message
    assert person.asked == [] and read(root / "incoming" / "a.txt") == "mine"


def test_a_rename_onto_a_name_this_job_just_sent_is_asked_about_again(server: Server, tmp_path: Path) -> None:
    """The folder was read once, before a.txt arrived: the name is taken now all the same, so nothing replaces it unasked."""
    root = server[2]
    here = tmp_path / "here"
    write(here / "box" / "a.txt", "sent first")
    write(here / "box" / "b.txt", "renamed")
    write(root / "box" / "b.txt", "there before")
    person = Person(Answer(Choice.RENAME, "a.txt"), Answer(Choice.NO))
    job = up(server, here / "box", TransferOptions(), person)
    assert job.state == State.DONE, job.message
    assert [conflict.name for conflict in person.asked] == ["b.txt", "a.txt"]
    assert (read(root / "box" / "a.txt"), read(root / "box" / "b.txt")) == ("sent first", "there before")


@pytest.mark.parametrize("sizes", [False, True], ids=["no-size", "size-refused-for-a-folder"])
@pytest.mark.parametrize("asking", ["ask", "no-question", "yes-to-all"])
def test_something_of_unknown_kind_in_a_folder_that_can_not_be_read_is_never_replaced(tmp_path: Path, asking: str, sizes: bool) -> None:
    """A folder x where an upload of a file x would go, in a folder that can't be read: nothing tells that it is a file, so it is left as
    it is, whatever the options or answers, and nothing is moved."""
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "incoming" / "x" / "inside.txt", "someone's folder")
    write(here / "x", "mine")
    person = Person(Answer(Choice.YES))
    options = TransferOptions(ask_before_replacing=asking == "ask")
    answers = Answers(batch=Choice.YES_TO_ALL) if asking == "yes-to-all" else Answers()
    with ftp_server(root, write_only=("incoming",), sizes_anywhere=sizes) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        job = transfer((ftp, settings, root), here / "x", "/incoming", True, "x", options, person, answers)
        ftp.close()
    assert job.state == State.FAILED and job.message == KIND_UNKNOWN_TEXT.format(name="x"), job.message
    assert person.asked == []
    assert sorted(path.name for path in (root / "incoming").iterdir()) == ["x"]
    assert read(root / "incoming" / "x" / "inside.txt") == "someone's folder"


def test_a_file_in_a_folder_that_can_not_be_read_is_not_replaced_where_nothing_tells_it_is_a_file(tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "incoming" / "a.txt", "someone's file")
    write(here / "a.txt", "mine")
    with ftp_server(root, write_only=("incoming",)) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        job = up((ftp, settings, root), here / "a.txt", TransferOptions(ask_before_replacing=False), folder="/incoming")
        ftp.close()
    assert job.state == State.FAILED and job.message == KIND_UNKNOWN_TEXT.format(name="a.txt"), job.message
    assert sorted(path.name for path in (root / "incoming").iterdir()) == ["a.txt"] and read(root / "incoming" / "a.txt") == "someone's file"


def test_a_subfolder_that_can_not_be_read_still_takes_an_upload(tmp_path: Path) -> None:
    """tree is readable, tree/sub only takes files: every level is handled as the top one."""
    root, here = tmp_path / "server", tmp_path / "here"
    (root / "tree" / "sub").mkdir(parents=True)
    write(here / "tree" / "b.txt", "b")
    write(here / "tree" / "sub" / "a.txt", "a")
    with ftp_server(root, write_only=("tree/sub",)) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        job = up((ftp, settings, root), here / "tree", TransferOptions())
        ftp.close()
    assert job.state == State.DONE, job.message
    assert read(root / "tree" / "b.txt") == "b" and read(root / "tree" / "sub" / "a.txt") == "a"


# --- a file that appears at the target while it is on its way


@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_a_file_that_appears_at_the_target_meanwhile_is_never_replaced(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    """Nothing was there when the transfer started, so nobody was asked; something else puts a file there while it is on its way. It
    stays as it is: on the server nothing replaces it, here the fetched copy is kept under a free name, and the item says so."""
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    write(source / "a.txt", "mine")
    kind = type(connection)
    method = "upload" if upload else "download"
    real = getattr(kind, method)

    def someone_else_meanwhile(self: RemoteConnection, *args: object) -> None:
        real(self, *args)
        write(target / "a.txt", "someone else's")

    with monkeypatch.context() as patch:
        patch.setattr(kind, method, someone_else_meanwhile)
        job = up(server, here / "a.txt") if upload else down(server, "/a.txt", here)
    assert job.state == State.FAILED
    assert read(target / "a.txt") == "someone else's"
    if upload:
        assert job.message == APPEARED_THERE_TEXT.format(name="a.txt")
        assert sorted(path.name for path in root.iterdir()) == ["a.txt"]  # nothing of ours is left there either
    else:
        assert job.message == APPEARED_HERE_TEXT.format(name="a.txt", kept="a~2.txt")
        assert sorted(path.name for path in here.iterdir()) == ["a.txt", "a~2.txt"] and read(here / "a~2.txt") == "mine"


def test_a_link_listed_with_its_own_size_is_fetched_by_the_size_of_the_file_it_leads_to(tmp_path: Path) -> None:
    """Some FTP servers list a link as a link, with the size of the link itself: what is fetched is the file it leads to."""
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "real.txt", "the content of the real file")
    (root / "link.txt").symlink_to("real.txt")
    here.mkdir()
    with ftp_server(root, links_as_links=True) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        listed = next(entry for entry in ftp.list_folder("/") if entry.name == "link.txt")
        assert listed.is_link and listed.size == len("real.txt")  # the link's own size
        job = down((ftp, settings, root), "/link.txt", here)
        ftp.close()
    assert job.state == State.DONE, job.message
    assert read(here / "link.txt") == "the content of the real file"


def test_a_download_onto_a_local_link_that_leads_nowhere_writes_nothing_anywhere(server: Server, tmp_path: Path) -> None:
    root = server[2]
    here, outside = tmp_path / "here", tmp_path / "outside"
    here.mkdir()
    outside.mkdir()
    write(root / "a.txt", "from the server")
    (here / "a.txt").symlink_to(outside / "missing.txt")
    job = down(server, "/a.txt", here)
    assert job.state == State.FAILED and job.message == LOCAL_LINK_NOWHERE_TEXT.format(name="a.txt"), job.message
    assert list(outside.iterdir()) == [] and (here / "a.txt").is_symlink()
    assert sorted(path.name for path in here.iterdir()) == ["a.txt"]


def test_a_server_file_rewritten_at_the_same_size_during_its_download_is_never_put_in_place(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    write(root / "a.txt", "first version", OLD_TIME)
    kind = type(connection)
    real = kind.download

    def rewritten_meanwhile(self: RemoteConnection, remote: str, local: str, progress: Callable[[int], None], limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        real(self, remote, local, progress, limit, offset, append, exclusive)
        write(root / "a.txt", "other version", NEWER_TIME)  # the same size, other bytes, another date

    with monkeypatch.context() as patch:
        patch.setattr(kind, "download", rewritten_meanwhile)
        job = down(server, "/a.txt", here, TransferOptions(delete_source=True))
    assert job.state == State.FAILED and job.message == CHANGED_THERE_TEXT.format(name="a.txt"), job.message
    assert list(here.iterdir()) == []  # neither a copy nor a part
    assert read(root / "a.txt") == "other version"  # and the move took nothing away


@NOT_AS_ROOT
def test_counting_first_skips_a_folder_that_can_not_be_read_and_fetches_the_rest(server: Server, tmp_path: Path) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    write(root / "tree" / "a.txt", "a")
    write(root / "tree" / "closed" / "b.txt", "b")
    (root / "tree" / "closed").chmod(0o000)
    try:
        job = down(server, "/tree", here, TransferOptions(count_first=True))
    finally:
        (root / "tree" / "closed").chmod(0o755)
    assert job.state == State.FAILED and job.message.startswith("Partly done: tree/closed was left out."), job.message
    assert read(here / "tree" / "a.txt") == "a"


@pytest.mark.parametrize("mlst", [True, False], ids=["with-mlst", "without-mlst"])
def test_placing_many_new_files_over_ftp_reads_each_folder_once(tmp_path: Path, mlst: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    for number in range(20):
        write(here / "box" / f"file-{number}.txt", str(number))
    heard: list[str] = []
    with ftp_server(root, without=() if mlst else ("MLST",), command_heard=heard.append) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        heard.clear()
        job = up((ftp, settings, root), here / "box", TransferOptions())
        ftp.close()
    assert job.state == State.DONE, job.message
    assert len(list((root / "box").iterdir())) == 20
    listings = [command for command in heard if command in ("LIST", "MLSD", "NLST")]
    assert len(listings) <= 3, listings  # the folder it goes into, and the new folder: never one for each file
    assert heard.count("MLST") <= 1 and heard.count("RNFR") == 60 and heard.count("TYPE") <= 22  # per file: no check twice (TYPE: one at login, one per file)


@pytest.mark.parametrize("move", [False, True], ids=["copy", "move"])
@pytest.mark.parametrize("mlst", [True, False], ids=["with-mlst", "without-mlst"])
def test_a_file_rewritten_during_its_fetch_is_seen_by_asking_the_server_this_moment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mlst: bool, move: bool) -> None:
    """The listing read at the start is older than the change: the check asks the server anew, so the stale copy is never put in place
    and a move never removes the newer file."""
    root, here = tmp_path / "server", tmp_path / "here"
    here.mkdir()
    write(root / "d" / "a.txt", "first version", OLD_TIME)
    write(root / "d" / "b.txt", "first version", OLD_TIME)
    real = FtpConnection.download

    def rewritten(self: FtpConnection, remote: str, local: str, progress: Callable[[int], None], limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        real(self, remote, local, progress, limit, offset, append, exclusive)
        if remote.endswith("a.txt"):
            write(root / "d" / "a.txt", "other version", NEWER_TIME)

    with ftp_server(root, without=() if mlst else ("MLST",)) as port:
        settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
        ftp = FtpConnection(settings, PASSWORD)
        ftp.connect()
        with monkeypatch.context() as patch:
            patch.setattr(FtpConnection, "download", rewritten)
            job = down((ftp, settings, root), "/d", here, TransferOptions(delete_source=move))
        ftp.close()
    assert job.state == State.FAILED and job.message == f"Partly done: d/a.txt was left out. {CHANGED_THERE_TEXT.format(name='a.txt')}", job.message
    assert read(root / "d" / "a.txt") == "other version" and not (here / "d" / "a.txt").exists()
    assert read(here / "d" / "b.txt") == "first version" and (root / "d" / "b.txt").exists() != move


class MeanwhileOnTheServer(Person):
    """Answers Yes; while the person thinks, something else changes the server."""

    def __init__(self, meanwhile: Callable[[], None]) -> None:
        super().__init__(Answer(Choice.YES))
        self.meanwhile: Callable[[], None] = meanwhile

    @override
    def __call__(self, job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
        self.meanwhile()
        super().__call__(job, conflict, reply)


@pytest.mark.parametrize("meanwhile", ["aside-name-taken", "changed", "became-a-folder", "gone"])
def test_a_file_is_replaced_only_while_it_is_still_the_one_the_person_agreed_to(server: Server, tmp_path: Path, meanwhile: str) -> None:
    root = server[2]
    write(root / "a.txt", "old", OLD_TIME)
    write(tmp_path / "a.txt", "new")
    aside = root / ("a.txt" + OLD_SUFFIX)

    def change() -> None:
        if meanwhile == "aside-name-taken":
            write(aside, "someone's own file")
        elif meanwhile == "changed":
            write(root / "a.txt", "changed by someone", NEWER_TIME)
        elif meanwhile == "became-a-folder":
            (root / "a.txt").unlink()
            write(root / "a.txt" / "inside.txt", "a folder now")
        else:
            (root / "a.txt").unlink()

    job = up(server, tmp_path / "a.txt", TransferOptions(), MeanwhileOnTheServer(change))
    names = sorted(path.name for path in root.iterdir())
    if meanwhile in ("changed", "became-a-folder"):
        assert job.state == State.FAILED and job.message == CHANGED_TO_REPLACE_TEXT.format(name="a.txt"), job.message
        assert names == ["a.txt"]  # nothing moved aside, nothing of ours left
        assert read(root / "a.txt") == "changed by someone" if meanwhile == "changed" else read(root / "a.txt" / "inside.txt") == "a folder now"
        return
    assert job.state == State.DONE, job.message
    assert read(root / "a.txt") == "new"
    if meanwhile == "aside-name-taken":
        assert names == ["a.txt", "a.txt" + OLD_SUFFIX] and read(aside) == "someone's own file"
    else:
        assert names == ["a.txt"]


def test_a_name_a_listing_leaves_out_is_told_never_silently_skipped(server: Server, tmp_path: Path) -> None:
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    write(root / "tree" / "back\\slash.txt", "odd")
    write(root / "tree" / "ok.txt", "ok")
    job = down(server, "/tree", here)
    assert job.state == State.FAILED and job.message == f"Partly done: tree/back\\slash.txt was left out. {UNSAFE_NAME_TEXT}", job.message
    assert read(here / "tree" / "ok.txt") == "ok" and not (here / "tree" / "back\\slash.txt").exists()


@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_a_foreign_file_that_takes_the_temporary_name_during_the_question_is_never_written_to(server: Server, tmp_path: Path, upload: bool) -> None:
    """The name was free when it was chosen; while the person thinks, a file of someone takes it. The part is made only where nothing
    is, so that file stays as it is, and the transfer goes under the next free name."""
    root = server[2]
    here = tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    write(source / "a.txt", "new")
    write(target / "a.txt", "old", OLD_TIME)
    foreign = target / ("a.txt" + PART_SUFFIX)

    def someone_else() -> None:
        write(foreign, "someone's own file")

    person = MeanwhileOnTheServer(someone_else)
    job = up(server, here / "a.txt", TransferOptions(), person) if upload else down(server, "/a.txt", here, TransferOptions(), person)
    assert job.state == State.DONE, job.message
    assert read(target / "a.txt") == "new" and read(foreign) == "someone's own file"
    assert sorted(path.name for path in target.iterdir()) == ["a.txt", "a.txt" + PART_SUFFIX]  # nothing of ours left


ANOTHER_SENDER: str = """import sys
from chrono_ftp.config import parts
from chrono_ftp.config.parts import PartRecord, upload_key
from chrono_ftp.core.connection import Connection
login = Connection(name="T", host=sys.argv[1], port=int(sys.argv[2]), user=sys.argv[3])
assert parts.claim(upload_key(login, sys.argv[4]), PartRecord('["here", "elsewhere"]', 100, 1.0, 0))
print("writing", flush=True)
if sys.argv[5] == "stays":
    sys.stdin.read()
else:
    import os
    os._exit(0)  # stops at once: no clean-up of any kind
"""


@contextmanager
def other_chrono_ftp(login: Connection, part: str, how: str) -> Generator[Child]:
    source = Path(parts.__file__).resolve().parents[2]
    with child(ANOTHER_SENDER, [login.host, str(login.port), login.user, part, how], {**os.environ, "PYTHONPATH": str(source)}) as other:
        other.says(b"writing\n")
        yield other


def test_two_chrono_ftps_sending_the_same_file_into_one_folder_never_share_a_temporary_file(server: Server, tmp_path: Path) -> None:
    connection, settings, root = server
    write(tmp_path / "a.txt", "mine")
    with other_chrono_ftp(settings, "/a.txt" + PART_SUFFIX, "stays"):  # it ends when the block does, also on a failure
        write(root / ("a.txt" + PART_SUFFIX), "the other one's bytes")  # it is writing its part now
        job = up(server, tmp_path / "a.txt", TransferOptions())
    assert job.state == State.DONE, job.message
    assert read(root / "a.txt") == "mine" and read(root / ("a.txt" + PART_SUFFIX)) == "the other one's bytes"
    assert sorted(path.name for path in root.iterdir()) == ["a.txt", "a.txt" + PART_SUFFIX]


def test_a_part_left_by_a_chrono_ftp_that_stopped_at_once_is_ours_and_never_leaks(server: Server, tmp_path: Path) -> None:
    """Its note was written before its first byte: the part is ours, and goes once tidying finds it can't be continued."""
    connection, settings, root = server
    with other_chrono_ftp(settings, "/gone.txt" + PART_SUFFIX, "stops") as other:
        other.ended()
    write(root / ("gone.txt" + PART_SUFFIX), "half", OLD_TIME)  # written long ago: nothing writes it now
    entries = connection.list_folder("/")
    tidy(connection, "/", entries, {f"/{entry.name}": (time.time() - 3600, entry.size) for entry in entries}, Owned(settings, TransferOptions()))
    assert not (root / ("gone.txt" + PART_SUFFIX)).exists()


def test_a_part_left_by_a_chrono_ftp_that_stopped_at_once_is_taken_over_by_the_next_transfer_of_its_file(server: Server, tmp_path: Path) -> None:
    connection, settings, root = server
    with other_chrono_ftp(settings, "/gone.txt" + PART_SUFFIX, "stops") as other:
        other.ended()
    write(root / ("gone.txt" + PART_SUFFIX), "half of something")
    write(tmp_path / "gone.txt", "whole")
    job = up(server, tmp_path / "gone.txt", TransferOptions())
    assert job.state == State.DONE, job.message
    assert sorted(path.name for path in root.iterdir()) == ["gone.txt"] and read(root / "gone.txt") == "whole"  # ours: taken over, not left


def test_a_rename_is_remembered_only_once_it_is_done_and_never_acted_on_without_a_name(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rename that failed leaves no decision behind for the other way (after rsync could not log in), and a rename without a name is
    never done: it would be the folder itself."""
    connection, settings, root = server
    write(root / "a.txt", "old")
    write(tmp_path / "a.txt", "new")
    job = Job(connection, settings, str(tmp_path / "a.txt"), "/", True, "a.txt", has_password=True)
    known: dict[str, Action] = {}
    kind = type(connection)

    def refused(self: RemoteConnection, *args: object) -> None:
        raise RemoteActionFailed("Refused.")

    def renamed(conflict: Conflict) -> Answer:
        return Answer(Choice.RENAME, "b.txt")

    def no_name(conflict: Conflict) -> Answer:
        return Answer(Choice.RENAME, "")

    with monkeypatch.context() as patch:
        patch.setattr(kind, "upload", refused)
        with pytest.raises(RemoteActionFailed):
            FileWay(job, connection, 0, Meter(job, lambda item: None), renamed, None, known).run()
    assert known == {}  # the failed rename is no decision another way could act on
    with pytest.raises(RemoteActionFailed, match=NOT_A_NAME_TEXT):
        FileWay(job, connection, 0, Meter(job, lambda item: None), no_name, None, {"a.txt": Action.RENAME}).run()
    assert sorted(path.name for path in root.iterdir()) == ["a.txt"] and read(root / "a.txt") == "old"


def test_a_download_replaces_the_file_here_only_while_it_is_the_one_agreed_to(server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _settings, root = server
    here = tmp_path / "here"
    write(root / "a.txt", "from the server")
    write(here / "a.txt", "old here", OLD_TIME)
    kind = type(connection)
    real = kind.download

    def changed_here_meanwhile(self: RemoteConnection, remote: str, local: str, progress: Callable[[int], None], limit: int = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        real(self, remote, local, progress, limit, offset, append, exclusive)
        write(here / "a.txt", "edited here meanwhile", NEWER_TIME)

    with monkeypatch.context() as patch:
        patch.setattr(kind, "download", changed_here_meanwhile)
        job = down(server, "/a.txt", here, TransferOptions(), Person(Answer(Choice.YES)))
    assert job.state == State.FAILED and job.message == CHANGED_HERE_REPLACE_TEXT.format(name="a.txt", kept="a~2.txt"), job.message
    assert read(here / "a.txt") == "edited here meanwhile" and read(here / "a~2.txt") == "from the server"
    assert sorted(path.name for path in here.iterdir()) == ["a.txt", "a~2.txt"]


@pytest.mark.parametrize("ask", [True, False], ids=["ask", "no-question"])
def test_a_name_the_server_listing_can_not_show_is_never_sent(server: Server, tmp_path: Path, ask: bool) -> None:
    """A file named back\\slash.txt is there already, but no listing shows it: it could be replaced unasked, so it is never sent."""
    root = server[2]
    write(root / "back\\slash.txt", "the server's own")
    write(tmp_path / "back\\slash.txt", "mine")
    person = Person(Answer(Choice.YES))
    job = up(server, tmp_path / "back\\slash.txt", TransferOptions(ask_before_replacing=ask), person)
    assert job.state == State.FAILED and job.message == UNCHECKABLE_NAME_TEXT, job.message
    assert person.asked == [] and read(root / "back\\slash.txt") == "the server's own"
    assert sorted(path.name for path in root.iterdir()) == ["back\\slash.txt"]


# --- what is never sent: a link to a folder, a link that leads nowhere, a pipe

NOT_SENT_TEXTS: dict[str, str] = {"folder-link": LINK_TO_FOLDER_UP_TEXT, "nowhere-link": LINK_NOWHERE_UP_TEXT, "pipe": NOT_A_FILE_UP_TEXT}


def not_sendable(folder: Path, outside: Path) -> None:
    """In folder: a link to a folder outside, a link that leads nowhere, and a pipe (which Windows does not have)."""
    write(outside / "secret.txt", "not part of this transfer")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "folder-link").symlink_to(outside)
    (folder / "nowhere-link").symlink_to(outside / "missing")
    if sys.platform == "win32":
        pytest.skip("Windows has no pipes in its folders")
    else:
        os.mkfifo(folder / "pipe")


@pytest.mark.parametrize("kind", list(NOT_SENT_TEXTS))
def test_a_picked_link_or_pipe_fails_saying_what_it_is_and_nothing_is_sent(server: Server, tmp_path: Path, kind: str) -> None:
    root, here = server[2], tmp_path / "here"
    not_sendable(here, tmp_path / "outside")
    job = up(server, here / kind)
    assert job.state == State.FAILED and job.message == NOT_SENT_TEXTS[kind].format(name=kind), job.message  # never a Done
    assert list(root.iterdir()) == []


def test_links_and_pipes_inside_an_uploaded_folder_are_left_out_and_counted(server: Server, tmp_path: Path) -> None:
    root, here = server[2], tmp_path / "here"
    write(here / "box" / "a.txt", "a")
    not_sendable(here / "box", tmp_path / "outside")
    job = up(server, here / "box")
    assert job.state == State.DONE and job.message.startswith("Done."), job.message
    assert note_text("links_left", 2) in job.message and note_text("others_left", 1) in job.message, job.message
    assert sorted(path.name for path in (root / "box").iterdir()) == ["a.txt"]


def test_a_picked_link_to_a_folder_on_the_server_fails_saying_so_and_inside_a_folder_it_is_counted(tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "box" / "a.txt", "a")
    write(root / "real" / "b.txt", "b")
    (root / "box" / "folder-link").symlink_to("../real")
    (root / "folder-link").symlink_to("real")
    here.mkdir()
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served = (connection, connection.connection, root)
        picked = down(served, "/folder-link", here)
        inside = down(served, "/box", here)
        connection.close()
    assert picked.state == State.FAILED and picked.message == LINK_TO_FOLDER_DOWN_TEXT.format(name="folder-link"), picked.message
    assert inside.state == State.DONE and inside.message == f"Done. {note_text('links_left', 1)}", inside.message
    assert sorted(path.name for path in here.iterdir()) == ["box"] and sorted(path.name for path in (here / "box").iterdir()) == ["a.txt"]


def pipe_on_the_server(root: Path) -> None:
    """A pipe on its own, and one in a folder next to a file. Reading a pipe waits until something writes to it: forever, here."""
    write(root / "box" / "a.txt", "a")
    if sys.platform == "win32":
        pytest.skip("Windows has no pipes in its folders")
    else:
        os.mkfifo(root / "pipe")
        os.mkfifo(root / "box" / "pipe")


@pytest.mark.parametrize("protocol", ["sftp", "ftp"])
def test_a_pipe_on_the_server_is_never_fetched_nor_made_here(tmp_path: Path, protocol: str) -> None:
    """Over FTP only where the listing tells the kind (unix.mode with the kind in it); where it can't, a pipe looks like a file."""
    root, here = tmp_path / "server", tmp_path / "here"
    pipe_on_the_server(root)
    here.mkdir()
    if protocol == "sftp":
        with ssh_server(root) as running:
            connection = trusted_ssh(running)
            served: Server = (connection, connection.connection, root)
            picked, inside = down(served, "/pipe", here), down(served, "/box", here)
            connection.close()
    else:
        with ftp_server(root, full_modes=True) as port:
            settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
            ftp = FtpConnection(settings, PASSWORD)
            ftp.connect()
            picked, inside = down((ftp, settings, root), "/pipe", here), down((ftp, settings, root), "/box", here)
            ftp.close()
    assert picked.state == State.FAILED and picked.message == NOT_A_FILE_DOWN_TEXT.format(name="pipe"), picked.message
    assert inside.state == State.DONE and inside.message == f"Done. {note_text('others_left', 1)}", inside.message
    assert sorted(path.name for path in here.iterdir()) == ["box"] and sorted(path.name for path in (here / "box").iterdir()) == ["a.txt"]


def test_an_ftp_listing_tells_a_pipe_socket_or_device_by_its_type_or_its_full_mode() -> None:
    assert entry_from("p", {"type": "OS.unix=fifo"}) == RemoteEntry("p", False, size_known=False, special=True)
    for mode in ("0010644", "0140755", "0020660", "0060660"):
        found = entry_from("p", {"type": "file", "size": "0", "unix.mode": mode})
        assert found is not None and found.special, mode
    for mode in ("0100644", "0644", "0o644", ""):
        found = entry_from("f", {"type": "file", "size": "0", "unix.mode": mode})
        assert found is not None and not found.special, mode  # a plain file, or only the rights: no kind of its own is told
    linked = entry_from("l", {"type": "OS.unix=slink:/x"})
    assert linked is not None and linked.is_link and not linked.special


def test_the_check_before_rsync_fetches_finds_links_pipes_sockets_and_devices_with_a_quoted_path() -> None:
    assert source_check_command("/a b/it's") == "find '/a b/it'\"'\"'s' \\( -type l -o -type p -o -type s -o -type c -o -type b \\) -print -quit"


def test_an_ftp_mode_that_holds_the_kind_gives_the_rights_alone_and_a_replace_keeps_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "a.txt", "old")
    (root / "a.txt").chmod(0o640)
    write(here / "a.txt", "new")
    sent: list[str] = []
    real = FtpConnection.set_mode

    def noted(self: FtpConnection, path: str, mode: str) -> None:
        sent.append(mode)
        real(self, path, mode)

    monkeypatch.setattr(FtpConnection, "set_mode", noted)
    listed: dict[bool, str] = {}
    job: Job | None = None
    for full in (False, True):
        with ftp_server(root, full_modes=full) as port:
            settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
            ftp = FtpConnection(settings, PASSWORD)
            ftp.connect()
            listed[full] = next(entry for entry in ftp.list_folder("/") if entry.name == "a.txt").permissions
            if full:
                job = up((ftp, settings, root), here / "a.txt", TransferOptions(ask_before_replacing=False))
            ftp.close()
    assert listed[True] == "0640" and int(listed[True], 8) == int(listed[False], 8)  # the same rights as a server that gives them alone
    assert job is not None and job.state == State.DONE, job
    assert read(root / "a.txt") == "new" and (root / "a.txt").stat().st_mode & 0o7777 == 0o640 and sent == ["0640"]


def pipes_in_the_way(root: Path, here: Path) -> None:
    """On the server a pipe where a file goes, on its own and in a folder; here the files of those names."""
    write(here / "p", "mine")
    write(here / "box" / "a.txt", "a")
    write(here / "box" / "p", "mine too")
    (root / "box").mkdir(parents=True)
    if sys.platform == "win32":
        pytest.skip("Windows has no pipes in its folders")
    else:
        os.mkfifo(root / "p")
        os.mkfifo(root / "box" / "p")


def pipes_still_there(root: Path) -> bool:
    return all(stat.S_ISFIFO(os.lstat(path).st_mode) for path in (root / "p", root / "box" / "p"))


@pytest.mark.parametrize("protocol", ["sftp", "ftp"])
def test_an_upload_never_replaces_a_pipe_on_the_server_nor_asks_about_it(tmp_path: Path, protocol: str) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    pipes_in_the_way(root, here)
    person = Person(Answer(Choice.YES))
    if protocol == "sftp":
        with ssh_server(root) as running:
            connection = trusted_ssh(running)
            served: Server = (connection, connection.connection, root)
            picked, inside = up(served, here / "p", person=person), up(served, here / "box", person=person)
            connection.close()
    else:
        with ftp_server(root, full_modes=True) as port:
            settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
            ftp = FtpConnection(settings, PASSWORD)
            ftp.connect()
            picked, inside = up((ftp, settings, root), here / "p", person=person), up((ftp, settings, root), here / "box", person=person)
            ftp.close()
    said = NOT_A_FILE_THERE_TEXT.format(name="p")
    assert picked.state == State.FAILED and picked.message == said, picked.message
    assert inside.state == State.FAILED and inside.message == f"Partly done: box/p was left out. {said}", inside.message
    assert person.asked == [] and pipes_still_there(root) and read(root / "box" / "a.txt") == "a"


def pipes_here(root: Path, here: Path) -> None:
    """Here a pipe where a fetched file goes, on its own and in a folder; on the server the files of those names."""
    write(root / "p", "theirs")
    write(root / "box" / "a.txt", "a")
    write(root / "box" / "p", "theirs too")
    (here / "box").mkdir(parents=True)
    if sys.platform == "win32":
        pytest.skip("Windows has no pipes in its folders")
    else:
        os.mkfifo(here / "p")
        os.mkfifo(here / "box" / "p")


@pytest.mark.parametrize("protocol", ["sftp", "ftp"])
def test_a_download_never_replaces_a_pipe_here_nor_asks_about_it(tmp_path: Path, protocol: str) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    pipes_here(root, here)
    person = Person(Answer(Choice.YES))
    if protocol == "sftp":
        with ssh_server(root) as running:
            connection = trusted_ssh(running)
            served: Server = (connection, connection.connection, root)
            picked, inside = down(served, "/p", here, person=person), down(served, "/box", here, person=person)
            connection.close()
    else:
        with ftp_server(root) as port:
            settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
            ftp = FtpConnection(settings, PASSWORD)
            ftp.connect()
            picked, inside = down((ftp, settings, root), "/p", here, person=person), down((ftp, settings, root), "/box", here, person=person)
            ftp.close()
    said = NOT_A_FILE_HERE_TEXT.format(name="p")
    assert picked.state == State.FAILED and picked.message == said, picked.message
    assert inside.state == State.FAILED and inside.message == f"Partly done: box/p was left out. {said}", inside.message
    assert person.asked == [] and pipes_still_there(here) and read(here / "box" / "a.txt") == "a"


def test_a_save_in_the_very_last_moment_before_a_replace_is_kept_aside_and_told(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Something else saves the file there after the last look and before it is moved aside: that version is never removed."""
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "a.txt", "old")
    write(here / "a.txt", "mine")
    real_rename = SshConnection.rename

    def saved_first(self: SshConnection, old: str, new: str) -> None:
        if old == "/a.txt" and new.endswith(OLD_SUFFIX):
            write(root / "a.txt", "someone's save, longer than old")  # between the check and the move aside
        real_rename(self, old, new)

    monkeypatch.setattr(SshConnection, "rename", saved_first)
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        job = up((connection, connection.connection, root), here / "a.txt", TransferOptions(ask_before_replacing=False))
        connection.close()
    kept = "a.txt" + OLD_SUFFIX
    assert parts.old_target(connection.connection, "/" + kept, -1) is None  # no note: never tidied away as ours
    assert job.state == State.DONE and job.message == "Done. " + CHANGED_ASIDE_TEXT.format(name="a.txt", kept=kept), job.message
    assert read(root / "a.txt") == "mine" and read(root / kept) == "someone's save, longer than old"


LONG_NAME: str = "n" * 246 + ".bin"  # 250 bytes: none of our suffixes fits next to it


def test_a_name_of_our_own_fits_a_folder_keeps_its_suffix_and_stays_its_own() -> None:
    for name in (LONG_NAME, "é" * 126, "\udcff" * 250):  # one byte a character, two, and bytes that are no UTF-8 (kept through surrogates)
        for extra in (PART_SUFFIX, f"~99{PART_SUFFIX}", OLD_SUFFIX):
            made = own_name(name, extra)
            stored = made.encode("utf-8", "surrogateescape")
            assert len(stored) <= NAME_BYTES and made.endswith(extra) and stored.decode("utf-8", "surrogateescape") == made
    assert own_name(LONG_NAME, PART_SUFFIX) != own_name("n" * 245 + "m.bin", PART_SUFFIX)  # alike at the start, still apart
    assert own_name("a.txt", PART_SUFFIX) == "a.txt" + PART_SUFFIX  # a name that fits keeps the form it always had


@pytest.mark.parametrize("upload", [True, False])
def test_a_long_name_is_sent_replaced_and_continued(server: Server, tmp_path: Path, upload: bool) -> None:
    root, here = server[2], tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    content = os.urandom(BIG)
    (source / LONG_NAME).write_bytes(content)
    (target / LONG_NAME).write_bytes(b"old")
    options = TransferOptions(resume=RESUME_ON, ask_before_replacing=False)
    part = target / own_name(LONG_NAME, PART_SUFFIX)
    job = stopped_halfway(server, here / LONG_NAME if upload else here, "/" if upload else "/" + LONG_NAME, upload, LONG_NAME, options, part)
    assert job.state == State.STOPPED, job.message
    assert 0 < part.stat().st_size < BIG and (target / LONG_NAME).read_bytes() == b"old"
    again = up(server, here / LONG_NAME, options) if upload else down(server, "/" + LONG_NAME, here, options)
    assert again.state == State.DONE, again.message
    assert (target / LONG_NAME).read_bytes() == content and sorted(path.name for path in target.iterdir()) == [LONG_NAME]  # part and aside gone


def test_a_part_of_a_long_name_is_tidied_once_nothing_can_continue_it(server: Server, tmp_path: Path) -> None:
    connection, settings, root = server
    here = tmp_path / "here"
    here.mkdir()
    (here / LONG_NAME).write_bytes(os.urandom(BIG))
    part = root / own_name(LONG_NAME, PART_SUFFIX)
    options = TransferOptions(resume=RESUME_ON)
    job = stopped_halfway(server, here / LONG_NAME, "/", True, LONG_NAME, options, part)
    assert job.state == State.STOPPED and part.exists(), job.message
    (here / LONG_NAME).unlink()  # its source is gone: nothing can continue the part
    os.utime(part, (OLD_TIME, OLD_TIME))
    entries = connection.list_folder("/")
    tidy(connection, "/", entries, {f"/{entry.name}": (time.time() - 3600, entry.size) for entry in entries}, Owned(settings, options))
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_a_move_keeps_a_source_rewritten_at_the_same_size_with_a_new_date(server: Server, tmp_path: Path, upload: bool) -> None:
    """Between the read and the removal the source got other bytes of the same length: only its date tells, and it stays."""
    connection, settings, root = server
    here = tmp_path / "here"
    source = write(here / "a.txt", "abc", OLD_TIME) if upload else write(root / "a.txt", "abc", OLD_TIME)
    write(root / "a.txt", "abc", OLD_TIME) if upload else write(here / "a.txt", "abc", OLD_TIME)  # what arrived
    job = Job(connection, settings, str(here / "a.txt"), "/", upload, "a.txt", has_password=True, options=TransferOptions(delete_source=True))
    way = FileWay(job, connection, 0, Meter(job, lambda item: None), lambda conflict: Answer(Choice.NO))
    read_as = connection.fresh_details("/a.txt")
    assert read_as is not None
    write(source, "xyz", NEWER_TIME)  # rewritten meanwhile: the same size, a new date
    if upload:
        way._remove_source(source, 3, OLD_TIME, True, "/a.txt")
    else:
        way._remove_remote("/a.txt", read_as, True)
    assert way.kept_changed == 1 and read(source) == "xyz"


# --- every look at whether a file is still the one agreed to counts its size, its date and its kind

AGREED: RemoteEntry = RemoteEntry("a.txt", False, 3, OLD_TIME)
ASIDE: str = "/a.txt" + OLD_SUFFIX


def unknown_kind_at(patch: pytest.MonkeyPatch, path: str) -> None:
    """The server can't tell what is at path this moment: its kind is unknown."""
    real = SshConnection.fresh_details

    def unknown(self: SshConnection, asked: str) -> RemoteEntry | None:
        found = real(self, asked)
        return replace(found, kind_known=False) if found is not None and asked == path else found

    patch.setattr(SshConnection, "fresh_details", unknown)


@pytest.mark.parametrize("aside", ["the-agreed-file", "a-new-date", "an-unknown-kind"])
def test_swap_in_removes_the_old_file_only_while_it_is_the_agreed_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, aside: str) -> None:
    root = tmp_path / "server"
    write(root / "a.txt", "old", NEWER_TIME if aside == "a-new-date" else OLD_TIME)  # the same size either way
    write(root / "t", "new")
    if aside == "an-unknown-kind":
        unknown_kind_at(monkeypatch, ASIDE)
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        swapped = connection.swap_in("/t", "/a.txt", AGREED, ASIDE)
        connection.close()
    assert read(root / "a.txt") == "new"
    if aside == "the-agreed-file":
        assert swapped == Swapped.REMOVED and not (root / ASIDE.lstrip("/")).exists()
    else:
        assert swapped == Swapped.CHANGED and read(root / ASIDE.lstrip("/")) == "old"


def test_swap_in_puts_the_old_file_back_when_the_new_one_can_not_take_its_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    write(root / "a.txt", "old", OLD_TIME)
    write(root / "t", "new")
    real = SshConnection.rename

    def refused(self: SshConnection, old: str, new: str) -> None:
        if old == "/t":
            raise RemoteActionFailed("Refused.")
        real(self, old, new)

    monkeypatch.setattr(SshConnection, "rename", refused)
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        with pytest.raises(RemoteActionFailed):
            connection.swap_in("/t", "/a.txt", AGREED, ASIDE)
        connection.close()
    assert read(root / "a.txt") == "old" and read(root / "t") == "new" and not (root / ASIDE.lstrip("/")).exists()


@pytest.mark.parametrize("look", ["right-before-the-replace", "right-before-rsync"])
@pytest.mark.parametrize("there", ["the-agreed-file", "a-new-date", "an-unknown-kind"])
def test_a_server_file_is_still_the_agreed_one_only_with_its_date_and_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, there: str, look: str) -> None:
    root = tmp_path / "server"
    write(root / "a.txt", "old", NEWER_TIME if there == "a-new-date" else OLD_TIME)
    write(tmp_path / "a.txt", "new")
    if there == "an-unknown-kind":
        unknown_kind_at(monkeypatch, "/a.txt")
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        job = Job(connection, connection.connection, str(tmp_path / "a.txt"), "/", True, "a.txt")
        way = FileWay(job, connection, 0, Meter(job, lambda item: None), lambda conflict: Answer(Choice.NO), ["a.txt"])
        if look == "right-before-the-replace":
            try:
                way._still_the_one("/a.txt", AGREED)
                refused = ""
            except RemoteActionFailed as error:
                refused = str(error)
        else:
            way.agreed_there["a.txt"] = ("/a.txt", AGREED)
            way.recheck_agreed()
            refused = way.failed[0][1] if way.failed else ""
        connection.close()
    assert refused == ("" if there == "the-agreed-file" else CHANGED_TO_REPLACE_TEXT.format(name="a.txt"))
    if look == "right-before-rsync":
        assert way.recording == (["a.txt"] if there == "the-agreed-file" else [])


@pytest.mark.parametrize("here", ["the-agreed-file", "a-new-date", "gone"])
def test_a_file_here_agreed_to_be_replaced_leaves_rsyncs_list_when_its_date_changed(tmp_path: Path, here: str) -> None:
    target = write(tmp_path / "a.txt", "old", OLD_TIME)
    if here == "a-new-date":
        os.utime(target, (NEWER_TIME, NEWER_TIME))  # the same size
    elif here == "gone":
        target.unlink()  # nothing is replaced: the name is simply taken
    remote = Scripted(CommandResult(0, "", ""))
    job = Job(remote, Connection(name="T", host="h"), str(tmp_path), "/a.txt", False, "a.txt")
    way = FileWay(job, remote, 0, Meter(job, lambda item: None), lambda conflict: Answer(Choice.NO), ["a.txt"])
    way.agreed_here["a.txt"] = (str(target), (3, OLD_TIME))
    way.recheck_agreed()
    if here == "a-new-date":
        assert way.recording == [] and way.failed == [("a.txt", CHANGED_HERE_NOT_REPLACED_TEXT.format(name="a.txt"))]
    else:
        assert way.recording == ["a.txt"] and way.failed == []


def test_a_fetched_file_never_replaces_one_here_rewritten_at_its_size_with_a_new_date(tmp_path: Path) -> None:
    target = write(tmp_path / "a.txt", "mid", NEWER_TIME)  # the question showed it at OLD_TIME, with this size
    temporary = write(tmp_path / ("a.txt" + PART_SUFFIX), "fetched")
    with pytest.raises(LocalActionFailed, match="changed on this computer meanwhile"):
        FileWay._replace_here(str(temporary), str(target), (3, OLD_TIME))
    assert read(target) == "mid" and read(tmp_path / "a~2.txt") == "fetched"


@pytest.mark.parametrize("meanwhile", ["a-new-date", "its-size-now-known", "skipped-and-changed"])
def test_an_answer_carried_over_holds_only_for_the_file_as_it_was_seen(meanwhile: str) -> None:
    """The look-over decided about the file there; the other way meets it again. A Skip still skips, whatever happened to it."""
    remote = Scripted(CommandResult(0, "", ""))
    job = Job(remote, Connection(name="T", host="h"), "/here/a.txt", "/", True, "a.txt")
    known: dict[str, Action] = {}
    shown: dict[str, tuple[int, bool, float | None]] = {}
    answer = Answer(Choice.NO if meanwhile == "skipped-and-changed" else Choice.YES)
    seen = Conflict("a.txt", "/", True, 5, 1.0, 0 if meanwhile == "its-size-now-known" else 3, OLD_TIME, None, target_size_known=meanwhile != "its-size-now-known")
    now = replace(seen, target_size_known=True) if meanwhile == "its-size-now-known" else replace(seen, target_modified=NEWER_TIME)
    FileWay(job, remote, 0, Meter(job, lambda item: None), lambda conflict: answer, [], known, shown)._decide("a.txt", seen)
    other = FileWay(job, remote, 0, Meter(job, lambda item: None), lambda conflict: Answer(Choice.STOP), None, known, shown)
    if meanwhile == "skipped-and-changed":
        assert other._decide("a.txt", now) == Action.SKIP
    else:
        with pytest.raises(RemoteActionFailed, match="changed on the server meanwhile"):
            other._decide("a.txt", now)


def test_a_name_of_our_own_at_the_bounds_of_a_folder() -> None:
    fits = "f" * (NAME_BYTES - len(PART_SUFFIX))  # with the suffix exactly as long as a folder allows
    assert own_name(fits, PART_SUFFIX) == fits + PART_SUFFIX
    over = fits + "g"  # one byte too long
    made = own_name(over, PART_SUFFIX)
    mark = "~" + hashlib.sha256(over.encode()).hexdigest()[:HASH_LENGTH]
    assert len(made.encode()) == NAME_BYTES and made.endswith(mark + PART_SUFFIX) and made == over[: NAME_BYTES - len(mark + PART_SUFFIX)] + mark + PART_SUFFIX


@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_append_holds_only_for_the_file_as_the_person_saw_it(tmp_path: Path, upload: bool) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "log.txt", "more" if not upload else "first\n", OLD_TIME)
    write(here / "log.txt", "more" if upload else "first\n", OLD_TIME)
    target = root / "log.txt" if upload else here / "log.txt"

    class Meanwhile(Person):
        @override
        def __call__(self, job: Job, conflict: Conflict, reply: Callable[[Answer], None]) -> None:
            write(target, "someone else's newer text\n")  # while the person thinks
            super().__call__(job, conflict, reply)

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        served: Server = (connection, connection.connection, root)
        person = Meanwhile(Answer(Choice.APPEND))
        job = up(served, here / "log.txt", person=person) if upload else down(served, "/log.txt", here, person=person)
        connection.close()
    said = (CHANGED_TO_REPLACE_TEXT if upload else CHANGED_HERE_NOT_REPLACED_TEXT).format(name="log.txt")
    assert job.state == State.FAILED and job.message == said, job.message
    assert read(target) == "someone else's newer text\n"


def test_with_new_dates_an_uploaded_folder_does_not_get_the_date_of_its_source(server: Server, tmp_path: Path) -> None:
    root = server[2]
    write(tmp_path / "box" / "a.txt", "a", OLD_TIME)
    os.utime(tmp_path / "box", (OLD_TIME, OLD_TIME))
    job = up(server, tmp_path / "box", TransferOptions(keep_timestamps=False))
    assert job.state == State.DONE, job.message
    assert (root / "box").stat().st_mtime != OLD_TIME and (root / "box" / "a.txt").stat().st_mtime != OLD_TIME


@pytest.mark.parametrize("size", [None, -1], ids=["no-size", "a-negative-size"])
def test_an_sftp_size_that_is_not_given_is_unknown_and_append_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: int | None) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "a.txt", "old")
    write(here / "a.txt", "more")
    real_readdir, real_stat = asyncssh.SFTPClient.readdir, asyncssh.SFTPClient.stat

    async def readdir(self: asyncssh.SFTPClient, path: str = ".") -> Sequence[asyncssh.SFTPName]:
        names = await real_readdir(self, path)
        for item in names:
            item.attrs.size = size
        return names

    async def stat(self: asyncssh.SFTPClient, path: str, *args: object, **named: object) -> asyncssh.SFTPAttrs:
        found = await real_stat(self, path)
        found.size = size
        return found

    monkeypatch.setattr(asyncssh.SFTPClient, "readdir", readdir)
    monkeypatch.setattr(asyncssh.SFTPClient, "stat", stat)
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        listed = next(entry for entry in connection.list_folder("/") if entry.name == "a.txt")
        found = connection.details("/a.txt")
        job = up((connection, connection.connection, root), here / "a.txt", person=Person(Answer(Choice.APPEND)))
        connection.close()
    assert not listed.size_known and found is not None and not found.size_known
    assert job.state == State.FAILED and job.message == NO_APPEND_TEXT and read(root / "a.txt") == "old"


def test_sftp_takes_every_kind_that_is_no_file_nor_folder_for_one() -> None:
    for kind in (asyncssh.FILEXFER_TYPE_FIFO, asyncssh.FILEXFER_TYPE_SOCKET, asyncssh.FILEXFER_TYPE_CHAR_DEVICE, asyncssh.FILEXFER_TYPE_BLOCK_DEVICE, asyncssh.FILEXFER_TYPE_SPECIAL):
        assert _special(asyncssh.SFTPAttrs(type=kind)), kind
    for kind in (asyncssh.FILEXFER_TYPE_REGULAR, asyncssh.FILEXFER_TYPE_DIRECTORY, asyncssh.FILEXFER_TYPE_SYMLINK):
        assert not _special(asyncssh.SFTPAttrs(type=kind)), kind


def test_a_socket_on_the_sftp_server_is_neither_a_file_nor_a_folder_in_its_listing_and_its_details(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    if sys.platform == "win32":
        pytest.skip("Windows has no sockets in its folders")
    else:
        listener = socket.socket(socket.AF_UNIX)
        started = os.getcwd()
        os.chdir(root)  # a socket's path is short: its name alone, from its own folder
        try:
            listener.bind("sock")
        finally:
            os.chdir(started)
        try:
            with ssh_server(root) as running:
                connection = trusted_ssh(running)
                listed = next(entry for entry in connection.list_folder("/") if entry.name == "sock")
                found = connection.details("/sock")
                connection.close()
        finally:
            listener.close()
        assert listed.special and found is not None and found.special


def test_rights_with_setuid_setgid_and_sticky_are_kept_from_both_kinds_of_server() -> None:
    assert _modes(0o104755) == "4755" and _modes(0o42775) == "2775" and _modes(0o41777) == "1777"
    assert _rights_only("0104755") == "4755" and _rights_only("0041777") == "1777"


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_of_the_full_length_is_finished_and_placed_not_sent_again(server: Server, tmp_path: Path, upload: bool) -> None:
    root, here = server[2], tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    write(source / "a.txt", "abcdef", OLD_TIME)
    write(target / ("a.txt" + PART_SUFFIX), "Xbcdef")  # marked: only a part that is used keeps the mark
    note_part(server, here, "a.txt", upload, 6, OLD_TIME, 6)
    job = up(server, here / "a.txt", TransferOptions(resume=RESUME_ON)) if upload else down(server, "/a.txt", here, TransferOptions(resume=RESUME_ON))
    assert job.state == State.DONE, job.message
    assert read(target / "a.txt") == "Xbcdef" and not (target / ("a.txt" + PART_SUFFIX)).exists()


@pytest.mark.parametrize("upload", [True, False])
def test_a_part_without_bytes_is_never_offered_to_resume(server: Server, tmp_path: Path, upload: bool) -> None:
    root, here = server[2], tmp_path / "here"
    here.mkdir()
    source, target = (here, root) if upload else (root, here)
    write(source / "a.txt", "abcdef", OLD_TIME)
    write(target / "a.txt", "old")
    write(target / ("a.txt" + PART_SUFFIX), "")
    note_part(server, here, "a.txt", upload, 6, OLD_TIME, 0)
    person = Person(Answer(Choice.NO))
    up(server, here / "a.txt", TransferOptions(resume=RESUME_ON), person) if upload else down(server, "/a.txt", here, TransferOptions(resume=RESUME_ON), person)
    assert len(person.asked) == 1 and person.asked[0].part_size is None


@pytest.mark.parametrize("here_already", [False, True], ids=["nothing-here", "a-file-here"])
def test_a_picked_item_no_longer_on_the_server_fails_saying_so_and_nothing_is_made_here(server: Server, tmp_path: Path, here_already: bool) -> None:
    here = tmp_path / "here"
    here.mkdir()
    if here_already:
        write(here / "gone.txt", "mine")
    job = down(server, "/gone.txt", here)
    assert job.state == State.FAILED and job.message == GONE_THERE_TEXT.format(name="gone.txt"), job.message
    assert [path.name for path in here.iterdir()] == (["gone.txt"] if here_already else [])
    if here_already:
        assert read(here / "gone.txt") == "mine"


def test_a_picked_item_the_sftp_server_refuses_to_tell_about_is_said_to_be_unreadable_not_gone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    write(root / "secret.txt", "theirs")
    here.mkdir()

    def nothing_listed(self: SshConnection, path: str) -> list[RemoteEntry]:
        return []  # the listing leaves the name out

    async def refused(self: asyncssh.SFTPClient, path: str, *args: object, **named: object) -> asyncssh.SFTPAttrs:
        raise asyncssh.SFTPPermissionDenied("Permission denied")

    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        monkeypatch.setattr(SshConnection, "list_folder", nothing_listed)
        monkeypatch.setattr(asyncssh.SFTPClient, "stat", refused)
        job = down((connection, connection.connection, root), "/secret.txt", here)
        monkeypatch.undo()
        connection.close()
    assert job.state == State.FAILED and job.message == UNREADABLE_THERE_TEXT.format(name="secret.txt"), job.message
    assert list(here.iterdir()) == []

