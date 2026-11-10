from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "searchdialog.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: The folder in the status line is shown as written, never read as HTML
# v0.0.6: A result keeps its real path, whatever the screen can show of it
# v0.0.5: Initial: search for files on the server; the search itself runs there (D-015)

WINDOW_TITLE: str = "Find files"
WIDTH: int = 640
HEIGHT: int = 420


class SearchDialog(QDialog):
    search_requested = Signal(str)
    result_opened = Signal(object)

    def __init__(self, folder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WIDTH, HEIGHT)
        self.pattern = QLineEdit("*")
        self.find_button = QPushButton("Find")
        self.status = plain_label(f"Searching below {shown_name(folder)}")
        self.paths: list[str] = []
        self.results = QListWidget()
        row = QHBoxLayout()
        row.addWidget(self.pattern)
        row.addWidget(self.find_button)
        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.status)
        layout.addWidget(self.results, 1)
        self.find_button.clicked.connect(self._find)
        self.pattern.returnPressed.connect(self._find)
        self.results.itemActivated.connect(self._opened)

    def _find(self) -> None:
        if self.pattern.text().strip() and self.find_button.isEnabled():
            self.find_button.setEnabled(False)
            self.status.setText("Searching on the server")
            self.results.clear()
            self.search_requested.emit(self.pattern.text().strip())

    def show_results(self, paths: list[str]) -> None:
        self.find_button.setEnabled(True)
        self.status.setText(f"{len(paths)} found")
        self.paths = paths
        self.results.clear()
        self.results.addItems([shown_name(path) for path in paths])

    def show_failure(self, text: str) -> None:
        self.find_button.setEnabled(True)
        self.status.setText(text)

    def _opened(self, item: QListWidgetItem) -> None:
        self.result_opened.emit(self.paths[self.results.row(item)])
