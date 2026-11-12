import datetime
import fnmatch
import os
from typing import cast, override

from PySide6.QtCore import QAbstractTableModel, QByteArray, QItemSelection, QItemSelectionModel, QMimeData, QModelIndex, QPersistentModelIndex, QPoint, Qt, QUrl, Signal
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QIcon, QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QStyle, QTreeView, QWidget

from chrono_ftp.connections.base import RemoteEntry, shown_name

# --- Version ------------------------------------------------------------
__version__ = "filelist.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Whether a drag runs from this list or over it, so the automatic refresh waits for its end (FP-01)
# v0.0.6: Initial: the one file list of both sides: sorting, selection kept, drag and drop, names that aren't valid UTF-8

COLUMNS: tuple[str, ...] = ("Name", "Size", "Changed", "Rights", "Owner", "Group", "Link target")
LOCAL_COLUMN_COUNT: int = 4  # owner and group matter on a server
NAME_COLUMN_WIDTH: int = 260
SIZE_COLUMN_WIDTH: int = 80
CHANGED_COLUMN_WIDTH: int = 130
PARENT_ENTRY: str = ".."
ENTRY_ROLE: int = Qt.ItemDataRole.UserRole + 3
LOCAL_FILES: str = "application/x-chrono-ftp-local"  # the selection of the left side: read there, so any name survives the drag
REMOTE_FILES: str = "application/x-chrono-ftp-remote"
DRAG_MARK: bytes = str(os.getpid()).encode()  # a drag from another Chrono FTP is not our own selection
FROM_LOCAL: str = "local"
FROM_REMOTE: str = "remote"
FROM_OUTSIDE: str = "outside"  # the file manager


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


def rights_text(permissions: str) -> str:
    """0755 as rwxr-xr-x; what the server gave stays as it is when it is no number."""
    try:
        mode = int(permissions, 8)
    except ValueError:
        return permissions
    letters = "".join(letter if mode & (1 << (8 - place)) else "-" for place, letter in enumerate("rwxrwxrwx"))
    return letters


def summary_text(chosen: list[RemoteEntry], entries: list[RemoteEntry]) -> str:
    """The count line under a list: what is selected out of what is here."""
    total = sum(entry.size for entry in entries if not entry.is_folder)
    picked = sum(entry.size for entry in chosen if not entry.is_folder)
    return f"{size_text(picked)} of {size_text(total)} in {len(chosen)} of {len(entries)}"


def mask_text(mask: str) -> str:
    return f" (filter: {mask})" if mask else ""


def _sort_value(entry: RemoteEntry, column: int) -> tuple[object, ...]:
    name = shown_name(entry.name).casefold()
    if column == 1:
        return (entry.size, name)
    if column == 2:
        return (entry.modified or 0.0, name)
    if column == 3:
        return (entry.permissions, name)
    if column == 4:
        return (entry.owner.casefold(), name)
    if column == 5:
        return (entry.group.casefold(), name)
    if column == 6:
        return (entry.link_target.casefold(), name)
    return (name,)


class FileModel(QAbstractTableModel):
    """The rows of one folder. None is the parent entry, always on top; folders come before files, whichever way a column is sorted."""

    def __init__(self, column_count: int, folder_icon: QIcon, file_icon: QIcon, link_icon: QIcon, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.column_count: int = column_count
        self.icons: tuple[QIcon, QIcon, QIcon] = (folder_icon, file_icon, link_icon)
        self.rows: list[RemoteEntry | None] = []
        self.sorted_by: tuple[int, Qt.SortOrder] = (0, Qt.SortOrder.AscendingOrder)

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    @override
    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else self.column_count

    @override
    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole and 0 <= section < self.column_count:
            return COLUMNS[section]
        return None

    @override
    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        return base if self.rows[index.row()] is None else base | Qt.ItemFlag.ItemIsDragEnabled

    def _text(self, entry: RemoteEntry | None, column: int) -> str:
        if entry is None:
            return PARENT_ENTRY if column == 0 else ""
        if column == 0:
            return shown_name(entry.name)
        if column == 1:
            return "" if entry.is_folder else size_text(entry.size)
        if column == 2:
            return date_text(entry.modified)
        return (rights_text(entry.permissions), entry.owner, entry.group, shown_name(entry.link_target))[column - 3]

    @override
    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        if not index.isValid():
            return None
        entry = self.rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(entry, index.column())
        if role == ENTRY_ROLE:
            return entry
        if role == Qt.ItemDataRole.DecorationRole and index.column() == 0 and entry is not None:
            return self.icons[2] if entry.is_link else self.icons[0] if entry.is_folder else self.icons[1]
        return None

    def _in_order(self, rows: list[RemoteEntry | None]) -> list[RemoteEntry | None]:
        column, order = self.sorted_by
        backwards = order == Qt.SortOrder.DescendingOrder
        entries = [row for row in rows if row is not None]
        folders = sorted((entry for entry in entries if entry.is_folder), key=lambda entry: _sort_value(entry, column), reverse=backwards)
        files = sorted((entry for entry in entries if not entry.is_folder), key=lambda entry: _sort_value(entry, column), reverse=backwards)
        return [*(row for row in rows if row is None), *folders, *files]

    def set_rows(self, rows: list[RemoteEntry | None]) -> None:
        self.beginResetModel()
        self.rows = self._in_order(rows)
        self.endResetModel()

    @override
    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        self.layoutAboutToBeChanged.emit()
        self.sorted_by = (column, order)
        before = list(self.rows)
        self.rows = self._in_order(before)
        moved = {id(row): position for position, row in enumerate(self.rows)}
        old = self.persistentIndexList()
        self.changePersistentIndexList(old, [self.index(moved[id(before[index.row()])], index.column()) for index in old])
        self.layoutChanged.emit()


class FileList(QTreeView):
    """One folder as a list. What the person does with it leaves through signals: this list knows no connection."""

    opened = Signal(object)  # an entry; None for the parent entry
    selection_changed = Signal()
    dropped = Signal(str, object, object)  # where it came from, the paths of an outside drag, the name of the folder it fell on ("" for this folder)

    def __init__(self, column_count: int, kind: str, accepts: frozenset[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.dragging_out: bool = False  # a drag started here runs (startDrag waits for its end)
        self.dragged_over: bool = False  # a drag this list would take is over it now
        self.kind: str = kind
        self.folder: str = ""
        self.accepts: frozenset[str] = accepts
        self.show_hidden: bool = True
        self.name_mask: str = ""  # only names that fit, like *.txt
        self.has_parent: bool = False
        self.all_entries: list[RemoteEntry] = []
        style = self.style()
        self.files = FileModel(column_count, style.standardIcon(QStyle.StandardPixmap.SP_DirIcon), style.standardIcon(QStyle.StandardPixmap.SP_FileIcon), style.standardIcon(QStyle.StandardPixmap.SP_FileLinkIcon), self)
        self.setModel(self.files)
        self.setRootIsDecorated(False)
        self.setItemsExpandable(False)
        self.setUniformRowHeights(True)  # a folder of a hundred thousand files stays quick
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setColumnWidth(0, NAME_COLUMN_WIDTH)
        self.setColumnWidth(1, SIZE_COLUMN_WIDTH)
        self.setColumnWidth(2, CHANGED_COLUMN_WIDTH)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.activated.connect(self._activated)
        selection = self.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self.selection_changed)

    def names(self) -> list[str]:
        """What the name column shows, top to bottom."""
        return [str(self.files.index(row, 0).data()) for row in range(self.files.rowCount())]

    def index_of(self, name: str) -> QModelIndex:
        return next((self.files.index(row, 0) for row, entry in enumerate(self.files.rows) if (entry.name if entry is not None else PARENT_ENTRY) == name), QModelIndex())

    def selected_entries(self) -> list[RemoteEntry]:
        selection = self.selectionModel()
        if selection is None:
            return []
        found = [self.files.rows[index.row()] for index in selection.selectedRows(0)]
        return [entry for entry in found if entry is not None]

    def show_entries(self, entries: list[RemoteEntry], with_parent: bool, same_folder: bool) -> None:
        """same_folder: what the person has selected and where the list stands stay as they are."""
        keep = {entry.name for entry in self.selected_entries()} if same_folder else set[str]()
        current_row = self.currentIndex().row()
        current = self.files.rows[current_row] if same_folder and 0 <= current_row < len(self.files.rows) else None
        scrollbar = self.verticalScrollBar()
        position = scrollbar.value() if same_folder else 0
        self.all_entries = entries
        self.has_parent = with_parent
        self._fill(keep, current.name if current is not None else None)
        scrollbar.setValue(position)

    def set_show_hidden(self, show: bool) -> None:
        if show != self.show_hidden:
            self.show_hidden = show
            self._fill({entry.name for entry in self.selected_entries()}, None)

    def set_name_mask(self, mask: str) -> None:
        self.name_mask = mask
        self._fill({entry.name for entry in self.selected_entries()}, None)

    def visible_entries(self) -> list[RemoteEntry]:
        return [row for row in self.files.rows if row is not None]

    def _fits(self, entry: RemoteEntry) -> bool:
        if not self.show_hidden and entry.name.startswith("."):
            return False
        return not self.name_mask or entry.is_folder or fnmatch.fnmatch(entry.name.casefold(), self.name_mask.casefold())

    def _fill(self, keep: set[str], current: str | None) -> None:
        shown = [entry for entry in self.all_entries if self._fits(entry)]
        rows: list[RemoteEntry | None] = [None] if self.has_parent else []
        self.files.set_rows([*rows, *shown])
        selection = self.selectionModel()
        if selection is None:
            return
        if current is not None:
            selection.setCurrentIndex(self.index_of(current), QItemSelectionModel.SelectionFlag.NoUpdate)
        chosen = QItemSelection()
        for row, entry in enumerate(self.files.rows):
            if entry is not None and entry.name in keep:
                chosen.select(self.files.index(row, 0), self.files.index(row, self.files.column_count - 1))
        selection.select(chosen, QItemSelectionModel.SelectionFlag.Select)
        self.selection_changed.emit()

    def _activated(self, index: QModelIndex) -> None:
        self.opened.emit(self.files.rows[index.row()])

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Backspace and self.has_parent:
            self.opened.emit(None)
            return
        super().keyPressEvent(event)

    # --- drag and drop ------------------------------------------------------

    @override
    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        chosen = self.selected_entries()
        if not chosen:
            return
        mime = self.drag_data()
        drag = QDrag(self)
        drag.setMimeData(mime)
        self.dragging_out = True
        try:
            drag.exec(Qt.DropAction.CopyAction)  # copying only: a file manager may never take a file away from here
        finally:
            self.dragging_out = False

    @property
    def in_drag(self) -> bool:
        """A drag runs from this list or over it: the list must not change under it."""
        return self.dragging_out or self.dragged_over

    @override
    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.dragged_over = False
        super().dragLeaveEvent(event)

    def drag_data(self) -> QMimeData:
        mime = QMimeData()
        mime.setData(LOCAL_FILES if self.kind == FROM_LOCAL else REMOTE_FILES, QByteArray(DRAG_MARK))
        if self.kind == FROM_LOCAL:  # so a file manager or another program can take them too
            mime.setUrls([QUrl.fromLocalFile(os.path.join(self.folder, entry.name)) for entry in self.selected_entries()])
        return mime

    def _source_of(self, mime: QMimeData) -> str | None:
        if mime.data(LOCAL_FILES).data() == DRAG_MARK:
            return FROM_LOCAL
        if mime.data(REMOTE_FILES).data() == DRAG_MARK:
            return FROM_REMOTE
        return FROM_OUTSIDE if mime.hasUrls() and all(url.isLocalFile() for url in mime.urls()) else None

    def _folder_at(self, position: QPoint) -> str:
        """The folder a drop falls on: its name, the parent entry, or "" for the folder shown."""
        index = self.indexAt(position)
        if not index.isValid():
            return ""
        entry = self.files.rows[index.row()]
        if entry is None:
            return PARENT_ENTRY
        return entry.name if entry.is_folder else ""

    def _wanted(self, mime: QMimeData, position: QPoint) -> bool:
        source = self._source_of(mime)
        if source is None or source not in self.accepts:
            return False
        if source != self.kind:
            return True
        folder = self._folder_at(position)  # within one list a drop must fall on another folder
        return bool(folder) and folder not in {entry.name for entry in self.selected_entries()}

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        source = self._source_of(mime) if mime is not None else None
        if source in self.accepts:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.dragged_over = True
        else:
            event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        mime = event.mimeData()
        if mime is not None and self._wanted(mime, event.position().toPoint()):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        self.dragged_over = False
        mime = event.mimeData()
        position = event.position().toPoint()
        if mime is None or not self._wanted(mime, position):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        source = cast(str, self._source_of(mime))
        paths = [url.toLocalFile() for url in mime.urls()] if source == FROM_OUTSIDE else []
        self.dropped.emit(source, paths, self._folder_at(position))
