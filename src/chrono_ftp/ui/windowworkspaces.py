"""Workspaces (FI-02): every open tab saved under a name, and opened again in one go. A right-click on an empty part of the tab row
saves, opens, renames and deletes them, and chooses the one that opens by itself at start."""

from collections.abc import Callable
from typing import override

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import workspaces as saved_workspaces
from chrono_ftp.config.workspaces import LONGEST_NAME, Workspace, WorkspaceTab, Workspaces
from chrono_ftp.connections.base import shown_name
from chrono_ftp.ui import icons
from chrono_ftp.ui.plaintext import asked
from chrono_ftp.ui.session import may_keep
from chrono_ftp.ui.windowbase import WINDOW_TITLE, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowworkspaces.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 7: a tab opened from an address counts as no tab of a connection that carries the name it was given, so a
#          workspace of that connection opens its own tabs (FJ-11)
# v0.0.47: Block FJ, review round 1: a tab opened from an address is left out of a saved workspace, as it is in no connection list, and
#          saving with only such tabs open says so; a connection saved under a new name keeps its place in every workspace (FJ-02, FJ-11)
# v0.0.45: Review round 2: a workspace opens again exactly the tabs it saved, two tabs on one connection included (D-098, D-100)
# v0.0.45: Review round 1: a name longer than the file keeps is refused when it is typed; a second tab of one connection is counted
#          and said apart from a tab that was open before; a workspace at start that opens nothing falls back on the connections window
# v0.0.45: Initial: the open tabs saved under a name and opened again in one go, renamed and deleted, and one that opens by itself at
#          start; the menu on an empty part of the tab row (FI-02, D-100)

SAVE_TEXT: str = "Save These Tabs as a Workspace…"
OPEN_MENU_TEXT: str = "Open a Workspace"
RENAME_MENU_TEXT: str = "Rename a Workspace"
DELETE_MENU_TEXT: str = "Delete a Workspace"
AT_START_MENU_TEXT: str = "Open at Start"
NO_WORKSPACE_TEXT: str = "No Workspace"
NO_TAB_TEXT: str = "No tab is open to save."
ONLY_ADDRESSES_TEXT: str = "A workspace can hold only tabs of connections in your list, and every open tab was opened from an address."
NONE_YET_TEXT: str = "You have no workspaces yet."
SAVE_TITLE: str = "Save a Workspace"
SAVE_LABEL: str = "Name for this workspace:"
RENAME_TITLE: str = "Rename a Workspace"
RENAME_LABEL: str = "New name for {name}:"
REPLACE_TEXT: str = "A workspace called {name} is there already. Replace it?"
TAKEN_TEXT: str = "A workspace called {name} is there already, so nothing was renamed."
DELETE_TEXT: str = "Delete the workspace {name}? The tabs it holds stay open."
SAVED_TEXTS: tuple[str, str] = ("Workspace {name} holds 1 tab.", "Workspace {name} holds {count} tabs.")
NOT_KEPT_TEXT: str = "The workspaces could not be kept: {reason}"
DELETED_TEXT: str = "Workspace {name} is gone. The tabs it held are still open."
RENAMED_TEXT: str = "Workspace {old} is called {new} now."
AT_START_TEXT: str = "Workspace {name} opens by itself when Chrono FTP starts."
NO_AT_START_TEXT: str = "No workspace opens by itself when Chrono FTP starts."
OPENED_TEXTS: tuple[str, str] = ("1 tab opened", "{count} tabs opened")
TOO_LONG_TEXT: str = "A workspace name can be at most {count} characters, so this one was not saved. Give a shorter name."
NOTHING_OPENED_AT_START_TEXT: str = "Workspace {name} opened no tab, so the connections window opens instead."
ALREADY_TEXTS: tuple[str, str] = ("1 was open already", "{count} were open already")
GONE_TEXT: str = "no longer in your connection list: {names}"
NOTHING_OPENED_TEXT: str = "Workspace {name} holds no tab that can be opened."
MENU_WIDTH_CHARACTERS: int = 30  # a menu wide enough for the longest of its entries


class WorkspacesPart(WindowBase):
    """Workspaces (FI-02)."""

    @override
    def workspaces_menu(self) -> QMenu:
        """The menu of an empty part of the tab row: save, open, rename and delete workspaces, and the one that opens at start."""
        kept = saved_workspaces.load()
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.setMinimumWidth(menu.fontMetrics().averageCharWidth() * MENU_WIDTH_CHARACTERS)
        save = QAction(icons.icon("document-properties"), SAVE_TEXT, menu)
        save.triggered.connect(self.save_workspace)
        if not self.sessions:
            save.setEnabled(False)  # greyed with its reason, never hidden (OR-20)
            save.setToolTip(NO_TAB_TEXT)
        menu.addAction(save)
        menu.addSeparator()
        for text, handler in ((OPEN_MENU_TEXT, self.open_workspace), (RENAME_MENU_TEXT, self.rename_workspace), (DELETE_MENU_TEXT, self.delete_workspace)):
            self._names_menu(menu, kept, text, handler)
        menu.addSeparator()
        at_start = menu.addMenu(AT_START_MENU_TEXT)
        at_start.setToolTipsVisible(True)
        nothing = QAction(NO_WORKSPACE_TEXT, at_start)
        nothing.setCheckable(True)
        nothing.setChecked(not kept.at_start)
        nothing.triggered.connect(lambda _checked=False: self.open_at_start(""))
        at_start.addAction(nothing)
        for name in saved_workspaces.names(kept):
            action = QAction(shown_name(name), at_start)
            action.setCheckable(True)
            action.setChecked(name == kept.at_start)
            action.triggered.connect(lambda _checked=False, name=name: self.open_at_start(name))
            at_start.addAction(action)
        return menu

    def _names_menu(self, menu: QMenu, kept: Workspaces, text: str, handler: Callable[[str], object]) -> None:
        submenu = menu.addMenu(text)
        submenu.setToolTipsVisible(True)
        names = saved_workspaces.names(kept)
        if not names:
            submenu.setEnabled(False)  # greyed with its reason (OR-20)
            submenu.setToolTip(NONE_YET_TEXT)
            return
        for name in names:
            action = QAction(shown_name(name), submenu)
            action.triggered.connect(lambda _checked=False, name=name, handler=handler: handler(name))
            submenu.addAction(action)

    @override
    def _workspaces_renamed(self, old: str, new: str) -> tuple[str, Callable[[], str]]:
        """A connection saved under a new name, or moved into a folder, keeps its place in every workspace. The reason they could not
        follow, empty when they did, and the way back: it writes exactly what stood there before (FJ-02)."""
        kept = saved_workspaces.load()
        changed = saved_workspaces.connection_renamed(kept, old, new)

        def back() -> str:
            return self._workspaces_written(kept)

        return ("", back) if changed == kept else (self._workspaces_written(changed), back)

    def _workspaces_written(self, kept: Workspaces) -> str:
        """These workspaces on the disk; the reason they could not be written, empty when they were."""
        try:
            saved_workspaces.save(kept)
        except OSError as error:
            return error.strerror or str(error)
        return ""

    def _write(self, kept: Workspaces) -> bool:
        try:
            saved_workspaces.save(kept)
        except OSError as error:
            self.status.showMessage(NOT_KEPT_TEXT.format(reason=error.strerror or str(error)))
            return False
        return True

    def save_workspace(self) -> None:
        """Every open tab, with the folders both its sides are in, under a name the person gives (FI-02)."""
        if not self.sessions:
            return
        if not any(may_keep(session) for session in self.sessions):
            self.status.showMessage(ONLY_ADDRESSES_TEXT)  # nothing of such a tab is kept, so there is nothing to save (FJ-11)
            return
        name = self._ask_text(SAVE_TITLE, SAVE_LABEL)
        if name is None:
            return
        if len(name) > LONGEST_NAME:  # never accept what would be thrown away when it is read back (G-2, OR-15)
            self.status.showMessage(TOO_LONG_TEXT.format(count=LONGEST_NAME))
            return
        kept = saved_workspaces.load()
        if saved_workspaces.named(kept, name) is not None and not asked(self, WINDOW_TITLE, REPLACE_TEXT.format(name=shown_name(name))):
            return
        tabs = tuple(WorkspaceTab(session.connection.name, session.local_folder, session.remote_folder) for session in self.sessions if may_keep(session))
        if self._write(saved_workspaces.with_workspace(kept, Workspace(name, tabs))):
            self.status.showMessage((SAVED_TEXTS[0] if len(tabs) == 1 else SAVED_TEXTS[1]).format(name=shown_name(name), count=len(tabs)))

    @override
    def open_workspace(self, name: str) -> int:
        """Every tab of the workspace, each in the folders it was saved in. A connection that already has a tab keeps the tab it has
        (D-051); one that is no longer in the connection list is named (FI-02)."""
        workspace = saved_workspaces.named(saved_workspaces.load(), name)
        if workspace is None:
            return 0
        known = {connection.name: connection for connection in saved_connections.load().connections}
        open_before: dict[str, int] = {}
        for session in self.sessions:
            if may_keep(session):  # a tab opened from an address is no tab of this connection, whatever name it was given (FJ-11)
                open_before[session.connection.name] = open_before.get(session.connection.name, 0) + 1
        counted: dict[str, int] = {}  # how many tabs of each connection this workspace has accounted for so far
        opened, already = 0, 0
        gone: list[str] = []
        self.opening_workspace = True  # the order the workspace saved wins over the order the tabs were dragged into (FI-01)
        try:
            for tab in workspace.tabs:
                connection = known.get(tab.connection)
                if connection is None:
                    gone.append(shown_name(tab.connection))
                    continue
                seen = counted.get(tab.connection, 0)
                counted[tab.connection] = seen + 1
                if seen < open_before.get(tab.connection, 0):
                    already += 1  # it had this tab before the workspace was opened: that tab stays as it is (D-051)
                    continue
                if seen:
                    self.open_another_tab(connection, tab.local_folder, tab.remote_folder)  # the person saved more than one tab on it (D-098)
                else:
                    self.open_connection(connection, tab.local_folder, tab.remote_folder)
                opened += 1
        finally:
            self.opening_workspace = False
        self.status.showMessage(self._opening_text(workspace.name, opened, already, gone))
        return opened

    def _opening_text(self, name: str, opened: int, already: int, gone: list[str]) -> str:
        """What really happened, never more and never less (G-2)."""
        parts: list[str] = []
        if opened:
            parts.append(OPENED_TEXTS[0] if opened == 1 else OPENED_TEXTS[1].format(count=opened))
        if already:
            parts.append(ALREADY_TEXTS[0] if already == 1 else ALREADY_TEXTS[1].format(count=already))
        if gone:
            parts.append(GONE_TEXT.format(names=", ".join(gone)))
        if not parts:
            return NOTHING_OPENED_TEXT.format(name=shown_name(name))
        return f"Workspace {shown_name(name)}: " + "; ".join(parts) + "."

    def rename_workspace(self, name: str) -> None:
        kept = saved_workspaces.load()
        if saved_workspaces.named(kept, name) is None:
            return
        chosen = self._ask_text(RENAME_TITLE, RENAME_LABEL.format(name=shown_name(name)), name)
        if chosen is None or chosen == name:
            return
        if len(chosen) > LONGEST_NAME:
            self.status.showMessage(TOO_LONG_TEXT.format(count=LONGEST_NAME))
            return
        if saved_workspaces.named(kept, chosen) is not None:
            self.status.showMessage(TAKEN_TEXT.format(name=shown_name(chosen)))  # nothing is replaced behind the person's back (G-2)
            return
        if self._write(saved_workspaces.renamed(kept, name, chosen)):
            self.status.showMessage(RENAMED_TEXT.format(old=shown_name(name), new=shown_name(chosen)))

    def delete_workspace(self, name: str) -> None:
        kept = saved_workspaces.load()
        if saved_workspaces.named(kept, name) is None or not asked(self, WINDOW_TITLE, DELETE_TEXT.format(name=shown_name(name))):
            return
        if self._write(saved_workspaces.without(kept, name)):
            self.status.showMessage(DELETED_TEXT.format(name=shown_name(name)))

    def open_at_start(self, name: str) -> None:
        kept = saved_workspaces.with_start(saved_workspaces.load(), name)
        if self._write(kept):
            self.status.showMessage(AT_START_TEXT.format(name=shown_name(name)) if kept.at_start else NO_AT_START_TEXT)

    @override
    def open_start_workspace(self) -> bool:
        """At start: the workspace the person chose, if any. False when none was chosen, and the connections window opens instead."""
        kept = saved_workspaces.load()
        if not kept.at_start:
            return False
        if self.open_workspace(kept.at_start):
            return True
        self.status.showMessage(NOTHING_OPENED_AT_START_TEXT.format(name=shown_name(kept.at_start)))
        return False  # nothing opened: the person gets the connections window, as without a workspace
