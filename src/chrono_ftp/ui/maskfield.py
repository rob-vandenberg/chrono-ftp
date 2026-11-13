"""The field every mask is typed in (block FD): its history in a drop-down, the syntax in its hover text, and a calm line that points at a
fault the moment one is typed (FD-01, OR-15, OR-23)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QCompleter, QLabel, QVBoxLayout, QWidget

from chrono_ftp.core.masks import HELP_TEXT, LONGEST_MASK, MaskError, parse
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "maskfield.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: a mask with its history, the syntax as its hover text, and the fault of a mask said and selected (D-081); typing is
#          never completed from the history


class MaskField(QWidget):
    """An editable drop-down with the masks typed before; below it, only while there is one, what is wrong with the mask typed."""

    changed = Signal()

    def __init__(self, history: tuple[str, ...] = (), placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.combo = QComboBox()
        self.combo.setEditable(True)
        self.combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)  # the history grows only with a mask that was used
        self.combo.setCompleter(QCompleter(list[str](), self.combo))  # completes nothing: never a mask the person did not type; the history is only in the drop-down
        self.combo.addItems(list(history))
        self.combo.setCurrentIndex(-1)
        self.combo.setToolTip(HELP_TEXT)
        line = self.combo.lineEdit()
        if line is not None:
            line.setMaxLength(LONGEST_MASK)
            line.setPlaceholderText(placeholder)
            line.setClearButtonEnabled(True)
        self.problem: QLabel = plain_label()
        self.problem.setWordWrap(True)
        self.problem.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.combo)
        layout.addWidget(self.problem)
        self.combo.editTextChanged.connect(self._typed)

    def text(self) -> str:
        return self.combo.currentText().strip()

    def set_text(self, text: str) -> None:
        self.combo.setEditText(text)
        self._typed()

    def acceptable(self) -> bool:
        return not self.fault()

    def fault(self) -> str:
        try:
            parse(self.text())
        except MaskError as error:
            return str(error)
        return ""

    def show_fault(self) -> None:
        """The fault selected in the field, so it can be typed over at once."""
        try:
            parse(self.text())
        except MaskError as error:
            line = self.combo.lineEdit()
            if line is not None:
                offset = len(self.combo.currentText()) - len(self.combo.currentText().lstrip())
                line.setSelection(offset + error.start, max(1, error.end - error.start))
            self.combo.setFocus()

    def _typed(self) -> None:
        fault = self.fault()
        self.problem.setText(fault)
        self.problem.setVisible(bool(fault))
        self.changed.emit()
