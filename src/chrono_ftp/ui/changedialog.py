from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QVBoxLayout, QWidget

from chrono_ftp.engine import commands

# --- Version ------------------------------------------------------------
__version__ = "changedialog.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: owner, group and permissions of what is selected, with or without what is inside (D-012)

WINDOW_TITLE: str = "Owner and permissions"


class ChangeDialog(QDialog):
    def __init__(self, can_change_owner: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.owner = QLineEdit()
        self.group = QLineEdit()
        self.mode = QLineEdit()
        self.mode.setPlaceholderText("755 or u+x")
        self.owner.setEnabled(can_change_owner)
        self.group.setEnabled(can_change_owner)
        self.recursive = QCheckBox("Also everything inside the selected folders")
        self.recursive.setChecked(True)
        form = QFormLayout()
        form.addRow("Owner", self.owner)
        form.addRow("Group", self.group)
        form.addRow("Permissions", self.mode)
        form.addRow("", self.recursive)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        for field in (self.owner, self.group, self.mode):
            field.textChanged.connect(self._update)
        self._update()

    def _update(self) -> None:
        owner, group, mode = self.owner.text().strip(), self.group.text().strip(), self.mode.text().strip()
        good = (not owner or commands.valid_name(owner)) and (not group or commands.valid_name(group)) and (not mode or commands.valid_mode(mode))
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            button.setEnabled(good and bool(owner or group or mode))

    def commands_for(self, paths: list[str]) -> list[str]:
        owner, group, mode = self.owner.text().strip(), self.group.text().strip(), self.mode.text().strip()
        recursive = self.recursive.isChecked()
        found: list[str] = []
        if owner or group:
            found.append(commands.owner_command(paths, owner, group, recursive))
        if mode:
            found.append(commands.mode_command(paths, mode, recursive))
        return found
