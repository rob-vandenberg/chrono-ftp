import re
from pathlib import Path
from typing import override

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "icons.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: An icon draws itself in the colour of the text of the moment, so a change of look leaves no icon behind in the old look;
#          what is drawn is kept, so a toolbar draws from memory and not from the file (FQ-04)
# v0.0.39: Thumbnails (view-preview) (FQ-02); also drawn at the small and large sizes of the toolbars (FQ-03)
# v0.0.38: Server Information (documentinfo) and Session Log (view-list-text) (FP-02, FP-03)
# v0.0.37: Open in the file manager (folder-open) and Install my public key (database-change-key) (FM-09, FM-11)
# v0.0.34: Add a bookmark, open a folder, and the link of synchronized browsing (FH-02, FH-05)
# v0.0.23: Also drawn at 24 and 48 pixels, so the icons of the side bars are sharp
# v0.0.21: The eye of a password field (view-visible, view-hidden)
# v0.0.14: The program icon (application_icon)
# v0.0.13: Run Now and Clear Finished of the queue
# v0.0.7: The pin of the queue
# v0.0.4: Initial: the Breeze icons of D-028, drawn in the colour of the text

ICON_FOLDER: Path = Path(__file__).resolve().parent / "resources" / "icons"
APPLICATION_ICON_FILE: Path = Path(__file__).resolve().parent / "resources" / "chrono-ftp.svg"  # the program icon
ICON_NAMES: tuple[str, ...] = (
    "view-preview",
    "documentinfo",
    "view-list-text",
    "folder-sync",
    "utilities-terminal",
    "view-task",
    "configure",
    "speedometer",
    "tab-new",
    "tab-close",
    "network-server",
    "network-connect",
    "network-disconnect",
    "object-locked",
    "drive-harddisk",
    "go-previous",
    "go-next",
    "go-parent-folder",
    "folder-root",
    "go-home",
    "view-refresh",
    "bookmarks",
    "view-filter",
    "view-list-tree",
    "view-list-details",
    "cloud-upload",
    "cloud-download",
    "document-edit",
    "edit-rename",
    "edit-delete",
    "document-properties",
    "folder-new",
    "document-new",
    "edit-find",
    "edit-copy",
    "edit-cut",
    "media-playback-start",
    "media-playback-pause",
    "process-stop",
    "arrow-up",
    "arrow-down",
    "view-split-left-right",
    "window-pin",
    "media-skip-forward",
    "edit-clear-history",
    "view-visible",
    "view-hidden",
    "bookmark-new",
    "document-open-folder",
    "edit-link",
    "folder-open",
    "database-change-key",
)
SIZES: tuple[int, ...] = (14, 18, 22, 24, 27, 33, 36, 44, 48, 72)  # the sizes in use, also of small and large toolbars, and sharp on a scaled screen
_TEXT_COLOUR: re.Pattern[str] = re.compile(r"(\.ColorScheme-Text\s*\{\s*color:\s*)#[0-9a-fA-F]{3,6}")


def _drawn(source: str, size: QSize) -> QPixmap:
    pixmap = QPixmap(size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(source.encode("utf-8"))).render(painter, QRectF(0, 0, size.width(), size.height()))
    painter.end()
    return pixmap


def _greyed(pixmap: QPixmap) -> QPixmap:
    """The whole shape in the grey of disabled text, also a full-colour icon or the red of Delete (OR-20)."""
    grey = QPixmap(pixmap)
    painter = QPainter(grey)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(grey.rect(), QColor(colours.DISABLED_TEXT))
    painter.end()
    return grey


_SOURCES: dict[str, str] = {}  # the text of each icon file, read once
_DRAWN: dict[tuple[str, int, int, int, str], QPixmap] = {}  # name, width, height, mode and look: drawn once, then from memory


def _source(name: str) -> str:
    """The icon file, read once and then kept: an icon is drawn over and over while the program runs."""
    if name not in _SOURCES:
        _SOURCES[name] = (ICON_FOLDER / f"{name}.svg").read_text("utf-8")
    return _SOURCES[name]


def _pixmap(name: str, size: QSize, mode: QIcon.Mode) -> QPixmap:
    """The icon at this size, in the look in use. Kept per look, so a change of look draws it again and nothing shows the old colour."""
    key = (name, size.width(), size.height(), int(mode.value), colours.in_use())
    kept = _DRAWN.get(key)
    if kept is not None:
        return kept
    ink = colours.ON_SELECTION if mode == QIcon.Mode.Selected else colours.TEXT  # on a selected row the text is the selection's own
    tinted = _TEXT_COLOUR.sub(lambda found: found.group(1) + ink, _source(name))
    drawn = _drawn(tinted, size)
    if mode == QIcon.Mode.Disabled:
        drawn = _greyed(drawn)
    _DRAWN[key] = drawn
    return drawn


class _Breeze(QIconEngine):
    """Draws a Breeze icon in the colour of the text at the moment it is drawn. That is what lets the look change while the program
    runs: every button keeps its icon and the icon follows the look (FQ-04)."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name: str = name

    @override
    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return _pixmap(self.name, size, mode)

    @override
    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        """A scaled screen asks for more pixels in the same space: the icon is drawn at that many and stays sharp."""
        whole = QSize(round(size.width() * scale), round(size.height() * scale))
        drawn = QPixmap(_pixmap(self.name, whole, mode))
        drawn.setDevicePixelRatio(scale)
        return drawn

    @override
    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        """Drawn at the pixels the screen really has: on a screen that doubles everything, half as many would look soft."""
        device = painter.device()
        painter.drawPixmap(rect, self.scaledPixmap(rect.size(), mode, state, device.devicePixelRatio() if device is not None else 1.0))

    @override
    def actualSize(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QSize:
        return size

    @override
    def availableSizes(self, mode: QIcon.Mode = QIcon.Mode.Normal, state: QIcon.State = QIcon.State.Off) -> list[QSize]:
        return [QSize(size, size) for size in SIZES]

    @override
    def iconName(self) -> str:
        return self.name

    @override
    def clone(self) -> QIconEngine:
        return _Breeze(self.name)


def icon(name: str) -> QIcon:
    """A Breeze icon. Its text colour becomes the colour of the text; the other colours of the icon stay, as in Chrono Mail."""
    return QIcon(_Breeze(name))


def application_icon() -> QIcon:
    """The icon of Chrono FTP (a folder with FTP and a clock), from the file shipped with the program: every window, the taskbar and Alt+Tab."""
    return QIcon(str(APPLICATION_ICON_FILE))
