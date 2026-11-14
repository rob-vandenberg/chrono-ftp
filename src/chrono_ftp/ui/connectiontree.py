"""The connections as a tree: a connection whose name holds a slash stands in a folder of that name, and folders hold folders (FJ-01).
Here are the tree itself, the dragging of a connection into a folder or out of one (FJ-02), the folders that stand open (FJ-03) and the
typing that jumps to a connection (FJ-07)."""

from collections.abc import Callable, Iterable
from typing import override

from PySide6.QtCore import QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QIcon, QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QWidget

from chrono_ftp.core import colours
from chrono_ftp.core.connection import FOLDER_SEPARATOR, Connection, folder_of, folders_in, own_name
from chrono_ftp.engine.listing import type_ahead
from chrono_ftp.ui import icons

# --- Version ------------------------------------------------------------
__version__ = "connectiontree.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: The colour blocks drawn again after a change of look, the tree untouched (FQ-04)
# v0.0.47: Review round 2: a space belongs to the name being typed, as it does in the file list, so "Mail server" can be found whole (FJ-07)
# v0.0.47: Initial: the connection list as a tree with folders, a connection dragged into a folder or out of one, the folders that
#          stand open kept for next time, and typing that jumps to a connection (FJ-01, FJ-02, FJ-03, FJ-07)

FOLDER: str = "folder"  # what an entry of the tree is
CONNECTION: str = "connection"
KIND = Qt.ItemDataRole.UserRole
NAME = Qt.ItemDataRole.UserRole + 1  # the whole name of a connection, or the whole path of a folder
TYPE_AHEAD_MILLISECONDS: int = 1500  # a pause this long in typing starts afresh, as in the file list (FG-01, FJ-07)
FOLDER_ICON: str = "folder-open"  # a folder of the list, in the same icon set as every other icon (D-028)


class ConnectionTree(QTreeWidget):
    """The connections in their folders. It never moves an entry itself: a drop is reported, and the window renames the connection, so
    the one rename there is keeps the password, bookmarks and location profiles (FJ-02)."""

    moved = Signal(str, str)  # a connection was dragged: its name, and the folder it should stand in ("" outside every folder)
    folders_changed = Signal()  # a folder was opened or closed: which ones stand open is kept for next time (FJ-03)
    typing_changed = Signal(str)  # what was typed to find a connection; "" once it starts afresh (FJ-07)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.typed: str = ""
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDragEnabled(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.type_pause: QTimer = QTimer(self)
        self.type_pause.setSingleShot(True)
        self.type_pause.setInterval(TYPE_AHEAD_MILLISECONDS)
        self.type_pause.timeout.connect(self.stop_typing)
        self.expanded.connect(self._opened_or_closed)
        self.collapsed.connect(self._opened_or_closed)

    def _opened_or_closed(self, _index: QModelIndex) -> None:
        self.folders_changed.emit()

    # --- what stands in the tree ---------------------------------------------------

    def fill(self, connections: Iterable[Connection], open_folders: Iterable[str], blocks: Callable[[str], QIcon] | None = None) -> None:
        """The whole tree afresh: folders first, then the connections, each by name. open_folders: the folders that stand open (FJ-03)."""
        items = list(connections)
        names = sorted((connection.name for connection in items), key=str.casefold)
        self.blockSignals(True)
        self.clear()
        folders: dict[str, QTreeWidgetItem] = {}
        for folder in sorted(folders_in(names), key=str.casefold):
            parent = folders.get(folder_of(folder))
            item = QTreeWidgetItem([own_name(folder)])
            item.setData(0, KIND, FOLDER)
            item.setData(0, NAME, folder)
            item.setIcon(0, icons.icon(FOLDER_ICON))
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDropEnabled) & ~Qt.ItemFlag.ItemIsDragEnabled)
            folders[folder] = item
            if parent is None:
                self.addTopLevelItem(item)
            else:
                parent.addChild(item)
        for connection in sorted(items, key=lambda item: item.name.casefold()):
            item = QTreeWidgetItem([own_name(connection.name)])
            item.setData(0, KIND, CONNECTION)
            item.setData(0, NAME, connection.name)
            item.setToolTip(0, connection.note)  # what the person wrote about it shows under the mouse (FJ-05)
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled) & ~Qt.ItemFlag.ItemIsDropEnabled)
            if blocks is not None:
                item.setIcon(0, blocks(connection.colour or colours.NO_COLOUR_TAB))
            parent = folders.get(folder_of(connection.name))
            if parent is None:
                self.addTopLevelItem(item)
            else:
                parent.addChild(item)
        wanted = set(open_folders)
        for folder, item in folders.items():
            item.setExpanded(folder in wanted)
        self.blockSignals(False)

    def draw_the_colour_blocks(self, colour_of: Callable[[str], str], blocks: Callable[[str], QIcon]) -> None:
        """The colour block of every connection drawn again, the tree itself untouched: a change of look changes the grey of a
        connection without a colour of its own, and the person's selection and half-filled fields must not move (FQ-04)."""
        walk = QTreeWidgetItemIterator(self)
        while (item := walk.value()) is not None:
            if item.data(0, KIND) == CONNECTION and isinstance(name := item.data(0, NAME), str):
                item.setIcon(0, blocks(colour_of(name) or colours.NO_COLOUR_TAB))
            walk += 1

    def open_folders(self) -> tuple[str, ...]:
        """The folders that stand open now, so they stand open again next time (FJ-03)."""
        return tuple(str(item.data(0, NAME)) for item in self._walk() if item.data(0, KIND) == FOLDER and item.isExpanded())

    def _walk(self, parent: QTreeWidgetItem | None = None) -> list[QTreeWidgetItem]:
        """Every entry, in the order the tree shows them, a closed folder's children included."""
        found: list[QTreeWidgetItem] = []
        count = self.topLevelItemCount() if parent is None else parent.childCount()
        for index in range(count):
            item = self.topLevelItem(index) if parent is None else parent.child(index)
            if item is None:
                continue
            found.append(item)
            found.extend(self._walk(item))
        return found

    def connection_items(self) -> list[QTreeWidgetItem]:
        return [item for item in self._walk() if item.data(0, KIND) == CONNECTION]

    def names_in(self, folder: str) -> list[str]:
        """The connections of this folder and of its folders, in the order the tree shows them (FJ-04)."""
        return [str(item.data(0, NAME)) for item in self.connection_items() if str(item.data(0, NAME)).startswith(folder + FOLDER_SEPARATOR)]

    def item_of(self, name: str) -> QTreeWidgetItem | None:
        return next((item for item in self._walk() if item.data(0, KIND) == CONNECTION and item.data(0, NAME) == name), None)

    def folder_at(self, place: QPoint) -> str | None:
        """The folder at this place in the tree; None where there is no folder."""
        item = self.itemAt(place)
        return str(item.data(0, NAME)) if item is not None and item.data(0, KIND) == FOLDER else None

    def select_name(self, name: str) -> None:
        """Selects this connection and opens the folders above it, so it is in sight."""
        item = self.item_of(name)
        if item is None:
            self.clear_choice()
            return
        self.open_above(item)
        self.setCurrentItem(item)

    def clear_choice(self) -> None:
        """Nothing selected, as New leaves the list."""
        self.setCurrentIndex(QModelIndex())
        self.clearSelection()

    def open_above(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()

    def selected_name(self) -> str:
        """The name of the selected connection; empty when a folder or nothing is selected."""
        item = self.currentItem()
        return str(item.data(0, NAME)) if item is not None and item.data(0, KIND) == CONNECTION else ""

    def selected_folder(self) -> str:
        """The path of the selected folder; empty when a connection or nothing is selected."""
        item = self.currentItem()
        return str(item.data(0, NAME)) if item is not None and item.data(0, KIND) == FOLDER else ""

    # --- dragging a connection into a folder or out of one (FJ-02) -------------------

    def _wanted_folder(self, place: QPoint) -> str | None:
        """The folder a drop at this place means: the folder dropped on, the folder of the connection dropped on, or none outside both.
        None when the drop would change nothing."""
        name = self.selected_name() or ""
        item = self.itemAt(place)
        if item is None:
            folder = ""
        elif item.data(0, KIND) == FOLDER:
            folder = str(item.data(0, NAME))
        else:
            folder = folder_of(str(item.data(0, NAME)))
        if not name or folder == folder_of(name):
            return None
        if folder == name or folder.startswith(name + FOLDER_SEPARATOR):
            return None  # a connection never lands in a folder of its own name
        return folder

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self.selected_name():
            event.accept()
        else:
            event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._wanted_folder(event.position().toPoint()) is None:
            event.ignore()
        else:
            event.accept()

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        """The tree moves nothing itself: the window renames the connection, which keeps its password, bookmarks and profiles (FJ-02)."""
        folder = self._wanted_folder(event.position().toPoint())
        name = self.selected_name()
        if folder is None or not name:
            event.ignore()
            return
        event.accept()
        self.moved.emit(name, folder)

    # --- typing jumps to a connection (FJ-07) ---------------------------------------

    def type_name(self, typed: str, skip_current: bool = False, backwards: bool = False) -> bool:
        """The selection to the first connection, from the selected one on, whose own name starts with what was typed; False, with a
        beep, where none does. A match inside a closed folder opens that folder, so the person sees why the selection moved."""
        items = self.connection_items()
        names: list[str | None] = [own_name(str(item.data(0, NAME))) for item in items]
        current = next((index for index, item in enumerate(items) if item is self.currentItem()), -1)
        found = type_ahead(names, typed, current, skip_current, backwards)
        if found < 0:
            QApplication.beep()
            self.type_pause.start()  # what was typed stays a moment, so the person can type on
            return False
        self.typed = typed
        item = items[found]
        self.open_above(item)
        self.setCurrentItem(item)
        self.scrollToItem(item)
        self.type_pause.start()
        self.typing_changed.emit(typed)
        return True

    def has_next_match(self) -> bool:
        items = self.connection_items()
        names: list[str | None] = [own_name(str(item.data(0, NAME))) for item in items]
        current = next((index for index, item in enumerate(items) if item is self.currentItem()), -1)
        return type_ahead(names, self.typed, current, True) not in (-1, current)

    def stop_typing(self) -> None:
        self.type_pause.stop()
        if self.typed:
            self.typed = ""
            self.typing_changed.emit("")

    @override
    def focusNextPrevChild(self, next: bool) -> bool:
        """Tab while typing goes to the next connection that fits, Shift+Tab to the one before; otherwise Tab moves the focus as usual."""
        if self.typed and self.hasFocus():
            self.type_name(self.typed, True, not next)
            return True
        return super().focusNextPrevChild(next)

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """A typed letter finds a connection (FJ-07): Backspace takes the last letter back, Escape starts afresh. Enter, the arrow keys
        and the keys of the window keep doing what they do."""
        key = event.key()
        held = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        if self.typed and not held:
            if key == Qt.Key.Key_Escape:
                event.accept()
                self.stop_typing()
                return
            if key == Qt.Key.Key_Backspace:
                event.accept()
                if len(self.typed) > 1:
                    self.type_name(self.typed[:-1])
                else:
                    self.stop_typing()
                return
        text = event.text()
        if text and text.isprintable() and (text != " " or self.typed) and not held:  # a space belongs to a name being typed ("Mail server")
            event.accept()
            self.type_name(self.typed + text)
            return
        super().keyPressEvent(event)
