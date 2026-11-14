from collections.abc import Callable

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog, QLabel, QMessageBox
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections
from chrono_ftp.core.connection import DEFAULT_PORTS, FTP, SFTP, Connection
from chrono_ftp.ui.connectionlist import COPY_ADDRESS_TEXT, EMPTY_FOR_NEW_TEXT, USE_FOR_NEW_TEXT, ConnectionList
from chrono_ftp.ui.connectiontree import NAME
from tests.conftest import MemoryStore

# --- Version ------------------------------------------------------------
__version__ = "test_ui_connectionlist.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: The list is a tree: a connection is chosen by name, and the right-click menu holds Copy the Address and the two items for a new connection; Open an Address… stands with the other buttons (FJ-01, FJ-06, FJ-11, FJ-12)
# v0.0.32: Connect is the default button; Enter in the list, a field or on a focused button connects
# v0.0.25: Connect with unsaved changes asks Save changes?: Save, Don't Save, Cancel; none without changes
# v0.0.20: The labels of the fields are not against the splitter
# v0.0.19: Export… stands right next to Import…
# v0.0.18: The buttons under the list and under the fields stand on one line
# v0.0.17: Delete is in the right-click menu; Duplicate; Import… and a greyed Export… under the list; the fields can be made narrow
# v0.0.9: The optional colour
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


def shown(window: ConnectionList) -> list[str]:
    """The connections the tree shows, in its order (FJ-01)."""
    return [str(item.data(0, NAME)) for item in window.tree.connection_items()]


def place_of(window: ConnectionList, name: str) -> QPoint:
    item = window.tree.item_of(name)
    assert item is not None
    return window.tree.visualItemRect(item).center()


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
    assert not window.delete_action.isEnabled()
    assert not window.connect_button.isEnabled()


def test_a_saved_connection_is_in_the_list_and_in_the_file(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    fill(window)
    window.save()
    assert shown(window) == ["Shop"]
    assert connections.load().connections[0].host == "shop.example.com"
    assert window.delete_action.isEnabled()


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
    window._select("Shop")
    assert window.password.text() == ""
    assert window.password.placeholderText() == "Stored"
    window.save()
    assert memory_store.passwords == {"Shop": "very-secret"}


def test_renaming_takes_the_password_along(qtbot: QtBot, memory_store: MemoryStore) -> None:
    memory_store.passwords["Shop"] = "very-secret"
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window._select("Shop")
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
    window.use_colour.setChecked(True)
    window.colour_button.click()
    fill(window)
    window.save()
    assert connections.load().connections[0].colour == "#cc00cc"


def test_delete_asks_first_and_no_keeps_everything(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords["Shop"] = "very-secret"
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window._select("Shop")
    monkeypatch.setattr(QMessageBox, "question", answer_no)
    window.delete()
    assert shown(window) == ["Shop"]
    assert memory_store.passwords == {"Shop": "very-secret"}


def test_delete_removes_the_connection_and_its_password(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    memory_store.passwords["Shop"] = "very-secret"
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window._select("Shop")
    monkeypatch.setattr(QMessageBox, "question", answer_yes)
    window.delete()
    assert shown(window) == []
    assert memory_store.passwords == {}
    assert connections.load().connections == ()


def test_connect_hands_over_the_selected_connection(qtbot: QtBot) -> None:
    chosen = Connection(name="Shop", host="shop.example.com")
    window = window_with(qtbot, chosen)
    window._select("Shop")
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
    assert shown(window) == []
    assert len(told) == 1 and "kept" in told[0]


def test_a_new_connection_has_no_colour_and_its_button_is_greyed(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    assert not window.use_colour.isChecked()
    assert not window.colour_button.isEnabled()
    fill(window)
    window.save()
    assert connections.load().connections[0].colour == ""


def test_the_tick_switches_the_colour_on_and_off_again(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="h", colour="#aa3355"))
    window._select("Shop")
    assert window.use_colour.isChecked() and window.colour_button.isEnabled()
    window.use_colour.setChecked(False)
    window.save()
    assert connections.load().connections[0].colour == ""


def test_a_connection_with_a_colour_after_one_without_shows_its_own_state(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="A", host="h"), Connection(name="B", host="h", colour="#aa3355"))
    window._select("B")
    assert window.use_colour.isChecked()
    window._select("A")
    assert not window.use_colour.isChecked() and not window.colour_button.isEnabled()


def test_duplicate_saves_a_copy_with_its_password_ready_to_be_renamed(qtbot: QtBot, memory_store: MemoryStore) -> None:
    window = window_with(qtbot, Connection(name="example.nl", host="example.nl", user="admin", port=2222))
    memory_store.write("example.nl", "very-secret")
    window._select("example.nl")
    window.duplicate()
    copy = next(connection for connection in connections.load().connections if connection.name == "example.nl (copy)")
    assert (copy.host, copy.user, copy.port) == ("example.nl", "admin", 2222)
    assert memory_store.read("example.nl (copy)") == "very-secret"
    assert window.name.text() == "example.nl (copy)"
    assert window.name.selectedText() == "example.nl (copy)"
    window.name.setText("example.org")
    window.save()
    assert sorted(connection.name for connection in connections.load().connections) == ["example.nl", "example.org"]
    assert memory_store.read("example.org") == "very-secret"


def test_a_second_duplicate_gets_a_name_of_its_own(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"), Connection(name="Shop (copy)", host="shop.example.com"))
    window._select("Shop")
    window.duplicate()
    assert window.name.text() == "Shop (copy 2)"


def test_the_right_click_menu_holds_connect_duplicate_and_delete_only_on_a_connection(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window.show()
    menu = window.menu_for(place_of(window, "Shop"))
    assert menu is not None
    assert [action.text() for action in menu.actions() if not action.isSeparator()] == ["Connect", "Duplicate", "Delete", COPY_ADDRESS_TEXT, USE_FOR_NEW_TEXT, EMPTY_FOR_NEW_TEXT]
    assert window.selected() is not None
    assert window.menu_for(window.tree.viewport().rect().bottomLeft()) is None


def test_import_is_under_the_list_and_export_is_greyed_with_a_hover_text(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    assert window.import_button.text() == "Import…"
    assert not window.export_button.isEnabled()
    assert window.export_button.toolTip()
    for promise in (" yet", "later version", "a version", "coming"):  # it says what is true today and promises nothing (OR-23)
        assert promise not in window.export_button.toolTip(), promise
    list_part = window.parts.widget(0)
    assert list_part is not None and list_part.isAncestorOf(window.import_button)


def test_the_fields_side_can_be_made_narrow(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    fields = window.parts.widget(1)
    assert fields is not None and fields.minimumSizeHint().width() < 400


def test_the_buttons_under_the_list_and_under_the_fields_stand_on_one_line(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    bottoms = {button.mapTo(window, button.rect().bottomLeft()).y() for button in (window.import_button, window.export_button, window.address_button, window.new_button, window.save_button, window.connect_button)}
    assert len(bottoms) == 1


def test_export_stands_right_next_to_import(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    gap = window.export_button.geometry().left() - window.import_button.geometry().right()
    assert 0 < gap < window.import_button.width() // 4  # the layout's own spacing, never the stretch


def test_the_labels_of_the_fields_are_not_against_the_splitter(qtbot: QtBot) -> None:
    window = window_with(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    fields = window.parts.widget(1)
    assert fields is not None
    assert window.name.parentWidget() is fields
    assert min(child.geometry().left() for child in fields.findChildren(QLabel)) >= 6


def answering(button: QMessageBox.StandardButton, asked: list[str]) -> Callable[..., QMessageBox.StandardButton]:
    def answer(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return button

    return answer


def edited_window(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, button: QMessageBox.StandardButton, asked: list[str]) -> tuple[ConnectionList, list[Connection]]:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window._select("Shop")
    window.host.setText("new.example.com")
    monkeypatch.setattr(QMessageBox, "question", answering(button, asked))
    handed: list[Connection] = []
    window.connect_requested.connect(handed.append)
    return window, handed


def test_connect_with_changes_and_save_saves_then_connects(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    window, handed = edited_window(qtbot, monkeypatch, QMessageBox.StandardButton.Save, asked)
    window.connect_button.click()
    assert len(asked) == 1 and "Save changes to Shop?" in asked[0]
    assert connections.load().connections[0].host == "new.example.com"
    assert [connection.host for connection in handed] == ["new.example.com"]


def test_connect_with_changes_and_dont_save_connects_with_the_saved_one(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    window, handed = edited_window(qtbot, monkeypatch, QMessageBox.StandardButton.Discard, asked)
    window.connect_button.click()
    assert connections.load().connections[0].host == "shop.example.com"
    assert [connection.host for connection in handed] == ["shop.example.com"]
    assert window.host.text() == "shop.example.com"


def test_connect_with_changes_and_cancel_changes_nothing(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []
    window, handed = edited_window(qtbot, monkeypatch, QMessageBox.StandardButton.Cancel, asked)
    window.connect_button.click()
    assert handed == []
    assert window.host.text() == "new.example.com"
    assert connections.load().connections[0].host == "shop.example.com"


def test_a_typed_password_counts_as_a_change(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window._select("Shop")
    window.password.setText("typed")
    assert window.has_changes()


def test_connect_without_changes_asks_nothing(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    window._select("Shop")
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", answering(QMessageBox.StandardButton.Cancel, asked))
    handed: list[Connection] = []
    window.connect_requested.connect(handed.append)
    window.connect_button.click()
    assert asked == []
    assert len(handed) == 1


def test_connect_is_the_default_button(qtbot: QtBot) -> None:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    assert window.connect_button.isDefault()


def enter_connects_from(qtbot: QtBot, pick: Callable[[ConnectionList], QWidget]) -> list[Connection]:
    window = window_with(qtbot, Connection(name="Shop", host="shop.example.com"))
    with qtbot.waitExposed(window):
        window.show()
    window._select("Shop")
    handed: list[Connection] = []
    window.connect_requested.connect(handed.append)
    target = pick(window)
    target.setFocus()
    QTest.keyClick(target, Qt.Key.Key_Return)
    window.hide()
    return handed


def test_enter_in_the_list_connects_the_selected_connection(qtbot: QtBot) -> None:
    assert [connection.name for connection in enter_connects_from(qtbot, lambda window: window.tree)] == ["Shop"]


def test_enter_in_a_field_connects_the_selected_connection(qtbot: QtBot) -> None:
    assert [connection.name for connection in enter_connects_from(qtbot, lambda window: window.host)] == ["Shop"]


def test_enter_on_another_focused_button_still_connects(qtbot: QtBot) -> None:
    assert [connection.name for connection in enter_connects_from(qtbot, lambda window: window.import_button)] == ["Shop"]
