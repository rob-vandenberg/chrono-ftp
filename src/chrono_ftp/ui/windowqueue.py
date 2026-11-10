"""The queue under the sides of the main window: shown while transfers run, hidden when idle unless pinned; its buttons, its right-click
menu and what happens when it is done (FC-02 to FC-04)."""

from collections.abc import Callable
from dataclasses import replace
from typing import override

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections.base import RemoteConnection
from chrono_ftp.transfers.queue import ENDED, LINE, Job, State
from chrono_ftp.ui.afterqueue import CLOSE, DISCONNECT, NOT_DONE_TEXT, POWER_OFF, STAY, SUSPEND, Countdown
from chrono_ftp.ui.menus import open_menu
from chrono_ftp.ui.queueview import CLOSED_TAB_TEXT, NOT_CONNECTED_TAB_TEXT, QUEUE_ACTIONS, LimitDialog
from chrono_ftp.ui.session import Session
from chrono_ftp.ui.windowbase import SETTINGS_NOT_KEPT_TEXT, WINDOW_TITLE, WindowBase
from chrono_ftp.ui.plaintext import box_text

# --- Version ------------------------------------------------------------
__version__ = "windowqueue.py 0.0.29"

# --- Version history ----------------------------------------------------
# v0.0.29: The right-click menu of the queue opens through open_menu, which lets go of the keyboard (D-052)
# v0.0.13: The queue's buttons and right-click menu: resume, pause, move up and down, run now, retry, speed limit, delete, pause all,
#          resume all and clear finished, each greyed where it does not apply; the switch to start items by themselves is kept in
#          Settings; a job that left the queue leaves the list; when the queue is done, the chosen step, once: disconnect, or close,
#          suspend or shut down after a countdown; the queue stays on the screen while items are paused; chosen items move as a block;
#          Stop for the selection; no pause over a stop on its way; a step the computer no longer allows is said in the status bar;
#          unsent edits keep Chrono FTP open and the computer on; closing chosen as the step asks nothing when nothing is unsent; work
#          that arrived meanwhile keeps the choice; when the person stopped or deleted the rest, the choice goes back to Stay; a job
#          resumed or retried goes over its tab's connection as it is now; one rule for the step: it runs when the last item to leave
#          the queue ended by itself, else the choice goes back to Stay with a calm word; while a step is chosen the queue stays shown;
#          a save from Edit counts as on its way again only when the queue really started it again; the reason an item can't go on is
#          given only for one that could otherwise; a dropped chosen step is told; how many transfers run at once, from the queue's title
#          bar or Settings, takes effect at once and is kept (FC-01)
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)

DISCONNECTED_TEXT: str = "The queue is done, so {name} was disconnected. Reconnect brings it back."
CALLED_OFF_TEXT: str = "The queue has work again, so the step after it was called off."
STOPPED_TEXT: str = "The queue was stopped, so nothing more happens."
WHEN_DONE_MARK_TEXT: str = "When done: {choice}"
WHEN_DONE_MARK_TIP: str = "Chosen in the queue. It happens once nothing is waiting, running or paused any more, and only when the last item ended by itself; then the choice goes back to Stay."
NOT_ALLOWED_TEXTS: dict[str, str] = {
    SUSPEND: "The computer does not allow suspending now, so it stays on.",
    POWER_OFF: "The computer does not allow shutting down now, so it stays on.",
}
EDITS_TEXTS: dict[str, tuple[str, str]] = {
    CLOSE: ("The queue is done, but 1 edited file is not on the server yet, so Chrono FTP stays open.", "The queue is done, but {count} edited files are not on the server yet, so Chrono FTP stays open."),
    POWER_OFF: ("The queue is done, but 1 edited file is not on the server yet, so the computer stays on.", "The queue is done, but {count} edited files are not on the server yet, so the computer stays on."),
}


def pausable(job: Job) -> bool:
    """Waiting, or running without a stop on its way (a resume on its way may be paused again)."""
    return job.state == State.WAITING or (job.state == State.RUNNING and (not job.stop_asked.is_set() or job.after_stop == State.WAITING))


def stoppable(job: Job) -> bool:
    """Waiting, paused, or running without a stop on its way (a pause on its way may still become a stop)."""
    return job.state in LINE or (job.state == State.RUNNING and not (job.stop_asked.is_set() and job.after_stop == State.STOPPED))


class QueuePart(WindowBase):
    """The queue under the sides of the main window: shown while transfers run, hidden when idle unless pinned."""

    def toggle_queue(self) -> None:
        if self.queue_panel.isVisibleTo(self):
            self.failure_unseen = False
            self.queue_panel.pin.setChecked(False)  # closing it by hand also lets go of the pin
            self.queue_closed = self._transfers_running()
            self._hide_queue()
        else:
            self.queue_closed = False
            self._show_queue()

    @override
    def _show_queue(self) -> None:
        self.queue_linger.stop()
        if not self.queue_panel.isVisibleTo(self):
            self.queue_panel.show()
            total = sum(self.body.sizes())
            self.body.setSizes([max(total - self.queue_height, 1), self.queue_height])

    def _hide_queue(self) -> None:
        if self.queue_panel.isVisibleTo(self):
            self.queue_height = self.queue_panel.height() or self.queue_height
            self.queue_panel.hide()

    @override
    def _transfers_running(self) -> bool:
        """Also paused items: they wait for the person."""
        return any(job.state in (*LINE, State.RUNNING) for job in self.queue.jobs)

    def _show_when_done(self) -> None:
        """A step chosen for when the queue is done is always to be seen: a mark in the status bar, also with the queue hidden."""
        box = self.queue_panel.when_done
        self.when_done_label.setText(WHEN_DONE_MARK_TEXT.format(choice=box.currentText()))
        self.when_done_label.setToolTip(WHEN_DONE_MARK_TIP)
        self.when_done_label.setVisible(self._armed())

    def _armed(self) -> bool:
        """A step is chosen for when the queue is done: the queue stays on the screen, so the person always sees it."""
        return self.queue_panel.when_done.choice() != STAY

    def _hide_idle_queue(self) -> None:
        if not (self.queue_panel.pin.isChecked() or self.failure_unseen or self._transfers_running() or self._armed()):
            self._hide_queue()

    def _pin_toggled(self, pinned: bool) -> None:
        if pinned:
            self.queue_linger.stop()
        elif not self.failure_unseen and not self._transfers_running() and not self._armed():
            self.queue_linger.start()

    @override
    def _follow_queue(self, job: Job) -> None:
        """The queue shows while something runs, and for a moment after, so the result can be seen."""
        if job.state == State.FAILED:
            self.failure_unseen = True
        running = self._transfers_running()
        if not running:
            self.queue_closed = False
        if job.state == State.FAILED or (running and not self.queue_closed):
            self._show_queue()
        elif not running and not self.queue_panel.pin.isChecked() and not self.failure_unseen and not self._armed():
            self.queue_linger.start()

    # --- the queue's own buttons (FC-02, FC-03)

    def _connect_queue_panel(self) -> None:
        handlers: dict[str, Callable[[], None]] = {
            "resume": lambda: self._each_selected(self._again(self.queue.resume)),
            "pause": lambda: self._each_selected(self.queue.pause),
            "stop": lambda: self._each_selected(self.queue.stop),
            "move_up": lambda: self._move_selected(-1),
            "move_down": lambda: self._move_selected(1),
            "run_now": self._run_selected_now,
            "retry": lambda: self._each_selected(self._again(self.queue.retry)),
            "limit": self._limit_selected,
            "delete": lambda: self._each_selected(self.queue.delete),
            "stop_all": self.stop_transfers,
            "pause_all": self.queue.pause_all,
            "resume_all": lambda: self._each_job(self._again(self.queue.resume)),
            "clear_finished": self.queue.clear_finished,
        }
        for name, action in self.queue_panel.actions_by_name.items():
            action.triggered.connect(lambda _checked=False, name=name: self._queue_action(handlers[name]))
        self.queue_view.itemSelectionChanged.connect(self.update_queue_actions)
        self.queue_view.customContextMenuRequested.connect(self._queue_menu_wanted)
        self.queue_panel.start_by_themselves.toggled.connect(self._start_by_themselves_changed)
        self.queue_panel.at_once.valueChanged.connect(self._at_once_changed)
        self.queue_panel.when_done.possible = lambda action: self.power.possible(action)  # asked of the power at that moment
        self.queue_panel.when_done.currentIndexChanged.connect(self._show_when_done)
        self.queue_panel.when_done.dropped = lambda value: self.status.showMessage(NOT_ALLOWED_TEXTS.get(value, ""))  # calmly, as at the end

    def _queue_action(self, work: Callable[[], None]) -> None:
        work()
        self.queue_view.follow(self.queue.jobs)
        self.update_queue_actions()

    def _each_selected(self, act: Callable[[Job], None]) -> None:
        for job in self.queue_view.selected_jobs():
            act(job)

    def _move_selected(self, step: int) -> None:
        self.queue.move_together(self.queue_view.selected_jobs(), step)  # as a block: their order stays

    def _run_selected_now(self) -> None:
        chosen = self.queue_view.selected_jobs()
        if len(chosen) == 1:
            self._again(self.queue.run_now)(chosen[0])

    def _why_not(self, job: Job) -> str:
        """Why it can't go on now: its tab is closed, or not connected; empty where it can."""
        if job.tab is None:
            return ""
        tab = self._tab_of(job)
        if tab is None:
            return CLOSED_TAB_TEXT
        return "" if tab.alive and tab.remote is not None else NOT_CONNECTED_TAB_TEXT

    def _each_job(self, act: Callable[[Job], None]) -> None:
        for job in self.queue.jobs:
            act(job)

    def _again(self, act: Callable[[Job], bool]) -> Callable[[Job], None]:
        """A job that runs again goes over its tab's connection as it is now: after a reconnect that is a new one."""
        def acting(job: Job) -> None:
            if self._why_not(job):
                return  # never over a connection that was closed, or is not connected
            tab = self._tab_of(job)
            if tab is not None and tab.remote is not None and job.state != State.RUNNING:
                job.remote = tab.remote
            if act(job):
                self._relink_edit(job)  # a save from Edit on its way again, only when it really is

        return acting

    def _limit_selected(self) -> None:
        chosen = [job for job in self.queue_view.selected_jobs() if job.state not in ENDED]
        if not chosen:
            return
        dialog = LimitDialog(chosen[0].limit, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted.value
        limit = dialog.chosen()
        dialog.deleteLater()
        if accepted:
            for job in chosen:
                self.queue.set_limit(job, limit)

    def queue_menu(self) -> QMenu:
        """The right-click menu of the queue: the same actions as its buttons, greyed alike."""
        self.update_queue_actions()
        menu = QMenu(self)
        for name, _text, _icon, _tip, _with_text in QUEUE_ACTIONS:
            menu.addAction(self.queue_panel.actions_by_name[name])
        return menu

    def _queue_menu_wanted(self, place: QPoint) -> None:
        menu = self.queue_menu()
        open_menu(menu, self.queue_view.viewport().mapToGlobal(place))
        menu.deleteLater()

    @override
    def update_queue_actions(self) -> None:
        """Each button only where it applies to the selection, greyed elsewhere, never hidden (OR-20)."""
        chosen = self.queue_view.selected_jobs()
        jobs = self.queue.jobs
        open_chosen = [job for job in chosen if not self._why_not(job)]
        pausing = [job for job in chosen if job.state == State.RUNNING and job.after_stop == State.PAUSED and not self._why_not(job)]
        enabled = {
            "resume": any(job.state == State.PAUSED for job in open_chosen) or bool(pausing),
            "pause": any(pausable(job) for job in chosen),
            "stop": any(stoppable(job) for job in chosen),
            "move_up": self.queue.can_move_together(chosen, -1),
            "move_down": self.queue.can_move_together(chosen, 1),
            "run_now": len(open_chosen) == 1 and len(chosen) == 1 and chosen[0].state in LINE,
            "retry": any(job.state in (State.FAILED, State.STOPPED) for job in open_chosen),
            "stop_all": any(stoppable(job) for job in jobs),
            "limit": any(job.state not in ENDED for job in chosen),
            "delete": any(not job.delete_asked for job in chosen),
            "pause_all": any(pausable(job) for job in jobs),
            "resume_all": any((job.state == State.PAUSED or (job.state == State.RUNNING and job.after_stop == State.PAUSED)) and not self._why_not(job) for job in jobs),
            "clear_finished": any(job.state in ENDED for job in jobs),
        }
        could = [job for job in chosen if job.state in (State.PAUSED, State.FAILED, State.STOPPED)]  # what could otherwise go on
        why = self._why_not(could[0]) if could and all(self._why_not(job) for job in could) else ""  # say why it can't (OR-7)
        for name, _text, _icon, tip, _with_text in QUEUE_ACTIONS:
            action = self.queue_panel.actions_by_name[name]
            action.setEnabled(enabled[name])
            action.setToolTip(why if why and name in ("resume", "retry", "run_now") else tip)

    def _job_removed(self, job: Job) -> None:
        self.queue_view.forget(job)
        self._edit_job_ended(job)  # a save deleted from the queue has ended too
        self.update_actions()

    def _start_by_themselves_changed(self, on: bool) -> None:
        if on == self.settings.start_by_themselves:
            return
        self.settings = replace(self.settings, start_by_themselves=on)
        self._apply_queue_settings()
        try:
            saved_settings.save(self.settings)
        except OSError:
            self.status.showMessage(SETTINGS_NOT_KEPT_TEXT)

    def _at_once_changed(self, value: int) -> None:
        """Fewer lets the running items finish; more starts waiting items at once. Kept in Settings."""
        if value == self.settings.transfers_at_once:
            return
        self.settings = replace(self.settings, transfers_at_once=value)
        self._apply_queue_settings()
        try:
            saved_settings.save(self.settings)
        except OSError:
            self.status.showMessage(SETTINGS_NOT_KEPT_TEXT)

    @override
    def _apply_queue_settings(self) -> None:
        self.queue.settings(self.settings.start_by_themselves, self.settings.keep_done_seconds, self.settings.pause_on_error, self.settings.transfers_at_once)
        box = self.queue_panel.start_by_themselves
        if box.isChecked() != self.settings.start_by_themselves:
            box.blockSignals(True)
            box.setChecked(self.settings.start_by_themselves)
            box.blockSignals(False)
        number = self.queue_panel.at_once
        if number.value() != self.settings.transfers_at_once:
            number.blockSignals(True)
            number.setValue(self.settings.transfers_at_once)
            number.blockSignals(False)

    # --- when the queue is done (FC-04)

    def _queue_finished(self, used: list[RemoteConnection], failed: int, by_itself: bool = True) -> None:
        """The chosen step, once; the choice goes back to Stay at once, also when its countdown is cancelled."""
        choice = self.queue_panel.when_done.choice()
        if choice == STAY or self._transfers_running():
            return  # nothing chosen, or work arrived meanwhile: the choice stays for when that is done
        self.queue_panel.when_done.back_to_stay()
        self._pin_toggled(self.queue_panel.pin.isChecked())  # nothing armed any more: the queue goes as usual once it is idle
        if not by_itself:
            self.status.showMessage(STOPPED_TEXT)  # the person stopped or deleted the last items: they took over
            return
        if choice == DISCONNECT:
            for session in self.sessions:
                if session.alive and any(session.remote is remote for remote in used):
                    self._disconnect(session)
            return
        if choice in (SUSPEND, POWER_OFF) and not self.power.possible(choice):
            self.status.showMessage(NOT_ALLOWED_TEXTS[choice])  # it became impossible meanwhile: said, calmly
            return
        if self._held_by_edits(choice):
            return
        self.countdown = Countdown(choice, failed, lambda: self._after_countdown(choice), lambda: not self._transfers_running(), lambda: self.status.showMessage(CALLED_OFF_TEXT), self)
        self.countdown.show()

    def _held_by_edits(self, choice: str) -> bool:
        """Edited files that are not on the server yet keep Chrono FTP open, and the computer on: their copies here may be the only ones.
        Suspending loses nothing, so it goes ahead."""
        unsent = sum(1 for edited in self.edited.values() if edited.pending)
        if choice not in (CLOSE, POWER_OFF) or not unsent:
            return False
        self.status.showMessage(EDITS_TEXTS[choice][0 if unsent == 1 else 1].format(count=unsent))
        return True

    def _after_countdown(self, choice: str) -> None:
        self.countdown = None
        if self._transfers_running() or self._held_by_edits(choice):
            return
        if choice == CLOSE:
            self.closing_when_done = True  # the person chose this: closing asks nothing more, as nothing is left unsent
            if not self.close():
                self.closing_when_done = False
            return
        if not self.power.possible(choice):
            self.status.showMessage(NOT_ALLOWED_TEXTS[choice])
            return
        problem = self.power.act(choice)
        if problem:
            QMessageBox.information(self, WINDOW_TITLE, box_text(NOT_DONE_TEXT.format(reason=problem)))

    def _disconnect(self, session: Session) -> None:
        """The connection ends; its tab goes grey, and Reconnect brings it back (OR-3)."""
        remote = session.remote
        session.alive = False
        if remote is not None:
            session.worker.submit(remote.close, lambda _nothing: None, lambda _error: None)
        self._set_tab(session)
        self.update_actions()
        self.status.showMessage(DISCONNECTED_TEXT.format(name=session.connection.name))
