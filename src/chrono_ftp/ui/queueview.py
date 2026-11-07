from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QProgressBar, QTableWidget, QTableWidgetItem, QWidget

from chrono_ftp.transfers.queue import Job

# --- Version ------------------------------------------------------------
__version__ = "queueview.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: the transfers with their progress (D-014)

COLUMNS: tuple[str, ...] = ("Name", "Direction", "Way", "State", "Progress")
HEIGHT: int = 130


class QueueView(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, len(COLUMNS), parent)
        self.setHorizontalHeaderLabels(list(COLUMNS))
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.setFixedHeight(HEIGHT)
        self.jobs: list[Job] = []

    def show_job(self, job: Job) -> None:
        if job not in self.jobs:
            self.jobs.append(job)
            row = self.rowCount()
            self.insertRow(row)
            self.setCellWidget(row, 4, QProgressBar())
        row = self.jobs.index(job)
        texts = (job.name, "Upload" if job.upload else "Download", job.way, job.message or job.state.value)
        for column, text in enumerate(texts):
            self.setItem(row, column, QTableWidgetItem(text))
        bar = self.cellWidget(row, 4)
        if isinstance(bar, QProgressBar):
            bar.setValue(job.percent)

    def selected_jobs(self) -> list[Job]:
        return [self.jobs[index.row()] for index in self.selectionModel().selectedRows()]
