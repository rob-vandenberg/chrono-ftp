"""The About window: the program's name, version and license (D-059), the full licence text (FP-05), and the update check (FP-06)."""

import html
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QFontDatabase
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.core.appinfo import licence_text, program_version, repository_url
from chrono_ftp.engine import updatecheck
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.ui.bridge import Bridge
from chrono_ftp.ui.icons import application_icon

# --- Version ------------------------------------------------------------
__version__ = "aboutdialog.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Licence shows the full AGPL-3.0 text shipped with the program (FP-05); Check for Updates asks GitHub, only when pressed and
#          on a thread of its own, and says calmly whether a newer version is there, with a link to its page (FP-06)
# v0.0.35: Initial: the About window, opened with F1 (D-059)

TITLE: str = "About Chrono FTP"
DESCRIPTION: str = "An open-source file transfer program for Linux."
LICENSE: str = "AGPL-3.0"
ICON_SIZE: int = 48
LICENCE_TITLE: str = "Licence of Chrono FTP"
NO_LICENCE_TEXT: str = "The licence text is not with this copy of Chrono FTP. Chrono FTP is licensed under the GNU Affero General Public License, version 3 only; its text is at https://www.gnu.org/licenses/agpl-3.0.html"
CHECKING_TEXT: str = "Asking GitHub…"
NEWER_TEXT: str = "Version {newest} is available; you have {running}. <a href=\"{page}\">See what is new</a>"
CURRENT_TEXT: str = "You have the newest version, {running}."
LICENCE_WIDTH: int = 640
LICENCE_HEIGHT: int = 600


def about_text() -> str:
    return f"<b>Chrono FTP</b> version {html.escape(program_version())}<br>{DESCRIPTION}<br><br>License: {LICENSE}"


class LicenceWindow(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(LICENCE_TITLE)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setPlainText(licence_text() or NO_LICENCE_TEXT)
        self.close_button = QPushButton("Close")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text, 1)
        layout.addLayout(bottom)
        self.resize(LICENCE_WIDTH, LICENCE_HEIGHT)


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
        self.update_state = QLabel()
        self.update_state.setTextFormat(Qt.TextFormat.RichText)
        self.update_state.setOpenExternalLinks(True)
        self.update_state.setWordWrap(True)
        self.update_state.hide()
        self.licence_button = QPushButton("Licence")
        self.licence_button.setToolTip("The full text of the licence, GNU AGPL version 3")
        self.licence_button.clicked.connect(self.show_licence)
        self.update_button = QPushButton("Check for Updates")
        self.update_button.setToolTip("Asks GitHub whether a newer version of Chrono FTP is there. Nothing is downloaded or installed.")
        self.update_button.clicked.connect(self.check_for_updates)
        self.close_button = QPushButton("Close")
        self.close_button.setDefault(True)  # Enter closes; Escape closes as in every window
        self.close_button.clicked.connect(self.accept)
        self.bridge: Bridge = Bridge(self)
        self.worker: Worker | None = None  # made at the first check: the window never waits for the network
        self.licence: LicenceWindow | None = None
        top = QHBoxLayout()
        top.addWidget(icon)
        top.addWidget(self.text, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.licence_button)
        bottom.addWidget(self.update_button)
        bottom.addStretch(1)
        bottom.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.update_state)
        layout.addLayout(bottom)

    def show_licence(self) -> None:
        if self.licence is None:
            self.licence = LicenceWindow(self)
        self.licence.show()
        self.licence.raise_()

    def check_for_updates(self) -> None:
        """Only on request (FP-06), on a thread of its own; a second press waits for the first answer."""
        running = program_version()
        address = repository_url()
        self.update_button.setEnabled(False)
        self.update_state.setText(CHECKING_TEXT)
        self.update_state.show()
        if self.worker is None:
            self.worker = Worker(self.bridge.deliver)
        self.worker.submit(lambda: updatecheck.check(address, running, updatecheck.fetch), self._answered, self._failed)

    def _answered(self, answer: updatecheck.UpdateAnswer) -> None:
        running = program_version()
        if answer.newer:
            self.update_state.setText(NEWER_TEXT.format(newest=html.escape(answer.newest), running=html.escape(running), page=html.escape(answer.page, quote=True)))
        else:
            self.update_state.setText(html.escape(CURRENT_TEXT.format(running=running)))
        self.update_button.setEnabled(True)

    def _failed(self, error: Exception) -> None:
        self.update_state.setText(html.escape(str(error) if isinstance(error, updatecheck.UpdateCheckFailed) else updatecheck.NOT_REACHED_TEXT))
        self.update_button.setEnabled(True)

    @override
    def done(self, result: int) -> None:
        self._finish()
        super().done(result)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self._finish()
        super().closeEvent(event)

    def _finish(self) -> None:
        self.bridge.close()  # an answer that comes later goes nowhere
        if self.worker is not None:
            self.worker.stop()
