"""Block FQ in the window: file colours on both sides with the selection over them and the Colours page (FQ-01), the thumbnail view of
each side, per tab and giving way to the person (FQ-02), and the toolbar menu with its icon sizes, text under buttons and standard layout,
kept for next time (FQ-03)."""

import threading
from collections.abc import Callable, Generator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QImage, QImageWriter, QPalette, QStyleHints
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QColorDialog, QLabel, QMenu, QToolButton, QWidget
from pytestqt.qtbot import QtBot

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.settings import LARGE, MEDIUM, SMALL, ToolbarChoices
from chrono_ftp.connections.base import RemoteEntry
from chrono_ftp.core.connection import Connection
from chrono_ftp.core import colours
from chrono_ftp.core.filecolours import ColourRule
from chrono_ftp.core.errors import ConnectionLost, RefusedForNow, RemoteActionFailed, TransferInterrupted
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.engine.thumbnails import MEGABYTE
from chrono_ftp.ui import icons, theme, thumbnails, toolbars
from chrono_ftp.ui.actiondialogs import NewFolderDialog
from chrono_ftp.ui.bridge import Bridge
from chrono_ftp.ui.connectionlist import colour_block
from chrono_ftp.ui.colourrules import ColourRulesEditor
from chrono_ftp.ui.connectionlist import ConnectionList
from chrono_ftp.ui.connectiontree import ConnectionTree
from chrono_ftp.ui.filelist import FROM_LOCAL, FROM_REMOTE, LOCAL_COLUMN_COUNT, THUMBNAIL_PIXELS, FileList
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.menus import as_drop_down
from chrono_ftp.ui.remoteside import RemoteSide
from chrono_ftp.ui.settingsdialog import SettingsDialog
from chrono_ftp.ui.thumbnails import Source, ThumbnailSide
from chrono_ftp.ui.toolbars import NO_BUTTON_TIP, RESTORE_TEXT, TEXT_UNDER_TEXT
from tests.fakeservers import ssh_server
from tests.ui.test_ui_sessions import connected, ssh_settings, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_looks.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: The look (FQ-04): light chosen in the settings shows at once in the window, the menus and the icons; the desktop is
#          followed while the program runs; a desktop that says nothing gives dark; a change of look leaves the person's own colours
# v0.0.39: Review: a picture refused for now comes later, a lasting refusal is not asked again; a lost connection greys the tab of
#          its fetch; a picture is decoded off the thread that reads it
# v0.0.39: Initial: block FQ in the window (FQ-01 to FQ-03)

__all__ = ["window"]  # the fixture, used by name

LOG_RULE: ColourRule = ColourRule("*.log", "#ffd000", "#5a1010")
CONNECTION_COLOUR: str = "#2e7d4f"


def picture_bytes(width: int = 200, height: int = 100) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#3070c0"))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    QImageWriter(buffer, QByteArray(b"PNG")).write(image)
    buffer.close()
    return bytes(data.data())


def pixel_of(view: FileList, name: str, column: int) -> QColor:
    """The colour in the middle of an empty cell of this row, as it is drawn."""
    index = view.files.index(view.index_of(name).row(), column)
    rect = view.visualRect(index)
    image = view.viewport().grab().toImage()
    return image.pixelColor(rect.center().x(), rect.center().y())


def entries() -> list[RemoteEntry]:
    return [RemoteEntry("server.log", False, 10), RemoteEntry("notes.txt", False, 10), RemoteEntry("logs.log", True)]  # no dates: "Changed" stays empty


# --- file colours (FQ-01) -----------------------------------------------------------------------------------------------------------


def test_a_file_colour_shows_on_its_row_and_the_selection_shows_over_it(qtbot: QtBot) -> None:
    view = FileList(LOCAL_COLUMN_COUNT, FROM_LOCAL, frozenset({FROM_REMOTE}))
    qtbot.addWidget(view)
    view.set_file_colours((LOG_RULE,))
    view.show_entries(entries(), False, False)
    view.resize(700, 300)
    view.show()
    qtbot.waitExposed(view)
    assert pixel_of(view, "server.log", 2).name() == LOG_RULE.background
    assert view.files.index(view.index_of("server.log").row(), 0).data(Qt.ItemDataRole.ForegroundRole).color().name() == LOG_RULE.text
    assert pixel_of(view, "notes.txt", 2).name() != LOG_RULE.background  # a name no rule fits keeps its side's look
    assert pixel_of(view, "logs.log", 2).name() != LOG_RULE.background  # *.log is for files: a folder keeps its look
    view.setCurrentIndex(view.index_of("server.log"))
    selected = pixel_of(view, "server.log", 2)
    highlights = {view.palette().color(group, QPalette.ColorRole.Highlight).name() for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)}
    assert selected.name() in highlights  # the selection shows clearly over a coloured row


def test_the_right_side_shows_file_colours_only_while_its_connection_is_live(qtbot: QtBot) -> None:
    side = RemoteSide()
    qtbot.addWidget(side)
    side.file_list.set_file_colours((LOG_RULE,))
    side.set_colour(CONNECTION_COLOUR)
    side.show_folder("/", entries())
    view = side.file_list
    log_row, other_row = view.index_of("server.log").row(), view.index_of("notes.txt").row()
    assert view.files.index(log_row, 0).data(Qt.ItemDataRole.BackgroundRole).color().name() == LOG_RULE.background
    assert view.files.index(other_row, 0).data(Qt.ItemDataRole.BackgroundRole) is None  # the connection's colour stays the rest (OR-2)
    side.set_colour(None)  # dropped: a colour on the right always means a live connection (OR-3)
    assert view.files.index(log_row, 0).data(Qt.ItemDataRole.BackgroundRole) is None
    assert view.files.index(log_row, 0).data(Qt.ItemDataRole.ForegroundRole) is None


def test_the_colours_page_makes_orders_and_removes_rules(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    dialog = SettingsDialog(saved_settings.Settings(file_colours=(LOG_RULE,)))
    qtbot.addWidget(dialog)
    dialog.show_page("Colours")
    editor = dialog.file_colours
    editor.add_rule()
    assert not editor.text_button.isEnabled() or editor.text_box.isChecked()
    QTest.keyClicks(editor.mask_field, "*.tmp; build/")
    def picked(*_args: object) -> QColor:
        return QColor("#123456")

    monkeypatch.setattr(QColorDialog, "getColor", staticmethod(picked))
    editor.pick(True)
    editor.background_box.setChecked(True)
    assert dialog.chosen().file_colours == (LOG_RULE, ColourRule("*.tmp; build/", "#123456", colours.FIRST_FILE_BACKGROUND))
    editor.move_rule(-1)
    assert [rule.mask for rule in dialog.chosen().file_colours] == ["*.tmp; build/", "*.log"]
    assert not editor.up_button.isEnabled() and editor.down_button.isEnabled()
    editor.delete_rule()
    editor.delete_rule()
    assert dialog.chosen().file_colours == ()
    assert not editor.mask_field.isEnabled() and editor.mask_field.toolTip()  # greyed with its reason (OR-20)


def test_a_new_rule_without_a_mask_is_left_out(qtbot: QtBot) -> None:
    dialog = SettingsDialog(saved_settings.Settings())
    qtbot.addWidget(dialog)
    dialog.file_colours.add_rule()
    assert dialog.chosen().file_colours == ()


def test_ok_in_settings_colours_the_rows_at_once(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "server.log").write_text("x", encoding="utf-8")
    window.local_side.go_to(str(tmp_path))
    chosen = saved_settings.Settings(file_colours=(LOG_RULE,))
    def accepted(_self: SettingsDialog) -> int:
        return SettingsDialog.DialogCode.Accepted.value

    def given(_self: SettingsDialog, _now: saved_settings.Settings | None = None) -> saved_settings.Settings:
        return chosen

    monkeypatch.setattr(SettingsDialog, "exec", accepted)
    monkeypatch.setattr(SettingsDialog, "chosen", given)
    window.open_settings()
    view = window.local_side.file_list
    assert view.files.index(view.index_of("server.log").row(), 0).data(Qt.ItemDataRole.BackgroundRole).color().name() == LOG_RULE.background
    assert saved_settings.load().file_colours == (LOG_RULE,)


# --- thumbnails (FQ-02) -------------------------------------------------------------------------------------------------------------


def decoration(view: FileList, name: str) -> QIcon:
    found = view.files.index(view.index_of(name).row(), 0).data(Qt.ItemDataRole.DecorationRole)
    assert isinstance(found, QIcon)
    return found


def is_picture(view: FileList, name: str) -> bool:
    return decoration(view, name).cacheKey() != view.files.icons[1].cacheKey()


def pictures_folder(folder: Path) -> Path:
    folder.mkdir()
    (folder / "a.png").write_bytes(picture_bytes())
    (folder / "broken.png").write_bytes(b"\x89PNG not really a picture")
    (folder / "big.png").write_bytes(picture_bytes() + b"\0" * (MEGABYTE + 1))  # over a limit of 1 MB
    (folder / "notes.txt").write_text("not a picture", encoding="utf-8")
    return folder


def test_the_thumbnail_view_of_this_computer_shows_pictures_and_keeps_the_rest_plain(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    folder = pictures_folder(tmp_path / "pictures")
    window.settings = saved_settings.Settings(thumbnail_megabytes=1)
    window._apply_settings()
    window.show()
    qtbot.waitExposed(window)
    window.local_side.go_to(str(folder))
    view = window.local_side.file_list
    assert not view.thumbnails_on
    window.local_side.navigation.buttons["thumbnails"].trigger()
    assert view.thumbnails_on and view.iconSize().height() == THUMBNAIL_PIXELS
    qtbot.waitUntil(lambda: is_picture(view, "a.png"))
    qtbot.waitUntil(lambda: any(key.name == "broken.png" for key in window.local_thumbnails.kept))
    qtbot.waitUntil(lambda: window.local_thumbnails.fetching is None)
    assert {key.name for key in window.local_thumbnails.kept} == {"a.png", "broken.png"}  # big.png and notes.txt are never read
    assert decoration(view, "a.png").actualSize(view.iconSize()).width() == THUMBNAIL_PIXELS
    assert not is_picture(view, "broken.png")  # a picture that can't be shown keeps its normal icon
    window.local_side.navigation.buttons["thumbnails"].trigger()
    assert not view.thumbnails_on and not is_picture(view, "a.png")


def test_thumbnails_wait_while_the_connection_is_busy_and_give_way_at_once(qtbot: QtBot, tmp_path: Path) -> None:
    view = FileList(LOCAL_COLUMN_COUNT, FROM_LOCAL, frozenset({FROM_REMOTE}))
    qtbot.addWidget(view)
    view.folder = "/pictures"
    view.show_entries([RemoteEntry("a.png", False, 100, 1.0), RemoteEntry("b.png", False, 100, 1.0)], False, False)
    view.resize(400, 300)
    view.show()
    qtbot.waitExposed(view)
    bridge = Bridge()  # the answers come on the window's thread, as in the program
    worker = Worker(bridge.deliver)
    allowed = [False]
    read: list[str] = []
    other_work = threading.Event()
    reading = threading.Event()

    def reader(name: str, keep_going: Callable[[], bool]) -> bytes | None:
        read.append(name)
        reading.set()
        if name == "a.png":
            other_work.wait(5)
            return None if not keep_going() else picture_bytes()  # the person's request came: it gives way
        return picture_bytes()

    side = ThumbnailSide(view)
    side.source_of = lambda: Source(reader, worker, lambda: allowed[0], worker)
    side.set_rules(5, "*.png")
    side.switch(True)
    side.fetch_next()
    assert side.fetching is None and side.timer.isActive()  # busy: nothing is fetched, it looks again later
    allowed[0] = True
    side.fetch_next()
    assert reading.wait(5)
    worker.submit(lambda: None, lambda _result: None, lambda _error: None)  # the person's own request, behind the fetch
    other_work.set()
    qtbot.waitUntil(lambda: is_picture(view, "b.png"))
    qtbot.waitUntil(lambda: is_picture(view, "a.png"))
    assert read == ["a.png", "a.png", "b.png"]  # a.png gave way, and came again first, in the order of the view, once the connection was free
    worker.stop()


def two_pictures(qtbot: QtBot) -> FileList:
    view = FileList(LOCAL_COLUMN_COUNT, FROM_LOCAL, frozenset({FROM_REMOTE}))
    qtbot.addWidget(view)
    view.folder = "/pictures"
    view.show_entries([RemoteEntry("a.png", False, 100, 1.0), RemoteEntry("b.png", False, 100, 1.0)], False, False)
    view.resize(400, 300)
    view.show()
    qtbot.waitExposed(view)
    return view


@pytest.mark.parametrize("for_now", [RefusedForNow("450 busy"), TransferInterrupted("426 broken off")])
def test_a_picture_refused_for_now_comes_later_and_a_lasting_refusal_is_not_asked_again(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, for_now: Exception) -> None:
    monkeypatch.setattr(thumbnails, "FIRST_RETRY_SECONDS", 0.05)
    view = two_pictures(qtbot)
    bridge = Bridge()
    worker = Worker(bridge.deliver)
    read: list[str] = []

    def reader(name: str, _keep_going: Callable[[], bool]) -> bytes | None:
        read.append(name)
        if name == "b.png":
            raise RemoteActionFailed("Permission denied")  # a lasting answer
        if read.count("a.png") == 1:
            raise for_now
        return picture_bytes()

    side = ThumbnailSide(view)
    side.source_of = lambda: Source(reader, worker, worker.idle, worker)
    side.set_rules(5, "*.png")
    side.switch(True)
    side.fetch_next()
    qtbot.waitUntil(lambda: is_picture(view, "a.png"))  # tried again once its wait was over
    qtbot.waitUntil(lambda: side.fetching is None)
    assert read.count("a.png") == 2 and read.count("b.png") == 1  # the lasting refusal was not asked for again
    assert not is_picture(view, "b.png")
    worker.stop()


def test_a_picture_is_decoded_off_the_thread_that_reads_it(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    view = two_pictures(qtbot)
    bridge = Bridge()
    reading, decoding = Worker(bridge.deliver), Worker(bridge.deliver)
    threads: dict[str, set[int]] = {"read": set(), "decode": set()}
    real = thumbnails.picture_from

    def reader(_name: str, _keep_going: Callable[[], bool]) -> bytes | None:
        threads["read"].add(threading.get_ident())
        return picture_bytes()

    def decoded(data: bytes) -> QImage | None:
        threads["decode"].add(threading.get_ident())
        return real(data)

    monkeypatch.setattr(thumbnails, "picture_from", decoded)
    side = ThumbnailSide(view)
    side.source_of = lambda: Source(reader, reading, reading.idle, decoding)
    side.set_rules(5, "*.png")
    side.switch(True)
    side.fetch_next()
    qtbot.waitUntil(lambda: is_picture(view, "a.png") and is_picture(view, "b.png"))
    assert threads["read"] and threads["decode"] and not threads["read"] & threads["decode"]  # the tab's thread only reads the bytes
    reading.stop()
    decoding.stop()


def test_a_lost_connection_found_by_a_thumbnail_greys_its_own_tab(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = pictures_folder(tmp_path / "server")
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server, "Shop"))
        source = window._remote_source()
        assert source is not None
        connected(qtbot, window, ssh_settings(server, "Other"))
        assert window.current() is window.sessions[1]
        source.lost(ConnectionLost("The connection was lost."))
        assert not window.sessions[0].alive and window.sessions[1].alive


def test_the_thumbnails_of_the_server_come_in_the_background_and_each_tab_keeps_its_view(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = pictures_folder(tmp_path / "server")
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        window.show()
        qtbot.waitExposed(window)
        connected(qtbot, window, ssh_settings(server, "Shop"))
        view = window.remote_side.file_list
        window.remote_side.navigation.buttons["thumbnails"].trigger()
        qtbot.waitUntil(lambda: is_picture(view, "a.png"))
        assert window.sessions[0].remote_thumbnails
        connected(qtbot, window, ssh_settings(server, "Other"))
        assert not view.thumbnails_on and not window.remote_side.navigation.buttons["thumbnails"].isChecked()
        window.tab_bar.setCurrentIndex(0)
        assert view.thumbnails_on and window.remote_side.navigation.buttons["thumbnails"].isChecked()
        assert is_picture(view, "a.png")  # kept in memory: not fetched again
        window.tab_bar.setCurrentIndex(1)


# --- toolbars (FQ-03) --------------------------------------------------------------------------------------------------------------


def noted_in(opened: list[QMenu]) -> Callable[[QMenu, QPoint], None]:
    def noted(menu: QMenu, _place: QPoint) -> None:
        opened.append(menu)

    return noted


def menu_of(window: MainWindow, monkeypatch: pytest.MonkeyPatch, name: str, place: QPoint) -> QMenu:
    opened: list[QMenu] = []
    monkeypatch.setattr(toolbars, "open_menu", noted_in(opened))
    window.toolbars.menu_wanted(name, place, window.top_row)
    assert len(opened) == 1  # through open_menu, as every menu (D-052)
    return opened[0]


def entry(menu: QMenu, text: str) -> QAction:
    return next(action for action in menu.actions() if action.text() == text)


def place_of_button(window: MainWindow, name: str, button: str) -> QPoint:
    toolbar = window.toolbars.bars[name]
    widget = toolbar.bar.widgetForAction(toolbar.buttons[button])
    assert isinstance(widget, QWidget)
    return widget.geometry().center()


def test_a_hidden_toolbar_keeps_its_keys_and_comes_back_next_time(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with qtbot.waitExposed(window):
        window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)
    menu = menu_of(window, monkeypatch, "local_files", QPoint(1, 1))
    item = entry(menu, "Left Side: File Buttons")
    assert item.isChecked()
    item.trigger()
    assert not window.local_side.actions_bar.isVisible()
    assert saved_settings.load().toolbars.hidden == ("local_files",)
    asked: list[bool] = []
    def cancelled(_self: NewFolderDialog) -> int:
        asked.append(True)
        return NewFolderDialog.DialogCode.Rejected.value

    monkeypatch.setattr(NewFolderDialog, "exec", cancelled)
    window.local_side.file_list.setFocus()
    QTest.keyClick(window.local_side.file_list, Qt.Key.Key_F7)
    assert asked == [True]  # F7 still makes a new folder
    again = MainWindow()
    qtbot.addWidget(again)
    again.show()
    qtbot.waitExposed(again)
    assert not again.local_side.actions_bar.isVisible() and again.remote_side.actions_bar.isVisible()
    again.close()


def test_the_icon_sizes_follow_the_menu_and_medium_is_the_size_before(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    sizes = {name: toolbar.bar.iconSize().height() for name, toolbar in window.toolbars.bars.items()}
    assert sizes == {"top": 18, "local_folders": 24, "local_files": 24, "remote_folders": 24, "remote_files": 24, "queue": 18}
    entry(menu_of(window, monkeypatch, "top", QPoint(1, 1)), "Large Icons").trigger()
    assert window.local_side.navigation.iconSize().height() == 36 and window.general_bar.iconSize().height() == 27
    entry(menu_of(window, monkeypatch, "top", QPoint(1, 1)), "Small Icons").trigger()
    assert window.local_side.navigation.iconSize().height() == 18 and window.queue_panel.bar.iconSize().height() == 14
    assert saved_settings.load().toolbars.icon_size == SMALL
    menu = menu_of(window, monkeypatch, "top", QPoint(1, 1))
    assert [action.text() for action in menu.actions() if action.isChecked() and "Icons" in action.text()] == ["Small Icons"]


def test_text_under_a_chosen_button_and_the_standard_layout(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    window.show()
    qtbot.waitExposed(window)
    upload = window.local_side.actions_bar.widgetForAction(window.actions_by_name["upload"])
    assert isinstance(upload, QToolButton) and upload.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    entry(menu_of(window, monkeypatch, "local_files", place_of_button(window, "local_files", "upload")), TEXT_UNDER_TEXT).trigger()
    assert upload.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextUnderIcon
    assert saved_settings.load().toolbars.text_under == ("local_files/upload",)
    between = menu_of(window, monkeypatch, "local_files", QPoint(window.local_side.actions_bar.width() - 2, 2))
    assert not entry(between, TEXT_UNDER_TEXT).isEnabled() and entry(between, TEXT_UNDER_TEXT).toolTip() == NO_BUTTON_TIP  # greyed with its reason (OR-20)
    entry(menu_of(window, monkeypatch, "top", QPoint(1, 1)), "Queue Buttons").trigger()
    entry(menu_of(window, monkeypatch, "top", QPoint(1, 1)), "Large Icons").trigger()
    entry(menu_of(window, monkeypatch, "top", QPoint(1, 1)), RESTORE_TEXT).trigger()
    assert upload.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert window.queue_panel.bar.isVisibleTo(window.queue_panel) and window.general_bar.iconSize().height() == 18
    stop_all = window.queue_panel.bar.widgetForAction(window.queue_panel.actions_by_name["stop_all"])
    assert isinstance(stop_all, QToolButton) and stop_all.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonTextBesideIcon  # as it was at first
    assert saved_settings.load().toolbars == ToolbarChoices((), MEDIUM, ())


def test_the_top_row_has_the_menu_when_every_toolbar_is_hidden(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    window.toolbars.apply(ToolbarChoices(tuple(window.toolbars.bars), LARGE, ()))
    opened: list[QMenu] = []
    monkeypatch.setattr(toolbars, "open_menu", noted_in(opened))
    window.top_row.customContextMenuRequested.emit(QPoint(1, 1))  # as a right-click on its empty part
    assert len(opened) == 1
    entry(opened[0], "Top Bar").trigger()
    assert window.general_bar.isVisibleTo(window)


# --- the look: dark, light or the desktop's (FQ-04) ---------------------------------------------------------------------------------


@pytest.fixture
def the_dark_look_again(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """The look is one thing for the whole program. Every test here starts in the dark look on a desktop that says nothing, whatever
    desktop it is really run on, and leaves the dark look behind it."""
    monkeypatch.setattr(QStyleHints, "colorScheme", says_nothing)
    theme.apply(qapp, colours.DARK)
    yield
    theme.apply(qapp, colours.DARK)


def chose_in_settings(window: MainWindow, monkeypatch: pytest.MonkeyPatch, settings: saved_settings.Settings) -> None:
    """OK in the settings window with these settings, as a person pressing OK."""
    def accepted(_self: SettingsDialog) -> int:
        return SettingsDialog.DialogCode.Accepted.value

    def given(_self: SettingsDialog, _now: saved_settings.Settings | None = None) -> saved_settings.Settings:
        return settings

    monkeypatch.setattr(SettingsDialog, "exec", accepted)
    monkeypatch.setattr(SettingsDialog, "chosen", given)
    window.open_settings()


def says_light(_self: QStyleHints) -> Qt.ColorScheme:
    return Qt.ColorScheme.Light


def says_dark(_self: QStyleHints) -> Qt.ColorScheme:
    return Qt.ColorScheme.Dark


def says_nothing(_self: QStyleHints) -> Qt.ColorScheme:
    """A desktop that gives no answer: not every desktop tells a program whether it is light or dark."""
    return Qt.ColorScheme.Unknown


def icon_colours(name: str) -> set[str]:
    image = icons.icon(name).pixmap(QSize(44, 44)).toImage()
    return {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() == 255}


def test_the_light_look_chosen_in_settings_shows_at_once_in_the_window_the_menus_and_the_icons(
    qtbot: QtBot, qapp: QApplication, the_dark_look_again: None, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert colours.in_use() == colours.DARK
    chose_in_settings(window, monkeypatch, saved_settings.Settings(look=colours.LIGHT))
    assert colours.in_use() == colours.LIGHT
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == colours.LIGHT_LOOK.window
    assert qapp.palette().color(QPalette.ColorRole.WindowText).name() == colours.LIGHT_LOOK.text
    assert colours.LIGHT_LOOK.menu_background in theme.menu_style()
    assert colours.LIGHT_LOOK.text in icon_colours("tab-new")  # an icon follows the look without being put back
    assert window.remote_side.file_list.palette().color(QPalette.ColorRole.Base).name() == colours.LIGHT_LOOK.not_connected  # no connection: plain grey (OR-3)
    assert saved_settings.load().look == colours.LIGHT
    chose_in_settings(window, monkeypatch, saved_settings.Settings(look=colours.DARK))
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == colours.DARK_LOOK.window
    assert colours.DARK_LOOK.text in icon_colours("tab-new")


def test_following_the_desktop_turns_the_window_light_when_the_desktop_turns_light(
    qtbot: QtBot, qapp: QApplication, the_dark_look_again: None, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window.settings = replace(window.settings, look=colours.FOLLOW_DESKTOP)
    monkeypatch.setattr(QStyleHints, "colorScheme", says_light)
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)  # as the desktop turning light while Chrono FTP is open
    assert colours.in_use() == colours.LIGHT
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == colours.LIGHT_LOOK.window
    monkeypatch.setattr(QStyleHints, "colorScheme", says_dark)
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
    assert colours.in_use() == colours.DARK


def test_a_desktop_the_program_does_not_follow_leaves_the_look_alone(
    qtbot: QtBot, qapp: QApplication, the_dark_look_again: None, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    window.settings = replace(window.settings, look=colours.DARK)
    monkeypatch.setattr(QStyleHints, "colorScheme", says_light)
    qapp.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Light)
    assert colours.in_use() == colours.DARK  # the person chose dark: the desktop does not override it


def test_a_desktop_that_says_nothing_gives_the_dark_look(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(QStyleHints, "colorScheme", says_nothing)
    assert theme.desktop_look(qapp) == colours.DARK
    assert theme.look_wanted(colours.FOLLOW_DESKTOP, qapp) == colours.DARK
    assert theme.look_wanted("a look from a damaged settings file", qapp) == colours.DARK  # OR-15


def test_a_change_of_look_keeps_the_colour_of_a_live_connection_and_the_file_rules(
    qtbot: QtBot, qapp: QApplication, the_dark_look_again: None, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "server.log").write_text("x", encoding="utf-8")
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server, "Shop", CONNECTION_COLOUR))
        window.remote_side.file_list.set_file_colours((LOG_RULE,))
        view = window.remote_side.file_list
        row = view.index_of("server.log").row()
        chose_in_settings(window, monkeypatch, saved_settings.Settings(look=colours.LIGHT, file_colours=(LOG_RULE,)))
        assert view.palette().color(QPalette.ColorRole.Base).name() == CONNECTION_COLOUR  # the person's own colour, untouched (OR-2)
        assert window.tab_bar.background_of(0) == CONNECTION_COLOUR
        assert view.files.index(row, 0).data(Qt.ItemDataRole.BackgroundRole).color().name() == LOG_RULE.background
        assert view.files.index(row, 0).data(Qt.ItemDataRole.ForegroundRole).color().name() == LOG_RULE.text


def test_the_right_side_follows_the_look_in_every_colour_it_does_not_set_itself(qtbot: QtBot, qapp: QApplication, the_dark_look_again: None) -> None:
    """The right side keeps a palette of its own for the connection's colour; the rest must still be the look of the moment."""
    theme.apply(qapp, colours.DARK)
    side = RemoteSide()
    qtbot.addWidget(side)
    side.set_colour(None)
    theme.apply(qapp, colours.LIGHT)
    side.set_colour("")  # a live connection without a colour of its own, as the window does after a change of look
    for view in (side.tree, side.file_list):
        for role in (QPalette.ColorRole.AlternateBase, QPalette.ColorRole.Window, QPalette.ColorRole.Button, QPalette.ColorRole.Mid):
            assert view.palette().color(role).name() == qapp.palette().color(role).name()
        assert view.palette().color(QPalette.ColorRole.Base).name() == colours.LIGHT_LOOK.panel
    side.set_colour(CONNECTION_COLOUR)
    assert side.file_list.palette().color(QPalette.ColorRole.Base).name() == CONNECTION_COLOUR  # the person's own colour wins (OR-2)


def test_the_lock_in_the_status_bar_follows_the_look(qtbot: QtBot, qapp: QApplication, the_dark_look_again: None, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    def ink_of(label: QWidget) -> set[str]:
        assert isinstance(label, QLabel)
        image = label.pixmap().toImage()
        return {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() == 255}

    assert colours.DARK_LOOK.text in ink_of(window.lock_label)
    chose_in_settings(window, monkeypatch, saved_settings.Settings(look=colours.LIGHT))
    assert colours.LIGHT_LOOK.text in ink_of(window.lock_label)
    assert colours.DARK_LOOK.text not in ink_of(window.lock_label)


def test_an_icon_on_a_selected_row_is_drawn_in_the_colour_that_reads_on_the_selection(qapp: QApplication, the_dark_look_again: None) -> None:
    image = icons.icon("folder-open").pixmap(QSize(44, 44), QIcon.Mode.Selected).toImage()
    seen = {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() == 255}
    assert colours.ON_SELECTION in seen and colours.TEXT not in seen


def test_the_colour_blocks_of_the_connection_list_follow_the_look(qtbot: QtBot, qapp: QApplication, the_dark_look_again: None) -> None:
    tree = ConnectionTree()
    qtbot.addWidget(tree)
    tree.fill([Connection(name="Shop", host="h", colour=CONNECTION_COLOUR), Connection(name="Plain", host="h")], (), colour_block)

    def block_of(name: str) -> str:
        found = tree.item_of(name)
        assert found is not None
        return found.icon(0).pixmap(QSize(16, 16)).toImage().pixelColor(8, 8).name()

    assert block_of("Plain") == colours.DARK_LOOK.no_colour_tab
    colours.use(colours.LIGHT)
    tree.draw_the_colour_blocks(lambda name: CONNECTION_COLOUR if name == "Shop" else "", colour_block)
    assert block_of("Plain") == colours.LIGHT_LOOK.no_colour_tab
    assert block_of("Shop") == CONNECTION_COLOUR  # the person's own colour, untouched by the look (OR-2)


def test_a_rule_that_gives_only_a_background_keeps_readable_text_on_both_looks(qtbot: QtBot, qapp: QApplication, the_dark_look_again: None) -> None:
    """The person's colour is untouched; the half they left empty is black or white by that colour's brightness (D-007, FQ-04)."""
    made_in_the_dark_look = ColourRule("*.log", "", colours.DARK_LOOK.first_file_background)
    made_in_the_light_look = ColourRule("*.txt", "", colours.LIGHT_LOOK.first_file_background)
    view = FileList(LOCAL_COLUMN_COUNT, FROM_LOCAL, frozenset({FROM_REMOTE}))
    qtbot.addWidget(view)
    view.set_file_colours((made_in_the_dark_look, made_in_the_light_look))
    view.show_entries(entries(), False, False)
    for look in (colours.DARK, colours.LIGHT):
        theme.apply(qapp, look)
        for rule, name in ((made_in_the_dark_look, "server.log"), (made_in_the_light_look, "notes.txt")):
            row = view.index_of(name).row()
            assert view.files.index(row, 0).data(Qt.ItemDataRole.BackgroundRole).color().name() == rule.background  # theirs, untouched
            assert view.files.index(row, 0).data(Qt.ItemDataRole.ForegroundRole).color().name() == colours.readable_text(rule.background)


def test_the_colours_page_shows_a_rule_the_way_the_file_list_draws_it(qtbot: QtBot, qapp: QApplication, the_dark_look_again: None) -> None:
    """What the Colours page shows is what a row will look like, also for a rule that gives only a background (FQ-01, FQ-04)."""
    background_only = ColourRule("*.log", "", colours.DARK_LOOK.first_file_background)
    both = ColourRule("*.txt", "#ff8800", "#202830")
    editor = ColourRulesEditor((background_only, both))
    qtbot.addWidget(editor)
    for rule, row in ((background_only, 0), (both, 1)):
        item = editor.list.item(row)
        assert item is not None
        assert item.data(Qt.ItemDataRole.BackgroundRole).name() == rule.background
        assert item.data(Qt.ItemDataRole.ForegroundRole).name() == (rule.text or colours.readable_text(rule.background))


def test_the_tabs_follow_the_look_both_live_without_a_colour_and_dropped(
    qtbot: QtBot, qapp: QApplication, the_dark_look_again: None, window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tab of a live connection without a colour of its own, and one whose connection dropped, are drawn in the look's greys (OR-3)."""
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server, "Shop", ""))  # live, and no colour of its own
        assert window.tab_bar.background_of(0) == colours.DARK_LOOK.no_colour_tab
        chose_in_settings(window, monkeypatch, saved_settings.Settings(look=colours.LIGHT))
        assert window.tab_bar.background_of(0) == colours.LIGHT_LOOK.no_colour_tab
        session = window.sessions[0]
        session.alive = False
        window._set_tab(session)  # as a connection that drops
        assert window.tab_bar.background_of(0) == colours.LIGHT_LOOK.not_connected_tab
        chose_in_settings(window, monkeypatch, saved_settings.Settings(look=colours.DARK))
        assert window.tab_bar.background_of(0) == colours.DARK_LOOK.not_connected_tab


def test_a_menu_opened_after_a_change_of_look_is_drawn_in_the_new_look(qtbot: QtBot, qapp: QApplication, the_dark_look_again: None) -> None:
    """A drop-down menu is made once and lives on, so it takes its colours each time it opens (D-052, FQ-04)."""
    menu = QMenu()
    qtbot.addWidget(menu)
    menu.addAction("Edit settings…")
    as_drop_down(menu)
    menu.aboutToShow.emit()
    assert colours.DARK_LOOK.menu_background in menu.styleSheet()
    theme.apply(qapp, colours.LIGHT)
    menu.aboutToShow.emit()  # as Qt does the moment it opens
    assert colours.LIGHT_LOOK.menu_background in menu.styleSheet()
    assert colours.DARK_LOOK.menu_background not in menu.styleSheet()


def test_the_connection_list_window_follows_the_look_while_it_stands_open(
    qtbot: QtBot, qapp: QApplication, the_dark_look_again: None, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its colour blocks are pictures made once, and its window can stand open while the look changes (FQ-04)."""
    window.show_connection_list()
    opened = window.connection_list
    assert isinstance(opened, ConnectionList)
    listing: ConnectionList = opened
    qtbot.addWidget(listing)
    listing.connections = [Connection(name="Plain", host="h")]
    listing._fill_list()

    def block_of(name: str) -> str:
        found = listing.tree.item_of(name)
        assert found is not None
        return found.icon(0).pixmap(QSize(16, 16)).toImage().pixelColor(8, 8).name()

    assert block_of("Plain") == colours.DARK_LOOK.no_colour_tab
    chose_in_settings(window, monkeypatch, saved_settings.Settings(look=colours.LIGHT))
    assert block_of("Plain") == colours.LIGHT_LOOK.no_colour_tab
