import datetime
import fnmatch
import os
from collections.abc import Callable, Iterable
from typing import cast, override

from PySide6.QtCore import QAbstractTableModel, QByteArray, QEvent, QItemSelection, QItemSelectionModel, QMimeData, QModelIndex, QPersistentModelIndex, QPoint, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QContextMenuEvent, QDrag, QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QFocusEvent, QGuiApplication, QIcon, QKeyEvent, QMouseEvent, QResizeEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMenu, QStyle, QTreeView, QWidget

from chrono_ftp.config.settings import SHORT
from chrono_ftp.connections.base import RemoteEntry, shown_name
from chrono_ftp.core.filecolours import ColourRule, rule_for
from chrono_ftp.engine.listing import extension_of, natural_key, plain_key, same_extension, type_ahead
from chrono_ftp.engine.listing import size_text as size_text  # given on: the sides and the tests read them here
from chrono_ftp.engine.listing import summary_text as summary_text
from chrono_ftp.ui.menus import open_menu

# --- Version ------------------------------------------------------------
__version__ = "filelist.py 0.0.40"

# --- Version history ----------------------------------------------------
# v0.0.40: Block FG: type-ahead with Tab to the next match (FG-01); select all, invert, clear, the same extension, the previous selection
#          and Insert (FG-02); the two-pane selection mode, where an action works on the focused file when nothing is marked (FG-03);
#          the file under the cursor only (FG-05); the size format (FG-06); the column header menu (FG-07); natural sort with folders
#          always first and by name, and sorting by extension (FG-08); the hidden and filtered counts (FG-09); a drag with the right
#          mouse button drops with a menu, its right-click menu waits for the button to come up (FG-14); a left-button drag unchanged;
#          review: the typed text starts afresh when the list is filled again or something is opened; only the left button opens on
#          a desktop that opens with one click; a large selection is kept as few ranges, so Select All and Invert on 10,000 files,
#          and every key after them, stay quick
# v0.0.39: File colours by mask: the first rule that fits colours the text and background of a row, the selection drawn over it
#          (FQ-01); the thumbnail view: tall rows with a small picture of each image file where it has arrived, and the rows in view
#          (FQ-02)
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
THUMBNAIL_PIXELS: int = 64  # the height of a row, and the largest side of a picture, in the thumbnail view (FQ-02)
RIGHT_DRAG: str = "application/x-chrono-ftp-right-drag"  # a drag made with the right mouse button: its drop asks with a menu (FG-14)
TYPE_AHEAD_MILLISECONDS: int = 1500  # a pause this long in typing starts the typed name afresh (FG-01)
STANDARD_WIDTHS: dict[int, int] = {0: NAME_COLUMN_WIDTH, 1: SIZE_COLUMN_WIDTH, 2: CHANGED_COLUMN_WIDTH}
STANDARD_OTHER_WIDTH: int = 100
SORT_UP_TEXT: str = "Sort Ascending"
SORT_DOWN_TEXT: str = "Sort Descending"
HIDE_COLUMN_TEXT: str = "Hide This Column"
NAME_STAYS_TEXT: str = "The name column always shows."
FIT_TEXT: str = "Fit Column Widths"
STANDARD_COLUMNS_TEXT: str = "Restore the Standard Columns"
TYPING_TEXT: str = "Find: {typed}"
TYPING_NEXT_TEXT: str = "Find: {typed}   (Tab: the next one)"
ThumbnailOf = Callable[[RemoteEntry], QIcon | None]


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


def mask_text(mask: str) -> str:
    return f" (filter: {mask})" if mask else ""


def status_text(view: "FileList") -> str:
    """The status line of a side: what was typed to find a name, or the selection totals with the filter (FG-01, FG-13)."""
    if view.typed:
        return (TYPING_NEXT_TEXT if view.has_next_match() else TYPING_TEXT).format(typed=view.typed)
    return summary_text(view.selected_entries(), view.visible_entries(), view.files.size_style) + mask_text(view.name_mask)


def _sort_value(entry: RemoteEntry, column: int, natural: bool = True, by_extension: bool = False) -> tuple[object, ...]:
    name = natural_key(entry.name) if natural else plain_key(entry.name)
    if column == 0 and by_extension:
        return (extension_of(entry.name), name)
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
        self.colour_rules: tuple[ColourRule, ...] = ()  # the person's file colours (FQ-01)
        self.colours_shown: bool = True  # off on the right side while no connection is live: a colour there means a live one (OR-3)
        self._rule_of: dict[tuple[str, bool], ColourRule | None] = {}  # the rule of each name, found once per list of rules
        self.thumbnail_of: ThumbnailOf | None = None  # the thumbnail view is on: the picture of a file, None while it has none (FQ-02)
        self.size_style: str = SHORT  # how sizes show (FG-06)
        self.natural: bool = True  # "file2" before "file10" (FG-08)
        self.by_extension: bool = False  # the name column sorts by the extension first (Ctrl+F4)

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
            return "" if entry.is_folder else size_text(entry.size, self.size_style)
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
            picture = self.thumbnail_of(entry) if self.thumbnail_of is not None and not entry.is_folder else None
            if picture is not None:
                return picture
            return self.icons[2] if entry.is_link else self.icons[0] if entry.is_folder else self.icons[1]
        if role in (Qt.ItemDataRole.ForegroundRole, Qt.ItemDataRole.BackgroundRole) and entry is not None:
            rule = self.rule_of(entry)
            if rule is None:
                return None
            colour = rule.text if role == Qt.ItemDataRole.ForegroundRole else rule.background
            return QBrush(QColor(colour)) if colour else None  # selected, a row shows the selection's colours over this (FQ-01)
        return None

    def rule_of(self, entry: RemoteEntry) -> ColourRule | None:
        """The file colour of a row: the first rule that fits its name (FQ-01)."""
        if not self.colours_shown or not self.colour_rules:
            return None
        found = (entry.name, entry.is_folder)
        if found not in self._rule_of:
            self._rule_of[found] = rule_for(self.colour_rules, entry.name, entry.is_folder)
        return self._rule_of[found]

    def set_colours(self, rules: tuple[ColourRule, ...], shown: bool) -> None:
        if rules == self.colour_rules and shown == self.colours_shown:
            return
        self.colour_rules = rules
        self.colours_shown = shown
        self._rule_of.clear()
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, self.column_count - 1))

    def _in_order(self, rows: list[RemoteEntry | None]) -> list[RemoteEntry | None]:
        """Folders always first and by their names, which run backwards only when the names are sorted backwards (FG-08)."""
        column, order = self.sorted_by
        backwards = order == Qt.SortOrder.DescendingOrder
        natural, by_extension = self.natural, self.by_extension
        entries = [row for row in rows if row is not None]
        folders = sorted((entry for entry in entries if entry.is_folder), key=lambda entry: _sort_value(entry, 0, natural), reverse=backwards and column == 0 and not by_extension)
        files = sorted((entry for entry in entries if not entry.is_folder), key=lambda entry: _sort_value(entry, column, natural, by_extension), reverse=backwards)
        return [*(row for row in rows if row is None), *folders, *files]

    def set_display(self, size_style: str, natural: bool) -> None:
        """The size format and the natural order (FG-06, FG-08): the rows are drawn and sorted again, the selection stays."""
        if size_style != self.size_style:
            self.size_style = size_style
            if self.rows:
                self.dataChanged.emit(self.index(0, 1), self.index(len(self.rows) - 1, 1))
        if natural != self.natural:
            self.natural = natural
            self.sort(*self.sorted_by)

    def set_rows(self, rows: list[RemoteEntry | None]) -> None:
        self.beginResetModel()
        self._rule_of.clear()  # only the names of this folder are kept
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
    view_moved = Signal()  # other rows came into view: scrolled, resized, or another folder (FQ-02)
    dropped = Signal(str, object, object)  # where it came from, the paths of an outside drag, the name of the folder it fell on ("" for this folder)
    right_dropped = Signal(str, object, object)  # the same for a drag made with the right mouse button: the window asks with a menu (FG-14)
    typing_changed = Signal(str)  # what was typed to find a name; "" once it starts afresh (FG-01)

    def __init__(self, column_count: int, kind: str, accepts: frozenset[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.typed: str = ""  # type-ahead: what was typed so far (FG-01)
        self.type_pause = QTimer(self)
        self.type_pause.setSingleShot(True)
        self.type_pause.setInterval(TYPE_AHEAD_MILLISECONDS)
        self.type_pause.timeout.connect(self.stop_typing)
        self.two_pane: bool = False  # the two-pane selection mode (FG-03)
        self.only_current: bool = False  # an action of "This File Only" runs: it acts on the file under the cursor (FG-05)
        self.hidden_count: int = 0  # entries left out as hidden, and all whose names start with a dot (FG-09)
        self.dot_count: int = 0
        self.filtered_count: int = 0  # entries left out by the filter
        self._pressed_at: QPoint | None = None  # where a mouse button went down: a drag of the focused file starts from there (FG-03)
        self._right_release: bool = False  # a release of another button than the left one is being handled: it opens nothing
        self._menu_waiting: QPoint | None = None  # a right-click menu waits for the button to come up: it may become a drag (FG-14)
        self._selection_now: tuple[str, frozenset[str]] = ("", frozenset())  # the folder and the names selected now
        self._selection_before: tuple[str, frozenset[str]] = ("", frozenset())  # the last selection that was cleared or replaced (FG-02)
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
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        selection = self.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(self._note_selection)
            selection.selectionChanged.connect(self.selection_changed)
            selection.currentChanged.connect(self._current_moved)
        header = self.header()
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu_wanted)
        header.sectionPressed.connect(self._header_pressed)

    def names(self) -> list[str]:
        """What the name column shows, top to bottom."""
        return [str(self.files.index(row, 0).data()) for row in range(self.files.rowCount())]

    def index_of(self, name: str) -> QModelIndex:
        return next((self.files.index(row, 0) for row, entry in enumerate(self.files.rows) if (entry.name if entry is not None else PARENT_ENTRY) == name), QModelIndex())

    def selected_entries(self) -> list[RemoteEntry]:
        """What is marked (selected), in the order of the list."""
        selection = self.selectionModel()
        if selection is None:
            return []
        found = [self.files.rows[row] for row in self.selected_rows()]
        return [entry for entry in found if entry is not None]

    def selected_rows(self) -> list[int]:
        """The rows selected, top to bottom, read from the ranges of the selection: quick also for 10,000 selected rows."""
        selection = self.selectionModel()
        if selection is None:
            return []
        rows: set[int] = set()
        ranges = selection.selection()
        for number in range(ranges.count()):
            part = ranges.at(number)
            rows.update(range(part.top(), part.bottom() + 1))
        return sorted(row for row in rows if 0 <= row < len(self.files.rows))

    def current_entry(self) -> RemoteEntry | None:
        """The entry under the cursor (the focused one); None on the parent entry or nowhere."""
        row = self.currentIndex().row()
        return self.files.rows[row] if 0 <= row < len(self.files.rows) else None

    def acting_entries(self) -> list[RemoteEntry]:
        """What an action works on: the marked entries; in the two-pane mode with nothing marked, and for "This File Only", the focused
        entry (FG-03, FG-05)."""
        if self.only_current or (self.two_pane and not self.selected_entries()):
            entry = self.current_entry()
            return [entry] if entry is not None else []
        return self.selected_entries()

    # --- selection (FG-02, FG-03) ---------------------------------------------

    def _entry_rows(self) -> QItemSelection:
        return self.rows_selection(row for row, entry in enumerate(self.files.rows) if entry is not None)

    def rows_selection(self, rows: Iterable[int]) -> QItemSelection:
        """These rows as few ranges as can be, one per run of rows that follow each other: a range per row makes a large selection slow."""
        found = QItemSelection()
        last_column = self.files.column_count - 1
        start = end = -2
        for row in sorted(rows):
            if row == end + 1:
                end = row
                continue
            if start >= 0:
                found.select(self.files.index(start, 0), self.files.index(end, last_column))
            start = end = row
        if start >= 0:
            found.select(self.files.index(start, 0), self.files.index(end, last_column))
        return found

    def _apply(self, rows: QItemSelection, how: QItemSelectionModel.SelectionFlag) -> None:
        selection = self.selectionModel()
        if selection is not None:
            selection.select(rows, how | QItemSelectionModel.SelectionFlag.Rows)

    def select_all_entries(self) -> None:
        """Ctrl+A: every entry, never the parent entry."""
        self._apply(self._entry_rows(), QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def invert_selection(self) -> None:
        """Ctrl+I: what was marked is no longer, and the rest is: the rows between the marked ones, as ranges."""
        marked = set(self.selected_rows())
        self._apply(self.rows_selection(row for row, entry in enumerate(self.files.rows) if entry is not None and row not in marked), QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def clear_marks(self) -> None:
        """Ctrl+Shift+A: nothing marked; the cursor stays where it is."""
        selection = self.selectionModel()
        if selection is not None:
            selection.clearSelection()

    def same_extension_wanted(self) -> str | None:
        """The extension Select the Same Extension would take: that of the focused file; None on a folder or nowhere."""
        entry = self.current_entry()
        return extension_of(entry.name) if entry is not None and not entry.is_folder else None

    def select_same_extension(self) -> None:
        """Every file with the extension of the focused one is marked too; a name without one marks the files without one."""
        extension = self.same_extension_wanted()
        if extension is None:
            return
        names = {entry.name for entry in same_extension(self.visible_entries(), extension)}
        rows = self.rows_selection(row for row, entry in enumerate(self.files.rows) if entry is not None and entry.name in names)
        self._apply(rows, QItemSelectionModel.SelectionFlag.Select)

    def can_restore(self) -> bool:
        folder, names = self._selection_before
        return folder == self.folder and bool(names & {entry.name for entry in self.visible_entries()})

    def restore_selection(self) -> None:
        """The selection that was cleared or replaced last, as far as its entries are still here."""
        folder, names = self._selection_before
        if folder != self.folder:
            return
        rows = self.rows_selection(row for row, entry in enumerate(self.files.rows) if entry is not None and entry.name in names)
        self._apply(rows, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _note_selection(self) -> None:
        """A selection that is cleared or replaced is kept, so Restore the Previous Selection brings it back; one that only grows (Shift+Down)
        is not a new one."""
        now = (self.folder, frozenset(entry.name for entry in self.selected_entries()))
        before_folder, before_names = self._selection_now
        if now == self._selection_now:
            return
        if before_names and (before_folder != now[0] or not before_names <= now[1]):
            self._selection_before = self._selection_now
        self._selection_now = now

    def toggle_row(self, row: int) -> None:
        if 0 <= row < len(self.files.rows) and self.files.rows[row] is not None:
            self._apply(QItemSelection(self.files.index(row, 0), self.files.index(row, self.files.column_count - 1)), QItemSelectionModel.SelectionFlag.Toggle)

    def mark_and_move_down(self) -> None:
        """Insert: the focused entry is marked, or no longer marked, and the cursor goes one down."""
        row = self.currentIndex().row()
        if row < 0:
            return
        self.toggle_row(row)
        below = min(row + 1, len(self.files.rows) - 1)
        selection = self.selectionModel()
        if selection is not None:
            selection.setCurrentIndex(self.files.index(below, 0), QItemSelectionModel.SelectionFlag.NoUpdate)
            self.scrollTo(self.files.index(below, 0))

    def _current_moved(self, _current: QModelIndex, _previous: QModelIndex) -> None:
        self.selection_changed.emit()  # what acts on the focused entry follows the cursor (FG-03, FG-05)

    @override
    def selectionCommand(self, index: QModelIndex | QPersistentModelIndex, event: QEvent | None = None) -> QItemSelectionModel.SelectionFlag:
        """In the two-pane mode a click or an arrow key only moves the cursor; Ctrl and Shift mark as usual (FG-03)."""
        if self.two_pane:
            modifiers = event.modifiers() if isinstance(event, (QMouseEvent, QKeyEvent)) else QGuiApplication.keyboardModifiers()
            if not modifiers & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                return QItemSelectionModel.SelectionFlag.NoUpdate
        return super().selectionCommand(index, event)

    # --- type-ahead (FG-01) ------------------------------------------------------

    def _names(self) -> list[str | None]:
        return [entry.name if entry is not None else None for entry in self.files.rows]

    def _go_to_row(self, row: int) -> None:
        """The cursor to this row; outside the two-pane mode the row is the selection, as a click would make it."""
        selection = self.selectionModel()
        if selection is None:
            return
        index = self.files.index(row, 0)
        how = QItemSelectionModel.SelectionFlag.NoUpdate if self.two_pane else QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
        selection.setCurrentIndex(index, how)
        self.scrollTo(index)

    def type_name(self, typed: str, skip_current: bool = False, backwards: bool = False) -> bool:
        """The cursor to the first name, from the cursor on, that starts with what was typed; False, with a beep, where none does."""
        found = type_ahead(self._names(), typed, self.currentIndex().row(), skip_current, backwards)
        if found < 0:
            QApplication.beep()
            self.type_pause.start()  # what was typed stays a moment, so the person can type on
            return False
        self.typed = typed
        self._go_to_row(found)
        self.type_pause.start()
        self.typing_changed.emit(typed)
        return True

    def has_next_match(self) -> bool:
        row = self.currentIndex().row()
        return type_ahead(self._names(), self.typed, row, True) not in (-1, row)

    def stop_typing(self) -> None:
        self.type_pause.stop()
        if self.typed:
            self.typed = ""
            self.typing_changed.emit("")

    @override
    def focusNextPrevChild(self, next: bool) -> bool:
        """Tab while typing goes to the next name that fits, Shift+Tab to the one before; otherwise Tab moves the focus as usual."""
        if self.typed and self.hasFocus():
            self.type_name(self.typed, True, not next)
            return True
        return super().focusNextPrevChild(next)

    @override
    def focusOutEvent(self, event: QFocusEvent) -> None:
        self.stop_typing()
        super().focusOutEvent(event)

    # --- the column header menu (FG-07) -------------------------------------------

    def _header_pressed(self, _section: int) -> None:
        self.files.by_extension = False  # a click on a column sorts by that column: by extension only through Ctrl+F4

    def sort_by(self, column: int, by_extension: bool = False) -> None:
        """A sort key (Ctrl+F3 to Ctrl+F9): by this column; the same key again turns the order round."""
        if not 0 <= column < self.files.column_count:
            return
        sorted_column, order = self.files.sorted_by
        same = sorted_column == column and self.files.by_extension == by_extension
        order = Qt.SortOrder.DescendingOrder if same and order == Qt.SortOrder.AscendingOrder else Qt.SortOrder.AscendingOrder
        self.files.by_extension = by_extension
        header = self.header()
        header.blockSignals(True)
        header.setSortIndicator(column, order)
        header.blockSignals(False)
        self.files.sort(column, order)

    def header_menu(self, section: int) -> QMenu:
        """Right-click on a column header: sort by it, hide it, show or hide each column, fit the widths, the standard columns."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        column = section if 0 <= section < self.files.column_count else 0
        up = QAction(SORT_UP_TEXT, menu)
        up.triggered.connect(lambda: self._sort_column(column, Qt.SortOrder.AscendingOrder))
        down = QAction(SORT_DOWN_TEXT, menu)
        down.triggered.connect(lambda: self._sort_column(column, Qt.SortOrder.DescendingOrder))
        hide = QAction(HIDE_COLUMN_TEXT, menu)
        hide.triggered.connect(lambda: self.setColumnHidden(column, True))
        if column == 0:
            hide.setEnabled(False)  # greyed with its reason (OR-20)
            hide.setToolTip(NAME_STAYS_TEXT)
        for action in (up, down, hide):
            menu.addAction(action)
        menu.addSeparator()
        for number in range(self.files.column_count):
            shown = QAction(COLUMNS[number], menu)
            shown.setCheckable(True)
            shown.setChecked(not self.isColumnHidden(number))
            shown.toggled.connect(lambda on=False, number=number: self.setColumnHidden(number, not on))
            if number == 0:
                shown.setEnabled(False)
                shown.setToolTip(NAME_STAYS_TEXT)
            menu.addAction(shown)
        menu.addSeparator()
        fit = QAction(FIT_TEXT, menu)
        fit.triggered.connect(self.fit_columns)
        standard = QAction(STANDARD_COLUMNS_TEXT, menu)
        standard.triggered.connect(self.standard_columns)
        menu.addAction(fit)
        menu.addAction(standard)
        return menu

    def _header_menu_wanted(self, place: QPoint) -> None:
        header = self.header()
        menu = self.header_menu(header.logicalIndexAt(place))
        open_menu(menu, header.mapToGlobal(place))
        menu.deleteLater()

    def _sort_column(self, column: int, order: Qt.SortOrder) -> None:
        self.files.by_extension = False
        self.sortByColumn(column, order)
        self.files.sort(column, order)  # also when the arrow already stood there: by extension is over

    def fit_columns(self) -> None:
        for number in range(self.files.column_count):
            if not self.isColumnHidden(number):
                self.resizeColumnToContents(number)

    def standard_columns(self) -> None:
        """Every column shown at its first width, sorted by name."""
        for number in range(self.files.column_count):
            self.setColumnHidden(number, False)
            self.setColumnWidth(number, STANDARD_WIDTHS.get(number, STANDARD_OTHER_WIDTH))
        header = self.header()
        for number in range(self.files.column_count):
            place = header.visualIndex(number)
            if place != number:
                header.moveSection(place, number)
        self._sort_column(0, Qt.SortOrder.AscendingOrder)

    def set_two_pane(self, on: bool) -> None:
        self.two_pane = on
        self.selection_changed.emit()

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

    def set_file_colours(self, rules: tuple[ColourRule, ...], shown: bool = True) -> None:
        """The person's file colours (FQ-01); shown False leaves every row as its side shows it."""
        self.files.set_colours(rules, shown)

    @property
    def thumbnails_on(self) -> bool:
        return self.files.thumbnail_of is not None

    def set_thumbnails(self, thumbnail_of: ThumbnailOf | None) -> None:
        """The thumbnail view (FQ-02): tall rows, a file's picture where it has one; None goes back to the plain list."""
        if thumbnail_of is self.files.thumbnail_of:
            return
        self.files.thumbnail_of = thumbnail_of
        self.setIconSize(QSize(THUMBNAIL_PIXELS, THUMBNAIL_PIXELS) if thumbnail_of is not None else QSize())
        self.view_moved.emit()

    def thumbnail_arrived(self, name: str) -> None:
        """A picture came: its row is drawn again; anything else stays exactly as it is."""
        index = self.index_of(name)
        if index.isValid():
            self.files.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def entries_in_view(self) -> list[RemoteEntry]:
        """The entries whose rows can be seen now, top to bottom."""
        rows = self.files.rows
        viewport = self.viewport()
        if not rows or viewport.height() <= 0:
            return []
        top = self.indexAt(QPoint(1, 1)).row()
        bottom = self.indexAt(QPoint(1, max(viewport.height() - 2, 1))).row()
        first = top if top >= 0 else 0
        last = bottom if bottom >= 0 else len(rows) - 1
        return [entry for entry in rows[first : last + 1] if entry is not None]

    def _scrolled(self, _value: int) -> None:
        self.view_moved.emit()

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.view_moved.emit()

    def visible_entries(self) -> list[RemoteEntry]:
        return [row for row in self.files.rows if row is not None]

    def _fits(self, entry: RemoteEntry) -> bool:
        if not self.show_hidden and entry.name.startswith("."):
            return False
        return not self.name_mask or entry.is_folder or fnmatch.fnmatch(entry.name.casefold(), self.name_mask.casefold())

    def _fill(self, keep: set[str], current: str | None) -> None:
        self.stop_typing()  # other rows: what was typed searched the rows before (FG-01)
        shown = [entry for entry in self.all_entries if self._fits(entry)]
        dotted = [entry for entry in self.all_entries if entry.name.startswith(".")]
        self.dot_count = len(dotted)
        self.hidden_count = 0 if self.show_hidden else len(dotted)
        self.filtered_count = len(self.all_entries) - len(shown) - self.hidden_count
        rows: list[RemoteEntry | None] = [None] if self.has_parent else []
        self.files.set_rows([*rows, *shown])
        selection = self.selectionModel()
        if selection is None:
            return
        if current is not None:
            selection.setCurrentIndex(self.index_of(current), QItemSelectionModel.SelectionFlag.NoUpdate)
        chosen = self.rows_selection(row for row, entry in enumerate(self.files.rows) if entry is not None and entry.name in keep)  # also a refresh keeps few ranges
        selection.select(chosen, QItemSelectionModel.SelectionFlag.Select)
        self.selection_changed.emit()
        self.view_moved.emit()

    def _activated(self, index: QModelIndex) -> None:
        if self._right_release:
            return  # a right-button release never opens anything, also on a desktop that opens with one click (FG-14)
        self.stop_typing()
        self.opened.emit(self.files.rows[index.row()])

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """A typed letter finds a name (FG-01): Backspace takes the last letter back, Escape starts afresh; Space marks in the two-pane mode."""
        key = event.key()
        held = event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier)
        if self.typed and not held:
            if key == Qt.Key.Key_Escape:
                self.stop_typing()
                return
            if key == Qt.Key.Key_Backspace:
                if len(self.typed) > 1:
                    self.type_name(self.typed[:-1])
                else:
                    self.stop_typing()
                return
        text = event.text()
        if key == Qt.Key.Key_Space and not held and not self.typed and self.two_pane:
            self.toggle_row(self.currentIndex().row())
            return
        if text and text.isprintable() and not held and (key != Qt.Key.Key_Space or self.typed):
            self.type_name(self.typed + text)
            return
        if key == Qt.Key.Key_Backspace and self.has_parent:
            self.stop_typing()
            self.opened.emit(None)
            return
        super().keyPressEvent(event)

    @override
    def keyboardSearch(self, search: str) -> None:
        pass  # the list's own search is the type-ahead above

    # --- drag and drop ------------------------------------------------------

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._pressed_at = event.position().toPoint()
        self._menu_waiting = None
        super().mousePressEvent(event)

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """In the two-pane mode with nothing marked, the focused entry is dragged; Qt drags only what is selected."""
        start = self._pressed_at
        held = event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        if start is not None and held and self.two_pane and not self.selected_entries() and self.current_entry() is not None:
            if (event.position().toPoint() - start).manhattanLength() > QApplication.startDragDistance() and self.indexAt(start).row() == self.currentIndex().row():
                self._pressed_at = None
                self.startDrag(Qt.DropAction.CopyAction)
                return
        super().mouseMoveEvent(event)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._right_release = event.button() != Qt.MouseButton.LeftButton  # Qt activates on any button where one click opens
        try:
            super().mouseReleaseEvent(event)
        finally:
            self._right_release = False
        self._pressed_at = None
        waiting = self._menu_waiting
        if event.button() == Qt.MouseButton.RightButton and waiting is not None:
            self._menu_waiting = None
            self.customContextMenuRequested.emit(waiting)  # the button came up without a drag: the menu after all

    @override
    def viewportEvent(self, event: QEvent) -> bool:
        """A right-click menu that comes while the button is still down waits until it comes up: the press may start a drag (FG-14)."""
        if event.type() == QEvent.Type.ContextMenu and isinstance(event, QContextMenuEvent) and event.reason() == QContextMenuEvent.Reason.Mouse and QGuiApplication.mouseButtons() & Qt.MouseButton.RightButton:
            self._menu_waiting = event.pos()
            return True
        return super().viewportEvent(event)

    @override
    def startDrag(self, supportedActions: Qt.DropAction) -> None:
        chosen = self.acting_entries()
        if not chosen:
            return
        self._menu_waiting = None  # it became a drag: no menu when the button comes up
        mime = self.drag_data(bool(QGuiApplication.mouseButtons() & Qt.MouseButton.RightButton))
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

    def drag_data(self, right: bool = False) -> QMimeData:
        """right: the drag is made with the right mouse button, so its drop here asks with a menu (FG-14)."""
        mime = QMimeData()
        mime.setData(LOCAL_FILES if self.kind == FROM_LOCAL else REMOTE_FILES, QByteArray(DRAG_MARK))
        if right:
            mime.setData(RIGHT_DRAG, QByteArray(DRAG_MARK))
        if self.kind == FROM_LOCAL:  # so a file manager or another program can take them too
            mime.setUrls([QUrl.fromLocalFile(os.path.join(self.folder, entry.name)) for entry in self.acting_entries()])
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
        return bool(folder) and folder not in {entry.name for entry in self.acting_entries()}

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
        right = source != FROM_OUTSIDE and mime.data(RIGHT_DRAG).data() == DRAG_MARK
        (self.right_dropped if right else self.dropped).emit(source, paths, self._folder_at(position))
