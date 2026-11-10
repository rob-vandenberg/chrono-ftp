from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "theme.py 0.0.30"

# --- Version history ----------------------------------------------------
# v0.0.30: The menu style is given to each menu, never to the whole program: there it kept the right side grey; a visible border
# v0.0.29: Menus with a border, room around them and roomy items, as in Chrono Mail
# v0.0.3: Initial: the neutral look of the program; nothing comes from the platform (OR-9)


MENU_STYLE: str = (  # for menus only (menus.open_menu): on the whole program it stops the connection colour from showing
    f"QMenu {{ background: {colours.MENU_BACKGROUND}; border: 1px solid {colours.MENU_BORDER}; padding: 4px; }}\n"
    "QMenu::item { padding: 6px 28px 6px 22px; border-radius: 3px; }\n"
    "QMenu::item:selected { background: palette(highlight); color: palette(highlighted-text); }\n"
    f"QMenu::item:disabled {{ color: {colours.DISABLED_TEXT}; }}\n"
    "QMenu::separator { height: 1px; background: palette(mid); margin: 4px 8px; }\n"
)


def neutral_palette() -> QPalette:
    roles: dict[QPalette.ColorRole, str] = {
        QPalette.ColorRole.Window: colours.WINDOW,
        QPalette.ColorRole.WindowText: colours.TEXT,
        QPalette.ColorRole.Base: colours.PANEL,
        QPalette.ColorRole.AlternateBase: colours.PANEL_ALTERNATIVE,
        QPalette.ColorRole.Text: colours.TEXT,
        QPalette.ColorRole.PlaceholderText: colours.MUTED_TEXT,
        QPalette.ColorRole.Button: colours.BUTTON,
        QPalette.ColorRole.ButtonText: colours.TEXT,
        QPalette.ColorRole.ToolTipBase: colours.BUTTON,
        QPalette.ColorRole.ToolTipText: colours.TEXT,
        QPalette.ColorRole.Highlight: colours.SELECTION,
        QPalette.ColorRole.HighlightedText: colours.ON_SELECTION,
        QPalette.ColorRole.Accent: colours.SELECTION,
        QPalette.ColorRole.BrightText: colours.ON_SELECTION,
        QPalette.ColorRole.Link: colours.LINK,
        QPalette.ColorRole.LinkVisited: colours.LINK_VISITED,
        QPalette.ColorRole.Light: colours.BEVEL_LIGHT,
        QPalette.ColorRole.Midlight: colours.BEVEL_MIDLIGHT,
        QPalette.ColorRole.Mid: colours.BEVEL_MID,
        QPalette.ColorRole.Dark: colours.BEVEL_DARK,
        QPalette.ColorRole.Shadow: colours.BEVEL_SHADOW,
    }
    palette = QPalette()
    for role, colour in roles.items():
        palette.setColor(role, QColor(colour))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colours.DISABLED_TEXT))
    return palette


def apply(application: QApplication) -> None:
    application.setStyle("Fusion")
    application.setPalette(neutral_palette())
