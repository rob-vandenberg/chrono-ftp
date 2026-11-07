import os
import signal
import sys
from collections.abc import Generator
from pathlib import Path
from types import FrameType

import pytest

from chrono_ftp.core import credentials

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: No test touches the real connection list or credential store
# v0.0.3: The window tests get their plugin (tests/ui/uiplugin.py)
# v0.0.1: Initial: no test may take longer than 20 seconds (AGENTS.md 4.4.4)

pytest_plugins = ["tests.ui.uiplugin"]

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


class MemoryStore:
    """The credential store of a test: nothing leaves the process."""

    def __init__(self) -> None:
        self.passwords: dict[str, str] = {}

    def read(self, name: str) -> str | None:
        return self.passwords.get(name)

    def write(self, name: str, password: str) -> None:
        self.passwords[name] = password

    def remove(self, name: str) -> None:
        self.passwords.pop(name, None)


@pytest.fixture(autouse=True)
def memory_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MemoryStore:
    store = MemoryStore()
    monkeypatch.setattr(credentials, "store", store)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return store
