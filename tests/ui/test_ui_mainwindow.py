import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_ftp.config import connections, settings
from chrono_ftp.core import colours
from chrono_ftp.ui import theme
from chrono_ftp.ui.localside import NO_ACCESS_TEXT, LocalSide
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.remoteside import NOT_CONNECTED_TEXT, RemoteSide
from chrono_ftp.ui.settingsdialog import SettingsDialog
from tests.fakeservers import NOT_AS_ROOT

# --- Version ------------------------------------------------------------
__version__ = "test_ui_mainwindow.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Tests that need a refusal by the rights of a folder skip as root
# v0.0.11: An answer of a thread after the window closed goes nowhere
# v0.0.7: One row of buttons
# v0.0.6: The shared file list
# v0.0.3: Initial: the fixed sides, the grey of no connection, a local folder really shown


ALL_ROLES: tuple[QPalette.ColorRole, ...] = tuple(role for role in QPalette.ColorRole if role not in {QPalette.ColorRole.NoRole, QPalette.ColorRole.NColorRoles})


def window_for(qtbot: QtBot) -> MainWindow:
    window = MainWindow()
    qtbot.addWidget(window)
    return window


def names_in(side: LocalSide) -> set[str]:
    return set(side.file_list.names())


def test_the_left_side_is_local_and_the_right_side_is_remote(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    assert window.sides.indexOf(window.local_side) == 0
    assert window.sides.indexOf(window.remote_side) == 1
    assert isinstance(window.sides.widget(0), LocalSide)
    assert isinstance(window.sides.widget(1), RemoteSide)


def test_no_side_can_be_dragged_away(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    assert not window.sides.childrenCollapsible()


def test_the_tab_bar_runs_above_both_sides(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    bar_top_left = window.top_row.mapTo(window, QPoint(0, 0))
    sides_top_left = window.sides.mapTo(window, QPoint(0, 0))
    assert bar_top_left.y() < sides_top_left.y()
    assert window.tab_bar.height() > 0
    assert window.top_row.width() == window.sides.width()


def test_the_lists_get_nearly_all_the_height(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    line = window.fontMetrics().height()
    for side in (window.local_side, window.remote_side):
        assert side.path_line.height() <= 2 * line
        assert side.file_list.height() >= side.height() - side.navigation.height() - 4 * line
    assert window.tab_bar.height() <= 3 * line


def test_the_right_side_is_plain_grey_and_says_not_connected(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    remote = window.remote_side
    assert remote.path_line.text() == NOT_CONNECTED_TEXT
    grey = QColor(colours.NOT_CONNECTED)
    assert remote.tree.palette().color(QPalette.ColorRole.Base) == grey
    assert remote.file_list.palette().color(QPalette.ColorRole.Base) == grey


def test_the_right_side_is_empty_without_a_connection(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    remote = window.remote_side
    for view in (remote.tree, remote.file_list):
        model = view.model()
        assert model is not None and model.rowCount() == 0


def test_the_left_side_keeps_the_neutral_colour(qtbot: QtBot, qapp: QApplication) -> None:
    theme.apply(qapp)
    window = window_for(qtbot)
    local = window.local_side
    assert local.file_list.palette().color(QPalette.ColorRole.Base) == QColor(colours.PANEL)
    assert local.tree.palette().color(QPalette.ColorRole.Base) == QColor(colours.PANEL)


def test_the_left_side_starts_in_the_home_folder(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    window = window_for(qtbot)
    assert window.local_side.folder == str(tmp_path)


def test_without_a_home_folder_the_left_side_starts_at_the_root(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "gone"))
    window = window_for(qtbot)
    assert window.local_side.folder == "/"


def test_the_left_side_shows_a_real_folder(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "report.txt").write_text("text", encoding="utf-8")
    (tmp_path / "pictures").mkdir()
    window = window_for(qtbot)
    local = window.local_side
    assert local.go_to(str(tmp_path))
    assert local.path_line.text() == str(tmp_path)
    qtbot.waitUntil(lambda: {"report.txt", "pictures"} <= names_in(local))


def test_a_double_click_on_a_folder_opens_it_and_the_parent_entry_goes_back(qtbot: QtBot, tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(tmp_path))
    qtbot.waitUntil(lambda: "inner" in names_in(local))
    local.file_list.opened.emit(local.file_list.files.rows[local.file_list.index_of("inner").row()])
    assert local.folder == str(inner)
    qtbot.waitUntil(lambda: ".." in names_in(local))
    local.file_list.opened.emit(None)
    assert local.folder == str(tmp_path)


def test_a_folder_that_does_not_exist_is_not_entered(qtbot: QtBot, tmp_path: Path) -> None:
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(tmp_path))
    assert not local.go_to(str(tmp_path / "gone"))
    assert local.folder == str(tmp_path)


def test_a_file_opened_in_the_list_leaves_the_folder_as_it_is(qtbot: QtBot, tmp_path: Path) -> None:
    file = tmp_path / "report.txt"
    file.write_text("text", encoding="utf-8")
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(tmp_path))
    qtbot.waitUntil(lambda: "report.txt" in names_in(local))
    local.file_list.opened.emit(local.file_list.files.rows[local.file_list.index_of("report.txt").row()])
    assert local.folder == str(tmp_path)


def test_the_tree_follows_the_folder(qtbot: QtBot, tmp_path: Path) -> None:
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(tmp_path))
    assert local.tree_model.filePath(local.tree.currentIndex()) == str(tmp_path)


def test_every_colour_of_the_palette_comes_from_the_colour_file() -> None:
    written = {value.lower() for name, value in vars(colours).items() if name.isupper() and isinstance(value, str)}
    palette = theme.neutral_palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for role in ALL_ROLES:
            assert palette.color(group, role).name() in written, role


def test_disabled_text_differs_from_enabled_text() -> None:
    palette = theme.neutral_palette()
    assert palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) != palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)


@pytest.mark.parametrize("rights", [0o444, 0o000])
def test_a_closed_folder_is_shown_as_closed(qtbot: QtBot, tmp_path: Path, rights: int) -> None:
    closed = tmp_path / "closed"
    closed.mkdir()
    window = window_for(qtbot)
    local = window.local_side
    try:
        closed.chmod(rights)
        assert local.go_to(str(closed))
        assert local.folder == str(closed)
        assert local.path_line.text() == f"{closed} ({NO_ACCESS_TEXT})"
        assert local.tree_model.filePath(local.tree.currentIndex()) == str(closed)
        assert names_in(local) == set()
    finally:
        closed.chmod(0o755)


@NOT_AS_ROOT
def test_the_arrow_keys_pass_through_a_closed_folder_in_the_tree(qtbot: QtBot, tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    window = window_for(qtbot)
    local = window.local_side
    try:
        (tmp_path / "b").chmod(0o000)
        with qtbot.waitExposed(window):
            window.show()
        local.go_to(str(tmp_path))
        local.tree.expand(local.tree_model.index(str(tmp_path)))
        qtbot.waitUntil(lambda: local.tree_model.rowCount(local.tree_model.index(str(tmp_path))) == 3)
        local.tree.setCurrentIndex(local.tree_model.index(str(tmp_path / "a")))
        local.tree.setFocus()
        QTest.keyClick(local.tree, Qt.Key.Key_Down)
        assert local.folder == str(tmp_path / "b")
        selected = local.tree.selectedIndexes()
        assert [local.tree_model.filePath(index) for index in selected] == [str(tmp_path / "b")]
        QTest.keyClick(local.tree, Qt.Key.Key_Down)
        assert local.folder == str(tmp_path / "c")
    finally:
        (tmp_path / "b").chmod(0o755)


def test_moving_in_the_tree_moves_the_list(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "next").mkdir()
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(tmp_path))
    local.tree.setCurrentIndex(local.tree_model.index(str(tmp_path / "next")))
    assert local.folder == str(tmp_path / "next")


def test_return_in_the_list_opens_the_folder(qtbot: QtBot, tmp_path: Path) -> None:
    inner = tmp_path / "inner"
    inner.mkdir()
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(tmp_path))
    qtbot.waitUntil(lambda: "inner" in names_in(local))
    with qtbot.waitExposed(window):
        window.show()
    local.file_list.setFocus()
    local.file_list.setCurrentIndex(local.file_list.index_of("inner"))
    QTest.keyClick(local.file_list, Qt.Key.Key_Return)
    assert local.folder == str(inner)


def test_when_the_folder_shown_is_removed_the_side_moves_up(qtbot: QtBot, tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(deep))
    shutil.rmtree(tmp_path / "a")
    qtbot.waitUntil(lambda: local.folder == str(tmp_path))
    assert local.path_line.text() == str(tmp_path)


def test_the_root_folder_has_no_parent_entry(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    local = window.local_side
    local.go_to("/")
    qtbot.waitUntil(lambda: len(names_in(local)) > 1)
    assert ".." not in names_in(local)


def test_a_new_file_keeps_the_selection_in_the_list(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "keep.txt").write_text("text", encoding="utf-8")
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(tmp_path))
    qtbot.waitUntil(lambda: "keep.txt" in names_in(local))
    local.file_list.setCurrentIndex(local.file_list.index_of("keep.txt"))
    (tmp_path / "new.txt").write_text("text", encoding="utf-8")
    qtbot.waitUntil(lambda: "new.txt" in names_in(local))
    assert local.file_list.currentIndex().data() == "keep.txt"


def test_when_a_folder_higher_up_is_renamed_the_side_moves_up(qtbot: QtBot, tmp_path: Path) -> None:
    deep = tmp_path / "p" / "q"
    deep.mkdir(parents=True)
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(deep))
    (tmp_path / "p").rename(tmp_path / "r")
    qtbot.waitUntil(lambda: local.folder == str(tmp_path))
    assert local.tree_model.filePath(local.tree.currentIndex()) == str(tmp_path)


def test_a_folder_removed_and_made_again_is_watched_again(qtbot: QtBot, tmp_path: Path) -> None:
    folder = tmp_path / "m"
    folder.mkdir()
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(folder))
    qtbot.waitUntil(lambda: ".." in names_in(local))
    folder.rename(tmp_path / "away")
    folder.mkdir()
    (folder / "new.txt").write_text("text", encoding="utf-8")
    qtbot.waitUntil(lambda: "new.txt" in names_in(local))
    assert local.folder == str(folder)
    assert str(folder) in local.watcher.directories()
    (folder / "new.txt").unlink()
    folder.rmdir()
    qtbot.waitUntil(lambda: local.folder == str(tmp_path))


@NOT_AS_ROOT
def test_a_folder_opened_while_shown_shows_its_files(qtbot: QtBot, tmp_path: Path) -> None:
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "file.txt").write_text("text", encoding="utf-8")
    window = window_for(qtbot)
    local = window.local_side
    try:
        folder.chmod(0o000)
        local.go_to(str(folder))
        assert names_in(local) == set()
        folder.chmod(0o755)
        qtbot.waitUntil(lambda: "file.txt" in names_in(local))
        assert local.path_line.text() == str(folder)
    finally:
        folder.chmod(0o755)


@NOT_AS_ROOT
def test_a_folder_closed_while_shown_is_shown_as_closed(qtbot: QtBot, tmp_path: Path) -> None:
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "file.txt").write_text("text", encoding="utf-8")
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(folder))
    qtbot.waitUntil(lambda: "file.txt" in names_in(local))
    try:
        folder.chmod(0o000)
        qtbot.waitUntil(lambda: NO_ACCESS_TEXT in local.path_line.text())
        assert names_in(local) == set()
    finally:
        folder.chmod(0o755)


@NOT_AS_ROOT
def test_a_closed_folder_read_earlier_shows_nothing(qtbot: QtBot, tmp_path: Path) -> None:
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "seen.txt").write_text("text", encoding="utf-8")
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(folder))
    qtbot.waitUntil(lambda: "seen.txt" in names_in(local))
    local.go_to(str(tmp_path))
    try:
        folder.chmod(0o000)
        local.go_to(str(folder))
        assert names_in(local) == set()
    finally:
        folder.chmod(0o755)


def test_another_folder_renamed_to_the_same_name_is_read_anew(qtbot: QtBot, tmp_path: Path) -> None:
    old = tmp_path / "j"
    other = tmp_path / "k"
    old.mkdir()
    other.mkdir()
    (old / "old.txt").write_text("text", encoding="utf-8")
    (other / "new.txt").write_text("text", encoding="utf-8")
    window = window_for(qtbot)
    local = window.local_side
    local.go_to(str(old))
    qtbot.waitUntil(lambda: "old.txt" in names_in(local))
    old.rename(tmp_path / "j2")
    other.rename(old)
    qtbot.waitUntil(lambda: "new.txt" in names_in(local))
    assert local.folder == str(old)


@NOT_AS_ROOT
def test_a_closed_folder_keeps_the_column_names(qtbot: QtBot, tmp_path: Path) -> None:
    closed = tmp_path / "closed"
    closed.mkdir()
    window = window_for(qtbot)
    local = window.local_side
    try:
        closed.chmod(0o000)
        local.go_to(str(closed))
        assert local.file_list.names() == []
        assert local.file_list.model().headerData(0, Qt.Orientation.Horizontal) == "Name"
    finally:
        closed.chmod(0o755)


def test_return_does_not_act_on_an_entry_of_the_folder_before(qtbot: QtBot, tmp_path: Path) -> None:
    (tmp_path / "inner").mkdir()
    (tmp_path / "other").mkdir()
    window = window_for(qtbot)
    local = window.local_side
    with qtbot.waitExposed(window):
        window.show()
    local.go_to(str(tmp_path))
    qtbot.waitUntil(lambda: "inner" in names_in(local))
    local.file_list.setCurrentIndex(local.file_list.index_of("inner"))
    local.go_to(str(tmp_path / "other"))
    local.file_list.setFocus()
    QTest.keyClick(local.file_list, Qt.Key.Key_Return)
    assert local.folder == str(tmp_path / "other")


@pytest.mark.parametrize("place,shown,orientation", [(settings.TREE_LEFT, True, Qt.Orientation.Horizontal), (settings.TREE_ABOVE, True, Qt.Orientation.Vertical), (settings.TREE_HIDDEN, False, Qt.Orientation.Horizontal)])
def test_the_folder_tree_goes_where_the_settings_say_on_both_sides(qtbot: QtBot, place: str, shown: bool, orientation: Qt.Orientation) -> None:
    settings.save(settings.Settings(place))
    window = window_for(qtbot)
    with qtbot.waitExposed(window):
        window.show()
    for side in (window.local_side, window.remote_side):
        assert side.tree.isVisible() is shown
        assert side.parts.orientation() == orientation
        assert side.file_list.isVisible()


def test_a_change_in_the_settings_window_applies_at_once_and_is_kept(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    window = window_for(qtbot)

    def accepted(dialog: SettingsDialog) -> int:
        dialog.tree_place.setCurrentIndex(dialog.tree_place.findData(settings.TREE_ABOVE))
        return SettingsDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SettingsDialog, "exec", accepted)
    window.actions_by_name["settings"].trigger()
    assert window.remote_side.parts.orientation() == Qt.Orientation.Vertical
    assert settings.load().tree_place == settings.TREE_ABOVE


def test_a_bad_settings_file_gives_the_standard_settings() -> None:
    path = connections.config_folder() / settings.FILE_NAME
    path.parent.mkdir(parents=True)
    path.write_text('{"tree_place": "nowhere"}', encoding="utf-8")
    assert settings.load().tree_place == settings.TREE_LEFT
    path.write_text("{broken", encoding="utf-8")
    assert settings.load().tree_place == settings.TREE_LEFT


def test_an_answer_that_comes_after_the_window_closed_goes_nowhere(qtbot: QtBot) -> None:
    window = window_for(qtbot)
    reached: list[str] = []
    window.bridge.deliver(lambda: reached.append("on its way"))  # handed in before the close, its turn comes after it
    window.close()
    window.bridge.deliver(lambda: reached.append("late"))
    QApplication.processEvents()
    assert reached == []
