import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication, QMenu
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections
from chrono_ftp.core.connection import Connection
from chrono_ftp.ui import connectionlist, menus, theme, windowlayout, windowqueue, windowtabs
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.remoteside import RemoteSide

# --- Version ------------------------------------------------------------
__version__ = "test_ui_menus.py 0.0.30"

# --- Version history ----------------------------------------------------
# v0.0.30: The menu style is given to each menu; the right side shows the connection colour with the program's look
# v0.0.29: Initial: a menu lets go of the keyboard once open, every menu opens that way, and menus have a border and roomy items


def test_an_open_menu_lets_go_of_the_keyboard(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    let_go: list[bool] = []
    real = menus.let_go_of_the_keyboard

    def noted(menu: QMenu) -> None:
        let_go.append(menu.isVisible())
        real(menu)
        menu.close()  # the test answers the menu: nothing waits for a click

    monkeypatch.setattr(menus, "let_go_of_the_keyboard", noted)
    menu = QMenu()
    qtbot.addWidget(menu)
    menu.addAction("Edit settings…")
    QTimer.singleShot(5000, menu.close)  # never waits for good
    menus.open_menu(menu, QPoint(10, 10))
    assert let_go == [True]


def opened_through(monkeypatch: pytest.MonkeyPatch, module: object) -> list[QMenu]:
    opened: list[QMenu] = []

    def noted(menu: QMenu, _place: QPoint) -> None:
        opened.append(menu)

    monkeypatch.setattr(module, "open_menu", noted)
    return opened


def test_every_menu_of_the_main_window_opens_through_open_menu(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = MainWindow()
    qtbot.addWidget(window)
    from_lists = opened_through(monkeypatch, windowlayout)
    window._local_menu_wanted(QPoint(1, 1))
    window._remote_menu_wanted(QPoint(1, 1))
    assert len(from_lists) == 2
    from_queue = opened_through(monkeypatch, windowqueue)
    window._queue_menu_wanted(QPoint(1, 1))
    assert len(from_queue) == 1
    from_tabs = opened_through(monkeypatch, windowtabs)

    def a_menu(_place: QPoint) -> QMenu:
        return QMenu(window)

    monkeypatch.setattr(window, "tab_menu_for", a_menu)
    window._show_tab_menu(QPoint(1, 1))
    assert len(from_tabs) == 1


def test_the_menu_of_the_connections_window_opens_through_open_menu(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    connections.save([Connection(name="Shop", host="shop.example.com")])
    window = ConnectionList()
    qtbot.addWidget(window)
    window.show()
    opened = opened_through(monkeypatch, connectionlist)
    window._show_menu(window.list.visualItemRect(window.list.item(0)).center())
    assert len(opened) == 1


def test_a_menu_has_a_border_and_roomy_items(qapp: QApplication) -> None:
    assert "border: 1px solid" in theme.MENU_STYLE
    plain = QMenu()
    plain.addAction("Edit settings…")
    styled = QMenu()
    styled.setStyleSheet(theme.MENU_STYLE)
    styled.addAction("Edit settings…")
    assert styled.sizeHint().height() > plain.sizeHint().height()
    assert styled.sizeHint().width() > plain.sizeHint().width()


def test_open_menu_gives_the_menu_the_menu_style(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def closed(menu: QMenu) -> None:
        menu.close()  # the test answers the menu: nothing waits for a click

    monkeypatch.setattr(menus, "let_go_of_the_keyboard", closed)
    menu = QMenu()
    qtbot.addWidget(menu)
    menu.addAction("Edit settings…")
    QTimer.singleShot(5000, menu.close)  # never waits for good
    menus.open_menu(menu, QPoint(10, 10))
    assert menu.styleSheet() == theme.MENU_STYLE


def test_the_right_side_shows_the_connection_colour_with_the_look_of_the_program(qtbot: QtBot, qapp: QApplication) -> None:
    theme.apply(qapp)
    try:
        side = RemoteSide()
        qtbot.addWidget(side)
        side.resize(600, 400)
        with qtbot.waitExposed(side):
            side.show()
        side.set_colour("#00007f")
        image = side.file_list.viewport().grab().toImage()
        assert image.pixelColor(image.width() // 2, image.height() - 5).name() == "#00007f"
    finally:
        qapp.setStyleSheet("")  # leaves nothing behind, also on a version that set one
