import os
import sys
import threading
from collections.abc import Generator
from pathlib import Path
from typing import cast, override

import pytest

from chrono_ftp.config import parts
from chrono_ftp.connections.base import Capabilities, Limit, Progress, RemoteConnection
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core.connection import FTP, SFTP, Connection
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers import split
from chrono_ftp.transfers.options import SPLIT, usable
from chrono_ftp.transfers.queue import PART_SUFFIX, Job, State, TransferQueue
from chrono_ftp.core.errors import ConnectionLost, TransferInterrupted
from chrono_ftp.transfers.split import Spans, move_in_parts, pieces_of
from tests.fakeservers import PASSWORD, USER, ftp_server, ssh_server
from tests.test_connections import trusted_ssh
from tests.test_transfer_queue import until

# --- Version ------------------------------------------------------------
__version__ = "test_transfer_split.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial (FC-05): a large file split in parts arrives whole (SFTP down and up, FTP down); an FTP upload is never split; a pause
#          and a resume of a split file continue each range; a stop; a part whose line drops; a server that takes fewer logins gives
#          fewer parts; the ranges of a part's note; the option is greyed where it can't be done; a drop now and then never loses a
#          part; a piece given back late is finished; any answer to a range ended early is no failure; a further part with a drop now
#          and then finishes the file while the item's own login can't

SIZE: int = 4 * 1024 * 1024
SPLITTING: TransferOptions = TransferOptions(ask_before_replacing=False, split_large=True)
SLOW: int = 256  # KB per second for the whole item: four parts share it, so nothing ends before the test acts


@pytest.fixture(autouse=True)
def small_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(split, "SPLIT_BYTES", 1024 * 1024)  # a file of a few MB counts as large here


@pytest.fixture
def four_at_once() -> Generator[TransferQueue]:
    made = TransferQueue(lambda item: None)
    made.settings(True, 15.0, False, 4)
    yield made
    made.close()


def ranges_by(kind: type[RemoteConnection], monkeypatch: pytest.MonkeyPatch, upload: bool = False) -> list[tuple[int, int, int, int]]:
    """Every range moved: by which connection (its id), from where to where, and how many bytes it confirmed."""
    seen: list[tuple[int, int, int, int]] = []
    lock = threading.Lock()
    name = "upload_range" if upload else "download_range"
    real = getattr(kind, name)

    def noted(self: RemoteConnection, first: str, second: str, start: int, end: int, progress: Progress, limit: Limit = 0) -> None:
        moved = [0]

        def counted(amount: int) -> None:
            moved[0] += amount
            progress(amount)

        try:
            real(self, first, second, start, end, counted, limit)
        finally:
            with lock:
                seen.append((id(self), start, end, moved[0]))

    monkeypatch.setattr(kind, name, noted)
    return seen


def big(folder: Path, name: str = "big.bin") -> Path:
    path = folder / name
    path.write_bytes(os.urandom(SIZE))
    return path


@pytest.fixture
def sftp(tmp_path: Path) -> Generator[tuple[SshConnection, Path]]:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        connection = trusted_ssh(server)
        yield connection, root
        connection.close()


@pytest.fixture
def ftp_root(tmp_path: Path) -> Path:
    root = tmp_path / "server"
    root.mkdir()
    return root


def ftp_login(port: int) -> tuple[FtpConnection, Connection]:
    settings = Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)
    connection = FtpConnection(settings, PASSWORD)
    connection.connect()
    return connection, settings


# --- each function does what it should


def test_an_sftp_download_goes_in_parts_and_arrives_whole(sftp: tuple[SshConnection, Path], tmp_path: Path, four_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, root = sftp
    seen = ranges_by(SshConnection, monkeypatch)
    big(root)
    here = tmp_path / "here"
    here.mkdir()
    item = Job(connection, connection.connection, str(here), "/big.bin", False, "big.bin", has_password=True, options=SPLITTING)
    four_at_once.add(item)
    until(lambda: item.state in (State.DONE, State.FAILED))
    assert item.state == State.DONE and (here / "big.bin").read_bytes() == (root / "big.bin").read_bytes()
    assert len({who for who, _start, _end, _moved in seen}) == 4  # its own login and three more
    assert four_at_once.jobs == [item] and not (here / f"big.bin{PART_SUFFIX}").exists()  # one item, and no part left


def test_an_sftp_upload_goes_in_parts_and_arrives_whole(sftp: tuple[SshConnection, Path], tmp_path: Path, four_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, root = sftp
    seen = ranges_by(SshConnection, monkeypatch, upload=True)
    source = big(tmp_path)
    item = Job(connection, connection.connection, str(source), "/", True, "big.bin", has_password=True, options=SPLITTING)
    four_at_once.add(item)
    until(lambda: item.state in (State.DONE, State.FAILED))
    assert item.state == State.DONE and (root / "big.bin").read_bytes() == source.read_bytes()
    assert len({who for who, _start, _end, _moved in seen}) > 1


def test_an_ftp_download_goes_in_parts_and_arrives_whole(ftp_root: Path, tmp_path: Path, four_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = ranges_by(FtpConnection, monkeypatch)
    big(ftp_root)
    here = tmp_path / "here"
    here.mkdir()
    with ftp_server(ftp_root) as port:
        connection, settings = ftp_login(port)
        item = Job(connection, settings, str(here), "/big.bin", False, "big.bin", has_password=True, options=SPLITTING)
        four_at_once.add(item)
        until(lambda: item.state in (State.DONE, State.FAILED))
        connection.close()
    assert item.state == State.DONE and (here / "big.bin").read_bytes() == (ftp_root / "big.bin").read_bytes()
    assert len({who for who, _start, _end, _moved in seen}) > 1


def test_an_ftp_upload_is_never_split(ftp_root: Path, tmp_path: Path, four_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    lent: list[Job] = []
    def noted(_queue: TransferQueue, job: Job) -> None:
        lent.append(job)

    monkeypatch.setattr(TransferQueue, "lend", noted)
    source = big(tmp_path)
    with ftp_server(ftp_root) as port:
        connection, settings = ftp_login(port)
        assert usable(SPLITTING, connection.capabilities, FTP, True)[0].split_large is False  # greyed: never chosen for it
        item = Job(connection, settings, str(source), "/", True, "big.bin", has_password=True, options=SPLITTING)  # chosen all the same
        four_at_once.add(item)
        until(lambda: item.state in (State.DONE, State.FAILED))
        connection.close()
    assert item.state == State.DONE and (ftp_root / "big.bin").read_bytes() == source.read_bytes() and lent == []


# --- what a person does with it


def test_a_paused_split_download_continues_each_part_and_arrives_whole(sftp: tuple[SshConnection, Path], tmp_path: Path, four_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    connection, root = sftp
    seen = ranges_by(SshConnection, monkeypatch)
    big(root)
    here = tmp_path / "here"
    here.mkdir()
    item = Job(connection, connection.connection, str(here), "/big.bin", False, "big.bin", has_password=True, options=SPLITTING, limit=SLOW)
    four_at_once.add(item)
    until(lambda: item.done > SIZE // 8)
    four_at_once.pause(item)
    until(lambda: item.state == State.PAUSED)
    note = parts.lookup(parts.download_key(str(here / f"big.bin{PART_SUFFIX}")))
    assert note is not None and note.part_size > 0 and note.ranges  # the first bytes, and the ranges the other parts confirmed
    kept = note.part_size + sum(end - start for start, end in note.ranges)
    seen.clear()
    four_at_once.set_limit(item, 0)
    four_at_once.resume(item)
    until(lambda: item.state in (State.DONE, State.FAILED))
    assert item.state == State.DONE and (here / "big.bin").read_bytes() == (root / "big.bin").read_bytes()
    assert sum(moved for _who, _start, _end, moved in seen) <= SIZE - kept  # nothing confirmed before came again


def test_a_stopped_split_download_gives_back_every_login(sftp: tuple[SshConnection, Path], tmp_path: Path, four_at_once: TransferQueue) -> None:
    connection, root = sftp
    big(root)
    here = tmp_path / "here"
    here.mkdir()
    item = Job(connection, connection.connection, str(here), "/big.bin", False, "big.bin", has_password=True, options=SPLITTING, limit=SLOW)
    four_at_once.add(item)
    until(lambda: item.done > 0)
    four_at_once.stop(item)
    until(lambda: item.state == State.STOPPED)
    assert four_at_once._lent == {} and not (here / "big.bin").exists()


# --- problems from outside


def test_a_part_whose_line_drops_is_taken_on_and_the_file_arrives_whole(ftp_root: Path, tmp_path: Path, four_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = ranges_by(FtpConnection, monkeypatch)
    big(ftp_root)
    here = tmp_path / "here"
    here.mkdir()
    with ftp_server(ftp_root, drops_data_after=SIZE // 16) as port:  # the first part's data connection drops on the way
        connection, settings = ftp_login(port)
        item = Job(connection, settings, str(here), "/big.bin", False, "big.bin", has_password=True, options=SPLITTING)
        four_at_once.add(item)
        until(lambda: item.state in (State.DONE, State.FAILED))
        connection.close()
    assert (item.state, item.message) == (State.DONE, "") and (here / "big.bin").read_bytes() == (ftp_root / "big.bin").read_bytes()
    assert any(moved < end - start for _who, start, end, moved in seen)  # one range broke off on the way, and was finished all the same


def test_a_server_that_takes_fewer_logins_gives_fewer_parts(ftp_root: Path, tmp_path: Path, four_at_once: TransferQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = ranges_by(FtpConnection, monkeypatch)
    big(ftp_root)
    here = tmp_path / "here"
    here.mkdir()
    with ftp_server(ftp_root, most_logins=3) as port:  # the tab's login and two more
        connection, settings = ftp_login(port)
        item = Job(connection, settings, str(here), "/big.bin", False, "big.bin", has_password=True, options=SPLITTING)
        four_at_once.add(item)
        until(lambda: item.state in (State.DONE, State.FAILED))
        connection.close()
    assert (item.state, item.message) == (State.DONE, "") and (here / "big.bin").read_bytes() == (ftp_root / "big.bin").read_bytes()
    assert len({who for who, _start, _end, _moved in seen}) == 2  # its own and one more: the refusal of a third was no failure


# --- the rules themselves


def test_the_option_is_greyed_where_a_connection_can_not_split() -> None:
    assert SPLIT in usable(SPLITTING, Capabilities(resume=True), FTP, True)[1]  # FTP can't write at a place in the middle
    assert SPLIT in usable(SPLITTING, Capabilities(resume=False), FTP, False)[1]  # nor start at one without REST
    assert SPLIT not in usable(SPLITTING, Capabilities(resume=True), FTP, False)[1]
    assert SPLIT not in usable(SPLITTING, Capabilities(), SFTP, True)[1]


def test_the_ranges_confirmed_and_what_is_left() -> None:
    spans = Spans(10, ((20, 30),))
    spans.add(10, 5)
    spans.add(30, 10)
    assert (spans.first(), spans.rest(), spans.gaps(0, 50)) == (15, ((20, 40),), [(15, 20), (40, 50)])
    assert pieces_of([(0, 4 * 1024 * 1024)], 2) == [(0, 1024 * 1024), (1024 * 1024, 2 * 1024 * 1024), (2 * 1024 * 1024, 3 * 1024 * 1024), (3 * 1024 * 1024, 4 * 1024 * 1024)]


def test_a_part_s_note_keeps_its_ranges_and_ranges_that_do_not_fit_count_as_none() -> None:
    key = parts.download_key("/somewhere/a.bin.chrono-ftp-part")
    record = parts.PartRecord("source", 100, 1.0, 10, ranges=((20, 30), (40, 50)))
    assert parts.remember(key, record) and parts.lookup(key) == record
    assert not parts.ranges_valid(((5, 30),), 10, 100) and not parts.ranges_valid(((20, 30), (25, 40)), 10, 100) and not parts.ranges_valid(((20, 200),), 10, 100)
    parts.forget(key)


class Lender:
    """The queue's side for move_in_parts on its own: lends as many further logins as given, and none again after a drop."""

    def __init__(self, extra: int) -> None:
        self.extra: int = extra
        self.back: list[RemoteConnection] = []

    def more(self) -> RemoteConnection | None:
        if not self.extra:
            return None
        self.extra -= 1
        return cast(RemoteConnection, object())

    def again(self, lost: RemoteConnection) -> RemoteConnection | None:
        self.back.append(lost)
        return None  # the server takes no new login now

    def done(self, connection: RemoteConnection) -> None:
        self.back.append(connection)


def test_a_long_file_with_a_drop_now_and_then_never_loses_its_part() -> None:
    first = cast(RemoteConnection, object())
    spans = Spans(0, ((10, 20), (30, 40), (50, 60)))  # four pieces still to do: each drops once, then goes
    tried: set[int] = set()

    def drops_once(_connection: RemoteConnection, start: int, end: int, progress: Progress, _limit: Limit) -> None:
        if start not in tried:
            tried.add(start)
            raise TransferInterrupted("The data connection for this file broke off on the way.")
        progress(end - start)

    move_in_parts(first, Lender(0), drops_once, spans, 70, lambda _amount: None, 0, threading.Event())
    assert spans.gaps(0, 70) == [] and len(tried) == 4  # four drops in all, never two in a row: the part went on


def test_a_piece_given_back_after_the_other_parts_ended_is_finished_by_the_item_s_own_login() -> None:
    first = cast(RemoteConnection, object())
    spans = Spans(0)
    lender = Lender(1)
    runner: list[threading.Thread] = []

    def joining() -> bool:
        frame = sys._current_frames().get(runner[0].ident or 0)
        while frame is not None:
            if frame.f_code.co_name == "join":
                return True  # the item's own part found nothing more to do, and waits for the other part
            frame = frame.f_back
        return False

    def move(connection: RemoteConnection, start: int, end: int, progress: Progress, _limit: Limit) -> None:
        if connection is not first and not lender.back:
            until(joining)
            raise ConnectionLost("The connection to the server was lost.")  # gives its piece back; its new login is refused
        progress(end - start)

    failed: list[BaseException] = []

    def run() -> None:
        try:
            move_in_parts(first, lender, move, spans, 4 * 1024 * 1024, lambda _amount: None, 0, threading.Event())
        except BaseException as error:
            failed.append(error)

    runner.append(threading.Thread(target=run, daemon=True))
    runner[0].start()
    runner[0].join(15)
    assert not runner[0].is_alive() and failed == [] and spans.gaps(0, 4 * 1024 * 1024) == []


def test_any_answer_to_a_range_ended_early_is_no_failure(ftp_root: Path, tmp_path: Path, four_at_once: TransferQueue) -> None:
    big(ftp_root)
    here = tmp_path / "here"
    here.mkdir()
    with ftp_server(ftp_root, early_end_reply="550 Not like this.") as port:
        connection, settings = ftp_login(port)
        item = Job(connection, settings, str(here), "/big.bin", False, "big.bin", has_password=True, options=SPLITTING)
        four_at_once.add(item)
        until(lambda: item.state in (State.DONE, State.FAILED))
        connection.close()
    assert (item.state, item.message) == (State.DONE, "") and (here / "big.bin").read_bytes() == (ftp_root / "big.bin").read_bytes()


class Renewing(Lender):
    """As Lender, but a further login whose line dropped is replaced by a new one."""

    @override
    def again(self, lost: RemoteConnection) -> RemoteConnection | None:
        self.back.append(lost)
        return cast(RemoteConnection, object())


def test_a_further_part_with_a_drop_now_and_then_finishes_the_file_while_the_item_s_own_login_can_not() -> None:
    first = cast(RemoteConnection, object())
    spans = Spans(0, ((10, 20), (30, 40), (50, 60)))  # four pieces: the further part drops once at each, then moves it
    tried: set[int] = set()
    lock = threading.Lock()

    def move(connection: RemoteConnection, start: int, end: int, progress: Progress, _limit: Limit) -> None:
        if connection is first:
            raise TransferInterrupted("The data connection for this file broke off on the way.")  # its own line keeps breaking
        with lock:
            again = start in tried
            tried.add(start)
        if not again:
            raise TransferInterrupted("The data connection for this file broke off on the way.")
        progress(end - start)

    move_in_parts(first, Renewing(1), move, spans, 70, lambda _amount: None, 0, threading.Event())
    assert spans.gaps(0, 70) == [] and len(tried) == 4  # four drops of the further part, never two in a row: it went on
