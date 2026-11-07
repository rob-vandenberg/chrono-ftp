"""The queue under the sides of the main window: shown while transfers run, hidden when idle unless pinned."""

from typing import override

from chrono_ftp.transfers.queue import Job, State
from chrono_ftp.ui.windowbase import WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowqueue.py 0.0.11"

# --- Version history ----------------------------------------------------
# v0.0.11: Initial: moved here from mainwindow.py, unchanged (D-041)


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

    def _transfers_running(self) -> bool:
        return any(job.state in (State.WAITING, State.RUNNING) for job in self.queue.jobs)

    def _hide_idle_queue(self) -> None:
        if not (self.queue_panel.pin.isChecked() or self.failure_unseen or self._transfers_running()):
            self._hide_queue()

    def _pin_toggled(self, pinned: bool) -> None:
        if pinned:
            self.queue_linger.stop()
        elif not self.failure_unseen and not self._transfers_running():
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
        elif not running and not self.queue_panel.pin.isChecked() and not self.failure_unseen:
            self.queue_linger.start()
