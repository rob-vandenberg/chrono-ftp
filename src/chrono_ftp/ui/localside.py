import os
from pathlib import Path

from PySide6.QtCore import QDir, QFileInfo, QFileSystemWatcher, QModelIndex, Qt
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QFileSystemModel, QLabel, QSplitter, QTreeView, QVBoxLayout, QWidget

# --- Version ------------------------------------------------------------
__version__ = "localside.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial: the left side, always this computer: folder tree and file list, neutral (OR-1, OR-2, D-019)

NAME_COLUMN_WIDTH: int = 260
NO_ACCESS_TEXT: str = "no access"


class LocalSide(QWidget):
    """The left side. A folder that can't be opened is shown as closed: its list is empty and the path line says so, so the tree, the list
    and the path line always agree (OR-15). When the folder shown or one above it is removed or renamed, the side moves to the nearest
    folder above it that is still there."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path_label = QLabel()
        self.tree_model = QFileSystemModel(self)
        self.tree_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.tree_model.setRootPath(QDir.rootPath())
        self.list_model = QFileSystemModel(self)
        self.closed_model = QStandardItemModel(0, self.list_model.columnCount(), self)  # what a closed folder shows: nothing
        self.closed_model.setHorizontalHeaderLabels([str(self.list_model.headerData(column, Qt.Orientation.Horizontal)) for column in range(self.list_model.columnCount())])
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setRootIndex(self.tree_model.index(QDir.rootPath()))
        self.tree.setHeaderHidden(True)
        for column in range(1, self.tree_model.columnCount()):
            self.tree.hideColumn(column)
        self.file_list = QTreeView()
        self.file_list.setModel(self.list_model)
        self.file_list.setRootIsDecorated(False)
        self.file_list.setItemsExpandable(False)
        self.file_list.setAlternatingRowColors(True)
        self.file_list.setColumnWidth(0, NAME_COLUMN_WIDTH)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.file_list)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.path_label)
        layout.addWidget(splitter, 1)  # all the height goes to the lists
        selection = self.tree.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._tree_moved)  # a click and the arrow keys alike
        self.file_list.activated.connect(self._entry_opened)  # a double click and Return alike
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self._folder_changed)
        self.folder: str = ""
        self.shown: tuple[int, int, bool] | None = None  # what the list shows: device, inode, can be opened
        if not self.go_to(QDir.homePath()):
            self.go_to(QDir.rootPath())

    def go_to(self, path: str) -> bool:
        clean = QDir.cleanPath(path)
        information = QFileInfo(clean)
        if not information.isDir():
            return False
        self.folder = clean
        self._watch()
        self._show_folder()
        self.file_list.setCurrentIndex(QModelIndex())  # an entry of the folder before is not on screen any more
        index = self.tree_model.index(clean)
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        return True

    def _state_of_folder(self) -> tuple[int, int, bool] | None:
        try:
            status = os.stat(self.folder)
        except OSError:
            return None
        information = QFileInfo(self.folder)
        return status.st_dev, status.st_ino, information.isReadable() and information.isExecutable()

    def _show_folder(self) -> None:
        self.shown = self._state_of_folder()
        can_open = self.shown is not None and self.shown[2]
        self.path_label.setText(self.folder if can_open else f"{self.folder} ({NO_ACCESS_TEXT})")
        columns = self.file_list.header().saveState()  # a change of model forgets the widths the person dragged
        if can_open:
            at_the_top = self.folder == QDir.rootPath()  # nothing above the root, so no parent entry there
            self.list_model.setFilter(QDir.Filter.AllEntries | (QDir.Filter.NoDotAndDotDot if at_the_top else QDir.Filter.NoDot))
            if self.file_list.model() is not self.list_model:
                self.file_list.setModel(self.list_model)
            self.file_list.setRootIndex(self.list_model.setRootPath(self.folder))
        else:
            self.file_list.setModel(self.closed_model)
        self.file_list.header().restoreState(columns)

    def _watch(self) -> None:
        """The folder and every folder above it: a removal or rename higher up shows only there."""
        place = Path(self.folder)
        wanted = {str(place), *(str(parent) for parent in place.parents)}
        watched = set(self.watcher.directories())
        if watched - wanted:
            self.watcher.removePaths(sorted(watched - wanted))
        if wanted - watched:
            self.watcher.addPaths(sorted(wanted - watched))

    def _renew_list_model(self) -> None:
        """The model holds on to what it read: only a new one reads the folder that now has this name."""
        old = self.list_model
        self.list_model = QFileSystemModel(self)
        if self.file_list.model() is old:
            self.file_list.setModel(self.closed_model)
        old.deleteLater()

    def _tree_moved(self, index: QModelIndex) -> None:
        path = self.tree_model.filePath(index)
        if path and path != self.folder:
            self.go_to(path)

    def _folder_changed(self) -> None:
        self._watch()  # a folder removed and made again has left the watcher
        if QFileInfo(self.folder).isDir():
            if self._state_of_folder() != self.shown:  # another folder under this name, or its rights changed
                self._renew_list_model()
                self._show_folder()
            return  # only contents changed: the lists follow by themselves and the selection stays
        place = Path(self.folder)
        while not self.go_to(str(place)) and place != place.parent:
            place = place.parent

    def _entry_opened(self, entry: QModelIndex) -> None:
        if entry.siblingAtColumn(0).data() == "..":
            self.go_to(str(Path(self.folder).parent))
        else:
            self.go_to(self.list_model.filePath(entry))  # a file is refused there
