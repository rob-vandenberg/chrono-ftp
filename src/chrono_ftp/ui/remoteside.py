from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette, QStandardItemModel
from PySide6.QtWidgets import QLabel, QSplitter, QTreeView, QVBoxLayout, QWidget

from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "remoteside.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial: the right side, always a server: plain grey while there is no connection (OR-1, OR-3)

NOT_CONNECTED_TEXT: str = "Not connected"
COLUMNS: tuple[str, ...] = ("Name", "Size", "Type", "Date Modified")
NAME_COLUMN_WIDTH: int = 260


class RemoteSide(QWidget):
    """The right side. Without a connection its folder tree and file list are empty and plain grey."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path_label = QLabel(NOT_CONNECTED_TEXT)
        self.tree = QTreeView()
        self.tree.setModel(QStandardItemModel(0, 1, self))
        self.tree.setHeaderHidden(True)
        files = QStandardItemModel(0, len(COLUMNS), self)
        files.setHorizontalHeaderLabels(list(COLUMNS))
        self.file_list = QTreeView()
        self.file_list.setModel(files)
        self.file_list.setRootIsDecorated(False)
        self.file_list.setColumnWidth(0, NAME_COLUMN_WIDTH)
        for view in (self.tree, self.file_list):
            palette = QPalette(view.palette())
            palette.setColor(QPalette.ColorRole.Base, QColor(colours.NOT_CONNECTED))
            palette.setColor(QPalette.ColorRole.Text, QColor(colours.ON_NOT_CONNECTED))
            view.setPalette(palette)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.file_list)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.path_label)
        layout.addWidget(splitter, 1)  # all the height goes to the lists
