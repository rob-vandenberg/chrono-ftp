from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.transfers.queue import Job
from chrono_ftp.ui import icons

# --- Version ------------------------------------------------------------
__version__ = "queueview.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: QueuePanel: a title bar with a pin; the height is free
# v0.0.6: Names that are not valid UTF-8 are shown readable
# v0.0.5: Initial: the transfers with their progress (D-014)

COLUMNS: tuple[str, ...] = ("Name", "Direction", "Way", "State", "Progress")
HEIGHT: int = 130  # until the person drags it
LOWEST_HEIGHT: int = 70
TITLE: str = "Transfers"
PIN_TEXT: str = "Keep this list on the screen when no transfer is running"


class QueueView(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(list(COLUMNS))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.jobs: list[Job] = []

    def show_job(self, job: Job) -> None:
        if job not in self.jobs:
            self.jobs.append(job)
            row = self.rowCount()
            self.insertRow(row)
            self.setCellWidget(row, 4, QProgressBar())
        row = self.jobs.index(job)
        texts = (shown_name(job.name), "Upload" if job.upload else "Download", job.way, shown_name(job.message) or job.state.value)
        for column, text in enumerate(texts):
            self.setItem(row, column, QTableWidgetItem(text))
        bar = self.cellWidget(row, 4)
        if isinstance(bar, QProgressBar):
            bar.setValue(job.percent)

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
