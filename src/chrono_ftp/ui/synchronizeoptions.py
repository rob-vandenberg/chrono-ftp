"""The choices of the Synchronize window (FF-01, FF-02, FF-08, FF-09): the direction, the mode, what two files are compared by, the
scope of the scan and the masks. What this connection can't do is greyed with its reason, never hidden (OR-7, OR-20)."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QStandardItemModel
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QRadioButton, QVBoxLayout, QWidget

from chrono_ftp.engine.synchronize import Criterion, Direction, Mode, Options
from chrono_ftp.ui.maskfield import MaskField

# --- Version ------------------------------------------------------------
__version__ = "synchronizeoptions.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: Initial: the direction and the mode, the criterion with its case choice, the deletes and the scope, and the masks with
#          "Skip empty folders" (FF-01, FF-02, FF-08, FF-09, D-091, D-092)

DIRECTION_TITLE: str = "Direction"
MODE_TITLE: str = "What it does"
CRITERION_TITLE: str = "Compare"
SCOPE_TITLE: str = "What it looks at"
MASK_TITLE: str = "Files"
CASE_TEXT: str = "Upper and lower case are different names"
CASE_TIP: str = "Off: README on this computer and readme on the other side are the same file."
DELETE_TEXT: str = "Delete on the other side"
DELETE_TIP: str = "On: the rows that delete start ticked. They are always shown, and Apply asks before anything is deleted."
DELETE_BOTH_WHY: str = "Both ways has no other side to delete on: each side is a source."
DELETE_TIMESTAMPS_WHY: str = "Timestamps only sets dates; it never copies and never deletes."
EXISTING_TEXT: str = "Existing files only"
EXISTING_TIP: str = "Only names that are already on both sides; nothing new is copied."
NO_SUBFOLDERS_TEXT: str = "No subfolders"
SELECTED_TEXT: str = "The selected files only"
SELECTED_WHY: str = "Nothing is selected in the list of this computer."
SKIP_EMPTY_TEXT: str = "Skip empty folders"
MASK_PLACEHOLDER: str = "*.txt; docs/ | *.bak"
MODE_TEXTS: dict[Mode, str] = {
    Mode.SYNCHRONIZE: "Synchronize: copy what is new or newer",
    Mode.MIRROR: "Mirror: make the target an exact copy",
    Mode.TIMESTAMPS: "Timestamps only: give the same date, copy nothing",
}
CRITERION_TEXTS: dict[Criterion, str] = {
    Criterion.DATE: "By date (and size)",
    Criterion.SIZE: "By size",
    Criterion.CHECKSUM: "By checksum",
    Criterion.NAME: "By name only",
}


def _greyed(box: QComboBox, number: int, why: str) -> None:
    """One choice of a drop-down greyed, with its reason as hover text (OR-20); nothing is ever hidden."""
    model = box.model()
    item = model.item(number) if isinstance(model, QStandardItemModel) else None
    if item is not None:
        item.setEnabled(False)
        item.setToolTip(why)
    box.setItemData(number, why, Qt.ItemDataRole.ToolTipRole)


class SynchronizeOptions(QWidget):
    """Every choice, with the one the person made last to start from (FF-11)."""

    changed = Signal()

    def __init__(self, options: Options, why_no_checksum: str = "", why_no_timestamps: str = "", anything_selected: bool = False, history: tuple[str, ...] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.why_no_checksum: str = why_no_checksum
        self.why_no_timestamps: str = why_no_timestamps
        self.to_there = QRadioButton(Direction.TO_THERE.value)
        self.to_here = QRadioButton(Direction.TO_HERE.value)
        self.both = QRadioButton(Direction.BOTH.value)
        self.directions: QButtonGroup = QButtonGroup(self)
        for number, button in enumerate((self.to_there, self.to_here, self.both)):
            self.directions.addButton(button, number)
        self.mode = QComboBox()
        for mode in Mode:
            self.mode.addItem(MODE_TEXTS[mode], mode.name)
        if why_no_timestamps:
            _greyed(self.mode, list(Mode).index(Mode.TIMESTAMPS), why_no_timestamps)
        self.criterion = QComboBox()
        for criterion in Criterion:
            self.criterion.addItem(CRITERION_TEXTS[criterion], criterion.name)
        if why_no_checksum:
            _greyed(self.criterion, list(Criterion).index(Criterion.CHECKSUM), why_no_checksum)
        self.case = QCheckBox(CASE_TEXT)
        self.case.setToolTip(CASE_TIP)
        self.delete_other = QCheckBox(DELETE_TEXT)
        self.delete_other.setToolTip(DELETE_TIP)
        self.existing = QCheckBox(EXISTING_TEXT)
        self.existing.setToolTip(EXISTING_TIP)
        self.no_subfolders = QCheckBox(NO_SUBFOLDERS_TEXT)
        self.selected_only = QCheckBox(SELECTED_TEXT)
        self.selected_only.setEnabled(anything_selected)
        if not anything_selected:
            self.selected_only.setToolTip(SELECTED_WHY)  # greyed with its reason (OR-20)
        self.skip_empty = QCheckBox(SKIP_EMPTY_TEXT)
        self.mask_field: MaskField = MaskField(history, MASK_PLACEHOLDER)
        self._build()
        self.show_options(options)
        for button in (self.to_there, self.to_here, self.both):
            button.toggled.connect(self._touched)
        for box in (self.case, self.delete_other, self.existing, self.no_subfolders, self.selected_only, self.skip_empty):
            box.toggled.connect(self._touched)
        self.mode.currentIndexChanged.connect(self._touched)
        self.criterion.currentIndexChanged.connect(self._touched)
        self.mask_field.changed.connect(self._touched)

    def _build(self) -> None:
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        direction = QGroupBox(DIRECTION_TITLE)
        row = QVBoxLayout(direction)
        for button in (self.to_there, self.to_here, self.both):
            row.addWidget(button)
        what = QGroupBox(MODE_TITLE)
        column = QVBoxLayout(what)
        column.addWidget(self.mode)
        column.addWidget(self.delete_other)
        column.addStretch(1)
        compare = QGroupBox(CRITERION_TITLE)
        column = QVBoxLayout(compare)
        column.addWidget(self.criterion)
        column.addWidget(self.case)
        column.addStretch(1)
        scope = QGroupBox(SCOPE_TITLE)
        column = QVBoxLayout(scope)
        for box in (self.existing, self.no_subfolders, self.selected_only):
            column.addWidget(box)
        files = QGroupBox(MASK_TITLE)
        line = QHBoxLayout(files)
        line.addWidget(QLabel("Mask"))
        line.addWidget(self.mask_field, 1)
        line.addWidget(self.skip_empty)
        grid.addWidget(direction, 0, 0)
        grid.addWidget(what, 0, 1)
        grid.addWidget(compare, 0, 2)
        grid.addWidget(scope, 0, 3)
        grid.addWidget(files, 1, 0, 1, 4)

    def _touched(self) -> None:
        self._follow()
        self.changed.emit()

    def _follow(self) -> None:
        """"Delete on the other side" is greyed, with its reason, where it can have no meaning (FF-08)."""
        both = self.both.isChecked()
        timestamps = self.chosen_mode() == Mode.TIMESTAMPS
        self.delete_other.setEnabled(not both and not timestamps)
        self.delete_other.setToolTip(DELETE_BOTH_WHY if both else DELETE_TIMESTAMPS_WHY if timestamps else DELETE_TIP)

    def chosen_mode(self) -> Mode:
        name = self.mode.currentData()
        return Mode[name] if isinstance(name, str) and name in Mode.__members__ else Mode.SYNCHRONIZE

    def chosen_criterion(self) -> Criterion:
        name = self.criterion.currentData()
        return Criterion[name] if isinstance(name, str) and name in Criterion.__members__ else Criterion.DATE

    def chosen_direction(self) -> Direction:
        if self.to_here.isChecked():
            return Direction.TO_HERE
        return Direction.BOTH if self.both.isChecked() else Direction.TO_THERE

    def show_options(self, options: Options) -> None:
        """The window opens on the choices of last time (FF-11); one this connection can't do falls back to one it can (OR-15)."""
        {Direction.TO_THERE: self.to_there, Direction.TO_HERE: self.to_here, Direction.BOTH: self.both}[options.direction].setChecked(True)
        mode = Mode.SYNCHRONIZE if options.mode == Mode.TIMESTAMPS and self.why_no_timestamps else options.mode
        criterion = Criterion.DATE if options.criterion == Criterion.CHECKSUM and self.why_no_checksum else options.criterion
        self.mode.setCurrentIndex(list(Mode).index(mode))
        self.criterion.setCurrentIndex(list(Criterion).index(criterion))
        self.case.setChecked(options.case_matters)
        self.delete_other.setChecked(options.delete_other_side)
        self.existing.setChecked(options.existing_only)
        self.no_subfolders.setChecked(options.no_subfolders)
        self.selected_only.setChecked(options.selected_only and self.selected_only.isEnabled())
        self.skip_empty.setChecked(options.skip_empty_folders)
        self.mask_field.set_text(options.mask)
        self._follow()

    def chosen(self) -> Options:
        return Options(
            direction=self.chosen_direction(),
            mode=self.chosen_mode(),
            criterion=self.chosen_criterion(),
            case_matters=self.case.isChecked(),
            delete_other_side=self.delete_other.isChecked() and self.delete_other.isEnabled(),
            existing_only=self.existing.isChecked(),
            no_subfolders=self.no_subfolders.isChecked(),
            selected_only=self.selected_only.isChecked() and self.selected_only.isEnabled(),
            mask=self.mask_field.text() if self.mask_field.acceptable() else "",
            skip_empty_folders=self.skip_empty.isChecked(),
        )
