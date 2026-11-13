import queue
import threading
from collections.abc import Callable

# --- Version ------------------------------------------------------------
__version__ = "jobs.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Whether a worker is idle, and whether anything waits behind the call that runs, so the thumbnails give way at once (FQ-02)
# v0.0.5: Initial: work on a connection runs on its own thread, so the window never freezes (D-019)

Deliver = Callable[[Callable[[], None]], None]  # hands a call to the window's thread


class Worker:
    """One thread for one connection: its requests run one after the other, and each answers exactly once. The thread never keeps the
    program alive, so quitting during a long command on a server just quits."""

    def __init__(self, deliver: Deliver) -> None:
        self._deliver: Deliver = deliver
        self._waiting: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._stopped: bool = False
        self._lock: threading.Lock = threading.Lock()
        self._open: int = 0  # calls handed in and not yet ended
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while (call := self._waiting.get()) is not None:
            call()

    def submit[T](self, work: Callable[[], T], done: Callable[[T], None], failed: Callable[[Exception], None]) -> None:
        if self._stopped:
            return  # the tab is gone: nobody waits for an answer

        def run() -> None:
            try:
                result = work()
            except Exception as error:  # every failure goes to the window as an answer, never lost on this thread
                problem = error
                self._deliver(lambda: failed(problem))
                return
            finally:
                with self._lock:
                    self._open -= 1
            self._deliver(lambda: done(result))

        with self._lock:
            self._open += 1
        self._waiting.put(run)

    def idle(self) -> bool:
        """Nothing runs and nothing waits."""
        with self._lock:
            return self._open == 0 and not self._stopped

    def others_waiting(self) -> bool:
        """Another call waits behind the one that runs: work that gives way stops for it (FQ-02). Safe on any thread."""
        return not self._waiting.empty()

    def finish_with(self, last: Callable[[], None]) -> None:
        """What is waiting still runs, then this last call, and then the thread ends. Safe to call twice."""
        if self._stopped:
            return
        self._stopped = True

        def run() -> None:
            try:
                last()
            except Exception:  # closing a connection that is already gone
                return

        self._waiting.put(run)
        self._waiting.put(None)

    def stop(self) -> None:
        self.finish_with(lambda: None)
