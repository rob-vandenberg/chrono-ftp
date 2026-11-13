"""Selection and keys in the main window (block FG): the selection keys and menu, the two-pane selection mode, the key set, "This File
Only", the size format and natural sort, the hidden files, the clipboard, the path in the title bar, the right-button drag, pausing the
automatic refresh and clearing the cached listings."""

from collections.abc import Callable
from functools import partial
from dataclasses import replace
from pathlib import Path
from typing import override

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCursor, QGuiApplication, QKeySequence
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox, QWidget

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.bookmarks import LOCAL, REMOTE
from chrono_ftp.config.settings import CLASSIC_KEYS, TITLE_FULL, TITLE_NONE
from chrono_ftp.connections.base import RemoteConnection, RemoteEntry, joined, parent_of, shown_name
from chrono_ftp.core.appinfo import program_version
from chrono_ftp.core.connection import FTP
from chrono_ftp.core.errors import ConnectionLost, RemoteActionFailed
from chrono_ftp.core.masks import MaskError, parse
from chrono_ftp.engine import commands
from chrono_ftp.engine.listing import clipboard_names
from chrono_ftp.ui.filelist import FROM_LOCAL, FROM_OUTSIDE, FROM_REMOTE, PARENT_ENTRY, FileList
from chrono_ftp.ui.localside import LocalSide
from chrono_ftp.ui.maskdialog import SELECT, UNSELECT, MaskDialog
from chrono_ftp.ui.menus import open_menu
from chrono_ftp.ui.plaintext import box_text, fitting
from chrono_ftp.ui.remoteside import RemoteSide
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import SETTINGS_NOT_KEPT_TEXT, WINDOW_TITLE, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowselection.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: The menu of each list also holds Mark Differences, and the left one Synchronize with another folder on this computer (FF-12, FF-14)
# v0.0.42: Calculate Folder Sizes (Shift+Space) in the right-click menu of a list (FE-03)
# v0.0.41: Select by Mask (Num+) and Unselect by Mask (Num−) on the list with the focus, also in the Select submenu, with the masks
#          typed before (FD-05); a greyed Num+ or Num− does nothing and the status bar says why
# v0.0.40: Initial: block FG: the selection keys and their menu (FG-02), the two-pane selection mode (FG-03), the key set with F6,
#          Shift+F6, Alt+F7 and the sort keys (FG-04), This File Only (FG-05), the size format (FG-06), natural sort (FG-08), the hidden
#          files on Ctrl+Alt+H and from the status line (FG-09), Back and Forward on Alt+Left and Alt+Right (FG-10), the clipboard
#          (FG-11), the path in the title bar (FG-12), the right-button drag and its menu (FG-14), Pause Automatic Refresh and Clear Cached
#          Listings (FG-15), Ctrl+R (FG-16)

SELECT_ACTIONS: tuple[tuple[str, str, str], ...] = (  # name, text, key
    ("select_all", "Select All", "Ctrl+A"),
    ("invert", "Invert Selection", "Ctrl+I"),
    ("clear", "Clear Selection", "Ctrl+Shift+A"),
    ("same_extension", "Select the Same Extension", ""),
    ("restore", "Restore the Previous Selection", ""),
    ("mark", "Mark and Move Down", "Insert"),
    ("select_mask", "Select by Mask…", "Num++"),
    ("unselect_mask", "Unselect by Mask…", "Num+-"),
)
COPY_ACTIONS: tuple[tuple[str, str, str], ...] = (
    ("copy_path", "Copy the Folder Path", ""),
    ("copy_names", "Copy the Selected Names", "Ctrl+Shift+C"),
    ("copy_full", "Copy the Full Paths", ""),
)
SORT_KEYS: tuple[tuple[str, int, bool], ...] = (  # key, column, by extension (FG-04): the classic two-pane sort keys
    ("Ctrl+F3", 0, False),
    ("Ctrl+F4", 0, True),
    ("Ctrl+F5", 2, False),
    ("Ctrl+F6", 1, False),
    ("Ctrl+F7", 3, False),
    ("Ctrl+F8", 4, False),
    ("Ctrl+F9", 5, False),
)
EXTENSION_KEY: str = "Ctrl+F4"  # the classic key set only: the file-manager key set leaves it free
FIND_KEYS: dict[str, str] = {CLASSIC_KEYS: "Alt+F7"}  # Find Files; any other key set: Ctrl+F
FILE_MANAGER_FIND_KEY: str = "Ctrl+F"
SELECT_MENU_TEXT: str = "Select"
COPY_MENU_TEXT: str = "Copy to Clipboard"
ONLY_MENU_TEXT: str = "This File Only"
ONLY_TEXTS: dict[str, str] = {
    "upload": "Upload This File Only",
    "download": "Download This File Only",
    "local_edit": "Edit This File Only",
    "edit": "Edit This File Only",
    "local_delete": "Delete This File Only",
    "delete": "Delete This File Only",
    "local_properties": "Properties of This File Only",
    "properties": "Properties of This File Only",
}
LOCAL_ONLY: tuple[str, ...] = ("upload", "local_edit", "local_delete", "local_properties")
REMOTE_ONLY: tuple[str, ...] = ("download", "edit", "delete", "properties")
MOVE_ACROSS_TEXTS: dict[str, str] = {LOCAL: "Move to the Server", REMOTE: "Move to This Computer"}
MOVE_ACROSS_WHY: str = "Select what to move, with a live connection and a folder shown on the server."
NOTHING_UNDER_CURSOR_TEXT: str = "There is no file under the cursor."
NOT_A_FILE_TEXT: str = "The entry under the cursor is a folder."
NOT_LIVE_TEXT: str = "The connection is not live. Reconnect first."
NO_FOCUSED_FILE_TEXT: str = "Put the cursor on a file first: its extension is taken."
NOTHING_TO_RESTORE_TEXT: str = "No earlier selection in this folder to bring back."
NOTHING_SELECTED_TEXT: str = "Nothing is selected."
EMPTY_LIST_TEXT: str = "This folder shows no entries."
NO_FOLDER_TEXT: str = "No folder is shown on this side."
COPIED_TEXT: tuple[str, str] = ("Copied 1 line to the clipboard.", "Copied {count} lines to the clipboard.")
HIDDEN_ON_TEXT: str = "Hidden files are shown now. Ctrl+Alt+H hides them again."
HIDDEN_OFF_TEXT: str = "Hidden files are hidden now. Ctrl+Alt+H shows them again."
COPY_HERE_TEXT: str = "Copy Here"
MOVE_HERE_TEXT: str = "Move Here"
CANCEL_TEXT: str = "Cancel"
DRAG_MOVE_OFF_TEXT: str = "Moving by dragging is switched off in Settings, Panels."
COPY_REFUSED_TEXT: str = "Not copied on the server:\n{lines}"
PAUSE_TEXT: str = "Pause Automatic Refresh"
PAUSE_OFF_TEXT: str = "The automatic refresh is off in Settings, Panels."
CLEAR_CACHE_TEXT: str = "Clear Cached Listings"
CLEARED_TEXT: str = "{name}: the folders read before are forgotten; the folder shown is read again."
PAUSED_TEXT: str = "{name}: the automatic refresh waits until you switch it on again."
RESUMED_TEXT: str = "{name}: the automatic refresh goes on."


class SelectionPart(WindowBase):
    """Selection and keys in the main window (block FG)."""

    def _build_selection(self) -> None:
        self.list_actions = {}
        for side_name, side in ((LOCAL, self.local_side), (REMOTE, self.remote_side)):
            view = side.file_list
            for name, text, key in (*SELECT_ACTIONS, *COPY_ACTIONS):
                self._list_action(f"{side_name}_{name}", text, key, view, self._selection_handler(side_name, name))
            for key, column, by_extension in SORT_KEYS:
                if column < view.files.column_count:
                    self._list_action(f"{side_name}_sort_{key}", f"Sort by {key}", key, view, lambda view=view, column=column, by_extension=by_extension: view.sort_by(column, by_extension))
            for name, key in (("back", "Alt+Left"), ("forward", "Alt+Right")):
                walk = self._list_action(f"{side_name}_{name}", side.navigation.buttons[name].text(), key, view, lambda side=side, name=name: self._walk(side, name))
                side.tree.addAction(walk)  # in the folder tree too, never in the path field (OR-21)
            refresh = self._list_action(f"{side_name}_refresh", "Refresh", "Ctrl+R", side, self._refresh_handler(side_name))  # next to F5, on the whole side as F5
            refresh.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            hidden = self._list_action(f"{side_name}_hidden", "Show or Hide Hidden Files", "Ctrl+Alt+H", side, self.toggle_hidden)
            hidden.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            side.status.hidden_clicked.connect(self.toggle_hidden)
            view.keypad_refused.connect(partial(self._keypad_refused, side_name))
        self._list_action(f"{LOCAL}_move_across", MOVE_ACROSS_TEXTS[LOCAL], "F6", self.local_side.file_list, lambda: self.move_across(True))
        self._list_action(f"{REMOTE}_move_across", MOVE_ACROSS_TEXTS[REMOTE], "F6", self.remote_side.file_list, lambda: self.move_across(False))
        self.local_side.file_list.right_dropped.connect(self._right_dropped_on_local)
        self.remote_side.file_list.right_dropped.connect(self._right_dropped_on_remote)
        search = self.actions_by_name["search"]
        search.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.addAction(search)  # its key works with the top bar hidden too (D-071)

    def _list_action(self, name: str, text: str, key: str, owner: QWidget, handler: Callable[[], None]) -> QAction:
        action = QAction(text, self)
        if key:
            action.setShortcut(QKeySequence(key))
        action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)  # the list with the focus only: never a field (OR-21)
        owner.addAction(action)
        action.triggered.connect(lambda _checked=False: handler())  # never the checked flag into a handler with defaults
        self.list_actions[name] = action
        return action

    def _side(self, side_name: str) -> LocalSide | RemoteSide:
        return self.local_side if side_name == LOCAL else self.remote_side

    def _selection_handler(self, side_name: str, name: str) -> Callable[[], None]:
        view = self._side(side_name).file_list
        handlers: dict[str, Callable[[], None]] = {
            "select_all": view.select_all_entries,
            "invert": view.invert_selection,
            "clear": view.clear_marks,
            "same_extension": view.select_same_extension,
            "restore": view.restore_selection,
            "mark": view.mark_and_move_down,
            "select_mask": lambda: self.select_by_mask(side_name, True),
            "unselect_mask": lambda: self.select_by_mask(side_name, False),
            "copy_path": lambda: self.copy_to_clipboard(side_name, "path"),
            "copy_names": lambda: self.copy_to_clipboard(side_name, "names"),
            "copy_full": lambda: self.copy_to_clipboard(side_name, "full"),
        }
        return handlers[name]

    def select_by_mask(self, side_name: str, select: bool) -> None:
        """Num+ or Num− on the list with the focus (OR-21): the small window with the mask field and the masks typed before (FD-05)."""
        view = self._side(side_name).file_list
        history = self.settings.mask_history
        dialog = MaskDialog(SELECT if select else UNSELECT, history[0] if history else "", history, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted.value
        mask, folders_too = dialog.chosen_mask(), dialog.folders.isChecked()
        dialog.deleteLater()
        if not accepted or not mask:
            return
        try:
            parsed = parse(mask)
        except MaskError:
            return  # the window takes no mask it can't read
        view.select_by_mask(parsed, select, folders_too)
        self.remember_mask(mask)
        view.setFocus()

    def _keypad_refused(self, side_name: str, plus: bool) -> None:
        """Num+ or Num− while its action is greyed: nothing happens, and the status bar says why (OR-20)."""
        action = self.list_actions[f"{side_name}_{'select_mask' if plus else 'unselect_mask'}"]
        if not action.isEnabled() and action.toolTip():
            self.status.showMessage(action.toolTip())

    def _refresh_handler(self, side_name: str) -> Callable[[], None]:
        return self.local_side.refresh if side_name == LOCAL else self.refresh_remote

    def refresh_remote(self) -> None:
        session = self.current()
        if session is not None and session.alive and session.remote_folder:
            self._reload(session)

    def _walk(self, side: LocalSide | RemoteSide, name: str) -> None:
        button = side.navigation.buttons[name]
        if button.isEnabled():
            button.trigger()

    # --- the settings of block FG ---------------------------------------------------

    @override
    def _apply_selection_settings(self) -> None:
        settings = self.settings
        for side in (self.local_side, self.remote_side):
            side.file_list.set_two_pane(settings.two_pane_selection)
            side.file_list.files.set_display(settings.size_format, settings.natural_sort)
        classic = settings.key_set == CLASSIC_KEYS
        self.actions_by_name["search"].setShortcut(QKeySequence(FIND_KEYS.get(settings.key_set, FILE_MANAGER_FIND_KEY)))
        for side_name in (LOCAL, REMOTE):
            self.list_actions[f"{side_name}_sort_{EXTENSION_KEY}"].setShortcut(QKeySequence(EXTENSION_KEY if classic else ""))
        self._show_title()

    def _show_title(self) -> None:
        """The version, and after it the folder of the right side, short or full, as the person chose (FG-12)."""
        title = f"{WINDOW_TITLE} {program_version()}"
        session = self.current()
        folder = session.remote_folder if session is not None else ""
        if folder and self.settings.title_path != TITLE_NONE:
            shown = folder if self.settings.title_path == TITLE_FULL else folder.rstrip("/").rsplit("/", 1)[-1] or "/"
            title += f" - {shown_name(shown)}"
        self.setWindowTitle(title)

    # --- greyed or not (OR-20) -------------------------------------------------------

    @override
    def _update_selection_actions(self) -> None:
        session = self.current()
        alive = session is not None and session.alive
        for side_name in (LOCAL, REMOTE):
            side = self._side(side_name)
            view = side.file_list
            shown = side_name == LOCAL or bool(self.remote_side.folder)
            entries = bool(view.visible_entries()) and shown
            marked = bool(view.selected_entries())
            acting = bool(view.acting_entries())
            states: dict[str, tuple[bool, str]] = {
                "select_all": (entries, EMPTY_LIST_TEXT),
                "invert": (entries, EMPTY_LIST_TEXT),
                "clear": (marked, NOTHING_SELECTED_TEXT),
                "same_extension": (view.same_extension_wanted() is not None, NO_FOCUSED_FILE_TEXT),
                "restore": (view.can_restore(), NOTHING_TO_RESTORE_TEXT),
                "mark": (entries, EMPTY_LIST_TEXT),
                "select_mask": (entries, EMPTY_LIST_TEXT),
                "unselect_mask": (marked, NOTHING_SELECTED_TEXT),
                "copy_path": (shown, NO_FOLDER_TEXT),
                "copy_names": (acting, NOTHING_SELECTED_TEXT),
                "copy_full": (acting, NOTHING_SELECTED_TEXT),
            }
            for name, (enabled, why) in states.items():
                action = self.list_actions[f"{side_name}_{name}"]
                action.setEnabled(enabled)
                action.setToolTip("" if enabled else why)
        for side_name, transfer in ((LOCAL, "upload"), (REMOTE, "download")):
            move = self.list_actions[f"{side_name}_move_across"]
            move.setEnabled(self.actions_by_name[transfer].isEnabled())
            move.setToolTip("" if move.isEnabled() else MOVE_ACROSS_WHY)
        self.list_actions[f"{REMOTE}_refresh"].setEnabled(alive)
        self._show_title()

    # --- the right-click menu of a list ------------------------------------------------

    @override
    def _add_list_items(self, menu: QMenu, side_name: str) -> None:
        """Move to the other side, This File Only, Select and Copy to Clipboard, after the actions of the side."""
        menu.setToolTipsVisible(True)
        menu.addAction(self.list_actions[f"{side_name}_move_across"])
        menu.addAction(self.list_actions[f"{side_name}_folder_sizes"])
        menu.addAction(self.list_actions[f"{side_name}_mark_differences"])  # block FF: what differs from the other side (FF-12)
        if side_name == LOCAL:
            menu.addAction(self.list_actions[f"{LOCAL}_sync_folder"])  # block FF: the same window for two folders here (FF-14)
        menu.addSeparator()
        only = menu.addMenu(ONLY_MENU_TEXT)
        only.setToolTipsVisible(True)
        for name in LOCAL_ONLY if side_name == LOCAL else REMOTE_ONLY:
            only.addAction(self._only_action(only, side_name, name))
        select = menu.addMenu(SELECT_MENU_TEXT)
        select.setToolTipsVisible(True)
        for name, _text, _key in SELECT_ACTIONS:
            select.addAction(self.list_actions[f"{side_name}_{name}"])
        copy = menu.addMenu(COPY_MENU_TEXT)
        copy.setToolTipsVisible(True)
        for name, _text, _key in COPY_ACTIONS:
            copy.addAction(self.list_actions[f"{side_name}_{name}"])

    def _only_action(self, menu: QMenu, side_name: str, name: str) -> QAction:
        """An action on the file under the cursor alone, whatever is selected (FG-05); greyed with its reason where it can't act."""
        view = self._side(side_name).file_list
        entry = view.current_entry()
        action = QAction(ONLY_TEXTS[name], menu)
        session = self.current()
        why = ""
        if entry is None:
            why = NOTHING_UNDER_CURSOR_TEXT
        elif name in ("local_edit", "edit") and entry.is_folder:
            why = NOT_A_FILE_TEXT
        elif (side_name == REMOTE or name == "upload") and (session is None or not session.alive):
            why = NOT_LIVE_TEXT
        action.setEnabled(not why)
        action.setToolTip(why)
        handler = self.actions_by_name[name]
        action.triggered.connect(lambda: self.on_focused_only(view, handler.trigger))
        return action

    def on_focused_only(self, view: FileList, work: Callable[[], None]) -> None:
        """work reads the selection of view as the file under the cursor alone; it reads it before any window opens. The actions are
        greyed or not for that file meanwhile, so an action greyed for the selection (Edit with two files) still acts on the one file."""
        view.only_current = True
        try:
            self.update_actions()
            work()
        finally:
            view.only_current = False
            self.update_actions()

    # --- the clipboard (FG-11) -----------------------------------------------------------

    def copy_to_clipboard(self, side_name: str, what: str) -> None:
        side = self._side(side_name)
        folder = side.folder
        if not folder:
            return
        if what == "path":
            text = shown_name(folder)
        else:
            names = [entry.name for entry in side.selected_entries()]
            if not names:
                return
            text = clipboard_names(names, folder, what == "full", side_name == LOCAL)
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(text)
        count = text.count("\n") + 1
        self.status.showMessage(COPIED_TEXT[0] if count == 1 else COPIED_TEXT[1].format(count=count))

    # --- hidden files (FG-09) ---------------------------------------------------------------

    def toggle_hidden(self) -> None:
        """Ctrl+Alt+H, or a click on the hidden count: both sides, kept for next time."""
        self.settings = replace(self.settings, show_hidden=not self.settings.show_hidden)
        for side in (self.local_side, self.remote_side):
            side.set_show_hidden(self.settings.show_hidden)
        self.status.showMessage(HIDDEN_ON_TEXT if self.settings.show_hidden else HIDDEN_OFF_TEXT)
        try:
            saved_settings.save(self.settings)
        except OSError:
            self.status.showMessage(SETTINGS_NOT_KEPT_TEXT)

    # --- F6: a transfer that moves (FG-04) ------------------------------------------------------

    def move_across(self, upload: bool) -> None:
        """F6: the selection goes to the other side and is removed here once it arrived, through the transfer window with Move ticked, or
        a question where that window is off."""
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, chosen = screen
        if upload:
            self._offer(session, self.local_side.selected_paths(), folder, True, move=True)
        else:
            self._offer(session, [joined(folder, entry.name) for entry in chosen], self.local_side.folder, False, move=True)

    # --- the right-button drag (FG-14) -------------------------------------------------------------

    def drop_menu(self, copy_why: str, move_why: str) -> str:
        """The menu at the end of a right-button drag: "copy", "move" or "" for Cancel and for a click elsewhere."""
        chosen: list[str] = []
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        for key, text, why in (("copy", COPY_HERE_TEXT, copy_why), ("move", MOVE_HERE_TEXT, move_why)):
            action = QAction(text, menu)
            action.setEnabled(not why)
            action.setToolTip(why)
            action.triggered.connect(lambda _checked=False, key=key: chosen.append(key))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(QAction(CANCEL_TEXT, menu))
        open_menu(menu, QCursor.pos())
        menu.deleteLater()
        return chosen[0] if chosen else ""

    def _right_dropped_on_local(self, source: str, paths: list[str], folder_name: str) -> None:
        QTimer.singleShot(0, self, lambda: self._ask_local_drop(source, paths, folder_name))  # once the drag has let go

    def _right_dropped_on_remote(self, source: str, paths: list[str], folder_name: str) -> None:
        QTimer.singleShot(0, self, lambda: self._ask_remote_drop(source, paths, folder_name))

    def _ask_local_drop(self, _source: str, _paths: list[str], folder_name: str) -> None:
        screen = self._on_screen()
        if screen is None or not screen[0].alive:
            return
        wanted = self.drop_menu("", "" if self.settings.drag_move else DRAG_MOVE_OFF_TEXT)
        if wanted:
            folder = self.local_side.folder
            if folder_name == PARENT_ENTRY:
                folder = str(Path(folder).parent)
            elif folder_name:
                folder = str(Path(folder, folder_name))
            session, shown, chosen = screen
            self._offer(session, [joined(shown, entry.name) for entry in chosen], folder, False, move=wanted == "move")

    def _ask_remote_drop(self, source: str, paths: list[str], folder_name: str) -> None:
        screen = self._on_screen()
        if screen is None or not screen[0].alive:
            return
        session, shown, chosen = screen
        target = parent_of(shown) if folder_name == PARENT_ENTRY else joined(shown, folder_name) if folder_name else shown
        move_why = "" if self.settings.drag_move else DRAG_MOVE_OFF_TEXT
        if source == FROM_REMOTE:
            copy_why = commands.why_no_copy(session.capabilities, chosen, session.connection.protocol == FTP)
            wanted = self.drop_menu(copy_why, move_why)
            if wanted == "move":
                self._move_into(session, shown, chosen, target)
            elif wanted == "copy":
                self.copy_into(session, shown, chosen, target)
            return
        wanted = self.drop_menu("", move_why)
        if wanted and source in (FROM_LOCAL, FROM_OUTSIDE):
            self._offer(session, paths or self.local_side.selected_paths(), target, True, move=wanted == "move")

    def copy_into(self, session: Session, folder: str, chosen: list[RemoteEntry], target: str) -> None:
        """Copy Here of a right-button drag within the server: each item copied on the server into target, as Copy To does; the others
        go on after a refusal, and the refusals are told."""
        pairs = [(joined(folder, entry.name), joined(target, entry.name)) for entry in chosen]

        def work(remote: RemoteConnection) -> list[str]:
            refused: list[str] = []
            for source, copy in pairs:
                try:
                    commands.copy_on_server(remote, source, copy)
                except ConnectionLost:
                    raise
                except RemoteActionFailed as error:
                    refused.append(f"{shown_name(source.rsplit('/', 1)[-1])}: {str(error).strip()}")
            return refused

        def done(refused: object) -> None:
            self._reload(session)
            if isinstance(refused, list) and refused:
                lines = "\n".join(fitting(str(line), self) for line in refused)
                QMessageBox.information(self, WINDOW_TITLE, box_text(COPY_REFUSED_TEXT.format(lines=lines)))

        self._ask(session, work, done, refresh_after_failure=True)

    # --- the automatic refresh and the cached listings (FG-15) ------------------------------------

    @override
    def tab_refresh_items(self, menu: QMenu, session: Session) -> None:
        pause = QAction(PAUSE_TEXT, menu)
        pause.setCheckable(True)
        pause.setChecked(session.refresh_paused)
        if not self.settings.refresh_seconds:
            pause.setEnabled(False)  # greyed with its reason (OR-20)
            pause.setToolTip(PAUSE_OFF_TEXT)
        pause.toggled.connect(lambda on=False: self.pause_refresh(session, on))
        clear = QAction(CLEAR_CACHE_TEXT, menu)
        if not session.alive:
            clear.setEnabled(False)
            clear.setToolTip(NOT_LIVE_TEXT)
        clear.triggered.connect(lambda: self.clear_cached_listings(session))
        menu.setToolTipsVisible(True)
        menu.addAction(pause)
        menu.addAction(clear)

    def pause_refresh(self, session: Session, paused: bool) -> None:
        session.refresh_paused = paused
        self.status.showMessage((PAUSED_TEXT if paused else RESUMED_TEXT).format(name=session.connection.name))

    def clear_cached_listings(self, session: Session) -> None:
        """The folders read before are forgotten: the tree of the right side, and the folder shown is read again."""
        if session not in self.sessions or not session.alive:
            return
        if session is self.current():
            self.remote_side.forget_tree()
        self.status.showMessage(CLEARED_TEXT.format(name=session.connection.name))
        self._reload(session)
