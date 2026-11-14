"""THE COLOUR FILE (OR-9). Every colour of Chrono FTP is written here and nowhere else; nothing comes from the platform. Colours are
written as "#rrggbb". The colours a person chooses for a connection are theirs: they are kept with the connection, not in this file.

Both looks live here, dark and light (FQ-04). use() puts one of them in use and every name below then holds that look's value, so the
rest of the program reads one colour and never learns that looks exist. Whether the look follows the desktop is a setting, not a look."""

from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "colours.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: Two looks, dark and light, and use() to put one in use (FQ-04)
# v0.0.45: The border round the tab that will take the files being dragged over it (FI-01)
# v0.0.39: The colours a new rule of file colours offers first (FQ-01); the colours of a rule are the person's own, kept in the settings
# v0.0.34: The path of the side with the keyboard focus is highlighted (FH-01)
# v0.0.33: Not connected, the right side is the darker row colour of the left side; the dropped tab keeps its light grey
# v0.0.30: The border of the menus, visible against the window
# v0.0.29: The background of the menus
# v0.0.9: The tab of a connection without a colour
# v0.0.5: The colour a new connection starts with
# v0.0.3: Initial: the neutral colours of the window and the grey of no connection

DARK: str = "dark"  # the two looks (FQ-04); a look is a complete set of the colours below
LIGHT: str = "light"
LOOKS: tuple[str, ...] = (DARK, LIGHT)
FOLLOW_DESKTOP: str = "desktop"  # the look comes from the desktop; a desktop that says nothing means dark
LOOK_CHOICES: tuple[str, ...] = (FOLLOW_DESKTOP, DARK, LIGHT)  # what the Look setting offers


@dataclass(frozen=True)
class Look:
    """One complete look. Every field is a colour of the window; the person's own colours are not here."""

    window: str
    panel: str  # the neutral lists of the local side
    panel_alternative: str
    button: str
    menu_background: str
    menu_border: str  # the line around a menu: clearly apart from the window and the menu
    text: str
    muted_text: str
    selection: str
    on_selection: str
    disabled_text: str  # greyed buttons and texts (OR-7, OR-20)
    link: str
    link_visited: str
    bevel_light: str  # the shades Qt's style draws edges with
    bevel_midlight: str
    bevel_mid: str
    bevel_dark: str
    bevel_shadow: str
    not_connected: str  # the right side without a connection: plain grey (OR-3, D-007)
    not_connected_tab: str  # the plain grey of a tab whose connection dropped (OR-3)
    first_file_text: str  # what a new rule of file colours offers first for the text (FQ-01)
    first_file_background: str  # and for the background of the row
    no_colour_tab: str  # the tab of a live connection without a colour of its own


DARK_LOOK: Look = Look(  # the look Chrono FTP always had
    window="#23282b",
    panel="#151c1c",
    panel_alternative="#1b2222",
    button="#272c30",
    menu_background="#272c30",
    menu_border="#4a5157",
    text="#e6e8ea",
    muted_text="#8f979c",
    selection="#2a6fc4",
    on_selection="#ffffff",
    disabled_text="#6b7378",
    link="#6aa5e8",
    link_visited="#b08ae0",
    bevel_light="#3d4448",
    bevel_midlight="#32383c",
    bevel_mid="#1c2022",
    bevel_dark="#15181a",
    bevel_shadow="#0c0e0f",
    not_connected="#151c1c",  # the darker row colour of the left side
    not_connected_tab="#8a8f94",
    first_file_text="#e8b04a",
    first_file_background="#3b3320",
    no_colour_tab="#3a4146",
)
LIGHT_LOOK: Look = Look(  # the same window on a light desktop: the same meanings, lighter values
    window="#eceff1",
    panel="#fbfcfd",
    panel_alternative="#f1f4f6",
    button="#e2e6ea",
    menu_background="#f8f9fb",
    menu_border="#b4bcc2",
    text="#16191c",
    muted_text="#5a6369",
    selection="#2a6fc4",  # the same blue, which carries white text on either look
    on_selection="#ffffff",
    disabled_text="#787f87",  # greyed text must be as plain here as it is in the dark look (OR-7, OR-20)
    link="#15539e",
    link_visited="#6b3a9c",
    bevel_light="#ffffff",
    bevel_midlight="#f4f6f8",
    bevel_mid="#c2c9ce",
    bevel_dark="#98a0a6",
    bevel_shadow="#6e767c",
    not_connected="#dde2e5",  # plainly grey against the white lists of the left side
    not_connected_tab="#5a6268",  # clearly darker than a live tab without a colour, which is light here
    first_file_text="#8a5000",
    first_file_background="#fcd98a",
    no_colour_tab="#bcc7d0",  # a tab must look like a tab against the window, as it does in the dark look
)
LOOKS_BY_NAME: dict[str, Look] = {DARK: DARK_LOOK, LIGHT: LIGHT_LOOK}

WINDOW: str = DARK_LOOK.window  # these hold the look in use; use() gives them the values of the other look
PANEL: str = DARK_LOOK.panel
PANEL_ALTERNATIVE: str = DARK_LOOK.panel_alternative
BUTTON: str = DARK_LOOK.button
MENU_BACKGROUND: str = DARK_LOOK.menu_background
MENU_BORDER: str = DARK_LOOK.menu_border
TEXT: str = DARK_LOOK.text
MUTED_TEXT: str = DARK_LOOK.muted_text
SELECTION: str = DARK_LOOK.selection
ON_SELECTION: str = DARK_LOOK.on_selection
DISABLED_TEXT: str = DARK_LOOK.disabled_text
LINK: str = DARK_LOOK.link
LINK_VISITED: str = DARK_LOOK.link_visited
BEVEL_LIGHT: str = DARK_LOOK.bevel_light
BEVEL_MIDLIGHT: str = DARK_LOOK.bevel_midlight
BEVEL_MID: str = DARK_LOOK.bevel_mid
BEVEL_DARK: str = DARK_LOOK.bevel_dark
BEVEL_SHADOW: str = DARK_LOOK.bevel_shadow

NOT_CONNECTED: str = DARK_LOOK.not_connected
ON_NOT_CONNECTED: str = DARK_LOOK.text
NOT_CONNECTED_TAB: str = DARK_LOOK.not_connected_tab

PATH_FOCUSED: str = DARK_LOOK.selection  # the path above the side that has the keyboard focus (FH-01)
ON_PATH_FOCUSED: str = DARK_LOOK.on_selection
PATH_UNFOCUSED: str = DARK_LOOK.button  # the path above the other side
ON_PATH_UNFOCUSED: str = DARK_LOOK.muted_text

FIRST_FILE_TEXT: str = DARK_LOOK.first_file_text
FIRST_FILE_BACKGROUND: str = DARK_LOOK.first_file_background
NO_COLOUR_TAB: str = DARK_LOOK.no_colour_tab

FIRST_CONNECTION: str = "#2e7d4f"  # what the colour button offers first; a colour of the person's own, the same on both looks
TAB_TAKES_FILES: str = "#ffd24a"  # the border round the tab that will take the files being dragged over it; it stands out on every connection colour (FI-01)
DARK_TEXT: str = "#101214"  # the text on a light connection colour
LIGHT_TEXT: str = "#ffffff"  # the text on a dark connection colour
DIM_FACTOR: float = 0.72  # an inactive tab is slightly dimmed
BRIGHTNESS_LIMIT: float = 0.5


_in_use: str = DARK


def in_use() -> str:
    """The look the colours above hold at this moment."""
    return _in_use


def use(look: str) -> None:
    """Puts a look in use: every colour above takes its value from that look. An unknown name gives the dark look (OR-15)."""
    global _in_use, WINDOW, PANEL, PANEL_ALTERNATIVE, BUTTON, MENU_BACKGROUND, MENU_BORDER, TEXT, MUTED_TEXT, SELECTION, ON_SELECTION
    global DISABLED_TEXT, LINK, LINK_VISITED, BEVEL_LIGHT, BEVEL_MIDLIGHT, BEVEL_MID, BEVEL_DARK, BEVEL_SHADOW
    global NOT_CONNECTED, ON_NOT_CONNECTED, NOT_CONNECTED_TAB, PATH_FOCUSED, ON_PATH_FOCUSED, PATH_UNFOCUSED, ON_PATH_UNFOCUSED
    global FIRST_FILE_TEXT, FIRST_FILE_BACKGROUND, NO_COLOUR_TAB
    chosen = LOOKS_BY_NAME.get(look, DARK_LOOK)
    _in_use = look if look in LOOKS else DARK
    WINDOW = chosen.window
    PANEL = chosen.panel
    PANEL_ALTERNATIVE = chosen.panel_alternative
    BUTTON = chosen.button
    MENU_BACKGROUND = chosen.menu_background
    MENU_BORDER = chosen.menu_border
    TEXT = chosen.text
    MUTED_TEXT = chosen.muted_text
    SELECTION = chosen.selection
    ON_SELECTION = chosen.on_selection
    DISABLED_TEXT = chosen.disabled_text
    LINK = chosen.link
    LINK_VISITED = chosen.link_visited
    BEVEL_LIGHT = chosen.bevel_light
    BEVEL_MIDLIGHT = chosen.bevel_midlight
    BEVEL_MID = chosen.bevel_mid
    BEVEL_DARK = chosen.bevel_dark
    BEVEL_SHADOW = chosen.bevel_shadow
    NOT_CONNECTED = chosen.not_connected
    ON_NOT_CONNECTED = chosen.text
    NOT_CONNECTED_TAB = chosen.not_connected_tab
    PATH_FOCUSED = chosen.selection
    ON_PATH_FOCUSED = chosen.on_selection
    PATH_UNFOCUSED = chosen.button
    ON_PATH_UNFOCUSED = chosen.muted_text
    FIRST_FILE_TEXT = chosen.first_file_text
    FIRST_FILE_BACKGROUND = chosen.first_file_background
    NO_COLOUR_TAB = chosen.no_colour_tab


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
    """A tab that is not the one you are on, stepped back towards the window: darker on a dark window, lighter on a light one. On a
    light window the old way, which only darkened, made the tabs you are not on louder than the tab you are on (FQ-04)."""
    red, green, blue = _channels(colour)
    if brightness(WINDOW) >= BRIGHTNESS_LIMIT:  # a light window: the step is towards the window itself, whatever colour the tab has
        window = _channels(WINDOW)
        return f"#{''.join(f'{int(part * DIM_FACTOR + towards * (1 - DIM_FACTOR)):02x}' for part, towards in zip((red, green, blue), window))}"
    return f"#{int(red * DIM_FACTOR):02x}{int(green * DIM_FACTOR):02x}{int(blue * DIM_FACTOR):02x}"
