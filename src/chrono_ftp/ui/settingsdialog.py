from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QVBoxLayout, QWidget

from chrono_ftp.config.settings import TREE_ABOVE, TREE_HIDDEN, TREE_LEFT, Settings
from chrono_ftp.ui.transferoptionsform import RIGHTS_INCOMPLETE_TEXT, TransferOptionsForm

# --- Version ------------------------------------------------------------
__version__ = "settingsdialog.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Transfers: the options every transfer starts with, and the switch that brings the transfer window back (FB-01, FB-09)
# v0.0.6: Hidden files shown or not
# v0.0.5: Initial: where the folder tree is (the owner)

WINDOW_TITLE: str = "Settings"
TREE_LABELS: dict[str, str] = {TREE_LEFT: "To the left of the file list", TREE_ABOVE: "Above the file list", TREE_HIDDEN: "Hidden"}


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.tree_place = QComboBox()
        for place, label in TREE_LABELS.items():
            self.tree_place.addItem(label, place)
        self.tree_place.setCurrentIndex(self.tree_place.findData(settings.tree_place))
        self.show_hidden = QCheckBox("Show hidden files")
        self.show_hidden.setChecked(settings.show_hidden)
        form = QFormLayout()
        form.addRow("Folder tree", self.tree_place)
        form.addRow("", self.show_hidden)
        self.transfer_options = TransferOptionsForm()
        self.transfer_options.set_options(settings.transfer_options)  # the defaults hold for every connection: nothing is greyed here
        self.show_transfer_window = QCheckBox("Show the transfer window before each upload and download")
        self.show_transfer_window.setToolTip("It shows the target folder and the settings of a transfer before it starts")
        self.show_transfer_window.setChecked(settings.show_transfer_window)
        transfers = QGroupBox("Transfers")
        transfers_layout = QVBoxLayout(transfers)
        transfers_layout.addWidget(self.show_transfer_window)
        transfers_layout.addWidget(self.transfer_options)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.transfer_options.changed.connect(self._check)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(transfers)
        layout.addWidget(self.buttons)

    def _check(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self.transfer_options.acceptable())  # a rights value that is not complete is never kept
            ok.setToolTip("" if self.transfer_options.acceptable() else RIGHTS_INCOMPLETE_TEXT)

    def chosen(self) -> Settings:
        return Settings(str(self.tree_place.currentData()), self.show_hidden.isChecked(), self.transfer_options.chosen(), self.show_transfer_window.isChecked())
