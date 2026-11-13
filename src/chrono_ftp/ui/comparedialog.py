from typing import override

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.connections.base import RemoteEntry, joined, shown_name
from chrono_ftp.engine.compare import LEFT_ALONE, Difference, Found
from chrono_ftp.ui.filelist import date_text, size_text
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "comparedialog.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Compare Contents: the files of the same size with other dates are compared by their checksums, greyed with its reason where
#          the connection computes none, and stoppable; rows of other content go to the side with the later date (FE-01); review
#          round 2: a stopped comparison says so
# v0.0.12: The folders in the status line are shown as written, never read as HTML
# v0.0.6: Initial: what differs between the two folders; the person ticks what is copied, nothing is ever deleted

WINDOW_TITLE: str = "Synchronize"
WIDTH: int = 900
HEIGHT: int = 520
COLUMNS: tuple[str, ...] = ("Name", "Difference", "On this computer", "On the server", "Action")
UPLOAD_TEXT: str = "Upload"
DOWNLOAD_TEXT: str = "Download"
TO_SERVER: frozenset[Difference] = frozenset({Difference.ONLY_LOCAL, Difference.NEWER_LOCAL, Difference.CONTENT_NEWER_LOCAL})
TO_COMPUTER: frozenset[Difference] = frozenset({Difference.ONLY_REMOTE, Difference.NEWER_REMOTE, Difference.CONTENT_NEWER_REMOTE})
CONTENTS_TEXT: str = "Compare Contents"
STOP_TEXT: str = "Stop"
CONTENTS_TIP: str = "Compares the files of the same size with other dates by their checksums, on both sides: those with the same content are left out."
NO_SAME_SIZE_TEXT: str = "No files of the same size with other dates to compare."
CONTENTS_RUNNING_TEXT: str = "Comparing the contents by {algorithm} checksums…"
CONTENTS_DONE_TEXT: str = "{left} with the same content were left out; {count} differences remain."
CONTENTS_UNKNOWN_TEXT: str = " {unknown} could not be compared and stay as they were."
CONTENTS_STOPPED_TEXT: str = "Comparing the contents was stopped; the rows stay as they were."


def _side_text(entry: RemoteEntry | None) -> str:
    if entry is None:
        return ""
    return "Folder" if entry.is_folder else f"{size_text(entry.size)}, {date_text(entry.modified)}"


def uploads(found: Found) -> bool:
    """Which way a row goes. A row with the same age on both sides goes to the server, the person can leave it unticked."""
    return found.difference not in TO_COMPUTER


class CompareDialog(QDialog):
    copy_requested = Signal(object)  # the rows ticked
    contents_requested = Signal()  # Compare Contents: the window runs the checksum step
    contents_stopped = Signal()

    def __init__(self, local_folder: str, remote_folder: str, parent: QWidget | None = None, contents_why: str = "") -> None:
        super().__init__(parent)
        self.contents_why: str = contents_why  # why the connection can't compare contents; empty when it can (OR-20)
        self.comparing: bool = False
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WIDTH, HEIGHT)
        self.found: list[Found] = []
        self.status = plain_label(f"Comparing {shown_name(local_folder)} with {shown_name(remote_folder)}")
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setEnabled(False)
        self.close_button = QPushButton("Close")
        self.contents_button = QPushButton(CONTENTS_TEXT)
        self.contents_button.setAutoDefault(False)
        self.contents_button.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.contents_button)
        row.addStretch(1)
        row.addWidget(self.copy_button)
        row.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.table, 1)
        layout.addLayout(row)
        self.copy_button.clicked.connect(self._copy)
        self.close_button.clicked.connect(self.close)
        self.contents_button.clicked.connect(self._contents_pressed)
        self._contents_state()

    def show_found(self, found: list[Found]) -> None:
        self.found = found
        self.table.setRowCount(len(found))
        self.status.setText(f"{len(found)} differences" if found else "These folders are the same.")
        for row, item in enumerate(found):
            name = QTableWidgetItem(shown_name(joined(item.folder, item.name) if item.folder else item.name))
            if item.difference not in LEFT_ALONE:
                name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                chosen = item.difference in TO_SERVER | TO_COMPUTER
                name.setCheckState(Qt.CheckState.Checked if chosen else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, name)
            action = "" if item.difference in LEFT_ALONE else UPLOAD_TEXT if uploads(item) else DOWNLOAD_TEXT
            texts = (item.difference.value, _side_text(item.local), _side_text(item.remote), action)
            for column, text in enumerate(texts, 1):
                self.table.setItem(row, column, QTableWidgetItem(text))
        self.copy_button.setEnabled(bool(found))
        self._contents_state()

    def _contents_state(self) -> None:
        if self.comparing:
            return
        same_size = any(row.difference == Difference.DATE for row in self.found)
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
        self.copy_button.setEnabled(False)
        self.status.setText(CONTENTS_RUNNING_TEXT.format(algorithm=algorithm))

    def contents_ended(self, found: list[Found] | None, same: int = 0, unknown: int = 0) -> None:
        """found None: stopped or failed, the rows stay as they were."""
        self.comparing = False
        self.contents_button.setText(CONTENTS_TEXT)
        if found is None:
            self.status.setText(CONTENTS_STOPPED_TEXT)  # a failure says its own reason after this
            self.copy_button.setEnabled(bool(self.found))
            self._contents_state()
            return
        self.show_found(found)
        text = CONTENTS_DONE_TEXT.format(left="1 file" if same == 1 else f"{same} files", count=len(found))
        self.status.setText(text + (CONTENTS_UNKNOWN_TEXT.format(unknown=unknown) if unknown else ""))

    def show_failure(self, text: str) -> None:
        self.status.setText(text)

    def ticked(self) -> list[Found]:
        rows = [row for row in range(self.table.rowCount()) if (item := self.table.item(row, 0)) is not None and item.checkState() == Qt.CheckState.Checked]
        return [self.found[row] for row in rows]

    def _copy(self) -> None:
        self.copy_requested.emit(self.ticked())
        self.close()

    @override
    def done(self, result: int) -> None:
        if self.comparing:
            self.contents_stopped.emit()  # a comparison that runs stops with the window
        super().done(result)
