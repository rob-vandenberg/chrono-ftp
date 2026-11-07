from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

# --- Version ------------------------------------------------------------
__version__ = "searchdialog.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: search for files on the server; the search itself runs there (D-015)

WINDOW_TITLE: str = "Find files"
WIDTH: int = 640
HEIGHT: int = 420


class SearchDialog(QDialog):
    search_requested = Signal(str)
    result_opened = Signal(str)

    def __init__(self, folder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WIDTH, HEIGHT)
        self.pattern = QLineEdit("*")
        self.find_button = QPushButton("Find")
        self.status = QLabel(f"Searching below {folder}")
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
        if self.pattern.text().strip():
            self.find_button.setEnabled(False)
            self.status.setText("Searching on the server")
            self.results.clear()
            self.search_requested.emit(self.pattern.text().strip())

    def show_results(self, paths: list[str]) -> None:
        self.find_button.setEnabled(True)
        self.status.setText(f"{len(paths)} found")
        self.results.addItems(paths)

    def show_failure(self, text: str) -> None:
        self.find_button.setEnabled(True)
        self.status.setText(text)

    def _opened(self, item: QListWidgetItem) -> None:
        self.result_opened.emit(item.text())
