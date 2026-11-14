"""The Colours page of Settings (FQ-01): the rules of file colours, in the order they are tried, each a mask with a text colour and a
background the person picks. The list shows every rule in its own colours."""

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QCheckBox, QColorDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.core import colours
from chrono_ftp.core.filecolours import MOST_RULES, ColourRule
from chrono_ftp.core.masks import LONGEST_MASK

# --- Version ------------------------------------------------------------
__version__ = "colourrules.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: A rule with a background and no text is shown with the text the file list gives it, black or white by that background (FQ-04)
# v0.0.39: Initial: the list of rules with New, Delete, Move Up and Move Down, and the mask and colours of the selected rule (FQ-01, D-069)

HINT_TEXT: str = (
    "The first rule whose mask fits a name colours that file's row, on both sides. A mask such as *.log; *.tmp fits files; "
    "one that ends in / fits folders, such as build/. A selected row shows the selection over its colours."
)
MASK_TIP: str = "Names like these get the colours of this rule: for example *.log; *.tmp. End a part with / for folders."
NONE_SELECTED_TIP: str = "Choose a rule in the list, or make one with New"
FULL_TIP: str = f"There are {MOST_RULES} rules, the most there can be"
MASK_PLACEHOLDER: str = "*.log; *.tmp"
NEW_RULE_TEXT: str = "(type a mask)"
SWATCH_PIXELS: int = 16
TEXT_TITLE: str = "Text colour"
BACKGROUND_TITLE: str = "Background"


def swatch(colour: str) -> QIcon:
    pixmap = QPixmap(SWATCH_PIXELS, SWATCH_PIXELS)
    pixmap.fill(QColor(colour) if colour else Qt.GlobalColor.transparent)
    return QIcon(pixmap)


class ColourRulesEditor(QWidget):
    def __init__(self, rules: tuple[ColourRule, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.rules: list[ColourRule] = list(rules)
        self._showing: bool = False  # the fields are being filled from the list: no change of the rule
        self.list = QListWidget()
        self.new_button = QPushButton("New")
        self.delete_button = QPushButton("Delete")
        self.up_button = QPushButton("Move Up")
        self.down_button = QPushButton("Move Down")
        self.mask_field = QLineEdit()
        self.mask_field.setPlaceholderText(MASK_PLACEHOLDER)
        self.mask_field.setMaxLength(LONGEST_MASK)
        self.mask_field.setToolTip(MASK_TIP)
        self.text_box = QCheckBox(TEXT_TITLE)
        self.text_button = QPushButton("Choose…")
        self.background_box = QCheckBox(BACKGROUND_TITLE)
        self.background_button = QPushButton("Choose…")
        hint = QLabel(HINT_TEXT)
        hint.setWordWrap(True)
        buttons = QHBoxLayout()
        for button in (self.new_button, self.delete_button, self.up_button, self.down_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        text_row = QHBoxLayout()
        text_row.addWidget(self.text_box)
        text_row.addWidget(self.text_button)
        text_row.addStretch(1)
        background_row = QHBoxLayout()
        background_row.addWidget(self.background_box)
        background_row.addWidget(self.background_button)
        background_row.addStretch(1)
        fields = QFormLayout()
        fields.addRow("Mask", self.mask_field)
        fields.addRow("", self._row(text_row))
        fields.addRow("", self._row(background_row))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(hint)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        layout.addLayout(fields)
        for rule in self.rules:
            self.list.addItem(self._item(rule))
        self.list.currentRowChanged.connect(self._selected)
        self.new_button.clicked.connect(self.add_rule)
        self.delete_button.clicked.connect(self.delete_rule)
        self.up_button.clicked.connect(lambda: self.move_rule(-1))
        self.down_button.clicked.connect(lambda: self.move_rule(1))
        self.mask_field.textEdited.connect(self._mask_typed)
        self.text_box.toggled.connect(self._text_switched)
        self.background_box.toggled.connect(self._background_switched)
        self.text_button.clicked.connect(lambda: self.pick(True))
        self.background_button.clicked.connect(lambda: self.pick(False))
        if self.rules:
            self.list.setCurrentRow(0)
        self._selected(self.list.currentRow())

    @staticmethod
    def _row(layout: QHBoxLayout) -> QWidget:
        holder = QWidget()
        layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(layout)
        return holder

    @staticmethod
    def _item(rule: ColourRule) -> QListWidgetItem:
        item = QListWidgetItem()
        ColourRulesEditor._paint(item, rule)
        return item

    @staticmethod
    def _paint(item: QListWidgetItem, rule: ColourRule) -> None:
        """The rule in its own colours: what a row it fits looks like."""
        item.setText(rule.mask or NEW_RULE_TEXT)
        ink = rule.text or (colours.readable_text(rule.background) if rule.background else "")  # as the file list draws it (D-007, FQ-04)
        item.setData(Qt.ItemDataRole.ForegroundRole, QColor(ink) if ink else None)
        item.setData(Qt.ItemDataRole.BackgroundRole, QColor(rule.background) if rule.background else None)

    def _current(self) -> int:
        row = self.list.currentRow()
        return row if 0 <= row < len(self.rules) else -1

    def _set(self, row: int, rule: ColourRule) -> None:
        self.rules[row] = rule
        item = self.list.item(row)
        if item is not None:
            self._paint(item, rule)
        self._show_buttons()

    def _selected(self, _row: int) -> None:
        row = self._current()
        rule = self.rules[row] if row >= 0 else ColourRule("")
        self._showing = True
        try:
            self.mask_field.setText(rule.mask)
            self.text_box.setChecked(bool(rule.text))
            self.background_box.setChecked(bool(rule.background))
        finally:
            self._showing = False
        self._show_buttons()

    def _show_buttons(self) -> None:
        """What can't act is greyed with its reason as hover text (OR-20)."""
        row = self._current()
        chosen = row >= 0
        rule = self.rules[row] if chosen else ColourRule("")
        for widget in (self.mask_field, self.text_box, self.background_box, self.delete_button):
            widget.setEnabled(chosen)
            widget.setToolTip("" if chosen else NONE_SELECTED_TIP)
        self.mask_field.setToolTip(MASK_TIP if chosen else NONE_SELECTED_TIP)
        self.text_button.setEnabled(chosen and bool(rule.text))
        self.text_button.setIcon(swatch(rule.text))
        self.text_button.setToolTip("Pick the colour of the text" if chosen and rule.text else f"Tick {TEXT_TITLE} first" if chosen else NONE_SELECTED_TIP)
        self.background_button.setEnabled(chosen and bool(rule.background))
        self.background_button.setIcon(swatch(rule.background))
        self.background_button.setToolTip("Pick the colour of the row" if chosen and rule.background else f"Tick {BACKGROUND_TITLE} first" if chosen else NONE_SELECTED_TIP)
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < len(self.rules) - 1)
        self.new_button.setEnabled(len(self.rules) < MOST_RULES)
        self.new_button.setToolTip("Make a new rule at the end of the list" if len(self.rules) < MOST_RULES else FULL_TIP)

    def add_rule(self) -> None:
        if len(self.rules) >= MOST_RULES:
            return
        rule = ColourRule("", colours.FIRST_FILE_TEXT)
        self.rules.append(rule)
        self.list.addItem(self._item(rule))
        self.list.setCurrentRow(len(self.rules) - 1)
        self.mask_field.setFocus()

    def delete_rule(self) -> None:
        row = self._current()
        if row < 0:
            return
        self.rules.pop(row)
        self.list.takeItem(row)
        self._selected(self.list.currentRow())

    def move_rule(self, step: int) -> None:
        row = self._current()
        target = row + step
        if row < 0 or not 0 <= target < len(self.rules):
            return
        self.rules[row], self.rules[target] = self.rules[target], self.rules[row]
        for place in (row, target):
            item = self.list.item(place)
            if item is not None:
                self._paint(item, self.rules[place])
        self.list.setCurrentRow(target)

    def _mask_typed(self, text: str) -> None:
        row = self._current()
        if row >= 0 and not self._showing:
            self._set(row, replace(self.rules[row], mask=text))

    def _text_switched(self, on: bool) -> None:
        self._colour_switched(True, on)

    def _background_switched(self, on: bool) -> None:
        self._colour_switched(False, on)

    def _colour_switched(self, text: bool, on: bool) -> None:
        row = self._current()
        if row < 0 or self._showing:
            return
        rule = self.rules[row]
        if text:
            self._set(row, replace(rule, text=(rule.text or colours.FIRST_FILE_TEXT) if on else ""))
        else:
            self._set(row, replace(rule, background=(rule.background or colours.FIRST_FILE_BACKGROUND) if on else ""))

    def pick(self, text: bool) -> None:
        row = self._current()
        if row < 0:
            return
        rule = self.rules[row]
        start = rule.text if text else rule.background
        chosen = QColorDialog.getColor(QColor(start), self, TEXT_TITLE if text else BACKGROUND_TITLE)
        if not chosen.isValid():
            return  # cancelled: the colour stays
        name = chosen.name()
        self._set(row, replace(rule, text=name) if text else replace(rule, background=name))

    def chosen(self) -> tuple[ColourRule, ...]:
        """The rules in their order; one without a mask fits nothing and is left out."""
        return tuple(replace(rule, mask=rule.mask.strip()) for rule in self.rules if rule.mask.strip())
