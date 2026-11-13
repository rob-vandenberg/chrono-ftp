from collections.abc import Callable
from typing import override

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QPaintEvent, QPen
from PySide6.QtWidgets import QStyleOptionTab, QStylePainter, QTabBar, QWidget

from chrono_ftp.core import colours
from chrono_ftp.ui.filelist import DRAG_MARK, FROM_LOCAL, FROM_OUTSIDE, LOCAL_FILES

# --- Version ------------------------------------------------------------
__version__ = "tabbar.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 2: the tab a drag is over is told even when it can't take the files, so the window says why (OR-7)
# v0.0.45: A tab can be dragged to another place in the row; files dragged from the left side or from a file manager can be dropped on
#          a tab, which is drawn with a clear border while it is the one that would take them, and a tab that can't take them is never
#          marked; the middle mouse button closes a tab, which Qt does for closable tabs and a test now holds to (FI-01)
# v0.0.33: A dropped connection's tab has its own grey, NOT_CONNECTED_TAB, unchanged
# v0.0.22: Hidden while it holds no tab: an empty bar kept room for its scroll arrows in front of New Tab
# v0.0.9: A live connection without a colour has a neutral tab
# v0.0.5: Initial: each tab in its connection's colour, inactive tabs dimmed, a dropped connection grey (D-007, D-008)

TEXT_MARGIN: int = 8
TAKES_FILES_WIDTH: int = 3  # the border round the tab that would take the files being dragged over it
NO_TAB: int = -1


class ConnectionTabBar(QTabBar):
    files_dropped = Signal(int, object)  # the tab the files fell on, and the paths of a drag from outside ([] for the left side's selection)
    hovered_with_files = Signal(int)  # the tab a drag of files is over now, whether it can take them or not; NO_TAB over none

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setTabsClosable(True)  # with this, the middle mouse button closes a tab too, through the same signal as its cross (FI-01)
        self.setMovable(True)  # dragged to another place in the row (FI-01)
        self.setExpanding(False)
        self.setVisible(False)  # no tab yet
        self.setAcceptDrops(True)
        self.takes_files: Callable[[int], str] = lambda _index: ""  # why this tab can't take dropped files; empty when it can
        self.taking: int = NO_TAB  # the tab that would take the files being dragged over the bar now
        self.hovered: int = NO_TAB  # the tab the drag is over, also one that can't take them: it says why (OR-7)

    @override
    def tabInserted(self, index: int) -> None:
        super().tabInserted(index)
        self.setVisible(True)

    @override
    def tabRemoved(self, index: int) -> None:
        super().tabRemoved(index)
        self.setVisible(self.count() > 0)  # an empty bar keeps room for its scroll arrows: a hole in front of New Tab

    def set_colour(self, index: int, colour: str | None) -> None:
        """colour None is a dropped connection: grey. An empty colour is a live connection without a colour of its own (D-007)."""
        self.setTabData(index, colour)
        self.update()

    def background_of(self, index: int) -> str:
        colour: object = self.tabData(index)
        base = (colour or colours.NO_COLOUR_TAB) if isinstance(colour, str) else colours.NOT_CONNECTED_TAB
        return base if index == self.currentIndex() else colours.dimmed(base)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QStylePainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            background = self.background_of(index)
            rectangle = self.tabRect(index)
            painter.fillRect(rectangle, QColor(background))
            painter.setPen(QColor(colours.readable_text(background)))
            painter.drawText(rectangle.adjusted(TEXT_MARGIN, 0, -TEXT_MARGIN, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.tabText(index))
            if index == self.taking:  # it is the tab that would take what is being dragged: a border nobody can miss (FI-01)
                painter.setPen(QPen(QColor(colours.TAB_TAKES_FILES), TAKES_FILES_WIDTH))
                painter.drawRect(rectangle.adjusted(TAKES_FILES_WIDTH // 2, TAKES_FILES_WIDTH // 2, -TAKES_FILES_WIDTH, -TAKES_FILES_WIDTH))

    # --- files dropped on a tab (FI-01) --------------------------------------------------

    def _files_in(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> str | None:
        """Where the files being dragged come from, or None when this bar can take nothing of it."""
        mime = event.mimeData()
        if mime is None:
            return None
        if mime.data(LOCAL_FILES).data() == DRAG_MARK:
            return FROM_LOCAL  # the selection of the left side of this Chrono FTP
        if mime.hasUrls() and all(url.isLocalFile() for url in mime.urls()):
            return FROM_OUTSIDE  # a file manager
        return None

    def _mark(self, over: int, taking: int) -> None:
        """over: the tab the drag is over; taking: the one that would really take the files. Both are told, so a tab that can't take
        them can say why (OR-7)."""
        if taking != self.taking:
            self.taking = taking
            self.update()
        if over != self.hovered:
            self.hovered = over
            self.hovered_with_files.emit(over)

    def _would_take(self, place: QPoint) -> int:
        """The tab at this place that can take the files, or NO_TAB: a tab that can't take them is never marked (G-2)."""
        index = self.tabAt(place)
        return index if 0 <= index < self.count() and not self.takes_files(index) else NO_TAB

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._files_in(event) is None:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        place = event.position().toPoint()
        self._mark(self.tabAt(place), self._would_take(place))

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if self._files_in(event) is None:
            event.ignore()
            return
        place = event.position().toPoint()
        index = self._would_take(place)
        self._mark(self.tabAt(place), index)
        if index == NO_TAB:
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    @override
    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._mark(NO_TAB, NO_TAB)
        super().dragLeaveEvent(event)

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        source = self._files_in(event)
        place = event.position().toPoint()
        index = self._would_take(place)
        if source is None or index == NO_TAB:
            self._mark(NO_TAB, NO_TAB)
            over = self.tabAt(place) if source is not None else NO_TAB
            if over != NO_TAB:
                self.hovered_with_files.emit(over)  # let go on a tab that can't take them: it says why, never silently (OR-7)
            event.ignore()
            return
        self._mark(NO_TAB, NO_TAB)
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        mime = event.mimeData()
        paths = [url.toLocalFile() for url in mime.urls()] if source == FROM_OUTSIDE and mime is not None else []
        self.files_dropped.emit(index, paths)
