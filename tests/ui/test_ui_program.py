"""Block FP in the window: the Settings window with its pages (FP-01) and what its choices do, the Session Log window (FP-02), the Server
Information window (FP-03), the Clean Up window (FP-04), the licence (FP-05) and the update check against a fake answer (FP-06)."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from pytestqt.qtbot import QtBot

from chrono_ftp.config import bookmarks as saved_places
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.connections import config_folder
from chrono_ftp.connections import sessionlog
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.logrules import DEBUG, DETAILED, NORMAL, OFF, LogRules
from chrono_ftp.core.synchronizing import Options as SyncOptions
from chrono_ftp.engine import cleanup, updatecheck
from chrono_ftp.transfers.queue import ENDED, Job, State
from chrono_ftp.ui.aboutdialog import CURRENT_TEXT, AboutWindow
from chrono_ftp.ui.cleanupdialog import CleanupWindow
from chrono_ftp.ui.logwindow import OFF_TEXT, LogWindow
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.serverinfodialog import ServerInfoWindow
from chrono_ftp.ui.settingsdialog import PAGES, SettingsDialog
from chrono_ftp.ui.transferdialog import TransferDialog
from chrono_ftp.ui.windowtabs import INFORMATION_TEXT, LOG_TEXT, NOT_LIVE_TEXT
from tests.fakeservers import PASSWORD, Passwd, USER, ssh_server
from tests.ui.test_ui_sessions import connected, names_on_the_right, select, ssh_settings, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_program.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: The Presets page follows Transfers (FD-04)
# v0.0.39: The Colours page follows Panels (FQ-01); a test that transfers waits until its job has ended, not only until the file is
#          there, so closing its window never finds a transfer still finishing (AGENTS.md 4.4.4)
# v0.0.38: Initial: block FP in the window (FP-01 to FP-06); review: the automatic refresh writes a summary to the log and waits for a
#          drag; the Log window takes new lines in batches

__all__ = ["window"]  # the fixture, used by name


def no_question(*args: object) -> QMessageBox.StandardButton:
    raise AssertionError(f"asked although switched off: {args[2]}")


def yes(*args: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Yes


def with_settings(window: MainWindow, **changes: object) -> None:
    window.settings = replace(window.settings, **changes)  # pyrefly: ignore[bad-argument-type]
    window._apply_settings()


# --- the Settings window (FP-01) -----------------------------------------------


def test_the_settings_window_has_its_pages_and_keeps_every_choice(qtbot: QtBot) -> None:
    chosen = saved_settings.Settings(tree_place=saved_settings.TREE_ABOVE, show_hidden=False, transfers_at_once=3, last_connection="Shop", confirm_delete=False, double_click=saved_settings.TRANSFER, refresh_seconds=60, columns_per_tab=True, log_level=NORMAL, log_size_megabytes=7, logs_kept=1)
    dialog = SettingsDialog(chosen)
    qtbot.addWidget(dialog)
    assert [dialog.page_list.item(row).text() for row in range(dialog.page_list.count())] == ["General", "Panels", "Colours", "Transfers", "Presets", "Queue", "Confirmations", "Log"]
    assert list(PAGES) == ["General", "Panels", "Colours", "Transfers", "Presets", "Queue", "Confirmations", "Log"]
    dialog.show_page("Confirmations")
    assert dialog.pages.currentIndex() == 6
    assert dialog.chosen() == chosen  # unchanged, every value comes back as it was, also the last connection it does not show


def unshown_settings() -> saved_settings.Settings:
    """Settings in which every value the Settings window does not show is away from its standard, so losing one is visible."""
    return saved_settings.Settings(
        last_connection="Shop",
        toolbars=saved_settings.ToolbarChoices(hidden=("queue",)),
        mask_history=("*.log", "*.tmp"),
        checksum_algorithm="md5",
        synchronize=replace(SyncOptions(), case_matters=False, delete_other_side=True, no_subfolders=True),
        scan_counts=(("/here\n/there", 12),),
        tab_order=("Shop", "Office"),
        open_connection_folders=("Work",),
        new_connection_from="Shop",
    )


def test_ok_in_settings_keeps_every_setting_the_window_does_not_show(qtbot: QtBot) -> None:
    """The checksum algorithm, the Synchronize choices, the counted folder pairs, the order of the tabs, the open folders of the
    connection list, the connection New starts from, the last connection, the toolbars and the masks typed are all kept."""
    before = unshown_settings()
    dialog = SettingsDialog(before)
    qtbot.addWidget(dialog)
    dialog.show_hidden.setChecked(not before.show_hidden)
    after = dialog.chosen()
    assert after.show_hidden is not before.show_hidden  # what the window shows did change
    assert replace(after, show_hidden=before.show_hidden) == before  # and nothing else changed, whatever else the settings hold
    for name in ("checksum_algorithm", "synchronize", "scan_counts", "tab_order", "open_connection_folders", "new_connection_from", "last_connection", "toolbars", "mask_history"):
        assert getattr(after, name) == getattr(before, name)


def test_ok_in_settings_keeps_the_settings_the_window_does_not_show_and_writes_them(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the window itself: OK changes what was chosen and the file keeps everything else."""
    before = unshown_settings()
    window.settings = before

    def accepted(dialog: SettingsDialog) -> int:
        dialog.show_hidden.setChecked(not before.show_hidden)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SettingsDialog, "exec", accepted)
    window.open_settings()
    assert window.settings.show_hidden is not before.show_hidden
    assert replace(window.settings, show_hidden=before.show_hidden) == before
    assert replace(saved_settings.load(), show_hidden=before.show_hidden) == before


def test_the_settings_window_puts_its_choices_on_the_settings_of_this_moment(qtbot: QtBot) -> None:
    """A setting can change while the window stands open (the connection last live): OK keeps the value of this moment, not the older one."""
    dialog = SettingsDialog(saved_settings.Settings(last_connection="Shop"))
    qtbot.addWidget(dialog)
    dialog.show_hidden.setChecked(False)
    after = dialog.chosen(saved_settings.Settings(last_connection="Office"))
    assert after.last_connection == "Office" and after.show_hidden is False


def test_the_automatic_refresh_is_off_at_its_lowest_step(qtbot: QtBot) -> None:
    dialog = SettingsDialog(saved_settings.Settings())
    qtbot.addWidget(dialog)
    assert dialog.refresh.text() == "Never by itself"
    assert dialog.chosen().refresh_seconds == 0
    dialog.refresh.setValue(saved_settings.FEWEST_REFRESH_SECONDS)
    assert dialog.chosen().refresh_seconds == saved_settings.FEWEST_REFRESH_SECONDS


def test_ok_in_settings_applies_the_log_and_the_refresh_at_once_and_keeps_them(window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    def accepted(dialog: SettingsDialog) -> int:
        dialog.log_level.setCurrentIndex(dialog.log_level.findData(NORMAL))
        dialog.refresh.setValue(30)
        return QDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SettingsDialog, "exec", accepted)
    window.open_settings()
    assert sessionlog.rules().level == NORMAL
    assert window.refresh_timer.isActive() and window.refresh_timer.interval() == 30_000
    assert saved_settings.load().log_level == NORMAL and saved_settings.load().refresh_seconds == 30


def test_delete_without_its_question_deletes_at_once(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        with_settings(window, confirm_delete=False)
        monkeypatch.setattr(QMessageBox, "question", no_question)
        select(window, "a.txt")
        window.actions_by_name["delete"].trigger()
        qtbot.waitUntil(lambda: not (tmp_path / "a.txt").exists())


def test_closing_a_tab_with_unfinished_transfers_asks_only_while_that_question_is_on(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        for name in ("Shop", "Other"):
            connected(qtbot, window, ssh_settings(server, name))
        for session in window.sessions:
            assert session.remote is not None
            window.queue.add(Job(session.remote, session.connection, str(tmp_path / "here.txt"), "/a.txt", False, "a.txt", state=State.PAUSED, tab=session))
        asked: list[str] = []

        def no(*args: object) -> QMessageBox.StandardButton:
            asked.append(str(args[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", no)
        window.close_tab(1)
        assert len(asked) == 1 and len(window.sessions) == 2  # asked, and No keeps the tab
        with_settings(window, confirm_close_running=False)
        monkeypatch.setattr(QMessageBox, "question", no_question)
        window.close_tab(1)
        window.close_tab(0)
        assert window.sessions == []
        qtbot.waitUntil(lambda: all(job.state == State.STOPPED for job in window.queue.jobs))


def test_a_drop_asks_through_a_question_where_the_transfer_window_is_off(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        with_settings(window, show_transfer_window=False)
        asked: list[str] = []

        def no(*args: object) -> QMessageBox.StandardButton:
            asked.append(str(args[2]))
            return QMessageBox.StandardButton.No

        monkeypatch.setattr(QMessageBox, "question", no)
        window._offer(session, [str(tmp_path / "a.txt")], "/", True, after_a_drop=True)
        qtbot.waitUntil(lambda: len(asked) == 1)
        assert "Upload 1 item to /" in asked[0] and not window.queue.jobs
        with_settings(window, show_transfer_window=True, confirm_drop=False)
        monkeypatch.setattr(QMessageBox, "question", no_question)
        monkeypatch.setattr(TransferDialog, "exec", no_question)  # the transfer window does not come either
        window._offer(session, [str(tmp_path / "a.txt")], "/", True, after_a_drop=True)
        qtbot.waitUntil(lambda: (root / "a.txt").exists())
        qtbot.waitUntil(lambda: bool(window.queue.jobs) and all(job.state in ENDED for job in window.queue.jobs))  # the job, not only its file
        assert all(job.state == State.DONE for job in window.queue.jobs)


def test_a_double_click_on_a_server_file_downloads_it_where_that_is_chosen(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("content", encoding="utf-8")
    here = tmp_path / "here"
    here.mkdir()
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.local_side.walk_to(str(here))
        qtbot.waitUntil(lambda: window.local_side.folder == str(here) and "a.txt" in names_on_the_right(window))
        with_settings(window, double_click=saved_settings.TRANSFER, show_transfer_window=False)
        entry = next(entry for entry in window.remote_side.file_list.files.rows if entry is not None and entry.name == "a.txt")
        window.remote_side.file_opened.emit(entry)
        qtbot.waitUntil(lambda: (here / "a.txt").exists() and (here / "a.txt").read_text("utf-8") == "content")
        qtbot.waitUntil(lambda: bool(window.queue.jobs) and all(job.state in ENDED for job in window.queue.jobs))  # the job, not only its file
        assert all(job.state == State.DONE for job in window.queue.jobs)
        assert not window.edited  # transferred, not opened for editing


def test_the_automatic_refresh_shows_what_changed_and_keeps_the_selection(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        with_settings(window, refresh_seconds=saved_settings.FEWEST_REFRESH_SECONDS)
        assert window.refresh_timer.isActive()
        select(window, "a.txt")
        (root / "new.txt").write_text("n", encoding="utf-8")
        window.refresh_by_itself()  # what the timer does
        qtbot.waitUntil(lambda: "new.txt" in names_on_the_right(window))
        assert [entry.name for entry in window.remote_side.selected_entries()] == ["a.txt"]
        with_settings(window, refresh_seconds=0)
        assert not window.refresh_timer.isActive()


def test_the_automatic_refresh_writes_one_summary_line_and_its_listing_only_at_debug(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    for number in range(50):
        (root / f"file{number}.txt").write_text("x", encoding="utf-8")
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        qtbot.waitUntil(lambda: "file0.txt" in names_on_the_right(window))
        log = sessionlog.log_for("Shop")
        for level, listed in ((DETAILED, False), (DEBUG, True)):
            with_settings(window, log_level=level)
            path = log.path
            assert path is not None
            before = path.read_text("utf-8") if path.exists() else ""
            window.refresh_by_itself()
            qtbot.waitUntil(lambda: not session.auto_refreshing)
            added = (path.read_text("utf-8"))[len(before):]
            assert "automatic refresh of /: 50 entries, unchanged" in added
            assert ("file7.txt" in added) == listed  # the listing itself only at Debug


def test_the_automatic_refresh_waits_while_a_drag_runs(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        during: list[bool] = []

        def dragging(drag: QDrag, action: Qt.DropAction) -> Qt.DropAction:
            window.refresh_by_itself()  # the timer fires while the person drags
            during.append(session.auto_refreshing)
            return Qt.DropAction.IgnoreAction

        monkeypatch.setattr(QDrag, "exec", dragging)
        window.remote_side.file_list.startDrag(Qt.DropAction.CopyAction)
        assert during == [False]  # nothing was asked of the server under the drag
        window.local_side.file_list.dragged_over = True  # a drag over the left side
        window.refresh_by_itself()
        assert not session.auto_refreshing
        window.local_side.file_list.dragged_over = False
        window.refresh_by_itself()  # the next tick after the drag
        assert session.auto_refreshing
        qtbot.waitUntil(lambda: not session.auto_refreshing)


def test_each_tab_keeps_its_own_sort_order_where_that_is_chosen(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        with_settings(window, columns_per_tab=True)
        connected(qtbot, window, ssh_settings(server, "Shop"))
        header = window.remote_side.file_list.header()
        window.remote_side.file_list.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        connected(qtbot, window, ssh_settings(server, "Other"))
        window.remote_side.file_list.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        window.tab_bar.setCurrentIndex(0)
        assert (header.sortIndicatorSection(), header.sortIndicatorOrder()) == (1, Qt.SortOrder.DescendingOrder)
        window.tab_bar.setCurrentIndex(1)
        assert (header.sortIndicatorSection(), header.sortIndicatorOrder()) == (0, Qt.SortOrder.AscendingOrder)


# --- the Session Log window (FP-02) ------------------------------------------------


def test_the_session_log_window_shows_each_new_line_as_it_comes(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        with_settings(window, log_level=NORMAL)
        connected(qtbot, window, ssh_settings(server))
        session = window.sessions[0]
        window.tab_menu_for(window.tab_bar.tabRect(0).center())  # the menu offers it
        window.show_session_log(session)
        log_window = window.log_windows["Shop"]
        assert isinstance(log_window, LogWindow)
        assert "connect to 127.0.0.1" in log_window.text.toPlainText()  # what the file holds already
        window.refresh()
        qtbot.waitUntil(lambda: log_window.text.toPlainText().count("> readdir /") >= 2)
        assert PASSWORD not in log_window.text.toPlainText()
        log_window.close()


def test_the_session_log_window_takes_many_lines_in_one_call(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    sessionlog.set_rules(LogRules(DETAILED))
    log = sessionlog.log_for("Many")
    log_window = LogWindow("Many", log)
    qtbot.addWidget(log_window)
    calls: list[int] = []
    deliver = log_window.bridge.deliver

    def counted(call: Callable[[], None]) -> None:
        calls.append(1)
        deliver(call)

    monkeypatch.setattr(log_window.bridge, "deliver", counted)
    log.answered_lines([f"entry {number}" for number in range(5000)])
    log.note("last")
    qtbot.waitUntil(lambda: "last" in log_window.text.toPlainText())
    assert len(calls) <= 2  # one call per batch, never one per line
    assert "entry 4999" in log_window.text.toPlainText()
    log_window.close()


def test_the_session_log_window_says_when_the_log_is_off(qtbot: QtBot) -> None:
    log_window = LogWindow("Shop", sessionlog.log_for("Shop"))
    qtbot.addWidget(log_window)
    assert log_window.state.text() == OFF_TEXT
    assert sessionlog.rules().level == OFF


# --- the Server Information window (FP-03) ---------------------------------------------


def test_server_information_opens_from_the_menu_of_a_tab_and_is_read_on_its_thread(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ssh_server(tmp_path, commands=Passwd(PASSWORD)) as server:  # every command answered by the test: nothing runs here
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        menu = window.tab_menu_for(window.tab_bar.tabRect(0).center())
        assert menu is not None
        texts = {action.text(): action for action in menu.actions()}
        assert LOG_TEXT in texts and texts[INFORMATION_TEXT].isEnabled()
        texts[INFORMATION_TEXT].trigger()
        info = window.info_windows["Shop"]
        assert isinstance(info, ServerInfoWindow)
        qtbot.waitUntil(lambda: info.copy_button.isEnabled())
        info.copy()
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        copied = clipboard.text()
        assert "Protocol: SFTP version" in copied and "Fingerprint (SHA-256): SHA256:" in copied and f"Server: {USER}@127.0.0.1" in copied
        assert "Free space:" in copied
        info.close()


def test_server_information_is_greyed_with_its_reason_while_a_tab_is_not_connected(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[str] = []

    def noted(*args: object) -> QMessageBox.StandardButton:
        told.append(str(args[2]))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "information", noted)
    window.open_connection(Connection(name="Gone", host="127.0.0.1", port=1, user=USER))  # nothing listens there
    qtbot.waitUntil(lambda: bool(told))
    menu = window.tab_menu_for(window.tab_bar.tabRect(0).center())
    assert menu is not None
    information = next(action for action in menu.actions() if action.text() == INFORMATION_TEXT)
    assert not information.isEnabled() and information.toolTip() == NOT_LIVE_TEXT


# --- the Clean Up window (FP-04) -------------------------------------------------------


def test_clean_up_ticks_nothing_at_first_and_removes_exactly_what_was_ticked(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    saved_places.save(saved_places.Places(bookmarks=(saved_places.Bookmark(saved_places.LOCAL, "", "/tmp", "tmp", ""),)))
    saved_settings.save(saved_settings.Settings(show_hidden=False))
    window.places = saved_places.load().places
    opened: list[CleanupWindow] = []

    def run(dialog: CleanupWindow) -> int:
        opened.append(dialog)
        assert not dialog.remove_button.isEnabled()  # nothing ticked at first
        rows = {dialog.kinds[row].key: dialog.tree.topLevelItem(row) for row in range(len(dialog.kinds))}
        item = rows[cleanup.BOOKMARKS]
        assert item is not None and item.checkState(0) == Qt.CheckState.Unchecked
        item.setCheckState(0, Qt.CheckState.Checked)
        assert dialog.remove_button.isEnabled()
        dialog.remove()
        return QDialog.DialogCode.Accepted.value

    asked: list[str] = []

    def noted_yes(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(CleanupWindow, "exec", run)
    monkeypatch.setattr(QMessageBox, "question", noted_yes)
    assert not window.clean_up_stored_data()
    assert opened and len(asked) == 1 and "Bookmarks and location profiles" in asked[0] and "Settings" not in asked[0]
    assert not (config_folder() / saved_places.FILE_NAME).exists()
    assert (config_folder() / saved_settings.FILE_NAME).exists() and not saved_settings.load().show_hidden
    assert window.places == saved_places.Places()


def test_cleaning_up_the_settings_closes_the_settings_window_and_writes_nothing_back(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    with_settings(window, show_hidden=False)
    saved_settings.save(window.settings)

    def run(dialog: CleanupWindow) -> int:
        rows = {dialog.kinds[row].key: dialog.tree.topLevelItem(row) for row in range(len(dialog.kinds))}
        item = rows[cleanup.SETTINGS]
        assert item is not None
        item.setCheckState(0, Qt.CheckState.Checked)
        dialog.remove()
        return QDialog.DialogCode.Accepted.value

    def settings_run(dialog: SettingsDialog) -> int:
        dialog.clean_up_button.click()
        assert not dialog.isVisible() and dialog.result() == QDialog.DialogCode.Rejected.value
        return dialog.result()

    monkeypatch.setattr(CleanupWindow, "exec", run)
    monkeypatch.setattr(SettingsDialog, "exec", settings_run)
    monkeypatch.setattr(QMessageBox, "question", yes)
    window.open_settings()
    assert not (config_folder() / saved_settings.FILE_NAME).exists()
    assert window.settings.show_hidden  # the standard settings, in memory
    window._save_sizes()
    assert not (config_folder() / saved_settings.WINDOW_FILE_NAME).exists()  # closing writes no window places back


# --- About: the licence and the update check (FP-05, FP-06) -----------------------------


def test_licence_shows_the_full_agpl_text(qtbot: QtBot) -> None:
    about = AboutWindow()
    qtbot.addWidget(about)
    about.licence_button.click()
    assert about.licence is not None and about.licence.isVisible()
    text = about.licence.text.toPlainText()
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text and "Version 3, 19 November 2007" in text
    about.licence.close()


def test_check_for_updates_says_calmly_whether_a_newer_version_is_there(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def newer(address: str) -> tuple[bytes, str]:
        asked.append(address)
        return json.dumps([{"name": "99.0.1"}, {"name": "0.0.1"}]).encode(), ""

    monkeypatch.setattr(updatecheck, "fetch", newer)  # never the real GitHub
    about = AboutWindow()
    qtbot.addWidget(about)
    about.update_button.click()
    qtbot.waitUntil(lambda: "99.0.1" in about.update_state.text())
    assert "releases/tag/99.0.1" in about.update_state.text() and about.update_button.isEnabled()
    assert asked == ["https://api.github.com/repos/rob-vandenberg/chrono-ftp/tags?per_page=100"]

    def same(address: str) -> tuple[bytes, str]:
        return json.dumps([{"name": "0.0.1"}]).encode(), ""

    monkeypatch.setattr(updatecheck, "fetch", same)
    about.update_button.click()
    qtbot.waitUntil(lambda: "newest version" in about.update_state.text())
    assert about.update_state.text().startswith(CURRENT_TEXT.split("{")[0])


def test_a_failed_update_check_says_so_calmly(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(address: str) -> tuple[bytes, str]:
        raise updatecheck.UpdateCheckFailed(updatecheck.NOT_REACHED_TEXT)

    monkeypatch.setattr(updatecheck, "fetch", unreachable)
    about = AboutWindow()
    qtbot.addWidget(about)
    about.update_button.click()
    qtbot.waitUntil(lambda: about.update_button.isEnabled() and "could not be reached" in about.update_state.text())
