from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui import icons
from chrono_ftp.ui.filelist import size_text
from chrono_ftp.ui.plaintext import box_text

# --- Version ------------------------------------------------------------
__version__ = "queueview.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: The time left, empty while the job waits for an answer; a job that waits says so; without a counted total the amount done;
#          a move says it deletes
#          the Direction and State columns fit their texts, and every cell has its whole text as hover text
# v0.0.7: QueuePanel: a title bar with a pin; the height is free
# v0.0.6: Names that are not valid UTF-8 are shown readable
# v0.0.5: Initial: the transfers with their progress (D-014)

COLUMNS: tuple[str, ...] = ("Name", "Direction", "Way", "State", "Progress", "Time left")
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
    return shown_name(job.message) or job.state.value


class QueueView(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(list(COLUMNS))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        metrics = self.fontMetrics()
        for column, longest in ((DIRECTION_COLUMN, LONGEST_DIRECTION), (STATE_COLUMN, WAITING_TEXT)):
            self.setColumnWidth(column, metrics.horizontalAdvance(longest) + CELL_PADDING)  # the texts it shows fit; longer ones are in the hover text
        self.jobs: list[Job] = []

    def show_job(self, job: Job) -> None:
        if job not in self.jobs:
            self.jobs.append(job)
            row = self.rowCount()
            self.insertRow(row)
            self.setCellWidget(row, PROGRESS_COLUMN, QProgressBar())
        row = self.jobs.index(job)
        running = job.state == State.RUNNING
        texts = (shown_name(job.name), direction_text(job), job.way, state_text(job))
        waiting = job.waiting_for_answer  # no time passes for the transfer while it waits for the person
        for column, text in enumerate((*texts, "", time_text(job.seconds_left) if running and not waiting else "")):
            if column == PROGRESS_COLUMN:
                continue
            item = QTableWidgetItem(text)
            item.setToolTip(box_text(text) if text else "")  # the whole text, also where the column is narrower
            self.setItem(row, column, item)
        bar = self.cellWidget(row, PROGRESS_COLUMN)
        if isinstance(bar, QProgressBar):
            bar.setValue(job.percent)
            uncounted = running and not job.total and not job.percent  # counting first is off: what is done so far is all there is to show
            bar.setFormat(size_text(job.done) if uncounted else "%p%")

    def selected_jobs(self) -> list[Job]:
        return [self.jobs[index.row()] for index in self.selectionModel().selectedRows()]


class QueuePanel(QWidget):
    """The queue with its title bar. The pin keeps it on the screen when no transfer runs."""

    def __init__(self, view: QueueView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view: QueueView = view
        self.pin = QToolButton()
        self.pin.setIcon(icons.icon("window-pin"))
        self.pin.setCheckable(True)
        self.pin.setAutoRaise(True)
        self.pin.setToolTip(PIN_TEXT)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(TITLE))
        row.addStretch(1)
        row.addWidget(self.pin, 0, Qt.AlignmentFlag.AlignRight)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(row)
        layout.addWidget(view, 1)
        self.setMinimumHeight(LOWEST_HEIGHT)
