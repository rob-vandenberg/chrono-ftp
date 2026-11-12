"""Block FM in the window: the rights editor, the Properties window on both sides and for several files, links, new files, Duplicate
and Copy to, my public key and the password on a fake server (OR-26), the file manager and the terminal in the folder shown."""

import asyncio
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path

import asyncssh
import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QLabel, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from chrono_ftp.connections.base import RemoteEntry
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core.errors import RemoteActionFailed
from chrono_ftp.ui.plaintext import NAME_SHARE
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.engine import localfiles, properties
from chrono_ftp.engine.rights import rights_of
from chrono_ftp.ui import windowproperties, windowserver
from chrono_ftp.ui.actiondialogs import CopyToDialog, LinkDialog, NewFolderDialog, PasswordDialog
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.propertiesdialog import Abilities, PropertiesDialog
from chrono_ftp.ui.rightseditor import RightsEditor
from chrono_ftp.ui.windownavigation import PROGRAM_KEYS
from chrono_ftp.ui.windowtabs import KEY_TEXT, PASSWORD_TEXT
from tests.conftest import MemoryStore
from tests.fakeservers import PASSWORD, USER, Passwd, ftp_server, ssh_server
from tests.ui.test_ui_sessions import connected, names_on_the_right, select, ssh_settings, trust_server, window

# --- Version ------------------------------------------------------------
__version__ = "test_ui_properties.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Initial: block FM, Properties and server actions (D-060 to D-063); review round 2: Copy To goes on after a refusal and
#          reads ~ and relative folders; review round 3: a connection lost while a window is open, a second F9, an incomplete octal
#          number, a temporary file that can't be made; review round 4: touched boxes only, refusals go on, slow names, Enter in a
#          field, texts and names read off the window's thread; review round 5: a date out of range, names the lists don't hold, a
#          name the screen can't show, rights set as a whole; review round 6: folders get Run only for Read switched on, root's password

_ = window  # the window of the session tests, with a password in the test's store


def ok_of(buttons: QDialogButtonBox) -> bool:
    button = buttons.button(QDialogButtonBox.StandardButton.Ok)
    return button is not None and button.isEnabled()


def pick_local(window: MainWindow, folder: Path, *names: str) -> None:
    window.local_side.go_to(str(folder))
    selection = window.local_side.file_list.selectionModel()
    assert selection is not None
    selection.clear()
    for name in names:
        selection.select(window.local_side.file_list.index_of(name), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)


def giving[T](value: T) -> Callable[..., T]:
    def answer(*_args: object) -> T:
        return value

    return answer


def noting(started: list[list[str]]) -> Callable[..., None]:
    """A program that is started is only noted."""

    def start(arguments: list[str], **_options: object) -> None:
        started.append(arguments)

    return start


def answered(told: list[str]) -> object:
    def note(_parent: object, _title: str, text: str, *_args: object) -> QMessageBox.StandardButton:
        told.append(text)
        return QMessageBox.StandardButton.Yes

    return note


# --- the rights editor (FM-02) ------------------------------------------------


def test_the_boxes_and_the_octal_number_stay_in_step(qtbot: QtBot) -> None:
    editor = RightsEditor(rights_of(0o644))
    qtbot.addWidget(editor)
    assert editor.octal.text() == "0644"
    editor.boxes[2].setCheckState(Qt.CheckState.Checked)  # owner run
    assert editor.octal.text() == "0744" and editor.is_changed()
    editor.octal.clear()
    QTest.keyClicks(editor.octal, "750")
    assert editor.value() == rights_of(0o750)


def test_a_box_where_the_files_differ_starts_as_leave_and_has_no_number(qtbot: QtBot) -> None:
    editor = RightsEditor(properties.summary([RemoteEntry("a", False, permissions="0644"), RemoteEntry("b", False, permissions="0664")]).rights)
    qtbot.addWidget(editor)
    assert editor.boxes[4].checkState() == Qt.CheckState.PartiallyChecked and editor.boxes[4].isTristate()
    assert not editor.boxes[0].isTristate()
    assert editor.octal.text() == "" and not editor.copy_button.isEnabled()
    assert not editor.is_changed()
    editor.boxes[6].setCheckState(Qt.CheckState.Unchecked)
    assert editor.value()[4] is None and editor.value()[6] is False  # only the touched box changes


def test_the_copy_button_copies_the_octal_number(qtbot: QtBot) -> None:
    editor = RightsEditor(rights_of(0o755))
    qtbot.addWidget(editor)
    editor.copy_button.click()
    assert QGuiApplication.clipboard().text() == "0755"


# --- the Properties window (FM-01, FM-04) --------------------------------------


def dialog_for(qtbot: QtBot, entries: list[RemoteEntry], abilities: Abilities) -> PropertiesDialog:
    dialog = PropertiesDialog(properties.summary(entries), "/srv", abilities)
    qtbot.addWidget(dialog)
    return dialog


def test_ok_is_usable_only_when_something_changed(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("a", False, permissions="0644", owner="anna", group="web")], Abilities(rights=True, group=True, groups=("web", "staff"), names_known=True))
    assert not ok_of(dialog.buttons)
    dialog.group.setCurrentText("staff")
    assert ok_of(dialog.buttons)
    assert dialog.change().group == "staff" and dialog.change().rights is None
    dialog.group.setCurrentText("web")
    assert not ok_of(dialog.buttons)


def test_a_name_the_lists_do_not_hold_is_accepted_and_a_bad_form_is_not(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("a", False, owner="anna", group="web")], Abilities(group=True, groups=("web",), names_known=True))
    dialog.group.setCurrentText("CORP\\domain users")  # a directory group that getent does not list
    assert ok_of(dialog.buttons) and dialog.change().group == "CORP\\domain users"
    dialog.group.setCurrentText("-R")
    assert not ok_of(dialog.buttons) and "-R" in dialog.problem.text()


def test_a_date_out_of_range_shows_as_unknown(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("a", False, 1, float(2**40), "0644")], Abilities())
    assert "(unknown)" in [label.text() for label in dialog.findChildren(QLabel)]


def test_a_name_the_screen_can_not_show_is_kept_and_never_sent(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("a", False, owner="b\udcffd", group="web")], Abilities(owner=True, owners=("anna",)))
    assert not ok_of(dialog.buttons) and dialog.change().owner == ""  # left as it is: nothing changes
    dialog.owner.setCurrentText("b\ufffdde")
    assert not ok_of(dialog.buttons)  # a garbled name is never sent
    dialog.owner.setCurrentText("anna")
    assert ok_of(dialog.buttons) and dialog.change().owner == "anna"


def test_without_a_shell_unknown_rights_are_set_only_as_a_whole_and_the_window_says_so(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("a", False, permissions=""), RemoteEntry("b", False, permissions="")], Abilities(rights=True, whole_rights=True))
    dialog.show()
    assert dialog.whole_note.isVisible()  # before OK
    dialog.rights.boxes[6].setCheckState(Qt.CheckState.Unchecked)
    assert not ok_of(dialog.buttons) and dialog.problem.text()
    dialog.rights.octal.clear()
    QTest.keyClicks(dialog.rights.octal, "640")
    assert ok_of(dialog.buttons) and dialog.change().rights == rights_of(0o640)
    shell = dialog_for(qtbot, [RemoteEntry("a", False, permissions="")], Abilities(rights=True, whole_rights=False))
    shell.rights.boxes[6].setCheckState(Qt.CheckState.Unchecked)
    assert ok_of(shell.buttons)  # with a shell one box goes alone, as a chmod that needs no rights known


def test_several_files_show_where_they_differ_and_what_can_not_change_says_why(qtbot: QtBot) -> None:
    entries = [RemoteEntry("a", False, 1, 1.0, "0644", "anna", "web"), RemoteEntry("b", False, 2, 2.0, "0644", "bob", "web")]
    dialog = dialog_for(qtbot, entries, Abilities(owner=False, owner_why="Only root can.", rights=True))
    assert dialog.owner.currentText() == "" and not dialog.owner.isEnabled() and dialog.owner.toolTip() == "Only root can."
    line = dialog.owner.lineEdit()
    assert line is not None and line.placeholderText() == "(differs)"
    assert dialog.group.currentText() == "web"


def test_an_owner_or_group_for_everything_inside_leaves_the_rights_inside_alone(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("f", True, permissions="0755", group="web")], Abilities(rights=True, group=True, inside=True, groups=("web", "staff")))
    dialog.inside.setChecked(True)
    dialog.folders_x.setChecked(False)
    assert dialog.change().rights is None and not ok_of(dialog.buttons)  # nothing touched: nothing to do
    dialog.group.setCurrentText("staff")
    assert dialog.change().rights is None and dialog.change().group == "staff" and dialog.change().inside


def test_everything_inside_changes_only_the_touched_box(qtbot: QtBot) -> None:
    """Review round 4: switching off Others Read on a 2755 folder gave every file inside all twelve boxes of the folder."""
    dialog = dialog_for(qtbot, [RemoteEntry("f", True, permissions="2755")], Abilities(rights=True, inside=True))
    dialog.inside.setChecked(True)
    dialog.rights.boxes[6].setCheckState(Qt.CheckState.Unchecked)
    touched = dialog.change().rights
    assert touched == tuple(False if place == 6 else None for place in range(12))
    assert dialog.change().folder_readers == ""  # no Read switched on: no folder gains Run


def test_folders_get_run_only_for_read_switched_on_and_inside_alone_changes_nothing(qtbot: QtBot) -> None:
    """Review round 6: a tick on "inside" alone gave a private folder's folders Run for every class that read."""
    dialog = dialog_for(qtbot, [RemoteEntry("f", True, permissions="0704")], Abilities(rights=True, inside=True))
    assert not dialog.folders_x.isChecked()  # off at first
    dialog.inside.setChecked(True)
    assert not ok_of(dialog.buttons)  # inside alone changes nothing
    dialog.folders_x.setChecked(True)
    assert not ok_of(dialog.buttons) and dialog.change().folder_readers == ""
    dialog.rights.boxes[3].setCheckState(Qt.CheckState.Checked)  # group Read on
    assert ok_of(dialog.buttons) and dialog.change().folder_readers == "g"  # not the owner's or others' Read, which were on already


def test_enter_in_a_name_or_the_octal_field_moves_on_and_applies_nothing(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("a", False, permissions="0644", group="web")], Abilities(rights=True, group=True, groups=("web", "staff")))
    applied: list[bool] = []
    dialog.accepted.connect(lambda: applied.append(True))
    with qtbot.waitExposed(dialog):
        dialog.show()
    line = dialog.group.lineEdit()
    assert line is not None
    line.setFocus()
    line.selectAll()
    QTest.keyClicks(line, "staff")
    QTest.keyClick(line, Qt.Key.Key_Return)
    dialog.rights.octal.setFocus()
    QTest.keyClick(dialog.rights.octal, Qt.Key.Key_Enter)
    assert applied == [] and dialog.isVisible() and dialog.group.currentText() == "staff"
    dialog.reject()


def test_on_ftp_everything_inside_is_not_shown(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("f", True, permissions="0755")], Abilities(rights=True, inside_shown=False))
    dialog.show()
    assert not dialog.inside.isVisible()


# --- Properties on this computer -----------------------------------------------


def test_properties_here_change_only_the_touched_box_of_several_files(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, mode in (("a", 0o664), ("b", 0o644)):
        (tmp_path / name).write_text(name, encoding="utf-8")
        (tmp_path / name).chmod(mode)
    pick_local(window, tmp_path, "a", "b")

    def others_may_not_read(dialog: PropertiesDialog) -> int:
        assert dialog.rights.boxes[4].checkState() == Qt.CheckState.PartiallyChecked
        dialog.rights.boxes[6].setCheckState(Qt.CheckState.Unchecked)
        return PropertiesDialog.DialogCode.Accepted.value

    monkeypatch.setattr(PropertiesDialog, "exec", others_may_not_read)
    window.actions_by_name["local_properties"].trigger()
    qtbot.waitUntil(lambda: stat.S_IMODE((tmp_path / "b").stat().st_mode) == 0o640)
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o660


def test_everything_inside_here_changes_only_the_touched_box(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "shared"
    folder.mkdir()
    (folder / "f.txt").write_text("f", encoding="utf-8")
    (folder / "f.txt").chmod(0o644)
    folder.chmod(0o3755)
    pick_local(window, tmp_path, "shared")

    def others_may_not_read(dialog: PropertiesDialog) -> int:
        dialog.inside.setChecked(True)
        dialog.rights.boxes[6].setCheckState(Qt.CheckState.Unchecked)
        return PropertiesDialog.DialogCode.Accepted.value

    monkeypatch.setattr(PropertiesDialog, "exec", others_may_not_read)
    monkeypatch.setattr(QMessageBox, "question", giving(QMessageBox.StandardButton.Yes))
    window.actions_by_name["local_properties"].trigger()
    qtbot.waitUntil(lambda: stat.S_IMODE((folder / "f.txt").stat().st_mode) == 0o640)
    qtbot.waitUntil(lambda: stat.S_IMODE(folder.stat().st_mode) == 0o3751)  # set group and sticky kept, Run for others kept


def test_this_computers_users_and_groups_are_read_away_from_the_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    pick_local(window, tmp_path, "a")
    threads: list[bool] = []
    real_users = localfiles.all_users

    def users() -> list[str]:
        threads.append(threading.current_thread() is threading.main_thread())
        return real_users()

    monkeypatch.setattr(localfiles, "all_users", users)
    monkeypatch.setattr(PropertiesDialog, "exec", giving(PropertiesDialog.DialogCode.Rejected.value))
    window.actions_by_name["local_properties"].trigger()
    qtbot.waitUntil(lambda: bool(threads))
    assert threads == [False]


def test_f9_opens_properties_on_the_side_with_the_focus(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    with qtbot.waitExposed(window):
        window.show()
    pick_local(window, tmp_path, "a")
    opened: list[str | None] = []

    def seen(dialog: PropertiesDialog) -> int:
        opened.append(dialog.summary.name)
        return PropertiesDialog.DialogCode.Rejected.value

    monkeypatch.setattr(PropertiesDialog, "exec", seen)
    window.local_side.file_list.setFocus()
    QTest.keyClick(window.local_side.file_list, Qt.Key.Key_F9)
    qtbot.waitUntil(lambda: opened == ["a"])  # what the files are is read on a thread of its own
    assert {"F9", "Shift+F4", "Shift+F5", "Alt+F6", "Ctrl+Alt+E"} <= set(PROGRAM_KEYS)  # no bookmark can take them


# --- new files, folders and links here (FM-05, FM-07, FM-08) -------------------


def test_shift_f4_here_makes_an_empty_file_and_opens_it(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window.local_side.go_to(str(tmp_path))
    opened: list[str] = []
    monkeypatch.setattr(window, "_ask_text", giving("notes.txt"))
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    window.actions_by_name["local_new_file"].trigger()
    assert (tmp_path / "notes.txt").read_bytes() == b"" and opened == [str(tmp_path / "notes.txt")]


def test_a_new_folder_here_can_get_its_rights(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    window.local_side.go_to(str(tmp_path))

    def private(dialog: NewFolderDialog) -> int:
        dialog.name.setText("mine")
        dialog.set_rights.setChecked(True)
        dialog.rights.set_rights(rights_of(0o700))
        return NewFolderDialog.DialogCode.Accepted.value

    monkeypatch.setattr(NewFolderDialog, "exec", private)
    window.actions_by_name["local_new"].trigger()
    assert stat.S_IMODE((tmp_path / "mine").stat().st_mode) == 0o700


def test_a_link_here_is_made_and_led_elsewhere(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    pick_local(window, tmp_path, "a.txt")

    def named(dialog: LinkDialog) -> int:
        assert dialog.target.text() == "a.txt"  # the selected file is what it leads to
        dialog.name.setText("to-a")
        return LinkDialog.DialogCode.Accepted.value

    monkeypatch.setattr(LinkDialog, "exec", named)
    window.actions_by_name["local_link"].trigger()
    assert os.readlink(tmp_path / "to-a") == "a.txt"
    pick_local(window, tmp_path, "to-a")

    def elsewhere(dialog: LinkDialog) -> int:
        assert dialog.name.isReadOnly() and not ok_of(dialog.buttons)
        dialog.target.setText("b.txt")
        return LinkDialog.DialogCode.Accepted.value

    monkeypatch.setattr(LinkDialog, "exec", elsewhere)
    window.actions_by_name["local_link"].trigger()
    assert os.readlink(tmp_path / "to-a") == "b.txt"


# --- the file manager and the terminal (FM-11, FM-12) ---------------------------


def test_the_folder_on_the_left_opens_in_the_file_manager(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(localfiles, "file_manager_available", lambda: True)
    monkeypatch.setattr(windowproperties.subprocess, "Popen", noting(started))
    window.local_side.go_to(str(tmp_path))
    window.update_actions()
    window.actions_by_name["local_explore"].trigger()
    assert started == [["xdg-open", str(tmp_path)]]


def test_without_xdg_open_the_file_manager_is_greyed_with_its_reason(qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(localfiles, "file_manager_available", lambda: False)
    window.update_actions()
    action = window.actions_by_name["local_explore"]
    assert not action.isEnabled() and "xdg-open" in action.toolTip()


def test_the_terminal_opens_in_the_folder_on_the_screen(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "inner").mkdir()
    started: list[list[str]] = []
    monkeypatch.setattr(windowserver.subprocess, "Popen", noting(started))
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: window.remote_side.folder == "/inner")
        window.open_terminal()
    assert started and started[0][-1].startswith("cd /inner ")


# --- the server (FM-01, FM-05 to FM-10) -----------------------------------------


def test_properties_without_a_shell_change_rights_through_the_protocol_file_by_file(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, mode in (("a", 0o664), ("b", 0o644)):
        (tmp_path / name).write_text(name, encoding="utf-8")
        (tmp_path / name).chmod(mode)
    seen: list[tuple[bool, bool, bool]] = []

    def others_may_not_read(dialog: PropertiesDialog) -> int:
        seen.append((dialog.rights.isEnabled(), dialog.group.isEnabled(), dialog.inside.isVisible()))
        dialog.rights.boxes[6].setCheckState(Qt.CheckState.Unchecked)
        return PropertiesDialog.DialogCode.Accepted.value

    monkeypatch.setattr(PropertiesDialog, "exec", others_may_not_read)
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "b" in names_on_the_right(window))
        select(window, "a", "b")
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: stat.S_IMODE((tmp_path / "b").stat().st_mode) == 0o640)
        assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o660
    assert seen == [(True, False, False)]  # rights through SFTP; the group needs a shell


def test_duplicate_and_copy_to_without_a_shell_copy_on_the_server_itself(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("content", encoding="utf-8")
    (tmp_path / "b.txt").write_text("other", encoding="utf-8")
    (tmp_path / "keep").mkdir()

    def into_keep(dialog: CopyToDialog) -> int:
        dialog.folder.setText("/keep")
        dialog.pattern.setText("*.bak")
        return CopyToDialog.DialogCode.Accepted.value

    monkeypatch.setattr(window, "_ask_text", giving("a-copy.txt"))
    monkeypatch.setattr(CopyToDialog, "exec", into_keep)
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["duplicate"].trigger()
        qtbot.waitUntil(lambda: "a-copy.txt" in names_on_the_right(window))
        assert (tmp_path / "a-copy.txt").read_text(encoding="utf-8") == "content"
        select(window, "a.txt", "b.txt")
        window.actions_by_name["copy_to"].trigger()
        qtbot.waitUntil(lambda: (tmp_path / "keep" / "b.txt.bak").exists())
        assert (tmp_path / "keep" / "a.txt.bak").read_text(encoding="utf-8") == "content"
        select(window, "keep")
        assert not window.actions_by_name["duplicate"].isEnabled()  # a folder needs a shell: greyed with its reason
        assert "folders" in window.actions_by_name["duplicate"].toolTip()


def test_copy_to_goes_on_after_a_refused_item_and_reads_a_folder_from_home(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review round 2: one refusal stopped the rest and named only itself; ~/keep was read from the folder shown."""
    (tmp_path / "inner").mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / "inner" / name).write_text(name, encoding="utf-8")
    (tmp_path / "keep").mkdir()
    (tmp_path / "keep" / "b.txt.bak").write_text("there before", encoding="utf-8")
    told: list[str] = []

    def into_keep(dialog: CopyToDialog) -> int:
        dialog.folder.setText("~/keep")
        dialog.pattern.setText("*.bak")
        return CopyToDialog.DialogCode.Accepted.value

    monkeypatch.setattr(CopyToDialog, "exec", into_keep)
    monkeypatch.setattr(QMessageBox, "information", answered(told))
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.open_remote_folder("/inner")
        qtbot.waitUntil(lambda: "c.txt" in names_on_the_right(window))
        select(window, "a.txt", "b.txt", "c.txt")
        window.actions_by_name["copy_to"].trigger()
        qtbot.waitUntil(lambda: bool(told))
    assert (tmp_path / "keep" / "a.txt.bak").exists() and (tmp_path / "keep" / "c.txt.bak").exists()
    assert (tmp_path / "keep" / "b.txt.bak").read_text(encoding="utf-8") == "there before"
    assert "a.txt" in told[0] and "c.txt" in told[0] and "b.txt:" in told[0]  # what went, and what did not and why


def lose(window: MainWindow) -> None:
    """The connection drops while a window is open: the tab is marked lost, as the check of connections does."""
    window.sessions[0].alive = False


@pytest.mark.parametrize("action", ["new_folder", "duplicate", "properties", "link", "new_file", "copy_to", "password"])
def test_a_connection_lost_while_a_window_is_open_says_nothing_was_changed(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    told: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", answered(told))

    def accepted_after_the_drop(dialog: object) -> int:
        lose(window)
        if isinstance(dialog, NewFolderDialog):
            dialog.name.setText("made")
        elif isinstance(dialog, LinkDialog):
            dialog.name.setText("to-a")
        elif isinstance(dialog, PasswordDialog):
            dialog.old.setText(PASSWORD)
            dialog.new.setText("a-new-secret")
            dialog.again.setText("a-new-secret")
        elif isinstance(dialog, PropertiesDialog):
            dialog.rights.set_rights(rights_of(0o600))
        return 1

    def typed_after_the_drop(*_args: object) -> str:
        lose(window)
        return "b.txt"

    for kind in (NewFolderDialog, LinkDialog, PasswordDialog, PropertiesDialog, CopyToDialog):
        monkeypatch.setattr(kind, "exec", accepted_after_the_drop)
    monkeypatch.setattr(window, "_ask_text", typed_after_the_drop)
    with ssh_server(root, commands=Passwd(PASSWORD) if action == "password" else None, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        if action != "password":
            qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
            select(window, "a.txt")
        if action == "password":
            window.change_password()
        else:
            window.actions_by_name[action].trigger()
        qtbot.waitUntil(lambda: bool(told))
    assert "nothing was changed" in told[0]
    assert sorted(path.name for path in root.iterdir()) == ["a.txt"]


def test_a_second_f9_while_the_names_are_read_opens_no_second_window(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    opened: list[str] = []

    def seen(dialog: PropertiesDialog) -> int:
        opened.append(str(dialog.summary.name))
        return PropertiesDialog.DialogCode.Rejected.value

    monkeypatch.setattr(PropertiesDialog, "exec", seen)
    with ssh_server(tmp_path) as server:  # a shell: the names are read from the server first
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["properties"].trigger()
        assert "users and groups" in window.status.currentMessage()
        window.actions_by_name["properties"].trigger()  # pressed again while the first is prepared
        qtbot.waitUntil(lambda: len(opened) == 1)
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: len(opened) == 2)
        flushed: list[bool] = []
        window.sessions[0].worker.submit(lambda: True, flushed.append, lambda _error: None)  # after everything asked before it
        qtbot.waitUntil(lambda: bool(flushed))
    assert len(opened) == 2


def test_ok_waits_while_the_octal_field_holds_no_complete_number(qtbot: QtBot) -> None:
    dialog = dialog_for(qtbot, [RemoteEntry("a", False, permissions="0644")], Abilities(rights=True))
    QTest.keyClick(dialog.rights.octal, Qt.Key.Key_End)
    QTest.keyClick(dialog.rights.octal, Qt.Key.Key_Backspace)
    QTest.keyClicks(dialog.rights.octal, "0")  # 0640
    assert ok_of(dialog.buttons)
    QTest.keyClick(dialog.rights.octal, Qt.Key.Key_Backspace)
    QTest.keyClick(dialog.rights.octal, Qt.Key.Key_Backspace)  # 06: no rights yet
    assert not ok_of(dialog.buttons) and "not a complete" in dialog.rights.octal.toolTip()
    QTest.keyClicks(dialog.rights.octal, "00")
    assert ok_of(dialog.buttons) and dialog.rights.value() == rights_of(0o600)  # the boxes follow once it is whole again
    folder = NewFolderDialog(True, "")
    qtbot.addWidget(folder)
    folder.name.setText("x")
    folder.set_rights.setChecked(True)
    folder.rights.octal.clear()
    QTest.keyClicks(folder.rights.octal, "7")
    assert not ok_of(folder.buttons)


def test_a_new_file_whose_temporary_copy_can_not_be_made_is_told_calmly(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    told: list[str] = []
    monkeypatch.setattr(window, "_ask_text", giving("new.txt"))
    monkeypatch.setattr(QMessageBox, "information", answered(told))

    def full(*_args: object, **_options: object) -> str:
        raise OSError(28, "No space left on device")

    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        monkeypatch.setattr(windowproperties.tempfile, "mkdtemp", full)
        window.actions_by_name["new_file"].trigger()
        qtbot.waitUntil(lambda: bool(told))
    assert "No space left" in told[0] and not (tmp_path / "new.txt").exists()


def test_rights_without_a_shell_go_on_after_a_refusal_and_say_what_kept_its_rights(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("a", "b"):
        (tmp_path / name).write_text(name, encoding="utf-8")
        (tmp_path / name).chmod(0o644)
    told: list[str] = []
    real = SshConnection.set_rights

    def refuses_a(self: SshConnection, path: str, mode: str) -> None:
        if path == "/a":
            raise RemoteActionFailed("Permission denied")
        real(self, path, mode)

    def others_may_not_read(dialog: PropertiesDialog) -> int:
        dialog.rights.boxes[6].setCheckState(Qt.CheckState.Unchecked)
        return PropertiesDialog.DialogCode.Accepted.value

    monkeypatch.setattr(SshConnection, "set_rights", refuses_a)
    monkeypatch.setattr(PropertiesDialog, "exec", others_may_not_read)
    monkeypatch.setattr(QMessageBox, "information", answered(told))
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "b" in names_on_the_right(window))
        select(window, "a", "b")
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: bool(told))
    assert stat.S_IMODE((tmp_path / "b").stat().st_mode) == 0o640
    assert "a: Permission denied" in told[0]


def test_names_that_come_too_slowly_fall_back_to_the_listing_with_a_note(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")

    async def slow_directory(process: asyncssh.SSHServerProcess[str]) -> None:
        if (process.command or "").startswith("echo chrono-ftp-probe"):
            process.stdout.write("chrono-ftp-probe\nLinux\n1000\ntool:chown\ntool:chmod\n")
            process.exit(0)
            return
        await asyncio.sleep(30)  # getent of a directory service that does not answer; ended by the end of the server
        process.exit(0)

    seen: list[bool] = []

    def looked(dialog: PropertiesDialog) -> int:
        seen.append(dialog.abilities.names_known)
        return PropertiesDialog.DialogCode.Rejected.value

    monkeypatch.setattr(properties, "NAMES_SECONDS", 0.5)
    monkeypatch.setattr(PropertiesDialog, "exec", looked)
    with ssh_server(tmp_path, commands=slow_directory) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a" in names_on_the_right(window))
        select(window, "a")
        window.actions_by_name["properties"].trigger()
        qtbot.waitUntil(lambda: bool(seen))
        assert seen == [False] and "names seen in this folder" in window.status.currentMessage()
        assert window.sessions[0].alive  # the connection goes on


def test_texts_of_a_copy_to_are_made_on_the_windows_thread(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "a.txt.bak").write_text("there before", encoding="utf-8")
    threads: list[bool] = []
    real_fitting = windowproperties.fitting

    def noted(text: str, widget: QWidget, share: float = NAME_SHARE) -> str:
        threads.append(threading.current_thread() is threading.main_thread())
        return real_fitting(text, widget, share)

    def as_bak(dialog: CopyToDialog) -> int:
        dialog.pattern.setText("*.bak")
        return CopyToDialog.DialogCode.Accepted.value

    told: list[str] = []
    monkeypatch.setattr(windowproperties, "fitting", noted)
    monkeypatch.setattr(CopyToDialog, "exec", as_bak)
    monkeypatch.setattr(QMessageBox, "information", answered(told))
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        window.actions_by_name["copy_to"].trigger()
        qtbot.waitUntil(lambda: bool(told))
    assert threads and all(threads)


def test_a_new_file_on_the_server_is_made_and_opened_in_the_editor(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(window, "_ask_text", giving("new.txt"))
    monkeypatch.setattr(window, "_open_local_file", opened.append)
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.actions_by_name["new_file"].trigger()
        qtbot.waitUntil(lambda: bool(opened))
        assert (tmp_path / "new.txt").read_bytes() == b""
        assert Path(opened[0]).name == "new.txt" and len(window.edited) == 1  # every save goes back, as with F4


def test_a_new_folder_on_the_server_gets_its_rights(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def private(dialog: NewFolderDialog) -> int:
        dialog.name.setText("mine")
        dialog.set_rights.setChecked(True)
        dialog.rights.set_rights(rights_of(0o700))
        return NewFolderDialog.DialogCode.Accepted.value

    monkeypatch.setattr(NewFolderDialog, "exec", private)
    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.actions_by_name["new_folder"].trigger()
        qtbot.waitUntil(lambda: "mine" in names_on_the_right(window))
    assert stat.S_IMODE((tmp_path / "mine").stat().st_mode) == 0o700


def test_a_link_on_the_server_is_made_and_led_elsewhere(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    def named(dialog: LinkDialog) -> int:
        dialog.name.setText("to-a")
        return LinkDialog.DialogCode.Accepted.value

    def elsewhere(dialog: LinkDialog) -> int:
        dialog.target.setText("b.txt")
        return LinkDialog.DialogCode.Accepted.value

    with ssh_server(tmp_path, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        monkeypatch.setattr(LinkDialog, "exec", named)
        window.actions_by_name["link"].trigger()
        qtbot.waitUntil(lambda: "to-a" in names_on_the_right(window))
        assert os.readlink(tmp_path / "to-a") == "a.txt"
        select(window, "to-a")
        monkeypatch.setattr(LinkDialog, "exec", elsewhere)
        window.actions_by_name["link"].trigger()
        qtbot.waitUntil(lambda: os.readlink(tmp_path / "to-a") == "b.txt")


def test_links_and_server_copies_on_ftp_are_greyed_with_their_reason(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    with ftp_server(tmp_path, copies=True) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        qtbot.waitUntil(lambda: "a.txt" in names_on_the_right(window))
        select(window, "a.txt")
        assert window.actions_by_name["duplicate"].isEnabled()  # this FTP server copies with SITE CPFR and CPTO
        link = window.actions_by_name["link"]
        assert link.isVisible() and not link.isEnabled() and "FTP" in link.toolTip()


def public_key(folder: Path) -> Path:
    path = folder / "id_test.pub"
    path.write_bytes(asyncssh.generate_private_key("ssh-ed25519", comment="anna@laptop").export_public_key("openssh"))
    return path


def test_my_public_key_is_installed_from_the_menu_of_the_tab(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    key = public_key(tmp_path)
    told: list[str] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", giving((str(key), "")))
    monkeypatch.setattr(QMessageBox, "information", answered(told))
    with ssh_server(root, shell=False) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        menu = window.tab_menu_for(window.tab_bar.tabRect(0).center())
        assert menu is not None
        entries = {action.text(): action for action in menu.actions()}
        assert entries[KEY_TEXT].isEnabled()
        assert not entries[PASSWORD_TEXT].isEnabled() and entries[PASSWORD_TEXT].toolTip()  # no shell: greyed with its reason
        entries[KEY_TEXT].trigger()
        qtbot.waitUntil(lambda: bool(told))
    assert key.read_text(encoding="utf-8").split()[1] in (root / ".ssh" / "authorized_keys").read_text(encoding="utf-8")
    assert "anna@laptop" in told[0] and "Made the folder .ssh" in told[0]


def test_the_password_is_changed_on_the_server_and_kept_in_the_store(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    passwd = Passwd(PASSWORD)
    told: list[str] = []

    def typed(dialog: PasswordDialog) -> int:
        dialog.old.setText(PASSWORD)
        dialog.new.setText("a-new-secret")
        assert not ok_of(dialog.buttons)  # not repeated yet
        dialog.again.setText("a-new-secret")
        assert ok_of(dialog.buttons)
        return PasswordDialog.DialogCode.Accepted.value

    monkeypatch.setattr(PasswordDialog, "exec", typed)
    monkeypatch.setattr(QMessageBox, "information", answered(told))
    with ssh_server(tmp_path, commands=passwd) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        menu = window.tab_menu_for(window.tab_bar.tabRect(0).center())
        assert menu is not None
        entries = {action.text(): action for action in menu.actions()}
        entries[PASSWORD_TEXT].trigger()
        qtbot.waitUntil(lambda: bool(told))
    assert passwd.password == "a-new-secret"
    assert memory_store.passwords["Shop"] == "a-new-secret"  # never asked for again (OR-24)
    assert "was changed" in told[0] and "a-new-secret" not in told[0]


def test_a_refused_password_is_told_calmly_and_the_stored_one_stays(qtbot: QtBot, window: MainWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> None:
    passwd = Passwd(PASSWORD, refuse=True)
    told: list[str] = []

    def typed(dialog: PasswordDialog) -> int:
        dialog.old.setText(PASSWORD)
        dialog.new.setText("short")
        dialog.again.setText("short")
        return PasswordDialog.DialogCode.Accepted.value

    monkeypatch.setattr(PasswordDialog, "exec", typed)
    monkeypatch.setattr(QMessageBox, "information", answered(told))
    with ssh_server(tmp_path, commands=passwd) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        window.change_password()
        qtbot.waitUntil(lambda: bool(told))
    assert memory_store.passwords["Shop"] == PASSWORD
    assert "old one still works" in told[0] and "shorter than 8" in told[0]


def test_change_password_is_not_in_the_menu_of_an_ftp_tab(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connected(qtbot, window, Connection(name="Shop", host="127.0.0.1", port=port, user=USER, protocol=FTP, start_folder="/"))
        menu = window.tab_menu_for(window.tab_bar.tabRect(0).center())
        assert menu is not None
        assert PASSWORD_TEXT not in [action.text() for action in menu.actions()]


def test_as_root_the_current_password_is_not_needed(qtbot: QtBot) -> None:
    dialog = PasswordDialog("root@host", as_root=True)
    qtbot.addWidget(dialog)
    assert not dialog.old.isEnabled() and "Not needed" in dialog.old.toolTip()
    dialog.new.setText("a-new-secret")
    dialog.again.setText("a-new-secret")
    assert ok_of(dialog.buttons) and dialog.chosen() == ("", "a-new-secret")


def test_every_window_of_block_fm_starts_with_ok_greyed(qtbot: QtBot, qapp: QApplication) -> None:
    for dialog in (LinkDialog("", "", False, True, ""), CopyToDialog(1, ""), NewFolderDialog(True, ""), PasswordDialog("anna@host")):
        qtbot.addWidget(dialog)
        assert not ok_of(dialog.buttons), type(dialog).__name__
