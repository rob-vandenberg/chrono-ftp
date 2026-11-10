"""THE COLOUR FILE (OR-9). Every colour of Chrono FTP is written here and nowhere else; nothing comes from the platform. Colours are
written as "#rrggbb". The colours a person chooses for a connection are theirs: they are kept with the connection, not in this file."""

# --- Version ------------------------------------------------------------
__version__ = "colours.py 0.0.30"

# --- Version history ----------------------------------------------------
# v0.0.30: The border of the menus, visible against the window
# v0.0.29: The background of the menus
# v0.0.9: The tab of a connection without a colour
# v0.0.5: The colour a new connection starts with
# v0.0.3: Initial: the neutral colours of the window and the grey of no connection

WINDOW: str = "#23282b"
PANEL: str = "#151c1c"  # the neutral lists of the local side
PANEL_ALTERNATIVE: str = "#1b2222"
BUTTON: str = "#272c30"
MENU_BACKGROUND: str = "#272c30"  # the menus, as Chrono Mail's
MENU_BORDER: str = "#4a5157"  # the line around a menu: clearly lighter than the window and the menu
TEXT: str = "#e6e8ea"
MUTED_TEXT: str = "#8f979c"
SELECTION: str = "#2a6fc4"
ON_SELECTION: str = "#ffffff"
DISABLED_TEXT: str = "#6b7378"  # greyed buttons and texts (OR-7, OR-20)
LINK: str = "#6aa5e8"
LINK_VISITED: str = "#b08ae0"
BEVEL_LIGHT: str = "#3d4448"  # the shades Qt's style draws edges with
BEVEL_MIDLIGHT: str = "#32383c"
BEVEL_MID: str = "#1c2022"
BEVEL_DARK: str = "#15181a"
BEVEL_SHADOW: str = "#0c0e0f"

NOT_CONNECTED: str = "#8a8f94"  # the plain grey of the right side: a colour always means a live connection (OR-3)
ON_NOT_CONNECTED: str = "#101214"

FIRST_CONNECTION: str = "#2e7d4f"  # what the colour button offers first
NO_COLOUR_TAB: str = "#3a4146"  # the tab of a live connection without a colour of its own
DARK_TEXT: str = "#101214"  # the text on a light connection colour
LIGHT_TEXT: str = "#ffffff"  # the text on a dark connection colour
DIM_FACTOR: float = 0.72  # an inactive tab is slightly dimmed
BRIGHTNESS_LIMIT: float = 0.5


def _channels(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def brightness(colour: str) -> float:
    """How light a colour looks to the eye, from 0 to 1."""
    red, green, blue = _channels(colour)
    return (0.299 * red + 0.587 * green + 0.114 * blue) / 255


def readable_text(colour: str) -> str:
    """Black or white, whichever reads on this background (D-007)."""
    return DARK_TEXT if brightness(colour) >= BRIGHTNESS_LIMIT else LIGHT_TEXT


def dimmed(colour: str) -> str:
    red, green, blue = _channels(colour)
    return f"#{int(red * DIM_FACTOR):02x}{int(green * DIM_FACTOR):02x}{int(blue * DIM_FACTOR):02x}"
