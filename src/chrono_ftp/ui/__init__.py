"""The PySide6 user interface. Pure window actions live here; server work does not."""

import sys

from PySide6.QtWidgets import QApplication

from chrono_ftp.ui import theme
from chrono_ftp.ui.icons import application_icon
from chrono_ftp.ui.mainwindow import WINDOW_TITLE, MainWindow

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.14"

# --- Version history ----------------------------------------------------
# v0.0.14: The program icon, and the windows belong to the start-menu entry chrono-ftp
# v0.0.5: The connection list opens at start, because there is no tab yet
# v0.0.4: The description follows D-022
# v0.0.3: The program starts here
# v0.0.1: Initial skeleton


DESKTOP_FILE_NAME: str = "chrono-ftp"  # the start-menu entry chrono-ftp.desktop; the taskbar puts the windows on its button


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName(WINDOW_TITLE)
    application.setDesktopFileName(DESKTOP_FILE_NAME)
    application.setWindowIcon(application_icon())
    theme.apply(application)
    window = MainWindow()
    window.show()
    window.show_connection_list()
    return application.exec()
