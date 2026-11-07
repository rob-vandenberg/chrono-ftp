from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

# --- Version ------------------------------------------------------------
__version__ = "logindialog.py 0.0.8"

# --- Version history ----------------------------------------------------
# v0.0.8: Initial: user and password in one window; the cursor stands where the person has to type (D-040)

WINDOW_TITLE: str = "Log in"
WIDTH: int = 440
MARGIN: int = 20
ROW_GAP: int = 12


class LoginDialog(QDialog):
    """password_needed: the server refused the login. Without it the password may stay empty: a key or an open server needs none."""

    def __init__(self, connection_name: str, user: str, password_shown: bool, password_needed: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.password_needed: bool = password_needed
        self.user = QLineEdit(user)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.setVerticalSpacing(ROW_GAP)
        form.setHorizontalSpacing(ROW_GAP)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow("User", self.user)
        if password_shown:
            form.addRow("Password", self.password)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        heading = QLabel(connection_name)
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(MARGIN)
        layout.addWidget(heading)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self.setMinimumWidth(WIDTH)
        self.user.textChanged.connect(self._show_state)
        self.password.textChanged.connect(self._show_state)
        self._show_state()
        (self.password if user and password_shown else self.user).setFocus()

    def _show_state(self) -> None:
        ready = bool(self.user.text().strip()) and (bool(self.password.text()) or not self.password_needed)
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            button.setEnabled(ready)
