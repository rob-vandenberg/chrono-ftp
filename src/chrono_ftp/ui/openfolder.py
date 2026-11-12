"""The Open folder window (Ctrl+O, FH-02): a path to type, with the paths typed earlier, and the bookmarks of the side."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.config.bookmarks import Bookmark
from chrono_ftp.connections.base import shown_name

# --- Version ------------------------------------------------------------
__version__ = "openfolder.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial: a path with its history and the bookmarks of the side (FH-02)

LOCAL_TITLE: str = "Open a folder on this computer"
REMOTE_TITLE: str = "Open a folder on {name}"
PATH_LABEL: str = "Folder (type a path, or choose one typed before):"
MARKS_LABEL: str = "Bookmarks (a click takes its folder, a double click opens it):"
NO_MARKS_TEXT: str = "No bookmarks yet. Ctrl+B adds the folder shown."
OPEN_TEXT: str = "Open"
WIDTH_CHARACTERS: int = 60


class OpenFolderDialog(QDialog):
    def __init__(self, title: str, current: str, typed: list[str], marks: list[Bookmark], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(self.fontMetrics().averageCharWidth() * WIDTH_CHARACTERS)
        self.raw: dict[str, str] = {}  # what a field shows -> the path itself: a name that is not valid UTF-8 stays what it is
        self.path = QComboBox()
        self.path.setEditable(True)
        self.path.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for earlier in typed:
            self.raw[shown_name(earlier)] = earlier
            self.path.addItem(shown_name(earlier))
        self.raw[shown_name(current)] = current
        self.path.setEditText(shown_name(current))
        self.marks = QListWidget()
        for number, mark in enumerate(marks):
            item = QListWidgetItem(f"{mark.name}    {shown_name(mark.path)}")
            item.setData(Qt.ItemDataRole.UserRole, number)
            item.setToolTip(shown_name(mark.path))
            self.marks.addItem(item)
        self.mark_list: list[Bookmark] = marks
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        open_button = buttons.button(QDialogButtonBox.StandardButton.Open)
        if open_button is not None:
            open_button.setText(OPEN_TEXT)
            open_button.setDefault(True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(PATH_LABEL))
        layout.addWidget(self.path)
        layout.addWidget(QLabel(MARKS_LABEL if marks else NO_MARKS_TEXT))
        layout.addWidget(self.marks)
        self.marks.setVisible(bool(marks))
        layout.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.marks.itemClicked.connect(self._mark_taken)
        self.marks.itemDoubleClicked.connect(self._mark_opened)
        line = self.path.lineEdit()
        if line is not None:
            line.selectAll()
        self.path.setFocus()

    def _mark_taken(self, item: QListWidgetItem) -> None:
        number = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(number, int) and 0 <= number < len(self.mark_list):
            path = self.mark_list[number].path
            self.raw[shown_name(path)] = path
            self.path.setEditText(shown_name(path))

    def _mark_opened(self, item: QListWidgetItem) -> None:
        self._mark_taken(item)
        self.accept()

    def chosen(self) -> str:
        """The path the person chose; one taken from the history or a bookmark is that path itself."""
        text = self.path.currentText().strip()
        return self.raw.get(text, text)
