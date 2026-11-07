from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPaintEvent
from PySide6.QtWidgets import QStyleOptionTab, QStylePainter, QTabBar, QWidget

from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "tabbar.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: each tab in its connection's colour, inactive tabs dimmed, a dropped connection grey (D-007, D-008)

TEXT_MARGIN: int = 8


class ConnectionTabBar(QTabBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setTabsClosable(True)
        self.setMovable(False)
        self.setExpanding(False)

    def set_colour(self, index: int, colour: str | None) -> None:
        """colour None is a dropped connection: grey, because a colour always means a live connection (OR-3)."""
        self.setTabData(index, colour)
        self.update()

    def background_of(self, index: int) -> str:
        colour: object = self.tabData(index)
        base = colour if isinstance(colour, str) else colours.NOT_CONNECTED
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
