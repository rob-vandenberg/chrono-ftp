from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QSplitter, QStyle, QTreeView, QVBoxLayout, QWidget

from chrono_ftp.config.settings import TREE_ABOVE, TREE_HIDDEN
from chrono_ftp.connections.base import RemoteEntry, joined, parent_of, shown_name
from chrono_ftp.core import colours
from chrono_ftp.engine.browsing import typed_remote_path
from chrono_ftp.ui.sidebar import FolderHistory, NavigationBar, action_bar, button_row
from chrono_ftp.ui.filelist import COLUMNS, FROM_LOCAL, FROM_OUTSIDE, FROM_REMOTE, FileList, mask_text, summary_text
from chrono_ftp.ui.pathbar import PathBar
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "remoteside.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: The path is a row of clickable folder parts, its hover text names user@host; a click on its empty part or Ctrl+L types a
#          path, read as Ctrl+O reads it; a click on a part gives this side the focus (FH-01); the path bar keeps the real path; on a
#          dropped tab the path can't be typed in or clicked, its hover text says to reconnect
# v0.0.33: Not connected: the darker row colour of the left side, light text and the usual selection (D-007)
# v0.0.12: The count line is shown as written, never read as HTML
# v0.0.9: A connection without a colour looks like the left side
# v0.0.7: One row of buttons
# v0.0.6: The shared file list; a path travels as an object, so a name that is not valid UTF-8 stays what it is
# v0.0.5: The folder tree and file list of a live connection in its colour, loaded in the background (D-007, D-019)
# v0.0.3: Initial: the right side, always a server: plain grey while there is no connection (OR-1, OR-3)

NOT_CONNECTED_TEXT: str = "Not connected"
TREE_WIDTH: int = 180
LIST_WIDTH: int = 420
PATH_ROLE: int = Qt.ItemDataRole.UserRole + 1
LOADED_ROLE: int = Qt.ItemDataRole.UserRole + 2
ROOT_FOLDER: str = "/"
DROPPED_TEXT: str = "The connection was lost. Reconnect first to open a folder here."
PATH_HINT: str = "Click a folder to open it; click the empty part, or press Ctrl+L, to type a path."


@dataclass(frozen=True)
class Place:
    """A folder of the tree. A plain str would pass through Qt, which changes a name that is not valid UTF-8."""

    path: str


def remote_parts(folder: str) -> list[tuple[str, str]]:
    """The folder parts of a path on the server, each with the path it opens; from the root down."""
    found: list[tuple[str, str]] = [(ROOT_FOLDER, ROOT_FOLDER)]
    walked = ROOT_FOLDER
    for part in [piece for piece in folder.split("/") if piece]:
        walked = joined(walked, part)
        found.append((shown_name(part), walked))
    return found


class RemoteSide(QWidget):
    """The right side. Without a connection its folder tree and file list are empty and plain grey."""

    folder_requested = Signal(object)  # the person wants to see this folder
    children_requested = Signal(object)  # the tree opened this folder for the first time
    file_opened = Signal(object)
    selection_changed = Signal()
    refresh_wanted = Signal()
    filter_wanted = Signal()
    path_typed = Signal(object)  # a path typed in the path field, read as Ctrl+O reads it

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.navigation = NavigationBar()
        self.actions_bar = action_bar()
        self.path_bar = PathBar()
        self.path_line = self.path_bar.line  # the field to type a path in
        self.count_label = plain_label()
        self.history = FolderHistory()
        self.home_folder: str = ""
        self.folder: str = ""
        self._walk: tuple[str, str] | None = None  # Back or Forward on its way: its folder; the history changes when the folder is there
        self.show_hidden: bool = True
        self._filling: bool = False  # the tree moves by itself while it follows the list
        self.tree_model = QStandardItemModel(0, 1, self)
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_list = FileList(len(COLUMNS), FROM_REMOTE, frozenset({FROM_LOCAL, FROM_OUTSIDE, FROM_REMOTE}))
        self.path_bar.next_focus = self.file_list
        self.set_colour(None)
        self.parts = QSplitter(Qt.Orientation.Horizontal)
        self.parts.setChildrenCollapsible(False)
        self.parts.addWidget(self.tree)
        self.parts.addWidget(self.file_list)
        self.parts.setStretchFactor(1, 1)
        self.parts.setSizes([TREE_WIDTH, LIST_WIDTH])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        layout.addLayout(button_row(self.navigation, self.actions_bar))
        layout.addWidget(self.path_bar)
        layout.addWidget(self.parts, 1)
        layout.addWidget(self.count_label)
        tree_selection = self.tree.selectionModel()
        if tree_selection is not None:
            tree_selection.currentChanged.connect(self._tree_moved)  # a click and the arrow keys alike
        self.tree.expanded.connect(self._tree_expanded)
        self.file_list.opened.connect(self._entry_opened)
        self.file_list.selection_changed.connect(self.selection_changed)
        self.file_list.selection_changed.connect(self._show_count)
        self.navigation.wanted.connect(self._button_pressed)
        self.path_line.returnPressed.connect(self._path_typed)
        self.path_bar.folder_chosen.connect(self._part_chosen)
        self.navigation.show_state(self.history, False, False)
        self.set_login("")
        self.path_bar.show_path("", [], NOT_CONNECTED_TEXT, NOT_CONNECTED_TEXT)
        self.path_bar.typing_allowed = False  # nowhere to go until a connection is live

    def set_tree_place(self, place: str) -> None:
        self.tree.setVisible(place != TREE_HIDDEN)
        self.parts.setOrientation(Qt.Orientation.Vertical if place == TREE_ABOVE else Qt.Orientation.Horizontal)

    def set_show_hidden(self, show: bool) -> None:
        self.show_hidden = show
        self.file_list.set_show_hidden(show)

    def set_colour(self, colour: str | None) -> None:
        """colour None: no connection, plain grey. Empty: a live connection without a colour, neutral like the left side. Text and selected
        rows stay readable on any colour (D-007)."""
        if colour == "":
            background, text, selected, on_selected = colours.PANEL, colours.TEXT, colours.SELECTION, colours.ON_SELECTION
        elif colour is None:
            background, text, selected, on_selected = colours.NOT_CONNECTED, colours.ON_NOT_CONNECTED, colours.SELECTION, colours.ON_SELECTION
        else:
            background, text = colour, colours.readable_text(colour)
            selected, on_selected = text, background
        self.file_list.setAlternatingRowColors(colour == "")  # as on the left side
        for view in (self.tree, self.file_list):
            palette = QPalette(view.palette())
            palette.setColor(QPalette.ColorRole.Base, QColor(background))
            palette.setColor(QPalette.ColorRole.Text, QColor(text))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(selected))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(on_selected))
            view.setPalette(palette)

    def _button_pressed(self, name: str) -> None:
        if name == "back" and self.history.behind:
            self._walk = ("back", self.history.behind[-1])
            self.folder_requested.emit(self.history.behind[-1])
        elif name == "forward" and self.history.ahead:
            self._walk = ("forward", self.history.ahead[-1])
            self.folder_requested.emit(self.history.ahead[-1])
        elif name == "parent":
            self.folder_requested.emit(parent_of(self.folder))
        elif name == "root":
            self.folder_requested.emit(ROOT_FOLDER)
        elif name == "home" and self.home_folder:
            self.folder_requested.emit(self.home_folder)
        elif name == "refresh":
            self.refresh_wanted.emit()
        elif name == "filter":
            self.filter_wanted.emit()

    def _path_typed(self) -> None:
        """Enter in the path field: read as Ctrl+O reads it (~ is the home folder of the login, a relative path starts here); the window
        opens it, or says calmly why not."""
        typed = self.path_bar.typed_path()
        self.path_bar.stop_typing()
        if typed and self.folder:
            self.path_typed.emit(typed_remote_path(self.folder, typed, self.home_folder))

    def _part_chosen(self, path: object) -> None:
        """A click on a folder part opens it, and this side takes the keyboard focus, as with a click in its list."""
        self.file_list.setFocus(Qt.FocusReason.MouseFocusReason)
        if isinstance(path, str) and path != self.folder:
            self.folder_requested.emit(path)

    def set_live(self, live: bool) -> None:
        """A dropped connection: the path can't be typed in or clicked, as while not connected, and its hover text says to reconnect
        first; once the tab is live again both work."""
        self.path_bar.typing_allowed = live and bool(self.folder)
        if not live:
            self.path_bar.stop_typing()
            if self.folder:
                self.path_bar.setToolTip(DROPPED_TEXT)

    def set_login(self, login: str) -> None:
        """user@host of the connection shown, in the hover text of the path; empty while not connected."""
        self.path_bar.setToolTip(f"{login}\n{PATH_HINT}" if login else NOT_CONNECTED_TEXT)

    def _show_count(self) -> None:
        self.count_label.setText(summary_text(self.selected_entries(), self.file_list.visible_entries()) + mask_text(self.file_list.name_mask) if self.folder else "")

    def clear(self) -> None:
        self.folder = ""
        self.history = FolderHistory()
        self._walk = None
        self.home_folder = ""
        self.navigation.show_state(self.history, False, False)
        self.path_bar.stop_typing()
        self.path_bar.show_path("", [], NOT_CONNECTED_TEXT, NOT_CONNECTED_TEXT)
        self.path_bar.typing_allowed = False
        self.tree_model.removeRows(0, self.tree_model.rowCount())
        self.file_list.show_entries([], False, False)
        self.set_colour(None)

    def _folder_icon_item(self, text: str, path: str) -> QStandardItem:
        style = self.style()
        item = QStandardItem(style.standardIcon(QStyle.StandardPixmap.SP_DirIcon), shown_name(text))
        item.setData(Place(path), PATH_ROLE)
        return item

    def _tree_item(self, path: str) -> QStandardItem:
        """The tree's item of this folder, with its parents made as they are needed."""
        if self.tree_model.rowCount() == 0:
            self.tree_model.appendRow(self._folder_icon_item(ROOT_FOLDER, ROOT_FOLDER))
        item = cast(QStandardItem, self.tree_model.item(0))
        walked = ROOT_FOLDER
        for part in [piece for piece in path.split("/") if piece]:
            walked = joined(walked, part)
            found = next((child for row in range(item.rowCount()) if (child := item.child(row)) is not None and child.data(PATH_ROLE) == Place(walked)), None)
            if found is None:
                found = self._folder_icon_item(part, walked)
                item.appendRow(found)
            item = found
        return item

    def fill_tree(self, path: str, entries: list[RemoteEntry]) -> None:
        """Merged with what the tree holds, so an open path below this folder stays open."""
        item = self._tree_item(path)
        names = sorted((entry.name for entry in entries if entry.is_folder and (self.show_hidden or not entry.name.startswith("."))), key=lambda name: shown_name(name).casefold())
        kept: set[str] = set()
        for row in reversed(range(item.rowCount())):  # rows that stay are left in place: they keep their open state and the selection
            child = item.child(row)
            place = child.data(PATH_ROLE) if child is not None else None
            if isinstance(place, Place) and PurePosixPath(place.path).name in names:
                kept.add(PurePosixPath(place.path).name)
            else:
                item.removeRow(row)
        for position, name in enumerate(names):
            if name not in kept:
                child = self._folder_icon_item(name, joined(path, name))
                child.appendRow(QStandardItem(""))  # a stand-in, so the folder shows it can be opened
                item.insertRow(position, [child])
        item.setData(True, LOADED_ROLE)
        self.tree.scrollTo(self.tree.currentIndex())

    def fill_children(self, path: str, entries: list[RemoteEntry]) -> None:
        """A folder opened in the tree. A row that goes away makes Qt move the tree's current folder, which must not move the list."""
        self._filling = True
        self.fill_tree(path, entries)
        if self.folder:
            self.tree.setCurrentIndex(self._tree_item(self.folder).index())
        self._filling = False

    def _open_down_to(self, item: QStandardItem) -> None:
        walker: QStandardItem | None = item
        while walker is not None:
            self.tree.setExpanded(walker.index(), True)
            walker = walker.parent()

    def show_folder(self, path: str, entries: list[RemoteEntry]) -> None:
        same = path == self.folder
        if self.folder and not same:
            if self._walk == ("back", path):
                self.history.back(self.folder)
            elif self._walk == ("forward", path):
                self.history.forward(self.folder)
            else:
                self.history.left(self.folder)
        self._walk = None
        self._filling = True
        self.folder = path
        self.file_list.folder = path
        self.path_bar.show_path(path, remote_parts(path))
        self.path_bar.typing_allowed = True
        self.fill_tree(path, entries)
        current = self._tree_item(path)
        self._open_down_to(current)
        self.tree.setCurrentIndex(current.index())
        self.tree.scrollTo(current.index())
        self.file_list.show_entries(entries, path != ROOT_FOLDER, same)
        self._filling = False
        self.navigation.show_state(self.history, path != ROOT_FOLDER, True)
        self._show_count()

    def selected_entries(self) -> list[RemoteEntry]:
        return self.file_list.selected_entries()

    def _tree_moved(self, index: QModelIndex) -> None:
        place = index.data(PATH_ROLE)
        if isinstance(place, Place) and place.path != self.folder and not self._filling:
            self.folder_requested.emit(place.path)

    def _tree_expanded(self, index: QModelIndex) -> None:
        place = index.data(PATH_ROLE)
        if isinstance(place, Place) and not index.data(LOADED_ROLE):
            self.children_requested.emit(place.path)

    def _entry_opened(self, entry: RemoteEntry | None) -> None:
        if entry is None:
            self.folder_requested.emit(parent_of(self.folder))
        elif entry.is_folder:
            self.folder_requested.emit(joined(self.folder, entry.name))
        else:
            self.file_opened.emit(entry)
