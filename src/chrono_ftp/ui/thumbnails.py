"""The thumbnail view of one side (FQ-02): the pictures of the image files in view, fetched one at a time in the background, kept in
memory only. Fetching gives way to the person: it waits while they browse, while a transfer runs and while the connection has other work,
and a fetch that is under way stops as soon as the person asks the connection anything or its file leaves the view."""

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, QTimer
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QWidget

from chrono_ftp.connections.base import KeepGoing, RemoteEntry
from chrono_ftp.core.errors import ConnectionLost, RefusedForNow, TransferInterrupted
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.engine.thumbnails import MEGABYTE, Key, key_of, to_fetch
from chrono_ftp.ui.filelist import THUMBNAIL_PIXELS, FileList

# --- Version ------------------------------------------------------------
__version__ = "thumbnails.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Review: a refusal for now or a read that broke off is tried again later, with a growing wait, and only a lasting answer
#          is kept; a lost connection is told for the tab whose fetch found it; a picture is decoded on a thread of its own, so the
#          tab's own thread only reads its bytes
# v0.0.39: Initial: the thumbnails of one side, fetched in view order, newest view first, giving way to the person; a picture that
#          can't be shown keeps the normal icon (FQ-02, D-070)

SETTLE_MILLISECONDS: int = 400  # a fetch starts this long after the person last scrolled or opened a folder: browsing goes first
PAUSE_MILLISECONDS: int = 1000  # while the connection or a transfer is busy, the next look is this much later
KEPT: int = 2000  # thumbnails kept in memory at most; the oldest go first
FIRST_RETRY_SECONDS: float = 5.0  # a file refused for now, or whose read broke off, is tried again after this, then twice as long each time
LONGEST_RETRY_SECONDS: float = 300.0
FOR_NOW: tuple[type[Exception], ...] = (RefusedForNow, TransferInterrupted)  # answers that may be different later


@dataclass(frozen=True)
class Source:
    """Where the files of a side are read: on which thread, and whether that may happen now."""

    read: Callable[[str, KeepGoing], bytes | None]  # a file of the folder shown, by name; None when told to stop
    worker: Worker  # where the bytes are read: on the right the tab's own thread
    may_run: Callable[[], bool]  # False while the person browses, a transfer runs or the connection has other work
    decoder: Worker  # where a picture is decoded: never the tab's own thread, so a large picture never makes the person wait
    lost: Callable[[Exception], None] = lambda _error: None  # the connection of this source was lost: its own tab goes grey


def picture_from(data: bytes) -> QImage | None:
    """The picture at thumbnail size, read straight at that size where the format allows, so a large picture never fills the memory;
    a broken, empty or unknown picture gives None, and keeps its normal icon (OR-15). Safe on any thread."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        return None
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and (size.width() > THUMBNAIL_PIXELS or size.height() > THUMBNAIL_PIXELS):
        reader.setScaledSize(size.scaled(THUMBNAIL_PIXELS, THUMBNAIL_PIXELS, Qt.AspectRatioMode.KeepAspectRatio))  # a small one stays as it is
    image = reader.read()
    buffer.close()
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        return None
    if image.width() > THUMBNAIL_PIXELS or image.height() > THUMBNAIL_PIXELS:  # a format that can't read at a size
        image = image.scaled(THUMBNAIL_PIXELS, THUMBNAIL_PIXELS, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return image


class ThumbnailSide(QObject):
    """The thumbnails of one file list. The window says where the files are read (source_of) and which login the list shows (place)."""

    def __init__(self, file_list: FileList, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.file_list: FileList = file_list
        self.source_of: Callable[[], Source | None] = lambda: None
        self.place: str = ""  # "" on this computer; the login on a server, so two tabs of one login share their pictures
        self.limit_bytes: int = 0
        self.mask: str = ""
        self.on: bool = False
        self.kept: OrderedDict[Key, QIcon | None] = OrderedDict()  # None: tried, and it can't be shown
        self.later: dict[Key, tuple[float, float]] = {}  # refused for now: when it may be tried again, and the wait after that
        self.fetching: Key | None = None
        self.in_view: frozenset[str] = frozenset()  # the names in view now; the thread of a fetch reads it to know whether to go on
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fetch_next)
        file_list.view_moved.connect(self._view_moved)

    def set_rules(self, limit_megabytes: int, mask: str) -> None:
        self.limit_bytes = limit_megabytes * MEGABYTE
        self.mask = mask
        self._view_moved()

    def switch(self, on: bool) -> None:
        self.on = on
        self.file_list.set_thumbnails(self.picture if on else None)
        if not on:
            self.timer.stop()
            self.in_view = frozenset()

    def picture(self, entry: RemoteEntry) -> QIcon | None:
        return self.kept.get(key_of(self.place, self.file_list.folder, entry))

    def _view_moved(self) -> None:
        if not self.on:
            return
        self.in_view = frozenset(entry.name for entry in self.file_list.entries_in_view())
        self.timer.start(SETTLE_MILLISECONDS)  # browsing goes first: the next fetch waits until the person stops

    def _keep(self, key: Key, icon: QIcon | None) -> None:
        self.kept[key] = icon
        while len(self.kept) > KEPT:
            self.kept.popitem(last=False)

    def fetch_next(self) -> None:
        """The first picture in view that is not here yet, newest view first; one at a time per login."""
        if not self.on or (self.fetching is not None and self.fetching.place == self.place):
            return
        source = self.source_of()
        if source is None:
            return
        if not source.may_run():
            self.timer.start(PAUSE_MILLISECONDS)
            return
        in_view = self.file_list.entries_in_view()
        self.in_view = frozenset(entry.name for entry in in_view)
        now = time.monotonic()
        held = {key for key, (when, _wait) in self.later.items() if when > now}
        waiting = to_fetch(self.place, self.file_list.folder, in_view, self.limit_bytes, self.mask, self.kept.keys() | held)
        if not waiting:
            coming = [when for key, (when, _wait) in self.later.items() if key in to_fetch(self.place, self.file_list.folder, in_view, self.limit_bytes, self.mask, self.kept)]
            if coming:
                self.timer.start(max(int((min(coming) - now) * 1000), 0) + 1)  # a file refused for now, once its wait is over
            return
        key = waiting[0]
        self.fetching = key
        worker = source.worker

        def keep_going() -> bool:
            """On the thread of the fetch: it stops for any other work of the connection, and when its file left the view."""
            return self.on and self.place == key.place and self.file_list.folder == key.folder and key.name in self.in_view and not worker.others_waiting()

        def read() -> bytes | None:
            return source.read(key.name, keep_going)  # only the bytes on this thread: the person's next request waits for nothing more

        def decode(data: bytes) -> None:
            source.decoder.submit(lambda: (picture_from(data),), lambda found: self._arrived(key, found), lambda error: self._failed(key, error, source))

        def read_done(data: bytes | None) -> None:
            if data is None:
                self._arrived(key, None)  # it stopped to give way
            else:
                decode(data)

        worker.submit(read, read_done, lambda error: self._failed(key, error, source))

    def _done_with(self, key: Key) -> None:
        if self.fetching is key:
            self.fetching = None
        if self.on and not self.timer.isActive():
            self.timer.start(0)  # the next one, unless the person's browsing has its own wait running

    def _arrived(self, key: Key, found: tuple[QImage | None] | None) -> None:
        if found is not None:  # None: it stopped to give way, and is tried again later
            image = found[0]
            self.later.pop(key, None)
            self._keep(key, QIcon(QPixmap.fromImage(image)) if image is not None else None)
            if key.place == self.place and key.folder == self.file_list.folder:
                self.file_list.thumbnail_arrived(key.name)
        self._done_with(key)

    def _failed(self, key: Key, error: Exception, source: Source) -> None:
        if isinstance(error, ConnectionLost):
            if self.fetching is key:
                self.fetching = None
            source.lost(error)  # that tab goes grey, as with any request; no picture is noted, so it comes once the tab is back
            return
        if isinstance(error, FOR_NOW):  # busy, or the read broke off: tried again later, each time after a longer wait, never in a loop
            _when, wait = self.later.get(key, (0.0, FIRST_RETRY_SECONDS))
            self.later[key] = (time.monotonic() + wait, min(wait * 2, LONGEST_RETRY_SECONDS))
        else:
            self.later.pop(key, None)
            self._keep(key, None)  # a lasting answer (refused, gone, too large, no picture): its normal icon, not asked for again
        self._done_with(key)
