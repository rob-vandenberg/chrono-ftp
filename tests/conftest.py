import os
import shutil
import signal
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from types import FrameType

import pytest

from chrono_ftp.core import credentials
from tests.fakeservers import end_the_servers

# --- Version ------------------------------------------------------------
__version__ = "conftest.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: A fake SSH server whose block ended while a window still held a connection to it ends once the test is over
# v0.0.12: The whole run has settings folders of its own, so a thread that outlives its test never reaches the real ones; the run fails
#          when the real settings folder of Chrono FTP gained or changed anything; nor the real data folder, also when it appears; each
#          test has a home folder of the run, so no test reads this computer's own known hosts
# v0.0.6: Nor the real data folder
# v0.0.5: No test touches the real connection list or credential store
# v0.0.3: The window tests get their plugin (tests/ui/uiplugin.py)
# v0.0.1: Initial: no test may take longer than 20 seconds (AGENTS.md 4.4.4)

pytest_plugins = ["tests.ui.uiplugin"]

# Qt runs without a screen in the tests; set before anything creates the application object.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The real settings and data folders of Chrono FTP, as the person's environment names them: only looked at, never written.
REAL_HOME: Path = Path.home()
REAL_SETTINGS: Path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "chrono-ftp"
REAL_DATA: Path = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "chrono-ftp"
THERE: tuple[int, int] = (-1, -1)  # what a snapshot notes for the folder itself, so a folder that appears, even empty, is a change


def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    """The folder itself and every file under it with its size and change time; empty when the folder is not there."""
    found: dict[str, tuple[int, int]] = {".": THERE} if os.path.lexists(folder) else {}
    for path in sorted(folder.rglob("*")) if folder.is_dir() else []:
        details = path.lstat()
        found[str(path.relative_to(folder))] = (details.st_size, details.st_mtime_ns)
    return found


REAL_FOLDERS_BEFORE: dict[Path, dict[str, tuple[int, int]]] = {folder: _snapshot(folder) for folder in (REAL_SETTINGS, REAL_DATA)}

# Settings folders of this run, set before any test or thread can ask for one: a test sets its own on top, and what it puts back when it
# ends is this, never the real one, also for a transfer thread that is still ending after its test.
RUN_SETTINGS: Path = Path(tempfile.mkdtemp(prefix="chrono-ftp-tests-"))
os.environ["XDG_CONFIG_HOME"] = str(RUN_SETTINGS / "config")
os.environ["XDG_DATA_HOME"] = str(RUN_SETTINGS / "data")
RUN_HOME: Path = RUN_SETTINGS / "home"  # the home folder of every test but the live ones: no ~/.ssh/known_hosts of this computer
RUN_HOME.mkdir()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """The run fails when a real folder of Chrono FTP appeared, went, or gained, lost or changed a file while it ran."""
    shutil.rmtree(RUN_SETTINGS, ignore_errors=True)
    for folder, before in REAL_FOLDERS_BEFORE.items():
        after = _snapshot(folder)
        if after != before:
            changed = sorted(name for name in set(after) | set(before) if after.get(name) != before.get(name))
            sys.stderr.write(f"\nThe real folder {folder} changed during the tests: {', '.join(changed)}\n")
            session.exitstatus = pytest.ExitCode.TESTS_FAILED

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
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return store


@pytest.fixture(autouse=True)
def home_of_the_run(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Whether a test's server is trusted depends on the test alone, never on this computer's ~/.ssh/known_hosts. The live tests keep
    the real home folder: they reach a real server with this computer's own key and known hosts."""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.setenv("HOME", str(RUN_HOME))


@pytest.fixture(autouse=True)
def fake_servers_end() -> Generator[None]:
    """Set up before the test's own fixtures, so it ends after them: a window has closed its connections by then (AGENTS.md 4.4.4)."""
    yield
    end_the_servers()
