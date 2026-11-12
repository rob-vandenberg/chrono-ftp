"""The Server Information window of one tab (FP-03): what the connection tells about itself, what it can do, and the free space."""

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.engine.serverinfo import Report
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "serverinfodialog.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: the sections of the server information as a list, read on the tab's own thread, with Copy (FP-03)

TITLE: str = "Server Information: {name}"
READING_TEXT: str = "Asking the server…"
NOT_READ_TEXT: str = "The server could not be asked: {reason}"
WIDTH: int = 720
HEIGHT: int = 560


class ServerInfoWindow(QDialog):
    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE.format(name=shown_name(name)))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["", ""])
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.state = plain_label(READING_TEXT)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip("Copy everything shown here, as text")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy)
        self.close_button = QPushButton("Close")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self.state, 1)
        bottom.addWidget(self.copy_button)
        bottom.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tree, 1)
        layout.addLayout(bottom)
        self.resize(WIDTH, HEIGHT)
        self.lines: list[str] = []

    def show_reading(self) -> None:
        """A new request: nothing of an earlier answer stays on the screen as if it were current."""
        self.tree.clear()
        self.lines = []
        self.copy_button.setEnabled(False)
        self.state.setText(READING_TEXT)

    def show_report(self, report: Report) -> None:
        self.tree.clear()
        self.lines = []
        for title, lines in report.sections:
            section = QTreeWidgetItem([title, ""])
            font = section.font(0)
            font.setBold(True)
            section.setFont(0, font)
            self.tree.addTopLevelItem(section)
            self.lines.append(title)
            for label, value in lines:
                shown_label, shown_value = shown_name(label), shown_name(value)
                item = QTreeWidgetItem([shown_label, shown_value])
                item.setToolTip(1, shown_value)
                section.addChild(item)
                self.lines.append(f"  {shown_label}: {shown_value}" if shown_value else f"  {shown_label}")
            section.setExpanded(True)
        self.tree.resizeColumnToContents(0)
        self.state.setText("")
        self.copy_button.setEnabled(True)

    def show_problem(self, reason: str) -> None:
        self.state.setText(NOT_READ_TEXT.format(reason=shown_name(reason)))

    def copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n".join(self.lines))
