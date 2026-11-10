"""The PySide6 user interface. Pure window actions live here; server work does not."""

import sys

from PySide6.QtWidgets import QApplication

from chrono_ftp.ui import theme
from chrono_ftp.ui.icons import application_icon
from chrono_ftp.ui.mainwindow import WINDOW_TITLE, MainWindow
from chrono_ftp.ui.singlecopy import SingleCopy, socket_name, tell_running_copy

# --- Version ------------------------------------------------------------
__version__ = "__init__.py 0.0.25"

# --- Version history ----------------------------------------------------
# v0.0.25: One Chrono FTP at a time: a second start brings the running one to the front (D-051)
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
    if tell_running_copy(socket_name()):
        return 0  # the running Chrono FTP comes to the front instead
    theme.apply(application)
    window = MainWindow()
    single_copy = SingleCopy(socket_name(), window.come_forward, application)
    window.show()
    window.show_connection_list()
    result = application.exec()
    single_copy.close()
    return result
