"""The small windows of block FM: a link (FM-05), Copy to (FM-06), a new folder with rights (FM-08) and a new password (FM-10)."""

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from chrono_ftp.connections.base import safe_name, shown_name
from chrono_ftp.engine.rights import NEW_FOLDER_RIGHTS, Rights, rights_of
from chrono_ftp.ui.passwordeye import PasswordEye
from chrono_ftp.ui.plaintext import plain_label
from chrono_ftp.ui.rightseditor import RightsEditor

# --- Version ------------------------------------------------------------
__version__ = "actiondialogs.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Initial: the link window, Copy to, the new folder with its optional rights, and the new password (D-060, D-061, D-062);
#          the new folder's OK waits while its octal field holds no complete number; as root the current password is not asked

LINK_TITLE: str = "Link"
LINK_HELP: str = "The link is a name that leads to another file or folder. A relative target is read from the folder of the link."
HARD_TEXT: str = "Hard link (another name for the same file, not for folders)"
COPY_TO_TITLE: str = "Copy to"
PATTERN_HELP: str = "* stands for each file's own name: *.bak gives report.txt.bak, old-* gives old-report.txt."
NEW_FOLDER_TITLE: str = "New folder"
SET_RIGHTS_TEXT: str = "Set rights"
PASSWORD_TITLE: str = "Change password"
PASSWORD_HELP: str = "The server's own passwd changes the password of {login}. Chrono FTP keeps the new one in your password store, where it kept the old one."
PASSWORDS_DIFFER_TEXT: str = "The new password and its repetition differ."
ROOT_TIP: str = "Not needed: as root, passwd asks for no current password."
SAME_PASSWORD_TEXT: str = "The new password is the same as the old one."


def _ok_cancel(dialog: QDialog) -> QDialogButtonBox:
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return buttons


def _set_ok(buttons: QDialogButtonBox, usable: bool) -> None:
    button = buttons.button(QDialogButtonBox.StandardButton.Ok)
    if button is not None:
        button.setEnabled(usable)


class LinkDialog(QDialog):
    """A new link, or (editing) another target for the link that is selected: its name then stays."""

    def __init__(self, name: str, target: str, editing: bool, hard_allowed: bool, hard_why: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(LINK_TITLE)
        self.editing: bool = editing
        self.name = QLineEdit(shown_name(name))
        self.name.setReadOnly(editing)
        self.target = QLineEdit(shown_name(target))
        self.hard = QCheckBox(HARD_TEXT)
        self.hard.setEnabled(hard_allowed and not editing)
        if not hard_allowed:
            self.hard.setToolTip(hard_why)
        form = QFormLayout()
        form.addRow("Link name", self.name)
        form.addRow("Leads to", self.target)
        form.addRow("", self.hard)
        self.buttons = _ok_cancel(self)
        help_text = QLabel(LINK_HELP)
        help_text.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(help_text)
        layout.addWidget(self.buttons)
        self._target_before: str = self.target.text()
        for field in (self.name, self.target):
            field.textChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        good = safe_name(self.name.text().strip()) and bool(self.target.text().strip()) and "\0" not in self.target.text()
        _set_ok(self.buttons, good and (not self.editing or self.target.text().strip() != self._target_before))

    def chosen(self) -> tuple[str, str, bool]:
        """Name, target and whether it is a hard link."""
        return self.name.text().strip(), self.target.text().strip(), self.hard.isChecked() and self.hard.isEnabled()


class CopyToDialog(QDialog):
    """A copy on the server: the folder it goes to, and a simple name pattern (the full mask engine comes with block FD)."""

    def __init__(self, count: int, folder: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(COPY_TO_TITLE)
        self.folder = QLineEdit(shown_name(folder))
        self.pattern = QLineEdit("*")
        form = QFormLayout()
        form.addRow("Copy", plain_label("1 item" if count == 1 else f"{count} items"))
        form.addRow("To the folder", self.folder)
        form.addRow("Name", self.pattern)
        help_text = QLabel(PATTERN_HELP)
        help_text.setWordWrap(True)
        self.buttons = _ok_cancel(self)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(help_text)
        layout.addWidget(self.buttons)
        for field in (self.folder, self.pattern):
            field.textChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        _set_ok(self.buttons, bool(self.folder.text().strip()) and bool(self.pattern.text().strip()) and "/" not in self.pattern.text())

    def chosen(self) -> tuple[str, str]:
        return self.folder.text().strip(), self.pattern.text().strip()


class NewFolderDialog(QDialog):
    """The name of the new folder, and, when chosen, its rights through the rights editor."""

    def __init__(self, rights_allowed: bool, rights_why: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(NEW_FOLDER_TITLE)
        self.name = QLineEdit()
        self.set_rights = QCheckBox(SET_RIGHTS_TEXT)
        self.set_rights.setEnabled(rights_allowed)
        if not rights_allowed:
            self.set_rights.setToolTip(rights_why)
        self.rights = RightsEditor(rights_of(NEW_FOLDER_RIGHTS))
        self.rights.setEnabled(False)
        form = QFormLayout()
        form.addRow("Name of the new folder", self.name)
        form.addRow("", self.set_rights)
        form.addRow("", self.rights)
        self.buttons = _ok_cancel(self)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self.name.textChanged.connect(self._update)
        self.set_rights.toggled.connect(self.rights.setEnabled)
        self.set_rights.toggled.connect(self._update)
        self.rights.changed.connect(self._update)
        self._update()

    def _update(self) -> None:
        _set_ok(self.buttons, bool(self.name.text().strip()) and (not self.set_rights.isChecked() or self.rights.is_valid()))

    def chosen(self) -> tuple[str, Rights | None]:
        return self.name.text().strip(), self.rights.value() if self.set_rights.isChecked() else None


class PasswordDialog(QDialog):
    """The old password, the new one and the new one again. Nothing typed here is ever kept, but the new one in the password store."""

    def __init__(self, login: str, as_root: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(PASSWORD_TITLE)
        self.as_root: bool = as_root
        self.old = QLineEdit()
        self.new = QLineEdit()
        self.again = QLineEdit()
        for field in (self.old, self.new, self.again):
            PasswordEye(field)
        self.old.setEnabled(not as_root)  # passwd asks root for no current password
        if as_root:
            self.old.setToolTip(ROOT_TIP)
        form = QFormLayout()
        form.addRow("Current password", self.old)
        form.addRow("New password", self.new)
        form.addRow("New password again", self.again)
        help_text = QLabel(PASSWORD_HELP.format(login=shown_name(login)))
        help_text.setWordWrap(True)
        self.problem = QLabel()
        self.buttons = _ok_cancel(self)
        layout = QVBoxLayout(self)
        layout.addWidget(help_text)
        layout.addLayout(form)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        for field in (self.old, self.new, self.again):
            field.textChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        new, again = self.new.text(), self.again.text()
        old = "" if self.as_root else self.old.text()
        problem = PASSWORDS_DIFFER_TEXT if again and new != again else SAME_PASSWORD_TEXT if new and new == old else ""
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))
        _set_ok(self.buttons, (self.as_root or bool(old)) and bool(new) and new == again and not problem)

    def chosen(self) -> tuple[str, str]:
        return "" if self.as_root else self.old.text(), self.new.text()
