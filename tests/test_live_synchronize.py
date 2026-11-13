"""Opt-in tests of block FF against a real SFTP server (D-026): the scan of a real listing, the precision its dates come back in, the
plan of each direction and mode, and the deletes, moves and dates that Apply would send to the queue, all in a throwaway folder that
goes again afterwards (OR-26). Run with `pytest -m live`; the servers are named as in test_live_transfers.py."""

import os
from pathlib import Path

import pytest

from chrono_ftp.connections.base import RemoteConnection, joined
from chrono_ftp.core.synchronizing import Direction, Mode, Options
from chrono_ftp.engine.synchronize import Action, local_reader, plan, precision_of_pairs, remote_reader, scan
from chrono_ftp.transfers.job import Job
from chrono_ftp.transfers.sidework import SideJob, SideWork, Step, run_side_job
from chrono_ftp.ui.session import no_progress
from chrono_ftp.core.connection import Connection
from tests.test_live_transfers import live

# --- Version ------------------------------------------------------------
__version__ = "test_live_synchronize.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: Initial: block FF against a real server (FF-01 to FF-10)

__all__ = ["live"]  # the fixture, used by name
pytestmark = pytest.mark.live
OLD_TIME: float = 1_600_000_000.0


def put(connection: RemoteConnection, here: Path, folder: str, name: str, data: bytes) -> None:
    (here / "upload").write_bytes(data)
    connection.upload(str(here / "upload"), joined(folder, name), no_progress)


def write_at(path: Path, text: str, moment: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (moment, moment))


def test_the_scan_of_a_real_server_finds_both_sides_and_its_date_precision(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    here = tmp_path / "here"
    here.mkdir()
    write_at(here / "mine.txt", "mine", OLD_TIME)
    write_at(here / "deep" / "inside.txt", "deep", OLD_TIME)
    put(server, tmp_path, folder, "theirs.txt", b"theirs")
    options = Options(direction=Direction.BOTH)
    pairs = scan(str(here), folder, local_reader, remote_reader(server), options)
    assert pairs is not None
    assert precision_of_pairs(pairs) in (1, 2, 60)  # whatever this server's listing reports, never a fixed margin (FF-03)
    rows = {row.path: row.action for row in plan(pairs, options, precision_of_pairs(pairs))}
    assert rows == {"mine.txt": Action.COPY_THERE, "deep": Action.COPY_THERE, "theirs.txt": Action.COPY_HERE}


def test_mirror_on_a_real_server_plans_a_delete_only_when_the_box_is_on(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    here = tmp_path / "here"
    here.mkdir()
    put(server, tmp_path, folder, "gone.txt", b"gone")
    for on, ticked in ((False, False), (True, True)):
        options = Options(direction=Direction.TO_THERE, mode=Mode.MIRROR, delete_other_side=on)
        pairs = scan(str(here), folder, local_reader, remote_reader(server), options)
        assert pairs is not None
        rows = plan(pairs, options, precision_of_pairs(pairs))
        assert [(row.action, row.ticked) for row in rows] == [(Action.DELETE_THERE, ticked)]


def test_the_deletes_moves_and_dates_run_on_a_real_server(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    put(server, tmp_path, folder, "gone.txt", b"gone")
    put(server, tmp_path, folder, "old-name.txt", b"move me")
    put(server, tmp_path, folder, "dated.txt", b"date me")
    job = Job(server, Connection(name="live", host=""), str(tmp_path), folder, True, "side")  # side work on the server uses the connection it was given, nothing else
    run_side_job(job, SideJob(SideWork.DELETE, True, (Step(joined(folder, "gone.txt")),)))
    run_side_job(job, SideJob(SideWork.MOVE, True, (Step(joined(folder, "old-name.txt"), joined(folder, "deep/new-name.txt")),)))
    run_side_job(job, SideJob(SideWork.TOUCH, True, (Step(joined(folder, "dated.txt"), moment=OLD_TIME),)))
    names = {entry.name for entry in server.list_folder(folder)}
    assert "gone.txt" not in names and "deep" in names
    assert {entry.name for entry in server.list_folder(joined(folder, "deep"))} == {"new-name.txt"}
    dated = server.details(joined(folder, "dated.txt"))
    assert dated is not None and dated.modified is not None and abs(dated.modified - OLD_TIME) < 2


def test_a_timestamps_only_plan_on_a_real_server_touches_only_files_of_the_same_size(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    here = tmp_path / "here"
    here.mkdir()
    put(server, tmp_path, folder, "same.txt", b"12345")
    write_at(here / "same.txt", "12345", OLD_TIME)
    put(server, tmp_path, folder, "other.txt", b"12345")
    write_at(here / "other.txt", "much longer here", OLD_TIME)
    options = Options(direction=Direction.TO_THERE, mode=Mode.TIMESTAMPS)
    pairs = scan(str(here), folder, local_reader, remote_reader(server), options)
    assert pairs is not None
    rows = plan(pairs, options, precision_of_pairs(pairs))
    assert {row.path: row.action for row in rows} == {"same.txt": Action.TOUCH_THERE}
