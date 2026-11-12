"""The right-click menus, the filter, the settings and the remembered sizes of the main window."""

from dataclasses import replace
from collections.abc import Callable
from typing import override

from PySide6.QtCore import QByteArray, QPoint
from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.ui.filelist import FileList
from chrono_ftp.ui.menus import open_menu
from chrono_ftp.ui.queueview import LOWEST_HEIGHT
from chrono_ftp.ui.settingsdialog import SettingsDialog
from chrono_ftp.ui.windowbase import CONNECTION_LIST_PARTS, CONNECTION_LIST_SIZE, HIGHEST_QUEUE_HEIGHT, LOCAL_MENU, REMOTE_MENU, SETTINGS_NOT_KEPT_TEXT, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowlayout.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: The settings also apply the session log and the automatic refresh (FP-01, FP-02); the settings window can open Clean Up
#          Stored Data, and window places removed there are not written back on closing (FP-04)
# v0.0.31: The settings window keeps the last connection, which it does not show (D-053)
# v0.0.29: The menus of the file lists open through open_menu, which lets go of the keyboard (D-052)
# v0.0.16: Also the splitter of the connections window
# v0.0.15: The size and place of the connections window are kept, also when it was not opened this time
# v0.0.13: The settings of the queue apply at once (FC-02, FC-03)
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


class LayoutPart(WindowBase):
    """The right-click menus, the filter, the settings and the remembered sizes of the main window."""

    @override
    def _apply_settings(self) -> None:
        for side in (self.local_side, self.remote_side):
            side.set_tree_place(self.settings.tree_place)
            side.set_show_hidden(self.settings.show_hidden)
        self._apply_queue_settings()
        self._apply_program_settings()

    def _sized_parts(self) -> dict[str, Callable[[], QByteArray]]:
        return {
            "window": self.saveGeometry,
            "sides": self.sides.saveState,
            "local_parts": self.local_side.parts.saveState,
            "remote_parts": self.remote_side.parts.saveState,
            "local_columns": self.local_side.file_list.header().saveState,
            "remote_columns": self.remote_side.file_list.header().saveState,
        }

    def _restore_sizes(self) -> None:
        restorers: dict[str, Callable[[QByteArray], bool]] = {
            "window": self.restoreGeometry,
            "sides": self.sides.restoreState,
            "local_parts": self.local_side.parts.restoreState,
            "remote_parts": self.remote_side.parts.restoreState,
            "local_columns": self.local_side.file_list.header().restoreState,
            "remote_columns": self.remote_side.file_list.header().restoreState,
        }
        state = saved_settings.load_window()
        self.saved_sizes = state
        for name, text in state.items():
            if name in restorers:
                restorers[name](QByteArray.fromBase64(text.encode("ascii", "replace")))  # a damaged value is refused by Qt and changes nothing
        height = state.get("queue_height", "")
        if height.isdecimal() and len(height) <= len(str(HIGHEST_QUEUE_HEIGHT)) and int(height) <= HIGHEST_QUEUE_HEIGHT:
            self.queue_height = max(int(height), LOWEST_HEIGHT)
        if state.get("queue_pinned") == "yes":
            self.queue_panel.pin.setChecked(True)
            self._show_queue()

    def _save_sizes(self) -> None:
        if self.sizes_forgotten:
            return  # removed in Clean Up Stored Data: nothing is written back by itself (FP-04)
        state = {name: bytes(read().toBase64().data()).decode("ascii") for name, read in self._sized_parts().items()}
        height = self.queue_panel.height() if self.queue_panel.isVisibleTo(self) else self.queue_height
        state["queue_height"] = str(height)
        state["queue_pinned"] = "yes" if self.queue_panel.pin.isChecked() else "no"
        if self.connection_list is not None:
            state[CONNECTION_LIST_SIZE] = bytes(self.connection_list.saveGeometry().toBase64().data()).decode("ascii")
            state[CONNECTION_LIST_PARTS] = bytes(self.connection_list.parts.saveState().toBase64().data()).decode("ascii")
        else:
            for name in (CONNECTION_LIST_SIZE, CONNECTION_LIST_PARTS):
                if name in self.saved_sizes:
                    state[name] = self.saved_sizes[name]  # not opened this time: what it had stays
        try:
            saved_settings.save_window(state)
        except OSError:
            pass  # the sizes are a convenience: closing never fails on them

    def menu_for(self, names: tuple[str, ...]) -> QMenu:
        menu = QMenu(self)
        for name in names:
            action = self.actions_by_name[name]
            if action.isVisible():
                menu.addAction(action)
        return menu

    def _local_menu_wanted(self, place: QPoint) -> None:
        open_menu(self.menu_for(LOCAL_MENU), self.local_side.file_list.mapToGlobal(place))

    def _remote_menu_wanted(self, place: QPoint) -> None:
        open_menu(self.menu_for(REMOTE_MENU), self.remote_side.file_list.mapToGlobal(place))

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self, self.clean_up_stored_data)
        accepted = dialog.exec() == SettingsDialog.DialogCode.Accepted.value
        dialog.deleteLater()
        if accepted:
            self.settings = replace(dialog.chosen(), last_connection=self.settings.last_connection)  # not in the settings window
            self._apply_settings()
            try:
                saved_settings.save(self.settings)
            except OSError:
                self.status.showMessage(SETTINGS_NOT_KEPT_TEXT)

    def _ask_mask(self, view: FileList) -> None:
        mask, given = QInputDialog.getText(self, "Filter", "Show only names like (for example *.txt); empty shows all", QLineEdit.EchoMode.Normal, view.name_mask)
        if given:
            view.set_name_mask(mask.strip())
            session = self.current()
            if session is not None and view is self.remote_side.file_list:
                session.name_mask = view.name_mask
