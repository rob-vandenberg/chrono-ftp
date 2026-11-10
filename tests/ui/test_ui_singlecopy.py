from pathlib import Path

from PySide6.QtCore import QDir
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from chrono_ftp.ui.singlecopy import SingleCopy, tell_running_copy

# --- Version ------------------------------------------------------------
__version__ = "test_ui_singlecopy.py 0.0.25"

# --- Version history ----------------------------------------------------
# v0.0.25: Initial: a second start brings the running copy forward; nothing running or a socket left behind blocks nothing


def name_for(tmp_path: Path) -> str:
    return f"chrono-ftp-test-{tmp_path.name}"  # its own name per test: never the real program's


def test_a_second_start_brings_the_running_copy_forward(qtbot: QtBot, tmp_path: Path) -> None:
    told: list[str] = []
    running = SingleCopy(name_for(tmp_path), lambda: told.append("forward"))
    try:
        assert running.listening()
        assert tell_running_copy(name_for(tmp_path))
        qtbot.waitUntil(lambda: told == ["forward"])
    finally:
        running.close()


def test_without_a_running_copy_the_start_goes_on(qapp: QApplication, tmp_path: Path) -> None:
    assert not tell_running_copy(name_for(tmp_path))


def test_a_socket_left_behind_by_a_crash_blocks_nothing(qapp: QApplication, tmp_path: Path) -> None:
    left = Path(QDir.tempPath()) / name_for(tmp_path)
    left.write_text("", encoding="utf-8")  # what a crashed copy leaves: a socket file nobody listens on
    running = SingleCopy(name_for(tmp_path), lambda: None)
    try:
        assert running.listening()
        assert not tell_running_copy(f"{name_for(tmp_path)}-other")
    finally:
        running.close()
        left.unlink(missing_ok=True)
