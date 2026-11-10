from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPaintEvent
from PySide6.QtWidgets import QStyleOptionTab, QStylePainter, QTabBar, QWidget

from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "tabbar.py 0.0.22"

# --- Version history ----------------------------------------------------
# v0.0.22: Hidden while it holds no tab: an empty bar kept room for its scroll arrows in front of New Tab
# v0.0.9: A live connection without a colour has a neutral tab
# v0.0.5: Initial: each tab in its connection's colour, inactive tabs dimmed, a dropped connection grey (D-007, D-008)

TEXT_MARGIN: int = 8


class ConnectionTabBar(QTabBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDrawBase(False)
        self.setTabsClosable(True)
        self.setMovable(False)
        self.setExpanding(False)
        self.setVisible(False)  # no tab yet

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
        base = (colour or colours.NO_COLOUR_TAB) if isinstance(colour, str) else colours.NOT_CONNECTED
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
