"""Opt-in tests of navigation against a real SFTP server (D-026, FH-02, FH-05). Run with `pytest -m live`; the server, key and trust
are set as in tests/test_live_transfers.py. Every test works in a throwaway folder of its own in the account's home, which goes again
afterwards with all it holds (OR-26)."""

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.connections.base import RemoteConnection, joined
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.config.bookmarks import REMOTE
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.openfolder import OpenFolderDialog
from tests.test_live_transfers import live

# --- Version ------------------------------------------------------------
__version__ = "test_ui_live_navigation.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial: synchronized browsing makes a missing folder on a real server; a folder that is not there moves nothing (FH-02, FH-05)

__all__ = ["live"]  # the fixture, used by name

pytestmark = pytest.mark.live


def window_on(qtbot: QtBot, server: RemoteConnection, folder: str) -> MainWindow:
    assert isinstance(server, SshConnection)
    made = MainWindow()
    qtbot.addWidget(made)
    made.open_connection(replace(server.connection, start_folder=folder))
    qtbot.waitUntil(lambda: bool(made.sessions) and made.sessions[0].alive and made.remote_side.folder == folder, timeout=15_000)
    return made


def test_synchronized_browsing_makes_the_missing_folder_on_a_real_server(qtbot: QtBot, live: tuple[RemoteConnection, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server, folder = live
    asked: list[str] = []

    def yes(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", yes)
    (tmp_path / "site" / "css").mkdir(parents=True)
    window = window_on(qtbot, server, folder)
    window.local_side.go_to(str(tmp_path / "site"))
    window.actions_by_name["sync_browsing"].trigger()
    window.local_side.go_to(str(tmp_path / "site" / "css"))
    qtbot.waitUntil(lambda: window.remote_side.folder == joined(folder, "css"), timeout=15_000)
    assert len(asked) == 1 and "css" in asked[0]
    assert "css" in {entry.name for entry in server.list_folder(folder) if entry.is_folder}
    window.local_side.go_to(str(tmp_path / "site"))
    qtbot.waitUntil(lambda: window.remote_side.folder == folder, timeout=15_000)
    assert window.sessions[0].sync_browsing
    window.close()


def test_a_folder_that_is_not_on_a_real_server_gives_a_calm_message_and_nothing_moves(qtbot: QtBot, live: tuple[RemoteConnection, str], monkeypatch: pytest.MonkeyPatch) -> None:
    server, folder = live
    told: list[str] = []

    def note(*args: object) -> QMessageBox.StandardButton:
        told.append(str(args[2]))
        return QMessageBox.StandardButton.Ok

    def typed(dialog: OpenFolderDialog) -> int:
        dialog.path.setEditText(joined(folder, "gone"))
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(QMessageBox, "information", note)
    monkeypatch.setattr(OpenFolderDialog, "exec", typed)
    window = window_on(qtbot, server, folder)
    window.open_folder(REMOTE)
    qtbot.waitUntil(lambda: bool(told), timeout=15_000)
    assert window.remote_side.folder == folder and "stays where it was" in told[0]
    window.close()
