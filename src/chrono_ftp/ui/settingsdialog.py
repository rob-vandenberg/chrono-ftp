from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QVBoxLayout, QWidget

from chrono_ftp.config.settings import TREE_ABOVE, TREE_HIDDEN, TREE_LEFT, Settings

# --- Version ------------------------------------------------------------
__version__ = "settingsdialog.py 0.0.5"

# --- Version history ----------------------------------------------------
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
        form = QFormLayout()
        form.addRow("Folder tree", self.tree_place)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def chosen(self) -> Settings:
        return Settings(str(self.tree_place.currentData()))
