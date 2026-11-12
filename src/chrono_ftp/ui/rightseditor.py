"""The rights editor (FM-02): read, write and run for the owner, the group and others, with set user, set group and sticky, kept in step
with an octal field. A box where several files differ starts as "leave as it is", so only the boxes the person touches change."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QRegularExpressionValidator
from PySide6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.engine import rights
from chrono_ftp.engine.rights import Rights

# --- Version ------------------------------------------------------------
__version__ = "rightseditor.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Initial: the grid of boxes with "leave as it is", the octal field and its copy button (D-060); while the field holds no
#          complete number it says so and OK waits

ROWS: tuple[str, ...] = ("Owner", "Group", "Others")
COLUMNS: tuple[str, ...] = ("Read", "Write", "Run")
SPECIAL_TEXTS: tuple[tuple[str, str], ...] = (
    ("Set user", "A program runs as the owner of the file (setuid); a folder ignores it."),
    ("Set group", "A program runs as the group of the file; new files in a folder take the folder's group (setgid)."),
    ("Sticky", "In a folder, only the owner of a file may delete or rename it (sticky)."),
)
LEAVE_TIP: str = "The selected files differ here. As it is shown now, each keeps its own; a click sets it for all."
OCTAL_TIP: str = "The rights as an octal number, such as 755 or 0644. Empty while a box says leave as it is."
NOT_OCTAL_TIP: str = "This is not a complete octal number yet: three or four digits from 0 to 7, such as 755. OK waits for it."
COPY_TEXT: str = "Copy"
COPY_TIP: str = "Copy the octal number"
RUN_TIP: str = "Run a file as a program; for a folder, open it"


class RightsEditor(QWidget):
    changed = Signal()

    def __init__(self, start: Rights, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.start: Rights = start
        self.boxes: list[QCheckBox] = []
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        for column, text in enumerate(COLUMNS):
            header = QLabel(text)
            if text == "Run":
                header.setToolTip(RUN_TIP)
            grid.addWidget(header, 0, column + 1, Qt.AlignmentFlag.AlignHCenter)
        for row, text in enumerate(ROWS):
            grid.addWidget(QLabel(text), row + 1, 0)
            for column in range(3):
                box = QCheckBox()
                box.setAccessibleName(f"{text} {COLUMNS[column]}")
                self.boxes.append(box)
                grid.addWidget(box, row + 1, column + 1, Qt.AlignmentFlag.AlignHCenter)
        specials = QHBoxLayout()
        for text, tip in SPECIAL_TEXTS:
            box = QCheckBox(text)
            box.setToolTip(tip)
            self.boxes.append(box)
            specials.addWidget(box)
        specials.addStretch(1)
        self.octal = QLineEdit()
        self.octal.setValidator(QRegularExpressionValidator(r"[0-7]{0,4}", self.octal))
        self.octal.setToolTip(OCTAL_TIP)
        self.octal.setMaximumWidth(self.octal.fontMetrics().averageCharWidth() * 8)
        self.copy_button = QPushButton(COPY_TEXT)
        self.copy_button.setToolTip(COPY_TIP)
        number = QHBoxLayout()
        number.addWidget(QLabel("Octal"))
        number.addWidget(self.octal)
        number.addWidget(self.copy_button)
        number.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(grid)
        layout.addLayout(specials)
        layout.addLayout(number)
        self._quiet: bool = False
        self.set_rights(start)
        for box in self.boxes:
            box.stateChanged.connect(self._box_changed)
        self.octal.textEdited.connect(self._octal_typed)
        self.copy_button.clicked.connect(self.copy_octal)

    def set_rights(self, value: Rights) -> None:
        """Every box as given; a box that says leave can take that state again only where it started so."""
        self._quiet = True
        for box, state, started in zip(self.boxes, value, self.start, strict=True):
            box.setTristate(started is None)
            box.setCheckState(Qt.CheckState.PartiallyChecked if state is None else Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
            box.setToolTip(LEAVE_TIP if started is None else box.toolTip() if box.text() else "")
        self._quiet = False
        self._show_octal()

    def value(self) -> Rights:
        return tuple(None if box.checkState() == Qt.CheckState.PartiallyChecked else box.checkState() == Qt.CheckState.Checked for box in self.boxes)

    def is_changed(self) -> bool:
        return self.value() != self.start

    def _box_changed(self) -> None:
        if self._quiet:
            return
        self._show_octal()
        self.changed.emit()

    def is_valid(self) -> bool:
        """The octal field holds what the boxes show, or a value they can take: while it holds a part of a number, nothing is set."""
        text = self.octal.text()
        return text == rights.octal_text(self.value()) or rights.from_octal(text) is not None

    def _show_octal(self) -> None:
        text = rights.octal_text(self.value())
        if self.octal.text() != text:
            self.octal.setText(text)
        self.copy_button.setEnabled(bool(text))
        self.octal.setToolTip(OCTAL_TIP)

    def _octal_typed(self, text: str) -> None:
        typed = rights.from_octal(text)
        if typed is None:
            self.copy_button.setEnabled(False)
            self.octal.setToolTip(OCTAL_TIP if self.is_valid() else NOT_OCTAL_TIP)
            self.changed.emit()  # OK is greyed while the field holds no rights
            return
        self.octal.setToolTip(OCTAL_TIP)
        self._quiet = True
        for box, state in zip(self.boxes, typed, strict=True):
            box.setCheckState(Qt.CheckState.Checked if state else Qt.CheckState.Unchecked)
        self._quiet = False
        self.copy_button.setEnabled(True)
        self.changed.emit()

    def copy_octal(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if self.octal.text():
            clipboard.setText(self.octal.text())
