"""Opt-in test against a real FTP account (D-026): several transfers at once (FC-01). Run with `pytest -m live`.

Which account: the connection stored in Chrono FTP under the name in CHRONO_FTP_LIVE_FTP_CONNECTION, by default "chronoftp". Its entry
is read from the person's own connection list without changing it, and copied into the test's own settings; the password comes from
the credential store as the program reads it, and is never shown, logged or kept. No host, address or account lives in the project (G-1).
A certificate the person trusted for that server is copied into the test's own trust store the same way. The test works in a throwaway
folder of its own in the account and removes it again, also when it fails (OR-26); it moves well under a megabyte."""

import json
import os
import secrets
from dataclasses import replace
from collections.abc import Generator
from pathlib import Path
from typing import cast

import pytest

from chrono_ftp.config import connections
from chrono_ftp.connections import trust
from chrono_ftp.connections.base import Limit, Progress, joined
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.core import credentials
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ChronoFtpError
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers import split
from chrono_ftp.transfers.queue import Job, State, TransferQueue
from tests.conftest import REAL_SETTINGS
from tests.test_transfer_queue import until

# --- Version ------------------------------------------------------------
__version__ = "test_live_parallel.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial: three uploads and three downloads at once on a real FTP account, in a throwaway folder; a download split in parts

NAME: str = os.environ.get("CHRONO_FTP_LIVE_FTP_CONNECTION", "chronoftp")
FILES: tuple[str, ...] = ("one.bin", "two.bin", "three.bin")
SIZE: int = 64 * 1024
SLOW: int = 32  # KB per second each: every file takes about two seconds, so the three are on their way together
FOLDER_PREFIX: str = "chrono-ftp-live-"
REPLACING: TransferOptions = TransferOptions(ask_before_replacing=False)

pytestmark = pytest.mark.live


def stored_entry() -> dict[str, object] | None:
    """The entry of the stored connection, read from the person's own list and never written."""
    try:
        data: object = json.loads((REAL_SETTINGS / connections.FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = cast(dict[str, object], data).get("connections") if isinstance(data, dict) else None
    for entry in cast(list[object], entries) if isinstance(entries, list) else []:
        if isinstance(entry, dict) and cast(dict[str, object], entry).get("name") == NAME:
            return cast(dict[str, object], entry)
    return None


def trusted_lines(connection: Connection) -> list[str]:
    """What the person trusted of this server, read from the person's own trust store and never written."""
    try:
        text = (REAL_SETTINGS / trust.CERTIFICATES_NAME).read_text(encoding="utf-8")
    except OSError:
        return []
    return [line for line in text.splitlines() if line.startswith(f"{connection.host}:{connection.port} ")]


@pytest.fixture
def account() -> Generator[tuple[FtpConnection, Connection, str]]:
    """The tab's login and a throwaway folder that did not exist before; it goes again afterwards, with all it holds."""
    entry = stored_entry()
    if entry is None:
        pytest.skip(f"There is no stored connection named {NAME}, so this test has nothing to run on.")
    connections.list_path().parent.mkdir(parents=True, exist_ok=True)  # the test's own settings folder, with only this entry in its list
    connections.list_path().write_text(json.dumps({"connections": [entry]}), encoding="utf-8")
    loaded = connections.load().connections
    if len(loaded) != 1 or loaded[0].protocol != FTP:
        pytest.skip(f"The stored connection {NAME} is not an FTP connection Chrono FTP can use.")
    settings = loaded[0]
    try:
        password = credentials.KeyringStore().read(settings.name)
    except ChronoFtpError:
        password = None
    if not password:
        pytest.skip(f"No password is stored for {NAME}, so this test can't log in.")
    for line in trusted_lines(settings):
        trust.remember_certificate(line)  # only in the trust store of this test, which goes with it
    connection = FtpConnection(settings, password)
    connection.connect()
    folder = joined(connection.home(), FOLDER_PREFIX + secrets.token_hex(6))
    connection.make_folder(folder)  # refused when it exists: never a folder that is not ours
    try:
        yield connection, settings, folder
    finally:
        try:
            connection.delete(folder, True)
        finally:
            connection.close()


def test_three_uploads_and_three_downloads_at_once_on_a_real_ftp_account(account: tuple[FtpConnection, Connection, str], tmp_path: Path) -> None:
    connection, settings, folder = account
    here, back = tmp_path / "here", tmp_path / "back"
    here.mkdir()
    back.mkdir()
    for name in FILES:
        (here / name).write_bytes(os.urandom(SIZE))
    most: list[int] = [0]

    def changed(_item: Job) -> None:
        most[0] = max(most[0], sum(1 for item in queue.jobs if item.state == State.RUNNING and item.done > 0))

    queue = TransferQueue(changed)
    queue.settings(True, 15.0, False, len(FILES))
    try:
        uploads = [Job(connection, settings, str(here / name), folder, True, name, has_password=True, options=REPLACING, limit=SLOW) for name in FILES]
        for item in uploads:
            queue.add(item)
        until(lambda: all(item.state in (State.DONE, State.FAILED) for item in uploads), 15.0)
        assert [item.message for item in uploads if item.state == State.FAILED] == []
        assert most[0] >= 2  # they were on their way together: as many as the server takes at once
        assert {entry.name: entry.size for entry in connection.list_folder(folder)} == dict.fromkeys(FILES, SIZE)
        most[0] = 0
        downloads = [Job(connection, settings, str(back), joined(folder, name), False, name, has_password=True, options=REPLACING, limit=SLOW) for name in FILES]
        for item in downloads:
            queue.add(item)
        until(lambda: all(item.state in (State.DONE, State.FAILED) for item in downloads), 15.0)
        assert [item.message for item in downloads if item.state == State.FAILED] == []
        assert most[0] >= 2
        assert all((back / name).read_bytes() == (here / name).read_bytes() for name in FILES)
    finally:
        queue.close()


def test_a_download_split_in_parts_on_a_real_ftp_account(account: tuple[FtpConnection, Connection, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(split, "SPLIT_BYTES", 1024 * 1024)  # a few MB count as large here
    connection, settings, folder = account
    logins: set[int] = set()
    real = FtpConnection.download_range

    def noted(self: FtpConnection, remote: str, local: str, start: int, end: int, progress: Progress, limit: Limit = 0) -> None:
        logins.add(id(self))
        real(self, remote, local, start, end, progress, limit)

    monkeypatch.setattr(FtpConnection, "download_range", noted)
    source, back = tmp_path / "big.bin", tmp_path / "back"
    source.write_bytes(os.urandom(3 * 1024 * 1024))
    back.mkdir()
    queue = TransferQueue(lambda item: None)
    queue.settings(True, 15.0, False, 4)
    try:
        sent = Job(connection, settings, str(source), folder, True, "big.bin", has_password=True, options=REPLACING)
        queue.add(sent)
        until(lambda: sent.state in (State.DONE, State.FAILED), 15.0)
        assert sent.state == State.DONE, sent.message
        fetched = Job(connection, settings, str(back), joined(folder, "big.bin"), False, "big.bin", has_password=True, options=replace(REPLACING, split_large=True))
        queue.add(fetched)
        until(lambda: fetched.state in (State.DONE, State.FAILED), 15.0)
        assert fetched.state == State.DONE, fetched.message
    finally:
        queue.close()
    assert (back / "big.bin").read_bytes() == source.read_bytes() and len(logins) >= 2  # in parts, as many as the server took
