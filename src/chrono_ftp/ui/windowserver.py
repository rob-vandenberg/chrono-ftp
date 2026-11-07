"""The actions of the main window on the server: new folder, rename, move, copy, delete, owner and permissions, find, synchronize and the terminal."""

import os
import subprocess
from pathlib import Path
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from chrono_ftp.connections.base import joined, parent_of, RemoteConnection, RemoteEntry, safe_name, shown_name
from chrono_ftp.core.errors import RemoteActionFailed
from chrono_ftp.engine import commands
from chrono_ftp.engine.compare import compare_folders, Found
from chrono_ftp.engine.terminal import terminal_arguments
from chrono_ftp.ui.changedialog import ChangeDialog
from chrono_ftp.ui.comparedialog import CompareDialog, uploads
from chrono_ftp.ui.searchdialog import SearchDialog
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import NOT_A_NAME_TEXT, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowserver.py 0.0.11"

# --- Version history ----------------------------------------------------
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


class ServerPart(WindowBase):
    """The actions of the main window on the server: new folder, rename, move, copy, delete, owner and permissions, find, synchronize and the terminal."""

    def new_folder(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        name = self._ask_text("New folder", "Name of the new folder")
        if name and not safe_name(name):
            QMessageBox.information(self, "New folder", NOT_A_NAME_TEXT)
        elif name:
            path = joined(folder, name)
            self._ask(session, lambda remote: remote.make_folder(path), self._then_refresh(session))

    def rename(self) -> None:
        screen = self._on_screen()
        if screen is None or len(screen[2]) != 1:
            return
        session, folder, chosen = screen
        name = self._ask_text("Rename", "New name", shown_name(chosen[0].name))
        if name == shown_name(chosen[0].name).strip():
            return
        if name and not safe_name(name):
            QMessageBox.information(self, "Rename", NOT_A_NAME_TEXT)
            return
        if name and name in {entry.name for entry in session.entries}:
            QMessageBox.information(self, "Rename", commands.TARGET_EXISTS_TEXT)
            return
        if name:
            old, new = joined(folder, chosen[0].name), joined(folder, name)
            self._ask(session, lambda remote: remote.rename(old, new), self._gone_and_refresh(session, folder, [chosen[0].name]))

    def move_on_server(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        target = self._ask_text("Move", "Folder on the server to move to", shown_name(folder))
        if target:
            self._move_into(session, folder, chosen, target)

    @override
    def _move_into(self, session: Session, folder: str, chosen: list[RemoteEntry], target: str) -> None:
        paths = [joined(folder, entry.name) for entry in chosen]
        finished: list[str] = []  # a refusal halfway: what went before it has moved all the same

        def work(remote: RemoteConnection) -> None:
            for path in paths:
                destination = joined(target, Path(path).name)
                if remote.capabilities.has("mv"):
                    commands.run_checked(remote, commands.without_overwriting(destination, commands.move_command(path, destination)))
                elif Path(path).name in {entry.name for entry in remote.list_folder(target)}:
                    raise RemoteActionFailed(commands.TARGET_EXISTS_TEXT)
                else:
                    remote.rename(path, destination)
                finished.append(Path(path).name)

        self._ask(session, work, self._gone_and_refresh(session, folder, finished), refresh_after_failure=True, on_failure=lambda: self._stop_editing(session, folder, finished))

    def copy(self) -> None:
        screen = self._on_screen()
        if screen is None or len(screen[2]) != 1:
            return
        session, folder, chosen = screen
        source = joined(folder, chosen[0].name)
        target = self._ask_text("Copy", "Copy on the server to", f"{shown_name(source)}-copy")
        if target:
            line = commands.without_overwriting(target, commands.copy_command(source, target))
            self._ask(session, lambda remote: commands.run_checked(remote, line), self._then_refresh(session), refresh_after_failure=True)

    def delete(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        names = ", ".join(shown_name(entry.name) for entry in chosen)
        if QMessageBox.question(self, "Delete", f"Delete in {shown_name(folder)} on the server: {names}?") != QMessageBox.StandardButton.Yes:
            return

        finished: list[str] = []  # a refusal halfway: what went before it is gone all the same

        def work(remote: RemoteConnection) -> None:
            for entry in chosen:
                remote.delete(joined(folder, entry.name), entry.is_folder and not entry.is_link)
                finished.append(entry.name)

        self._ask(session, work, self._gone_and_refresh(session, folder, finished), refresh_after_failure=True, on_failure=lambda: self._stop_editing(session, folder, finished))

    def change(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        paths = [joined(folder, entry.name) for entry in chosen]
        dialog = ChangeDialog(commands.can_change_owner(session.capabilities), self)
        if dialog.exec() != ChangeDialog.DialogCode.Accepted.value:
            return
        lines = dialog.commands_for(paths)
        text = "This will run on the server:\n\n" + "\n".join(shown_name(line) for line in lines) + "\n\nGo ahead?"  # OR-6: the exact command, first
        if QMessageBox.question(self, "Owner and permissions", text) != QMessageBox.StandardButton.Yes:
            return

        def work(remote: RemoteConnection) -> None:
            for line in lines:
                commands.run_checked(remote, line)

        self.status.showMessage("Running on the server")
        self._ask(session, work, self._then_refresh(session), refresh_after_failure=True)

    def search(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        dialog = SearchDialog(folder, self)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)  # it belongs to this tab: no other tab can come up meanwhile

        def find(pattern: str) -> None:
            remote = session.remote
            if remote is None:
                dialog.show_failure("The connection is closed.")
                return

            def show(found: list[str]) -> None:
                dialog.show_results(found)

            def failed(error: Exception) -> None:
                dialog.show_failure(str(error))

            session.worker.submit(lambda: commands.search(remote, folder, pattern), show, failed)

        def opened(path: str) -> None:
            self.load_folder(session, parent_of(path))
            dialog.close()

        dialog.search_requested.connect(find)
        dialog.result_opened.connect(opened)
        dialog.show()

    def compare(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        local_folder = self.local_side.folder
        dialog = CompareDialog(local_folder, folder, self)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)  # it belongs to this tab: no other tab can come up meanwhile
        remote = session.remote
        if remote is None:
            return

        def show(found: list[Found]) -> None:
            dialog.show_found(found)

        def failed(error: Exception) -> None:
            dialog.show_failure(shown_name(str(error)))

        def copy(rows: list[Found]) -> None:
            for row in rows:
                here = os.path.join(local_folder, row.folder) if row.folder else local_folder
                there = joined(folder, row.folder) if row.folder else folder
                if uploads(row):
                    self._add_job(session, os.path.join(here, row.name), there, True, row.name)
                else:
                    self._add_job(session, here, joined(there, row.name), False, row.name)

        dialog.copy_requested.connect(copy)
        session.worker.submit(lambda: compare_folders(remote, local_folder, folder), show, failed)
        dialog.show()

    def open_terminal(self) -> None:
        session = self.current()
        if session is not None:
            subprocess.Popen(terminal_arguments(session.connection, session.remote_folder), start_new_session=True)
