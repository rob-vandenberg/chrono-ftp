import re
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "icons.py 0.0.7"

# --- Version history ----------------------------------------------------
# v0.0.7: The pin of the queue
# v0.0.4: Initial: the Breeze icons of D-028, drawn in the colour of the text

ICON_FOLDER: Path = Path(__file__).resolve().parent / "resources" / "icons"
ICON_NAMES: tuple[str, ...] = (
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
)
SIZES: tuple[int, ...] = (22, 33, 44)  # normal, and sharp on a scaled screen
_TEXT_COLOUR: re.Pattern[str] = re.compile(r"(\.ColorScheme-Text\s*\{\s*color:\s*)#[0-9a-fA-F]{3,6}")


def _drawn(source: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(source.encode("utf-8"))).render(painter, QRectF(0, 0, size, size))
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


def icon(name: str) -> QIcon:
    """A Breeze icon. Its text colour becomes the colour of the text; the other colours of the icon stay, as in Chrono Mail."""
    source = (ICON_FOLDER / f"{name}.svg").read_text("utf-8")
    tinted = _TEXT_COLOUR.sub(lambda found: found.group(1) + colours.TEXT, source)
    result = QIcon()
    for size in SIZES:
        pixmap = _drawn(tinted, size)
        result.addPixmap(pixmap, QIcon.Mode.Normal)
        result.addPixmap(_greyed(pixmap), QIcon.Mode.Disabled)
    return result
