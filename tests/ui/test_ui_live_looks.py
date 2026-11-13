"""Opt-in test of thumbnails against a real SFTP server (D-026, FQ-02). Run with `pytest -m live`; the server, key and trust are set as
in tests/test_live_transfers.py. The test works in a throwaway folder of its own in the account's home, which goes again afterwards with
all it holds (OR-26)."""

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from pytestqt.qtbot import QtBot

from chrono_ftp.connections.base import RemoteConnection, joined
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.engine.thumbnails import MEGABYTE
from chrono_ftp.ui.filelist import THUMBNAIL_PIXELS
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.thumbnails import picture_from
from tests.test_live_transfers import live
from tests.ui.test_ui_looks import picture_bytes

# --- Version ------------------------------------------------------------
__version__ = "test_ui_live_looks.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Initial: a picture on a real server read into memory and shown as a thumbnail in the window, nothing written here (FQ-02)

__all__ = ["live"]  # the fixture, used by name

pytestmark = pytest.mark.live


def test_a_picture_on_a_real_server_is_read_into_memory_only(live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    source = tmp_path / "a.png"
    source.write_bytes(picture_bytes(400, 300))
    server.upload(str(source), joined(folder, "a.png"), lambda _done: None)
    before = sorted(tmp_path.rglob("*"))
    data = server.read_file(joined(folder, "a.png"), MEGABYTE, lambda: True)
    assert data is not None and data == source.read_bytes()
    assert sorted(tmp_path.rglob("*")) == before  # nothing was written on this computer
    image = picture_from(data)
    assert image is not None and (image.width(), image.height()) == (THUMBNAIL_PIXELS, THUMBNAIL_PIXELS * 3 // 4)
    assert server.read_file(joined(folder, "a.png"), MEGABYTE, lambda: False) is None  # it gives way when told
    assert [entry.name for entry in server.list_folder(folder)] == ["a.png"]  # and the connection goes on


def test_the_window_shows_the_thumbnail_of_a_picture_on_a_real_server(qtbot: QtBot, live: tuple[RemoteConnection, str], tmp_path: Path) -> None:
    server, folder = live
    assert isinstance(server, SshConnection)
    source = tmp_path / "a.png"
    source.write_bytes(picture_bytes())
    server.upload(str(source), joined(folder, "a.png"), lambda _done: None)
    window = MainWindow()
    qtbot.addWidget(window)
    with qtbot.waitExposed(window):
        window.show()
    window.open_connection(replace(server.connection, start_folder=folder))
    qtbot.waitUntil(lambda: bool(window.sessions) and window.sessions[0].alive and window.remote_side.folder == folder, timeout=15_000)
    view = window.remote_side.file_list
    window.remote_side.navigation.buttons["thumbnails"].trigger()

    def shown() -> bool:
        icon = view.files.index(view.index_of("a.png").row(), 0).data(Qt.ItemDataRole.DecorationRole)
        return isinstance(icon, QIcon) and icon.cacheKey() != view.files.icons[1].cacheKey()

    qtbot.waitUntil(shown, timeout=15_000)
    window.close()
