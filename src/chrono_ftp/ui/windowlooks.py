"""The looks of the main window (block FQ): file colours by mask, the thumbnail view of each side, and the toolbars with their menu."""

from dataclasses import replace
from typing import override

from PySide6.QtCore import QPoint, Qt

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.settings import ToolbarChoices
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.engine.thumbnails import MEGABYTE, reader_for
from chrono_ftp.transfers.queue import State
from chrono_ftp.ui import queueview, sidebar
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.thumbnails import Source, ThumbnailSide
from chrono_ftp.ui.toolbars import Toolbars
from chrono_ftp.ui.windowbase import GENERAL_ICON_SIZE, SETTINGS_NOT_KEPT_TEXT, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowlooks.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Review: a lost connection found by a thumbnail greys the tab whose fetch found it; pictures of the right side are decoded on
#          the thread of the thumbnails, not on the tab's own
# v0.0.39: Initial: block FQ: the file colours on both sides (FQ-01), the thumbnail view of each side, per tab, fetched in the
#          background and giving way to the person (FQ-02), the toolbars' right-click menu and its choices kept (FQ-03)

TOOLBAR_LABELS: dict[str, str] = {
    "top": "Top Bar",
    "local_folders": "Left Side: Folder Buttons",
    "local_files": "Left Side: File Buttons",
    "remote_folders": "Right Side: Folder Buttons",
    "remote_files": "Right Side: File Buttons",
    "queue": "Queue Buttons",
}
THUMBNAILS: str = "thumbnails"  # the button of each side's folder buttons


def place_of(session: Session) -> str:
    """The login a tab shows: two tabs of one login share their thumbnails."""
    connection = session.connection
    return f"{connection.protocol}:{connection.user}@{connection.host}:{connection.port}"


class LooksPart(WindowBase):
    """The looks of the main window (block FQ)."""

    def _build_looks(self) -> None:
        self.thumbnail_worker = None
        self.local_thumbnails = ThumbnailSide(self.local_side.file_list, self)
        self.remote_thumbnails = ThumbnailSide(self.remote_side.file_list, self)
        self.local_thumbnails.source_of = self._local_source
        self.remote_thumbnails.source_of = self._remote_source
        self.local_side.navigation.buttons[THUMBNAILS].toggled.connect(self._local_thumbnails_switched)
        self.remote_side.navigation.buttons[THUMBNAILS].toggled.connect(self._remote_thumbnails_switched)
        self.toolbars = Toolbars(self)
        own = self.actions_by_name
        for name, bar, size, buttons, keep_width in (
            ("top", self.general_bar, GENERAL_ICON_SIZE, own, True),
            ("local_folders", self.local_side.navigation, sidebar.ICON_SIZE, self.local_side.navigation.buttons, False),
            ("local_files", self.local_side.actions_bar, sidebar.ICON_SIZE, own, False),
            ("remote_folders", self.remote_side.navigation, sidebar.ICON_SIZE, self.remote_side.navigation.buttons, False),
            ("remote_files", self.remote_side.actions_bar, sidebar.ICON_SIZE, own, False),
            ("queue", self.queue_panel.bar, queueview.ICON_SIZE, self.queue_panel.actions_by_name, False),
        ):
            on_bar = bar.actions()
            self.toolbars.add(name, TOOLBAR_LABELS[name], bar, size, {button: action for button, action in buttons.items() if action in on_bar}, keep_width)
        self.toolbars.changed.connect(self._toolbars_chosen)
        self.top_row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)  # with every toolbar hidden, the menu is still here
        self.top_row.customContextMenuRequested.connect(self._top_row_menu_wanted)

    @override
    def _apply_looks(self) -> None:
        settings = self.settings
        for side in (self.local_side, self.remote_side):
            side.file_list.set_file_colours(settings.file_colours, side.file_list.files.colours_shown)  # the right side shows them only while live
        for thumbnails in (self.local_thumbnails, self.remote_thumbnails):
            thumbnails.set_rules(settings.thumbnail_megabytes, settings.thumbnail_mask)
        self.toolbars.apply(settings.toolbars)

    # --- the thumbnail view (FQ-02) ----------------------------------------------

    @override
    def _enter_looks(self, session: Session | None) -> None:
        """Each tab shows each side as it left it: with or without thumbnails."""
        if session is not None:
            self.remote_thumbnails.place = place_of(session)
            local, remote = session.local_thumbnails, session.remote_thumbnails
        else:
            local, remote = self.local_thumbnails.on, False
        for button, on in ((self.local_side.navigation.buttons[THUMBNAILS], local), (self.remote_side.navigation.buttons[THUMBNAILS], remote)):
            button.blockSignals(True)
            button.setChecked(on)
            button.blockSignals(False)
        self.local_thumbnails.switch(local)
        self.remote_thumbnails.switch(remote)

    def _top_row_menu_wanted(self, place: QPoint) -> None:
        self.toolbars.menu_wanted("", place, self.top_row)

    def _local_thumbnails_switched(self, on: bool) -> None:
        self._thumbnails_switched(True, on)

    def _remote_thumbnails_switched(self, on: bool) -> None:
        self._thumbnails_switched(False, on)

    def _thumbnails_switched(self, local: bool, on: bool) -> None:
        session = self.current()
        if session is not None:
            if local:
                session.local_thumbnails = on
            else:
                session.remote_thumbnails = on
                self.remote_thumbnails.place = place_of(session)
        (self.local_thumbnails if local else self.remote_thumbnails).switch(on)

    def _in_drag(self) -> bool:
        return self.local_side.file_list.in_drag or self.remote_side.file_list.in_drag

    def _thumbnail_thread(self) -> Worker:
        if self.thumbnail_worker is None:
            self.thumbnail_worker = Worker(self.bridge.deliver)  # a thread of its own: pictures here are read, and all are decoded, on it
        return self.thumbnail_worker

    def _local_source(self) -> Source | None:
        worker = self._thumbnail_thread()
        folder = self.local_side.file_list.folder

        def may_run() -> bool:
            return worker.idle() and not self._in_drag() and not any(job.state == State.RUNNING for job in self.queue.jobs)

        return Source(reader_for(None, folder, self.settings.thumbnail_megabytes * MEGABYTE), worker, may_run, worker)

    def _remote_source(self) -> Source | None:
        """Through the tab's own thread, only while it has nothing else to do and no transfer to its server runs (FQ-02)."""
        session = self.current()
        if session is None or not session.alive or session.remote is None or not self.remote_side.folder:
            return None
        shown: Session = session
        place = (session.connection.host, session.connection.port)

        def may_run() -> bool:
            if not shown.alive or shown.connecting or shown.auto_refreshing or not shown.worker.idle() or self._in_drag():
                return False
            return not any(job.state == State.RUNNING and (job.connection.host, job.connection.port) == place for job in self.queue.jobs)

        reader = reader_for(session.remote, self.remote_side.file_list.folder, self.settings.thumbnail_megabytes * MEGABYTE)
        return Source(reader, session.worker, may_run, self._thumbnail_thread(), lambda error: self._thumbnail_connection_lost(shown, error))

    def _thumbnail_connection_lost(self, session: Session, error: Exception) -> None:
        """The tab whose fetch found its connection gone, whichever tab is shown now."""
        if session in self.sessions and session.alive:
            self.status.showMessage(f"{session.connection.name}: {error}")
            self._mark_lost(session)  # grey, as after any request that found the connection gone (OR-3)

    # --- the toolbars (FQ-03) -------------------------------------------------------

    def _toolbars_chosen(self, choices: object) -> None:
        if not isinstance(choices, ToolbarChoices):
            return
        self.settings = replace(self.settings, toolbars=choices)
        try:
            saved_settings.save(self.settings)
        except OSError:
            self.status.showMessage(SETTINGS_NOT_KEPT_TEXT)
