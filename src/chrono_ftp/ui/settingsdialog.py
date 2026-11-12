from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget

from chrono_ftp.config.settings import DOUBLE_CLICKS, FEWEST_AT_ONCE, FEWEST_REFRESH_SECONDS, MOST_AT_ONCE, MOST_KEEP_DONE_SECONDS, MOST_REFRESH_SECONDS, OPEN, TRANSFER, TREE_ABOVE, TREE_HIDDEN, TREE_LEFT, Settings
from chrono_ftp.connections.base import shown_name
from chrono_ftp.connections.sessionlog import log_folder
from chrono_ftp.core.logrules import DEBUG, DETAILED, LEAST_SIZE_MEGABYTES, MOST_KEPT, MOST_SIZE_MEGABYTES, NORMAL, OFF
from chrono_ftp.ui.queueview import AT_ONCE_TEXT, AT_ONCE_TIP
from chrono_ftp.ui.transferoptionsform import RIGHTS_INCOMPLETE_TEXT, TransferOptionsForm

# --- Version ------------------------------------------------------------
__version__ = "settingsdialog.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Preferences (FP-01): pages on the left (General, Panels, Transfers, Queue, Confirmations, Log), each choice on its page with
#          the values it had; the confirmations, the double-click, the automatic refresh and the columns per tab; the session log's
#          level, size and kept logs (FP-02); Clean Up Stored Data on the General page (FP-04); the last connection stays as it was
# v0.0.13: Queue: how long a finished item stays, what an error does, and whether items start by themselves (FC-02, FC-03); how many
#          transfers run at once (FC-01), named and explained as in the queue's title bar
# v0.0.12: Transfers: the options every transfer starts with, and the switch that brings the transfer window back (FB-01, FB-09)
# v0.0.6: Hidden files shown or not
# v0.0.5: Initial: where the folder tree is (the owner)

WINDOW_TITLE: str = "Settings"
PAGES: tuple[str, ...] = ("General", "Panels", "Transfers", "Queue", "Confirmations", "Log")
TREE_LABELS: dict[str, str] = {TREE_LEFT: "To the left of the file list", TREE_ABOVE: "Above the file list", TREE_HIDDEN: "Hidden"}
GO_ON_TEXT: str = "Go on with the next item"
PAUSE_TEXT: str = "Pause the queue"
START_TEXT: str = "Start items by themselves"
START_TIP: str = "When this is off, a new item waits in the queue, paused, until you start it with Resume"
KEEP_TIP: str = "A finished item stays in the queue this long, so you can see it went well. Stopped and failed items stay until you retry, delete or clear them."
ERROR_TIP: str = "What the queue does when an item can't be transferred. Paused items go on with Resume All."
DOUBLE_CLICK_LABELS: dict[str, str] = {OPEN: "Opens it (on the server through Edit)", TRANSFER: "Copies it to the other side"}
REFRESH_OFF_TEXT: str = "Never by itself"
REFRESH_TIP: str = "The server's folder on the right is read again this often while the tab is shown. Each time asks the server a little work."
COLUMNS_TIP: str = "On: each tab keeps its own sort order and column widths. The folders are kept per tab always."
CLOSE_TAB_TEXT: str = "Ask before closing a tab whose transfers are not finished"
DELETE_TEXT: str = "Ask before deleting"
DELETE_TIP: str = "On this computer files go to the Trash; on a server they are gone for good."
DROP_TEXT: str = "Ask before a dragged file is transferred"
DROP_TIP: str = "The transfer window shows first, or a short question where that window is switched off on the Transfers page."
LOG_LEVEL_LABELS: dict[int, str] = {OFF: "Off", NORMAL: "Normal: each request and answer", DETAILED: "Detailed: also every line of each answer", DEBUG: "Debug: also the connection itself"}
LOG_TIP: str = "A log per connection of what Chrono FTP asks the server and what it answers. Passwords and keys are never written."
CLEAN_UP_TEXT: str = "Clean Up Stored Data…"
CLEAN_UP_TIP: str = "See what Chrono FTP keeps on this computer, and remove what you choose"
PAGE_LIST_CHARACTERS: int = 14


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None, clean_up: Callable[[], bool] | None = None) -> None:
        """clean_up: opens the Clean Up window; True when it removed the settings, which closes this window without keeping anything."""
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.last_connection: str = settings.last_connection  # not shown here, and kept as it is
        self.clean_up_callback: Callable[[], bool] | None = clean_up
        self.pages = QStackedWidget()
        self.page_list = QListWidget()
        self.page_list.addItems(list(PAGES))
        self.page_list.setMaximumWidth(self.fontMetrics().averageCharWidth() * PAGE_LIST_CHARACTERS * 2)
        self.page_list.currentRowChanged.connect(self.pages.setCurrentIndex)

        # General
        self.tree_place = QComboBox()
        for place, label in TREE_LABELS.items():
            self.tree_place.addItem(label, place)
        self.tree_place.setCurrentIndex(self.tree_place.findData(settings.tree_place))
        self.clean_up_button = QPushButton(CLEAN_UP_TEXT)
        self.clean_up_button.setToolTip(CLEAN_UP_TIP)
        self.clean_up_button.setEnabled(clean_up is not None)
        self.clean_up_button.clicked.connect(self._clean_up)
        general = QFormLayout()
        general.addRow("Folder tree", self.tree_place)
        general.addRow("Stored data", self.clean_up_button)

        # Panels
        self.show_hidden = QCheckBox("Show hidden files")
        self.show_hidden.setChecked(settings.show_hidden)
        self.double_click = QComboBox()
        for choice in DOUBLE_CLICKS:
            self.double_click.addItem(DOUBLE_CLICK_LABELS[choice], choice)
        self.double_click.setCurrentIndex(self.double_click.findData(settings.double_click))
        self.refresh = QSpinBox()
        self.refresh.setRange(FEWEST_REFRESH_SECONDS - 1, MOST_REFRESH_SECONDS)  # the lowest step is "never"
        self.refresh.setSpecialValueText(REFRESH_OFF_TEXT)
        self.refresh.setSuffix(" s")
        self.refresh.setValue(settings.refresh_seconds or FEWEST_REFRESH_SECONDS - 1)
        self.refresh.setToolTip(REFRESH_TIP)
        self.columns_per_tab = QCheckBox("Each tab keeps its own sort order and columns")
        self.columns_per_tab.setChecked(settings.columns_per_tab)
        self.columns_per_tab.setToolTip(COLUMNS_TIP)
        panels = QFormLayout()
        panels.addRow("", self.show_hidden)
        panels.addRow("A double-click on a file", self.double_click)
        panels.addRow("Refresh the server's folder", self.refresh)
        panels.addRow("", self.columns_per_tab)

        # Transfers
        self.transfer_options = TransferOptionsForm()
        self.transfer_options.set_options(settings.transfer_options)  # the defaults hold for every connection: nothing is greyed here
        self.show_transfer_window = QCheckBox("Show the transfer window before each upload and download")
        self.show_transfer_window.setToolTip("It shows the target folder and the settings of a transfer before it starts")
        self.show_transfer_window.setChecked(settings.show_transfer_window)
        transfers = QVBoxLayout()
        transfers.addWidget(self.show_transfer_window)
        transfers.addWidget(self.transfer_options)

        # Queue
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
        queue = QFormLayout()
        queue.addRow(AT_ONCE_TEXT, self.at_once)
        queue.addRow("Keep finished items for", self.keep_done)
        queue.addRow("When an item fails", self.on_error)
        queue.addRow("", self.start_by_themselves)

        # Confirmations
        self.confirm_close_running = QCheckBox(CLOSE_TAB_TEXT)
        self.confirm_close_running.setChecked(settings.confirm_close_running)
        self.confirm_delete = QCheckBox(DELETE_TEXT)
        self.confirm_delete.setChecked(settings.confirm_delete)
        self.confirm_delete.setToolTip(DELETE_TIP)
        self.confirm_drop = QCheckBox(DROP_TEXT)
        self.confirm_drop.setChecked(settings.confirm_drop)
        self.confirm_drop.setToolTip(DROP_TIP)
        confirmations = QVBoxLayout()
        for box in (self.confirm_close_running, self.confirm_delete, self.confirm_drop):
            confirmations.addWidget(box)

        # Log
        self.log_level = QComboBox()
        for level, label in LOG_LEVEL_LABELS.items():
            self.log_level.addItem(label, level)
        self.log_level.setCurrentIndex(self.log_level.findData(settings.log_level))
        self.log_level.setToolTip(LOG_TIP)
        self.log_size = QSpinBox()
        self.log_size.setRange(LEAST_SIZE_MEGABYTES, MOST_SIZE_MEGABYTES)
        self.log_size.setSuffix(" MB")
        self.log_size.setValue(settings.log_size_megabytes)
        self.log_size.setToolTip("A log that reaches this size starts afresh; the earlier one is kept as an older log")
        self.logs_kept = QSpinBox()
        self.logs_kept.setRange(0, MOST_KEPT)
        self.logs_kept.setValue(settings.logs_kept)
        self.logs_kept.setToolTip("How many older logs of each connection stay; the oldest goes first")
        self.log_place = QLabel(shown_name(str(log_folder())))
        self.log_place.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        log = QFormLayout()
        log.addRow("Session log", self.log_level)
        log.addRow("Largest log", self.log_size)
        log.addRow("Older logs kept", self.logs_kept)
        log.addRow("Written to", self.log_place)

        for layout in (general, panels, transfers, queue, confirmations, log):
            page = QWidget()
            holder = QVBoxLayout(page)
            box = QGroupBox(PAGES[self.pages.count()])
            box.setLayout(layout)
            holder.addWidget(box)
            holder.addStretch(1)
            self.pages.addWidget(page)
        self.page_list.setCurrentRow(0)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.transfer_options.changed.connect(self._check)
        body = QHBoxLayout()
        body.addWidget(self.page_list)
        body.addWidget(self.pages, 1)
        outer = QVBoxLayout(self)
        outer.addLayout(body)
        outer.addWidget(self.buttons)

    def show_page(self, name: str) -> None:
        self.page_list.setCurrentRow(PAGES.index(name))

    def _clean_up(self) -> None:
        if self.clean_up_callback is not None and self.clean_up_callback():
            self.reject()  # the settings were removed: nothing of this window is kept over that

    def _check(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self.transfer_options.acceptable())  # a rights value that is not complete is never kept
            ok.setToolTip("" if self.transfer_options.acceptable() else RIGHTS_INCOMPLETE_TEXT)

    def chosen(self) -> Settings:
        refresh = self.refresh.value()
        level = self.log_level.currentData()
        click = self.double_click.currentData()
        return Settings(
            str(self.tree_place.currentData()),
            self.show_hidden.isChecked(),
            self.transfer_options.chosen(),
            self.show_transfer_window.isChecked(),
            self.start_by_themselves.isChecked(),
            self.keep_done.value(),
            self.on_error.currentData() is True,
            self.at_once.value(),
            self.last_connection,
            confirm_close_running=self.confirm_close_running.isChecked(),
            confirm_delete=self.confirm_delete.isChecked(),
            confirm_drop=self.confirm_drop.isChecked(),
            double_click=click if isinstance(click, str) else OPEN,
            refresh_seconds=refresh if refresh >= FEWEST_REFRESH_SECONDS else 0,
            columns_per_tab=self.columns_per_tab.isChecked(),
            log_level=level if isinstance(level, int) else OFF,
            log_size_megabytes=self.log_size.value(),
            logs_kept=self.logs_kept.value(),
        )
