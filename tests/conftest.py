import os
import signal
import sys
from collections.abc import Generator
from types import FrameType

import pytest

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 0.0.1"

# --- Version history ----------------------------------------------------
# v0.0.1: Initial: no test may take longer than 20 seconds (AGENTS.md 4.4.4)

# Qt runs without a screen in the tests; set before anything creates the application object.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

TEST_TIME_LIMIT_SECONDS: int = 20  # no test should ever need longer; a test may never hang


class TestTookTooLong(Exception):  # noqa: N818 - the name says what happened
    pass


def _too_long(signum: int, frame: FrameType | None) -> None:
    raise TestTookTooLong(f"the test took longer than {TEST_TIME_LIMIT_SECONDS} s")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Generator[None]:
    """Every test, its setup and its clean-up together, within the limit."""
    if sys.platform == "win32":
        yield  # Windows has no alarm (the printed waits still show a hang)
    else:
        earlier = signal.signal(signal.SIGALRM, _too_long)
        signal.setitimer(signal.ITIMER_REAL, TEST_TIME_LIMIT_SECONDS)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, earlier)
