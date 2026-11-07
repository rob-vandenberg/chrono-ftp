from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QToolBar, QWidget

from chrono_ftp.ui import icons

# --- Version ------------------------------------------------------------
__version__ = "sidebar.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial: the buttons every side has for walking through folders, and where it has been

BUTTONS: tuple[tuple[str, str, str], ...] = (
    ("back", "Back", "go-previous"),
    ("forward", "Forward", "go-next"),
    ("parent", "Parent folder", "go-parent-folder"),
    ("root", "Root folder", "folder-root"),
    ("home", "Home folder", "go-home"),
    ("refresh", "Refresh", "view-refresh"),
    ("filter", "Filter", "view-filter"),
)


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

    def back(self, current: str) -> str:
        self.ahead.append(current)
        return self.behind.pop()

    def forward(self, current: str) -> str:
        self.behind.append(current)
        return self.ahead.pop()


class NavigationBar(QToolBar):
    wanted = Signal(str)  # the name of the button pressed

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMovable(False)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.buttons: dict[str, QAction] = {}
        for name, text, icon_name in BUTTONS:
            action = QAction(icons.icon(icon_name), text, self)
            action.setToolTip(text)
            action.triggered.connect(lambda _checked=False, name=name: self.wanted.emit(name))
            self.addAction(action)
            self.buttons[name] = action

    def show_state(self, history: FolderHistory, has_parent: bool, usable: bool) -> None:
        self.buttons["back"].setEnabled(usable and bool(history.behind))
        self.buttons["forward"].setEnabled(usable and bool(history.ahead))
        self.buttons["parent"].setEnabled(usable and has_parent)
        for name in ("root", "home", "refresh", "filter"):
            self.buttons[name].setEnabled(usable)


def action_bar() -> QToolBar:
    bar = QToolBar()
    bar.setMovable(False)
    bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
    return bar
