"""Every right-click menu opens here (D-052): it holds the mouse, so a click elsewhere closes it, but lets go of the keyboard, so the
desktop's own shortcuts, such as a screenshot, still work while it is open."""

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QMenu

from chrono_ftp.ui import theme

# --- Version ------------------------------------------------------------
__version__ = "menus.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: The menu style comes from the look in use, each time a menu opens (FQ-04)
# v0.0.40: A drop-down menu of a button (Back and Forward, FG-10; the folders above a path, FG-16) gets the same style and lets go of the keyboard
# v0.0.30: Every menu gets the menu style here, not from the whole program
# v0.0.29: Initial: a menu lets go of the keyboard once it is open (the trial on the tab menu, now for every menu)


def let_go_of_the_keyboard(menu: QMenu) -> None:
    """The open menu keeps the mouse; its keys still reach it through the focused window of the program."""
    window = menu.windowHandle()
    if window is not None and menu.isVisible():
        window.setKeyboardGrabEnabled(False)


def as_drop_down(menu: QMenu) -> None:
    """A menu Qt opens itself, as the drop-down of a button: the menu style, and it lets go of the keyboard as every menu does (D-052).
    The style is set each time it opens, because such a menu is made once and the look can change while it lives (FQ-04)."""
    menu.aboutToShow.connect(lambda: menu.setStyleSheet(theme.menu_style()))
    menu.aboutToShow.connect(lambda: QTimer.singleShot(0, lambda: let_go_of_the_keyboard(menu)))


def open_menu(menu: QMenu, place: QPoint) -> None:
    """Opens the menu at this place on the screen, in the menu style, and waits until it closes."""
    menu.setStyleSheet(theme.menu_style())
    menu.aboutToShow.connect(lambda: QTimer.singleShot(0, lambda: let_go_of_the_keyboard(menu)))  # once Qt has made the grab
    menu.exec(place)
