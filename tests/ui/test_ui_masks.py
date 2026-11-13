"""Block FD in the window: the mask field (FD-01), the transfer settings of masks, text and names (FD-01 to FD-03), presets in Settings and
in the transfer window (FD-04), selecting by mask with Num+ and Num− (FD-05), and the filter window with its mark next to the path (FD-06)."""

from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QInputDialog
from pytestqt.qtbot import QtBot

from chrono_ftp.core.presets import STANDARD_NAME, Preset, PresetRules
from chrono_ftp.core.transferoptions import AUTOMATIC, BINARY, CASE_LOWER, RESUME_ON, TEXT, TransferOptions
from chrono_ftp.transfers.options import TEXT_NO_APPEND, TEXT_WHOLE_TEXT, Conflict
from chrono_ftp.transfers.queue import State
from chrono_ftp.ui.maskdialog import FILTER, SELECT, MaskDialog
from chrono_ftp.ui.maskfield import MaskField
from chrono_ftp.ui.mainwindow import MainWindow
from chrono_ftp.ui.overwritedialog import NO_TEXT_APPEND_TEXT, OverwriteDialog
from chrono_ftp.ui.presetseditor import PresetsEditor
from chrono_ftp.ui.transferdialog import TransferDialog
from chrono_ftp.ui.transferoptionsform import TransferOptionsForm, summary_text
from tests.fakeservers import ssh_server
from tests.ui.test_ui_selection import chosen, files_in, focused, local_window, shown, with_settings
from tests.ui.test_ui_sessions import connected, ssh_settings, trust_server, window
from tests.ui.test_ui_transfers import settled, upload_one

# --- Version ------------------------------------------------------------
__version__ = "test_ui_masks.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: block FD in the window (FD-01 to FD-06); review: a greyed Num− never starts finding a name and says why; a mask
#          with only an excluded part selects every file but that; a fault in the text mask holds OK back only in the automatic mode;
#          typing in a mask field is never completed from the history; with Folders too what is left out leaves folders out as well

__all__ = ["window"]  # the fixture, used by name


def answered_with(mask: str, folders_too: bool = False, clear: bool = False) -> Callable[[QDialog], int]:
    """A stand-in for exec of a mask window: types the mask and presses its button (or Clear)."""

    def answer(dialog: QDialog) -> int:
        assert isinstance(dialog, MaskDialog)
        if clear:
            dialog._clear()
            return QDialog.DialogCode.Accepted.value
        dialog.field.set_text(mask)
        dialog.folders.setChecked(folders_too)
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok is not None and ok.isEnabled()
        return QDialog.DialogCode.Accepted.value

    return answer


# --- the mask field (FD-01) -------------------------------------------------------------------------------------------------------------


def test_the_mask_field_says_a_fault_at_once_and_keeps_its_history(qtbot: QtBot) -> None:
    field = MaskField(("*.txt", "*.jpg"))
    qtbot.addWidget(field)
    assert [field.combo.itemText(row) for row in range(field.combo.count())] == ["*.txt", "*.jpg"]
    field.set_text("*>1Q")
    assert not field.acceptable() and "“>1Q”" in field.problem.text() and not field.problem.isHidden()
    field.show_fault()
    line = field.combo.lineEdit()
    assert line is not None and line.selectedText() == ">1Q"  # the fault is selected, to be typed over
    field.set_text("*.txt | old*")
    assert field.acceptable() and field.problem.isHidden()


def test_a_mask_window_keeps_its_button_greyed_while_the_mask_has_a_fault(qtbot: QtBot) -> None:
    dialog = MaskDialog(SELECT, "", ())
    qtbot.addWidget(dialog)
    ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None and ok.text() == "Select" and not ok.isEnabled() and ok.toolTip()  # nothing typed yet
    dialog.field.set_text("*>")
    assert not ok.isEnabled() and "can't be read" in ok.toolTip()
    dialog.field.set_text("*.txt")
    assert ok.isEnabled()
    filtering = MaskDialog(FILTER, "", ())
    qtbot.addWidget(filtering)
    ok = filtering.buttons.button(QDialogButtonBox.StandardButton.Ok)
    assert ok is not None and ok.isEnabled() and filtering.clear_button is not None  # an empty filter shows every file


# --- the transfer settings (FD-01 to FD-03) ---------------------------------------------------------------------------------------------


def test_the_form_keeps_masks_text_and_names(qtbot: QtBot) -> None:
    form = TransferOptionsForm()
    qtbot.addWidget(form)
    options = TransferOptions(file_mask="*.txt", skip_hidden=True, skip_empty_folders=True, transfer_mode=AUTOMATIC, text_mask="*.md", remove_bom=True, name_case=CASE_LOWER, replace_characters=True)
    form.set_options(options)
    assert form.chosen() == options
    assert "Mask *.txt" in summary_text(options, True) and "Text by mask" in summary_text(options, True)


def test_text_mode_greys_resume_and_splitting_with_the_reason(qtbot: QtBot) -> None:
    form = TransferOptionsForm()
    qtbot.addWidget(form)
    form.set_options(TransferOptions(resume=RESUME_ON))
    assert form.resume.isEnabled() and not form.text_mask.isEnabled() and not form.remove_bom.isEnabled()
    form.mode.setCurrentIndex(form.mode.findData(TEXT))
    assert not form.resume.isEnabled() and form.resume.toolTip() == TEXT_WHOLE_TEXT
    assert not form.split_large.isEnabled() and form.split_large.toolTip() == TEXT_WHOLE_TEXT
    assert form.remove_bom.isEnabled() and form.chosen().resume == RESUME_ON  # binary files of another transfer still continue
    form.mode.setCurrentIndex(form.mode.findData(AUTOMATIC))
    assert form.resume.isEnabled() and form.text_mask.isEnabled()
    form.mode.setCurrentIndex(form.mode.findData(BINARY))
    assert not form.remove_bom.isEnabled() and form.remove_bom.toolTip()


def test_a_mask_with_a_fault_keeps_the_settings_from_being_taken(qtbot: QtBot) -> None:
    form = TransferOptionsForm()
    qtbot.addWidget(form)
    form.set_options(TransferOptions(file_mask="*.txt"))
    form.file_mask.set_text("*>")
    assert not form.acceptable() and form.problem_text()
    assert form.chosen().file_mask == "*.txt"  # never a mask that can't be read


def test_append_is_greyed_for_a_text_with_its_reason(qtbot: QtBot) -> None:
    conflict = Conflict("a.txt", "/", True, 3, 1.0, 4, 1.0, None, TEXT_NO_APPEND)
    dialog = OverwriteDialog(conflict)
    qtbot.addWidget(dialog)
    append = next(button for choice, button in dialog.buttons.items() if choice.value == "append")
    assert not append.isEnabled() and append.toolTip() == NO_TEXT_APPEND_TEXT


# --- presets (FD-04) --------------------------------------------------------------------------------------------------------------------


def test_presets_are_made_renamed_duplicated_and_deleted(qtbot: QtBot, monkeypatch: pytest.MonkeyPatch) -> None:
    typed = ["Web", "Site"]

    def get_text(*_args: object, **_kwargs: object) -> tuple[str, bool]:
        return (typed.pop(0), True)

    monkeypatch.setattr(QInputDialog, "getText", get_text)
    editor = PresetsEditor((), lambda: TransferOptions(only_newer=True))
    qtbot.addWidget(editor)
    assert not editor.rename_button.isEnabled() and editor.rename_button.toolTip()
    editor.new_preset()
    assert [preset.name for preset in editor.chosen()] == ["Web"] and editor.chosen()[0].options.only_newer  # starts from the standard
    editor.host.setText("*.example.com")
    editor.form.mode.setCurrentIndex(editor.form.mode.findData(TEXT))
    editor.rename_preset()
    editor.duplicate_preset()
    presets = editor.chosen()
    assert [preset.name for preset in presets] == ["Site", "Site (copy)"]
    assert presets[1].rules == PresetRules(host="*.example.com") and presets[1].options.transfer_mode == TEXT
    editor.names.setCurrentRow(0)
    editor.delete_preset()
    assert [preset.name for preset in editor.chosen()] == ["Site (copy)"]


def test_the_transfer_window_takes_the_preset_given_and_another_can_be_chosen(qtbot: QtBot) -> None:
    web = Preset("Web", TransferOptions(transfer_mode=AUTOMATIC))
    standard = TransferOptions()
    dialog = TransferDialog(True, ["a.txt"], "/", web.options, {}, lambda start: None, None, True, False, (web,), "Web", standard, lambda options: (options, {}))
    qtbot.addWidget(dialog)
    assert dialog.preset.currentText() == "Web" and dialog.preset_name() == "Web" and "Text by mask" in dialog.summary.text()
    dialog.preset.setCurrentIndex(dialog.preset.findText(STANDARD_NAME))
    assert dialog.chosen_options() == standard and "Text by mask" not in dialog.summary.text()
    plain = TransferDialog(True, ["a.txt"], "/", standard, {}, lambda start: None)
    qtbot.addWidget(plain)
    assert plain.preset.isHidden()  # without presets nothing to choose


def test_an_upload_without_the_transfer_window_starts_with_the_preset_that_fits(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root, here = tmp_path / "server", tmp_path / "here"
    root.mkdir()
    here.mkdir()
    (here / "a.txt").write_bytes(b"a\r\n")
    with_settings(window, show_transfer_window=False, presets=(Preset("Text", TransferOptions(transfer_mode=TEXT), PresetRules(host="127.0.0.*")),))
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        upload_one(qtbot, window, here, "a.txt")
        qtbot.waitUntil(lambda: bool(window.queue.jobs) and window.queue.jobs[0].state == State.DONE)
        settled(qtbot, window)
    assert window.queue.jobs[0].options.transfer_mode == TEXT
    assert (root / "a.txt").read_bytes() == b"a\n"


# --- select by mask (FD-05) -------------------------------------------------------------------------------------------------------------


def test_num_plus_selects_by_mask_and_num_minus_unselects_on_the_list_with_the_focus(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = files_in(tmp_path, "a.txt", "b.txt", "c.jpg")
    (folder / "texts").mkdir()
    made = local_window(qtbot, folder)
    view = made.local_side.file_list
    qtbot.waitUntil(lambda: "c.jpg" in view.names())
    monkeypatch.setattr(MaskDialog, "exec", answered_with("*.txt"))
    QTest.keyClick(view, Qt.Key.Key_Plus, Qt.KeyboardModifier.KeypadModifier)
    assert chosen(view) == ["a.txt", "b.txt"]
    assert made.settings.mask_history[0] == "*.txt"
    monkeypatch.setattr(MaskDialog, "exec", answered_with("b*"))
    QTest.keyClick(view, Qt.Key.Key_Minus, Qt.KeyboardModifier.KeypadModifier)
    assert chosen(view) == ["a.txt"]
    monkeypatch.setattr(MaskDialog, "exec", answered_with("t*", folders_too=True))
    QTest.keyClick(view, Qt.Key.Key_Plus, Qt.KeyboardModifier.KeypadModifier)
    assert chosen(view) == ["texts", "a.txt"]
    assert made.remote_side.file_list.selected_entries() == []
    plus = made.list_actions["local_select_mask"]
    assert plus.shortcutContext() == Qt.ShortcutContext.WidgetShortcut  # the list with the focus only (OR-21)


def test_the_plus_of_the_main_keyboard_still_finds_a_name(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "+plus.txt", "other.txt"))
    view = made.local_side.file_list
    qtbot.waitUntil(lambda: "other.txt" in view.names())
    QTest.keyClick(view, Qt.Key.Key_Plus)
    assert view.typed == "+"  # only the keypad's plus selects by mask


# --- the filter (FD-06) -----------------------------------------------------------------------------------------------------------------


def test_the_filter_window_shows_only_what_fits_and_keeps_the_folders(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = files_in(tmp_path, "a.txt", "b.jpg", "old.txt")
    (folder / "sub").mkdir()
    made = local_window(qtbot, folder)
    side = made.local_side
    qtbot.waitUntil(lambda: "b.jpg" in side.file_list.names())
    monkeypatch.setattr(MaskDialog, "exec", answered_with("*.txt | old*"))
    side.filter_wanted.emit()
    assert sorted(side.file_list.names()) == ["..", "a.txt", "sub"]
    assert not side.filter_mark.isHidden() and "*.txt | old*" in side.filter_mark.button.text()
    assert side.status.filtered_button.text() == "2 filtered"
    assert made.settings.mask_history[0] == "*.txt | old*"
    asked: list[bool] = []
    side.filter_wanted.connect(lambda: asked.append(True))
    side.filter_mark.button.click()  # a click on the mark changes the filter
    assert asked
    side.filter_mark.clear_button.click()  # the button beside it clears it
    assert side.file_list.name_mask == "" and side.filter_mark.isHidden()
    assert "b.jpg" in side.file_list.names()


def test_clear_in_the_filter_window_shows_every_file(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made = local_window(qtbot, files_in(tmp_path, "a.txt", "b.jpg"))
    view = made.local_side.file_list
    view.set_name_mask("*.txt")
    monkeypatch.setattr(MaskDialog, "exec", answered_with("", clear=True))
    made.local_side.filter_wanted.emit()
    assert view.name_mask == "" and "b.jpg" in view.names()


def test_a_size_filter_and_a_bad_filter(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "small.txt"))
    (tmp_path / "big.txt").write_bytes(b"x" * 5000)
    view = made.local_side.file_list
    made.local_side.refresh()
    qtbot.waitUntil(lambda: "big.txt" in view.names())
    view.set_name_mask(">1K")
    assert "big.txt" in view.names() and "small.txt" not in view.names()
    view.set_name_mask("*>")  # never from the window; one that can't be read hides nothing (OR-15)
    assert "small.txt" in view.names()


def test_each_tab_keeps_the_filter_of_both_sides(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root) as server:
        trust_server(server)
        connected(qtbot, window, ssh_settings(server))
        connected(qtbot, window, ssh_settings(server, "Other"))
        window._set_filter(window.local_side.file_list, "*.png")
        window._set_filter(window.remote_side.file_list, "*.log")
        window.tab_bar.setCurrentIndex(0)
        qtbot.waitUntil(lambda: window.current() is window.sessions[0])
        assert window.local_side.file_list.name_mask == "" and window.remote_side.file_list.name_mask == ""
        window.tab_bar.setCurrentIndex(1)
        qtbot.waitUntil(lambda: window.current() is window.sessions[1])
        assert window.local_side.file_list.name_mask == "*.png" and window.remote_side.file_list.name_mask == "*.log"
        assert not window.local_side.filter_mark.isHidden()


def test_a_greyed_num_minus_does_nothing_and_says_why(qtbot: QtBot, tmp_path: Path) -> None:
    made = local_window(qtbot, files_in(tmp_path, "-dash.txt", "other.txt"))
    view = made.local_side.file_list
    qtbot.waitUntil(lambda: "other.txt" in view.names())
    made.update_actions()
    assert not made.list_actions["local_unselect_mask"].isEnabled()  # nothing selected
    QTest.keyClick(view, Qt.Key.Key_Minus, Qt.KeyboardModifier.KeypadModifier)
    assert view.typed == "" and chosen(view) == []
    assert made.status.currentMessage() == made.list_actions["local_unselect_mask"].toolTip() != ""


def test_a_mask_with_only_an_excluded_part_selects_every_file_but_that(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = files_in(tmp_path, "a.txt", "b.bak", "c.jpg")
    (folder / "sub").mkdir()
    made = local_window(qtbot, folder)
    view = made.local_side.file_list
    qtbot.waitUntil(lambda: "c.jpg" in view.names())
    monkeypatch.setattr(MaskDialog, "exec", answered_with("| *.bak"))
    QTest.keyClick(view, Qt.Key.Key_Plus, Qt.KeyboardModifier.KeypadModifier)
    assert chosen(view) == ["a.txt", "c.jpg"]  # as the filter shows the same mask; folders only with Folders too
    view.clear_marks()
    monkeypatch.setattr(MaskDialog, "exec", answered_with("| *.bak", folders_too=True))
    QTest.keyClick(view, Qt.Key.Key_Plus, Qt.KeyboardModifier.KeypadModifier)
    assert chosen(view) == ["sub", "a.txt", "c.jpg"]


def test_a_fault_in_the_text_mask_holds_ok_back_only_while_it_is_in_use(qtbot: QtBot) -> None:
    form = TransferOptionsForm()
    qtbot.addWidget(form)
    form.set_options(TransferOptions(transfer_mode=AUTOMATIC))
    form.text_mask.set_text("*>")
    assert not form.acceptable()
    form.mode.setCurrentIndex(form.mode.findData(BINARY))
    assert form.acceptable() and form.problem_text() == ""


def test_typing_a_mask_is_never_completed_from_the_history(qtbot: QtBot) -> None:
    field = MaskField(("*.txt | old*",))
    qtbot.addWidget(field)
    shown(qtbot, field)
    line = field.combo.lineEdit()
    assert line is not None
    focused(qtbot, line)  # a completer acts only on a field with the focus
    QTest.keyClicks(line, "*.txt")
    QTest.keyClick(line, Qt.Key.Key_Return)
    assert field.text() == "*.txt"
    assert field.combo.itemText(0) == "*.txt | old*"  # the history is still in the drop-down


def test_with_folders_too_what_the_mask_leaves_out_leaves_folders_out_as_well(qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = files_in(tmp_path, "a.txt", "bake.txt")
    for name in ("bakery", "box"):
        (folder / name).mkdir()
    made = local_window(qtbot, folder)
    view = made.local_side.file_list
    qtbot.waitUntil(lambda: "box" in view.names())
    monkeypatch.setattr(MaskDialog, "exec", answered_with("| bak*", folders_too=True))
    QTest.keyClick(view, Qt.Key.Key_Plus, Qt.KeyboardModifier.KeypadModifier)
    assert chosen(view) == ["box", "a.txt"]
    view.clear_marks()
    monkeypatch.setattr(MaskDialog, "exec", answered_with("b* | bak*", folders_too=True))
    QTest.keyClick(view, Qt.Key.Key_Plus, Qt.KeyboardModifier.KeypadModifier)
    assert chosen(view) == ["box"]
