from collections.abc import Callable
import time
from dataclasses import replace
from typing import override
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QMimeData, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QDropEvent, QGuiApplication, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QProxyStyle, QStyle, QStyleHintReturn, QStyleOption, QWidget
from pytestqt.qtbot import QtBot

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.bookmarks import LOCAL
from chrono_ftp.config.settings import BYTES, FILE_MANAGER_KEYS, TITLE_FULL, TITLE_NONE
from chrono_ftp.connections.base import RemoteEntry
from chrono_ftp.ui import windowselection
from chrono_ftp.ui.filelist import FROM_LOCAL, FROM_OUTSIDE, FROM_REMOTE, FileList
from chrono_ftp.ui.localside import LocalSide
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.sidebar import FolderHistory
from chrono_ftp.ui.windowselection import DRAG_MOVE_OFF_TEXT, MOVE_HERE_TEXT
from tests.fakeservers import ssh_server
from tests.ui.test_ui_sessions import connected, ssh_settings, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_selection.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: The Select submenu ends with Select by Mask and Unselect by Mask (FD-05)
# v0.0.40: Review: Select All, Invert, a key and a refresh stay quick on 10,000 files; typing starts afresh when the list is filled again or something opens; a right-click never opens, also on a desktop
#          that opens with one click
# v0.0.40: Initial: block FG on the screen: type-ahead, the selection keys on the list with the focus (OR-21), the two-pane mode, the key
#          sets and sort keys, This File Only, the size format, the column header menu, natural sort, the hidden counts, the history
#          drop-down, the clipboard, the title bar, the right-button drag and its menu, pausing the refresh and clearing the listings,
#          Ctrl+R, the drop-down of the path field

__all__ = ["window"]  # the fixture, used by name

ENTRIES: list[RemoteEntry] = [
    RemoteEntry("beta.txt", False, 10),
    RemoteEntry("bravo.log", False, 2000),
    RemoteEntry("alpha.txt", False, 300),
    RemoteEntry("file10", False, 1),
    RemoteEntry("file2", False, 1),
    RemoteEntry("zoo", True),
    RemoteEntry("Docs", True),
    RemoteEntry(".hidden", False, 1),
]


def list_for(qtbot: QtBot) -> FileList:
    view = FileList(7, FROM_REMOTE, frozenset({FROM_LOCAL, FROM_OUTSIDE, FROM_REMOTE}))
    qtbot.addWidget(view)
    view.resize(600, 400)
    view.show_entries(list(ENTRIES), True, False)
    return view


def chosen(view: FileList) -> list[str]:
    return [entry.name for entry in view.selected_entries()]


def current(view: FileList) -> str:
    entry = view.current_entry()
    return entry.name if entry is not None else ""


def press(widget: QWidget, key: str) -> None:
    QTest.keySequence(widget, QKeySequence(key))


def shown(qtbot: QtBot, widget: QWidget) -> None:
    with qtbot.waitExposed(widget):
        widget.show()
    widget.activateWindow()
    qtbot.waitUntil(widget.isActiveWindow)


def focused(qtbot: QtBot, widget: QWidget) -> None:
    widget.setFocus()
    qtbot.waitUntil(widget.hasFocus)


def local_window(qtbot: QtBot, folder: Path) -> MainWindow:
    made = MainWindow()
    qtbot.addWidget(made)
    shown(qtbot, made)
    made.local_side.go_to(str(folder))
    focused(qtbot, made.local_side.file_list)
    return made


def files_in(tmp_path: Path, *names: str) -> Path:
    for name in names:
        (tmp_path / name).write_text("x" * 10, encoding="utf-8")
    return tmp_path


def with_settings(made: MainWindow, **changes: object) -> None:
    made.settings = replace(made.settings, **changes)  # pyrefly: ignore[bad-argument-type]
    made._apply_settings()


def noted_in(opened: list[QMenu]) -> Callable[[QMenu, QPoint], None]:
    def noted(menu: QMenu, _place: QPoint) -> None:
        opened.append(menu)

    return noted


def row_point(view: FileList, name: str) -> QPoint:
    return view.visualRect(view.index_of(name)).center()


# --- type-ahead (FG-01) ----------------------------------------------------------


def test_typing_jumps_to_the_first_matching_name_and_tab_goes_to_the_next(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    shown(qtbot, view)
    focused(qtbot, view)
    QTest.keyClicks(view, "b")
    assert current(view) == "beta.txt" and chosen(view) == ["beta.txt"] and view.typed == "b"  # files come after the folders
    QTest.keyClick(view, Qt.Key.Key_Tab)
    assert current(view) == "bravo.log" and view.hasFocus()  # Tab stays in the list while typing
    QTest.keyClick(view, Qt.Key.Key_Backtab, Qt.KeyboardModifier.ShiftModifier)
    assert current(view) == "beta.txt"
    QTest.keyClicks(view, "e")
    assert current(view) == "beta.txt" and view.typed == "be"
    QTest.keyClick(view, Qt.Key.Key_Backspace)
    assert view.typed == "b"
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.typed == ""
    QTest.keyClicks(view, "a")
    assert current(view) == "alpha.txt"


def test_a_pause_starts_the_typed_name_afresh_and_a_name_that_fits_nothing_keeps_the_cursor(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    shown(qtbot, view)
    focused(qtbot, view)
    told: list[str] = []
    view.typing_changed.connect(told.append)
    QTest.keyClicks(view, "z")
    assert current(view) == "zoo"
    QTest.keyClicks(view, "x")  # no name starts with zx
    assert current(view) == "zoo" and view.typed == "z"
    view.type_pause.timeout.emit()  # the pause, without waiting for it
    assert view.typed == "" and told == ["z", ""]
    QTest.keyClicks(view, "d")
    assert current(view) == "Docs"


def test_typing_starts_afresh_when_the_list_is_filled_again_or_something_opens(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    shown(qtbot, view)
    focused(qtbot, view)
    opened: list[object] = []
    view.opened.connect(opened.append)
    QTest.keyClicks(view, "do")
    assert view.typed == "do"
    view.show_entries([RemoteEntry("docs", True), RemoteEntry("readme", False)], True, False)  # another folder
    assert view.typed == ""
    QTest.keyClicks(view, "r")
    assert current(view) == "readme" and view.typed == "r"
    view.show_entries([RemoteEntry("docs", True), RemoteEntry("rest", False)], True, True)  # the same folder, read again
    assert view.typed == ""
    QTest.keyClicks(view, "d")
    QTest.keyClick(view, Qt.Key.Key_Return)  # opens docs
    assert view.typed == "" and len(opened) == 1
    QTest.keyClicks(view, "r")
    view.set_show_hidden(False)
    assert view.typed == ""
    QTest.keyClicks(view, "r")
    view.set_name_mask("*.txt")
    assert view.typed == ""
    QTest.keyClick(view, Qt.Key.Key_Backspace)  # nothing typed: up, never an edit of old text
    assert opened[-1] is None


class SingleClick(QProxyStyle):
    """A desktop set to open with one click."""

    @override
    def styleHint(self, hint: QStyle.StyleHint, option: QStyleOption | None = None, widget: QWidget | None = None, returnData: QStyleHintReturn | None = None) -> int:
        if hint == QStyle.StyleHint.SH_ItemView_ActivateItemOnSingleClick:
            return 1
        return super().styleHint(hint, option, widget, returnData)


@pytest.mark.parametrize("single_click", [True, False])
def test_a_right_click_never_opens_and_a_left_click_opens_where_the_desktop_says_so(qtbot: QtBot, single_click: bool) -> None:
    view = list_for(qtbot)
    style = SingleClick()
    if single_click:
        view.setStyle(style)
    shown(qtbot, view)
    opened: list[object] = []
    view.opened.connect(opened.append)
    view.customContextMenuRequested.connect(lambda _place=QPoint(): None)
    place = row_point(view, "zoo")
    QTest.mouseClick(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, place)
    assert opened == []
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, place)
    assert len(opened) == (1 if single_click else 0)


def test_the_status_line_says_what_was_typed(qtbot: QtBot, tmp_path: Path) -> None:
    files_in(tmp_path, "one.txt", "other.txt", "two.txt")
    side = LocalSide()
    qtbot.addWidget(side)
    shown(qtbot, side)
    side.go_to(str(tmp_path))
    focused(qtbot, side.file_list)
    QTest.keyClicks(side.file_list, "o")
    assert side.count_label.text().startswith("Find: o") and "Tab" in side.count_label.text()
    QTest.keyClick(side.file_list, Qt.Key.Key_Escape)
    assert side.count_label.text().startswith("10 bytes of 30 bytes")


def test_typing_in_the_path_field_never_moves_the_list(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "apple", "banana"))
    view = made.local_side.file_list
    QTest.keyClicks(view, "b")
    assert current(view) == "banana"
    press(view, "Ctrl+L")
    line = made.local_side.path_line
    qtbot.waitUntil(line.hasFocus)
    QTest.keyClicks(line, "a")
    press(line, "Ctrl+A")  # the field's own select all, never the list's
    assert current(view) == "banana" and chosen(view) == ["banana"] and view.typed == ""


# --- selection keys (FG-02), on the list with the focus (OR-21) -------------------------


def test_the_selection_keys_act_on_the_list_with_the_focus_only(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "a.txt", "b.txt", "c.log"))
    view = made.local_side.file_list
    press(view, "Ctrl+A")
    assert chosen(view) == ["a.txt", "b.txt", "c.log"]  # never the parent entry
    assert chosen(made.remote_side.file_list) == []
    press(view, "Ctrl+Shift+A")
    assert chosen(view) == []
    view.setCurrentIndex(view.index_of("a.txt"))
    view.clear_marks()
    press(view, "Insert")
    assert chosen(view) == ["a.txt"] and current(view) == "b.txt"
    press(view, "Ctrl+I")
    assert chosen(view) == ["b.txt", "c.log"]
    press(view, "Ctrl+Shift+A")
    made.list_actions["local_restore"].trigger()  # the selection Ctrl+Shift+A cleared
    assert chosen(view) == ["b.txt", "c.log"]
    selection = view.selectionModel()
    assert selection is not None
    selection.setCurrentIndex(view.index_of("a.txt"), QItemSelectionModel.SelectionFlag.NoUpdate)
    made.list_actions["local_same_extension"].trigger()  # adds the files like a.txt
    assert chosen(view) == ["a.txt", "b.txt", "c.log"]


def test_select_all_invert_and_a_key_stay_quick_on_ten_thousand_files(qtbot: QtBot) -> None:
    view = FileList(7, FROM_REMOTE, frozenset({FROM_LOCAL}))
    qtbot.addWidget(view)
    view.resize(600, 400)
    shown(qtbot, view)
    view.show_entries([RemoteEntry(f"file{number:05}", False, 1) for number in range(10_000)], True, False)
    focused(qtbot, view)

    def timed(work: Callable[[], None]) -> float:
        start = time.monotonic()
        work()
        return time.monotonic() - start

    assert timed(view.select_all_entries) < 0.5
    assert len(view.selected_entries()) == 10_000 and view.names()[0] == ".."
    selection = view.selectionModel()
    assert selection is not None
    selection.select(view.rows_selection([1, 2, 5000]), QItemSelectionModel.SelectionFlag.Deselect | QItemSelectionModel.SelectionFlag.Rows)
    assert timed(view.invert_selection) < 0.5
    assert chosen(view) == ["file00000", "file00001", "file04999"]
    view.select_all_entries()
    view.setCurrentIndex(view.index_of("file00000"))
    view.select_all_entries()
    assert timed(lambda: QTest.keyClick(view, Qt.Key.Key_Down)) < 0.5
    assert chosen(view) == ["file00001"]
    view.select_all_entries()
    assert timed(lambda: view.show_entries(list(view.all_entries), True, True)) < 0.5  # a refresh keeps the large selection quickly
    assert len(view.selected_entries()) == 10_000


def test_restore_brings_back_a_selection_a_click_replaced_and_not_one_that_only_grew(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    selection = view.selectionModel()
    assert selection is not None
    flags = QItemSelectionModel.SelectionFlag
    selection.select(view.index_of("alpha.txt"), flags.ClearAndSelect | flags.Rows)
    selection.select(view.index_of("beta.txt"), flags.Select | flags.Rows)  # grew
    selection.select(view.index_of("zoo"), flags.ClearAndSelect | flags.Rows)  # replaced
    assert view.can_restore()
    view.restore_selection()
    assert chosen(view) == ["alpha.txt", "beta.txt"]
    view.show_entries(list(ENTRIES), True, False)  # another folder: nothing of here to bring back
    view.folder = "/elsewhere"
    assert not view.can_restore()


def test_the_selection_items_are_in_the_menu_of_each_list_and_greyed_with_a_reason(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "a.txt"))
    menu = made.menu_for(())
    made._add_list_items(menu, LOCAL)
    submenus = {action.text(): action.menu() for action in menu.actions() if action.menu() is not None}
    select = submenus["Select"]
    assert isinstance(select, QMenu)
    texts = [action.text() for action in select.actions()]
    assert texts == ["Select All", "Invert Selection", "Clear Selection", "Select the Same Extension", "Restore the Previous Selection", "Mark and Move Down", "Select by Mask…", "Unselect by Mask…"]
    clear = next(action for action in select.actions() if action.text() == "Clear Selection")
    assert not clear.isEnabled() and clear.toolTip()  # nothing selected: greyed, and why (OR-20)
    assert set(submenus) == {"This File Only", "Select", "Copy to Clipboard"}


# --- the two-pane selection mode (FG-03) -------------------------------------------------


def test_in_the_two_pane_mode_a_click_and_an_arrow_move_without_selecting_and_space_marks(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "a.txt", "b.txt", "c.txt"))
    with_settings(made, two_pane_selection=True)
    view = made.local_side.file_list
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, row_point(view, "a.txt"))
    assert current(view) == "a.txt" and chosen(view) == []
    assert made.local_side.selected_paths() == [str(tmp_path / "a.txt")]  # nothing marked: an action works on the focused file
    assert made.actions_by_name["local_rename"].isEnabled()
    QTest.keyClick(view, Qt.Key.Key_Down)
    assert current(view) == "b.txt" and chosen(view) == []
    QTest.keyClick(view, Qt.Key.Key_Space)
    QTest.keyClick(view, Qt.Key.Key_Down)
    press(view, "Insert")
    assert chosen(view) == ["b.txt", "c.txt"]
    assert made.local_side.selected_paths() == [str(tmp_path / "b.txt"), str(tmp_path / "c.txt")]  # marked: those, not the focused
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, row_point(view, "a.txt"))
    assert chosen(view) == ["a.txt", "b.txt", "c.txt"]


def test_in_the_two_pane_mode_a_drag_with_nothing_marked_carries_the_focused_file(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "a.txt", "b.txt"))
    with_settings(made, two_pane_selection=True)
    view = made.local_side.file_list
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, row_point(view, "b.txt"))
    assert [url.toLocalFile() for url in view.drag_data().urls()] == [str(tmp_path / "b.txt")]
    with_settings(made, two_pane_selection=False)
    assert chosen(view) == [] and view.drag_data().urls() == []  # back to the plain mode: only what is selected


def test_the_settings_window_keeps_the_choices_of_block_fg(qtbot: QtBot) -> None:
    from chrono_ftp.ui.settingsdialog import SettingsDialog

    chosen_settings = saved_settings.Settings(two_pane_selection=True, key_set=FILE_MANAGER_KEYS, size_format=BYTES, natural_sort=False, title_path=TITLE_FULL, drag_move=False)
    dialog = SettingsDialog(chosen_settings)
    qtbot.addWidget(dialog)
    assert dialog.chosen() == chosen_settings
    dialog.two_pane.setChecked(False)
    assert dialog.chosen().two_pane_selection is False


def test_without_the_two_pane_mode_a_click_selects_as_before(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "a.txt", "b.txt"))
    view = made.local_side.file_list
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, row_point(view, "a.txt"))
    assert chosen(view) == ["a.txt"]
    QTest.keyClick(view, Qt.Key.Key_Down)
    assert chosen(view) == ["b.txt"]
    view.clear_marks()
    assert made.local_side.selected_paths() == []  # the focused file is no selection here


# --- This File Only (FG-05) ------------------------------------------------------------------


def test_this_file_only_acts_on_the_file_under_the_cursor_and_ignores_the_selection(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = local_window(qtbot, files_in(tmp_path, "a.txt", "b.txt", "c.txt"))
    view = made.local_side.file_list
    view.select_all_entries()
    selection = view.selectionModel()
    assert selection is not None
    selection.setCurrentIndex(view.index_of("b.txt"), QItemSelectionModel.SelectionFlag.NoUpdate)
    asked: list[str] = []

    def yes(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", yes)
    trashed: list[str] = []

    def to_trash(path: str) -> bool:
        trashed.append(path)
        return True

    monkeypatch.setattr("chrono_ftp.ui.windowlocal.QFile.moveToTrash", to_trash)
    menu = made.menu_for(())
    made._add_list_items(menu, LOCAL)
    only = next(action.menu() for action in menu.actions() if action.text() == "This File Only")
    assert isinstance(only, QMenu)
    assert [action.text() for action in only.actions()] == ["Upload This File Only", "Edit This File Only", "Delete This File Only", "Properties of This File Only"]
    upload = only.actions()[0]
    assert not upload.isEnabled() and upload.toolTip()  # no live connection: greyed, and why
    only.actions()[2].trigger()
    assert trashed == [str(tmp_path / "b.txt")] and len(asked) == 1
    assert chosen(view) == ["a.txt", "b.txt", "c.txt"] and not view.only_current
    opened: list[str] = []
    monkeypatch.setattr(made, "_open_local_file", opened.append)
    assert not made.actions_by_name["local_edit"].isEnabled()  # three selected: Edit is greyed
    only.actions()[1].trigger()
    assert opened == [str(tmp_path / "b.txt")]  # This File Only edits the one under the cursor all the same
    assert not made.actions_by_name["local_edit"].isEnabled()


# --- the key sets and sort keys (FG-04) -------------------------------------------------------


def test_the_sort_keys_sort_by_their_column_and_the_same_key_again_turns_the_order(qtbot: QtBot, tmp_path: Path) -> None:
    for name, size in (("small.b", 1), ("large.a", 300), ("middle.c", 20)):
        (tmp_path / name).write_text("x" * size, encoding="utf-8")
    made = local_window(qtbot, tmp_path)
    view = made.local_side.file_list
    press(view, "Ctrl+F6")
    assert view.names()[1:] == ["small.b", "middle.c", "large.a"]
    press(view, "Ctrl+F6")
    assert view.names()[1:] == ["large.a", "middle.c", "small.b"]
    press(view, "Ctrl+F4")
    assert view.names()[1:] == ["large.a", "small.b", "middle.c"]
    press(view, "Ctrl+F3")
    assert view.names()[1:] == ["large.a", "middle.c", "small.b"]


def test_the_file_manager_key_set_finds_with_ctrl_f_and_leaves_ctrl_f4_free(qtbot: QtBot, tmp_path: Path) -> None:
    for name in ("b.a", "a.b"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    made = local_window(qtbot, tmp_path)
    assert made.actions_by_name["search"].shortcut().toString() == "Alt+F7"
    with_settings(made, key_set=FILE_MANAGER_KEYS)
    assert made.actions_by_name["search"].shortcut().toString() == "Ctrl+F"
    view = made.local_side.file_list
    press(view, "Ctrl+F4")
    assert view.names()[1:] == ["a.b", "b.a"]  # still by name


def test_shift_f6_moves_on_the_server_and_f6_moves_to_the_other_side(qtbot: QtBot) -> None:
    made = MainWindow()
    qtbot.addWidget(made)
    assert made.actions_by_name["move"].shortcut().toString() == "Shift+F6"
    assert made.list_actions["local_move_across"].shortcut().toString() == "F6"
    assert made.list_actions["remote_move_across"].shortcut().toString() == "F6"
    assert not made.list_actions["local_move_across"].isEnabled()  # no connection: greyed


# --- the size format (FG-06) and natural sort (FG-08) ---------------------------------------


def test_the_size_column_follows_the_size_format(qtbot: QtBot) -> None:
    made = MainWindow()
    qtbot.addWidget(made)
    view = made.remote_side.file_list
    view.show_entries([RemoteEntry("big", False, 1_234_567)], False, False)
    assert view.files.index(0, 1).data() == "1.2 MB"
    with_settings(made, size_format=BYTES)
    assert view.files.index(0, 1).data() == "1,234,567"


def test_natural_sort_puts_file2_before_file10_and_folders_first_by_name_whatever_the_column(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    assert view.names()[:3] == ["..", "Docs", "zoo"]
    assert view.names().index("file2") < view.names().index("file10")
    view.files.set_display(view.files.size_style, False)
    assert view.names().index("file10") < view.names().index("file2")
    view.sortByColumn(1, Qt.SortOrder.DescendingOrder)
    assert view.names()[:3] == ["..", "Docs", "zoo"]  # folders by name, also sorted by size backwards


# --- the column header menu (FG-07) ---------------------------------------------------------


def test_the_column_header_menu_sorts_hides_shows_and_restores(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    menu = view.header_menu(1)
    actions = {action.text(): action for action in menu.actions() if action.text()}
    assert list(actions)[:3] == ["Sort Ascending", "Sort Descending", "Hide This Column"]
    actions["Sort Descending"].trigger()
    assert view.files.sorted_by == (1, Qt.SortOrder.DescendingOrder)
    actions["Hide This Column"].trigger()
    assert view.isColumnHidden(1)
    name_menu = view.header_menu(0)
    name_entries = {action.text(): action for action in name_menu.actions() if action.text()}
    assert not name_entries["Hide This Column"].isEnabled() and name_entries["Hide This Column"].toolTip()
    assert not name_entries["Name"].isEnabled() and not name_entries["Size"].isChecked()
    name_entries["Owner"].trigger()  # unticked: hidden
    assert view.isColumnHidden(4)
    name_entries["Restore the Standard Columns"].trigger()
    assert not view.isColumnHidden(1) and not view.isColumnHidden(4) and view.files.sorted_by == (0, Qt.SortOrder.AscendingOrder)


def test_the_header_menu_opens_through_open_menu(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    view = list_for(qtbot)
    opened: list[QMenu] = []
    monkeypatch.setattr("chrono_ftp.ui.filelist.open_menu", noted_in(opened))
    view.header().customContextMenuRequested.emit(QPoint(5, 5))
    assert len(opened) == 1  # D-052


# --- hidden and filtered counts (FG-09) ---------------------------------------------------------


def test_the_status_line_counts_hidden_and_filtered_and_a_click_or_ctrl_alt_h_switches(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files_in(tmp_path, ".one", ".two", "a.txt", "b.log")
    made = local_window(qtbot, tmp_path)
    status = made.local_side.status
    assert status.hidden_button.isVisibleTo(made) and status.hidden_button.text() == "2 hidden, shown"
    status.hidden_button.click()
    assert not made.settings.show_hidden and status.hidden_button.text() == "2 hidden"
    assert saved_settings.load().show_hidden is False  # kept for next time
    assert ".one" not in made.local_side.file_list.names()
    press(made.local_side.file_list, "Ctrl+Alt+H")
    assert made.settings.show_hidden and ".one" in made.local_side.file_list.names()
    made.local_side.file_list.set_name_mask("*.txt")
    assert status.filtered_button.isVisibleTo(made) and status.filtered_button.text() == "3 filtered"  # .one, .two and b.log
    asked: list[bool] = []

    def ask_mask(_view: FileList) -> None:
        asked.append(True)

    monkeypatch.setattr(made, "_ask_mask", ask_mask)
    made.local_side.filter_wanted.disconnect()
    made.local_side.filter_wanted.connect(lambda: made._ask_mask(made.local_side.file_list))
    status.filtered_button.click()
    assert asked == [True]


# --- the history (FG-10) ------------------------------------------------------------------------


def test_back_has_a_drop_down_of_the_last_folders_and_alt_left_goes_back(qtbot: QtBot, tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    made = local_window(qtbot, tmp_path)
    side = made.local_side
    for name in ("a", "b", "c"):
        side.go_to(str(tmp_path / name))
    side.navigation.fill_walk_menu("back")
    menu = side.navigation.walk_menus["back"]
    assert [action.text() for action in menu.actions()][:3] == [str(tmp_path / "b"), str(tmp_path / "a"), str(tmp_path)]  # the nearest first
    menu.actions()[1].trigger()
    assert side.folder == str(tmp_path / "a")
    side.navigation.fill_walk_menu("forward")
    assert [action.text() for action in side.navigation.walk_menus["forward"].actions()] == [str(tmp_path / "b"), str(tmp_path / "c")]
    focused(qtbot, side.file_list)
    press(side.file_list, "Alt+Right")
    assert side.folder == str(tmp_path / "b")
    press(side.file_list, "Alt+Left")
    assert side.folder == str(tmp_path / "a")
    press(side.file_list, "Backspace")
    assert side.folder == str(tmp_path)  # Backspace still goes up


def test_the_history_goes_several_steps_at_once() -> None:
    history = FolderHistory()
    for folder in ("/1", "/2", "/3"):
        history.left(folder)
    assert history.back("/4", 2) == "/2"
    assert history.behind == ["/1"] and history.ahead == ["/4", "/3"]
    assert history.forward("/2", 5) == "/4"  # never further than there is


# --- the clipboard (FG-11) ------------------------------------------------------------------------


def test_the_names_paths_and_folder_go_to_the_clipboard(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "one.txt", "my file.txt"))
    view = made.local_side.file_list
    view.select_all_entries()
    press(view, "Ctrl+Shift+C")
    clipboard = QGuiApplication.clipboard()
    assert clipboard.text() == '"my file.txt"\none.txt'
    made.list_actions["local_copy_full"].trigger()
    assert clipboard.text() == f'"{tmp_path / "my file.txt"}"\n{tmp_path / "one.txt"}'
    made.list_actions["local_copy_path"].trigger()
    assert clipboard.text() == str(tmp_path)


# --- live: title, F6, right-button drag, refresh (FG-04, FG-12, FG-14, FG-15, FG-16) --------------


def server_with_files(tmp_path: Path) -> Path:
    root = tmp_path / "server"
    (root / "www" / "inner").mkdir(parents=True)
    (root / "www" / "page.html").write_text("x", encoding="utf-8")
    return root


def test_the_title_bar_shows_the_folder_of_the_server_short_full_or_not(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(server_with_files(tmp_path)) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.open_remote_folder("/www/inner")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/www/inner")
        assert window.windowTitle().endswith(" - inner") and window.windowTitle().startswith("Chrono FTP ")
        with_settings(window, title_path=TITLE_FULL)
        assert window.windowTitle().endswith(" - /www/inner")
        with_settings(window, title_path=TITLE_NONE)
        assert " - " not in window.windowTitle()


def test_f6_offers_a_move_and_a_right_button_drop_asks_copy_or_move(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "here").mkdir()
    local = files_in(tmp_path / "here", "a.txt")
    offered: list[tuple[list[str], str, bool, bool]] = []

    def offer(_session: object, sources: list[str], target: str, upload: bool, after_a_drop: bool = False, dropped: bool = False, move: bool = False) -> None:
        offered.append((sources, target, upload, move))

    monkeypatch.setattr(window, "_offer", offer)
    with ssh_server(server_with_files(tmp_path)) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.open_remote_folder("/www")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/www")
        window.local_side.go_to(str(local))
        window.local_side.file_list.select_all_entries()
        window.update_actions()
        window.list_actions["local_move_across"].trigger()
        assert offered == [([str(local / "a.txt")], "/www", True, True)]
        picks: list[str] = []

        def pick(menu: QMenu, _place: QPoint) -> None:
            for action in menu.actions():
                picks.append(f"{action.text()}:{action.isEnabled()}")
            next(action for action in menu.actions() if action.text() == MOVE_HERE_TEXT).trigger()

        monkeypatch.setattr(windowselection, "open_menu", pick)
        mime = window.local_side.file_list.drag_data(right=True)
        below = QPointF(5, window.remote_side.file_list.viewport().height() - 2)  # below the rows: the folder shown
        drop = QDropEvent(below, Qt.DropAction.CopyAction, mime, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier)
        window.remote_side.file_list.dropEvent(drop)
        qtbot.waitUntil(lambda: len(offered) == 2)
        assert offered[1] == ([str(local / "a.txt")], "/www", True, True) and picks[:2] == ["Copy Here:True", "Move Here:True"]
        with_settings(window, drag_move=False)
        picks.clear()

        def look(menu: QMenu, _place: QPoint) -> None:
            picks.extend(f"{action.text()}:{action.isEnabled()}:{action.toolTip()}" for action in menu.actions())

        monkeypatch.setattr(windowselection, "open_menu", look)
        again = window.local_side.file_list.drag_data(right=True)  # kept: the event does not own it
        window.remote_side.file_list.dropEvent(QDropEvent(below, Qt.DropAction.CopyAction, again, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier))
        qtbot.waitUntil(lambda: len(picks) >= 2)
        assert picks[1] == f"Move Here:False:{DRAG_MOVE_OFF_TEXT}" and len(offered) == 2  # nothing chosen: nothing offered


def test_a_left_button_drop_never_asks_with_a_menu(qtbot: QtBot) -> None:
    view = FileList(7, FROM_REMOTE, frozenset({FROM_LOCAL}))
    qtbot.addWidget(view)
    plain: list[str] = []
    right: list[str] = []

    def noted(found: list[str]) -> Callable[[str, object, object], None]:
        def note(source: str, _paths: object, _folder: object) -> None:
            found.append(source)

        return note

    view.dropped.connect(noted(plain))
    view.right_dropped.connect(noted(right))
    sender = FileList(4, FROM_LOCAL, frozenset({FROM_REMOTE}))
    qtbot.addWidget(sender)
    for is_right in (False, True):
        mime: QMimeData = sender.drag_data(right=is_right)
        view.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert plain == [FROM_LOCAL] and right == [FROM_LOCAL]


def test_a_right_click_menu_waits_until_the_button_comes_up(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    shown(qtbot, view)
    asked: list[QPoint] = []
    view.customContextMenuRequested.connect(asked.append)
    place = row_point(view, "zoo")
    QTest.mousePress(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, place)
    assert QGuiApplication.mouseButtons() & Qt.MouseButton.RightButton
    QApplication.sendEvent(view.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Mouse, place, view.viewport().mapToGlobal(place)))  # as the desktop sends it at the press
    assert asked == []  # the press may still become a drag
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, place)
    assert asked == [place]
    QApplication.sendEvent(view.viewport(), QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, place, view.viewport().mapToGlobal(place)))  # the menu key: at once
    assert asked == [place, place]


def test_the_refresh_of_a_tab_can_be_paused_and_its_listings_cleared(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(server_with_files(tmp_path)) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        with_settings(window, refresh_seconds=5)
        menu = window.tab_menu_for(window.tab_bar.tabRect(0).center())
        assert menu is not None
        entries = {action.text(): action for action in menu.actions() if action.text()}
        entries["Pause Automatic Refresh"].trigger()
        assert session.refresh_paused
        window.refresh_by_itself()
        assert not session.auto_refreshing  # paused: nothing asked of the server
        assert window.remote_side.tree_model.rowCount() == 1
        entries["Clear Cached Listings"].trigger()
        assert window.remote_side.tree_model.rowCount() == 0
        qtbot.waitUntil(lambda: window.remote_side.tree_model.rowCount() == 1)  # the folder shown is read again
        with_settings(window, refresh_seconds=0)
        again = window.tab_menu_for(window.tab_bar.tabRect(0).center())
        assert again is not None
        pause = next(action for action in again.actions() if action.text() == "Pause Automatic Refresh")
        assert not pause.isEnabled() and pause.toolTip()


def test_ctrl_r_refreshes_the_side_with_the_focus(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(server_with_files(tmp_path)) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        shown(qtbot, window)
        focused(qtbot, window.remote_side.file_list)
        assert "www" in window.remote_side.file_list.names()
        (tmp_path / "server" / "new").mkdir()
        press(window.remote_side.file_list, "Ctrl+R")
        qtbot.waitUntil(lambda: "new" in window.remote_side.file_list.names())
        local = tmp_path / "local"
        local.mkdir()
        window.local_side.go_to(str(local))
        focused(qtbot, window.local_side.file_list)
        (local / "made").write_text("x", encoding="utf-8")
        press(window.local_side.file_list, "Ctrl+R")
        assert "made" in window.local_side.file_list.names()


# --- the drop-down of the path field (FG-16) ------------------------------------------------


def test_the_path_field_drop_down_lists_the_folders_above_and_opens_one(qtbot: QtBot, tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    side = LocalSide()
    qtbot.addWidget(side)
    side.go_to(str(deep))
    menu = side.path_bar.parents_menu()
    texts = [action.text() for action in menu.actions()]
    assert texts[:3] == ["b", "a", tmp_path.name] and texts[-1] == "/"
    side.path_bar.start_typing()
    menu.actions()[1].trigger()
    assert side.folder == str(tmp_path / "a") and not side.path_bar.typing


def test_alt_down_in_the_path_field_opens_the_drop_down(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    side = LocalSide()
    qtbot.addWidget(side)
    shown(qtbot, side)
    side.go_to(str(tmp_path))
    opened: list[QMenu] = []
    monkeypatch.setattr("chrono_ftp.ui.pathbar.open_menu", noted_in(opened))
    side.path_bar.start_typing()
    QTest.keyClick(side.path_line, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    assert len(opened) == 1


def test_every_key_of_block_fg_is_a_key_of_the_program() -> None:
    from chrono_ftp.ui.windownavigation import PROGRAM_KEYS

    keys = {"Ctrl+I", "Ctrl+Shift+A", "Insert", "F6", "Shift+F6", "Alt+F7", "Ctrl+F", "Ctrl+F3", "Ctrl+F4", "Ctrl+F5", "Ctrl+F6", "Ctrl+F7", "Ctrl+F8", "Ctrl+F9", "Alt+Left", "Alt+Right", "Ctrl+Shift+C", "Ctrl+Alt+H", "Ctrl+R"}
    assert keys <= set(PROGRAM_KEYS)  # no bookmark can take them


def test_no_two_actions_of_a_list_share_a_key(qtbot: QtBot) -> None:
    made = MainWindow()
    qtbot.addWidget(made)
    for owner in (made.local_side.file_list, made.remote_side.file_list, made.local_side, made.remote_side):
        keys = [action.shortcut().toString() for action in owner.actions() if not action.shortcut().isEmpty()]
        assert len(keys) == len(set(keys)), keys
