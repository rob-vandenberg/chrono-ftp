"""The transfer options on the rsync way (FB-02 to FB-08), with a real rsync that reaches this computer through a stand-in for ssh. All
of it happens inside the test's own folder: the SFTP server is closed in there, and rsync gets only paths in there."""

import os
import shlex
import shutil
import stat
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from chrono_ftp.config import parts
from chrono_ftp.config.parts import PartRecord, upload_key, upload_source
from chrono_ftp.connections.base import RemoteConnection, RemoteEntry
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core.errors import RemoteActionFailed
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.transferoptions import RESUME_OFF, RESUME_ON, TransferOptions
from chrono_ftp.connections.base import Capabilities, CommandResult
from chrono_ftp.transfers import rsyncway
from chrono_ftp.transfers.job import PART_SUFFIX, Job, Meter, State
from chrono_ftp.transfers.options import Answer, Choice
from chrono_ftp.transfers.queue import LEFTOVER_SECONDS, Owned, TransferQueue, note_text, tidy, tidy_partials_here
from chrono_ftp.transfers.filebyfile import NOT_A_FILE_HERE_TEXT, NOT_A_FILE_THERE_TEXT, NOT_A_FILE_DOWN_TEXT, CHANGED_HERE_NOT_REPLACED_TEXT, CHANGED_TO_REPLACE_TEXT, LINK_TO_FOLDER_DOWN_TEXT, FileWay, FILE_HERE_TEXT, FILE_IN_THE_WAY_TEXT, FOLDER_HERE_TEXT, FOLDER_IN_THE_WAY_TEXT, LINK_NOWHERE_TEXT, UNSAFE_NAME_TEXT
from chrono_ftp.transfers.rsyncway import DROPPED_KEPT_TEXT
from chrono_ftp.transfers.rsyncway import PARTIAL_FOLDER, _remove_arrived_sources, rsync_arguments
from tests.fakeservers import NOT_AS_ROOT, ssh_server
from tests.test_connections import trusted_ssh
from tests.test_transfer_safety import NOT_SENT_TEXTS, MeanwhileOnTheServer, not_sendable, pipe_on_the_server, pipes_here, pipes_in_the_way, pipes_still_there
from tests.test_transfer_options import BIG, NEWER_TIME, OLD_TIME, SLOW, Person, read, write
from tests.test_engine import Scripted
from tests.test_transfers import Leftovers, fake_rsync, wait_for

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_rsync.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: moved here from test_transfer_options.py, now inside the test's own folder; download with move, dates and rights;
#          a move keeps a source that changed; the part of a stopped rsync; Rename, Append, Resume and Only if newer while rsync looks
#          over a job; the other way after rsync could not log in asks nobody twice; a link to a file goes as its content, both ways; a new
#          file that turns up meanwhile is never replaced; a drop after bytes moved is a broken-off transfer; after a fallback each item
#          fails once and each byte counts once; a move never removes the source of a file rsync did not send; a link that leads nowhere
#          fails alone; an upload move has the server put each file on its disk first; never a file replaced by a folder nor a folder by a
#          file; a test that needs a refusal skips as root; what a move could check is counted; a name the look-over leaves out; rsync's
#          folder of unfinished copies tidied on both sides; a move removes only folders that arrived; whether anything moved by the
#          files sent; a look-over note told once; a rename onto a server link stays out of rsync's list; a picked link or pipe fails
#          and those inside a folder are counted, as file by file; a file agreed to be replaced that changed meanwhile is left as it is;
#          a pipe on the server is never fetched nor made here, nor replaced by an upload; a pipe here is never replaced by a download; the
#          other way after rsync could not log in never replaces a file that changed after the person agreed; a move keeps the source
#          of a file left out of rsync's list, also when a copy just like it turned up there; an upload into a folder that can't be
#          read goes file by file

WAIT_SECONDS: float = 10.0


@dataclass
class RsyncServer:
    connection: RemoteConnection
    settings: Connection
    shell: list[str]
    folder: Path  # the folder on the server: its path is the same for SFTP and for rsync
    mark: bytes  # in the environment of every program the stand-in for ssh starts, so a test can wait for them to end

    def receivers_ended(self) -> bool:
        """No program started through the stand-in runs any more: rsync on the server side has put everything in its place."""
        for entry in Path("/proc").iterdir():
            try:
                if entry.name.isdigit() and self.mark in (entry / "environ").read_bytes():
                    return False
            except OSError:
                continue  # it ended meanwhile, or is not ours to read
        return True

    def wait_for_receivers(self) -> None:
        deadline = time.monotonic() + WAIT_SECONDS
        while not self.receivers_ended():
            assert time.monotonic() < deadline, "rsync on the server side did not end"
            threading.Event().wait(0.02)


@pytest.fixture
def rsync_server(tmp_path: Path) -> Generator[RsyncServer]:
    """The SFTP server is closed in tmp_path/sftp-root, where the folder of the server sits at the same path it has for rsync: that path,
    tmp_path/server, is a link to it. So both reach the same files, and neither can reach anything outside tmp_path."""
    for program in ("rsync", "sh", "env"):
        if shutil.which(program) is None:
            pytest.skip(f"{program} is not installed on this computer")
    root = tmp_path / "sftp-root"
    real = root / str(tmp_path / "server").lstrip("/")
    real.mkdir(parents=True)
    (tmp_path / "server").symlink_to(real)
    mark = f"CHRONO_FTP_TEST_RSYNC={tmp_path}"
    shell = tmp_path / "fake-ssh"
    shell.write_text(f'#!/bin/sh\nwhile [ "${{1#-}}" != "$1" ]; do [ "$1" = "-l" ] && shift; shift; done\nshift\nexec env "{mark}" sh -c "$*"\n', encoding="utf-8")
    shell.chmod(0o755)
    with ssh_server(root) as running:
        connection = trusted_ssh(running)
        server = RsyncServer(connection, connection.connection, [str(shell)], tmp_path / "server", mark.encode())
        yield server
        server.wait_for_receivers()
        connection.close()


def rsync_job(server: RsyncServer, local: Path, remote_path: str, upload: bool, name: str, options: TransferOptions, person: Person | None = None, limit: int = 0, stop_halfway: bool = False, program: str = "rsync") -> Job:
    queue = TransferQueue(lambda item: None, program, ask=person, remote_shell=server.shell)
    queue.limit = limit
    job = Job(server.connection, server.settings, str(local), remote_path, upload, name, options=options)
    try:
        queue.add(job)
        if stop_halfway:
            deadline = time.monotonic() + WAIT_SECONDS
            while job.percent == 0 and job.state in (State.WAITING, State.RUNNING):
                assert time.monotonic() < deadline, "rsync did not get under way"
                threading.Event().wait(0.01)
            queue.stop(job)
        finished = threading.Event()
        threading.Thread(target=lambda: wait_for((job,), finished), daemon=True).start()
        assert finished.wait(15)
    finally:
        queue.close()  # also when the test fails: no thread of it runs on
        server.wait_for_receivers()
    return job


@pytest.mark.parametrize("upload", [True, False])
def test_rsync_asks_first_and_never_writes_a_file_the_person_refused(rsync_server: RsyncServer, tmp_path: Path, upload: bool) -> None:
    server_side, here = rsync_server.folder, tmp_path / "here"
    source, target = (here, server_side) if upload else (server_side, here)
    for name in ("refused.txt", "replaced.txt"):
        write(source / "tree" / name, "new")
        write(target / "tree" / name, "old")
    write(source / "tree" / "deep" / "fresh.txt", "fresh")
    person = Person(Answer(Choice.NO), Answer(Choice.YES))
    if upload:
        job = rsync_job(rsync_server, here / "tree", str(server_side), True, "tree", TransferOptions(), person)
    else:
        job = rsync_job(rsync_server, here, str(server_side / "tree"), False, "tree", TransferOptions(), person)
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert sorted(item.name for item in person.asked) == ["refused.txt", "replaced.txt"]
    first = person.asked[0].name
    second = "replaced.txt" if first == "refused.txt" else "refused.txt"
    assert read(target / "tree" / first) == "old" and read(target / "tree" / second) == "new"
    assert read(target / "tree" / "deep" / "fresh.txt") == "fresh"


def test_rsync_moves_up_with_dates(rsync_server: RsyncServer, tmp_path: Path) -> None:
    server_side, here = rsync_server.folder, tmp_path / "here"
    write(here / "tree" / "deep" / "a.txt", "a", OLD_TIME)
    job = rsync_job(rsync_server, here / "tree", str(server_side), True, "tree", TransferOptions(delete_source=True))
    assert job.state == State.DONE and job.way == "rsync", job.message
    arrived = server_side / "tree" / "deep" / "a.txt"
    assert read(arrived) == "a" and arrived.stat().st_mtime == OLD_TIME
    assert not (here / "tree").exists()


def test_an_upload_that_sets_rights_goes_file_by_file_so_existing_folders_keep_theirs(rsync_server: RsyncServer, tmp_path: Path) -> None:
    server_side, here = rsync_server.folder, tmp_path / "here"
    write(here / "tree" / "new" / "a.txt", "a")
    (server_side / "tree").mkdir()
    (server_side / "tree").chmod(0o711)
    job = rsync_job(rsync_server, here / "tree", str(server_side), True, "tree", TransferOptions(set_rights=True, rights="640"))
    assert job.state == State.DONE and job.way == "SFTP", job.message
    assert stat.S_IMODE((server_side / "tree" / "new" / "a.txt").stat().st_mode) == 0o640
    assert stat.S_IMODE((server_side / "tree" / "new").stat().st_mode) == 0o750
    assert stat.S_IMODE((server_side / "tree").stat().st_mode) == 0o711


def test_rsync_moves_down_and_keeps_what_was_refused_and_the_folders_that_still_hold_it(rsync_server: RsyncServer, tmp_path: Path) -> None:
    server_side, here = rsync_server.folder, tmp_path / "here"
    write(server_side / "tree" / "gone" / "a.txt", "a")
    write(server_side / "tree" / "kept" / "b.txt", "new")
    write(here / "tree" / "kept" / "b.txt", "old")
    job = rsync_job(rsync_server, here, str(server_side / "tree"), False, "tree", TransferOptions(delete_source=True), Person(Answer(Choice.NO)))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert read(here / "tree" / "gone" / "a.txt") == "a"
    assert not (server_side / "tree" / "gone").exists()  # emptied, so it went too
    assert read(server_side / "tree" / "kept" / "b.txt") == "new" and read(here / "tree" / "kept" / "b.txt") == "old"


def test_rsync_download_keeps_dates_and_the_rights_of_a_replaced_file(rsync_server: RsyncServer, tmp_path: Path) -> None:
    server_side, here = rsync_server.folder, tmp_path / "here"
    write(server_side / "tree" / "a.txt", "new", OLD_TIME)
    os.utime(server_side / "tree", (OLD_TIME, OLD_TIME))
    write(here / "tree" / "a.txt", "old!")
    (here / "tree" / "a.txt").chmod(0o600)
    job = rsync_job(rsync_server, here, str(server_side / "tree"), False, "tree", TransferOptions(ask_before_replacing=False))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert read(here / "tree" / "a.txt") == "new"
    assert (here / "tree" / "a.txt").stat().st_mtime == OLD_TIME and (here / "tree").stat().st_mtime == OLD_TIME
    assert stat.S_IMODE((here / "tree" / "a.txt").stat().st_mode) == 0o600


@pytest.mark.parametrize("upload", [True, False])
def test_a_move_keeps_every_source_that_changed_after_it_was_read(rsync_server: RsyncServer, tmp_path: Path, upload: bool) -> None:
    server_side, here = rsync_server.folder, tmp_path / "here"
    source, target = (here, server_side) if upload else (server_side, here)
    write(source / "tree" / "same.txt", "same", OLD_TIME)
    write(source / "tree" / "grew.txt", "grew", OLD_TIME)
    write(target / "tree" / "same.txt", "same", OLD_TIME)
    write(target / "tree" / "grew.txt", "grew", OLD_TIME)  # what arrived: the source as it was read
    write(source / "tree" / "grew.txt", "grew and grew", OLD_TIME + 5)  # it changed after it was read
    job = Job(rsync_server.connection, rsync_server.settings, str(here if not upload else here / "tree"), str(server_side if upload else server_side / "tree"), upload, "tree", options=TransferOptions(delete_source=True))
    kept = _remove_arrived_sources(job, {"tree/same.txt": (4, OLD_TIME), "tree/grew.txt": (4, OLD_TIME)})
    assert kept == {"kept_changed": 1, "kept_unchecked": 0, "kept_not_removed": 0, "removed_unflushed": 0}
    assert not (source / "tree" / "same.txt").exists()
    assert read(source / "tree" / "grew.txt") == "grew and grew"


def test_a_stopped_rsync_keeps_its_part_aside_and_the_next_run_finishes(rsync_server: RsyncServer, tmp_path: Path) -> None:
    server_side, here = rsync_server.folder, tmp_path / "here"
    here.mkdir()
    content = os.urandom(4 * BIG)
    (here / "big.bin").write_bytes(content)
    options = TransferOptions(resume=RESUME_ON, ask_before_replacing=False)
    job = rsync_job(rsync_server, here / "big.bin", str(server_side), True, "big.bin", options, limit=SLOW, stop_halfway=True)
    assert job.state == State.STOPPED  # rsync_job waited until rsync on the server side ended: its part is where it puts it
    assert not (server_side / "big.bin").exists()  # never a half file under the real name
    part = server_side / PARTIAL_FOLDER / "big.bin"
    deadline = time.monotonic() + WAIT_SECONDS
    while not (part.exists() and part.stat().st_size > 0):  # the thing checked: the part holds bytes
        assert time.monotonic() < deadline, "rsync kept no part"
        threading.Event().wait(0.02)
    again = rsync_job(rsync_server, here / "big.bin", str(server_side), True, "big.bin", options)
    assert again.state == State.DONE, again.message
    assert (server_side / "big.bin").read_bytes() == content
    assert not (server_side / PARTIAL_FOLDER).exists()


def test_rsync_gets_a_flag_for_every_option() -> None:
    job = Job(Leftovers([]), Connection(name="T", host="h"), "/home/a/tree", "/srv", True, "tree")
    plain = rsync_arguments(job, 0)
    assert "--no-perms" in plain and "--no-times" not in plain and "--no-inc-recursive" in plain and f"--partial-dir={PARTIAL_FOLDER}" in plain
    job.options = TransferOptions(resume=RESUME_OFF, keep_timestamps=False, count_first=False, delete_source=True)
    chosen = rsync_arguments(job, 0, files_from="/tmp/list")
    assert "--no-perms" in chosen and not any(item.startswith("--chmod") for item in chosen) and "--no-times" in chosen
    assert "--remove-source-files" not in chosen  # a move removes each source itself, only when it is unchanged
    assert "--no-inc-recursive" not in chosen and not any(item.startswith("--partial-dir") for item in chosen)
    assert chosen[-6:] == ["--files-from=/tmp/list", "--from0", "--ignore-times", "--", "/home/a/", "h:/srv/"]


def rsync_upload(server: RsyncServer, here: Path, person: Person, options: TransferOptions | None = None, program: str = "rsync") -> Job:
    return rsync_job(server, here / "tree", str(server.folder), True, "tree", options or TransferOptions(), person, program=program)


def test_rename_on_the_rsync_way_keeps_both(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here = tmp_path / "here"
    write(here / "tree" / "a.txt", "new")
    write(here / "tree" / "b.txt", "b")
    write(rsync_server.folder / "tree" / "a.txt", "old")
    job = rsync_upload(rsync_server, here, Person(Answer(Choice.RENAME, "a (2).txt")))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert [read(rsync_server.folder / "tree" / name) for name in ("a.txt", "a (2).txt", "b.txt")] == ["old", "new", "b"]


def test_rename_onto_a_link_leaves_the_file_the_person_kept_out_of_rsyncs_list(rsync_server: RsyncServer, tmp_path: Path) -> None:
    """The name the person gave is a link on the server (one that turned up after the queue looked for links): the file it leads to is
    replaced by the look-over itself, and the kept file never reaches rsync's list, where its name would be read apart from the new one."""
    there = rsync_server.folder
    write(tmp_path / "here" / "a.txt", "new")
    write(there / "a.txt", "old")
    write(there / "real.txt", "was here")
    (there / "b.txt").symlink_to("real.txt")
    job = Job(rsync_server.connection, rsync_server.settings, str(tmp_path / "here" / "a.txt"), str(there), True, "a.txt", options=TransferOptions())
    answers = [Answer(Choice.RENAME, "b.txt"), Answer(Choice.YES)]
    meter = Meter(job, lambda item: None)
    look = FileWay(job, rsync_server.connection, 0, meter, lambda conflict: answers.pop(0), [], {})
    look.run()
    assert look.recording == []
    try:
        rsyncway.with_rsync(job, 0, "rsync", rsync_server.shell, meter, look.recording, look.recorded_sources)
    finally:
        rsync_server.wait_for_receivers()
    assert read(there / "a.txt") == "old"  # the file the person chose to keep
    assert (there / "b.txt").is_symlink() and read(there / "real.txt") == "new"


def test_append_on_the_rsync_way_adds_to_the_end(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here = tmp_path / "here"
    write(here / "tree" / "log.txt", "second\n")
    write(rsync_server.folder / "tree" / "log.txt", "first\n")
    job = rsync_upload(rsync_server, here, Person(Answer(Choice.APPEND)))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert read(rsync_server.folder / "tree" / "log.txt") == "first\nsecond\n"


def test_resume_on_the_rsync_way_continues_the_part(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here = tmp_path / "here"
    write(here / "tree" / "a.txt", "0123456789")
    target = rsync_server.folder / "tree" / "a.txt"
    write(target, "old")
    write(rsync_server.folder / "tree" / ("a.txt" + PART_SUFFIX), "XYZ")
    temporary = f"{rsync_server.folder}/tree/a.txt{PART_SUFFIX}"
    parts.remember(upload_key(rsync_server.settings, temporary), PartRecord(upload_source(str(here / "tree" / "a.txt")), 10, (here / "tree" / "a.txt").stat().st_mtime, 3))
    person = Person(Answer(Choice.RESUME))
    job = rsync_upload(rsync_server, here, person)
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert person.asked[0].part_size == 3 and read(target) == "XYZ3456789"


def test_only_if_newer_on_the_rsync_way(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    write(here / "tree" / "older-there.txt", "new", NEWER_TIME)
    write(here / "tree" / "newer-there.txt", "new", OLD_TIME)
    write(there / "older-there.txt", "old", OLD_TIME)
    write(there / "newer-there.txt", "old", NEWER_TIME)
    person = Person(Answer(Choice.ONLY_IF_NEWER))
    job = rsync_upload(rsync_server, here, person)
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert len(person.asked) == 1
    assert [read(there / name) for name in ("older-there.txt", "newer-there.txt")] == ["new", "old"]


@pytest.mark.parametrize("status", [12, 255])
def test_after_rsync_could_not_log_in_the_other_way_asks_nobody_twice_and_moves_safely(rsync_server: RsyncServer, tmp_path: Path, status: int) -> None:
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    write(here / "tree" / "a.txt", "new")
    write(here / "tree" / "b.txt", "b")
    write(there / "a.txt", "old")
    person = Person(Answer(Choice.YES))
    job = rsync_upload(rsync_server, here, person, TransferOptions(delete_source=True), fake_rsync(tmp_path, f"exit {status}"))
    assert job.state == State.DONE and job.way == "SFTP", job.message
    assert len(person.asked) == 1
    assert [read(there / name) for name in ("a.txt", "b.txt")] == ["new", "b"]
    assert not (here / "tree").exists()  # each source went once it arrived, then the emptied folder


def test_rsync_sends_a_link_to_a_file_as_its_content_and_never_follows_a_link_to_a_folder(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    write(here / "tree" / "real.txt", "content")
    (here / "tree" / "link.txt").symlink_to("real.txt")
    write(tmp_path / "outside" / "secret.txt", "not part of this upload")
    (here / "tree" / "folder-link").symlink_to(tmp_path / "outside")
    job = rsync_job(rsync_server, here / "tree", str(rsync_server.folder), True, "tree", TransferOptions(ask_before_replacing=False))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert not (there / "link.txt").is_symlink() and read(there / "link.txt") == "content"  # as the file by file way sends it
    assert not (there / "folder-link").exists()


def test_rsync_fetches_a_link_to_a_file_as_its_content_and_never_follows_a_link_to_a_folder(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    here.mkdir()
    write(there / "real.txt", "content")
    (there / "link.txt").symlink_to("real.txt")
    write(tmp_path / "outside" / "secret.txt", "not part of this download")
    (there / "folder-link").symlink_to(tmp_path / "outside")
    job = rsync_job(rsync_server, here, str(there), False, "tree", TransferOptions(ask_before_replacing=False))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert not (here / "tree" / "link.txt").is_symlink() and read(here / "tree" / "link.txt") == "content"  # as the file by file way fetches it
    assert not (here / "tree" / "folder-link").exists()


def test_a_new_file_that_turns_up_before_rsync_runs_is_never_replaced(rsync_server: RsyncServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    write(here / "tree" / "asked.txt", "new")
    write(there / "asked.txt", "old")
    write(here / "tree" / "fresh.txt", "new")  # not there at the look-over: nobody is asked about it
    real_run = rsyncway._run

    def someone_else_meanwhile(job: Job, arguments: list[str], meter: Meter) -> tuple[int, str, bool, set[str]]:
        if "--ignore-existing" in arguments:
            write(there / "fresh.txt", "someone else's")  # it turns up between the look-over and rsync
        return real_run(job, arguments, meter)

    monkeypatch.setattr(rsyncway, "_run", someone_else_meanwhile)
    person = Person(Answer(Choice.YES))
    job = rsync_job(rsync_server, here / "tree", str(rsync_server.folder), True, "tree", TransferOptions(), person)
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert [item.name for item in person.asked] == ["asked.txt"]
    assert read(there / "asked.txt") == "new" and read(there / "fresh.txt") == "someone else's"
    assert job.message == f"Done. {note_text('appeared', 1)}"


@pytest.mark.parametrize("sent", [True, False], ids=["a-file-arrived", "only-progress"])
def test_rsync_that_drops_after_a_file_arrived_is_a_broken_off_transfer_never_a_silent_restart(tmp_path: Path, sent: bool) -> None:
    """Whether anything arrived is told by the files rsync names as sent, never by its progress: after one did, the other way never
    takes over (it would ask again about what arrived); after only a percent of one, it does."""
    remote = Scripted(CommandResult(0, "", ""))
    remote.capabilities = Capabilities(shell=True, tools=frozenset({"rsync"}))
    mark = r"printf 'chrono-ftp-sent:file.txt\n'; " if sent else ""
    program = fake_rsync(tmp_path, mark + r"printf '   100  10%%  1MB/s    0:00:09\r'; exit 255")
    queue = TransferQueue(lambda item: None, program)
    write(tmp_path / "file.txt", "x")
    job = Job(remote, Connection(name="T", host="h"), str(tmp_path / "file.txt"), "/srv", True, "file.txt", options=TransferOptions(ask_before_replacing=False))
    try:
        queue.add(job)
        finished = threading.Event()
        threading.Thread(target=lambda: wait_for((job,), finished), daemon=True).start()
        assert finished.wait(WAIT_SECONDS)
    finally:
        queue.close()
    if sent:
        assert job.state == State.FAILED and job.way == "rsync"  # the other way did not take over
        assert job.message == DROPPED_KEPT_TEXT
    else:
        assert job.state == State.DONE and job.way != "rsync", job.message  # nothing arrived: the other way took over


@NOT_AS_ROOT
def test_after_rsync_could_not_log_in_each_failed_item_is_told_once_and_every_byte_counted_once(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here = tmp_path / "here"
    write(here / "tree" / "secret.txt", "s")
    write(here / "tree" / "open.txt", "open")
    write(here / "tree" / "kept.txt", "kept")
    write(rsync_server.folder / "tree" / "kept.txt", "old")  # refused: counted as done by the look-over, and by the other way again
    write(here / "tree" / "blocked.txt", "b")
    (rsync_server.folder / "tree" / "blocked.txt").mkdir()  # a folder in its way: it fails in the look-over and in the other way
    (here / "tree" / "secret.txt").chmod(0)
    try:
        job = rsync_job(rsync_server, here / "tree", str(rsync_server.folder), True, "tree", TransferOptions(), Person(Answer(Choice.NO)), program=fake_rsync(tmp_path, "exit 255"))
    finally:
        (here / "tree" / "secret.txt").chmod(0o644)
    assert job.way == "SFTP"
    assert job.message.startswith("Partly done: 2 items were left out (tree/blocked.txt, tree/secret.txt).")  # each once, not once per way
    assert job.done == len("open") + len("kept")  # each byte once; the unreadable one never moved


@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_a_move_never_removes_the_source_of_a_file_that_turned_up_at_the_target_however_alike_it_looks(rsync_server: RsyncServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, upload: bool) -> None:
    """Same size, same date, other bytes: only rsync knows it did not send it. A new file it did send, whatever its name, is the
    move's own: its source goes."""
    here, there = tmp_path / "here", rsync_server.folder
    source, target = (here, there) if upload else (there, here)
    odd = "line\nbreak \x01.txt"  # a name rsync can only write with escapes
    write(source / "tree" / "fresh.txt", "ours", OLD_TIME)
    write(source / "tree" / odd, "sent", OLD_TIME)
    (target / "tree").mkdir(parents=True)
    real_run = rsyncway._run

    def someone_else_meanwhile(job: Job, arguments: list[str], meter: Meter) -> tuple[int, str, bool, set[str]]:
        if "--ignore-existing" in arguments:
            write(target / "tree" / "fresh.txt", "them", OLD_TIME)  # turns up between the look-over and rsync, alike in size and date
        return real_run(job, arguments, meter)

    monkeypatch.setattr(rsyncway, "_run", someone_else_meanwhile)
    options = TransferOptions(delete_source=True)
    job = rsync_job(rsync_server, here / "tree", str(there), True, "tree", options) if upload else rsync_job(rsync_server, here, str(there / "tree"), False, "tree", options)
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert read(target / "tree" / "fresh.txt") == "them" and read(source / "tree" / "fresh.txt") == "ours"  # its source stays
    assert read(target / "tree" / odd) == "sent" and not (source / "tree" / odd).exists()  # sent: the move's own
    assert job.message == f"Done. {note_text('appeared', 1)}"


def test_a_link_that_leads_nowhere_in_a_download_fails_alone_and_the_rest_arrives(rsync_server: RsyncServer, tmp_path: Path) -> None:
    """With --copy-links rsync stops the whole list at a link it can't follow (status 23): the look-over leaves it out and names it."""
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    here.mkdir()
    write(there / "real.txt", "content")
    (there / "link.txt").symlink_to("real.txt")
    (there / "dead.txt").symlink_to("missing.txt")
    job = rsync_job(rsync_server, here, str(there), False, "tree", TransferOptions(ask_before_replacing=False))
    assert job.way == "rsync"
    assert job.state == State.FAILED and LINK_NOWHERE_TEXT in job.message and "dead.txt" in job.message, job.message
    assert read(here / "tree" / "real.txt") == "content" and read(here / "tree" / "link.txt") == "content"
    assert not os.path.lexists(here / "tree" / "dead.txt")


@pytest.mark.parametrize("confirmed", [True, False])
def test_an_rsync_upload_move_has_the_server_put_each_file_on_its_disk_before_the_original_goes(rsync_server: RsyncServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, confirmed: bool) -> None:
    here = tmp_path / "here"
    write(here / "tree" / "a.txt", "a")
    asked: list[tuple[str, bool]] = []

    def flushed(self: SshConnection, path: str) -> None:
        asked.append((path, (here / "tree" / "a.txt").exists()))  # the original is still here when the server is asked
        if not confirmed:
            raise RemoteActionFailed("The server did not answer.")

    monkeypatch.setattr(SshConnection, "make_durable", flushed)
    job = rsync_job(rsync_server, here / "tree", str(rsync_server.folder), True, "tree", TransferOptions(delete_source=True))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert asked == [(f"{rsync_server.folder}/tree/a.txt", True)]
    assert read(rsync_server.folder / "tree" / "a.txt") == "a"
    assert (here / "tree" / "a.txt").exists() != confirmed  # gone only once the server confirmed it
    assert job.message == ("" if confirmed else f"Done. {note_text('kept_unchecked', 1)}")


@pytest.mark.parametrize("server_has", ["file", "folder"])
@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_rsync_never_replaces_a_file_by_a_folder_nor_a_folder_by_a_file(rsync_server: RsyncServer, tmp_path: Path, upload: bool, server_has: str) -> None:
    """Without the question nothing else asks for a look-over: rsync would replace tree/sub of the one kind by the other. That one item
    fails alone with its text, and everything else arrives."""
    here, there = tmp_path / "here", rsync_server.folder
    source, target = (here, there) if upload else (there, here)
    write(source / "tree" / "other.txt", "other")
    if (server_has == "file") == upload:  # the target holds the file, the source the folder
        write(target / "tree" / "sub", "a file that must stay")
        write(source / "tree" / "sub" / "inner.txt", "inner")
    else:
        write(target / "tree" / "sub" / "inner.txt", "a folder that must stay")
        write(source / "tree" / "sub", "a file")
    options = TransferOptions(ask_before_replacing=False)
    job = rsync_job(rsync_server, here / "tree", str(there), True, "tree", options) if upload else rsync_job(rsync_server, here, str(there / "tree"), False, "tree", options)
    assert job.state == State.FAILED and job.way == "rsync", job.message
    target_holds_the_file = (server_has == "file") == upload
    if upload:
        said = FILE_IN_THE_WAY_TEXT if target_holds_the_file else FOLDER_IN_THE_WAY_TEXT
    else:
        said = FILE_HERE_TEXT if target_holds_the_file else FOLDER_HERE_TEXT
    assert job.message == f"Partly done: tree/sub was left out. {said}", job.message
    if target_holds_the_file:
        assert read(target / "tree" / "sub") == "a file that must stay"
    else:
        assert read(target / "tree" / "sub" / "inner.txt") == "a folder that must stay"
    assert read(target / "tree" / "other.txt") == "other"


def test_rsync_tells_a_name_the_look_over_leaves_out(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    here.mkdir()
    write(there / "back\\slash.txt", "odd")
    write(there / "ok.txt", "ok")
    job = rsync_job(rsync_server, here, str(there), False, "tree", TransferOptions())  # the question looks the job over first
    assert job.way == "rsync"
    assert job.state == State.FAILED and job.message == f"Partly done: tree/back\\slash.txt was left out. {UNSAFE_NAME_TEXT}", job.message
    assert read(here / "tree" / "ok.txt") == "ok"


@pytest.mark.parametrize("source_stays", [True, False], ids=["can-continue", "source-gone"])
@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_rsyncs_folder_of_unfinished_copies_is_tidied_once_nothing_in_it_can_continue(rsync_server: RsyncServer, tmp_path: Path, upload: bool, source_stays: bool) -> None:
    server_side, here = rsync_server.folder, tmp_path / "here"
    here.mkdir()
    content = os.urandom(4 * BIG)
    source = here / "big.bin" if upload else server_side / "big.bin"
    source.write_bytes(content)
    options = TransferOptions(resume=RESUME_ON, ask_before_replacing=False)
    if upload:
        job = rsync_job(rsync_server, source, str(server_side), True, "big.bin", options, limit=SLOW, stop_halfway=True)
    else:
        job = rsync_job(rsync_server, here, str(source), False, "big.bin", options, limit=SLOW, stop_halfway=True)
    assert job.state == State.STOPPED
    partial = (server_side if upload else here) / PARTIAL_FOLDER
    deadline = time.monotonic() + WAIT_SECONDS
    while not (partial / "big.bin").exists():
        assert time.monotonic() < deadline, "rsync kept no part"
        threading.Event().wait(0.02)
    long_ago = time.time() - 2 * LEFTOVER_SECONDS
    os.utime(partial, (long_ago, long_ago))  # nothing wrote to it for long
    if not source_stays:
        source.unlink()
    if upload:
        entries = rsync_server.connection.list_folder(str(server_side))
        tidy(rsync_server.connection, str(server_side), entries, {}, Owned(rsync_server.settings, options))
    else:
        tidy_partials_here(Job(rsync_server.connection, rsync_server.settings, str(here), str(server_side / "big.bin"), False, "big.bin", options=options))
    assert partial.exists() == source_stays


def test_an_rsync_move_removes_only_the_folders_that_arrived(rsync_server: RsyncServer, tmp_path: Path) -> None:
    """tree/sub could not go (a file of that name is in the way on the server): it stays here, also when it is empty."""
    here, there = tmp_path / "here", rsync_server.folder
    write(here / "tree" / "a.txt", "a")
    (here / "tree" / "sub").mkdir()
    (here / "tree" / "fine").mkdir()
    write(there / "tree" / "sub", "a file in the way")
    job = rsync_job(rsync_server, here / "tree", str(there), True, "tree", TransferOptions(delete_source=True, ask_before_replacing=False))
    assert job.way == "rsync" and job.state == State.FAILED, job.message
    assert (there / "tree" / "fine").is_dir() and read(there / "tree" / "a.txt") == "a"
    assert (here / "tree" / "sub").is_dir()  # its twin failed: it stays
    assert not (here / "tree" / "fine").exists() and not (here / "tree" / "a.txt").exists()


def test_a_note_of_the_look_over_is_told_once_when_the_other_way_takes_over(rsync_server: RsyncServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The look-over left a.txt as it was (its date is unknown); rsync could not log in, and the other way met it again: told once."""
    here, there = tmp_path / "here", rsync_server.folder
    write(here / "tree" / "a.txt", "new")
    write(there / "tree" / "a.txt", "old")
    listing = SshConnection.list_folder

    def no_dates(self: SshConnection, path: str) -> list[RemoteEntry]:
        return [replace(entry, modified=None) for entry in listing(self, path)]

    monkeypatch.setattr(SshConnection, "list_folder", no_dates)
    program = fake_rsync(tmp_path, "exit 255")  # rsync could not log in
    job = rsync_job(rsync_server, here / "tree", str(there), True, "tree", TransferOptions(only_newer=True, ask_before_replacing=False), program=program)
    assert job.state == State.DONE and job.message == f"Done. {note_text('unknown_dates', 1)}", job.message
    assert read(there / "tree" / "a.txt") == "old"


# --- the same truth as the file by file way


@pytest.mark.parametrize("kind", list(NOT_SENT_TEXTS))
def test_rsync_never_sends_a_picked_link_or_pipe_and_says_what_it_is(rsync_server: RsyncServer, tmp_path: Path, kind: str) -> None:
    here = tmp_path / "here"
    not_sendable(here, tmp_path / "outside")
    job = rsync_job(rsync_server, here / kind, str(rsync_server.folder), True, kind, TransferOptions(ask_before_replacing=False))
    assert job.state == State.FAILED and job.message == NOT_SENT_TEXTS[kind].format(name=kind), job.message
    assert list(rsync_server.folder.iterdir()) == []


def test_rsync_leaves_out_links_and_pipes_inside_a_folder_and_counts_them_both_ways(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here, there = tmp_path / "here", rsync_server.folder
    write(here / "box" / "a.txt", "a")
    not_sendable(here / "box", tmp_path / "outside")
    up_job = rsync_job(rsync_server, here / "box", str(there), True, "box", TransferOptions(ask_before_replacing=False))
    assert up_job.state == State.DONE and up_job.way == "rsync", up_job.message
    assert note_text("links_left", 2) in up_job.message and note_text("others_left", 1) in up_job.message, up_job.message
    assert sorted(path.name for path in (there / "box").iterdir()) == ["a.txt"]
    write(there / "real" / "b.txt", "b")
    (there / "box" / "folder-link").symlink_to("../real")
    (there / "folder-link").symlink_to("real")
    back = tmp_path / "back"
    back.mkdir()
    picked = rsync_job(rsync_server, back, str(there / "folder-link"), False, "folder-link", TransferOptions(ask_before_replacing=False))
    inside = rsync_job(rsync_server, back, str(there / "box"), False, "box", TransferOptions(ask_before_replacing=False))
    assert picked.state == State.FAILED and picked.message == LINK_TO_FOLDER_DOWN_TEXT.format(name="folder-link"), picked.message
    assert inside.state == State.DONE and inside.way == "rsync" and inside.message == f"Done. {note_text('links_left', 1)}", inside.message
    assert sorted(path.name for path in back.iterdir()) == ["box"] and sorted(path.name for path in (back / "box").iterdir()) == ["a.txt"]


@pytest.mark.parametrize("move", [False, True], ids=["copy", "move"])
@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_rsync_never_replaces_a_file_that_changed_after_the_person_agreed(rsync_server: RsyncServer, tmp_path: Path, upload: bool, move: bool) -> None:
    """The question showed the file there; while the person thought, something else wrote to it. rsync leaves it as it is now, the
    other file goes, and for a move the source of the one left stays."""
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    source, target = (here / "tree", there) if upload else (there, here / "tree")
    write(source / "a.txt", "mine")
    write(source / "b.txt", "b")
    write(target / "a.txt", "old")

    def someone_else() -> None:
        write(target / "a.txt", "someone else's newer text")

    person = MeanwhileOnTheServer(someone_else)
    options = TransferOptions(delete_source=move)
    if upload:
        job = rsync_job(rsync_server, here / "tree", str(rsync_server.folder), True, "tree", options, person)
        said = CHANGED_TO_REPLACE_TEXT.format(name="a.txt")
    else:
        job = rsync_job(rsync_server, here, str(there), False, "tree", options, person)
        said = CHANGED_HERE_NOT_REPLACED_TEXT.format(name="a.txt")
    assert job.way == "rsync" and len(person.asked) == 1
    assert job.state == State.FAILED and job.message == f"Partly done: tree/a.txt was left out. {said}", job.message
    assert read(target / "a.txt") == "someone else's newer text" and read(target / "b.txt") == "b"
    assert read(source / "a.txt") == "mine" and (source / "b.txt").exists() != move


def test_rsync_never_fetches_a_pipe_on_the_server_nor_makes_one_here(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here, there = tmp_path / "here", rsync_server.folder
    pipe_on_the_server(there)
    here.mkdir()
    picked = rsync_job(rsync_server, here, str(there / "pipe"), False, "pipe", TransferOptions(ask_before_replacing=False))
    inside = rsync_job(rsync_server, here, str(there / "box"), False, "box", TransferOptions(ask_before_replacing=False))
    assert picked.state == State.FAILED and picked.message == NOT_A_FILE_DOWN_TEXT.format(name="pipe"), picked.message
    assert inside.state == State.DONE and inside.way == "rsync" and inside.message == f"Done. {note_text('others_left', 1)}", inside.message
    assert sorted(path.name for path in here.iterdir()) == ["box"] and sorted(path.name for path in (here / "box").iterdir()) == ["a.txt"]


def test_rsync_never_replaces_a_pipe_on_the_server_also_unasked(rsync_server: RsyncServer, tmp_path: Path) -> None:
    """Without the question nothing else asks for a look-over: the pipe on the server does, and it is left out with its text."""
    here, there = tmp_path / "here", rsync_server.folder
    pipes_in_the_way(there, here)
    options = TransferOptions(ask_before_replacing=False)
    picked = rsync_job(rsync_server, here / "p", str(there), True, "p", options)
    inside = rsync_job(rsync_server, here / "box", str(there), True, "box", options)
    said = NOT_A_FILE_THERE_TEXT.format(name="p")
    assert picked.state == State.FAILED and picked.message == said, picked.message
    assert inside.way == "rsync" and inside.state == State.FAILED and inside.message == f"Partly done: box/p was left out. {said}", inside.message
    assert pipes_still_there(there) and read(there / "box" / "a.txt") == "a"


def test_rsync_never_replaces_a_pipe_here_also_unasked(rsync_server: RsyncServer, tmp_path: Path) -> None:
    """Without the question nothing else asks for a look-over: the pipe here does, and it is left out with its text."""
    here, there = tmp_path / "here", rsync_server.folder
    pipes_here(there, here)
    options = TransferOptions(ask_before_replacing=False)
    picked = rsync_job(rsync_server, here, str(there / "p"), False, "p", options)
    inside = rsync_job(rsync_server, here, str(there / "box"), False, "box", options)
    said = NOT_A_FILE_HERE_TEXT.format(name="p")
    assert picked.state == State.FAILED and picked.message == said, picked.message
    assert inside.way == "rsync" and inside.state == State.FAILED and inside.message == f"Partly done: box/p was left out. {said}", inside.message
    assert pipes_still_there(here) and read(here / "box" / "a.txt") == "a"


@pytest.mark.parametrize("move", [False, True], ids=["copy", "move"])
@pytest.mark.parametrize("upload", [True, False], ids=["upload", "download"])
def test_after_rsync_could_not_log_in_an_answer_holds_only_for_the_file_the_person_saw(rsync_server: RsyncServer, tmp_path: Path, upload: bool, move: bool) -> None:
    """The person agreed to replace a.txt; while rsync tried to log in, something else wrote to it. The other way leaves it as it is now."""
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    source, target = (here / "tree", there) if upload else (there, here / "tree")
    write(source / "a.txt", "mine")
    write(source / "b.txt", "b")
    write(target / "a.txt", "old")
    meanwhile = f"printf %s {shlex.quote("someone else's newer text")} > {shlex.quote(str(target / "a.txt"))}; exit 255"
    person = Person(Answer(Choice.YES))
    options = TransferOptions(delete_source=move)
    if upload:
        job = rsync_upload(rsync_server, here, person, options, fake_rsync(tmp_path, meanwhile))
        said = CHANGED_TO_REPLACE_TEXT.format(name="a.txt")
    else:
        job = rsync_job(rsync_server, here, str(there), False, "tree", options, person, program=fake_rsync(tmp_path, meanwhile))
        said = CHANGED_HERE_NOT_REPLACED_TEXT.format(name="a.txt")
    assert job.way == "SFTP" and len(person.asked) == 1, job.way
    assert job.state == State.FAILED and job.message == f"Partly done: tree/a.txt was left out. {said}", job.message
    assert read(target / "a.txt") == "someone else's newer text" and read(target / "b.txt") == "b"
    assert read(source / "a.txt") == "mine" and (source / "b.txt").exists() != move


def test_a_move_keeps_the_source_of_a_file_left_out_of_rsyncs_list_also_when_a_copy_like_it_turned_up(rsync_server: RsyncServer, tmp_path: Path) -> None:
    """While the person thought, something else put a file there just like the source (its bytes and date). rsync did not send it, so
    this move has nothing to remove the source for: it stays."""
    here, there = tmp_path / "here", rsync_server.folder / "tree"
    source = write(here / "tree" / "a.txt", "mine", OLD_TIME)
    write(here / "tree" / "b.txt", "b")
    write(there / "a.txt", "old")

    def someone_else() -> None:
        write(there / "a.txt", "mine", OLD_TIME)

    person = MeanwhileOnTheServer(someone_else)
    job = rsync_upload(rsync_server, here, person, TransferOptions(delete_source=True))
    assert job.way == "rsync" and job.state == State.FAILED and job.message == f"Partly done: tree/a.txt was left out. {CHANGED_TO_REPLACE_TEXT.format(name='a.txt')}", job.message
    assert read(source) == "mine" and not (here / "tree" / "b.txt").exists()


@NOT_AS_ROOT
def test_an_upload_into_a_folder_that_takes_files_but_can_not_be_read_goes_file_by_file(rsync_server: RsyncServer, tmp_path: Path) -> None:
    drop = rsync_server.folder / "drop"
    drop.mkdir()
    write(tmp_path / "here" / "a.txt", "mine")
    drop.chmod(0o300)  # a drop box: files go in, nothing in it can be seen
    try:
        job = rsync_job(rsync_server, tmp_path / "here" / "a.txt", str(drop), True, "a.txt", TransferOptions(ask_before_replacing=False))
    finally:
        drop.chmod(0o700)
    assert job.state == State.DONE and job.way == "SFTP", job.message
    assert read(drop / "a.txt") == "mine"

