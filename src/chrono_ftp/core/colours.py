"""THE COLOUR FILE (OR-9). Every colour of Chrono FTP is written here and nowhere else; nothing comes from the platform. Colours are
written as "#rrggbb". The colours a person chooses for a connection are theirs: they are kept with the connection, not in this file."""

# --- Version ------------------------------------------------------------
__version__ = "colours.py 0.0.3"

# --- Version history ----------------------------------------------------
# v0.0.3: Initial: the neutral colours of the window and the grey of no connection

WINDOW: str = "#23282b"
PANEL: str = "#151c1c"  # the neutral lists of the local side
PANEL_ALTERNATIVE: str = "#1b2222"
BUTTON: str = "#272c30"
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
