"""The About window: the program's name, version and license (D-059)."""

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.core.appinfo import program_version
from chrono_ftp.ui.icons import application_icon

# --- Version ------------------------------------------------------------
__version__ = "aboutdialog.py 0.0.35"

# --- Version history ----------------------------------------------------
# v0.0.35: Initial: the About window, opened with F1 (D-059)

TITLE: str = "About Chrono FTP"
DESCRIPTION: str = "An open-source file transfer program for Linux."
LICENSE: str = "AGPL-3.0"
ICON_SIZE: int = 48


def about_text() -> str:
    return f"<b>Chrono FTP</b> version {html.escape(program_version())}<br>{DESCRIPTION}<br><br>License: {LICENSE}"


class AboutWindow(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        icon = QLabel()
        icon.setPixmap(application_icon().pixmap(ICON_SIZE, ICON_SIZE))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.text = QLabel()
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setText(about_text())
        self.text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.close_button = QPushButton("Close")
        self.close_button.setDefault(True)  # Enter closes; Escape closes as in every window
        self.close_button.clicked.connect(self.accept)
        top = QHBoxLayout()
        top.addWidget(icon)
        top.addWidget(self.text, 1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addLayout(bottom)
        self.setFixedSize(self.sizeHint())
