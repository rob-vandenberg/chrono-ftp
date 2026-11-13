"""Hashes and sizes in the main window (block FE): the folder sizes of the Size column (Shift+Space), counted in the background and
stoppable, and what the Size and Checksum tabs of Properties ask, on either side. All of it runs on threads of its own, so the person's
own actions never wait for it (D-019); on the server it uses the tab's connection, never a login of its own."""

import threading
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QToolButton

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.bookmarks import LOCAL, REMOTE
from chrono_ftp.connections.base import CHECKSUM_ALGORITHMS, KeepGoing, RemoteConnection, RemoteEntry, joined, shown_name
from chrono_ftp.core.connection import FTP
from chrono_ftp.engine import checksums, sizes
from chrono_ftp.engine.checksums import Checksum
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.engine.sizes import Count
from chrono_ftp.ui.propertiesdialog import Calculator, Stop
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import SETTINGS_NOT_KEPT_TEXT, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowsizes.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Initial: block FE: Shift+Space counts the selected folders of the side with the focus into the Size column, in the background
#          and stoppable (FE-03); the calculations of the Size and Checksum tabs of Properties, on both sides (FE-02); review round 1:
#          each calculation on a thread of its own, stopped with its window or the main window; a count belongs to its tab and its
#          folder: closing that tab or showing another stops it silently; while a name is being typed, Shift+Space types a space

FOLDER_SIZES_TEXT: str = "Calculate Folder Sizes"
FOLDER_SIZES_KEY: str = "Shift+Space"
NO_FOLDER_SELECTED_TEXT: str = "Select one or more folders first: their sizes are counted into the Size column."
NOT_LIVE_TEXT: str = "The connection is not live. Reconnect first."
TYPING_TEXT: str = "A name is being typed: Shift+Space types a space into it."
COUNTING_TEXT: str = "Counting the size of {what}… Stop Counting ends it."
COUNTED_TEXT: str = "The Size column shows the size of {what}."
COUNT_STOPPED_TEXT: str = "Counting the folder sizes was stopped."
COUNT_FAILED_TEXT: str = "The folder sizes could not be counted: {reason}"
STOP_COUNTING_TEXT: str = "Stop Counting"
STOP_COUNTING_TIP: str = "Stops counting the folder sizes; what was counted stays in the Size column."


def folders_text(count: int) -> str:
    return "1 folder" if count == 1 else f"{count} folders"


class SizesPart(WindowBase):
    """Block FE in the main window: folder sizes in the lists and the calculations of Properties."""

    def _build_sizes(self) -> None:
        self.counting: dict[str, tuple[threading.Event, Session | None]] = {}  # the side being counted: how to stop it, and its tab
        self.size_workers: dict[str, Worker] = {}  # a thread per side for counting folders, made at its first use
        self.calculations: set[threading.Event] = set()  # the stops of the calculations of Properties and Compare that run now
        for side_name, side in ((LOCAL, self.local_side), (REMOTE, self.remote_side)):
            action = QAction(FOLDER_SIZES_TEXT, self)
            action.setShortcut(QKeySequence(FOLDER_SIZES_KEY))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)  # the list with the focus only (OR-21)
            side.file_list.addAction(action)
            action.triggered.connect(lambda _checked=False, side_name=side_name: self.folder_sizes(side_name))
            self.list_actions[f"{side_name}_folder_sizes"] = action
            side.file_list.sizes_refused.connect(partial(self._sizes_refused, side_name))
            side.file_list.typing_changed.connect(self._typing_changed)
        self.stop_counting = QToolButton()
        self.stop_counting.setText(STOP_COUNTING_TEXT)
        self.stop_counting.setToolTip(STOP_COUNTING_TIP)
        self.stop_counting.clicked.connect(self.stop_all_counting)
        self.stop_counting.hide()
        self.status.addPermanentWidget(self.stop_counting)

    def _thread(self, side_name: str) -> Worker:
        worker = self.size_workers.get(side_name)
        if worker is None:
            worker = Worker(self.bridge.deliver)
            self.size_workers[side_name] = worker
        return worker

    def _typing_changed(self, _typed: str) -> None:
        self._update_size_actions()

    @override
    def run_alone(self, work: Callable[[], object], done: Callable[[object], None], failed: Callable[[Exception], None], stop: threading.Event) -> None:
        """A calculation on a thread of its own, which ends with it, so no calculation waits for another; stop is set when the main
        window closes, so a login it holds is closed at once."""
        worker = Worker(self.bridge.deliver)
        self.calculations.add(stop)

        def ended(result: object) -> None:
            self.calculations.discard(stop)
            done(result)

        def refused(error: Exception) -> None:
            self.calculations.discard(stop)
            failed(error)

        worker.submit(work, ended, refused)
        worker.stop()  # the thread ends once the work is done

    def _end_size_threads(self) -> None:
        """Closing: whatever still counts or calculates stops, and its threads end."""
        self.stop_all_counting()
        for stop in list(self.calculations):
            stop.set()
        for worker in self.size_workers.values():
            worker.stop()

    # --- the folder sizes of a list (FE-03) ----------------------------------------------

    def _folders_chosen(self, side_name: str) -> list[RemoteEntry]:
        side = self.local_side if side_name == LOCAL else self.remote_side
        return [entry for entry in side.file_list.acting_entries() if sizes.walked(entry)]

    def _size_reason(self, side_name: str) -> str:
        """Why the folder sizes of this side can't be counted now; empty when they can (OR-20)."""
        if (self.local_side if side_name == LOCAL else self.remote_side).file_list.typed:
            return TYPING_TEXT  # the key reaches the list and types its space
        if side_name == REMOTE:
            session = self.current()
            if session is None or not session.alive or not self.remote_side.folder:
                return NOT_LIVE_TEXT
        return "" if self._folders_chosen(side_name) else NO_FOLDER_SELECTED_TEXT

    def _update_size_actions(self) -> None:
        for side_name in (LOCAL, REMOTE):
            action = self.list_actions[f"{side_name}_folder_sizes"]
            why = self._size_reason(side_name)
            action.setEnabled(not why)
            action.setToolTip(why)

    def _sizes_refused(self, side_name: str) -> None:
        """Shift+Space while counting is greyed: nothing happens, and the status bar says why."""
        why = self._size_reason(side_name)
        if why:
            self.status.showMessage(why)

    def folder_sizes(self, side_name: str) -> None:
        """Shift+Space on the list with the focus: the selected folders are counted in the background, each Size cell filled as its
        folder is done; a new count on the same side replaces one that still runs."""
        if self._size_reason(side_name):
            return
        chosen = self._folders_chosen(side_name)
        side = self.local_side if side_name == LOCAL else self.remote_side
        folder = side.folder
        view = side.file_list
        session = self.current() if side_name == REMOTE else None
        remote = session.remote if session is not None else None
        if side_name == REMOTE and remote is None:
            return
        previous = self.counting.get(side_name)
        if previous is not None:
            previous[0].set()
        stop = threading.Event()
        self.counting[side_name] = (stop, session)
        self.stop_counting.show()
        what = folders_text(len(chosen))
        self.status.showMessage(COUNTING_TEXT.format(what=what))

        def keep_going() -> bool:  # another folder or another tab: its counts would mean nothing there
            return not stop.is_set() and view.folder == folder and (session is None or self.shown is session)

        def arrived(name: str, found: Count) -> None:
            if view.folder == folder and (session is None or self.current() is session):
                view.files.set_folder_size(name, found.size, found.complete)

        def each(name: str, found: Count) -> None:  # on the counting thread
            self.bridge.deliver(lambda: arrived(name, found))

        def work() -> bool:
            if remote is not None:
                return sizes.remote_counts(remote, folder, chosen, keep_going, each)
            return sizes.local_counts(folder, [entry.name for entry in chosen], keep_going, each)

        def ended(text: str) -> None:
            mine = self.counting.get(side_name)
            if mine is not None and mine[0] is stop:  # a count stopped with its tab says nothing (OR-23)
                del self.counting[side_name]
                self.status.showMessage(text)
            self.stop_counting.setVisible(bool(self.counting))

        def done(finished: bool) -> None:
            ended(COUNTED_TEXT.format(what=what) if finished else COUNT_STOPPED_TEXT)

        def failed(error: Exception) -> None:
            ended(COUNT_FAILED_TEXT.format(reason=shown_name(str(error))))

        self._thread(side_name).submit(work, done, failed)

    def stop_all_counting(self) -> None:
        for stop, _session in self.counting.values():
            stop.set()

    @override
    def _stop_counts(self, which: Callable[[Session | None], bool]) -> None:
        """The counts of the tabs which chooses stop at once and say nothing more; the button shows only while a count of the tab shown runs."""
        for side_name, (stop, session) in list(self.counting.items()):
            if which(session):
                stop.set()
                del self.counting[side_name]
        self.stop_counting.setVisible(bool(self.counting))

    # --- the Size and Checksum tabs of Properties (FE-02) --------------------------------------

    def _run_away(self, work: Callable[[KeepGoing], bool], done: Callable[[bool], None], failed: Callable[[str], None]) -> Stop:
        """work runs on a thread of its own; its answer comes on the window's thread. The stop it returns is safe on any thread."""
        stop = threading.Event()

        def keep_going() -> bool:
            return not stop.is_set()

        self.run_alone(lambda: work(keep_going), lambda finished: done(bool(finished)), lambda error: failed(shown_name(str(error))), stop)
        return stop.set

    def _remember_algorithm(self, algorithm: str) -> None:
        if algorithm == self.settings.checksum_algorithm:
            return
        self.settings = replace(self.settings, checksum_algorithm=algorithm)
        try:
            saved_settings.save(self.settings)
        except OSError:
            self.status.showMessage(SETTINGS_NOT_KEPT_TEXT)

    def _first_algorithm(self, offered: tuple[str, ...], preferred: str) -> str:
        last = self.settings.checksum_algorithm
        return last if last in offered else preferred if preferred in offered else offered[0] if offered else ""

    @override
    def remote_calculator(self, session: Session, folder: str, chosen: list[RemoteEntry]) -> Calculator | None:
        remote = session.remote
        if remote is None:
            return None
        connection: RemoteConnection = remote
        capabilities = session.capabilities
        offered = checksums.algorithms_of(capabilities)

        def count(each: Callable[[str, Count], None], done: Callable[[bool], None], failed: Callable[[str], None]) -> Stop:
            def work(keep_going: KeepGoing) -> bool:
                return sizes.remote_counts(connection, folder, chosen, keep_going, lambda name, found: self.bridge.deliver(lambda: each(name, found)))

            return self._run_away(work, done, failed)

        def sums(algorithm: str, each: Callable[[Checksum], None], done: Callable[[bool], None], failed: Callable[[str], None], told: Callable[[str], None]) -> Stop:
            def work(keep_going: KeepGoing) -> bool:
                listed = checksums.remote_files(connection, folder, chosen, keep_going)
                if listed is None:
                    return False
                files, problems = listed
                for problem in problems:
                    self.bridge.deliver(lambda problem=problem: each(problem))
                paths = [joined(folder, relative) for relative in files]
                names = dict(zip(paths, files, strict=True))

                def found(checksum: Checksum) -> None:
                    shown = replace(checksum, path=names.get(checksum.path, checksum.path))
                    self.bridge.deliver(lambda: each(shown))

                return checksums.remote_checksums(connection, paths, algorithm, keep_going, found, lambda text: self.bridge.deliver(lambda: told(text)))

            return self._run_away(work, done, failed)

        why = checksums.why_no_checksum(capabilities, session.connection.protocol == FTP)
        return Calculator(count, sums, offered, self._first_algorithm(offered, checksums.preferred_algorithm(capabilities)), why, self._remember_algorithm)

    @override
    def local_calculator(self, folder: str, chosen: list[RemoteEntry]) -> Calculator:
        names = [entry.name for entry in chosen]

        def count(each: Callable[[str, Count], None], done: Callable[[bool], None], failed: Callable[[str], None]) -> Stop:
            def work(keep_going: KeepGoing) -> bool:
                return sizes.local_counts(folder, names, keep_going, lambda name, found: self.bridge.deliver(lambda: each(name, found)))

            return self._run_away(work, done, failed)

        def sums(algorithm: str, each: Callable[[Checksum], None], done: Callable[[bool], None], failed: Callable[[str], None], _told: Callable[[str], None]) -> Stop:
            def work(keep_going: KeepGoing) -> bool:
                listed = checksums.local_files(folder, names, keep_going)
                if listed is None:
                    return False
                files, problems = listed
                for problem in problems:
                    self.bridge.deliver(lambda problem=problem: each(problem))
                return checksums.local_checksums(folder, files, algorithm, keep_going, lambda found: self.bridge.deliver(lambda: each(found)))

            return self._run_away(work, done, failed)

        return Calculator(count, sums, CHECKSUM_ALGORITHMS, self._first_algorithm(CHECKSUM_ALGORITHMS, checksums.PREFERRED[0]), "", self._remember_algorithm)
