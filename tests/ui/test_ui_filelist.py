from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QFile, QItemSelectionModel, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QToolBar
from pytestqt.qtbot import QtBot

from chrono_ftp.config import settings
from chrono_ftp.connections.base import CommandResult, RemoteEntry
from chrono_ftp.core.connection import Connection
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.filelist import DRAG_MARK, FROM_LOCAL, FROM_OUTSIDE, FROM_REMOTE, LOCAL_FILES, REMOTE_FILES, FileList, rights_text, size_text, summary_text
from chrono_ftp.ui.queueview import HEIGHT as QUEUE_HEIGHT
from chrono_ftp.ui.mainwindow import LOCAL_MENU, MainWindow, REMOTE_MENU
from chrono_ftp.ui.sidebar import FolderHistory
from tests.test_engine import Scripted

# --- Version ------------------------------------------------------------
__version__ = "test_ui_filelist.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: The queue, the pin and the rows of buttons
# v0.0.6: Initial

ENTRIES: list[RemoteEntry] = [RemoteEntry("b.txt", False, 10, 2000.0), RemoteEntry("A.txt", False, 300, 1000.0), RemoteEntry("zebra", True), RemoteEntry("alpha", True), RemoteEntry(".hidden", False, 1)]


def list_for(qtbot: QtBot, kind: str = FROM_REMOTE) -> FileList:
    view = FileList(7, kind, frozenset({FROM_LOCAL, FROM_OUTSIDE, FROM_REMOTE}))
    qtbot.addWidget(view)
    view.resize(600, 400)
    view.show_entries(list(ENTRIES), True, False)
    return view


def pick(view: FileList, *names: str) -> None:
    selection = view.selectionModel()
    assert selection is not None
    for name in names:
        selection.select(view.index_of(name), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def test_the_parent_entry_stays_on_top_and_folders_before_files_whichever_way_it_is_sorted(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    assert view.names() == ["..", "alpha", "zebra", ".hidden", "A.txt", "b.txt"]
    view.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    assert view.names() == ["..", "zebra", "alpha", "b.txt", "A.txt", ".hidden"]


def test_a_column_sorts_by_its_value_not_by_its_text(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    view.sortByColumn(1, Qt.SortOrder.AscendingOrder)
    assert view.names()[3:] == [".hidden", "b.txt", "A.txt"]


def test_sorting_keeps_the_selection(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    pick(view, "b.txt")
    view.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    assert [entry.name for entry in view.selected_entries()] == ["b.txt"]


def test_a_refresh_keeps_the_selection_and_another_folder_does_not(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    pick(view, "b.txt")
    view.show_entries(list(ENTRIES), True, True)
    assert [entry.name for entry in view.selected_entries()] == ["b.txt"]
    view.show_entries(list(ENTRIES), True, False)
    assert view.selected_entries() == []


def test_a_name_that_is_not_utf8_is_shown_readable_and_keeps_its_real_name(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    view.show_entries([RemoteEntry("b\udcffd.txt", False, 1)], False, False)
    assert view.names() == ["b�d.txt"]
    pick(view, "b\udcffd.txt")
    assert view.selected_entries()[0].name == "b\udcffd.txt"


def test_hidden_files_and_the_filter_only_change_what_is_shown(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    view.set_show_hidden(False)
    assert ".hidden" not in view.names()
    view.set_show_hidden(True)
    view.set_name_mask("a*")
    assert view.names() == ["..", "alpha", "zebra", "A.txt"]  # a folder stays, or nothing could be reached
    view.set_name_mask("")
    assert "b.txt" in view.names()


def test_a_link_shows_where_it_leads(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    view.show_entries([RemoteEntry("link", False, is_link=True, link_target="/srv/a.txt")], False, False)
    assert view.index_of("link").siblingAtColumn(6).data() == "/srv/a.txt"


def test_rights_read_as_letters() -> None:
    assert rights_text("0755") == "rwxr-xr-x"
    assert rights_text("0640") == "rw-r-----"
    assert rights_text("") == ""


def test_the_count_line_says_what_is_selected_of_what_is_here() -> None:
    assert summary_text(ENTRIES[:1], ENTRIES) == f"{size_text(10)} of {size_text(311)} in 1 of 5"


def test_backspace_goes_up_and_only_where_there_is_an_up(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    with qtbot.waitSignal(view.opened) as opened:
        QTest.keyClick(view, Qt.Key.Key_Backspace)
    assert opened.args == [None]
    view.show_entries(list(ENTRIES), False, False)
    seen: list[object] = []
    view.opened.connect(seen.append)
    QTest.keyClick(view, Qt.Key.Key_Backspace)
    assert seen == []


def drop_on(view: FileList, mime: QMimeData, row_name: str | None) -> list[tuple[str, object, str]]:
    seen: list[tuple[str, object, str]] = []

    def noted(source: str, paths: object, folder: str) -> None:
        seen.append((source, paths, folder))

    view.dropped.connect(noted)
    position = QPointF(view.visualRect(view.index_of(row_name)).center()) if row_name else QPointF(5, view.viewport().height() - 5)
    view.dropEvent(QDropEvent(position, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    return seen


def marked(kind: str) -> QMimeData:
    mime = QMimeData()
    mime.setData(kind, QByteArray(DRAG_MARK))
    return mime


def test_a_drop_on_a_folder_with_a_name_that_is_not_utf8_keeps_the_real_name(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    view.show_entries([RemoteEntry("caf\udce9", True)], False, False)
    assert drop_on(view, marked(LOCAL_FILES), "caf\udce9")[0][2] == "caf\udce9"


def test_a_drag_from_another_running_chrono_ftp_is_not_taken_for_our_own_selection(qtbot: QtBot) -> None:
    mime = QMimeData()
    mime.setData(REMOTE_FILES, QByteArray(b"0"))
    assert drop_on(list_for(qtbot), mime, "alpha") == []


def test_a_drop_says_where_it_came_from_and_the_folder_it_fell_on(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    assert drop_on(view, marked(LOCAL_FILES), "alpha") == [(FROM_LOCAL, [], "alpha")]


def test_a_drop_on_a_file_or_empty_space_goes_to_the_folder_shown(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    assert drop_on(view, marked(LOCAL_FILES), "b.txt")[0][2] == ""
    assert drop_on(list_for(qtbot), marked(LOCAL_FILES), None)[0][2] == ""


def test_a_drop_from_the_file_manager_brings_its_paths(qtbot: QtBot) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile("/home/a/report.txt")])
    assert drop_on(list_for(qtbot), mime, None) == [(FROM_OUTSIDE, ["/home/a/report.txt"], "")]


def test_within_one_list_a_drop_needs_another_folder(qtbot: QtBot) -> None:
    view = list_for(qtbot)
    pick(view, "alpha")
    assert drop_on(view, marked(REMOTE_FILES), None) == []
    assert drop_on(list_for(qtbot), marked(REMOTE_FILES), "b.txt") == []
    second = list_for(qtbot)
    pick(second, "alpha")
    assert drop_on(second, marked(REMOTE_FILES), "alpha") == []
    third = list_for(qtbot)
    pick(third, "b.txt")
    assert drop_on(third, marked(REMOTE_FILES), "zebra") == [(FROM_REMOTE, [], "zebra")]


def test_a_drag_from_the_left_also_gives_a_file_manager_its_paths(qtbot: QtBot) -> None:
    view = list_for(qtbot, FROM_LOCAL)
    view.folder = "/home/a"
    pick(view, "b.txt")
    assert [url.toLocalFile() for url in view.drag_data().urls()] == ["/home/a/b.txt"]


def test_the_history_goes_back_and_forward_and_a_new_folder_forgets_the_way_forward() -> None:
    history = FolderHistory()
    history.left("/a")
    assert history.back("/b") == "/a"
    assert history.forward("/a") == "/b"
    history.back("/b")
    history.left("/a")
    assert history.ahead == []


def window_for(qtbot: QtBot) -> MainWindow:
    window = MainWindow()
    qtbot.addWidget(window)
    return window


def test_the_left_side_walks_with_its_buttons(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "inner").mkdir()
    window = window_for(qtbot)
    side = window.local_side
    side.go_to(str(tmp_path))
    side.go_to(str(tmp_path / "inner"))
    side.navigation.buttons["back"].trigger()
    assert side.folder == str(tmp_path)
    side.navigation.buttons["forward"].trigger()
    assert side.folder == str(tmp_path / "inner")
    side.navigation.buttons["parent"].trigger()
    assert side.folder == str(tmp_path)
    side.navigation.buttons["root"].trigger()
    assert side.folder == "/"
    assert not side.navigation.buttons["parent"].isEnabled()


def test_a_typed_path_is_followed_and_a_wrong_one_puts_the_line_back(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "inner").mkdir()
    window = window_for(qtbot)
    side = window.local_side
    side.path_line.setText(str(tmp_path / "inner"))
    side.path_line.returnPressed.emit()
    assert side.folder == str(tmp_path / "inner")
    side.path_line.setText(str(tmp_path / "gone"))
    side.path_line.returnPressed.emit()
    assert side.path_line.text() == str(tmp_path / "inner")


def test_delete_on_this_computer_moves_every_chosen_file_to_the_trash(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text("x", encoding="utf-8")
    window = window_for(qtbot)
    side = window.local_side
    side.go_to(str(tmp_path))
    pick(side.file_list, "a.txt", "b.txt")

    def yes(*_args: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    trashed: list[str] = []

    def to_trash(path: str) -> bool:
        trashed.append(path)
        Path(path).unlink()
        return True

    monkeypatch.setattr(QMessageBox, "question", yes)
    monkeypatch.setattr(QFile, "moveToTrash", to_trash)
    window.actions_by_name["local_delete"].trigger()
    assert sorted(Path(path).name for path in trashed) == ["a.txt", "b.txt"]
    assert side.file_list.names()[-1] == "c.txt"


def test_a_file_that_can_not_go_to_the_trash_is_told_and_stays(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    window = window_for(qtbot)
    side = window.local_side
    side.go_to(str(tmp_path))
    pick(side.file_list, "a.txt")
    told: list[str] = []

    def yes(*_args: object) -> QMessageBox.StandardButton:
        return QMessageBox.StandardButton.Yes

    def note(_parent: object, _title: str, text: str) -> None:
        told.append(text)

    def refused(_path: str) -> bool:
        return False

    monkeypatch.setattr(QMessageBox, "question", yes)
    monkeypatch.setattr(QMessageBox, "information", note)
    monkeypatch.setattr(QFile, "moveToTrash", refused)
    window.actions_by_name["local_delete"].trigger()
    assert "a.txt" in told[0]
    assert (tmp_path / "a.txt").exists()


def test_a_local_name_that_is_not_utf8_is_never_opened_as_another_file(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_for(qtbot)
    told: list[str] = []
    opened: list[object] = []

    def note(_parent: object, _title: str, text: str) -> None:
        told.append(text)

    def open_url(url: object) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(QMessageBox, "information", note)
    monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
    window._open_local_file("/tmp/caf\udce9")
    assert told and not opened


def test_back_skips_a_folder_that_was_removed(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "gone").mkdir()
    (tmp_path / "here").mkdir()
    window = window_for(qtbot)
    side = window.local_side
    side.go_to(str(tmp_path))
    side.go_to(str(tmp_path / "gone"))
    side.go_to(str(tmp_path / "here"))
    (tmp_path / "gone").rmdir()
    side.navigation.buttons["back"].trigger()
    assert side.folder == str(tmp_path)


def test_each_side_has_its_own_menu_with_only_its_own_actions(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    assert [action.text() for action in window.menu_for(LOCAL_MENU).actions()] == ["Upload", "Edit", "Rename", "Delete", "New"]
    assert [action.text() for action in window.menu_for(REMOTE_MENU).actions()] == ["Download", "Edit", "Rename", "Move", "Copy", "Delete", "Properties", "New"]


def test_a_file_on_this_computer_can_be_renamed_and_made_with_the_buttons(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "old.txt").write_text("x", encoding="utf-8")
    window = window_for(qtbot)
    side = window.local_side
    side.go_to(str(tmp_path))
    pick(side.file_list, "old.txt")
    answers = iter(["new.txt", "made"])

    def answer(*_args: object) -> str:
        return next(answers)

    monkeypatch.setattr(window, "_ask_text", answer)
    window.actions_by_name["local_rename"].trigger()
    assert (tmp_path / "new.txt").exists()
    window.actions_by_name["local_new"].trigger()
    assert (tmp_path / "made").is_dir()
    assert "made" in side.file_list.names()


def test_sizes_columns_and_the_sort_order_come_back_after_a_restart(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    window.resize(window.width(), 640)
    qtbot.waitUntil(lambda: window.height() == 640)
    header = window.remote_side.file_list.header()
    header.resizeSection(1, 200)
    window.remote_side.file_list.sortByColumn(2, Qt.SortOrder.DescendingOrder)
    window.close()
    again = window_for(qtbot)
    assert again.height() == 640
    assert again.remote_side.file_list.header().sectionSize(1) == 200
    assert again.remote_side.file_list.files.sorted_by == (2, Qt.SortOrder.DescendingOrder)


def test_a_damaged_window_file_changes_nothing(qtbot: QtBot) -> None:
    settings.save_window({"window": "not base64 !!", "sides": "AAAA", "other": "x"})
    window_for(qtbot)


def test_the_queue_button_hides_and_shows_the_queue(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    assert not window.queue_view.isVisible()
    window.actions_by_name["queue"].trigger()
    assert window.queue_view.isVisible()
    window.actions_by_name["queue"].trigger()
    assert not window.queue_view.isVisible()


def finished_job(window: MainWindow, state: State) -> Job:
    job = Job(Scripted(CommandResult(0, "", "")), Connection(name="T", host="h"), "/a", "/", True, "a", state=state)
    window.queue.jobs.append(job)
    return job


def shown_window(qtbot: QtBot) -> MainWindow:
    window = window_for(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    return window


def test_the_queue_comes_with_a_transfer_and_goes_a_moment_after_the_last_one(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    job = finished_job(window, State.RUNNING)
    window._job_changed(job)
    assert window.queue_panel.isVisible()
    job.state = State.DONE
    window._job_changed(job)
    assert window.queue_panel.isVisible() and window.queue_linger.isActive()
    window.queue_linger.timeout.emit()
    assert not window.queue_panel.isVisible()


def test_the_queue_closed_by_hand_during_a_transfer_stays_closed_unless_one_fails(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    job = finished_job(window, State.RUNNING)
    window._job_changed(job)
    window.actions_by_name["queue"].trigger()
    window._job_changed(job)
    assert not window.queue_panel.isVisible()
    job.state = State.FAILED
    window._job_changed(job)
    assert window.queue_panel.isVisible()


def test_a_new_run_of_transfers_shows_the_queue_again_after_it_was_closed_by_hand(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    job = finished_job(window, State.RUNNING)
    window._job_changed(job)
    window.actions_by_name["queue"].trigger()
    job.state = State.DONE
    window._job_changed(job)
    next_job = finished_job(window, State.RUNNING)
    window._job_changed(next_job)
    assert window.queue_panel.isVisible()
    next_job.state = State.DONE  # closing the window would ask about a running transfer


def test_a_huge_queue_height_in_the_window_file_changes_nothing(qtbot: QtBot) -> None:
    settings.save_window({"queue_height": "9" * 5000})
    assert window_for(qtbot).queue_height == QUEUE_HEIGHT


def test_many_tabs_leave_the_general_buttons_in_full_view(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    for number in range(12):
        window.tab_bar.addTab(f"server{number}.example.com")
    qtbot.wait(50)
    assert window.general_bar.width() >= window.general_bar.sizeHint().width()


def test_a_pinned_queue_stays_and_comes_back_after_a_restart(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    window.actions_by_name["queue"].trigger()
    window.queue_panel.pin.setChecked(True)
    job = finished_job(window, State.DONE)
    window._job_changed(job)
    window._hide_idle_queue()
    assert window.queue_panel.isVisible()
    window.close()
    assert shown_window(qtbot).queue_panel.isVisible()


def test_letting_go_of_the_pin_hides_an_idle_queue_after_a_moment(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    window.actions_by_name["queue"].trigger()
    window.queue_panel.pin.setChecked(True)
    window.queue_panel.pin.setChecked(False)
    assert window.queue_linger.isActive()


def test_a_failed_transfer_keeps_the_queue_open_until_it_is_closed_by_hand(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    job = finished_job(window, State.FAILED)
    window._job_changed(job)
    assert window.queue_panel.isVisible() and not window.queue_linger.isActive()
    other = finished_job(window, State.DONE)
    window._job_changed(other)
    window._hide_idle_queue()
    assert window.queue_panel.isVisible()
    window.actions_by_name["queue"].trigger()
    assert not window.queue_panel.isVisible()
    assert not window.failure_unseen


def test_the_height_of_the_queue_is_the_one_the_person_dragged_also_after_a_restart(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    window.actions_by_name["queue"].trigger()
    total = sum(window.body.sizes())
    window.body.setSizes([total - 200, 200])
    window.actions_by_name["queue"].trigger()
    window.actions_by_name["queue"].trigger()
    assert window.body.sizes()[1] == 200
    window.close()
    again = shown_window(qtbot)
    again.actions_by_name["queue"].trigger()
    assert again.body.sizes()[1] == 200


def test_a_hidden_queue_gives_all_the_height_to_the_lists(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    assert window.sides.height() == window.body.height()


def test_the_side_buttons_are_one_row_with_the_text_on_hover(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    for side in (window.local_side, window.remote_side):
        assert side.navigation.y() == side.actions_bar.y()
        assert side.actions_bar.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
    assert window.actions_by_name["delete"].toolTip() == "Delete"


def test_the_general_buttons_share_the_row_of_the_tabs(qtbot: QtBot) -> None:
    window = shown_window(qtbot)
    assert window.general_bar.parent() is window.top_row
    assert window.findChildren(QToolBar, options=Qt.FindChildOption.FindDirectChildrenOnly) == []
