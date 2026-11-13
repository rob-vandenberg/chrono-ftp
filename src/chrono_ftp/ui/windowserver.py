"""The actions of the main window on the server: rename, move, delete, find and the terminal."""

import os
import subprocess
import threading
from dataclasses import replace
from pathlib import Path
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from chrono_ftp.connections.base import joined, parent_of, RemoteConnection, RemoteEntry, safe_name, shown_name
from chrono_ftp.core.errors import RemoteActionFailed
from chrono_ftp.engine import commands
from chrono_ftp.engine.terminal import terminal_arguments
from chrono_ftp.ui.searchdialog import SearchDialog
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import NOT_A_NAME_TEXT, WindowBase
from chrono_ftp.ui.plaintext import box_text, fitting

# --- Version ------------------------------------------------------------
__version__ = "windowserver.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: Synchronize moved to windowsynchronize.py, where it became the checklist window of block FF (FF-04, D-041)
# v0.0.42: Compare Contents in Synchronize: the checksum step through the best way the connection offers, also without a shell where
#          the server computes checksums, on a thread of its own and stoppable (FE-01, D-027); review round 1: each run on a thread
#          of its own, stopped when the main window closes; a note on the way shows in the window
# v0.0.41: The rows ticked in Compare go byte for byte under their own names, whatever the transfer settings say of masks, text and names (FD-01 to FD-03)
# v0.0.38: Delete asks first unless the person switched that question off (FP-01)
# v0.0.37: New folder, copy, and owner and permissions moved to windowproperties.py (block FM); the terminal opens in the folder on the
#          screen (FM-12)
# v0.0.12: Synchronize does exactly the ticked rows: no overwrite question and no "only newer", with the dates, rights and resume of
#          Settings; names in message boxes are shown as plain text, long ones shortened in their middle; a Synchronize whose connection
#          was lost meanwhile queues nothing and says so in a box
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


class ServerPart(WindowBase):
    """The actions of the main window on the server: rename, move, delete, find and the terminal."""

    def rename(self) -> None:
        screen = self._on_screen()
        if screen is None or len(screen[2]) != 1:
            return
        session, folder, chosen = screen
        name = self._ask_text("Rename", "New name", shown_name(chosen[0].name))
        if name == shown_name(chosen[0].name).strip():
            return
        if name and not safe_name(name):
            QMessageBox.information(self, "Rename", box_text(NOT_A_NAME_TEXT))
            return
        if name and name in {entry.name for entry in session.entries}:
            QMessageBox.information(self, "Rename", box_text(commands.TARGET_EXISTS_TEXT))
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

    def delete(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        names = ", ".join(fitting(shown_name(entry.name), self) for entry in chosen)
        if self.settings.confirm_delete and QMessageBox.question(self, "Delete", box_text(f"Delete in {fitting(shown_name(folder), self)} on the server: {names}?")) != QMessageBox.StandardButton.Yes:
            return  # asked unless the person switched it off in Settings, Confirmations (FP-01)

        finished: list[str] = []  # a refusal halfway: what went before it is gone all the same

        def work(remote: RemoteConnection) -> None:
            for entry in chosen:
                remote.delete(joined(folder, entry.name), entry.is_folder and not entry.is_link)
                finished.append(entry.name)

        self._ask(session, work, self._gone_and_refresh(session, folder, finished), refresh_after_failure=True, on_failure=lambda: self._stop_editing(session, folder, finished))

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

    def open_terminal(self) -> None:
        session = self.current()
        if session is not None:
            folder = self.remote_side.folder or session.remote_folder  # the folder on the screen (FM-12)
            subprocess.Popen(terminal_arguments(session.connection, folder), start_new_session=True)
