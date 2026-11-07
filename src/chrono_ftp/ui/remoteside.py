import datetime
from typing import cast

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QLabel, QSplitter, QStyle, QTreeView, QVBoxLayout, QWidget

from chrono_ftp.config.settings import TREE_ABOVE, TREE_HIDDEN
from chrono_ftp.connections.base import RemoteEntry, joined, parent_of
from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "remoteside.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: The folder tree and file list of a live connection in its colour, loaded in the background (D-007, D-019)
# v0.0.3: Initial: the right side, always a server: plain grey while there is no connection (OR-1, OR-3)

NOT_CONNECTED_TEXT: str = "Not connected"
COLUMNS: tuple[str, ...] = ("Name", "Size", "Date Modified", "Permissions", "Owner", "Group")
NAME_COLUMN_WIDTH: int = 260
PATH_ROLE: int = Qt.ItemDataRole.UserRole + 1
LOADED_ROLE: int = Qt.ItemDataRole.UserRole + 2
ENTRY_ROLE: int = Qt.ItemDataRole.UserRole + 3
PARENT_ENTRY: str = ".."
ROOT_FOLDER: str = "/"


def size_text(size: int) -> str:
    amount = float(size)
    for unit in ("bytes", "KB", "MB", "GB"):
        if amount < 1024:
            return f"{int(amount)} {unit}" if unit == "bytes" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def date_text(moment: float | None) -> str:
    if moment is None:
        return ""
    try:
        return datetime.datetime.fromtimestamp(moment).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""  # a date from the server is input (OR-15)


class RemoteSide(QWidget):
    """The right side. Without a connection its folder tree and file list are empty and plain grey."""

    folder_requested = Signal(str)  # the person wants to see this folder
    children_requested = Signal(str)  # the tree opened this folder for the first time
    selection_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path_label = QLabel(NOT_CONNECTED_TEXT)
        self.folder: str = ""
        self._filling: bool = False  # the tree moves by itself while it follows the list
        self.tree_model = QStandardItemModel(0, 1, self)
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setHeaderHidden(True)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files = QStandardItemModel(0, len(COLUMNS), self)
        self.files.setHorizontalHeaderLabels(list(COLUMNS))
        self.file_list = QTreeView()
        self.file_list.setModel(self.files)
        self.file_list.setRootIsDecorated(False)
        self.file_list.setItemsExpandable(False)
        self.file_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.file_list.setColumnWidth(0, NAME_COLUMN_WIDTH)
        self.set_colour(None)
        self.parts = QSplitter(Qt.Orientation.Horizontal)
        self.parts.setChildrenCollapsible(False)
        self.parts.addWidget(self.tree)
        self.parts.addWidget(self.file_list)
        self.parts.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.path_label)
        layout.addWidget(self.parts, 1)
        tree_selection = self.tree.selectionModel()
        if tree_selection is not None:
            tree_selection.currentChanged.connect(self._tree_moved)  # a click and the arrow keys alike
        self.tree.expanded.connect(self._tree_expanded)
        self.file_list.activated.connect(self._entry_opened)
        selection = self.file_list.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self.selection_changed)

    def set_tree_place(self, place: str) -> None:
        self.tree.setVisible(place != TREE_HIDDEN)
        self.parts.setOrientation(Qt.Orientation.Vertical if place == TREE_ABOVE else Qt.Orientation.Horizontal)

    def set_colour(self, colour: str | None) -> None:
        """colour None: no connection, plain grey. Text and selected rows stay readable on any colour (D-007)."""
        background = colour or colours.NOT_CONNECTED
        text = colours.readable_text(background) if colour else colours.ON_NOT_CONNECTED
        for view in (self.tree, self.file_list):
            palette = QPalette(view.palette())
            palette.setColor(QPalette.ColorRole.Base, QColor(background))
            palette.setColor(QPalette.ColorRole.Text, QColor(text))
            palette.setColor(QPalette.ColorRole.Highlight, QColor(text))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(background))
            view.setPalette(palette)

    def clear(self) -> None:
        self.folder = ""
        self.path_label.setText(NOT_CONNECTED_TEXT)
        self.tree_model.removeRows(0, self.tree_model.rowCount())
        self.files.removeRows(0, self.files.rowCount())
        self.set_colour(None)

    def _folder_icon_item(self, text: str, path: str) -> QStandardItem:
        style = self.style()
        item = QStandardItem(style.standardIcon(QStyle.StandardPixmap.SP_DirIcon), text)
        item.setData(path, PATH_ROLE)
        return item

    def _tree_item(self, path: str) -> QStandardItem:
        """The tree's item of this folder, with its parents made as they are needed."""
        if self.tree_model.rowCount() == 0:
            self.tree_model.appendRow(self._folder_icon_item(ROOT_FOLDER, ROOT_FOLDER))
        item = cast(QStandardItem, self.tree_model.item(0))
        walked = ROOT_FOLDER
        for part in [piece for piece in path.split("/") if piece]:
            walked = joined(walked, part)
            found = next((child for row in range(item.rowCount()) if (child := item.child(row)) is not None and child.data(PATH_ROLE) == walked), None)
            if found is None:
                found = self._folder_icon_item(part, walked)
                item.appendRow(found)
            item = found
        return item

    def fill_tree(self, path: str, entries: list[RemoteEntry]) -> None:
        """Merged with what the tree holds, so an open path below this folder stays open."""
        item = self._tree_item(path)
        names = sorted((entry.name for entry in entries if entry.is_folder), key=str.casefold)
        for row in reversed(range(item.rowCount())):  # rows that stay are left in place: they keep their open state and the selection
            child = item.child(row)
            if child is None or not isinstance(child.data(PATH_ROLE), str) or child.text() not in names:
                item.removeRow(row)
        present = {child.text() for row in range(item.rowCount()) if (child := item.child(row)) is not None}
        for position, name in enumerate(names):
            if name not in present:
                child = self._folder_icon_item(name, joined(path, name))
                child.appendRow(QStandardItem(""))  # a stand-in, so the folder shows it can be opened
                item.insertRow(position, [child])
        item.setData(True, LOADED_ROLE)
        self.tree.scrollTo(self.tree.currentIndex())

    def _open_down_to(self, item: QStandardItem) -> None:
        walker: QStandardItem | None = item
        while walker is not None:
            self.tree.setExpanded(walker.index(), True)
            walker = walker.parent()

    def show_folder(self, path: str, entries: list[RemoteEntry]) -> None:
        keep = {entry.name for entry in self.selected_entries()} if path == self.folder else set[str]()
        self._filling = True
        self._fill_folder(path, entries)
        self._filling = False
        selection = self.file_list.selectionModel()
        if selection is not None:
            for row in range(self.files.rowCount()):
                name = self.files.item(row, 0)
                if name is not None and name.text() in keep:
                    selection.select(name.index(), QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)

    def _fill_folder(self, path: str, entries: list[RemoteEntry]) -> None:
        self.folder = path
        self.path_label.setText(path)
        self.fill_tree(path, entries)
        current = self._tree_item(path)
        self._open_down_to(current)
        self.tree.setCurrentIndex(current.index())
        self.tree.scrollTo(current.index())
        self.files.removeRows(0, self.files.rowCount())
        if path != ROOT_FOLDER:
            row = [QStandardItem(PARENT_ENTRY), *(QStandardItem("") for _column in COLUMNS[1:])]
            self.files.appendRow(row)
        style = self.style()
        for entry in sorted(entries, key=lambda item: (not item.is_folder, item.name.casefold())):
            name = QStandardItem(entry.name)
            name.setData(entry, ENTRY_ROLE)
            if entry.is_folder:
                name.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_DirIcon))
            size = "" if entry.is_folder else size_text(entry.size)
            self.files.appendRow([name, QStandardItem(size), QStandardItem(date_text(entry.modified)), QStandardItem(entry.permissions), QStandardItem(entry.owner), QStandardItem(entry.group)])

    def selected_entries(self) -> list[RemoteEntry]:
        selection = self.file_list.selectionModel()
        if selection is None:
            return []
        found = [index.data(ENTRY_ROLE) for index in selection.selectedRows(0)]
        return [entry for entry in found if isinstance(entry, RemoteEntry)]

    def _tree_moved(self, index: QModelIndex) -> None:
        path = index.data(PATH_ROLE)
        if isinstance(path, str) and path != self.folder and not self._filling:
            self.folder_requested.emit(path)

    def _tree_expanded(self, index: QModelIndex) -> None:
        path = index.data(PATH_ROLE)
        if isinstance(path, str) and not index.data(LOADED_ROLE):
            self.children_requested.emit(path)

    def _entry_opened(self, index: QModelIndex) -> None:
        first = index.siblingAtColumn(0)
        if first.data() == PARENT_ENTRY:
            self.folder_requested.emit(parent_of(self.folder))
            return
        entry = first.data(ENTRY_ROLE)
        if isinstance(entry, RemoteEntry) and entry.is_folder:
            self.folder_requested.emit(joined(self.folder, entry.name))
