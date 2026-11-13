"""Opt-in tests of block FD against a real SFTP server (D-026): the file mask, text files and names, file by file and beside rsync, in a
throwaway folder that goes again afterwards (OR-26). Run with `pytest -m live`; the servers are named as in test_live_transfers.py."""

from pathlib import Path

import pytest

from chrono_ftp.connections.base import RemoteConnection, joined
from chrono_ftp.core.transferoptions import AUTOMATIC, CASE_LOWER, TransferOptions
from chrono_ftp.transfers.job import State
from tests.test_live_transfers import live, run

# --- Version ------------------------------------------------------------
__version__ = "test_live_masks.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: the mask, text files, names and the hand-off from rsync to file by file on a real server (FD-01 to FD-03)

__all__ = ["live"]  # the fixture, used by name
pytestmark = pytest.mark.live
CRLF_TEXT: bytes = b"one\r\ntwo\r\n"
LF_TEXT: bytes = b"one\ntwo\n"


def site(base: Path) -> Path:
    (base / "Site" / "Deep").mkdir(parents=True)
    (base / "Site" / "index.html").write_bytes(CRLF_TEXT)
    (base / "Site" / "logo.png").write_bytes(CRLF_TEXT)
    (base / "Site" / "notes.bak").write_bytes(b"old")
    (base / "Site" / "Deep" / "page.HTML").write_bytes(CRLF_TEXT)
    (base / "Site" / ".cache").write_bytes(b"cache")
    return base / "Site"


def remote_names(connection: RemoteConnection, folder: str) -> list[str]:
    found: list[str] = []
    for item in connection.list_folder(folder):
        found.append(item.name)
        if item.is_folder:
            found += [f"{item.name}/{name}" for name in remote_names(connection, joined(folder, item.name))]
    return sorted(found)


@pytest.mark.parametrize("rsync", [False, True])
def test_a_mask_text_and_names_go_up_and_come_back(live: tuple[RemoteConnection, str], tmp_path: Path, rsync: bool) -> None:
    connection, folder = live
    if rsync and not connection.capabilities.has("rsync"):
        pytest.skip("this server has no shell with rsync")
    options = TransferOptions(ask_before_replacing=False, file_mask="| *.bak", skip_hidden=True, transfer_mode=AUTOMATIC, name_case=CASE_LOWER)
    job = run(connection, site(tmp_path), folder, True, "Site", options, rsync=rsync)
    assert job.state == State.DONE, job.message
    assert job.way == ("rsync" if rsync else "SFTP")
    assert remote_names(connection, joined(folder, "site")) == ["deep", "deep/page.html", "index.html", "logo.png"]
    back = tmp_path / "back"
    back.mkdir()
    job = run(connection, back, joined(folder, "site"), False, "site", TransferOptions(ask_before_replacing=False, transfer_mode=AUTOMATIC), rsync=rsync)
    assert job.state == State.DONE, job.message
    assert (back / "site" / "index.html").read_bytes() == LF_TEXT and (back / "site" / "deep" / "page.html").read_bytes() == LF_TEXT
    assert (back / "site" / "logo.png").read_bytes() == CRLF_TEXT  # binary, byte for byte both ways
