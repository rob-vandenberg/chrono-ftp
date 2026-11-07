"""The actions of the main window on this computer: open, edit, new folder, rename and delete to the Trash."""

from collections.abc import Callable
from pathlib import Path
from typing import cast, override

from PySide6.QtCore import QFile, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from chrono_ftp.connections.base import shown_name
from chrono_ftp.core.errors import LocalActionFailed
from chrono_ftp.engine import localfiles
from chrono_ftp.ui.windowbase import NOT_OPENABLE_TEXT, WINDOW_TITLE, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowlocal.py 0.0.11"

# --- Version history ----------------------------------------------------
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


class LocalPart(WindowBase):
    """The actions of the main window on this computer: open, edit, new folder, rename and delete to the Trash."""

    def _on_this_computer(self, work: Callable[[], None]) -> None:
        try:
            work()
        except LocalActionFailed as error:
            QMessageBox.information(self, WINDOW_TITLE, str(error))
        self.local_side.refresh()

    @override
    def _open_local_file(self, path: str) -> None:
        if shown_name(path) != path:  # Qt would change the name, and open another file
            QMessageBox.information(self, WINDOW_TITLE, NOT_OPENABLE_TEXT)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _edit_local(self) -> None:
        paths = self.local_side.selected_paths()
        if len(paths) == 1:
            self._open_local_file(paths[0])

    def _new_local_folder(self) -> None:
        folder = self.local_side.folder
        name = self._ask_text("New folder", "Name of the new folder")
        if name:
            self._on_this_computer(lambda: localfiles.make_folder(folder, name))

    def _rename_local(self) -> None:
        chosen = self.local_side.selected_entries()
        if len(chosen) != 1:
            return
        folder, old = self.local_side.folder, chosen[0].name
        name = self._ask_text("Rename", "New name", shown_name(old))
        if name and name != shown_name(old).strip():
            self._on_this_computer(lambda: localfiles.rename(folder, old, name))

    def _delete_local(self) -> None:
        paths = self.local_side.selected_paths()
        if not paths:
            return
        names = ", ".join(shown_name(Path(path).name) for path in paths)
        if QMessageBox.question(self, "Delete", f"Move to the Trash from {shown_name(self.local_side.folder)}: {names}?") != QMessageBox.StandardButton.Yes:
            return
        left = [shown_name(Path(path).name) for path in paths if not cast(object, QFile.moveToTrash(path)) is True]  # a plain bool, whatever the stub says
        if left:
            QMessageBox.information(self, "Delete", f"These could not go to the Trash and are still here: {', '.join(left)}")
        self.local_side.refresh()
