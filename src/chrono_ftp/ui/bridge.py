import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, Signal

# --- Version ------------------------------------------------------------
__version__ = "bridge.py 0.0.11"

# --- Version history ----------------------------------------------------
# v0.0.11: Closed with the window: an answer that comes later, or is still on its way, goes nowhere instead of to a deleted window
# v0.0.5: Initial: answers of other threads reach the window on the window's own thread


class Bridge(QObject):
    arrived = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock: threading.Lock = threading.Lock()  # a thread that is handing in an answer finishes it before close() returns
        self._closed: bool = False
        self.arrived.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def deliver(self, call: Callable[[], None]) -> None:
        with self._lock:
            if not self._closed:
                self.arrived.emit(call)

    def close(self) -> None:
        """The window has closed: nothing more happens in it, so the answers of its threads have nobody to go to."""
        with self._lock:
            self._closed = True

    def _run(self, call: Callable[[], None]) -> None:
        if not self._closed:
            call()
