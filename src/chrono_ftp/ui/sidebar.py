from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QHBoxLayout, QMenu, QToolBar, QToolButton, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.engine.listing import history_shown
from chrono_ftp.ui import icons
from chrono_ftp.ui.menus import as_drop_down
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "sidebar.py 0.0.40"

# --- Version history ----------------------------------------------------
# v0.0.40: Back and Forward have a drop-down with the last folders, and the history can go several steps at once (FG-10); the status
#          line of a side, with the hidden and filtered counts to click (FG-09)
# v0.0.39: A Thumbnails button, switched on and off (FQ-02)
# v0.0.34: A Bookmarks button, whose menu adds, opens and organizes the bookmarks of the side (FH-02)
# v0.0.23: The icons of the side bars are 24 pixels
# v0.0.7: One compact row of buttons per side, icons only with the text on hover
# v0.0.6: Initial: the buttons every side has for walking through folders, and where it has been

ICON_SIZE: int = 24
ROW_GAP: int = 12  # between the walking buttons and the action buttons
BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("back", "Back", "go-previous"),
    ("forward", "Forward", "go-next"),
    ("parent", "Parent folder", "go-parent-folder"),
    ("root", "Root folder", "folder-root"),
    ("home", "Home folder", "go-home"),
    ("bookmarks", "Bookmarks: add this folder, open a folder, organize", "bookmarks"),
    ("refresh", "Refresh", "view-refresh"),
    ("filter", "Filter", "view-filter"),
    ("thumbnails", "Thumbnails: small pictures of image files", "view-preview"),
)
SWITCHES: tuple[str, ...] = ("thumbnails",)  # buttons that stay pressed while their view is on
WALKS: tuple[str, ...] = ("back", "forward")  # buttons with a drop-down of the folders they lead to (FG-10)
HIDDEN_TEXT: str = "{count} hidden"
HIDDEN_SHOWN_TEXT: str = "{count} hidden, shown"
HIDDEN_TIP: str = "Hidden files, whose names start with a dot, are not shown. Click, or press Ctrl+Alt+H, to show them."
HIDDEN_SHOWN_TIP: str = "Files whose names start with a dot are shown. Click, or press Ctrl+Alt+H, to hide them."
FILTERED_TEXT: str = "{count} filtered"
FILTERED_TIP: str = "Only names like {mask} are shown. Click to change the filter."


def _compact(bar: QToolBar) -> None:
    bar.setMovable(False)
    bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    bar.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    bar.setContentsMargins(0, 0, 0, 0)
    layout = bar.layout()
    if layout is not None:
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)


class FolderHistory:
    """Where a side has been. A new folder forgets the way forward, as in a web browser."""

    def __init__(self) -> None:
        self.behind: list[str] = []
        self.ahead: list[str] = []

    def clear(self) -> None:
        self.behind.clear()
        self.ahead.clear()

    def left(self, folder: str) -> None:
        self.behind.append(folder)
        self.ahead.clear()

    def back(self, current: str, steps: int = 1) -> str:
        """steps back at once, from the drop-down of Back (FG-10); every folder passed on the way goes ahead, as one step at a time would."""
        for _ in range(max(1, min(steps, len(self.behind)))):
            self.ahead.append(current)
            current = self.behind.pop()
        return current

    def forward(self, current: str, steps: int = 1) -> str:
        for _ in range(max(1, min(steps, len(self.ahead)))):
            self.behind.append(current)
            current = self.ahead.pop()
        return current


class NavigationBar(QToolBar):
    wanted = Signal(str)  # the name of the button pressed
    history_chosen = Signal(str, int)  # "back" or "forward", and how many steps: a folder chosen in the drop-down (FG-10)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        _compact(self)
        self.buttons: dict[str, QAction] = {}
        self.history_of: Callable[[str], list[str]] | None = None  # the folders Back or Forward lead to, the nearest last
        self.walk_menus: dict[str, QMenu] = {}
        for name, text, icon_name in BUTTONS:
            action = QAction(icons.icon(icon_name), text, self)
            action.setToolTip(text)
            action.setCheckable(name in SWITCHES)
            action.triggered.connect(lambda _checked=False, name=name: self.wanted.emit(name))
            if name in WALKS:
                menu = QMenu(self)
                as_drop_down(menu)
                menu.aboutToShow.connect(lambda name=name: self.fill_walk_menu(name))
                action.setMenu(menu)
                self.walk_menus[name] = menu
            self.addAction(action)
            self.buttons[name] = action
            button = self.widgetForAction(action)
            if name in WALKS and isinstance(button, QToolButton):
                button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)  # a click goes one step, the arrow lists the folders

    def fill_walk_menu(self, name: str) -> None:
        """The last folders Back or Forward lead to, the nearest first (FG-10)."""
        menu = self.walk_menus[name]
        menu.clear()
        folders = history_shown(self.history_of(name)) if self.history_of is not None else []
        for steps, folder in enumerate(folders, 1):
            entry = QAction(shown_name(folder).replace("&", "&&"), menu)
            entry.triggered.connect(lambda _checked=False, steps=steps: self.history_chosen.emit(name, steps))
            menu.addAction(entry)

    def show_state(self, history: FolderHistory, has_parent: bool, usable: bool) -> None:
        self.buttons["back"].setEnabled(usable and bool(history.behind))
        self.buttons["forward"].setEnabled(usable and bool(history.ahead))
        self.buttons["parent"].setEnabled(usable and has_parent)
        for name in ("root", "home", "bookmarks", "refresh", "filter", "thumbnails"):
            self.buttons[name].setEnabled(usable)


class SideStatus(QWidget):
    """The status line of a side: the selection totals or what was typed, and the hidden and filtered counts, each a click away from its
    switch (FG-09, FG-13)."""

    hidden_clicked = Signal()
    filtered_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.count_label = plain_label()
        self.filtered_button = QToolButton()
        self.hidden_button = QToolButton()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.count_label, 1)
        for button in (self.filtered_button, self.hidden_button):
            button.setAutoRaise(True)
            button.hide()
            row.addWidget(button)
        self.hidden_button.clicked.connect(self.hidden_clicked)
        self.filtered_button.clicked.connect(self.filtered_clicked)

    def show_counts(self, hidden: int, dotted: int, show_hidden: bool, filtered: int, mask: str) -> None:
        if show_hidden and dotted:
            self.hidden_button.setText(HIDDEN_SHOWN_TEXT.format(count=dotted))
            self.hidden_button.setToolTip(HIDDEN_SHOWN_TIP)
        elif hidden:
            self.hidden_button.setText(HIDDEN_TEXT.format(count=hidden))
            self.hidden_button.setToolTip(HIDDEN_TIP)
        self.hidden_button.setVisible(bool(dotted))
        self.filtered_button.setText(FILTERED_TEXT.format(count=filtered))
        self.filtered_button.setToolTip(FILTERED_TIP.format(mask=mask))
        self.filtered_button.setVisible(bool(mask) and filtered > 0)


def action_bar() -> QToolBar:
    """Icons only, the text comes on hover: every row taken from the lists is a row less of files."""
    bar = QToolBar()
    _compact(bar)
    return bar


def button_row(navigation: QToolBar, actions: QToolBar) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(ROW_GAP)
    row.addWidget(navigation)
    row.addWidget(actions, 1)
    return row
