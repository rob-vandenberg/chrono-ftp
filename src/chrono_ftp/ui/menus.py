"""Every right-click menu opens here (D-052): it holds the mouse, so a click elsewhere closes it, but lets go of the keyboard, so the
desktop's own shortcuts, such as a screenshot, still work while it is open."""

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QMenu

from chrono_ftp.ui import theme

# --- Version ------------------------------------------------------------
__version__ = "menus.py 0.0.30"

# --- Version history ----------------------------------------------------
# v0.0.30: Every menu gets the menu style here, not from the whole program
# v0.0.29: Initial: a menu lets go of the keyboard once it is open (the trial on the tab menu, now for every menu)


def let_go_of_the_keyboard(menu: QMenu) -> None:
    """The open menu keeps the mouse; its keys still reach it through the focused window of the program."""
    window = menu.windowHandle()
    if window is not None and menu.isVisible():
        window.setKeyboardGrabEnabled(False)


def open_menu(menu: QMenu, place: QPoint) -> None:
    """Opens the menu at this place on the screen, in the menu style, and waits until it closes."""
    menu.setStyleSheet(theme.MENU_STYLE)
    menu.aboutToShow.connect(lambda: QTimer.singleShot(0, lambda: let_go_of_the_keyboard(menu)))  # once Qt has made the grab
    menu.exec(place)
