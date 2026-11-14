from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSpinBox, QStackedWidget, QVBoxLayout, QWidget

from chrono_ftp.config.settings import BYTES, CLASSIC_KEYS, FILE_MANAGER_KEYS, KILOBYTES, SHORT, TITLE_FULL, TITLE_NONE, TITLE_SHORT
from chrono_ftp.config.settings import DOUBLE_CLICKS, FEWEST_AT_ONCE, FEWEST_REFRESH_SECONDS, FEWEST_THUMBNAIL_MEGABYTES, MOST_AT_ONCE, MOST_KEEP_DONE_SECONDS, MOST_REFRESH_SECONDS, MOST_THUMBNAIL_MEGABYTES, OPEN, TRANSFER, TREE_ABOVE, TREE_HIDDEN, TREE_LEFT, Settings
from chrono_ftp.connections.base import shown_name
from chrono_ftp.connections.sessionlog import log_folder
from chrono_ftp.core.colours import DARK, FOLLOW_DESKTOP, LIGHT
from chrono_ftp.core.masks import LONGEST_MASK
from chrono_ftp.core.logrules import DEBUG, DETAILED, LEAST_SIZE_MEGABYTES, MOST_KEPT, MOST_SIZE_MEGABYTES, NORMAL, OFF
from chrono_ftp.ui.colourrules import ColourRulesEditor
from chrono_ftp.ui.queueview import AT_ONCE_TEXT, AT_ONCE_TIP
from chrono_ftp.ui.presetseditor import PresetsEditor
from chrono_ftp.ui.transferoptionsform import TransferOptionsForm

# --- Version ------------------------------------------------------------
__version__ = "settingsdialog.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: The Look choice on the General page: the same as the desktop, dark or light (FQ-04)
# v0.0.41: Masks (block FD): the Transfers page has the file mask, text and names tabs (FD-01 to FD-03); a Presets page with the named
#          sets of transfer settings and their rules (FD-04); the history of masks stays as it is
# v0.0.40: Selection and keys (block FG): the key set and the path in the title bar on the General page; the two-pane selection mode,
#          the size format, natural sort and moving by a right-button drag on the Panels page
# v0.0.39: Looks (block FQ): a Colours page with the rules of file colours (FQ-01); the largest file and the names that get a thumbnail
#          on the Panels page (FQ-02); the toolbar choices, made on the toolbars themselves, stay as they are (FQ-03)
# v0.0.38: Preferences (FP-01): pages on the left (General, Panels, Transfers, Queue, Confirmations, Log), each choice on its page with
#          the values it had; the confirmations, the double-click, the automatic refresh and the columns per tab; the session log's
#          level, size and kept logs (FP-02); Clean Up Stored Data on the General page (FP-04); the last connection stays as it was
# v0.0.13: Queue: how long a finished item stays, what an error does, and whether items start by themselves (FC-02, FC-03); how many
#          transfers run at once (FC-01), named and explained as in the queue's title bar
# v0.0.12: Transfers: the options every transfer starts with, and the switch that brings the transfer window back (FB-01, FB-09)
# v0.0.6: Hidden files shown or not
# v0.0.5: Initial: where the folder tree is (the owner)

WINDOW_TITLE: str = "Settings"
PAGES: tuple[str, ...] = ("General", "Panels", "Colours", "Transfers", "Presets", "Queue", "Confirmations", "Log")
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
THUMBNAIL_SIZE_TIP: str = "A larger file gets no thumbnail, so a picture is never a long wait. Thumbnails are kept in memory only."
KEY_SET_LABELS: dict[str, str] = {CLASSIC_KEYS: "Classic two-pane keys", FILE_MANAGER_KEYS: "File manager keys"}
KEY_SET_TIP: str = "Classic two-pane keys: Alt+F7 finds files and Ctrl+F4 sorts by extension. File manager keys: Ctrl+F finds files. The other keys are the same in both."
TITLE_LABELS: dict[str, str] = {TITLE_SHORT: "The last folder of the server", TITLE_FULL: "The whole path on the server", TITLE_NONE: "No path"}
TITLE_TIP: str = "What the title bar shows after the name and version of Chrono FTP"
SIZE_LABELS: dict[str, str] = {SHORT: "Short: 1.2 MB", KILOBYTES: "Kilobytes: 1,206 KB", BYTES: "Bytes: 1,234,567"}
TWO_PANE_TEXT: str = "Two-pane selection: a click or an arrow key moves without selecting"
TWO_PANE_TIP: str = "Insert or Space marks the file under the cursor, Ctrl+click marks too. An action works on the marked files, or on the file under the cursor when nothing is marked."
NATURAL_TEXT: str = "Natural order of names: file2 before file10"
NATURAL_TIP: str = "Folders always come first, ordered by their names."
DRAG_MOVE_TEXT: str = "Offer Move Here after dragging with the right mouse button"
DRAG_MOVE_TIP: str = "Dragging with the right mouse button ends with a small menu: Copy Here, Move Here or Cancel. Dragging with the left button is not changed."
THUMBNAIL_MASK_TIP: str = "The names that get a thumbnail in the Thumbnails view; other files keep their normal icon. Empty: no thumbnails."
LOOK_LABELS: dict[str, str] = {FOLLOW_DESKTOP: "The same as my desktop", DARK: "Dark", LIGHT: "Light"}
LOOK_TIP: str = "The colours of the window. The same as my desktop: Chrono FTP turns light or dark along with your desktop, while it is open; where your desktop says nothing about it, Chrono FTP stays dark. The colours you gave a connection, and the colours of your file rules, stay as you chose them."


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None, clean_up: Callable[[], bool] | None = None) -> None:
        """clean_up: opens the Clean Up window; True when it removed the settings, which closes this window without keeping anything."""
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.last_connection: str = settings.last_connection  # not shown here, and kept as it is
        self.toolbars = settings.toolbars  # chosen on the toolbars themselves (FQ-03), and kept as they are
        self.mask_history = settings.mask_history  # grows where a mask is used, and kept as it is
        self.clean_up_callback: Callable[[], bool] | None = clean_up
        self.pages = QStackedWidget()
        self.page_list = QListWidget()
        self.page_list.addItems(list(PAGES))
        self.page_list.setMaximumWidth(self.fontMetrics().averageCharWidth() * PAGE_LIST_CHARACTERS * 2)
        self.page_list.currentRowChanged.connect(self.pages.setCurrentIndex)

        # General
        self.look = QComboBox()
        for look, label in LOOK_LABELS.items():
            self.look.addItem(label, look)
        self.look.setCurrentIndex(self.look.findData(settings.look))
        self.look.setToolTip(LOOK_TIP)
        self.tree_place = QComboBox()
        for place, label in TREE_LABELS.items():
            self.tree_place.addItem(label, place)
        self.tree_place.setCurrentIndex(self.tree_place.findData(settings.tree_place))
        self.clean_up_button = QPushButton(CLEAN_UP_TEXT)
        self.clean_up_button.setToolTip(CLEAN_UP_TIP)
        self.clean_up_button.setEnabled(clean_up is not None)
        self.clean_up_button.clicked.connect(self._clean_up)
        self.key_set = QComboBox()
        for key_set, label in KEY_SET_LABELS.items():
            self.key_set.addItem(label, key_set)
        self.key_set.setCurrentIndex(self.key_set.findData(settings.key_set))
        self.key_set.setToolTip(KEY_SET_TIP)
        self.title_path = QComboBox()
        for place, label in TITLE_LABELS.items():
            self.title_path.addItem(label, place)
        self.title_path.setCurrentIndex(self.title_path.findData(settings.title_path))
        self.title_path.setToolTip(TITLE_TIP)
        general = QFormLayout()
        general.addRow("Look", self.look)
        general.addRow("Folder tree", self.tree_place)
        general.addRow("Keys", self.key_set)
        general.addRow("Title bar", self.title_path)
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
        self.thumbnail_size = QSpinBox()
        self.thumbnail_size.setRange(FEWEST_THUMBNAIL_MEGABYTES, MOST_THUMBNAIL_MEGABYTES)
        self.thumbnail_size.setSuffix(" MB")
        self.thumbnail_size.setValue(settings.thumbnail_megabytes)
        self.thumbnail_size.setToolTip(THUMBNAIL_SIZE_TIP)
        self.thumbnail_mask = QLineEdit(settings.thumbnail_mask)
        self.thumbnail_mask.setMaxLength(LONGEST_MASK)
        self.thumbnail_mask.setToolTip(THUMBNAIL_MASK_TIP)
        self.two_pane = QCheckBox(TWO_PANE_TEXT)
        self.two_pane.setChecked(settings.two_pane_selection)
        self.two_pane.setToolTip(TWO_PANE_TIP)
        self.size_format = QComboBox()
        for style, label in SIZE_LABELS.items():
            self.size_format.addItem(label, style)
        self.size_format.setCurrentIndex(self.size_format.findData(settings.size_format))
        self.natural_sort = QCheckBox(NATURAL_TEXT)
        self.natural_sort.setChecked(settings.natural_sort)
        self.natural_sort.setToolTip(NATURAL_TIP)
        self.drag_move = QCheckBox(DRAG_MOVE_TEXT)
        self.drag_move.setChecked(settings.drag_move)
        self.drag_move.setToolTip(DRAG_MOVE_TIP)
        panels = QFormLayout()
        panels.addRow("", self.show_hidden)
        panels.addRow("", self.two_pane)
        panels.addRow("Sizes", self.size_format)
        panels.addRow("", self.natural_sort)
        panels.addRow("", self.drag_move)
        panels.addRow("A double-click on a file", self.double_click)
        panels.addRow("Refresh the server's folder", self.refresh)
        panels.addRow("", self.columns_per_tab)
        panels.addRow("Thumbnails of files up to", self.thumbnail_size)
        panels.addRow("Thumbnails of the names", self.thumbnail_mask)

        # Colours
        self.file_colours = ColourRulesEditor(settings.file_colours)
        colour_page = QVBoxLayout()
        colour_page.addWidget(self.file_colours)

        # Transfers
        self.transfer_options = TransferOptionsForm(history=settings.mask_history)
        self.transfer_options.set_options(settings.transfer_options)  # the defaults hold for every connection: nothing is greyed here
        self.show_transfer_window = QCheckBox("Show the transfer window before each upload and download")
        self.show_transfer_window.setToolTip("It shows the target folder and the settings of a transfer before it starts")
        self.show_transfer_window.setChecked(settings.show_transfer_window)
        transfers = QVBoxLayout()
        transfers.addWidget(self.show_transfer_window)
        transfers.addWidget(self.transfer_options)

        # Presets
        self.presets = PresetsEditor(settings.presets, self.transfer_options.chosen, settings.mask_history)
        presets = QVBoxLayout()
        presets.addWidget(self.presets)

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

        for layout in (general, panels, colour_page, transfers, presets, queue, confirmations, log):
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
        self.presets.form.changed.connect(self._check)
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
            acceptable = self.transfer_options.acceptable() and self.presets.acceptable()  # a rights value that is not complete is never kept
            ok.setEnabled(acceptable)
            ok.setToolTip(self.transfer_options.problem_text() or self.presets.form.problem_text())

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
            file_colours=self.file_colours.chosen(),
            thumbnail_megabytes=self.thumbnail_size.value(),
            thumbnail_mask=self.thumbnail_mask.text().strip(),
            toolbars=self.toolbars,
            two_pane_selection=self.two_pane.isChecked(),
            key_set=key_set if isinstance(key_set := self.key_set.currentData(), str) else CLASSIC_KEYS,
            size_format=size if isinstance(size := self.size_format.currentData(), str) else SHORT,
            natural_sort=self.natural_sort.isChecked(),
            title_path=place if isinstance(place := self.title_path.currentData(), str) else TITLE_SHORT,
            drag_move=self.drag_move.isChecked(),
            presets=self.presets.chosen(),
            mask_history=self.mask_history,
            look=look if isinstance(look := self.look.currentData(), str) else FOLLOW_DESKTOP,
        )
