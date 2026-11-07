from collections.abc import Callable

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections
from chrono_ftp.core.connection import DEFAULT_PORTS, FTP, SFTP, Connection
from chrono_ftp.ui.connectionlist import ConnectionList
from tests.conftest import MemoryStore

# --- Version ------------------------------------------------------------
__version__ = "test_ui_connectionlist.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial


def note_in(told: list[str]) -> Callable[..., None]:
    def note(*args: object) -> None:
        told.append(str(args[2]))

    return note


def pick_colour(*args: object) -> QColor:
    return QColor("#cc00cc")


def answer_no(*args: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.No


def answer_yes(*args: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def window_with(qtbot: QtBot, *saved: Connection) -> ConnectionList:
    connections.save(saved)
    window = ConnectionList()
    qtbot.addWidget(window)
    return window


def fill(window: ConnectionList, name: str = "Shop", host: str = "shop.example.com") -> None:
    window.name.setText(name)
    window.host.setText(host)


def test_save_is_greyed_until_name_and_host_are_filled_in(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    assert not window.save_button.isEnabled()
    window.name.setText("Shop")
    assert not window.save_button.isEnabled()
    window.host.setText("shop.example.com")
    assert window.save_button.isEnabled()


def test_delete_and_connect_are_greyed_without_a_selected_connection(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    assert not window.delete_button.isEnabled()
    assert not window.connect_button.isEnabled()


def test_a_saved_connection_is_in_the_list_and_in_the_file(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    fill(window)
    window.save()
    assert window.list.count() == 1
    assert connections.load().connections[0].host == "shop.example.com"
    assert window.delete_button.isEnabled()


def test_the_password_goes_to_the_store_and_never_into_the_file(qtbot: QtBot, memory_store: MemoryStore) -> None:
    window = window_with(qtbot)
    fill(window)
    window.password.setText("very-secret")
    window.save()
    assert memory_store.passwords == {"Shop": "very-secret"}
    assert "very-secret" not in connections.list_path().read_text(encoding="utf-8")


def test_a_stored_password_is_shown_as_stored_and_not_asked_again(qtbot: QtBot, memory_store: MemoryStore) -> None:
    memory_store.passwords["Shop"] = "very-secret"
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window.list.setCurrentRow(0)
    assert window.password.text() == ""
    assert window.password.placeholderText() == "Stored"
    window.save()
    assert memory_store.passwords == {"Shop": "very-secret"}


def test_renaming_takes_the_password_along(qtbot: QtBot, memory_store: MemoryStore) -> None:
    memory_store.passwords["Shop"] = "very-secret"
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window.list.setCurrentRow(0)
    window.name.setText("Store")
    window.save()
    assert memory_store.passwords == {"Store": "very-secret"}
    assert [item.name for item in connections.load().connections] == ["Store"]


def test_two_connections_cannot_have_the_same_name(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    fill(window, "Shop", "other.example.com")
    window.save()
    assert len(told) == 1
    assert [item.host for item in connections.load().connections] == ["shop.example.com"]


def test_choosing_the_protocol_fills_in_the_standard_port(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    window.protocol.setCurrentIndex(window.protocol.findData(FTP))
    assert window.port.value() == DEFAULT_PORTS[FTP]
    window.protocol.setCurrentIndex(window.protocol.findData(SFTP))
    assert window.port.value() == DEFAULT_PORTS[SFTP]


def test_a_port_of_your_own_stays_when_the_protocol_changes(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    window.port.setValue(2022)
    window.protocol.setCurrentIndex(window.protocol.findData(FTP))
    assert window.port.value() == 2022


def test_the_chosen_colour_is_saved(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot)
    monkeypatch.setattr(QColorDialog, "getColor", pick_colour)
    window.colour_button.click()
    fill(window)
    window.save()
    assert connections.load().connections[0].colour == "#cc00cc"


def test_delete_asks_first_and_no_keeps_everything(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords["Shop"] = "very-secret"
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window.list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", answer_no)
    window.delete()
    assert window.list.count() == 1
    assert memory_store.passwords == {"Shop": "very-secret"}


def test_delete_removes_the_connection_and_its_password(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords["Shop"] = "very-secret"
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window.list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    window.delete()
    assert window.list.count() == 0
    assert memory_store.passwords == {}
    assert connections.load().connections == ()


def test_connect_hands_over_the_selected_connection(qtbot: QtBot) -> None:
    chosen = Connection(name="Shop", host="shop.example.com")
    window = window_with(qtbot, chosen)
    window.list.setCurrentRow(0)
    with qtbot.waitSignal(window.connect_requested) as signal:
        window.connect_button.click()
    assert signal.args == [chosen]


def test_a_damaged_list_is_told_calmly_and_the_window_opens(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    path = connections.list_path()
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", note_in(told))
    window = ConnectionList()
    qtbot.addWidget(window)
    assert window.list.count() == 0
    assert len(told) == 1 and "kept" in told[0]
