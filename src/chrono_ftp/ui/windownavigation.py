"""Navigation in the main window (block FH): the path of the focused side, the Open folder window, bookmarks, location profiles, the same
folder on the other side, synchronized browsing and the remembered last folders."""

import os
from dataclasses import replace
from collections.abc import Callable
from pathlib import PurePath, PurePosixPath
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

from chrono_ftp.config import bookmarks as saved_places
from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config.bookmarks import LOCAL, REMOTE, SHARED, Bookmark, Places, Profile
from chrono_ftp.connections.base import RemoteConnection, parent_of, shown_name
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, LocalActionFailed
from chrono_ftp.engine import localfiles
from chrono_ftp.engine.browsing import matching_folder, typed_local_path, typed_remote_path
from chrono_ftp.ui import icons
from chrono_ftp.ui.bookmarksdialog import BookmarksDialog, key_usable
from chrono_ftp.ui.menus import open_menu
from chrono_ftp.ui.openfolder import LOCAL_TITLE, REMOTE_TITLE, OpenFolderDialog
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import WINDOW_TITLE, WindowBase
from chrono_ftp.ui.plaintext import box_text

# --- Version ------------------------------------------------------------
__version__ = "windownavigation.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Shift+Space, Calculate Folder Sizes, is a key of the program: a bookmark can't take it (block FE)
# v0.0.40: The keys of block FG are keys of the program: a bookmark can't take them
# v0.0.37: F9, Shift+F4, Shift+F5, Alt+F6 and Ctrl+Alt+E are keys of the program: a bookmark can't take them (block FM)
# v0.0.35: F1 is a key of the program: a bookmark can't take it (D-059)
# v0.0.34: Initial: Ctrl+L, Ctrl+O, Ctrl+B and Ctrl+. on the focused side, the bookmark menus and keys, location profiles,
#          synchronized browsing per tab, and the last folders written back (FH-01 to FH-06); a typed path is read the same for Ctrl+L
#          and Ctrl+O and kept only once it opened; a tab follows its connection saved under a new name or with new folders; a bookmark
#          key needs Ctrl, Alt or a free F-key, never a key lists need; a step into a folder whose parent is missing turns synchronized browsing off at once,
#          and a failed make says the server's reason; an ended connection turns it off; an & in a name stays an &; a deleted
#          connection's own places go; a folder typed in the bookmarks window is read from the folders shown; a move up because the
#          folder went away is no step; a dropped tab's path can't be typed in or clicked; a side still on the folder a gone starting
#          folder fell back to keeps its starting folder, also after the side moved up by itself

PROGRAM_KEYS: dict[str, str] = {  # the keys of the program itself: a bookmark can't take one (the bookmarks window says so)
    "Ctrl+U": "Upload",
    "Ctrl+D": "Download",
    "F2": "Rename",
    "F4": "Edit",
    "F5": "Refresh",
    "F7": "New folder",
    "Del": "Delete",
    "Backspace": "the parent folder",
    "Return": "the selected entry",
    "Enter": "the selected entry",
    "Ctrl+A": "Select all",
    "Ctrl+O": "Open folder",
    "Ctrl+B": "Add bookmark",
    "Ctrl+.": "the same folder on the other side",
    "Ctrl+L": "typing a path",
    "F1": "About Chrono FTP",
    "F9": "Properties",
    "Shift+F4": "New file",
    "Shift+F5": "Copy to",
    "Alt+F6": "Link",
    "Ctrl+Alt+E": "Open in the file manager",
    "Ctrl+I": "Invert Selection",  # block FG
    "Ctrl+Shift+A": "Clear Selection",
    "Insert": "Mark and Move Down",
    "Space": "marking in the two-pane selection mode",
    "F6": "Move to the other side",
    "Shift+F6": "Move on the server",
    "Alt+F7": "Find Files",
    "Ctrl+F": "Find Files",
    "Ctrl+F3": "Sort by name",
    "Ctrl+F4": "Sort by extension",
    "Ctrl+F5": "Sort by date",
    "Ctrl+F6": "Sort by size",
    "Ctrl+F7": "Sort by rights",
    "Ctrl+F8": "Sort by owner",
    "Ctrl+F9": "Sort by group",
    "Alt+Left": "Back",
    "Alt+Right": "Forward",
    "Ctrl+Shift+C": "Copy the Selected Names",
    "Ctrl+Alt+H": "Show or Hide Hidden Files",
    "Ctrl+R": "Refresh",
    "Tab": "the next name that fits what was typed",
    "Esc": "starting the typed name afresh",
    "Shift+Space": "Calculate Folder Sizes",  # block FE
}
SIDE_ACTIONS: tuple[tuple[str, str, str, str], ...] = (  # name after the side, text, icon, key
    ("open", "Open Folder…", "document-open-folder", "Ctrl+O"),
    ("bookmark", "Add Bookmark", "bookmark-new", "Ctrl+B"),
    ("other_side", "Same Folder on the Other Side", "view-split-left-right", "Ctrl+."),
    ("type", "Type a Path", "edit-rename", "Ctrl+L"),
)
ORGANIZE_TEXT: str = "Organize Bookmarks…"
NO_BOOKMARKS_TEXT: str = "No bookmarks yet"
ADD_PROFILE_TEXT: str = "Add the Current Folders…"
ORGANIZE_PROFILES_TEXT: str = "Organize Location Profiles…"
NO_PROFILES_TEXT: str = "No location profiles yet"
SYNC_ON_TEXT: str = "Synchronized browsing is on for this tab: a step into or out of a folder on one side makes the same step on the other."
SYNC_OFF_TEXT: str = "Synchronized browsing is off for this tab."
SYNC_UNMATCHED_TEXT: str = "Synchronized browsing is now off: the other side can't make the same step, as it can't go up that far. Both sides stay where they are."
SYNC_NOT_MADE_TEXT: str = "Synchronized browsing is now off, as the folder {path} was not made."
SYNC_FAILED_TEXT: str = "Synchronized browsing is now off: the folder {path} on {where} could not be opened or made. {why}"
SYNC_NO_PARENT_TEXT: str = "Synchronized browsing is now off: {where} has no folder {parent}, so it can't follow this step into {name}. Both sides stay where they are."
SYNC_ENDED_TEXT: str = "Synchronized browsing is off for {name}, as its connection ended. You can turn it on again once it is live."
CANT_OPEN_TEXT: str = "It can't be opened."
MAKE_REMOTE_TEXT: str = "There is no folder {name} in {parent} on the server.\n\nMake it, so both sides stay in step? No turns synchronized browsing off."
MAKE_LOCAL_TEXT: str = "There is no folder {name} in {parent} on this computer.\n\nMake it, so both sides stay in step? No turns synchronized browsing off."
LOCAL_GONE_TEXT: str = "There is no folder {path} on this computer, so this side stays where it was."
REMOTE_GONE_TEXT: str = "The folder {path} can't be opened on the server, so this side stays where it was. {why}"
BOOKMARK_ADDED_TEXT: str = "Bookmark {name} added. Organize Bookmarks, in the Bookmarks menu, renames it, gives it a key or shows it on every connection."
BOOKMARK_THERE_TEXT: str = "This folder is a bookmark already."
PROFILE_ADDED_TEXT: str = "Location profile {name} added."
PLACES_SET_ASIDE_TEXT: str = "Part of your bookmarks could not be read. A copy is kept, so nothing is lost: {path}"
PLACES_NOT_KEPT_TEXT: str = "Your bookmarks apply now, but could not be kept for next time."
FOLDERS_NOT_KEPT_TEXT: str = "The last folders of {name} could not be kept for next time."
SYNC_TIP: str = "Synchronized browsing, for this tab: a step into or out of a folder on one side makes the same step on the other side. Needs a live connection."
PROFILES_TIP: str = "Location profiles: a saved pair of a local and a remote folder; one click opens both. Needs a live connection."


def menu_text(name: str) -> str:
    """A name the person gave, as the text of a menu entry: an & stays an &, never a shortcut mark."""
    return name.replace("&", "&&")


def _name_and_parent(path: str, local: bool) -> tuple[str, str]:
    place = PurePath(path) if local else PurePosixPath(path)
    return shown_name(place.name), shown_name(str(place.parent))


class NavigationPart(WindowBase):
    """Navigation in the main window (block FH)."""

    def _build_navigation(self) -> None:
        loaded = saved_places.load()
        self.places = loaded.places
        self.places_writable = loaded.writable
        if loaded.set_aside is not None:
            self.status.showMessage(PLACES_SET_ASIDE_TEXT.format(path=loaded.set_aside))
        for side_name, side in ((LOCAL, self.local_side), (REMOTE, self.remote_side)):
            for name, text, icon_name, key in SIDE_ACTIONS:
                action = QAction(icons.icon(icon_name), text, self)
                action.setShortcut(QKeySequence(key))
                action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)  # a key acts on the side with the focus (OR-21)
                side.addAction(action)
                action.triggered.connect(lambda _checked=False, name=name, side_name=side_name: self._side_action(side_name, name))
                self.actions_by_name[f"{side_name}_{name}"] = action
            side.navigation.wanted.connect(lambda wanted="", side_name=side_name: self._show_bookmark_menu(side_name) if wanted == "bookmarks" else None)
        self.local_side.path_not_found.connect(self._local_path_not_found)
        self.remote_side.path_typed.connect(self._remote_path_typed)
        self.actions_by_name["sync_browsing"].setCheckable(True)
        self.actions_by_name["sync_browsing"].setToolTip(SYNC_TIP)
        self.actions_by_name["profiles"].setToolTip(PROFILES_TIP)
        application = QApplication.instance()
        if isinstance(application, QApplication):
            application.focusChanged.connect(self._focus_moved)
        self._show_focus()
        self._set_bookmark_keys()

    # --- the side with the focus (FH-01) --------------------------------------

    def _focus_moved(self, _old: QWidget | None, new: QWidget | None) -> None:
        if new is None:
            return
        if new is self.local_side or self.local_side.isAncestorOf(new):
            self.focused_side = LOCAL
        elif new is self.remote_side or self.remote_side.isAncestorOf(new):
            self.focused_side = REMOTE
        self._show_focus()

    def _show_focus(self) -> None:
        self.local_side.path_bar.set_focused(self.focused_side == LOCAL)
        self.remote_side.path_bar.set_focused(self.focused_side == REMOTE)

    def _side_action(self, side: str, name: str) -> None:
        if name == "open":
            self.open_folder(side)
        elif name == "bookmark":
            self.add_bookmark(side)
        elif name == "other_side":
            self.same_folder_on_the_other_side(side)
        elif name == "type":
            (self.local_side if side == LOCAL else self.remote_side).path_bar.start_typing()

    @override
    def _tab_shown(self) -> None:
        """A tab came to the front, or its connection went live or dropped: its own state of navigation shows."""
        session = self.current()
        action = self.actions_by_name["sync_browsing"]
        action.blockSignals(True)
        action.setChecked(session is not None and session.sync_browsing)
        action.blockSignals(False)
        login = ""
        if session is not None and session.alive:
            connection = session.connection
            login = f"{connection.user}@{connection.host}" if connection.user else connection.host
        self.remote_side.set_login(login)
        self.remote_side.set_live(session is not None and session.alive)
        self._set_bookmark_keys()

    # --- the places file ---------------------------------------------------

    def _keep_places(self, places: Places) -> None:
        self.places = places
        try:
            if not self.places_writable:
                raise OSError("the file of the bookmarks can't be read, so it is left as it is")
            saved_places.save(places)
        except OSError:
            self.status.showMessage(PLACES_NOT_KEPT_TEXT)
        self._set_bookmark_keys()

    @override
    def _connection_deleted(self, name: str) -> None:
        """A deleted connection's own bookmarks, profiles and typed paths go with it, so a new connection of that name starts clean."""
        self._keep_places(saved_places.without_connection(self.places, name))

    @override
    def _connection_saved(self, old: str, connection: Connection) -> None:
        """A connection was saved in the connections window, maybe under a new name. Its bookmarks, location profiles and typed paths go
        with the name. Its open tabs take the new record at once, without logging in again: the name, colour and folders; what the login
        uses stays as the tab logged in, so Connect still sees a changed login and logs in again (D-051)."""
        if old != connection.name:
            self._keep_places(saved_places.with_connection_renamed(self.places, old, connection.name))
        for index, session in enumerate(self.sessions):
            if session.connection.name != old:
                continue
            login = session.connection
            session.connection = replace(connection, host=login.host, port=login.port, user=login.user, protocol=login.protocol, key_file=login.key_file)
            self.tab_bar.setTabText(index, session.connection.name)  # as the tab was made
            self._set_tab(session)
        self._set_bookmark_keys()

    def _connection_name(self) -> str:
        session = self.current()
        return session.connection.name if session is not None else ""

    # --- bookmarks (FH-02) ---------------------------------------------------

    def bookmark_menu_for(self, side: str) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for name in ("bookmark", "open"):
            menu.addAction(self.actions_by_name[f"{side}_{name}"])
        organize = QAction(icons.icon("bookmarks"), ORGANIZE_TEXT, menu)
        organize.triggered.connect(lambda: self.organize_bookmarks(side))
        menu.addAction(organize)
        menu.addSeparator()
        marks = saved_places.bookmarks_of(self.places, side, self._connection_name())
        for mark in marks:
            entry = QAction(f"{menu_text(mark.name)}\t{mark.key}" if mark.key else menu_text(mark.name), menu)
            entry.setToolTip(shown_name(mark.path))
            entry.triggered.connect(lambda _checked=False, mark=mark: self.open_bookmark(mark))
            menu.addAction(entry)
        if not marks:
            empty = QAction(NO_BOOKMARKS_TEXT, menu)
            empty.setEnabled(False)
            menu.addAction(empty)
        return menu

    def _show_bookmark_menu(self, side: str) -> None:
        bar = (self.local_side if side == LOCAL else self.remote_side).navigation
        button = bar.widgetForAction(bar.buttons["bookmarks"])
        menu = self.bookmark_menu_for(side)
        if button is not None:
            open_menu(menu, button.mapToGlobal(button.rect().bottomLeft()))
        menu.deleteLater()

    def add_bookmark(self, side: str) -> None:
        """The folder of this side becomes a bookmark at once, named after its last folder (Ctrl+B)."""
        session = self.current()
        if side == REMOTE and (session is None or not session.alive or not self.remote_side.folder):
            return
        path = self.local_side.folder if side == LOCAL else self.remote_side.folder
        connection = session.connection.name if side == REMOTE and session is not None else SHARED
        if saved_places.has_bookmark(self.places, side, connection, path):
            self.status.showMessage(BOOKMARK_THERE_TEXT)
            return
        mark = Bookmark(saved_places.default_name(path, side), path, side, connection)
        self._keep_places(saved_places.with_bookmark(self.places, mark))
        self.status.showMessage(BOOKMARK_ADDED_TEXT.format(name=shown_name(mark.name)))

    def organize_bookmarks(self, start_tab: str) -> None:
        session = self.current()
        home = session.home_folder if session is not None and session.alive else ""
        dialog = BookmarksDialog(self.places, self._connection_name(), PROGRAM_KEYS, start_tab, self, self.local_side.folder, self.remote_side.folder, home)
        if dialog.exec() == BookmarksDialog.DialogCode.Accepted.value:
            self._keep_places(dialog.places())

    def open_bookmark(self, mark: Bookmark) -> None:
        if mark.side == LOCAL:
            self._go_local(mark.path)
            return
        session = self.current()
        if session is not None and session.alive:
            self._go_remote(session, mark.path)

    def _set_bookmark_keys(self) -> None:
        """The keys of the bookmarks: local ones on the left side, those of the tab's connection on the right. A key the program uses,
        or one an earlier bookmark of that side has, is left out: a key never does two things."""
        for action in self.bookmark_keys:
            for side in (self.local_side, self.remote_side):
                side.removeAction(action)
            action.deleteLater()
        self.bookmark_keys = []
        session = self.current()
        for side_name, side in ((LOCAL, self.local_side), (REMOTE, self.remote_side)):
            if side_name == REMOTE and session is None:
                continue
            used = set(PROGRAM_KEYS)
            for mark in saved_places.bookmarks_of(self.places, side_name, self._connection_name()):
                if not mark.key or mark.key in used or not key_usable(mark.key):
                    continue  # the file is input: a key a list or field needs is never taken
                used.add(mark.key)
                action = QAction(menu_text(mark.name), self)
                action.setShortcut(QKeySequence(mark.key))
                action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                action.triggered.connect(lambda _checked=False, mark=mark: self.open_bookmark(mark))
                side.addAction(action)
                self.bookmark_keys.append(action)

    # --- going somewhere -------------------------------------------------------

    def _go_local(self, path: str, quietly: bool = False) -> bool:
        """The left side opens this folder; one that isn't there gives a calm message and nothing moves. Quietly: the move is no step for
        synchronized browsing (the other side is set on its own)."""
        self.quiet_local = quietly
        try:
            went = self.local_side.go_to(path)
        finally:
            self.quiet_local = False
        if not went:
            QMessageBox.information(self, WINDOW_TITLE, box_text(LOCAL_GONE_TEXT.format(path=shown_name(path))))
        return went

    def _go_remote(self, session: Session, path: str, quietly: bool = False, opened: Callable[[], None] | None = None) -> None:
        """The right side opens this folder; one that can't be opened gives a calm message and nothing moves. opened: called once the
        folder is on the screen."""
        if quietly:
            session.sync_asked = path

        def not_there(error: Exception) -> None:
            QMessageBox.information(self, WINDOW_TITLE, box_text(REMOTE_GONE_TEXT.format(path=shown_name(path), why=error)))

        self.load_folder(session, path, not_there, opened)

    def _local_path_not_found(self, path: object) -> None:
        """A path typed on the left (Ctrl+L) that is no folder: told as Ctrl+O tells it."""
        if isinstance(path, str):
            QMessageBox.information(self, WINDOW_TITLE, box_text(LOCAL_GONE_TEXT.format(path=shown_name(path))))

    def _remote_path_typed(self, path: object) -> None:
        """A path typed on the right (Ctrl+L), already read as Ctrl+O reads it."""
        session = self.current()
        if isinstance(path, str) and session is not None and session.alive:
            self._go_remote(session, path)

    def open_folder(self, side: str) -> None:
        """The Open folder window (Ctrl+O): a typed path, one typed before, or a bookmark of the side."""
        session = self.current()
        connection = session.connection.name if session is not None else ""
        if side == REMOTE and (session is None or not session.alive):
            return
        current = self.local_side.folder if side == LOCAL else self.remote_side.folder
        title = LOCAL_TITLE if side == LOCAL else REMOTE_TITLE.format(name=connection)
        marks = saved_places.bookmarks_of(self.places, side, connection)
        dialog = OpenFolderDialog(title, current, saved_places.typed_of(self.places, side, connection), marks, self)
        if dialog.exec() != OpenFolderDialog.DialogCode.Accepted.value:
            return
        chosen = dialog.chosen()
        if not chosen:
            return
        if side == LOCAL:
            path = typed_local_path(self.local_side.folder, chosen)
            if self._go_local(path):
                self._keep_places(saved_places.with_typed(self.places, LOCAL, connection, path))
        elif session is not None:
            path = typed_remote_path(current, chosen, session.home_folder)
            # kept only once it opened, as on the left; by then the person may be on another tab: it belongs to this connection
            self._go_remote(session, path, opened=lambda: self._keep_places(saved_places.with_typed(self.places, REMOTE, connection, path)))

    def same_folder_on_the_other_side(self, side: str) -> None:
        """The folder of this side opens on the other side (Ctrl+.); when it isn't there, a calm message and nothing moves."""
        session = self.current()
        if session is None or not session.alive:
            return
        if side == LOCAL:
            self._go_remote(session, self.local_side.folder, quietly=True)
        elif self.remote_side.folder:
            self._go_local(self.remote_side.folder, quietly=True)

    # --- location profiles (FH-03) ---------------------------------------------

    def profile_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        add = QAction(icons.icon("bookmark-new"), ADD_PROFILE_TEXT, menu)
        add.triggered.connect(self.add_profile)
        add.setEnabled(bool(self.remote_side.folder))
        menu.addAction(add)
        organize = QAction(icons.icon("bookmarks"), ORGANIZE_PROFILES_TEXT, menu)
        organize.triggered.connect(lambda: self.organize_bookmarks("profiles"))
        menu.addAction(organize)
        menu.addSeparator()
        profiles = saved_places.profiles_of(self.places, self._connection_name())
        folders: dict[str, QMenu] = {}
        for profile in profiles:
            place = menu
            if profile.folder:
                if profile.folder not in folders:
                    folders[profile.folder] = menu.addMenu(menu_text(profile.folder))
                    folders[profile.folder].setToolTipsVisible(True)
                place = folders[profile.folder]
            entry = QAction(menu_text(profile.name), place)
            entry.setToolTip(f"{shown_name(profile.local)}\n{shown_name(profile.remote)}")
            entry.triggered.connect(lambda _checked=False, profile=profile: self.open_profile(profile))
            place.addAction(entry)
        if not profiles:
            empty = QAction(NO_PROFILES_TEXT, menu)
            empty.setEnabled(False)
            menu.addAction(empty)
        return menu

    def show_profiles(self) -> None:
        button = self.general_bar.widgetForAction(self.actions_by_name["profiles"])
        menu = self.profile_menu()
        if button is not None:
            open_menu(menu, button.mapToGlobal(button.rect().bottomLeft()))
        menu.deleteLater()

    def add_profile(self) -> None:
        session = self.current()
        if session is None or not self.remote_side.folder:
            return
        local, remote = self.local_side.folder, self.remote_side.folder
        start = f"{saved_places.default_name(local, LOCAL)} - {saved_places.default_name(remote, REMOTE)}"
        name = self._ask_text("Location profile", "Name of the profile for these two folders", shown_name(start))
        if name:
            self._keep_places(saved_places.with_profile(self.places, Profile(name, local, remote, "", session.connection.name)))
            self.status.showMessage(PROFILE_ADDED_TEXT.format(name=name))

    def open_profile(self, profile: Profile) -> None:
        """Both folders at once; a folder that is no longer there gives a calm message and leaves its side where it was."""
        session = self.current()
        if session is None or not session.alive:
            return
        if profile.local != self.local_side.folder:
            self._go_local(profile.local, quietly=True)
        self._go_remote(session, profile.remote, quietly=True)

    # --- synchronized browsing (FH-05) -------------------------------------------

    def toggle_sync_browsing(self) -> None:
        session = self.current()
        action = self.actions_by_name["sync_browsing"]
        if session is None:
            action.setChecked(False)
            return
        session.sync_browsing = action.isChecked()
        self.status.showMessage(SYNC_ON_TEXT if session.sync_browsing else SYNC_OFF_TEXT)

    def _stop_sync(self, session: Session, why: str, in_a_box: bool) -> None:
        session.sync_browsing = False
        if session is self.current():
            self._tab_shown()
        self.status.showMessage(why)
        if in_a_box:
            QMessageBox.information(self, WINDOW_TITLE, box_text(why))

    @override
    def _local_moved(self, session: Session, previous: str, folder: str) -> None:
        """The left side made a step: the right side makes the same one."""
        if self.local_side.moved_by_itself:
            if session.local_fallback:
                session.local_fallback = folder  # the program moved it, not the person: it is still no folder of the person's
        else:
            session.local_fallback = ""  # the person moved it: its folder is the person's own now
        if self.quiet_local or self.local_side.moved_by_itself or not session.sync_browsing or not session.alive or session is not self.current():
            return
        there = session.wanted_folder or session.remote_folder
        if not previous or not there:
            return
        target = matching_folder(previous, folder, there, True)
        if target is None:
            self._stop_sync(session, SYNC_UNMATCHED_TEXT, True)
            return
        self._sync_remote_to(session, target)

    def _sync_remote_to(self, session: Session, target: str) -> None:
        session.sync_asked = target

        def failed(error: Exception) -> None:
            session.sync_asked = ""
            self._remote_missing(session, target, error)

        self.load_folder(session, target, failed)

    def _remote_missing(self, session: Session, target: str, error: Exception) -> None:
        """A step of synchronized browsing found no folder to open on the server. Only a folder whose parent is there can be made; for
        any other, synchronized browsing turns off at once and says which folder is missing."""
        parent = parent_of(target)

        def look(remote: RemoteConnection) -> str:
            try:
                if remote.name_taken(target):
                    return "there"  # there, but it can't be opened
            except ConnectionLost:
                raise
            except ChronoFtpError:
                pass
            try:
                remote.list_folder(parent)
            except ConnectionLost:
                raise
            except ChronoFtpError:
                return "no parent"
            return "missing"

        def answer(found: object) -> None:
            if session not in self.sessions or not session.sync_browsing:
                return
            name, parent_shown = _name_and_parent(target, False)
            if found == "there":
                self._stop_sync(session, SYNC_FAILED_TEXT.format(path=shown_name(target), where="the server", why=error), True)
            elif found == "no parent":
                self._stop_sync(session, SYNC_NO_PARENT_TEXT.format(where="the server", parent=parent_shown, name=name), True)
            elif QMessageBox.question(self, WINDOW_TITLE, box_text(MAKE_REMOTE_TEXT.format(name=name, parent=parent_shown))) != QMessageBox.StandardButton.Yes:
                self._stop_sync(session, SYNC_NOT_MADE_TEXT.format(path=shown_name(target)), False)
            else:
                self._make_remote(session, target)

        self._ask(session, look, answer)

    def _make_remote(self, session: Session, target: str) -> None:
        """The one folder synchronized browsing asked for, made through the tab's own thread; a refusal says the server's reason."""
        remote = session.remote
        if remote is None or not session.alive:
            return

        def refused(error: Exception) -> None:
            if isinstance(error, ChronoFtpError) and not isinstance(error, ConnectionLost):
                self._stop_sync(session, SYNC_FAILED_TEXT.format(path=shown_name(target), where="the server", why=shown_name(str(error))), True)
            else:
                self._failed(session, error)

        session.worker.submit(lambda: remote.make_folder(target), lambda _made: self._sync_remote_to(session, target), refused)

    @override
    def _remote_moved(self, session: Session, previous: str, folder: str) -> None:
        """The right side made a step: the left side makes the same one."""
        if previous and previous != folder:
            session.remote_fallback = ""  # moved: its folder is the person's own now
        if folder == session.sync_asked:
            session.sync_asked = ""
            return  # the answer to a step of this program, not one of the person
        if not session.sync_browsing or session is not self.current() or not previous or previous == folder:
            return
        target = matching_folder(previous, folder, self.local_side.folder, False)
        if target is None:
            self._stop_sync(session, SYNC_UNMATCHED_TEXT, True)
            return
        if not os.path.isdir(target):
            name, parent = _name_and_parent(target, True)
            if not os.path.isdir(os.path.dirname(target)):
                self._stop_sync(session, SYNC_NO_PARENT_TEXT.format(where="this computer", parent=parent, name=name), True)
                return
            if QMessageBox.question(self, WINDOW_TITLE, box_text(MAKE_LOCAL_TEXT.format(name=name, parent=parent))) != QMessageBox.StandardButton.Yes:
                self._stop_sync(session, SYNC_NOT_MADE_TEXT.format(path=shown_name(target)), False)
                return
            try:
                localfiles.make_folder(os.path.dirname(target), os.path.basename(target))
            except LocalActionFailed as problem:
                self._stop_sync(session, SYNC_FAILED_TEXT.format(path=shown_name(target), where="this computer", why=problem), True)
                return
        self.quiet_local = True
        try:
            went = self.local_side.go_to(target)
        finally:
            self.quiet_local = False
        if not went:
            self._stop_sync(session, SYNC_FAILED_TEXT.format(path=shown_name(target), where="this computer", why=CANT_OPEN_TEXT), True)

    @override
    def _sync_ends(self, session: Session) -> None:
        """Its connection was lost, closed or is logging in again: synchronized browsing turns off, and only the person turns it on again."""
        if not session.sync_browsing:
            return
        session.sync_browsing = False
        session.sync_asked = ""
        if session is self.current():
            self._tab_shown()
        self.status.showMessage(SYNC_ENDED_TEXT.format(name=session.connection.name))

    # --- starting folders (FH-06) ------------------------------------------------

    @override
    def _remember_folders(self, session: Session) -> None:
        """When its connection remembers its last folders, the folders of this tab become its starting folders."""
        try:
            local = "" if session.local_folder == session.local_fallback else session.local_folder  # empty: the starting folder stays
            remote = "" if session.remote_folder == session.remote_fallback else session.remote_folder
            saved = saved_connections.remember_folders(session.connection.name, local, remote)
        except OSError:
            self.status.showMessage(FOLDERS_NOT_KEPT_TEXT.format(name=session.connection.name))
            return
        if saved is not None and self.connection_list is not None:
            self.connection_list.folders_remembered(saved)
