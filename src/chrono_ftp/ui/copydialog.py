"""The window of a paste (FI-03), on this computer and on a server alike: it says what will happen before anything happens, has the one
button that reads Start and then Abort (D-097), names the file it is copying while it runs, and says at the end what was done and what
was not, naming every item that could not be done and why (G-2)."""

from typing import override

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QProgressBar, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.engine.copyplan import Copy
from chrono_ftp.ui.plaintext import plain_label
from chrono_ftp.ui.startabort import StartAbortButton

# --- Version ------------------------------------------------------------
__version__ = "copydialog.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 4: "This can't be copied" for one, "These" for more (OR-23)
# v0.0.45: Review round 1: the window of a paste on this computer and on a server; it says beforehand which items are copied under a
#          copy name and which can't be done and why, names the file it is busy with while it copies, and says "1 thing" as a person
#          says it (FI-03, D-097, D-099)

WINDOW_TITLE: str = "Copy Files Here"
TO_DO_TEXTS: tuple[str, str] = ("1 thing goes to {folder}.", "{count} things go to {folder}.")
BESIDE_TEXTS: tuple[str, str] = (' One of them is already in that folder, so it is copied beside it as "{name}".', ' {count} of them are already in that folder, so they are copied beside them, as "{name}" and so on.')
CANT_TEXTS: tuple[str, str] = (" This can't be copied: {problems}.", " These can't be copied: {problems}.")
NOTHING_TO_DO_TEXT: str = "There is nothing here that can be copied."
NOTHING_YET_TEXT: str = " Nothing happens until you press Start."
START_TIP: str = "Copies them into that folder. Nothing happens before you press this."
NOTHING_TO_DO_WHY: str = "There is nothing to copy."
AFTER_A_RUN_WHY: str = "These were copied already. Close this window and press Ctrl+V again for another copy."
WORKING_TEXT: str = "Copying {number} of {count}: {name}"
CLOSE_TEXT: str = "Close"


def problem_lines(problems: list[tuple[str, str]]) -> str:
    """Every item that could not be done, with its reason: "a.txt (a folder with this name is there)"."""
    return "; ".join(f"{shown_name(name)} ({why})" for name, why in problems)


class CopyDialog(QDialog):
    """Closing it while the work runs aborts that work, so nothing is ever left running where nobody can stop it (D-097)."""

    start_requested = Signal()
    work_stopped = Signal()

    def __init__(self, folder: str, plans: list[Copy], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.running: bool = False
        self.ran: bool = False
        self.doable: int = len([plan for plan in plans if not plan.why_not])
        self.status = plain_label(self._what_will_happen(folder, plans))
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.hide()
        self.start_button = StartAbortButton(START_TIP)
        self.close_button = QPushButton(CLOSE_TEXT)
        self.close_button.setAutoDefault(False)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.start_button)
        row.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addLayout(row)
        self.start_button.start_requested.connect(self._start_pressed)
        self.start_button.abort_requested.connect(self.work_stopped)
        self.close_button.clicked.connect(self.close)
        self._start_state()

    def _what_will_happen(self, folder: str, plans: list[Copy]) -> str:
        """What the person must know before pressing Start: how many go where, which get a copy name, and what can't be done (G-2)."""
        if not plans:
            return NOTHING_TO_DO_TEXT
        beside = [plan for plan in plans if plan.beside and not plan.why_not]
        refused = [(plan.name, plan.why_not) for plan in plans if plan.why_not]
        text = (TO_DO_TEXTS[0] if self.doable == 1 else TO_DO_TEXTS[1]).format(count=self.doable, folder=shown_name(folder)) if self.doable else NOTHING_TO_DO_TEXT
        if beside:
            text += (BESIDE_TEXTS[0] if len(beside) == 1 else BESIDE_TEXTS[1]).format(count=len(beside), name=shown_name(beside[0].name))
        if refused:
            text += (CANT_TEXTS[0] if len(refused) == 1 else CANT_TEXTS[1]).format(problems=problem_lines(refused))
        return text + (NOTHING_YET_TEXT if self.doable else "")

    def _start_pressed(self) -> None:
        self.start_requested.emit()

    def _start_state(self) -> None:
        if self.running:
            self.start_button.show_running(True)
            return
        self.start_button.show_ready(AFTER_A_RUN_WHY if self.ran else "" if self.doable else NOTHING_TO_DO_WHY)

    def work_started(self, count: int) -> None:
        self.running = True
        self.ran = True
        self.progress.setRange(0, max(1, count))
        self.progress.setValue(0)
        self.progress.show()
        self._start_state()

    def work_doing(self, number: int, count: int, name: str) -> None:
        """The item that is being copied now, named from the moment it starts, never the one before it."""
        self.progress.setValue(number - 1)
        self.status.setText(WORKING_TEXT.format(number=number, count=count, name=shown_name(name)))

    def work_ended(self, text: str) -> None:
        """What was done and what was not, with the window still on screen to read it (G-2)."""
        self.running = False
        self.progress.hide()
        self.status.setText(text)
        self._start_state()

    @override
    def done(self, result: int) -> None:
        if self.running:
            self.work_stopped.emit()  # closing the window aborts the work it started (D-097)
        super().done(result)
