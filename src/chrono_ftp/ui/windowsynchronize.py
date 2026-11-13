"""The synchronize part of the main window (block FF): the Synchronize window of a tab, the same window for two folders on this
computer (FF-14), Start through the queue (FF-10), and the command that marks in both lists what differs from the other side (FF-12).
Every server call goes through the tab's own thread, and the work on this computer through a thread of its own, so the window never
freezes (D-019)."""

import os
import shutil
import threading
from typing import override
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QMessageBox

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.bookmarks import LOCAL, REMOTE
from chrono_ftp.connections.base import RemoteConnection, joined, shown_name
from chrono_ftp.core.connection import FTP
from chrono_ftp.core.synchronizing import Options, counted_before, remember_count, scan_key
from chrono_ftp.core.transferoptions import plain
from chrono_ftp.engine import checksums
from chrono_ftp.engine.sizes import Count, local_count, remote_count_by_listing
from chrono_ftp.engine.synchronize import (
    COPIES,
    DELETES,
    MOVES,
    TOUCHES,
    Action,
    Criterion,
    Direction,
    Mode,
    Pair,
    Row,
    checksum_pairs,
    checksums_of,
    delete_warning,
    local_reader,
    plan,
    precision_of_pairs,
    why_not_these_two,
    remote_reader,
    scan,
)
from chrono_ftp.transfers.queue import Job
from chrono_ftp.transfers.sidework import SideJob, SideWork, Step, on_this_computer
from chrono_ftp.ui.startabort import aborted_text, finished_text
from chrono_ftp.ui.comparedialog import CompareDialog
from chrono_ftp.ui.filelist import FileList
from chrono_ftp.ui.plaintext import counted, box_text, fitting
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowsynchronize.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: What a run on this computer says at its end comes from ui/startabort.py, shared with the paste of files (D-099)
# v0.0.44: The window says "looking inside the files" where it said checksums
# v0.0.44: An Abort pressed after the last step says it finished, because nothing was aborted
# v0.0.44: Review by the owner: Start in place of Apply, and the same button aborts while the work runs; two folders on this
#          computer are synchronized with the window on screen, abortable, and it says what was done and what was not; Start on a
#          server opens the queue and says that the work went there; the background counting has its own button (D-097)
# v0.0.43: Initial: the Synchronize window with its choices and its checklist, Apply through the queue, the deletes that are named and
#          asked for first, the folder sizes counted in the background, the scan statistics, the two local folders and Mark
#          Differences (FF-01 to FF-14, D-038 changed, D-091 to D-096); review round 1: a scan can be stopped and leaves nothing
#          running, and the question before a delete names the files and the folders apart and says a folder goes whole

NO_FOLDER_TEXT: str = "Pick the other folder on this computer"
NOTHING_TICKED_TEXT: str = "Nothing is ticked, so nothing was done."
MARK_TEXT: str = "Mark Differences"
ANOTHER_FOLDER_TEXT: str = "Synchronize with another folder on this computer…"
MARK_TIP: str = "Marks the files of this folder that differ from, or are missing on, the other side. Nothing is copied."
MARK_WHY_NO_TAB: str = "There is no live connection to compare with."
MARKED_TEXT: str = "{count} of {folder} {differ} from the other side."
NONE_MARKED_TEXT: str = "Nothing in {folder} differs from the other side."
LOCAL_SIDE_WORDS: str = "the other folder"
TO_THE_QUEUE_TEXT: str = "{count} went to the queue under the two panels; you can watch, pause and stop them there."
ONE_TO_THE_QUEUE_TEXT: str = "1 thing went to the queue under the two panels; you can watch, pause and stop it there."


def _there_path(folder: str, row: Row, on_a_server: bool) -> str:
    inside = joined(folder, row.folder) if row.folder else folder
    if on_a_server:
        return joined(inside, row.name)
    return os.path.join(folder, row.folder, row.name) if row.folder else os.path.join(folder, row.name)


def _here_path(folder: str, row: Row) -> str:
    return os.path.join(folder, row.folder, row.name) if row.folder else os.path.join(folder, row.name)


class SynchronizePart(WindowBase):
    """Synchronize: the window of a tab, the window for two local folders, and Mark Differences."""

    @override
    def _build_synchronize(self) -> None:
        """The commands of block FF that live in a list's own menu: Mark Differences on both sides (FF-12), and the second folder on
        this computer on the left (FF-14)."""
        for side_name, side in ((LOCAL, self.local_side), (REMOTE, self.remote_side)):
            action = QAction(MARK_TEXT, self)
            action.setToolTip(MARK_TIP)
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)  # the list with the focus only (OR-21)
            side.file_list.addAction(action)
            action.triggered.connect(lambda _checked=False, name=side_name: self.mark_differences(name))
            self.list_actions[f"{side_name}_mark_differences"] = action
        other = QAction(ANOTHER_FOLDER_TEXT, self)
        other.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.local_side.file_list.addAction(other)
        other.triggered.connect(lambda _checked=False: self.synchronize_two_local_folders())
        self.list_actions[f"{LOCAL}_sync_folder"] = other

    @override
    def _update_synchronize_actions(self) -> None:
        """Mark Differences needs a live connection and a folder on both sides; greyed with its reason, never hidden (OR-20)."""
        session = self.current()
        ready = session is not None and session.alive and bool(self.remote_side.folder) and bool(self.local_side.folder)
        for side_name in (LOCAL, REMOTE):
            action = self.list_actions.get(f"{side_name}_mark_differences")
            if action is not None:
                action.setEnabled(ready)
                action.setToolTip(MARK_TIP if ready else MARK_WHY_NO_TAB)

    # --- the window of a tab ---------------------------------------------------

    @override
    def synchronize(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, there_folder, _chosen = screen
        here_folder = self.local_side.folder
        remote = session.remote
        if remote is None:
            return
        on_ftp = session.connection.protocol == FTP
        why_no_checksum = checksums.why_no_checksum(session.capabilities, on_ftp)
        why_no_timestamps = "" if session.capabilities.timestamps else NO_TIMESTAMPS_TEXT
        selected = tuple(entry.name for entry in self.local_side.selected_entries())
        dialog = CompareDialog(here_folder, there_folder, self, why_no_checksum, self._start_options(), why_no_timestamps, bool(selected), self.settings.mask_history)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)  # it belongs to this tab: no other tab can come up meanwhile
        connection: RemoteConnection = remote
        runs: list[threading.Event] = []  # each run of the checksum step has its own stop: a stopped one never answers for the next
        scans: list[threading.Event] = []  # and so has each scan: Stop ends it, and a stopped one never answers for the next

        def failed(error: Exception) -> None:
            dialog.show_failure(shown_name(str(error)))

        def start_scan() -> None:
            options = dialog.chosen()
            self._keep_options(options)
            key = scan_key(here_folder, there_folder)
            for earlier in scans:
                earlier.set()  # nothing of an earlier scan goes on while this one runs
            stop = threading.Event()
            scans.append(stop)
            dialog.scan_started(counted_before(self.settings.scan_counts, key))
            algorithm = checksums.preferred_algorithm(session.capabilities)
            keep_going = lambda: not stop.is_set()  # noqa: E731 - handed to the engine, which asks it between every step

            def seen(count: int) -> None:
                self.bridge.deliver(lambda: dialog.scan_progress(count) if stop is scans[-1] and not stop.is_set() else None)

            def work() -> tuple[list[Row], int] | None:
                pairs = scan(here_folder, there_folder, local_reader, remote_reader(connection), options, keep_going, selected, True, seen)
                if pairs is None:
                    return None
                found: dict[str, tuple[str, str]] = {}
                if options.criterion == Criterion.CHECKSUM and algorithm:
                    computed = checksums_of(connection, here_folder, there_folder, checksum_pairs(pairs, options), algorithm, keep_going)
                    if computed is None:
                        return None
                    found = computed
                return plan(pairs, options, precision_of_pairs(pairs), found), len(pairs)

            def ready(result: object) -> None:
                if stop is not scans[-1]:
                    return  # an earlier scan that was stopped: it never answers for the one that runs
                if not isinstance(result, tuple):
                    dialog.scan_was_stopped()  # the only way work gives nothing is a stop (OR-23)
                    return
                rows, looked_at = result
                if isinstance(rows, list) and isinstance(looked_at, int):
                    self._remember_scan(key, looked_at)
                    dialog.show_rows(rows)

            def refused(error: Exception) -> None:
                if stop is scans[-1]:
                    failed(error)

            session.worker.submit(work, ready, refused)

        def stop_scan() -> None:
            if scans:
                scans[-1].set()

        def contents() -> None:
            """The checksum step of block FE, on a thread of its own: the tab's own thread stays free for browsing."""
            stop = threading.Event()
            runs.append(stop)
            algorithm = checksums.preferred_algorithm(session.capabilities)
            dialog.contents_started(algorithm)
            rows = list(dialog.rows)

            def work() -> object:
                pairs = [Pair(row.folder, row.name, row.here, row.there) for row in rows if row.here is not None and row.there is not None and not row.is_folder and row.here.size == row.there.size]
                found = checksums_of(connection, here_folder, there_folder, pairs, algorithm, lambda: not stop.is_set())
                if found is None:
                    return None
                same = {path for path, (mine, theirs) in found.items() if mine == theirs}
                kept = [row for row in rows if row.path not in same]  # the same content: the row is left out (D-020)
                return kept, len(same), len(pairs) - len(found)

            def ready(result: object) -> None:
                if stop is not runs[-1]:
                    return  # an earlier run that was stopped
                if not isinstance(result, tuple):
                    dialog.contents_ended(None)
                    return
                kept, same, unknown = result
                if isinstance(kept, list) and isinstance(same, int) and isinstance(unknown, int):
                    dialog.contents_ended(kept, same, unknown)

            def refused(error: Exception) -> None:
                if stop is runs[-1]:
                    dialog.contents_ended(None)
                    failed(error)

            self.run_alone(work, ready, refused, stop)

        def stopped() -> None:
            if runs:
                runs[-1].set()

        def sizes_wanted(rows: object) -> None:
            if isinstance(rows, list):
                self._count_sync_folders(session, dialog, here_folder, there_folder, [row for row in rows if isinstance(row, Row)])

        def start_rows(rows: object) -> None:
            if isinstance(rows, list):
                self._start_on_the_server(session, dialog, here_folder, there_folder, [row for row in rows if isinstance(row, Row)])

        dialog.start_requested.connect(start_rows)
        dialog.scan_requested.connect(start_scan)
        dialog.scan_stopped.connect(stop_scan)
        dialog.contents_requested.connect(contents)
        dialog.contents_stopped.connect(stopped)
        dialog.sizes_wanted.connect(sizes_wanted)
        dialog.show()
        start_scan()

    @override
    def compare(self) -> None:
        """The name the action had before block FF."""
        self.synchronize()

    # --- the choices kept (FF-11) -----------------------------------------------

    def _start_options(self) -> Options:
        """The choices of last time, with the direction taken from the side that has the keyboard focus (FF-11)."""
        kept = self.settings.synchronize
        return replace(kept, direction=Direction.TO_THERE if self.focused_side == LOCAL else Direction.TO_HERE, selected_only=False)

    def _keep_options(self, options: Options) -> None:
        self.settings = replace(self.settings, synchronize=options)
        if options.mask:
            self.remember_mask(options.mask)
        self._save_settings_quietly()

    def _remember_scan(self, key: str, count: int) -> None:
        """How many names this scan looked at, so the next scan of the same folder pair has a progress bar (FF-13)."""
        self.settings = replace(self.settings, scan_counts=remember_count(self.settings.scan_counts, key, count))
        self._save_settings_quietly()

    def _save_settings_quietly(self) -> None:
        try:
            saved_settings.save(self.settings)
        except OSError:
            pass  # the choices hold for now; only next time's memory is lost, which is nothing to alarm anyone with (OR-23)

    # --- the size of a one-sided folder (FF-06) ----------------------------------

    def _count_sync_folders(self, session: Session | None, dialog: CompareDialog, here_folder: str, there_folder: str, rows: list[Row]) -> None:
        """The folders that are on one side only, worked out in the background with the counting of block FE, so the window never
        waits. The person stops it from the button that says so, and it also ends with its window: work nobody can see or stop may
        never go on running (D-097)."""
        stop = threading.Event()
        dialog.finished.connect(lambda _result=0: stop.set())
        dialog.counting_stopped.connect(stop.set)
        remote = session.remote if session is not None else None

        def tell(row: Row, count: Count) -> None:
            self.bridge.deliver(lambda: dialog.size_found(row.folder, row.name, count))

        def work() -> object:
            for row in rows:
                if stop.is_set():
                    return None
                if row.here is not None:
                    found = local_count(_here_path(here_folder, row), lambda: not stop.is_set())
                elif remote is not None:
                    found = remote_count_by_listing(remote, _there_path(there_folder, row, True), lambda: not stop.is_set())
                else:
                    found = local_count(_there_path(there_folder, row, False), lambda: not stop.is_set())
                if found is None:
                    return None
                tell(row, found)
            return None

        def ended(_result: object) -> None:
            if stop.is_set():
                dialog.counting_was_stopped()

        self.run_alone(work, ended, lambda _error: ended(None), stop)

    # --- Start: what the ticked rows do (FF-08, FF-10) ----------------------------

    def _deletes_agreed(self, rows: list[Row], on_a_server: bool = True, a_mask_is_on: bool = False) -> bool:
        """A delete is the step that can't be undone: what will go is named in full, files and folders apart and a folder with
        everything in it, and it is asked for first (G-2, FF-08). The text itself is made without a screen (delete_warning)."""
        text = delete_warning(rows, on_a_server, a_mask_is_on)
        if not text:
            return True
        return QMessageBox.question(self, "Synchronize", box_text(text)) == QMessageBox.StandardButton.Yes

    def _start_on_the_server(self, session: Session, dialog: CompareDialog, here_folder: str, there_folder: str, rows: list[Row]) -> None:
        """Start on a server hands the work to the queue, where it can be watched, paused and stopped: the ticked rows are the
        decision, so copies go without an overwrite question and without "only newer", and the deletes, moves and dates go as items
        of their own (FF-10). The queue is opened and the status bar says so, so the screen itself shows where the work went (D-097)."""
        if not rows:
            self.status.showMessage(NOTHING_TICKED_TEXT)
            return
        if session not in self.sessions or not session.alive or session.remote is None:
            self._lost_meanwhile(session)  # nothing is queued: the tab is grey, and Reconnect brings it back
            return
        if not self._deletes_agreed(rows, True, bool(dialog.chosen().mask)):
            return
        options = replace(plain(self.settings.transfer_options), ask_before_replacing=False, only_newer=False)  # exactly the rows ticked, byte for byte
        for row in rows:
            if row.action not in COPIES:
                continue
            here = os.path.join(here_folder, row.folder) if row.folder else here_folder
            there = joined(there_folder, row.folder) if row.folder else there_folder
            if row.action == Action.COPY_THERE:
                self._add_job(session, os.path.join(here, row.name), there, True, row.name, options)
            else:
                self._add_job(session, here, joined(there, row.name), False, row.name, options)
        queued = len([row for row in rows if row.action in COPIES])
        for side in self._side_jobs(here_folder, there_folder, rows, True):
            self._add_side_job(session, side)
            queued += 1
        self._show_queue()  # the work went there, and the person can see that from the screen (D-097)
        self.status.showMessage(ONE_TO_THE_QUEUE_TEXT if queued == 1 else TO_THE_QUEUE_TEXT.format(count=f"{queued} things"))
        dialog.close()

    def _side_jobs(self, here_folder: str, there_folder: str, rows: list[Row], on_a_server: bool) -> list[SideJob]:
        """The deletes, moves and dates of the ticked rows, gathered per kind and per side: one item of the queue each (FF-10)."""
        gathered: dict[tuple[SideWork, bool], list[Step]] = {}
        for row in rows:
            there = row.on_there
            path = _there_path(there_folder, row, on_a_server) if there else _here_path(here_folder, row)
            if row.action in DELETES:
                gathered.setdefault((SideWork.DELETE, there), []).append(Step(path, is_folder=row.is_folder))
            elif row.action in MOVES:
                old = Row(row.from_folder, row.from_name, row.action, row.here, row.there, True, "")
                was = _there_path(there_folder, old, on_a_server) if there else _here_path(here_folder, old)
                gathered.setdefault((SideWork.MOVE, there), []).append(Step(was, path, is_folder=row.is_folder))
            elif row.action in TOUCHES:
                source = row.here if there else row.there
                if source is not None and source.modified is not None:
                    gathered.setdefault((SideWork.TOUCH, there), []).append(Step(path, moment=source.modified))
            elif row.action in COPIES and not on_a_server:
                target = _there_path(there_folder, row, False) if row.action == Action.COPY_THERE else _here_path(here_folder, row)
                source = _here_path(here_folder, row) if row.action == Action.COPY_THERE else _there_path(there_folder, row, False)
                gathered.setdefault((SideWork.COPY, row.action == Action.COPY_THERE), []).append(Step(source, target, is_folder=row.is_folder))
        return [SideJob(work, there, tuple(steps)) for (work, there), steps in gathered.items() if steps]

    def _add_side_job(self, session: Session, side: SideJob) -> Job | None:
        remote = session.remote
        if remote is None:
            return None
        name = f"{side.work.value} ({side.count})"
        job = Job(remote, session.connection, self.local_side.folder, self.remote_side.folder, side.on_the_server, name, has_password=bool(session.password), tab=session, side=side)
        self.queue.add(job)
        return job

    # --- two folders on this computer (FF-14) -------------------------------------

    @override
    def synchronize_two_local_folders(self) -> None:
        """The same window for two folders on this computer; the right side of the main window is never touched (OR-1, FF-14)."""
        here_folder = self.local_side.folder
        there_folder = QFileDialog.getExistingDirectory(self, NO_FOLDER_TEXT, here_folder)
        if not there_folder:
            return
        why = why_not_these_two(here_folder, there_folder)
        if why:
            QMessageBox.information(self, "Synchronize", box_text(why))  # the run never starts: it would write a folder into itself
            return
        selected = tuple(entry.name for entry in self.local_side.selected_entries())
        dialog = CompareDialog(here_folder, there_folder, self, NO_CHECKSUM_LOCALLY_TEXT, self._start_options(), "", bool(selected), self.settings.mask_history, on_a_server=False)
        scans: list[threading.Event] = []  # each scan has its own stop: Stop ends it, and a stopped one never answers for the next

        def start_scan() -> None:
            options = replace(dialog.chosen(), criterion=Criterion.DATE if dialog.chosen().criterion == Criterion.CHECKSUM else dialog.chosen().criterion)
            self._keep_options(options)
            key = scan_key(here_folder, there_folder)
            for earlier in scans:
                earlier.set()
            stop = threading.Event()
            scans.append(stop)
            dialog.scan_started(counted_before(self.settings.scan_counts, key))

            def seen(count: int) -> None:
                self.bridge.deliver(lambda: dialog.scan_progress(count) if stop is scans[-1] and not stop.is_set() else None)

            def work() -> object:
                pairs = scan(here_folder, there_folder, local_reader, local_reader, options, lambda: not stop.is_set(), selected, False, seen)
                return None if pairs is None else (plan(pairs, options, 1, {}, False), len(pairs))

            def ready(result: object) -> None:
                if stop is not scans[-1]:
                    return
                if isinstance(result, tuple) and isinstance(result[0], list) and isinstance(result[1], int):
                    self._remember_scan(key, result[1])
                    dialog.show_rows(result[0])
                else:
                    dialog.scan_was_stopped()

            self.run_alone(work, ready, lambda error: dialog.show_failure(shown_name(str(error))) if stop is scans[-1] else None, stop)

        def stop_scan() -> None:
            if scans:
                scans[-1].set()

        def sizes_wanted(rows: object) -> None:
            if isinstance(rows, list):
                self._count_sync_folders(None, dialog, here_folder, there_folder, [row for row in rows if isinstance(row, Row)])

        work_stops: list[threading.Event] = []  # the run that is busy: Abort and closing the window both end it (D-097)

        def start_rows(rows: object) -> None:
            if isinstance(rows, list):
                self._start_two_local(dialog, here_folder, there_folder, [row for row in rows if isinstance(row, Row)], work_stops)

        def abort_work() -> None:
            for running in work_stops:
                running.set()

        dialog.start_requested.connect(start_rows)
        dialog.work_stopped.connect(abort_work)
        dialog.scan_requested.connect(start_scan)
        dialog.scan_stopped.connect(stop_scan)
        dialog.sizes_wanted.connect(sizes_wanted)
        dialog.finished.connect(lambda _result=0: (stop_scan(), abort_work()))  # nothing is left running behind a closed window
        dialog.show()
        start_scan()

    def _start_two_local(self, dialog: CompareDialog, here_folder: str, there_folder: str, rows: list[Row], stops: list[threading.Event]) -> None:
        """Two folders on this computer: no connection is in play, so the work runs on a thread of its own while the window stays on
        screen, showing what it is doing and how far it is. The same button now reads Abort; it ends after the file it is busy with,
        and the window says plainly what was done and what was not (D-097)."""
        if not rows:
            dialog.work_ended(NOTHING_TICKED_TEXT)
            return
        if not self._deletes_agreed(rows, False, bool(dialog.chosen().mask)):
            return
        sides = self._side_jobs(here_folder, there_folder, rows, False)
        stop = threading.Event()
        stops.append(stop)
        count = sum(side.count for side in sides)
        done, failed = [0], [0]
        dialog.work_started(count)

        def work() -> object:
            for side in sides:
                for step in side.steps:
                    if stop.is_set():
                        return None  # after the file it was busy with: what was done stays done
                    try:
                        on_this_computer(side.work, step)
                        done[0] += 1
                    except (OSError, ValueError, shutil.Error):
                        failed[0] += 1
                    told, name = done[0] + failed[0], os.path.basename(step.target or step.path)
                    self.bridge.deliver(lambda shown=told, what=name: dialog.work_progress(shown, count, what))
            return None

        def ended(_result: object) -> None:
            left = count - done[0] - failed[0]
            if stop.is_set() and left > 0:  # an Abort after the last step stopped nothing: it finished (G-2)
                text = aborted_text(done[0], count, left + failed[0])
            else:
                text = finished_text(done[0], count, failed[0])
            dialog.work_ended(text)
            self.status.showMessage(text)
            self.local_side.refresh()

        self.run_alone(work, ended, lambda _error: ended(None), stop)

    # --- Mark Differences (FF-12) --------------------------------------------------

    @override
    def mark_differences(self, side_name: str) -> None:
        """One command in both lists: the files of the folder shown that differ from, or are missing on, the other side are marked.
        Only the folders shown are looked at, and nothing is copied (FF-12)."""
        session = self.current()
        here_folder, there_folder = self.local_side.folder, self.remote_side.folder
        if session is None or not session.alive or session.remote is None or not there_folder or not here_folder:
            self.status.showMessage(MARK_WHY_NO_TAB)
            return
        connection: RemoteConnection = session.remote
        options = replace(self.settings.synchronize, no_subfolders=True, selected_only=False, existing_only=False, direction=Direction.BOTH, mode=Mode.SYNCHRONIZE)
        view: FileList = (self.local_side if side_name == LOCAL else self.remote_side).file_list

        def work() -> object:
            return scan(here_folder, there_folder, local_reader, remote_reader(connection), options, lambda: True, (), True)

        def ready(result: object) -> None:
            if not isinstance(result, list):
                return
            pairs = [pair for pair in result if isinstance(pair, Pair)]
            rows = plan(pairs, options, precision_of_pairs(pairs))
            names = {row.name for row in rows if row.action != Action.NOTHING or row.reason}
            view.select_names(names)
            folder = fitting(shown_name(here_folder if side_name == LOCAL else there_folder), self)
            self.status.showMessage(MARKED_TEXT.format(count=counted(len(names), "thing", "things"), folder=folder, differ="differs" if len(names) == 1 else "differ") if names else NONE_MARKED_TEXT.format(folder=folder))

        session.worker.submit(work, ready, lambda error: self.status.showMessage(shown_name(str(error))))


NO_TIMESTAMPS_TEXT: str = "This connection can't set the date of a file on the server, so only dates on this computer could be set."
NO_CHECKSUM_LOCALLY_TEXT: str = "Two folders on this computer are compared by date and size; looking inside the files is for a server."
NOTHING_FOUND_TEXT: str = "The comparison was stopped."