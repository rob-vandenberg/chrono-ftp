"""Block FD in transfers, against the fake SFTP and FTP servers, both ways: the file mask, hidden files and empty folders (FD-01), text
files with their line endings (FD-02) and names (FD-03), file by file; and on the rsync way the exact list of what passes, with what
rsync can't do handed to the file by file way."""

import os
from dataclasses import replace
from pathlib import Path

import pytest

from chrono_ftp.core.transferoptions import AUTOMATIC, CASE_LOWER, TEXT, TransferOptions
from chrono_ftp.transfers import conversion
from chrono_ftp.transfers.job import State
from chrono_ftp.transfers.options import Answer, Choice, TEXT_NO_APPEND
from chrono_ftp.transfers.queue import Job, TransferQueue, note_text
from tests.test_transfer_options import NEWER_TIME, OLD_TIME, Person, Server, down, read, server, up, write
from tests.test_transfer_queue import until
from tests.test_transfer_rsync import RsyncServer, rsync_job, rsync_server

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_masks.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: masks, text files and names in transfers, file by file over SFTP and FTP and beside rsync (FD-01 to FD-03);
#          review: a retry never asks again about a text that went already, both ways; two names of one transfer that become one never
#          write over each other, with or without the question and a move, both ways, also beside rsync; what the person picked always
#          goes, the mask deciding only inside a picked folder

__all__ = ["rsync_server", "server"]  # the fixtures, used by name
CRLF_TEXT: bytes = b"one\r\ntwo\r\n"
LF_TEXT: bytes = b"one\ntwo\n"
LEFT_OUT: str = "left_by_choice"


def tree(base: Path) -> Path:
    """A folder with text, a picture, a hidden file, a hidden folder and an empty folder."""
    write(base / "tree" / "a.txt", "a")
    write(base / "tree" / "deep" / "b.txt", "b")
    write(base / "tree" / "photo.jpg", "jpg")
    write(base / "tree" / ".hidden.txt", "h")
    write(base / "tree" / ".git" / "config.txt", "c")
    (base / "tree" / "empty").mkdir()
    write(base / "tree" / "only-jpg" / "x.jpg", "x")
    return base / "tree"


def names_under(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*"))


def remote_root(server: Server) -> Path:
    return server[2]


# --- the file mask, hidden files and empty folders (FD-01) ------------------------------------------------------------------------------


def test_an_upload_sends_only_what_passes_and_tells_what_was_left_out(server: Server, tmp_path: Path) -> None:
    source = tree(tmp_path / "here")
    job = up(server, source, TransferOptions(file_mask="*.txt", skip_hidden=True, skip_empty_folders=True))
    assert job.state == State.DONE, job.message
    assert names_under(remote_root(server) / "tree") == ["a.txt", "deep", "deep/b.txt"]
    assert note_text(LEFT_OUT, 5) in job.message  # photo.jpg, .hidden.txt, .git, empty and only-jpg
    assert job.total == 2  # what is left out is not counted either


def test_a_download_fetches_only_what_passes(server: Server, tmp_path: Path) -> None:
    tree(remote_root(server))
    here = tmp_path / "here"
    here.mkdir()
    job = down(server, "/tree", here, TransferOptions(file_mask="*.txt | b*", skip_hidden=True))
    assert job.state == State.DONE, job.message
    assert names_under(here / "tree") == ["a.txt", "deep", "empty", "only-jpg"]  # without "skip empty folders" they are made
    assert job.total == 1


def test_a_mask_with_a_folder_pattern_and_a_size(server: Server, tmp_path: Path) -> None:
    source = tree(tmp_path / "here")
    write(source / "deep" / "big.txt", "x" * 2000)
    job = up(server, source, TransferOptions(file_mask="*>1K | deep/"))
    assert job.state == State.DONE, job.message
    assert not (remote_root(server) / "tree" / "deep").exists()  # the folder is left out with all it holds
    assert names_under(remote_root(server) / "tree") == [".git", "empty", "only-jpg"]  # folders hold no file over 1 KB, and are made all the same


def test_a_picked_file_the_mask_would_leave_out_still_goes(server: Server, tmp_path: Path) -> None:
    source = write(tmp_path / "here" / "photo.jpg", "jpg")
    job = up(server, source, TransferOptions(file_mask="*.txt", skip_hidden=True))
    assert job.state == State.DONE and job.message == "", job.message  # the person chose it
    assert read(remote_root(server) / "photo.jpg") == "jpg"


def test_the_mask_decides_only_what_goes_from_inside_a_picked_folder(server: Server, tmp_path: Path) -> None:
    source = tree(tmp_path / "here")
    job = up(server, source, TransferOptions(file_mask="deep/"))
    assert job.state == State.DONE, job.message
    assert names_under(remote_root(server) / "tree") == [".hidden.txt", "a.txt", "deep", "deep/b.txt", "photo.jpg"]  # the files of tree, and deep
    back = tmp_path / "back"
    back.mkdir()
    job = down(server, "/tree", back, TransferOptions(file_mask="deep/"))
    assert job.state == State.DONE, job.message
    assert (back / "tree" / "deep" / "b.txt").exists() and not (back / "tree" / "empty").exists()


def test_a_mask_that_cant_be_read_sends_nothing_and_says_why(server: Server, tmp_path: Path) -> None:
    source = write(tmp_path / "here" / "a.txt", "a")
    job = up(server, source, TransferOptions(file_mask="*>1Q"))
    assert job.state == State.FAILED and "can't be read" in job.message
    assert not (remote_root(server) / "a.txt").exists()


# --- text files (FD-02) -----------------------------------------------------------------------------------------------------------------


def test_a_text_upload_arrives_with_the_servers_line_endings_and_the_source_unchanged(server: Server, tmp_path: Path) -> None:
    source = tmp_path / "here" / "a.txt"
    source.parent.mkdir()
    source.write_bytes(conversion.BOM + CRLF_TEXT)
    os.utime(source, (OLD_TIME, OLD_TIME))
    job = up(server, source, TransferOptions(transfer_mode=TEXT, remove_bom=True))
    assert job.state == State.DONE, job.message
    assert (remote_root(server) / "a.txt").read_bytes() == LF_TEXT
    assert source.read_bytes() == conversion.BOM + CRLF_TEXT
    assert job.percent == 100
    assert not any(conversion.converted_folder().glob(f"{os.getpid()}-*"))  # the converted copy of our own is gone


def test_a_text_download_arrives_with_this_computers_line_endings(server: Server, tmp_path: Path) -> None:
    (remote_root(server) / "a.txt").write_bytes(CRLF_TEXT)
    here = tmp_path / "here"
    here.mkdir()
    job = down(server, "/a.txt", here, TransferOptions(transfer_mode=TEXT))
    assert job.state == State.DONE, job.message
    assert (here / "a.txt").read_bytes() == LF_TEXT
    assert names_under(here) == ["a.txt"]  # no part, no converted copy left behind


def test_the_automatic_mode_converts_only_the_text_files(server: Server, tmp_path: Path) -> None:
    for name in ("a.txt", "b.bin"):
        (remote_root(server) / name).write_bytes(CRLF_TEXT)
    here = tmp_path / "here"
    here.mkdir()
    for name in ("a.txt", "b.bin"):
        assert down(server, f"/{name}", here, TransferOptions(transfer_mode=AUTOMATIC)).state == State.DONE
    assert (here / "a.txt").read_bytes() == LF_TEXT and (here / "b.bin").read_bytes() == CRLF_TEXT


def test_binary_changes_nothing(server: Server, tmp_path: Path) -> None:
    source = tmp_path / "here" / "a.txt"
    source.parent.mkdir()
    source.write_bytes(conversion.BOM + CRLF_TEXT)
    assert up(server, source).state == State.DONE
    assert (remote_root(server) / "a.txt").read_bytes() == conversion.BOM + CRLF_TEXT


def test_a_text_move_removes_its_source_only_once_the_converted_copy_arrived(server: Server, tmp_path: Path) -> None:
    source = tmp_path / "here" / "a.txt"
    source.parent.mkdir()
    source.write_bytes(CRLF_TEXT)
    job = up(server, source, TransferOptions(transfer_mode=TEXT, delete_source=True))
    assert job.state == State.DONE, job.message
    assert (remote_root(server) / "a.txt").read_bytes() == LF_TEXT and not source.exists()
    (remote_root(server) / "b.txt").write_bytes(CRLF_TEXT)
    job = down(server, "/b.txt", tmp_path / "here", TransferOptions(transfer_mode=TEXT, delete_source=True))
    assert job.state == State.DONE, job.message
    assert (tmp_path / "here" / "b.txt").read_bytes() == LF_TEXT and not (remote_root(server) / "b.txt").exists()


def test_a_text_that_replaces_a_file_is_asked_about_without_append_or_resume(server: Server, tmp_path: Path) -> None:
    source = write(tmp_path / "here" / "a.txt", "new\r\n", NEWER_TIME)
    write(remote_root(server) / "a.txt", "old\n", OLD_TIME)
    person = Person(Answer(Choice.YES))
    job = up(server, source, TransferOptions(transfer_mode=TEXT), person)
    assert job.state == State.DONE, job.message
    assert person.asked[0].no_append == TEXT_NO_APPEND and person.asked[0].part_size is None
    assert read(remote_root(server) / "a.txt") == "new\n"


def test_a_large_text_with_resume_on_goes_whole_and_leaves_nothing_beside_it(server: Server, tmp_path: Path) -> None:
    """A text always goes whole, and its size is checked against its converted copy."""
    source = tmp_path / "here" / "big.txt"
    source.parent.mkdir()
    source.write_bytes(b"line\r\n" * 200_000)
    job = up(server, source, TransferOptions(transfer_mode=TEXT, resume="on"))
    assert job.state == State.DONE
    assert names_under(remote_root(server)) == ["big.txt"]
    assert (remote_root(server) / "big.txt").stat().st_size == 5 * 200_000


# --- names (FD-03) ----------------------------------------------------------------------------------------------------------------------


def test_names_take_their_converted_form_both_ways(server: Server, tmp_path: Path) -> None:
    write(tmp_path / "here" / "Tree" / "Read Me.TXT", "a")
    write(tmp_path / "here" / "Tree" / "Sub" / "B.txt", "b")
    job = up(server, tmp_path / "here" / "Tree", TransferOptions(name_case=CASE_LOWER))
    assert job.state == State.DONE, job.message
    assert names_under(remote_root(server) / "tree") == ["read me.txt", "sub", "sub/b.txt"]
    write(remote_root(server) / "odd" / "a:b.txt", "c")
    back = tmp_path / "back"
    back.mkdir()
    job = down(server, "/odd", back, TransferOptions(replace_characters=True))
    assert job.state == State.DONE, job.message
    assert names_under(back) == ["odd", "odd/a%3Ab.txt"]


@pytest.mark.parametrize("ask", [True, False])
@pytest.mark.parametrize("move", [False, True])
@pytest.mark.parametrize("upload", [True, False])
def test_two_names_of_one_transfer_that_become_one_never_write_over_each_other(server: Server, tmp_path: Path, ask: bool, move: bool, upload: bool) -> None:
    here = tmp_path / "here"
    source, target = (here, remote_root(server)) if upload else (remote_root(server), here)
    write(source / "pair" / "A.txt", "first")
    write(source / "pair" / "a.txt", "second")
    write(source / "pair" / "Sub" / "x.txt", "x1")
    write(source / "pair" / "sub" / "y.txt", "y2")
    here.mkdir(exist_ok=True)
    person = Person(Answer(Choice.YES), Answer(Choice.YES))
    options = TransferOptions(name_case=CASE_LOWER, ask_before_replacing=ask, delete_source=move)
    job = up(server, here / "pair", options, person) if upload else down(server, "/pair", here, options, person)
    assert job.state == State.FAILED and "Partly done" in job.message and "this one was not written over it" in job.message
    assert person.asked == []  # what this transfer wrote is never asked about as an old file
    files = {"first": "A.txt", "second": "a.txt"}  # which of the two came first depends on the order of the listing: it stays whole
    winner = files[read(target / "pair" / "a.txt")]
    loser = "a.txt" if winner == "A.txt" else "A.txt"
    assert read(source / "pair" / loser) == ("first" if loser == "A.txt" else "second")  # what was left out stays, also on a move
    assert (source / "pair" / winner).exists() is not move
    inside = sorted(path.name for path in (target / "pair" / "sub").iterdir())
    assert inside in (["x.txt"], ["y.txt"])  # the second folder of the same name was left out with what it holds
    left = ("Sub", "x.txt") if inside == ["y.txt"] else ("sub", "y.txt")
    assert (source / "pair" / left[0] / left[1]).exists()


def test_a_file_that_was_there_before_follows_the_question(server: Server, tmp_path: Path) -> None:
    write(tmp_path / "here" / "pair" / "A.txt", "new")
    write(remote_root(server) / "pair" / "a.txt", "old")
    person = Person(Answer(Choice.YES))
    job = up(server, tmp_path / "here" / "pair", TransferOptions(name_case=CASE_LOWER), person)
    assert job.state == State.DONE, job.message
    assert len(person.asked) == 1 and read(remote_root(server) / "pair" / "a.txt") == "new"


def test_beside_rsync_two_names_that_become_one_never_write_over_each_other(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here = tmp_path / "here"
    write(here / "pair" / "A.txt", "first")
    write(here / "pair" / "a.txt", "second")
    job = rsync_job(rsync_server, here / "pair", str(rsync_server.folder), True, "pair", TransferOptions(ask_before_replacing=False, name_case=CASE_LOWER, delete_source=True))
    assert job.state == State.FAILED and "this one was not written over it" in job.message
    assert read(rsync_server.folder / "pair" / "a.txt") == "first"
    assert read(here / "pair" / "a.txt") == "second" and not (here / "pair" / "A.txt").exists()


# --- beside rsync -----------------------------------------------------------------------------------------------------------------------


def test_rsync_gets_the_exact_list_of_what_passes(rsync_server: RsyncServer, tmp_path: Path) -> None:
    source = tree(tmp_path / "here")
    job = rsync_job(rsync_server, source, str(rsync_server.folder), True, "tree", TransferOptions(ask_before_replacing=False, file_mask="*.txt", skip_hidden=True, skip_empty_folders=True))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert names_under(rsync_server.folder / "tree") == ["a.txt", "deep", "deep/b.txt"]


def test_beside_rsync_a_text_and_a_new_name_go_file_by_file(rsync_server: RsyncServer, tmp_path: Path) -> None:
    here = tmp_path / "here"
    (here / "Site" / "New").mkdir(parents=True)
    (here / "Site" / "index.html").write_bytes(CRLF_TEXT)
    (here / "Site" / "logo.png").write_bytes(CRLF_TEXT)
    (here / "Site" / "New" / "page.html").write_bytes(CRLF_TEXT)
    options = TransferOptions(ask_before_replacing=False, transfer_mode=AUTOMATIC, name_case=CASE_LOWER)
    job = rsync_job(rsync_server, here / "Site", str(rsync_server.folder), True, "Site", options)
    assert job.state == State.DONE and job.way == "rsync", job.message
    there = rsync_server.folder / "site"
    assert names_under(there) == ["index.html", "logo.png", "new", "new/page.html"]
    assert (there / "index.html").read_bytes() == LF_TEXT and (there / "new" / "page.html").read_bytes() == LF_TEXT
    assert (there / "logo.png").read_bytes() == CRLF_TEXT


def test_beside_rsync_a_text_download_is_converted_and_the_rest_comes_by_rsync(rsync_server: RsyncServer, tmp_path: Path) -> None:
    there = rsync_server.folder / "site"
    there.mkdir()
    (there / "a.txt").write_bytes(CRLF_TEXT)
    (there / "b.bin").write_bytes(CRLF_TEXT)
    (there / "deep").mkdir()
    (there / "deep" / "c.txt").write_bytes(CRLF_TEXT)
    here = tmp_path / "here"
    here.mkdir()
    job = rsync_job(rsync_server, here, str(there), False, "site", replace(TransferOptions(), ask_before_replacing=False, transfer_mode=AUTOMATIC))
    assert job.state == State.DONE and job.way == "rsync", job.message
    assert (here / "site" / "a.txt").read_bytes() == LF_TEXT and (here / "site" / "deep" / "c.txt").read_bytes() == LF_TEXT
    assert (here / "site" / "b.bin").read_bytes() == CRLF_TEXT


def test_a_retry_never_asks_again_about_a_text_that_went_already(server: Server, tmp_path: Path) -> None:
    """The first run converts a.txt and fails on b.txt alone; the retry finds a.txt at its converted size and leaves it, unasked."""
    connection, settings, root = server
    here = tmp_path / "here"
    for upload in (True, False):
        source, target = (here, root) if upload else (root, here)
        name = f"tree-{upload}"
        (source / name).mkdir(parents=True)
        (source / name / "a.txt").write_bytes(CRLF_TEXT)
        (source / name / "b.txt").write_bytes(CRLF_TEXT)
        (target / name / "b.txt").mkdir(parents=True)  # in the way: b.txt fails alone in the first run
        person = Person()
        queue = TransferQueue(lambda item: None, ask=person)
        local, remote = (str(here / name), "/") if upload else (str(here), f"/{name}")
        job = Job(connection, settings, local, remote, upload, name, has_password=True, options=TransferOptions(transfer_mode=TEXT))
        try:
            queue.add(job)
            until(lambda: job.state == State.FAILED)
            assert (target / name / "a.txt").read_bytes() == LF_TEXT and person.asked == []
            (target / name / "b.txt").rmdir()
            queue.retry(job)
            until(lambda: job.state == State.DONE)
        finally:
            queue.close()
        assert person.asked == [], [item.name for item in person.asked]
        assert (target / name / "b.txt").read_bytes() == LF_TEXT
