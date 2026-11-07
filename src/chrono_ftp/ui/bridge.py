from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal

# --- Version ------------------------------------------------------------
__version__ = "bridge.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: answers of other threads reach the window on the window's own thread


class Bridge(QObject):
    arrived = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.arrived.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def deliver(self, call: Callable[[], None]) -> None:
        self.arrived.emit(call)

    def _run(self, call: Callable[[], None]) -> None:
        call()
