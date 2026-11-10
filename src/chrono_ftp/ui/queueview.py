from collections.abc import Callable
from typing import override

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QKeySequence, QShowEvent, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QSpinBox, QTableWidget, QTableWidgetItem, QToolBar, QToolButton, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.transfers.queue import FEWEST_AT_ONCE, HELD_NEXT, HELD_PLACE, HELD_ROOM, MOST_AT_ONCE, Job, State
from chrono_ftp.ui import icons
from chrono_ftp.ui.afterqueue import CHOICE_TIPS, CHOICES, NOT_POSSIBLE_TIPS, STAY, WHEN_DONE_TIP
from chrono_ftp.ui.filelist import size_text
from chrono_ftp.ui.plaintext import box_text

# --- Version ------------------------------------------------------------
__version__ = "queueview.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Queue control (FC-02 to FC-04): buttons with hover texts for resume, pause, move up and down, run now, retry, speed limit,
#          delete, pause all, resume all and clear finished, greyed where they don't apply; the Delete key on the list; a switch to start
#          items by themselves; what happens when the queue is done; a column with each item's own speed limit; a paused item, one that
#          is being paused and one that is being deleted say so; the list follows the queue's order and leaves out what left it; Stop
#          for the selected items; a "When done" choice the computer does not allow is greyed once the box shows, and never taken; the
#          list keeps its scroll position and current row when it follows the queue's order; Stop All here too; the hover text of an
#          item whose tab is closed; a choice that is not allowed brings back the one before it; a chosen step dropped as no longer allowed
#          is told; several transfers at once (FC-01): the number in the title bar, each running item's own speed, and why an item
#          still waits while it could otherwise run, or that it runs next; "Transfers at once" is named alike here and in Settings
# v0.0.12: The time left, empty while the job waits for an answer; a job that waits says so; without a counted total the amount done;
#          a move says it deletes
#          the Direction and State columns fit their texts, and every cell has its whole text as hover text
# v0.0.7: QueuePanel: a title bar with a pin; the height is free
# v0.0.6: Names that are not valid UTF-8 are shown readable
# v0.0.5: Initial: the transfers with their progress (D-014)

COLUMNS: tuple[str, ...] = ("Name", "Direction", "Way", "State", "Progress", "Time left", "Speed", "Speed limit")
SPEED_COLUMN: int = 6
LIMIT_COLUMN: int = 7
HELD_TEXTS: dict[str, tuple[str, str]] = {  # why an item still waits, and its hover text (OR-23)
    HELD_PLACE: ("Waits for the same place", "Another transfer works in the same place; this item starts as soon as that one has ended."),
    HELD_ROOM: ("Waits for a free connection", "The server takes no more connections at the moment; this item starts as soon as another transfer to it has ended."),
    HELD_NEXT: ("Runs next", "This item starts as soon as a running transfer has ended."),
}
AT_ONCE_TEXT: str = "Transfers at once"  # one name for the setting, here and in Settings (OR-20)
AT_ONCE_TIP: str = "How many transfers run at the same time. Fewer lets the running ones finish first; more starts waiting items at once."
PAUSING_TEXT: str = "Pausing"
STOPPING_TEXT: str = "Stopping"
FAILED_HINT: str = "Retry tries it again and continues where it can; Delete removes it from the queue."
OWN_LIMIT_HINT: str = "This item's own speed limit. Without one, it shares the speed limit of the status bar with the other running items."
NO_LIMIT_TEXT: str = "None"
LIMIT_TITLE: str = "Speed Limit"
QUEUE_LIMIT_TEXT: str = "Use the speed limit of the status bar"
HIGHEST_LIMIT: int = 1_000_000
START_TEXT: str = "Start items by themselves"
START_TIP: str = "When this is off, a new item waits in the queue, paused, until you start it with Resume"
WHEN_DONE_TEXT: str = "When done"
CLOSED_TAB_TEXT: str = "Its tab is closed, so it can't go on."
NOT_CONNECTED_TAB_TEXT: str = "Its tab is not connected. Reconnect it first."
STOP_ALL_TEXT: str = "Stop every transfer that runs, waits or is paused"
ICON_SIZE: int = 18
QUEUE_ACTIONS: tuple[tuple[str, str, str, str, bool], ...] = (  # name, text, icon, hover text, text beside the icon
    ("resume", "Resume", "media-playback-start", "Start or continue the selected items", False),
    ("pause", "Pause", "media-playback-pause", "Pause the selected items; what arrived is kept, so Resume continues from there", False),
    ("stop", "Stop", "process-stop", "Stop the selected items; Retry queues them again", False),
    ("move_up", "Move Up", "arrow-up", "Move the selected items one place up among the items that wait", False),
    ("move_down", "Move Down", "arrow-down", "Move the selected items one place down among the items that wait", False),
    ("run_now", "Run Now", "media-skip-forward", "Run the selected item next, before the other items that wait", False),
    ("retry", "Retry", "view-refresh", "Queue the selected stopped or failed items again; they continue where they can", False),
    ("limit", "Speed Limit", "speedometer", "Set a speed limit for the selected items; it holds at once, also while they run", False),
    ("delete", "Delete", "edit-delete", "Remove the selected items from the queue (Del); a running item is stopped first", False),
    ("stop_all", "Stop All", "process-stop", STOP_ALL_TEXT, True),
    ("pause_all", "Pause All", "media-playback-pause", "Pause every item that waits or runs", True),
    ("resume_all", "Resume All", "media-playback-start", "Start or continue every paused item", True),
    ("clear_finished", "Clear Finished", "edit-clear-history", "Remove every finished, stopped and failed item from the queue", True),
)
DIRECTION_COLUMN: int = 1
STATE_COLUMN: int = 3
PROGRESS_COLUMN: int = 4
CELL_PADDING: int = 24  # the cell's own margins around its text
LONGEST_DIRECTION: str = "Download, then delete the source"
WAITING_TEXT: str = "Waiting for your answer"
HEIGHT: int = 130  # until the person drags it
LOWEST_HEIGHT: int = 70
TITLE: str = "Transfers"
MINUTE: int = 60
HOUR: int = 3600
PIN_TEXT: str = "Keep this list on the screen when no transfer is running"


def time_text(seconds: float | None) -> str:
    if seconds is None:
        return ""
    whole = int(seconds)
    if whole < MINUTE:
        return f"{whole} s"
    if whole < HOUR:
        return f"{whole // MINUTE} min {whole % MINUTE:02d} s"
    return f"{whole // HOUR} h {whole % HOUR // MINUTE:02d} min"


def direction_text(job: Job) -> str:
    direction = "Upload" if job.upload else "Download"
    return f"{direction}, then delete the source" if job.options.delete_source else direction


def state_text(job: Job) -> str:
    if job.waiting_for_answer:
        return WAITING_TEXT
    if job.state == State.RUNNING and job.delete_asked:
        return STOPPING_TEXT
    if job.state == State.RUNNING and job.after_stop == State.PAUSED:
        return PAUSING_TEXT
    if job.state == State.PAUSED:
        return job.state.value
    if job.state == State.WAITING and job.held in HELD_TEXTS:
        return HELD_TEXTS[job.held][0]
    return shown_name(job.message) or job.state.value


def speed_text(job: Job) -> str:
    """The item's own speed of late, while it runs; empty while it waits for an answer or the speed is not known yet."""
    if job.state != State.RUNNING or job.waiting_for_answer or job.speed is None:
        return ""
    return f"{size_text(int(job.speed))}/s"


def limit_text(job: Job) -> str:
    """The item's own speed limit; empty where the one of the status bar holds."""
    if job.limit is None:
        return ""
    return NO_LIMIT_TEXT if job.limit == 0 else f"{job.limit} KB/s"


class QueueView(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(list(COLUMNS))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        metrics = self.fontMetrics()
        state_texts = (WAITING_TEXT, *(text for text, _tip in HELD_TEXTS.values()))
        for column, texts in ((DIRECTION_COLUMN, (LONGEST_DIRECTION,)), (STATE_COLUMN, state_texts)):
            self.setColumnWidth(column, max(metrics.horizontalAdvance(text) for text in texts) + CELL_PADDING)  # the texts it shows fit; longer ones are in the hover text
        self.jobs: list[Job] = []

    def _row_of(self, job: Job) -> int | None:
        return next((row for row, item in enumerate(self.jobs) if item is job), None)

    def show_job(self, job: Job) -> None:
        row = self._row_of(job)
        if row is None:
            self.jobs.append(job)
            row = self.rowCount()
            self.insertRow(row)
            self.setCellWidget(row, PROGRESS_COLUMN, QProgressBar())
        running = job.state == State.RUNNING
        texts = (shown_name(job.name), direction_text(job), job.way, state_text(job))
        waiting = job.waiting_for_answer  # no time passes for the transfer while it waits for the person
        for column, text in enumerate((*texts, "", time_text(job.seconds_left) if running and not waiting else "", speed_text(job), limit_text(job))):
            if column == PROGRESS_COLUMN:
                continue
            item = QTableWidgetItem(text)
            tip = box_text(text) if text else ""  # the whole text, also where the column is narrower
            if column == STATE_COLUMN and job.state == State.FAILED:
                tip = box_text(f"{text}\n\n{FAILED_HINT}")
            elif column == STATE_COLUMN and job.state == State.WAITING and job.held in HELD_TEXTS:
                tip = HELD_TEXTS[job.held][1]
            elif column == LIMIT_COLUMN and text:
                tip = OWN_LIMIT_HINT
            item.setToolTip(tip)
            self.setItem(row, column, item)
        bar = self.cellWidget(row, PROGRESS_COLUMN)
        if isinstance(bar, QProgressBar):
            bar.setValue(job.percent)
            uncounted = running and not job.total and not job.percent  # counting first is off: what is done so far is all there is to show
            bar.setFormat(size_text(job.done) if uncounted else "%p%")

    def selected_jobs(self) -> list[Job]:
        """In the order of the list."""
        return [self.jobs[row] for row in sorted(index.row() for index in self.selectionModel().selectedRows()) if row < len(self.jobs)]  # also while a row goes

    def forget(self, job: Job) -> None:
        """A job that left the queue leaves the list."""
        row = self._row_of(job)
        if row is not None:
            self.removeRow(row)
            self.jobs.pop(row)

    def follow(self, jobs: list[Job]) -> None:
        """The rows in the queue's order, without what left it. The rows stay and only their contents move, so the scroll position, the
        current row and the selection stay with their jobs."""
        if len(jobs) == len(self.jobs) and all(first is second for first, second in zip(jobs, self.jobs)):
            return
        chosen = self.selected_jobs()
        current = self.jobs[self.currentRow()] if 0 <= self.currentRow() < len(self.jobs) else None
        scrolled = self.verticalScrollBar().value()
        for job in [item for item in self.jobs if not any(item is other for other in jobs)]:
            self.forget(job)
        known = [job for job in jobs if self._row_of(job) is not None]
        self.jobs = known
        for job in known:
            self.show_job(job)  # each row now shows the job at its place
        for job in jobs:
            if self._row_of(job) is None:
                self.show_job(job)
        selection = self.selectionModel()
        selection.clearSelection()
        row = self._row_of(current) if current is not None else None
        if row is not None:
            selection.setCurrentIndex(self.model().index(row, 0), selection.SelectionFlag.NoUpdate)  # the keyboard goes on where it was
        for job in chosen:
            row = self._row_of(job)
            if row is not None:
                selection.select(self.model().index(row, 0), selection.SelectionFlag.Select | selection.SelectionFlag.Rows)
        self.verticalScrollBar().setValue(scrolled)  # the list stays where the person had it


class WhenDoneBox(QComboBox):
    """What happens when the queue is done. Before its list opens, possible is asked for each choice, so one this computer can't do now is
    greyed with a hover text that says why (OR-7, OR-20)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        for value, text in CHOICES:
            self.addItem(text, value)
            self.setItemData(self.count() - 1, CHOICE_TIPS[value], Qt.ItemDataRole.ToolTipRole)
        self.setToolTip(WHEN_DONE_TIP)
        self.possible: Callable[[str], bool] = lambda _action: True
        self._last: int = 0
        self._correcting: bool = False
        self.dropped: Callable[[str], None] = lambda _value: None  # a chosen step the computer no longer allows was dropped
        self.currentIndexChanged.connect(self._chosen)

    def _chosen(self, index: int) -> None:
        """However it is chosen (the list, the arrow keys, the wheel): a choice this computer does not allow now never stays, and the
        choice before it comes back."""
        if self._correcting:
            return
        value = str(self.itemData(index))
        if value in NOT_POSSIBLE_TIPS and not self.possible(value):
            earlier = self._last if self._last != index else 0
            self._correcting = True
            try:
                self.check_choices()
                self.setCurrentIndex(earlier)
            finally:
                self._correcting = False
            return
        self._last = index

    @override
    def showEvent(self, event: QShowEvent) -> None:
        self.check_choices()  # greyed from the start, not only once the list opens
        super().showEvent(event)

    def check_choices(self) -> None:
        model = self.model()
        if not isinstance(model, QStandardItemModel):
            return
        for row in range(self.count()):
            value = str(self.itemData(row))
            item = model.item(row)
            if item is None or value not in NOT_POSSIBLE_TIPS:
                continue
            can = self.possible(value)
            item.setEnabled(can)
            item.setToolTip(CHOICE_TIPS[value] if can else NOT_POSSIBLE_TIPS[value])
            if not can and self.currentIndex() == row:
                self.setCurrentIndex(0)
                if not self._correcting:
                    self.dropped(value)  # it was chosen before: the person is told it no longer holds

    @override
    def showPopup(self) -> None:
        self.check_choices()
        super().showPopup()

    def choice(self) -> str:
        return str(self.currentData())

    def back_to_stay(self) -> None:
        self.setCurrentIndex(self.findData(STAY))


class LimitDialog(QDialog):
    """The speed limit of the chosen items: their own, or the one of the status bar."""

    def __init__(self, limit: int | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(LIMIT_TITLE)
        self.follow_queue = QCheckBox(QUEUE_LIMIT_TEXT)
        self.follow_queue.setChecked(limit is None)
        self.speed = QSpinBox()
        self.speed.setRange(0, HIGHEST_LIMIT)
        self.speed.setSpecialValueText(NO_LIMIT_TEXT)
        self.speed.setSuffix(" KB/s")
        self.speed.setValue(limit or 0)
        self.speed.setEnabled(limit is not None)
        self.follow_queue.toggled.connect(self._follow_toggled)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form = QFormLayout(self)
        form.addRow("", self.follow_queue)
        form.addRow("Speed limit", self.speed)
        form.addRow(buttons)

    def _follow_toggled(self, on: bool) -> None:
        self.speed.setEnabled(not on)

    def chosen(self) -> int | None:
        return None if self.follow_queue.isChecked() else self.speed.value()


class QueuePanel(QWidget):
    """The queue with its title bar: the buttons of the queue (also in its right-click menu, the Delete key on the list), the switch to
    start items by themselves, how many transfers run at once, what happens when it is done, and the pin that keeps it on the screen when
    no transfer runs."""

    def __init__(self, view: QueueView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view: QueueView = view
        self.pin = QToolButton()
        self.pin.setIcon(icons.icon("window-pin"))
        self.pin.setCheckable(True)
        self.pin.setAutoRaise(True)
        self.pin.setToolTip(PIN_TEXT)
        self.bar = QToolBar()
        self.bar.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.actions_by_name: dict[str, QAction] = {}
        for name, text, icon_name, tip, with_text in QUEUE_ACTIONS:
            action = QAction(icons.icon(icon_name), text, self)
            action.setToolTip(tip)
            self.bar.addAction(action)
            button = self.bar.widgetForAction(action)
            if with_text and isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            if name == "delete":
                action.setShortcut(QKeySequence("Del"))
                action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)  # only while the queue list has the focus (OR-21)
                view.addAction(action)
            if name == "limit":
                self.bar.addSeparator()
            self.actions_by_name[name] = action
        self.start_by_themselves = QCheckBox(START_TEXT)
        self.start_by_themselves.setToolTip(START_TIP)
        self.at_once = QSpinBox()
        self.at_once.setRange(FEWEST_AT_ONCE, MOST_AT_ONCE)
        self.at_once.setToolTip(AT_ONCE_TIP)
        at_once_label = QLabel(AT_ONCE_TEXT)
        at_once_label.setToolTip(AT_ONCE_TIP)
        at_once_label.setBuddy(self.at_once)
        self.when_done = WhenDoneBox()
        when_label = QLabel(WHEN_DONE_TEXT)
        when_label.setToolTip(WHEN_DONE_TIP)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(TITLE))
        row.addWidget(self.bar, 1)
        row.addWidget(self.start_by_themselves)
        row.addWidget(at_once_label)
        row.addWidget(self.at_once)
        row.addWidget(when_label)
        row.addWidget(self.when_done)
        row.addWidget(self.pin, 0, Qt.AlignmentFlag.AlignRight)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(row)
        layout.addWidget(view, 1)
        self.setMinimumHeight(LOWEST_HEIGHT)
