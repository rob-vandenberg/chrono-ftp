from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QSplitter, QTabBar, QVBoxLayout, QWidget

from chrono_ftp.ui.localside import LocalSide
from chrono_ftp.ui.remoteside import RemoteSide

# --- Version ------------------------------------------------------------
__version__ = "mainwindow.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial: the tab bar above both sides, local on the left, remote on the right (OR-1, D-008)

WINDOW_TITLE: str = "Chrono FTP"
START_WIDTH: int = 1200
START_HEIGHT: int = 720
EMPTY_TAB_BAR_LINES: int = 2  # an empty tab bar has no height of its own: it keeps the height of a tab


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(START_WIDTH, START_HEIGHT)
        self.tab_bar = QTabBar()
        self.tab_bar.setDrawBase(False)
        self.tab_bar.setFixedHeight(self.fontMetrics().height() * EMPTY_TAB_BAR_LINES)
        self.local_side = LocalSide()
        self.remote_side = RemoteSide()
        self.sides = QSplitter(Qt.Orientation.Horizontal)
        self.sides.setChildrenCollapsible(False)
        self.sides.addWidget(self.local_side)  # always left, never swapped (OR-1)
        self.sides.addWidget(self.remote_side)
        self.sides.setStretchFactor(0, 1)
        self.sides.setStretchFactor(1, 1)
        centre = QWidget()
        layout = QVBoxLayout(centre)
        layout.addWidget(self.tab_bar)
        layout.addWidget(self.sides, 1)
        self.setCentralWidget(centre)
        self.statusBar()
