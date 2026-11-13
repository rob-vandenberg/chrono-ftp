"""The Synchronize window (block FF): its choices on top, and below them the checklist of every planned action (FF-04). Each row has a
tick, what it would do, and the size and date of both sides; the rows can be sorted by any column and selected by action, the status
line says how many and how much per action, and nothing happens until Apply (D-038)."""

from typing import override

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QMenu, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.connections.base import RemoteEntry, shown_name
from chrono_ftp.engine.listing import size_text
from chrono_ftp.engine.sizes import Count
from chrono_ftp.engine.synchronize import COPIES, DELETES, Action, Criterion, Options, Row, Total, can_turn, text_of, totals, turned, why_no_turn
from chrono_ftp.ui.filelist import date_text
from chrono_ftp.ui.plaintext import plain_label
from chrono_ftp.ui.synchronizeoptions import SynchronizeOptions

# --- Version ------------------------------------------------------------
__version__ = "comparedialog.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: Block FF: the window gets its choices (direction, mode, criterion, deletes, scope, masks) and becomes a checklist of every
#          planned action, sortable by any column, with Select by action, the count and size per action, Reverse for one row, the size
#          of a one-sided folder counted in the background, and a progress bar that uses the last scan of the same folder pair; Apply
#          names how many items will be deleted and asks first (FF-01 to FF-13, D-038 changed, D-091 to D-095); review round 1: a scan
#          can be stopped from the same button, and Reverse is greyed with its reason on a row it has no meaning for
# v0.0.42: Compare Contents: the files of the same size with other dates are compared by their checksums, greyed with its reason where
#          the connection computes none, and stoppable; rows of other content go to the side with the later date (FE-01); review
#          round 2: a stopped comparison says so
# v0.0.12: The folders in the status line are shown as written, never read as HTML
# v0.0.6: Initial: what differs between the two folders; the person ticks what is copied, nothing is ever deleted

WINDOW_TITLE: str = "Synchronize"
WIDTH: int = 1000
HEIGHT: int = 640
COLUMNS: tuple[str, ...] = ("Name", "Action", "Size here", "Date here", "Size there", "Date there", "Difference")
NAME_COLUMN: int = 0
ACTION_COLUMN: int = 1
APPLY_TEXT: str = "Apply"
COMPARE_AGAIN_TEXT: str = "Compare Again"
REVERSE_TEXT: str = "Reverse"
REVERSE_TIP: str = "Turns the selected rows around: a copy goes the other way, a delete becomes the copy that keeps the file."
SELECT_TEXT: str = "Select"
SELECT_ALL_TEXT: str = "Everything"
SELECT_NOTHING_TEXT: str = "Nothing"
CONTENTS_TEXT: str = "Compare Contents"
STOP_TEXT: str = "Stop"
CONTENTS_TIP: str = "Compares the files of the same size with other dates by their checksums, on both sides: those with the same content are left out."
NO_SAME_SIZE_TEXT: str = "No files of the same size with other dates to compare."
CONTENTS_RUNNING_TEXT: str = "Comparing the contents by {algorithm} checksums…"
CONTENTS_DONE_TEXT: str = "{left} with the same content were left out; {count} differences remain."
CONTENTS_UNKNOWN_TEXT: str = " {unknown} could not be compared and stay as they were."
CONTENTS_STOPPED_TEXT: str = "Comparing the contents was stopped; the rows stay as they were."
SCANNING_TEXT: str = "Looking at {here} and {there}…"
SAME_TEXT: str = "These folders are the same."
NOTHING_TO_DO_TEXT: str = "Nothing to do with these choices."
CHOICES_CHANGED_TEXT: str = "The choices changed: press Compare Again to see the new plan."
SCAN_STOPPED_TEXT: str = "Comparing was stopped; the rows stay as they were."
MASK_FAULT_TEXT: str = "The mask could not be read, so every file is looked at: {why}"
COUNTING_TEXT: str = "counting…"
ROW_DATA: int = int(Qt.ItemDataRole.UserRole)
SORT_DATA: int = int(Qt.ItemDataRole.UserRole) + 1


class _Cell(QTableWidgetItem):
    """A cell that sorts by what it means, not by how it reads: a size by its number, a date by its moment."""

    def __init__(self, text: str, key: object = None) -> None:
        super().__init__(text)
        self.key: object = key if key is not None else text

    @override
    def __lt__(self, other: QTableWidgetItem) -> bool:
        mine, theirs = self.key, getattr(other, "key", other.text())
        if isinstance(mine, (int, float)) and isinstance(theirs, (int, float)):
            return mine < theirs
        return str(mine) < str(theirs)


def _size_key(entry: RemoteEntry | None, count: Count | None) -> tuple[str, float]:
    if count is not None:
        return f"{size_text(count.size)}{'' if count.complete else ' at least'}", float(count.size)
    if entry is None:
        return "", -1.0
    if entry.is_folder:
        return COUNTING_TEXT, -1.0
    return (size_text(entry.size), float(entry.size)) if entry.size_known else ("", -1.0)


def _date_key(entry: RemoteEntry | None) -> tuple[str, float]:
    if entry is None or entry.modified is None:
        return "", -1.0
    return date_text(entry.modified), entry.modified


class CompareDialog(QDialog):
    """The Synchronize window. Its rows are the plan; the window itself never touches a file."""

    apply_requested = Signal(object)  # the rows ticked, in the order they were planned
    contents_requested = Signal()  # Compare Contents: the window runs the checksum step
    contents_stopped = Signal()
    scan_requested = Signal()  # the choices changed: the scan runs again
    scan_stopped = Signal()  # Stop while a scan runs: it ends and the rows stay as they were
    sizes_wanted = Signal(object)  # the one-sided folders whose size is counted in the background (FF-06)

    def __init__(self, local_folder: str, remote_folder: str, parent: QWidget | None = None, contents_why: str = "", options: Options | None = None, why_no_timestamps: str = "", anything_selected: bool = False, history: tuple[str, ...] = (), on_a_server: bool = True) -> None:
        super().__init__(parent)
        self.contents_why: str = contents_why  # why the connection can't compare contents; empty when it can (OR-20)
        self.on_a_server: bool = on_a_server
        self.comparing: bool = False
        self.scanning: bool = False
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WIDTH, HEIGHT)
        self.rows: list[Row] = []
        self.here_folder: str = local_folder
        self.there_folder: str = remote_folder
        self.choices: SynchronizeOptions = SynchronizeOptions(options or Options(), contents_why, why_no_timestamps, anything_selected, history)
        self.status = plain_label(SCANNING_TEXT.format(here=shown_name(local_folder), there=shown_name(remote_folder)))
        self.counts = plain_label("")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSortIndicatorShown(True)
        self.apply_button = QPushButton(APPLY_TEXT)
        self.apply_button.setEnabled(False)
        self.again_button = QPushButton(COMPARE_AGAIN_TEXT)
        self.again_button.setAutoDefault(False)
        self.reverse_button = QPushButton(REVERSE_TEXT)
        self.reverse_button.setAutoDefault(False)
        self.reverse_button.setToolTip(REVERSE_TIP)
        self.reverse_button.setEnabled(False)
        self.select_button = QPushButton(SELECT_TEXT)
        self.select_button.setAutoDefault(False)
        self.select_menu: QMenu = QMenu(self.select_button)
        self.select_menu.setToolTipsVisible(True)
        self.select_button.setMenu(self.select_menu)
        self.close_button = QPushButton("Close")
        self.contents_button = QPushButton(CONTENTS_TEXT)
        self.contents_button.setAutoDefault(False)
        self.contents_button.setEnabled(False)
        self._build()
        self.choices.changed.connect(self._choices_changed)
        self.apply_button.clicked.connect(self._apply)
        self.close_button.clicked.connect(self.close)
        self.again_button.clicked.connect(self._again)
        self.reverse_button.clicked.connect(self._reverse)
        self.contents_button.clicked.connect(self._contents_pressed)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemChanged.connect(self._tick_changed)
        self._contents_state()
        self._fill_select_menu()

    def _build(self) -> None:
        row = QHBoxLayout()
        row.addWidget(self.again_button)
        row.addWidget(self.reverse_button)
        row.addWidget(self.select_button)
        row.addWidget(self.contents_button)
        row.addStretch(1)
        row.addWidget(self.apply_button)
        row.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.choices)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.counts)
        layout.addLayout(row)

    # --- the scan ------------------------------------------------------------

    def chosen(self) -> Options:
        return self.choices.chosen()

    def _choices_changed(self) -> None:
        """A choice that changes the plan: the person presses Compare Again, so no scan ever starts behind their back (G-2)."""
        self.again_button.setDefault(True)
        if not self.scanning:
            self.status.setText(CHOICES_CHANGED_TEXT)

    def _again(self) -> None:
        """While a scan runs the same button stops it, as Compare Contents does: a scan of a big tree takes minutes (FF-13)."""
        if self.scanning:
            self.scan_stopped.emit()
            return
        self.scan_requested.emit()

    def scan_started(self, expected: int = 0) -> None:
        """The progress bar counts the names looked at, against the count of the last scan of the same folder pair (FF-13); without
        one it only moves."""
        self.scanning = True
        self.progress.setRange(0, max(0, expected))
        self.progress.setValue(0)
        self.progress.show()
        self.apply_button.setEnabled(False)
        self.again_button.setText(STOP_TEXT)
        self.again_button.setEnabled(True)
        self.status.setText(SCANNING_TEXT.format(here=shown_name(self.here_folder), there=shown_name(self.there_folder)))

    def scan_progress(self, seen: int) -> None:
        if self.progress.maximum() and seen > self.progress.maximum():
            self.progress.setRange(0, 0)  # more than last time: it only moves from here on, never jumps back
        self.progress.setValue(seen)

    def show_rows(self, rows: list[Row]) -> None:
        """The plan as it is now. The ticks the person set are kept where the row is still there."""
        self.scanning = False
        self.progress.hide()
        self.again_button.setText(COMPARE_AGAIN_TEXT)
        self.again_button.setEnabled(True)
        self.again_button.setDefault(False)
        kept = {(row.folder, row.name, row.action): row.ticked for row in self.rows}
        self.rows = [row if (row.folder, row.name, row.action) not in kept else _ticked(row, kept[(row.folder, row.name, row.action)]) for row in rows]
        self._fill()
        self.status.setText(f"{len(self.rows)} rows" if self.rows else SAME_TEXT)
        self.apply_button.setEnabled(bool(self.rows))
        self._contents_state()
        self._fill_select_menu()
        wanted = [row for row in self.rows if row.is_folder and row.size is None and (row.here is None or row.there is None)]
        if wanted:
            self.sizes_wanted.emit(wanted)  # counted in the background, so the window never waits (FF-06)

    def show_found(self, rows: list[Row]) -> None:
        """The name the window had before block FF; the rows are the plan."""
        self.show_rows(rows)

    def _fill(self) -> None:
        order = self.table.horizontalHeader().sortIndicatorOrder()
        column = self.table.horizontalHeader().sortIndicatorSection()
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.rows))
        for number, row in enumerate(self.rows):
            name = _Cell(shown_name(row.path if not row.from_name else f"{row.from_path} → {row.path}"))
            if row.action != Action.NOTHING:
                name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                name.setCheckState(Qt.CheckState.Checked if row.ticked else Qt.CheckState.Unchecked)
            name.setData(ROW_DATA, number)
            self.table.setItem(number, NAME_COLUMN, name)
            here_size, here_key = _size_key(row.here, row.size if row.there is None else None)
            there_size, there_key = _size_key(row.there, row.size if row.here is None else None)
            here_date, here_moment = _date_key(row.here)
            there_date, there_moment = _date_key(row.there)
            cells = (
                _Cell("" if row.action == Action.NOTHING else text_of(row.action.value, self.on_a_server)),
                _Cell(here_size, here_key),
                _Cell(here_date, here_moment),
                _Cell(there_size, there_key),
                _Cell(there_date, there_moment),
                _Cell(text_of(row.reason, self.on_a_server)),
            )
            for place, cell in enumerate(cells, 1):
                cell.setData(ROW_DATA, number)
                self.table.setItem(number, place, cell)
        self.table.blockSignals(False)
        self.table.setSortingEnabled(True)
        if column >= 0:
            self.table.sortItems(column, order)
        self._show_counts()

    def _row_of(self, item: QTableWidgetItem | None) -> int:
        number = item.data(ROW_DATA) if item is not None else None
        return number if isinstance(number, int) and 0 <= number < len(self.rows) else -1

    def _tick_changed(self, item: QTableWidgetItem) -> None:
        number = self._row_of(item)
        if number < 0 or item.column() != NAME_COLUMN:
            return
        self.rows[number] = _ticked(self.rows[number], item.checkState() == Qt.CheckState.Checked)
        self._show_counts()

    # --- what the person does with the rows -------------------------------------

    def _selection_changed(self) -> None:
        """Reverse acts only on rows it has a meaning for; where none of the selected rows has, it is greyed with the reason (OR-20)."""
        chosen = [self.rows[number] for number in self.selected_rows()]
        usable = [row for row in chosen if can_turn(row)]
        self.reverse_button.setEnabled(bool(usable))
        why = next((why_no_turn(row) for row in chosen if why_no_turn(row)), "") if not usable else ""
        self.reverse_button.setToolTip(why or REVERSE_TIP)

    def selected_rows(self) -> list[int]:
        numbers = {self._row_of(item) for item in self.table.selectedItems()}
        return sorted(number for number in numbers if number >= 0)

    def _reverse(self) -> None:
        """Reverse of one row (FF-05): the other way round, and back when it is pressed again. A row it has no meaning for is left
        exactly as it is (can_turn)."""
        for number in self.selected_rows():
            self.rows[number] = turned(self.rows[number])
        self._fill()
        self._fill_select_menu()

    def _fill_select_menu(self) -> None:
        self.select_menu.clear()
        everything = self.select_menu.addAction(SELECT_ALL_TEXT)
        everything.triggered.connect(lambda: self._select(None, True))
        nothing = self.select_menu.addAction(SELECT_NOTHING_TEXT)
        nothing.triggered.connect(lambda: self._select(None, False))
        self.select_menu.addSeparator()
        for action in sorted({row.action for row in self.rows if row.action != Action.NOTHING}, key=lambda item: item.name):
            item = self.select_menu.addAction(text_of(action.value, self.on_a_server))
            item.triggered.connect(lambda _checked=False, chosen=action: self._select(chosen, True))
        self.select_button.setEnabled(bool(self.rows))

    def _select(self, action: Action | None, ticked: bool) -> None:
        self.rows = [_ticked(row, ticked) if row.action != Action.NOTHING and (action is None or row.action == action) else row for row in self.rows]
        self._fill()

    def ticked(self) -> list[Row]:
        return [row for row in self.rows if row.ticked and row.action != Action.NOTHING]

    def deletes_ticked(self) -> int:
        return len([row for row in self.ticked() if row.action in DELETES])

    def _apply(self) -> None:
        self.apply_requested.emit(self.ticked())

    def _show_counts(self) -> None:
        found = totals(self.rows, ticked_only=True)
        if not found:
            self.counts.setText("")
            return
        self.counts.setText("   ".join(f"{text_of(action.value, self.on_a_server)}: {_amount(total)}" for action, total in sorted(found.items(), key=lambda pair: pair[0].name)))

    # --- the size of a one-sided folder (FF-06) ----------------------------------

    def size_found(self, folder: str, name: str, count: Count) -> None:
        for number, row in enumerate(self.rows):
            if row.folder == folder and row.name == name and row.is_folder:
                self.rows[number] = _sized(row, count)
        self._fill()

    # --- the checksum step (block FE) --------------------------------------------

    def _contents_state(self) -> None:
        if self.comparing:
            return
        same_size = any(row.here is not None and row.there is not None and not row.is_folder and row.here.size == row.there.size for row in self.rows)
        why = self.contents_why or ("" if same_size else NO_SAME_SIZE_TEXT)
        self.contents_button.setEnabled(not why)
        self.contents_button.setToolTip(why or CONTENTS_TIP)  # greyed with its reason (OR-20)

    def _contents_pressed(self) -> None:
        if self.comparing:
            self.contents_stopped.emit()
            return
        self.contents_requested.emit()

    def contents_started(self, algorithm: str) -> None:
        self.comparing = True
        self.contents_button.setText(STOP_TEXT)
        self.contents_button.setToolTip("")
        self.apply_button.setEnabled(False)
        self.status.setText(CONTENTS_RUNNING_TEXT.format(algorithm=algorithm))

    def contents_ended(self, rows: list[Row] | None, same: int = 0, unknown: int = 0) -> None:
        """rows None: stopped or failed, the rows stay as they were."""
        self.comparing = False
        self.contents_button.setText(CONTENTS_TEXT)
        if rows is None:
            self.status.setText(CONTENTS_STOPPED_TEXT)  # a failure says its own reason after this
            self.apply_button.setEnabled(bool(self.rows))
            self._contents_state()
            return
        self.show_rows(rows)
        text = CONTENTS_DONE_TEXT.format(left="1 file" if same == 1 else f"{same} files", count=len(rows))
        self.status.setText(text + (CONTENTS_UNKNOWN_TEXT.format(unknown=unknown) if unknown else ""))

    def scan_was_stopped(self) -> None:
        """The person pressed Stop: the rows stay exactly as they were, and the window says so calmly (OR-23)."""
        self.show_failure(SCAN_STOPPED_TEXT)
        self.apply_button.setEnabled(bool(self.rows))

    def show_failure(self, text: str) -> None:
        self.scanning = False
        self.progress.hide()
        self.again_button.setText(COMPARE_AGAIN_TEXT)
        self.again_button.setEnabled(True)
        self.status.setText(text)

    @override
    def done(self, result: int) -> None:
        if self.comparing:
            self.contents_stopped.emit()  # a comparison that runs stops with the window
        if self.scanning:
            self.scan_stopped.emit()  # and so does a scan: nothing is left running behind a closed window
        super().done(result)


def _ticked(row: Row, ticked: bool) -> Row:
    return Row(row.folder, row.name, row.action, row.here, row.there, ticked, row.reason, row.from_folder, row.from_name, row.size)


def _sized(row: Row, count: Count) -> Row:
    return Row(row.folder, row.name, row.action, row.here, row.there, row.ticked, row.reason, row.from_folder, row.from_name, count)


def _amount(total: Total) -> str:
    return f"{total.count}" if not total.size else f"{total.count} ({size_text(total.size)})"


def uploads(row: Row) -> bool:
    """Which way a copy row goes."""
    return row.action in COPIES and row.action == Action.COPY_THERE


def wants_checksums(options: Options) -> bool:
    return options.criterion == Criterion.CHECKSUM
