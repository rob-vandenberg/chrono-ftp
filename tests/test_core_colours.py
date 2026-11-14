import re
from collections.abc import Generator
from dataclasses import fields

import pytest

from chrono_ftp.core import colours
from pathlib import Path

from chrono_ftp.engine.terminal import terminal_arguments
from chrono_ftp.core.connection import Connection

# --- Version ------------------------------------------------------------
__version__ = "test_core_colours.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: The two looks: every colour has a value in both, the light one is light and the dark one dark, an unknown name gives the
#          dark look, and the colours a person chooses are the same on both looks (FQ-04)
# v0.0.9: The optional colour
# v0.0.6: Follows the shared file list and the daily-use work
# v0.0.5: Initial


COLOUR: re.Pattern[str] = re.compile(r"^#[0-9a-f]{6}$")
THE_SAME_ON_BOTH_LOOKS: tuple[str, ...] = (  # these mean the same on a light and on a dark window
    "SELECTION",
    "ON_SELECTION",
    "PATH_FOCUSED",  # the path above the side with the keyboard focus: the selection colour, the same blue on both looks
    "ON_PATH_FOCUSED",
    "FIRST_CONNECTION",
    "TAB_TAKES_FILES",
    "DARK_TEXT",
    "LIGHT_TEXT",
)


@pytest.fixture(autouse=True)
def the_dark_look_again() -> Generator[None]:
    """Every test here leaves the dark look in use, so no test is changed by the one before it."""
    yield
    colours.use(colours.DARK)


def _channels(colour: str) -> tuple[int, int, int]:
    text = colour.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def every_colour() -> tuple[str, ...]:
    """The names of every colour in the file (OR-9): they are upper case and hold a colour."""
    return tuple(name for name in dir(colours) if name.isupper() and isinstance(value := getattr(colours, name), str) and value.startswith("#"))


@pytest.mark.parametrize("look", [colours.DARK_LOOK, colours.LIGHT_LOOK])
def test_both_looks_give_every_colour_of_the_window_a_value(look: colours.Look) -> None:
    """OR-32, D-105: two complete sets of the same names, and OR-9: every colour is written here."""
    for name in (field.name for field in fields(colours.Look)):
        value = getattr(look, name)
        assert isinstance(value, str) and COLOUR.match(value), f"{name} is not a colour: {value!r}"


def test_the_light_look_is_light_and_its_text_is_dark_and_the_dark_look_is_the_other_way_round() -> None:
    """OR-32, D-105: the two looks are a light one and a dark one, and what a new file rule is offered follows the look."""
    for look, background_is_light in ((colours.LIGHT_LOOK, True), (colours.DARK_LOOK, False)):
        for background in (look.window, look.panel, look.panel_alternative, look.button, look.menu_background, look.not_connected):
            assert (colours.brightness(background) > colours.BRIGHTNESS_LIMIT) is background_is_light
        assert (colours.brightness(look.text) < colours.BRIGHTNESS_LIMIT) is background_is_light
        assert (colours.brightness(look.first_file_text) < colours.brightness(look.first_file_background)) is background_is_light


def test_every_colour_that_belongs_to_a_look_changes_with_the_look() -> None:
    """OR-32, D-105: putting a look in use gives every colour of the window that look's value; what means the same on both stays."""
    colours.use(colours.DARK)
    dark = {name: getattr(colours, name) for name in every_colour()}
    colours.use(colours.LIGHT)
    for name, was in dark.items():
        now = getattr(colours, name)
        if name in THE_SAME_ON_BOTH_LOOKS:
            assert now == was, f"{name} should mean the same on both looks"
        else:
            assert now != was, f"{name} keeps its dark value in the light look: use() does not give it the look's value"
    colours.use(colours.DARK)
    assert {name: getattr(colours, name) for name in every_colour()} == dark


def test_the_look_in_use_is_told_and_an_unknown_name_gives_the_dark_look() -> None:
    colours.use(colours.LIGHT)
    assert colours.in_use() == colours.LIGHT
    colours.use("a look from a damaged settings file")  # OR-15: input is never trusted
    assert colours.in_use() == colours.DARK
    assert colours.WINDOW == colours.DARK_LOOK.window


def test_a_colour_a_person_chooses_reads_on_both_looks() -> None:
    """D-007: the text on a colour the person chose is black or white, whichever reads on it, on either look."""
    for look in (colours.DARK, colours.LIGHT):
        colours.use(look)
        for chosen in (colours.FIRST_CONNECTION, "#ffff00", "#102030"):
            picked = colours.readable_text(chosen)
            other = colours.LIGHT_TEXT if picked == colours.DARK_TEXT else colours.DARK_TEXT
            assert contrast(picked, chosen) > contrast(other, chosen)  # of the two, the one that reads better on their colour


@pytest.mark.parametrize("background,text", [("#000000", colours.LIGHT_TEXT), ("#ffffff", colours.DARK_TEXT), ("#00aa00", colours.LIGHT_TEXT), ("#ffff00", colours.DARK_TEXT), ("#aa00aa", colours.LIGHT_TEXT), ("#2e7d4f", colours.LIGHT_TEXT)])
def test_text_is_black_or_white_by_the_brightness_of_the_background(background: str, text: str) -> None:
    assert colours.readable_text(background) == text


def test_a_dimmed_colour_steps_back_towards_the_window_of_the_look() -> None:
    """OR-32, D-105: the tab you are not on steps back, which on a dark window means darker and on a light window lighter."""
    for look in (colours.DARK, colours.LIGHT):
        colours.use(look)
        light_window = colours.brightness(colours.WINDOW) >= colours.BRIGHTNESS_LIMIT
        for colour in ("#aa00aa", "#2e7d4f", "#bcc7d0", "#ffffff", "#000000", colours.WINDOW):
            for part, before, window in zip(_channels(colours.dimmed(colour)), _channels(colour), _channels(colours.WINDOW)):
                if light_window:
                    assert abs(part - window) <= abs(before - window)  # every part steps towards the window, or is already there
                else:
                    assert part <= before  # a dark window: stepping back is stepping down, as it always was


def test_the_terminal_opens_on_the_right_port_with_the_key_in_the_folder() -> None:
    connection = Connection(name="T", host="host.example.com", user="anna", port=2022, key_file="/keys/id")
    arguments = terminal_arguments(connection, "/srv/my site")
    assert arguments[:3] == ["konsole", "-e", "ssh"]  # no colour: the normal colours of the terminal
    assert arguments[arguments.index("ssh"):].count("-p") == 1 and "2022" in arguments
    assert arguments[arguments.index("-i") + 1] == "/keys/id"
    assert "anna@host.example.com" in arguments
    assert "'/srv/my site'" in arguments[-1]


def test_the_terminal_gets_the_colour_of_its_connection_with_readable_text(tmp_path: Path) -> None:
    connection = Connection(name="T", host="host.example.com", colour="#102030")
    arguments = terminal_arguments(connection, "/srv")
    assert arguments[1:3] == ["-p", "ColorScheme=chrono-ftp-102030"]
    scheme = (tmp_path / "data" / "konsole" / "chrono-ftp-102030.colorscheme").read_text(encoding="utf-8")
    assert "[Background]\nColor=16,32,48" in scheme
    assert "[Foreground]\nColor=255,255,255" in scheme


def contrast(first: str, second: str) -> float:
    """How far apart two colours are to the eye, by the usual measure: 1 is the same colour, 21 is black against white."""

    def light(colour: str) -> float:
        parts = [int(colour.lstrip("#")[place * 2:place * 2 + 2], 16) / 255 for place in range(3)]
        straight = [part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4 for part in parts]
        return 0.2126 * straight[0] + 0.7152 * straight[1] + 0.0722 * straight[2]

    one, other = light(first), light(second)
    return (max(one, other) + 0.05) / (min(one, other) + 0.05)


def tab_greys(look: colours.Look) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """How light the tab of a live connection without a colour of its own is, and that of a dropped one: shown and not shown."""
    colours.use(colours.LIGHT if look is colours.LIGHT_LOOK else colours.DARK)  # dimming steps back towards this look's window
    live = tuple(colours.brightness(shade) for shade in (look.no_colour_tab, colours.dimmed(look.no_colour_tab)))
    dropped = tuple(colours.brightness(shade) for shade in (look.not_connected_tab, colours.dimmed(look.not_connected_tab)))
    return live, dropped


def test_a_live_tab_without_a_colour_never_looks_like_a_tab_whose_connection_dropped() -> None:
    """OR-3: a colour always means a live connection, so no shade of a live tab without a colour of its own may be a shade of a
    dropped one, in either look, whether or not the tab is the one you are on."""
    for name, look in (("dark", colours.DARK_LOOK), ("light", colours.LIGHT_LOOK)):
        live, dropped = tab_greys(look)
        assert max(live) < min(dropped) or min(live) > max(dropped), f"the {name} look mixes the two greys"


def test_greyed_text_is_its_own_colour_on_both_looks() -> None:
    """OR-7, OR-20: what cannot be done is greyed, never hidden, so the grey is neither the ordinary text nor any background it is
    drawn on."""
    for look in (colours.DARK_LOOK, colours.LIGHT_LOOK):
        assert look.disabled_text not in (look.text, look.muted_text)
        for background in (look.window, look.button, look.panel, look.panel_alternative, look.menu_background, look.not_connected):
            assert look.disabled_text != background
