"""The toolbars and their right-click menu (FQ-03): each toolbar can be hidden, the icons have three sizes, a chosen button can show its
text under its icon, and "Restore the standard layout" brings back the look of a new installation. A hidden toolbar's keys keep working:
they belong to the lists they act on, never to the toolbar."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QToolBar, QToolButton, QWidget

from chrono_ftp.config.settings import ICON_SIZES, LARGE, MEDIUM, SMALL, ToolbarChoices
from chrono_ftp.ui.menus import open_menu

# --- Version ------------------------------------------------------------
__version__ = "toolbars.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Initial: show or hide each toolbar, three icon sizes, text under chosen buttons, restore the standard layout (FQ-03, D-071)

SIZE_FACTORS: dict[str, float] = {SMALL: 0.75, MEDIUM: 1.0, LARGE: 1.5}  # medium is the size each toolbar had before block FQ
SIZE_TEXTS: dict[str, str] = {SMALL: "Small Icons", MEDIUM: "Medium Icons", LARGE: "Large Icons"}
TEXT_UNDER_TEXT: str = "Text under this button"
TEXT_UNDER_TIP: str = "Show the name of this button under its icon"
NO_BUTTON_TIP: str = "Right-click a button to choose this for that button"
RESTORE_TEXT: str = "Restore the standard layout"
RESTORE_TIP: str = "Every toolbar shown, medium icons, and no text under the buttons that have none at first"
SHOW_TIP: str = "Show or hide this toolbar; its keys keep working while it is hidden"
SEPARATOR: str = "/"


@dataclass
class Toolbar:
    name: str
    label: str  # as the menu shows it
    bar: QToolBar
    size: int  # the icon size at medium, in pixels
    buttons: dict[str, QAction]
    standard_styles: dict[str, Qt.ToolButtonStyle]  # each button's look before any choice
    keep_width: bool  # the bar keeps room for all its buttons, so none of them hides behind an arrow


def scaled(size: int, icon_size: str) -> int:
    return round(size * SIZE_FACTORS.get(icon_size, 1.0))


class Toolbars(QObject):
    """Every toolbar of the main window, the choices that hold for them, and the right-click menu that changes those choices."""

    changed = Signal(object)  # the new ToolbarChoices, to be kept

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.bars: dict[str, Toolbar] = {}
        self.choices: ToolbarChoices = ToolbarChoices()

    def add(self, name: str, label: str, bar: QToolBar, size: int, buttons: dict[str, QAction], keep_width: bool = False) -> None:
        styles: dict[str, Qt.ToolButtonStyle] = {}
        for button_name, action in buttons.items():
            widget = bar.widgetForAction(action)
            styles[button_name] = widget.toolButtonStyle() if isinstance(widget, QToolButton) else bar.toolButtonStyle()
        self.bars[name] = Toolbar(name, label, bar, size, dict(buttons), styles, keep_width)
        bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def wanted(place: QPoint) -> None:
            self.menu_wanted(name, place)

        bar.customContextMenuRequested.connect(wanted)

    def apply(self, choices: ToolbarChoices) -> None:
        self.choices = choices
        for toolbar in self.bars.values():
            bar = toolbar.bar
            pixels = scaled(toolbar.size, choices.icon_size)
            bar.setIconSize(QSize(pixels, pixels))
            for button_name, action in toolbar.buttons.items():
                widget = bar.widgetForAction(action)
                if isinstance(widget, QToolButton):
                    under = f"{toolbar.name}{SEPARATOR}{button_name}" in choices.text_under
                    widget.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon if under else toolbar.standard_styles[button_name])
            if toolbar.keep_width:
                bar.setMinimumWidth(0)
                layout = bar.layout()
                if layout is not None:
                    layout.invalidate()
                bar.setMinimumWidth(bar.sizeHint().width())
            bar.setVisible(toolbar.name not in choices.hidden)

    def _choose(self, choices: ToolbarChoices) -> None:
        self.apply(choices)
        self.changed.emit(choices)

    def button_at(self, name: str, place: QPoint) -> str:
        """The name of the button under this place of the toolbar; "" between buttons or off any toolbar."""
        toolbar = self.bars.get(name)
        if toolbar is None:
            return ""
        action = toolbar.bar.actionAt(place)
        return next((button for button, known in toolbar.buttons.items() if known is action), "") if action is not None else ""

    def menu_for(self, name: str, place: QPoint, parent: QWidget | None = None) -> QMenu:
        """The menu of a right-click at this place of this toolbar ("" for a place that is no toolbar, such as the empty part of the top row)."""
        menu = QMenu(parent)
        menu.setToolTipsVisible(True)
        choices = self.choices
        for toolbar in self.bars.values():
            shown = menu.addAction(toolbar.label)
            shown.setCheckable(True)
            shown.setChecked(toolbar.name not in choices.hidden)
            shown.setToolTip(SHOW_TIP)
            shown.toggled.connect(self._shower(toolbar.name))
        menu.addSeparator()
        sizes = QActionGroup(menu)
        for icon_size in ICON_SIZES:
            size_action = menu.addAction(SIZE_TEXTS[icon_size])
            size_action.setCheckable(True)
            size_action.setChecked(choices.icon_size == icon_size)
            sizes.addAction(size_action)
            size_action.triggered.connect(lambda _checked=False, icon_size=icon_size: self._choose(replace(self.choices, icon_size=icon_size)))
        menu.addSeparator()
        button = self.button_at(name, place)
        under = menu.addAction(TEXT_UNDER_TEXT)
        under.setCheckable(True)
        full_name = f"{name}{SEPARATOR}{button}"
        under.setChecked(bool(button) and full_name in choices.text_under)
        under.setEnabled(bool(button))
        under.setToolTip(TEXT_UNDER_TIP if button else NO_BUTTON_TIP)  # greyed with its reason (OR-20)
        def text_under(on: bool) -> None:
            self._text_under(full_name, on)

        under.toggled.connect(text_under)
        menu.addSeparator()
        restore = menu.addAction(RESTORE_TEXT)
        restore.setToolTip(RESTORE_TIP)
        restore.triggered.connect(lambda _checked=False: self._choose(ToolbarChoices()))
        return menu

    def _shower(self, name: str) -> Callable[[bool], None]:
        def show(on: bool) -> None:
            self._show(name, on)

        return show

    def _show(self, name: str, on: bool) -> None:
        hidden = set(self.choices.hidden)
        if on:
            hidden.discard(name)
        else:
            hidden.add(name)
        self._choose(replace(self.choices, hidden=tuple(sorted(hidden))))

    def _text_under(self, full_name: str, on: bool) -> None:
        chosen = set(self.choices.text_under)
        if on:
            chosen.add(full_name)
        else:
            chosen.discard(full_name)
        self._choose(replace(self.choices, text_under=tuple(sorted(chosen))))

    def menu_wanted(self, name: str, place: QPoint, owner: QWidget | None = None) -> None:
        """A right-click on a toolbar, or on owner, a place that is no toolbar: place is in the coordinates of that widget."""
        toolbar = self.bars.get(name)
        widget = toolbar.bar if toolbar is not None else owner
        if widget is None:
            return
        open_menu(self.menu_for(name, place, widget), widget.mapToGlobal(place))

