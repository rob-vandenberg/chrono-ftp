"""The Presets page of Settings (FD-04): named sets of transfer options, each with rules on the host, the user, the remote folder and the
local folder; New, Rename, Duplicate and Delete. The transfer window takes the first preset whose rules all fit."""

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QInputDialog, QLineEdit, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.core.masks import LONGEST_MASK
from chrono_ftp.core.presets import LONGEST_NAME, MOST_PRESETS, STANDARD_NAME, Preset, PresetRules, name_problem
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.ui.plaintext import box_text, plain_label
from chrono_ftp.ui.transferoptionsform import TransferOptionsForm

# --- Version ------------------------------------------------------------
__version__ = "presetseditor.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: the presets with their rules and options; New, Rename, Duplicate and Delete (D-084)

INTRO_TEXT: str = (f"A preset is a named set of transfer settings. The transfer window takes the first preset whose rules all fit the "
                   f"transfer, and shows its name; you can choose another there. A preset without rules is only chosen by hand. "
                   f"Without a fitting preset, the settings of the Transfers page hold, named “{STANDARD_NAME}”.")
RULES_TIP: str = "A mask of the whole value, such as *.example.com or /var/www/*; several separated by ;. Empty: any."
NEW_TEXT: str = "New…"
RENAME_TEXT: str = "Rename…"
DUPLICATE_TEXT: str = "Duplicate"
DELETE_TEXT: str = "Delete"
NOTHING_CHOSEN_TEXT: str = "Choose a preset in the list first."
FULL_TEXT: str = f"There are {MOST_PRESETS} presets already."
NAME_LABEL: str = "Name of the preset:"
COPY_NAME: str = "{name} (copy)"


class PresetsEditor(QWidget):
    """standard: the options of the Transfers page now; a new preset starts with them."""

    def __init__(self, presets: tuple[Preset, ...], standard: Callable[[], TransferOptions], history: tuple[str, ...] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.presets: list[Preset] = list(presets)
        self.standard: Callable[[], TransferOptions] = standard
        self.row: int = -1  # the preset whose fields are shown
        intro = plain_label(INTRO_TEXT)
        intro.setWordWrap(True)
        self.names = QListWidget()
        self.names.addItems([preset.name for preset in self.presets])
        self.new_button = QPushButton(NEW_TEXT)
        self.rename_button = QPushButton(RENAME_TEXT)
        self.duplicate_button = QPushButton(DUPLICATE_TEXT)
        self.delete_button = QPushButton(DELETE_TEXT)
        self.host = QLineEdit()
        self.user = QLineEdit()
        self.remote_folder = QLineEdit()
        self.local_folder = QLineEdit()
        for field, placeholder in ((self.host, "*.example.com"), (self.user, "www-data"), (self.remote_folder, "/var/www/*"), (self.local_folder, "~/Sites/*")):
            field.setMaxLength(LONGEST_MASK)
            field.setPlaceholderText(placeholder)
            field.setToolTip(RULES_TIP)
        self.form = TransferOptionsForm(history=history)
        buttons = QHBoxLayout()
        for button in (self.new_button, self.rename_button, self.duplicate_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        rules = QFormLayout()
        rules.addRow("Host", self.host)
        rules.addRow("User", self.user)
        rules.addRow("Remote folder", self.remote_folder)
        rules.addRow("Local folder", self.local_folder)
        self.details = QWidget()
        details = QVBoxLayout(self.details)
        details.setContentsMargins(0, 0, 0, 0)
        details.addLayout(rules)
        details.addWidget(self.form)
        top = QHBoxLayout()
        top.addWidget(self.names, 1)
        top.addWidget(self.details, 3)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(intro)
        layout.addLayout(buttons)
        layout.addLayout(top)
        self.names.currentRowChanged.connect(self._chosen)
        self.new_button.clicked.connect(self.new_preset)
        self.rename_button.clicked.connect(self.rename_preset)
        self.duplicate_button.clicked.connect(self.duplicate_preset)
        self.delete_button.clicked.connect(self.delete_preset)
        self.names.setCurrentRow(0 if self.presets else -1)
        self._show_state()

    def acceptable(self) -> bool:
        return self.row < 0 or self.form.acceptable()

    def _keep_shown(self) -> None:
        """What the fields say goes into the preset shown."""
        if 0 <= self.row < len(self.presets):
            rules = PresetRules(self.host.text().strip(), self.user.text().strip(), self.remote_folder.text().strip(), self.local_folder.text().strip())
            self.presets[self.row] = replace(self.presets[self.row], options=self.form.chosen(), rules=rules)

    def _chosen(self, row: int) -> None:
        self._keep_shown()
        self.row = row
        if 0 <= row < len(self.presets):
            preset = self.presets[row]
            self.host.setText(preset.rules.host)
            self.user.setText(preset.rules.user)
            self.remote_folder.setText(preset.rules.remote_folder)
            self.local_folder.setText(preset.rules.local_folder)
            self.form.set_options(preset.options)  # a preset holds for every connection: nothing is greyed here
        self._show_state()

    def _show_state(self) -> None:
        chosen = 0 <= self.row < len(self.presets)
        self.details.setEnabled(chosen)
        for button in (self.rename_button, self.duplicate_button, self.delete_button):
            button.setEnabled(chosen)
            button.setToolTip("" if chosen else NOTHING_CHOSEN_TEXT)
        room = len(self.presets) < MOST_PRESETS
        self.new_button.setEnabled(room)
        self.new_button.setToolTip("" if room else FULL_TEXT)
        if not room:
            self.duplicate_button.setEnabled(False)
            self.duplicate_button.setToolTip(FULL_TEXT)

    def _ask_name(self, start: str, taken: list[str]) -> str | None:
        """A free name typed by the person; None when they cancel. A name that can't be taken is said, and asked again."""
        while True:
            text, given = QInputDialog.getText(self, "Preset", NAME_LABEL, QLineEdit.EchoMode.Normal, start)
            if not given:
                return None
            problem = name_problem(text, taken)
            if not problem:
                return text.strip()
            QMessageBox.information(self, "Preset", box_text(problem))
            start = text[:LONGEST_NAME]

    def _add(self, preset: Preset) -> None:
        self._keep_shown()
        self.presets.append(preset)
        self.names.addItem(preset.name)
        self.names.setCurrentRow(len(self.presets) - 1)

    def new_preset(self) -> None:
        name = self._ask_name("", [preset.name for preset in self.presets])
        if name is not None:
            self._add(Preset(name, replace(self.standard(), delete_source=False)))

    def rename_preset(self) -> None:
        if not 0 <= self.row < len(self.presets):
            return
        self._keep_shown()
        others = [preset.name for number, preset in enumerate(self.presets) if number != self.row]
        name = self._ask_name(self.presets[self.row].name, others)
        if name is not None:
            self.presets[self.row] = replace(self.presets[self.row], name=name)
            item = self.names.item(self.row)
            if item is not None:
                item.setText(name)

    def duplicate_preset(self) -> None:
        if not 0 <= self.row < len(self.presets):
            return
        self._keep_shown()
        original = self.presets[self.row]
        taken = [preset.name for preset in self.presets]
        name = COPY_NAME.format(name=original.name)[:LONGEST_NAME]
        number = 2
        while name_problem(name, taken):
            name = f"{COPY_NAME.format(name=original.name)} {number}"[-LONGEST_NAME:]
            number += 1
        self._add(replace(original, name=name))

    def delete_preset(self) -> None:
        if not 0 <= self.row < len(self.presets):
            return
        row = self.row
        self.row = -1  # nothing of the fields goes back into a preset that is gone
        del self.presets[row]
        self.names.takeItem(row)
        self._chosen(self.names.currentRow())

    def chosen(self) -> tuple[Preset, ...]:
        self._keep_shown()
        return tuple(self.presets)
