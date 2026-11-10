import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from chrono_ftp.core import colours
from chrono_ftp.ui import icons
from chrono_ftp.ui.sidebar import ICON_SIZE as SIDE_ICON_SIZE, NavigationBar

# --- Version ------------------------------------------------------------
__version__ = "test_ui_icons.py 0.0.23"

# --- Version history ----------------------------------------------------
# v0.0.23: The side bars show their icons at 24 pixels, drawn at that size
# v0.0.14: The program icon loads, with its blue square and orange clock
# v0.0.4: Initial: every icon of D-028 is there, loads and takes the colour of the text

ICON_SIZE: QSize = QSize(22, 22)
LARGE_SIZE: QSize = QSize(44, 44)  # thin icons have no solid pixel at the small size


def colours_of(name: str, mode: QIcon.Mode, size: QSize = ICON_SIZE) -> set[str]:
    image = icons.icon(name).pixmap(size, mode).toImage()
    return {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() == 255}


def test_the_folder_holds_exactly_the_named_icons() -> None:
    # owner rule OR-32, D-028: the icons of the decision, with their licence
    assert sorted(path.stem for path in icons.ICON_FOLDER.glob("*.svg")) == sorted(icons.ICON_NAMES)
    assert (icons.ICON_FOLDER / "LICENSE-breeze.txt").is_file()


@pytest.mark.parametrize("name", icons.ICON_NAMES)
def test_every_icon_draws_something(qapp: QApplication, name: str) -> None:
    image = icons.icon(name).pixmap(ICON_SIZE).toImage()
    assert any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()) for y in range(image.height()))


def test_an_icon_takes_the_colour_of_the_text(qapp: QApplication) -> None:
    assert colours.TEXT in colours_of("tab-new", QIcon.Mode.Normal)


@pytest.mark.parametrize("name", icons.ICON_NAMES)
def test_every_disabled_icon_is_one_grey(qapp: QApplication, name: str) -> None:
    assert colours_of(name, QIcon.Mode.Disabled, LARGE_SIZE) == {colours.DISABLED_TEXT}


def test_an_icon_stays_whole_when_something_is_added_to_it(qapp: QApplication) -> None:
    tab = icons.icon("tab-new")
    tab.addPixmap(tab.pixmap(ICON_SIZE))
    tab.setIsMask(False)
    assert not tab.pixmap(ICON_SIZE).isNull()


def test_a_scaled_screen_gets_a_sharp_icon(qapp: QApplication) -> None:
    pixmap = icons.icon("tab-new").pixmap(ICON_SIZE, 2.0)
    assert pixmap.width() == 2 * ICON_SIZE.width()


def test_the_program_icon_shows_its_blue_square_and_orange_clock(qapp: QApplication) -> None:
    image = icons.application_icon().pixmap(QSize(64, 64)).toImage()
    seen = {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() == 255}
    assert {"#2b6cb0", "#ffffff", "#ed8936"} <= seen


def test_the_side_bars_show_their_icons_at_24_pixels_drawn_at_that_size(qapp: QApplication) -> None:
    assert SIDE_ICON_SIZE == 24
    assert QSize(24, 24) in icons.icon("go-home").availableSizes()
    bar = NavigationBar()
    assert bar.iconSize() == QSize(24, 24)
