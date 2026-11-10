"""The eye in a password field: shows or hides what the person typed; a stored password is never in the field, so never shown."""

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLineEdit

from chrono_ftp.ui.icons import icon

# --- Version ------------------------------------------------------------
__version__ = "passwordeye.py 0.0.21"

# --- Version history ----------------------------------------------------
# v0.0.21: Initial: the eye shows only while the field holds typed text; an emptied field hides the password again

SHOW_TEXT: str = "Show password"
HIDE_TEXT: str = "Hide password"


class PasswordEye(QObject):
    """Put on a password field: an eye at its right end while it holds typed text, which shows or hides that text."""

    def __init__(self, field: QLineEdit) -> None:
        super().__init__(field)
        self.field: QLineEdit = field
        self.action: QAction = QAction(self)
        field.addAction(self.action, QLineEdit.ActionPosition.TrailingPosition)
        self.action.triggered.connect(self.toggle)
        field.textChanged.connect(self._text_changed)
        self.hide_text()

    def shown(self) -> bool:
        return self.field.echoMode() == QLineEdit.EchoMode.Normal

    def toggle(self) -> None:
        if self.shown():
            self.hide_text()
        else:
            self.field.setEchoMode(QLineEdit.EchoMode.Normal)
            self.action.setIcon(icon("view-hidden"))
            self.action.setToolTip(HIDE_TEXT)

    def hide_text(self) -> None:
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.action.setIcon(icon("view-visible"))
        self.action.setToolTip(SHOW_TEXT)
        self.action.setVisible(bool(self.field.text()))

    def _text_changed(self, text: str) -> None:
        if text:
            self.action.setVisible(True)
        else:
            self.hide_text()  # emptied, also when another connection is chosen: hidden again, no eye
