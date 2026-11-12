import os
from pathlib import Path

from PySide6.QtCore import QDir, QFileSystemWatcher, QModelIndex, Qt, QTimer, Signal
from PySide6.QtWidgets import QFileSystemModel, QSplitter, QTreeView, QVBoxLayout, QWidget

from chrono_ftp.config.settings import TREE_ABOVE, TREE_HIDDEN
from chrono_ftp.connections.base import RemoteEntry, shown_name
from chrono_ftp.engine import localfiles
from chrono_ftp.engine.browsing import typed_local_path
from chrono_ftp.ui.sidebar import FolderHistory, NavigationBar, action_bar, button_row
from chrono_ftp.ui.filelist import FROM_LOCAL, FROM_REMOTE, LOCAL_COLUMN_COUNT, FileList, mask_text, summary_text
from chrono_ftp.ui.pathbar import PathBar
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "localside.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: The path is a row of clickable folder parts; a click on its empty part or Ctrl+L types a path, read as Ctrl+O reads it, and
#          one that is no folder is told; a click on a part gives this side the focus (FH-01); the path bar keeps the real path; a move
#          up because the folder went away is marked as the side's own (moved_by_itself), no step of the person
# v0.0.12: The count line is shown as written, never read as HTML
# v0.0.7: One row of buttons
# v0.0.6: The shared file list, read with os, so a name that is not valid UTF-8 shows too; sorting, drops, hidden files
# v0.0.5: Several entries can be selected; the folder shown is told to the window
# v0.0.3: Initial: the left side, always this computer: folder tree and file list, neutral (OR-1, OR-2, D-019)

NO_ACCESS_TEXT: str = "no access"
PATH_TIP: str = "This computer. Click a folder to open it; click the empty part, or press Ctrl+L, to type a path."
TREE_WIDTH: int = 180
LIST_WIDTH: int = 420
SETTLE_MILLISECONDS: int = 150  # a copy into this folder changes it thousands of times: it is read once things settle


def local_parts(folder: str) -> list[tuple[str, str]]:
    """The folder parts of a path on this computer, each with the path it opens; from the root down."""
    found: list[tuple[str, str]] = []
    walked: Path | None = None
    for part in Path(folder).parts:
        walked = Path(part) if walked is None else walked / part
        found.append((shown_name(part), str(walked)))
    return found


class LocalSide(QWidget):
    """The left side. A folder that can't be opened is shown as closed: its list is empty and the path line says so, so the tree, the list
    and the path line always agree (OR-15). When the folder shown or one above it is removed or renamed, the side moves to the nearest
    folder above it that is still there."""

    folder_changed = Signal(object)  # a str; object, because Qt would change a name that is not valid UTF-8
    selection_changed = Signal()
    file_opened = Signal(object)
    filter_wanted = Signal()
    path_not_found = Signal(object)  # a typed path that is no folder here: the window says so

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.navigation = NavigationBar()
        self.actions_bar = action_bar()
        self.path_bar = PathBar()
        self.path_bar.setToolTip(PATH_TIP)
        self.path_line = self.path_bar.line  # the field to type a path in
        self.count_label = plain_label()
        self.history = FolderHistory()
        self.tree_model = QFileSystemModel(self)
        self.tree_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        self.tree_model.setRootPath(QDir.rootPath())
        self.tree = QTreeView()
        self.tree.setModel(self.tree_model)
        self.tree.setRootIndex(self.tree_model.index(QDir.rootPath()))
        self.tree.setHeaderHidden(True)
        for column in range(1, self.tree_model.columnCount()):
            self.tree.hideColumn(column)
        self.file_list = FileList(LOCAL_COLUMN_COUNT, FROM_LOCAL, frozenset({FROM_REMOTE}))
        self.path_bar.next_focus = self.file_list
        self.file_list.setAlternatingRowColors(True)
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
        layout.addWidget(self.parts, 1)  # all the height goes to the lists
        layout.addWidget(self.count_label)
        selection = self.tree.selectionModel()
        if selection is not None:
            selection.currentChanged.connect(self._tree_moved)  # a click and the arrow keys alike
        self.file_list.opened.connect(self._entry_opened)  # a double click and Return alike
        self.file_list.selection_changed.connect(self.selection_changed)
        self.file_list.selection_changed.connect(self._show_count)
        self.navigation.wanted.connect(self._button_pressed)
        self.path_line.returnPressed.connect(self._path_typed)
        self.path_bar.folder_chosen.connect(self._part_chosen)
        self.watcher = QFileSystemWatcher(self)
        self.settle = QTimer(self)
        self.settle.setSingleShot(True)
        self.settle.setInterval(SETTLE_MILLISECONDS)
        self.settle.timeout.connect(self._folder_changed)
        self.watcher.directoryChanged.connect(self._folder_touched)
        self.folder: str = ""
        self.moved_by_itself: bool = False  # the side moves up because its folder went away: no step of the person
        self._walking: bool = False  # Back and Forward move through the history, they don't add to it
        if not self.go_to(QDir.homePath()):
            self.go_to(QDir.rootPath())

    def go_to(self, path: str) -> bool:
        clean = os.path.normpath(path)
        if not os.path.isdir(clean):
            return False
        same = clean == self.folder
        if self.folder and not same and not self._walking:
            self.history.left(self.folder)
        self.folder = clean
        self.file_list.folder = clean
        self._watch()
        self._read(same)
        if not same:
            self.file_list.setCurrentIndex(QModelIndex())  # an entry of the folder before is not on screen any more
        if shown_name(clean) == clean:  # the tree can't reach a folder whose name Qt can't hold
            index = self.tree_model.index(clean)
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)
        self.navigation.show_state(self.history, clean != os.path.dirname(clean), True)
        self.folder_changed.emit(clean)
        return True

    def walk_to(self, folder: str) -> None:
        """A move that adds nothing to the history: Back, Forward and a change of tab."""
        self._walking = True
        self.go_to(folder)
        self._walking = False
        self.navigation.show_state(self.history, self.folder != os.path.dirname(self.folder), True)

    def _button_pressed(self, name: str) -> None:
        self.history.behind = [folder for folder in self.history.behind if os.path.isdir(folder)]  # one removed since is no step
        self.history.ahead = [folder for folder in self.history.ahead if os.path.isdir(folder)]
        if name == "back" and self.history.behind:
            self.walk_to(self.history.back(self.folder))
        elif name == "forward" and self.history.ahead:
            self.walk_to(self.history.forward(self.folder))
        elif name == "parent":
            self.go_to(os.path.dirname(self.folder))
        elif name == "root":
            self.go_to(QDir.rootPath())
        elif name == "home":
            self.go_to(QDir.homePath())
        elif name == "refresh":
            self.refresh()
        elif name == "filter":
            self.filter_wanted.emit()

    def _path_typed(self) -> None:
        """Enter in the path field: read as Ctrl+O reads it; a folder that isn't there leaves the side where it was and is told."""
        typed = self.path_bar.typed_path()
        path = typed_local_path(self.folder, typed) if typed else self.folder
        self.path_bar.stop_typing()
        if not self.go_to(path):
            self.path_not_found.emit(path)

    def _part_chosen(self, path: object) -> None:
        """A click on a folder part opens it, and this side takes the keyboard focus, as with a click in its list."""
        self.file_list.setFocus(Qt.FocusReason.MouseFocusReason)
        if isinstance(path, str) and path != self.folder:
            self.go_to(path)

    def _show_count(self) -> None:
        self.count_label.setText(summary_text(self.selected_entries(), self.file_list.visible_entries()) + mask_text(self.file_list.name_mask))

    def refresh(self) -> None:
        self._read(True)

    def set_tree_place(self, place: str) -> None:
        self.tree.setVisible(place != TREE_HIDDEN)
        self.parts.setOrientation(Qt.Orientation.Vertical if place == TREE_ABOVE else Qt.Orientation.Horizontal)

    def set_show_hidden(self, show: bool) -> None:
        self.file_list.set_show_hidden(show)
        hidden = QDir.Filter.Hidden if show else QDir.Filter(0)
        self.tree_model.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot | hidden)

    def selected_entries(self) -> list[RemoteEntry]:
        return self.file_list.selected_entries()

    def selected_paths(self) -> list[str]:
        return [os.path.join(self.folder, entry.name) for entry in self.file_list.selected_entries()]

    def _read(self, same_folder: bool) -> None:
        entries = localfiles.read_folder(self.folder)
        shown = shown_name(self.folder) if entries is not None else f"{shown_name(self.folder)} ({NO_ACCESS_TEXT})"
        self.path_bar.show_path(self.folder, local_parts(self.folder), "" if entries is not None else NO_ACCESS_TEXT, shown)
        self.file_list.show_entries(entries or [], entries is not None and self.folder != os.path.dirname(self.folder), same_folder)

    def _watch(self) -> None:
        """The folder and every folder above it: a removal or rename higher up shows only there."""
        place = Path(self.folder)
        wanted = {str(place), *(str(parent) for parent in place.parents)}
        wanted = {folder for folder in wanted if shown_name(folder) == folder}
        watched = set(self.watcher.directories())
        if watched - wanted:
            self.watcher.removePaths(sorted(watched - wanted))
        if wanted - watched:
            self.watcher.addPaths(sorted(wanted - watched))

    def _folder_touched(self, _folder: str) -> None:
        self.settle.start()

    def _tree_moved(self, index: QModelIndex) -> None:
        path = self.tree_model.filePath(index)
        if path and path != self.folder and os.path.isdir(self.folder):  # when the folder shown goes away, the tree moves to a neighbour by itself
            self.go_to(path)

    def _folder_changed(self) -> None:
        self._watch()  # a folder removed and made again has left the watcher
        if os.path.isdir(self.folder):
            self._read(True)
            return
        place = Path(self.folder)
        self.moved_by_itself = True  # the folder went away: no step of the person
        try:
            while not self.go_to(str(place)) and place != place.parent:
                place = place.parent
        finally:
            self.moved_by_itself = False

    def _entry_opened(self, entry: RemoteEntry | None) -> None:
        if entry is None:
            self.go_to(os.path.dirname(self.folder))
        elif entry.is_folder:
            self.go_to(os.path.join(self.folder, entry.name))
        else:
            self.file_opened.emit(os.path.join(self.folder, entry.name))
