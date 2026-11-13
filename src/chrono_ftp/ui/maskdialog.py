"""The small windows of a mask on one side: Select by Mask (Num+), Unselect by Mask (Num−) and the Filter (block FD, FD-05, FD-06)."""

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.ui.maskfield import MaskField
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "maskdialog.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: select and unselect by mask, with folders too where ticked; the filter, with Clear; each with the masks typed
#          before (D-085, D-086)

SELECT: str = "select"
UNSELECT: str = "unselect"
FILTER: str = "filter"
TITLES: dict[str, str] = {SELECT: "Select by Mask", UNSELECT: "Unselect by Mask", FILTER: "Filter"}
LABELS: dict[str, str] = {
    SELECT: "Select the files that fit this mask, such as *.txt or *.jpg; *.png:",
    UNSELECT: "Unselect the files that fit this mask, such as *.txt or *.jpg; *.png:",
    FILTER: "Show only the files that fit this mask, such as *.txt or | *.bak; folders always stay in view:",
}
BUTTONS: dict[str, str] = {SELECT: "Select", UNSELECT: "Unselect", FILTER: "Filter"}
FOLDERS_TEXT: str = "Folders too"
FOLDERS_TIP: str = "Folders whose names fit the mask count as well. A pattern that ends in /, such as build/, is for folders always."
CLEAR_TEXT: str = "Clear"
CLEAR_TIP: str = "Show every file again"
EMPTY_TIP: str = "Please type a mask first."


class MaskDialog(QDialog):
    """mode: SELECT, UNSELECT or FILTER. For the filter an empty mask shows every file, and Clear does that at once."""

    def __init__(self, mode: str, mask: str, history: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode: str = mode
        self.cleared: bool = False
        self.setWindowTitle(TITLES[mode])
        self.label: QLabel = plain_label(LABELS[mode])
        self.label.setWordWrap(True)
        self.field = MaskField(history, "*.txt")
        self.field.set_text(mask)
        self.folders = QCheckBox(FOLDERS_TEXT)
        self.folders.setToolTip(FOLDERS_TIP)
        self.folders.setVisible(mode != FILTER)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(BUTTONS[mode])  # the button names the one action it does (OR-20)
        self.clear_button: QPushButton | None = None
        if mode == FILTER:
            self.clear_button = self.buttons.addButton(CLEAR_TEXT, QDialogButtonBox.ButtonRole.ResetRole)
            self.clear_button.setToolTip(CLEAR_TIP)
            self.clear_button.clicked.connect(self._clear)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.field)
        layout.addWidget(self.folders)
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        self.field.changed.connect(self._check)
        self.setMinimumWidth(460)
        self._check()
        self.field.combo.setFocus()

    def chosen_mask(self) -> str:
        return "" if self.cleared else self.field.text()

    def _check(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is None:
            return
        empty = not self.field.text() and self.mode != FILTER  # an empty filter shows every file
        ok.setEnabled(self.field.acceptable() and not empty)
        ok.setToolTip(self.field.fault() or (EMPTY_TIP if empty else ""))

    def _accept(self) -> None:
        if not self.field.acceptable():
            self.field.show_fault()
            return
        self.accept()

    def _clear(self) -> None:
        self.cleared = True
        self.accept()
