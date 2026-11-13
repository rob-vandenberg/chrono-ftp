"""The one button that starts work and, while that work runs, aborts it (D-097), and the words every window of work on this computer
says. There is one of each: the Synchronize window and the paste of files on this computer both use these."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QWidget

from chrono_ftp.ui.plaintext import was

# --- Version ------------------------------------------------------------
__version__ = "startabort.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 4: every sentence with a number reads as a person writes it, also for one and for none (OR-23)
# v0.0.45: Initial: the Start/Abort button and the words of work on this computer, pulled out of the Synchronize window so the paste of
#          files on this computer uses the same one (D-097, D-099)

START_TEXT: str = "Start"  # the one word a person expects for starting the work (D-097)
ABORT_TEXT: str = "Abort"  # the same button while it runs: one button, naming the one action that applies (OR-20, D-097)
ABORT_TIP: str = "Stops after the file it is busy with; what was done stays done."
WORKING_TEXT: str = "Doing {done} of {count}: {name}"
ONE_DONE_TEXT: str = "Finished: the one thing was done."
ONE_FAILED_TEXT: str = "Finished: the one thing could not be done."
ALL_DONE_TEXT: str = "Finished: all {count} were done."
SOME_DONE_TEXT: str = "Finished: {done} of {count} {was} done; {failed} could not be."
NOTHING_DONE_TEXT: str = "Aborted: nothing was done; what was to come was not touched."
ABORTED_TEXT: str = "Aborted: {done} of {count} {was_done} done, and {left} {was_left} not. What was done stays done."


def finished_text(done: int, count: int, failed: int) -> str:
    """What a run says when it is through, in the English a person writes, whatever the numbers are (OR-23)."""
    if count == 1:
        return ONE_DONE_TEXT if done else ONE_FAILED_TEXT
    if not failed:
        return ALL_DONE_TEXT.format(count=count)
    return SOME_DONE_TEXT.format(done=done, count=count, was=was(done), failed=failed)


def aborted_text(done: int, count: int, left: int) -> str:
    """The same for a run the person aborted."""
    if not done:
        return NOTHING_DONE_TEXT
    return ABORTED_TEXT.format(done=done, count=count, was_done=was(done), left=left, was_left=was(left))


class StartAbortButton(QPushButton):
    """One button: Start, and Abort while the work runs. Greyed with its reason where there is nothing to start (OR-20)."""

    start_requested = Signal()
    abort_requested = Signal()

    def __init__(self, start_tip: str = "", parent: QWidget | None = None) -> None:
        super().__init__(START_TEXT, parent)
        self.running: bool = False
        self.start_tip: str = start_tip
        self.setToolTip(start_tip)
        self.clicked.connect(self._pressed)

    def _pressed(self) -> None:
        (self.abort_requested if self.running else self.start_requested).emit()

    def show_running(self, running: bool) -> None:
        self.running = running
        if running:
            self.setText(ABORT_TEXT)
            self.setToolTip(ABORT_TIP)
            self.setEnabled(True)

    def show_ready(self, why: str = "", start_tip: str | None = None) -> None:
        """why: why there is nothing to start now; it is then greyed and says that (OR-20)."""
        if start_tip is not None:
            self.start_tip = start_tip
        self.running = False
        self.setText(START_TEXT)
        self.setEnabled(not why)
        self.setToolTip(why or self.start_tip)
