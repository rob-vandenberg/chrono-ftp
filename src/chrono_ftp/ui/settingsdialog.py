from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QSpinBox, QVBoxLayout, QWidget

from chrono_ftp.config.settings import FEWEST_AT_ONCE, MOST_AT_ONCE, MOST_KEEP_DONE_SECONDS, TREE_ABOVE, TREE_HIDDEN, TREE_LEFT, Settings
from chrono_ftp.ui.queueview import AT_ONCE_TEXT, AT_ONCE_TIP
from chrono_ftp.ui.transferoptionsform import RIGHTS_INCOMPLETE_TEXT, TransferOptionsForm

# --- Version ------------------------------------------------------------
__version__ = "settingsdialog.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Queue: how long a finished item stays, what an error does, and whether items start by themselves (FC-02, FC-03); how many
#          transfers run at once (FC-01), named and explained as in the queue's title bar
# v0.0.12: Transfers: the options every transfer starts with, and the switch that brings the transfer window back (FB-01, FB-09)
# v0.0.6: Hidden files shown or not
# v0.0.5: Initial: where the folder tree is (the owner)

WINDOW_TITLE: str = "Settings"
TREE_LABELS: dict[str, str] = {TREE_LEFT: "To the left of the file list", TREE_ABOVE: "Above the file list", TREE_HIDDEN: "Hidden"}
GO_ON_TEXT: str = "Go on with the next item"
PAUSE_TEXT: str = "Pause the queue"
START_TEXT: str = "Start items by themselves"
START_TIP: str = "When this is off, a new item waits in the queue, paused, until you start it with Resume"
KEEP_TIP: str = "A finished item stays in the queue this long, so you can see it went well. Stopped and failed items stay until you retry, delete or clear them."
ERROR_TIP: str = "What the queue does when an item can't be transferred. Paused items go on with Resume All."


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
        self.keep_done = QSpinBox()
        self.keep_done.setRange(0, MOST_KEEP_DONE_SECONDS)
        self.keep_done.setSuffix(" s")
        self.keep_done.setValue(settings.keep_done_seconds)
        self.keep_done.setToolTip(KEEP_TIP)
        self.on_error = QComboBox()
        self.on_error.addItem(GO_ON_TEXT, False)
        self.on_error.addItem(PAUSE_TEXT, True)
        self.on_error.setCurrentIndex(1 if settings.pause_on_error else 0)
        self.on_error.setToolTip(ERROR_TIP)
        self.start_by_themselves = QCheckBox(START_TEXT)
        self.start_by_themselves.setChecked(settings.start_by_themselves)
        self.start_by_themselves.setToolTip(START_TIP)
        self.at_once = QSpinBox()
        self.at_once.setRange(FEWEST_AT_ONCE, MOST_AT_ONCE)
        self.at_once.setValue(settings.transfers_at_once)
        self.at_once.setToolTip(AT_ONCE_TIP)
        queue = QGroupBox("Queue")
        queue_layout = QFormLayout(queue)
        queue_layout.addRow(AT_ONCE_TEXT, self.at_once)
        queue_layout.addRow("Keep finished items for", self.keep_done)
        queue_layout.addRow("When an item fails", self.on_error)
        queue_layout.addRow("", self.start_by_themselves)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.transfer_options.changed.connect(self._check)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(transfers)
        layout.addWidget(queue)
        layout.addWidget(self.buttons)

    def _check(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self.transfer_options.acceptable())  # a rights value that is not complete is never kept
            ok.setToolTip("" if self.transfer_options.acceptable() else RIGHTS_INCOMPLETE_TEXT)

    def chosen(self) -> Settings:
        return Settings(str(self.tree_place.currentData()), self.show_hidden.isChecked(), self.transfer_options.chosen(), self.show_transfer_window.isChecked(), self.start_by_themselves.isChecked(), self.keep_done.value(), self.on_error.currentData() is True, self.at_once.value())
