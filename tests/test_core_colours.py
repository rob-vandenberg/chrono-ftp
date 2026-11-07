import pytest

from chrono_ftp.core import colours
from chrono_ftp.engine.terminal import terminal_arguments
from chrono_ftp.core.connection import Connection

# --- Version ------------------------------------------------------------
__version__ = "test_core_colours.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial


@pytest.mark.parametrize("background,text", [("#000000", colours.LIGHT_TEXT), ("#ffffff", colours.DARK_TEXT), ("#00aa00", colours.LIGHT_TEXT), ("#ffff00", colours.DARK_TEXT), ("#aa00aa", colours.LIGHT_TEXT), ("#2e7d4f", colours.LIGHT_TEXT)])
def test_text_is_black_or_white_by_the_brightness_of_the_background(background: str, text: str) -> None:
    assert colours.readable_text(background) == text


def test_a_dimmed_colour_is_darker() -> None:
    assert colours.brightness(colours.dimmed("#aa00aa")) < colours.brightness("#aa00aa")


def test_the_terminal_opens_on_the_right_port_with_the_key_in_the_folder() -> None:
    connection = Connection(name="T", host="host.example.com", user="anna", port=2022, key_file="/keys/id")
    arguments = terminal_arguments(connection, "/srv/my site")
    assert arguments[:3] == ["konsole", "-e", "ssh"]
    assert arguments[arguments.index("-p") + 1] == "2022"
    assert arguments[arguments.index("-i") + 1] == "/keys/id"
    assert "anna@host.example.com" in arguments
    assert "'/srv/my site'" in arguments[-1]
