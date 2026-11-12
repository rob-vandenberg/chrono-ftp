"""Opt-in test of block FP against a real SFTP server (D-026): the server information and the free space of a folder (FP-03), and the
session log of the work on it, which never holds a password or key (FP-02). Run with `pytest -m live`; the server, key and trust are set as
in tests/test_live_transfers.py. Only reads the server; the throwaway folder of the fixture goes again (OR-26)."""

import pytest

from chrono_ftp.connections import sessionlog
from chrono_ftp.connections.base import RemoteConnection
from chrono_ftp.core.logrules import DEBUG, LogRules
from chrono_ftp.engine import serverinfo
from tests.test_live_transfers import live

# --- Version ------------------------------------------------------------
__version__ = "test_live_serverinfo.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: server information, free space and the session log on a real server (FP-02, FP-03)

__all__ = ["live"]  # the fixture, used by name

pytestmark = pytest.mark.live


def test_server_information_and_free_space_on_a_real_server(live: tuple[RemoteConnection, str]) -> None:
    server, folder = live
    report = serverinfo.report(server, folder, False, "live")
    lines = {f"{title}/{label}": value for title, entries in report.sections for label, value in entries}
    assert lines["Connection/Protocol"].startswith("SFTP version ")
    assert lines["Connection/Server software"].startswith("SSH-2.0-")
    assert lines["Encryption/Cipher"] and lines["Encryption/Key exchange"] != serverinfo.UNKNOWN
    assert lines["Host key/Fingerprint (SHA-256)"].startswith("SHA256:")
    space = server.free_space(folder)
    assert space is not None and space.available is not None and space.available > 0
    assert space.total is not None and space.total >= space.available
    assert " free of " in lines["Space/Free space"]


def test_the_session_log_of_a_real_server_holds_the_work_and_no_key(live: tuple[RemoteConnection, str]) -> None:
    server, folder = live
    sessionlog.set_rules(LogRules(DEBUG))
    server.list_folder(folder)
    path = sessionlog.log_for(server.log.connection_name or "").path
    assert path is not None
    text = path.read_text("utf-8")
    assert f"> readdir {folder}" in text
    assert "PRIVATE KEY" not in text.upper()
