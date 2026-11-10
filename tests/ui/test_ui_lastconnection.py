from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.core.connection import Connection
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.settingsdialog import SettingsDialog
from tests.fakeservers import ftp_server
from tests.ui.test_ui_queue import ftp_settings
from tests.ui.test_ui_sessions import connected, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_lastconnection.py 0.0.31"

# --- Version history ----------------------------------------------------
# v0.0.31: Initial: the connection that was last live is kept, and the connections window starts on it (D-053)

__all__ = ["window"]  # the fixture, used by name

MANY: int = 40  # more connections than the list shows at once, so the last one must be scrolled into view


def started_window(qtbot: QtBot) -> MainWindow:
    started = MainWindow()
    qtbot.addWidget(started)
    started.show()
    started.show_connection_list_at_start()
    return started


def test_a_connection_that_goes_live_is_kept_as_the_last_one(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, ftp_settings(port))
        assert saved_settings.load().last_connection == "Shop"
        assert window.settings.last_connection == "Shop"


def test_the_connections_window_starts_on_the_last_connection_in_view(qtbot: QtBot) -> None:
    saved_connections.save([Connection(name=f"Server {number:02d}", host="example.org") for number in range(MANY)])
    saved_settings.save(saved_settings.Settings(last_connection="Server 39"))
    started = started_window(qtbot)
    connection_list = started.connection_list
    assert connection_list is not None
    selected = connection_list.selected()
    assert selected is not None and selected.name == "Server 39"
    assert connection_list.name.text() == "Server 39"
    item = connection_list.list.currentItem()
    assert item is not None
    assert connection_list.list.viewport().rect().intersects(connection_list.list.visualItemRect(item))
    connection_list.hide()


def test_a_last_connection_that_is_gone_leaves_the_window_as_without_one(qtbot: QtBot) -> None:
    saved_connections.save([Connection(name="Alpha", host="example.org"), Connection(name="Beta", host="example.nl")])
    saved_settings.save(saved_settings.Settings())
    plain = started_window(qtbot)
    assert plain.connection_list is not None
    without = plain.connection_list.selected()
    plain.connection_list.hide()
    saved_settings.save(saved_settings.Settings(last_connection="Gone"))
    started = started_window(qtbot)
    assert started.connection_list is not None
    assert started.connection_list.selected() == without
    started.connection_list.hide()


def test_the_settings_window_keeps_the_last_connection(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    saved_settings.save(saved_settings.Settings(last_connection="Shop"))
    started = MainWindow()
    qtbot.addWidget(started)

    def accepted(_dialog: SettingsDialog) -> int:
        return SettingsDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SettingsDialog, "exec", accepted)
    started.open_settings()
    assert started.settings.last_connection == "Shop"
    assert saved_settings.load().last_connection == "Shop"
