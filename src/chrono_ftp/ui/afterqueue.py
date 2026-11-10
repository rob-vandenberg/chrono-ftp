"""What happens when the queue is done (FC-04): the choices, the computer's own suspend and shut down through logind, and the window that
counts down before Chrono FTP closes or the computer sleeps or shuts down, so nobody is surprised (G-2)."""

from collections.abc import Callable
from typing import override

from PySide6.QtCore import QTimer
from PySide6.QtDBus import QDBusConnection, QDBusMessage
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

# --- Version ------------------------------------------------------------
__version__ = "afterqueue.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial: stay, disconnect, close Chrono FTP, suspend or shut down when the queue is done, once; suspend and shut down through
#          logind, greyed where it says they can't be done; a window counts down first, with Cancel; "1 second"

STAY: str = "stay"
DISCONNECT: str = "disconnect"
CLOSE: str = "close"
SUSPEND: str = "Suspend"  # logind's own names: Can<name> asks, <name> does it
POWER_OFF: str = "PowerOff"
CHOICES: tuple[tuple[str, str], ...] = (
    (STAY, "Stay"),
    (DISCONNECT, "Disconnect"),
    (CLOSE, "Close Chrono FTP"),
    (SUSPEND, "Suspend the computer"),
    (POWER_OFF, "Shut down the computer"),
)
CHOICE_TIPS: dict[str, str] = {
    STAY: "Nothing happens when the queue is done",
    DISCONNECT: "The connections the queue used are disconnected when it is done; Reconnect brings them back",
    CLOSE: "Chrono FTP closes when the queue is done, after a countdown you can cancel",
    SUSPEND: "The computer goes to sleep when the queue is done, after a countdown you can cancel",
    POWER_OFF: "The computer shuts down when the queue is done, after a countdown you can cancel",
}
NOT_POSSIBLE_TIPS: dict[str, str] = {
    SUSPEND: "This computer does not let Chrono FTP suspend it now",
    POWER_OFF: "This computer does not let Chrono FTP shut it down now",
}
WHEN_DONE_TIP: str = "What happens once every item in the queue has ended. It applies once, then goes back to Stay."
COUNTDOWN_SECONDS: int = 30
COUNTDOWN_TEXTS: dict[str, tuple[str, str]] = {  # for 1 second, and for more
    CLOSE: ("The queue is done. Chrono FTP closes in 1 second.", "The queue is done. Chrono FTP closes in {seconds} seconds."),
    SUSPEND: ("The queue is done. The computer goes to sleep in 1 second.", "The queue is done. The computer goes to sleep in {seconds} seconds."),
    POWER_OFF: ("The queue is done. The computer shuts down in 1 second.", "The queue is done. The computer shuts down in {seconds} seconds."),
}
FAILED_TEXTS: tuple[str, str] = ("1 item could not be transferred.", "{count} items could not be transferred.")
CANCEL_TIP: str = "Nothing happens; Chrono FTP and the computer stay as they are"
COUNTDOWN_TITLE: str = "Queue done"
LOGIN_SERVICE: str = "org.freedesktop.login1"
LOGIN_PATH: str = "/org/freedesktop/login1"
LOGIN_INTERFACE: str = "org.freedesktop.login1.Manager"
NOT_DONE_TEXT: str = "The computer could not be asked to do this ({reason}), so it stays as it is."


def failed_text(count: int) -> str:
    if not count:
        return ""
    return FAILED_TEXTS[0] if count == 1 else FAILED_TEXTS[1].format(count=count)


class Power:
    """The computer's own suspend and shut down, through logind on the system bus. Never interactive: logind may not ask for a password
    while nobody may be at the computer, so what needs one is not possible here."""

    def _call(self, method: str, *arguments: object) -> QDBusMessage:
        message = QDBusMessage.createMethodCall(LOGIN_SERVICE, LOGIN_PATH, LOGIN_INTERFACE, method)
        if arguments:
            message.setArguments(list(arguments))
        return QDBusConnection.systemBus().call(message)

    def possible(self, action: str) -> bool:
        """Whether logind says the action can be done now, without asking anyone ("yes"; never "challenge", "no" or "na")."""
        reply = self._call(f"Can{action}")
        found = reply.arguments()
        return reply.type() == QDBusMessage.MessageType.ReplyMessage and bool(found) and found[0] == "yes"

    def act(self, action: str) -> str:
        """Suspends or shuts down the computer; the reason when that could not be asked for, else empty."""
        reply = self._call(action, False)  # interactive false: never a password question
        if reply.type() == QDBusMessage.MessageType.ErrorMessage:
            return reply.errorMessage() or reply.errorName() or "no answer"
        return ""


class Countdown(QDialog):
    """Counts down before Chrono FTP closes or the computer sleeps or shuts down; Cancel calls it off. due runs at the end. still_wanted is
    asked every second: when the queue has work again, it is called off by itself, and gone runs."""

    def __init__(self, action: str, failed: int, due: Callable[[], None], still_wanted: Callable[[], bool], gone: Callable[[], None], parent: QWidget | None = None, seconds: int = COUNTDOWN_SECONDS) -> None:
        super().__init__(parent)
        self.setWindowTitle(COUNTDOWN_TITLE)
        self.action: str = action
        self.seconds: int = seconds
        self.due: Callable[[], None] = due
        self.still_wanted: Callable[[], bool] = still_wanted
        self.gone: Callable[[], None] = gone
        self.label = QLabel()
        self.failed = QLabel(failed_text(failed))
        self.failed.setVisible(bool(failed))
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setToolTip(CANCEL_TIP)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.failed)
        layout.addWidget(self.buttons)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self._show_seconds()
        self.timer.start()

    def _show_seconds(self) -> None:
        one, more = COUNTDOWN_TEXTS[self.action]
        self.label.setText(one if self.seconds == 1 else more.format(seconds=self.seconds))

    def tick(self) -> None:
        if not self.still_wanted():
            self.timer.stop()
            self.reject()
            self.gone()
            return
        self.seconds -= 1
        if self.seconds > 0:
            self._show_seconds()
            return
        self.timer.stop()
        self.accept()
        self.due()

    @override
    def reject(self) -> None:
        self.timer.stop()
        super().reject()
