"""The Synchronize window (block FF): its choices on top, and below them the checklist of every planned action (FF-04). Each row has a
tick, what it would do, and the size and date of both sides; the rows can be sorted by any column and selected by action, the status
line says how many and how much per action, and nothing happens until the person presses Start (D-038, D-097)."""

from typing import override

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QMenu, QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.connections.base import RemoteEntry, shown_name
from chrono_ftp.engine.listing import size_text
from chrono_ftp.engine.sizes import Count
from chrono_ftp.engine.synchronize import COPIES, DELETES, HERE, THERE_FOLDER, THERE_SERVER, Action, Criterion, Options, Row, Total, can_turn, text_of, totals, turned, why_no_turn
from chrono_ftp.ui.filelist import date_text
from chrono_ftp.ui.startabort import ABORT_TEXT as ABORT_TEXT  # given on: the window tests and the synchronize part read them here
from chrono_ftp.ui.startabort import ABORT_TIP, START_TEXT as START_TEXT, WORKING_TEXT, StartAbortButton
from chrono_ftp.ui.plaintext import counted, was, plain_label
from chrono_ftp.ui.synchronizeoptions import SynchronizeOptions

# --- Version ------------------------------------------------------------
__version__ = "comparedialog.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 4: the line after looking inside the files reads as a person writes it, for one file and for none (OR-23)
# v0.0.45: The Start/Abort button and the words of work on this computer come from ui/startabort.py: there is one of them, shared
#          with the paste of files on this computer (D-097, D-099)
# v0.0.43: Block FF: the window gets its choices (direction, mode, criterion, deletes, scope, masks) and becomes a checklist of every
#          planned action, sortable by any column, with Select by action, the count and size per action, Reverse for one row, the size
#          of a one-sided folder counted in the background, and a progress bar that uses the last scan of the same folder pair; Apply
#          names how many items will be deleted and asks first (FF-01 to FF-13, D-038 changed, D-091 to D-095); review round 1: a scan
#          can be stopped from the same button, and Reverse is greyed with its reason on a row it has no meaning for
# v0.0.44: The two steps are named as a person would: Compare the Folders Again / Stop Comparing the Folders against Look Inside the
#          Files / Stop Looking Inside the Files, and no word on the window is one only a programmer uses
# v0.0.44: The two stops say plainly which is which: Compare the Folders Again / Stop Comparing the Folders against Look Inside the
#          Files / Stop Looking Inside the Files; no word on the window names a button that is not there
# v0.0.44: Review by the owner: Apply became Start, and the same button reads Abort while the work runs; the window stays on screen
#          while two folders on this computer are synchronized, showing what it is doing and how far it is; every long-running thing
#          has a button on screen that stops it; the words a person reads were gone over one by one (D-097)
# v0.0.42: Compare Contents: the files of the same size with other dates are compared by their checksums, greyed with its reason where
#          the connection computes none, and stoppable; rows of other content go to the side with the later date (FE-01); review
#          round 2: a stopped comparison says so
# v0.0.12: The folders in the status line are shown as written, never read as HTML
# v0.0.6: Initial: what differs between the two folders; the person ticks what is copied, nothing is ever deleted

WINDOW_TITLE: str = "Synchronize"
WIDTH: int = 1000
HEIGHT: int = 640
NAME_COLUMN: int = 0
ACTION_COLUMN: int = 1
COLUMN_COUNT: int = 7
NAME_HEADING: str = "Name"
ACTION_HEADING: str = "What will happen"
SIZE_HEADING: str = "Size on {side}"
DATE_HEADING: str = "Date on {side}"
WHY_HEADING: str = "Why"
WAIT_FOR_IT_WHY: str = "Wait until the list is finished."
START_TIP: str = "Does everything that is ticked. Nothing happens before you press this."
START_QUEUE_TIP: str = "Puts everything that is ticked in the queue under the two panels, where you can watch, pause and stop it."
NOTHING_TICKED_WHY: str = "Nothing is ticked yet, so there is nothing to start."
AFTER_A_RUN_WHY: str = "This list is from before the last run. Press Compare the Folders Again for a fresh look."
COMPARE_AGAIN_TEXT: str = "Compare the Folders Again"
COMPARE_AGAIN_TIP: str = "Goes through both folders again with the choices above and makes a new list."
STOP_COMPARING_TEXT: str = "Stop Comparing the Folders"
STOP_COUNTING_TEXT: str = "Stop Counting the Folders"
STOP_COUNTING_TIP: str = "Stops working out how much is in the folders that are on one side only."
REVERSE_TEXT: str = "Act on the Other Side"
REVERSE_TIP: str = "Makes the selected rows act on the other side: a copy goes the other way, and a delete becomes the copy that keeps the file."
SELECT_TEXT: str = "Tick Rows"
SELECT_TIP: str = "Ticks every row, no row, or only the rows that do one thing."
SELECT_ALL_TEXT: str = "Everything"
SELECT_NOTHING_TEXT: str = "Nothing"
CONTENTS_TEXT: str = "Look Inside the Files"
STOP_CONTENTS_TEXT: str = "Stop Looking Inside the Files"
CONTENTS_TIP: str = "Opens every file of the same size on both sides and reads it from beginning to end, then takes out the rows whose two files turn out to hold exactly the same thing. This looks inside the files themselves; it does not go through the folders again."
NO_SAME_SIZE_TEXT: str = "There are no files of the same size on both sides, so there is nothing to look inside."
CONTENTS_RUNNING_TEXT: str = "Looking inside the files on both sides…"
CONTENTS_DONE_TEXT: str = "{left} turned out to be the same on both sides and {is_gone} from the list; {count} left to do."
CONTENTS_UNKNOWN_TEXT: str = " {unknown} could not be opened on one of the two sides and {stays} as {it} {was}."
CONTENTS_STOPPED_TEXT: str = "Looking inside the files was stopped; the list stays as it was."
SCANNING_TEXT: str = "Going through {here} and {there}…"
COUNTING_RUNNING_TEXT: str = "Working out how much is in {count}…"
ONE_FOLDER_TEXT: str = "1 folder"
FOLDERS_TEXT: str = "{count} folders"
COUNTING_STOPPED_TEXT: str = "Counting the folders was stopped; the sizes it had not reached stay unknown."
TO_DO_TEXT: str = "{count} to do. Nothing happens until you press Start."
ONE_TO_DO_TEXT: str = "1 thing"
THINGS_TO_DO_TEXT: str = "{count} things"
NOTHING_LEFT_TEXT: str = "nothing"
SAME_TEXT: str = "These two folders are already the same: there is nothing to do."
CHOICES_CHANGED_TEXT: str = "The choices changed: press Compare the Folders Again to see what that means."
SCAN_STOPPED_TEXT: str = "Comparing the folders was stopped; the list stays as it was."
NOTHING_ACTION_TEXT: str = "Nothing"
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

    start_requested = Signal(object)  # Start: the rows ticked, in the order they were planned
    work_stopped = Signal()  # Abort: the work that runs here ends after the file it is busy with
    contents_requested = Signal()  # Look Inside the Files: the window reads both sides through and drops the rows that match
    contents_stopped = Signal()
    scan_requested = Signal()  # the choices changed: the scan runs again
    scan_stopped = Signal()  # Stop while a scan runs: it ends and the rows stay as they were
    sizes_wanted = Signal(object)  # the one-sided folders whose size is worked out in the background (FF-06)
    counting_stopped = Signal()  # Stop Counting Folders: that work ends and what it did not reach stays unknown

    def __init__(self, local_folder: str, remote_folder: str, parent: QWidget | None = None, contents_why: str = "", options: Options | None = None, why_no_timestamps: str = "", anything_selected: bool = False, history: tuple[str, ...] = (), on_a_server: bool = True) -> None:
        super().__init__(parent)
        self.contents_why: str = contents_why  # why the connection can't compare contents; empty when it can (OR-20)
        self.on_a_server: bool = on_a_server
        self.comparing: bool = False
        self.scanning: bool = False
        self.counting_left: int = 0  # the folders on one side only whose size is still being worked out (FF-06)
        self.running: bool = False  # the work itself is being done, here on this computer (FF-14)
        self.ran: bool = False  # it ran once: the list is from before that run
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WIDTH, HEIGHT)
        self.rows: list[Row] = []
        self.here_folder: str = local_folder
        self.there_folder: str = remote_folder
        self.choices: SynchronizeOptions = SynchronizeOptions(options or Options(), contents_why, why_no_timestamps, anything_selected, history, on_a_server=on_a_server)
        self.status = plain_label(SCANNING_TEXT.format(here=shown_name(local_folder), there=shown_name(remote_folder)))
        self.counts = plain_label("")
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        there = THERE_SERVER if on_a_server else THERE_FOLDER
        self.table = QTableWidget(0, COLUMN_COUNT)
        self.table.setHorizontalHeaderLabels([NAME_HEADING, ACTION_HEADING, SIZE_HEADING.format(side=HERE), DATE_HEADING.format(side=HERE), SIZE_HEADING.format(side=there), DATE_HEADING.format(side=there), WHY_HEADING])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSortIndicatorShown(True)
        self.start_button = StartAbortButton(START_QUEUE_TIP if on_a_server else START_TIP)  # the one button of D-097, shared (ui/startabort.py)
        self.start_button.setEnabled(False)
        self.again_button = QPushButton(COMPARE_AGAIN_TEXT)
        self.again_button.setAutoDefault(False)
        self.again_button.setToolTip(COMPARE_AGAIN_TIP)
        self.reverse_button = QPushButton(REVERSE_TEXT)
        self.reverse_button.setAutoDefault(False)
        self.reverse_button.setToolTip(REVERSE_TIP)
        self.reverse_button.setEnabled(False)
        self.select_button = QPushButton(SELECT_TEXT)
        self.select_button.setAutoDefault(False)
        self.select_menu: QMenu = QMenu(self.select_button)
        self.select_menu.setToolTipsVisible(True)
        self.select_button.setMenu(self.select_menu)
        self.select_button.setToolTip(SELECT_TIP)
        self.close_button = QPushButton("Close")
        self.contents_button = QPushButton(CONTENTS_TEXT)
        self.contents_button.setAutoDefault(False)
        self.contents_button.setEnabled(False)
        self._build()
        self.choices.changed.connect(self._choices_changed)
        self.start_button.start_requested.connect(self._start_pressed)
        self.start_button.abort_requested.connect(self.work_stopped)
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
        row.addWidget(self.start_button)
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
        """One button for comparing: it compares again, and while something of that comparing runs it stops exactly that (D-097).
        A scan of a big tree takes minutes, and so can working out what is in the folders that are on one side only."""
        if self.scanning:
            self.scan_stopped.emit()
            return
        if self.counting_left:
            self.counting_stopped.emit()
            return
        self.scan_requested.emit()

    def _again_state(self) -> None:
        """The button always names the one thing it would do now: stop what runs, or compare again (OR-20)."""
        if self.scanning:
            text, tip = STOP_COMPARING_TEXT, ABORT_TIP
        elif self.counting_left:
            text, tip = STOP_COUNTING_TEXT, STOP_COUNTING_TIP
        else:
            text, tip = COMPARE_AGAIN_TEXT, COMPARE_AGAIN_TIP
        self.again_button.setText(text)
        self.again_button.setToolTip(tip)
        self.again_button.setEnabled(not self.running)

    def scan_started(self, expected: int = 0) -> None:
        """The progress bar counts the names looked at, against the count of the last scan of the same folder pair (FF-13); without
        one it only moves."""
        self.scanning = True
        self.progress.setRange(0, max(0, expected))
        self.progress.setValue(0)
        self.progress.show()
        self._start_state()
        self._again_state()
        self.status.setText(SCANNING_TEXT.format(here=shown_name(self.here_folder), there=shown_name(self.there_folder)))

    def scan_progress(self, seen: int) -> None:
        if self.progress.maximum() and seen > self.progress.maximum():
            self.progress.setRange(0, 0)  # more than last time: it only moves from here on, never jumps back
        self.progress.setValue(seen)

    def show_rows(self, rows: list[Row]) -> None:
        """The plan as it is now. The ticks the person set are kept where the row is still there."""
        self.scanning = False
        self.progress.hide()
        self.again_button.setDefault(False)
        kept = {(row.folder, row.name, row.action): row.ticked for row in self.rows}
        self.rows = [row if (row.folder, row.name, row.action) not in kept else _ticked(row, kept[(row.folder, row.name, row.action)]) for row in rows]
        self._fill()
        self.status.setText(TO_DO_TEXT.format(count=_things(len(self.rows))) if self.rows else SAME_TEXT)
        wanted = [row for row in self.rows if row.is_folder and row.size is None and (row.here is None or row.there is None)]
        self.counting_left = len(wanted)
        self._start_state()
        self._again_state()
        self._contents_state()
        self._fill_select_menu()
        if wanted:
            self.status.setText(COUNTING_RUNNING_TEXT.format(count=ONE_FOLDER_TEXT if len(wanted) == 1 else FOLDERS_TEXT.format(count=len(wanted))))
            self.sizes_wanted.emit(wanted)  # worked out in the background, so the window never waits (FF-06)

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
                _Cell(NOTHING_ACTION_TEXT if row.action == Action.NOTHING else text_of(row.action.value, self.on_a_server)),
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
        self._start_state()  # whatever changed the rows, Start says now whether there is anything to start (OR-20)

    def _row_of(self, item: QTableWidgetItem | None) -> int:
        number = item.data(ROW_DATA) if item is not None else None
        return number if isinstance(number, int) and 0 <= number < len(self.rows) else -1

    def _tick_changed(self, item: QTableWidgetItem) -> None:  # noqa: D102 - see below
        number = self._row_of(item)
        if number < 0 or item.column() != NAME_COLUMN:
            return
        self.rows[number] = _ticked(self.rows[number], item.checkState() == Qt.CheckState.Checked)
        self._show_counts()
        self._start_state()  # Start is greyed while nothing is ticked, and says why (OR-20)

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

    def _start_pressed(self) -> None:
        self.start_requested.emit(self.ticked())

    def _start_state(self) -> None:
        """Start, or Abort while the work runs; greyed with its reason where there is nothing to start (OR-20, D-097)."""
        if self.running:
            self.start_button.show_running(True)
            return
        why = "" if self.ticked() else AFTER_A_RUN_WHY if self.ran else NOTHING_TICKED_WHY
        if self.scanning or self.comparing:
            why = WAIT_FOR_IT_WHY
        self.start_button.show_ready(why, START_QUEUE_TIP if self.on_a_server else START_TIP)

    # --- the work itself, while it runs here on this computer (FF-14) ----------------

    def work_started(self, count: int) -> None:
        self.running = True
        self.ran = True
        self.progress.setRange(0, max(1, count))
        self.progress.setValue(0)
        self.progress.show()
        self.again_button.setEnabled(False)
        self.contents_button.setEnabled(False)
        self._start_state()

    def work_progress(self, done: int, count: int, name: str) -> None:
        self.progress.setValue(done)
        self.status.setText(WORKING_TEXT.format(done=done, count=count, name=shown_name(name)))

    def work_ended(self, text: str) -> None:
        """What was done and what was not, in words, with the window still on screen to read them (G-2)."""
        self.running = False
        self.progress.hide()
        self.again_button.setEnabled(True)
        self.status.setText(text)
        self._contents_state()
        self._start_state()

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
        self.counting_left = max(0, self.counting_left - 1)
        self._fill()
        if not self.counting_left and not self.running:
            self.status.setText(TO_DO_TEXT.format(count=_things(len(self.rows))) if self.rows else SAME_TEXT)
        self._again_state()

    def counting_was_stopped(self) -> None:
        """The person pressed Stop Counting Folders: what was not reached stays unknown, and the window says so calmly (OR-23)."""
        self.counting_left = 0
        self.status.setText(COUNTING_STOPPED_TEXT)
        self._again_state()

    # --- the checksum step (block FE) --------------------------------------------

    def _contents_state(self) -> None:
        if self.comparing or self.running:
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
        self.contents_button.setText(STOP_CONTENTS_TEXT)
        self.contents_button.setToolTip(ABORT_TIP)
        self._start_state()
        _ = algorithm  # how the two sides are compared is ours to pick, never a word the person has to know
        self.status.setText(CONTENTS_RUNNING_TEXT)

    def contents_ended(self, rows: list[Row] | None, same: int = 0, unknown: int = 0) -> None:
        """rows None: stopped or failed, the rows stay as they were."""
        self.comparing = False
        self.contents_button.setText(CONTENTS_TEXT)
        if rows is None:
            self.status.setText(CONTENTS_STOPPED_TEXT)  # a failure says its own reason after this
            self._start_state()
            self._contents_state()
            return
        self.show_rows(rows)
        text = CONTENTS_DONE_TEXT.format(left=counted(same, "file", "files"), is_gone="is gone" if same == 1 else "are gone", count=_things(len(rows)) if rows else NOTHING_LEFT_TEXT)
        unknown_text = CONTENTS_UNKNOWN_TEXT.format(unknown=counted(unknown, "file", "files"), stays="stays" if unknown == 1 else "stay", it="it" if unknown == 1 else "they", was=was(unknown))
        self.status.setText(text + (unknown_text if unknown else ""))

    def scan_was_stopped(self) -> None:
        """The person pressed Stop: the rows stay exactly as they were, and the window says so calmly (OR-23)."""
        self.show_failure(SCAN_STOPPED_TEXT)
        self._start_state()

    def show_failure(self, text: str) -> None:
        self.scanning = False
        self.progress.hide()
        self._again_state()
        self.status.setText(text)

    @override
    def done(self, result: int) -> None:
        if self.comparing:
            self.contents_stopped.emit()  # a comparison that runs stops with the window
        if self.scanning:
            self.scan_stopped.emit()  # and so does a scan: nothing is left running behind a closed window
        if self.counting_left:
            self.counting_stopped.emit()
        if self.running:
            self.work_stopped.emit()  # closing the window aborts the work it started (D-097)
        super().done(result)


def _things(count: int) -> str:
    return ONE_TO_DO_TEXT if count == 1 else THINGS_TO_DO_TEXT.format(count=count)


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
