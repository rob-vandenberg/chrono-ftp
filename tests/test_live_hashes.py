"""Opt-in tests of block FE against a real SFTP server with a shell (D-026): checksums by the server's *sum tools in one command, the
size of folders by one find on the server against the walk of the listings, the checksum step of compare, and a long command ended
when stopped; all in a throwaway folder that goes again afterwards (OR-26). Run with `pytest -m live`; the servers are named as in
test_live_transfers.py."""

import hashlib
import os
import secrets
import subprocess
import time
from pathlib import Path

import pytest

from chrono_ftp.connections.base import CommandResult, KeepGoing, RemoteConnection, joined
from chrono_ftp.engine import checksums, sizes
from chrono_ftp.engine.checksums import Checksum
from chrono_ftp.engine.compare import Difference, compare_contents, compare_folders
from chrono_ftp.engine.sizes import Count
from chrono_ftp.ui.session import no_progress
from tests.test_live_transfers import live

# --- Version ------------------------------------------------------------
__version__ = "test_live_hashes.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Initial: checksums, folder sizes, the checksum step of compare and a stopped command on a real server (FE-01 to FE-03);
#          review round 2: a stop ends every process of the command, not only its shell

__all__ = ["live"]  # the fixture, used by name
pytestmark = pytest.mark.live
NAMES: tuple[str, ...] = ("plain.txt", "with space.txt", "it's.txt", "-dash.txt", "new\nline.txt")


def always() -> bool:
    return True


def put(connection: RemoteConnection, here: Path, folder: str, name: str, data: bytes) -> None:
    (here / "upload").write_bytes(data)
    connection.upload(str(here / "upload"), joined(folder, name), no_progress)


def test_checksums_by_the_shell_of_a_real_server_are_one_command_and_right(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    assert checksums.way_of(server.capabilities, "sha256") == checksums.SHELL
    for number, name in enumerate(NAMES):
        put(server, tmp_path, folder, name, b"x" * (number + 1) * 1000)
    server.make_folder(joined(folder, "inside"))
    asked: list[str] = []
    run_stoppable = server.run_stoppable

    def noted(command: str, keep_going: KeepGoing) -> CommandResult | None:
        asked.append(command)
        return run_stoppable(command, keep_going)

    setattr(server, "run_stoppable", noted)
    for algorithm in checksums.algorithms_of(server.capabilities):
        found: list[Checksum] = []
        paths = [joined(folder, name) for name in NAMES] + [joined(folder, "inside"), joined(folder, "gone")]
        assert checksums.remote_checksums(server, paths, algorithm, always, found.append)
        assert [item.value for item in found[: len(NAMES)]] == [hashlib.new(algorithm, b"x" * (number + 1) * 1000).hexdigest() for number in range(len(NAMES))]
        assert found[-2].problem == checksums.NOT_A_FILE_TEXT and found[-1].problem == checksums.GONE_TEXT
    assert len(asked) == len(checksums.algorithms_of(server.capabilities))  # one command per algorithm for all the files


def test_folder_sizes_by_one_find_agree_with_the_walk_of_the_listings(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    top = joined(folder, "top")
    server.make_folder(top)
    server.make_folder(joined(top, "deep"))
    server.make_folder(joined(top, "empty"))
    put(server, tmp_path, top, "a.bin", b"a" * 12345)
    put(server, tmp_path, joined(top, "deep"), "b.bin", b"b" * 678)
    server.make_link("deep", joined(top, "link"))
    entries = [entry for entry in server.list_folder(folder) if entry.name == "top"]
    walked = sizes.remote_count_by_listing(server, top, always)
    assert walked is not None and (walked.files, walked.folders, walked.complete) == (3, 3, True)
    if not sizes.can_count_on_server(server.capabilities):
        pytest.skip("this server's find can't print sizes: the listings are walked, as above")
    counted: dict[str, Count] = {}
    assert sizes.remote_counts(server, folder, entries, always, counted.__setitem__)
    assert counted["top"] == Count(12345 + 678 + len("deep"), 3, 3, True)  # the link counts its own size, as find tells it


def test_compare_contents_on_a_real_server(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    here = tmp_path / "here"
    here.mkdir()
    put(server, tmp_path, folder, "same.txt", b"same content")
    put(server, tmp_path, folder, "other.txt", b"remote bytes")
    (here / "same.txt").write_bytes(b"same content")
    (here / "other.txt").write_bytes(b"local  bytes")
    for name in ("same.txt", "other.txt"):
        os.utime(here / name, (1_600_000_000, 1_600_000_000))  # older here
    found = compare_folders(server, str(here), folder)
    assert {row.difference for row in found} == {Difference.DATE}
    result = compare_contents(server, str(here), folder, found, checksums.preferred_algorithm(server.capabilities), always)
    assert result is not None and result.same == 1
    assert [(row.name, row.difference) for row in result.found] == [("other.txt", Difference.CONTENT_NEWER_REMOTE)]


def test_a_long_command_on_a_real_server_ends_when_stopped(live: tuple[RemoteConnection, str]) -> None:
    server, _folder = live
    started = time.monotonic()
    assert server.run_stoppable("sleep 30", lambda: time.monotonic() < started + 0.5) is None
    assert time.monotonic() - started < 5
    result = server.run_stoppable("echo still here", always)
    assert result is not None and result.output.strip() == "still here"


def running(marker: str) -> list[str]:
    """The processes of this computer whose command line holds marker; only read, never signalled."""
    found = subprocess.run(["pgrep", "-a", "-f", marker], capture_output=True, text=True, check=False, timeout=10)
    return [line for line in found.stdout.splitlines() if "pgrep" not in line]


def test_a_stop_ends_every_process_of_the_command_on_the_server(live: tuple[RemoteConnection, str]) -> None:
    """The server is this computer, so what is left of the command can be seen here, by a marker of its own."""
    server, _folder = live
    seconds = f"59.{secrets.randbelow(10**6):06d}"  # a sleep no other program runs
    marker = f"chrono-ftp-mark-{secrets.token_hex(6)}"
    command = f"sleep {seconds} | sha256sum; : {marker}"
    started = time.monotonic()
    assert server.run_stoppable(command, lambda: time.monotonic() < started + 1) is None
    limit = time.monotonic() + 5
    while (running(f"sleep {seconds}") or running(marker)) and time.monotonic() < limit:
        time.sleep(0.1)  # the server's processes end on their signal
    assert running(f"sleep {seconds}") == [] and running(marker) == []

