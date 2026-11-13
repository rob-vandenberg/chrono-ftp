"""THE COLOUR FILE (OR-9). Every colour of Chrono FTP is written here and nowhere else; nothing comes from the platform. Colours are
written as "#rrggbb". The colours a person chooses for a connection are theirs: they are kept with the connection, not in this file."""

# --- Version ------------------------------------------------------------
__version__ = "colours.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: The border round the tab that will take the files being dragged over it (FI-01)
# v0.0.39: The colours a new rule of file colours offers first (FQ-01); the colours of a rule are the person's own, kept in the settings
# v0.0.34: The path of the side with the keyboard focus is highlighted (FH-01)
# v0.0.33: Not connected, the right side is the darker row colour of the left side; the dropped tab keeps its light grey
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

NOT_CONNECTED: str = PANEL  # the right side without a connection: plain dark grey, the darker row colour of the left side (OR-3, D-007)
ON_NOT_CONNECTED: str = TEXT
NOT_CONNECTED_TAB: str = "#8a8f94"  # the plain grey of a tab whose connection dropped (OR-3)

PATH_FOCUSED: str = SELECTION  # the path above the side that has the keyboard focus (FH-01)
ON_PATH_FOCUSED: str = ON_SELECTION
PATH_UNFOCUSED: str = BUTTON  # the path above the other side
ON_PATH_UNFOCUSED: str = MUTED_TEXT

FIRST_CONNECTION: str = "#2e7d4f"  # what the colour button offers first
FIRST_FILE_TEXT: str = "#e8b04a"  # what a new rule of file colours offers first for the text (FQ-01)
FIRST_FILE_BACKGROUND: str = "#3b3320"  # and for the background of the row
NO_COLOUR_TAB: str = "#3a4146"  # the tab of a live connection without a colour of its own
TAB_TAKES_FILES: str = "#ffd24a"  # the border round the tab that will take the files being dragged over it; it stands out on every connection colour (FI-01)
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
