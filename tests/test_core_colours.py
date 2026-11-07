import pytest

from chrono_ftp.core import colours
from pathlib import Path

from chrono_ftp.engine.terminal import terminal_arguments
from chrono_ftp.core.connection import Connection

# --- Version ------------------------------------------------------------
__version__ = "test_core_colours.py 0.0.9"

# --- Version history ----------------------------------------------------
# v0.0.9: The optional colour
# v0.0.6: Follows the shared file list and the daily-use work
# v0.0.5: Initial


@pytest.mark.parametrize("background,text", [("#000000", colours.LIGHT_TEXT), ("#ffffff", colours.DARK_TEXT), ("#00aa00", colours.LIGHT_TEXT), ("#ffff00", colours.DARK_TEXT), ("#aa00aa", colours.LIGHT_TEXT), ("#2e7d4f", colours.LIGHT_TEXT)])
def test_text_is_black_or_white_by_the_brightness_of_the_background(background: str, text: str) -> None:
    assert colours.readable_text(background) == text


def test_a_dimmed_colour_is_darker() -> None:
    assert colours.brightness(colours.dimmed("#aa00aa")) < colours.brightness("#aa00aa")


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
