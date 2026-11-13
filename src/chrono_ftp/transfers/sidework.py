"""What Apply of the Synchronize window sends to the queue beside its copies (FF-10): the deletes, the moves on one side, the dates it
sets, and the copies between two folders on this computer (FF-14). Each is one item of the queue, so the person sees it, can stop it
and can watch it like any transfer; one item that is refused never stops the rest, and the job says afterwards what could not be done."""

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from chrono_ftp.connections.base import RemoteConnection, parent_of
from chrono_ftp.core.errors import ChronoFtpError, ConnectionLost, RemoteActionFailed
from chrono_ftp.transfers.job import Job, Stopped

# --- Version ------------------------------------------------------------
__version__ = "sidework.py 0.0.43"

# --- Version history ----------------------------------------------------
# v0.0.43: Initial: the deletes, moves, dates and local copies of Apply, each as one item of the queue (FF-10, D-095)

TARGET_EXISTS_TEXT: str = "There is already something with this name there, so nothing was moved."
TARGET_IS_A_LINK_TEXT: str = "There is a link with this name there, so nothing was copied: a copy would write into whatever it leads to."
PART_DONE_TEXT: str = "{done} of {count} were done; {failed} could not be: {why}"
NONE_DONE_TEXT: str = "None of these {count} could be done: {why}"
NOT_A_FOLDER_TEXT: str = "a folder is in the way of this name"


class SideWork(Enum):
    """What one item of this kind does. The text names the one action it does (OR-20)."""

    DELETE = "Delete"
    MOVE = "Move"
    TOUCH = "Set the date"
    COPY = "Copy"


@dataclass(frozen=True)
class Step:
    """One thing to do: a path, and what is done with it."""

    path: str  # the file or folder it acts on, as the side writes paths
    target: str = ""  # a move or a copy: where it goes
    moment: float | None = None  # a date to set
    is_folder: bool = False


@dataclass(frozen=True)
class SideJob:
    """One queue item that is no transfer: what it does, on which side, and its steps in order."""

    work: SideWork
    on_the_server: bool
    steps: tuple[Step, ...]

    @property
    def count(self) -> int:
        return len(self.steps)


Told = Callable[[int], None]  # how many steps are done, for the progress of the item


def _make_local_folders(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _make_remote_folders(connection: RemoteConnection, path: str) -> None:
    """The folder a move or a copy goes into, and every folder above it that is not there yet, top first."""
    missing: list[str] = []
    folder = parent_of(path)
    while folder not in ("", "/"):
        try:
            if connection.details(folder) is not None:
                break
        except ConnectionLost:
            raise
        except ChronoFtpError:
            break
        missing.append(folder)
        folder = parent_of(folder)
    for item in reversed(missing):
        connection.make_folder(item)


def _on_the_server(connection: RemoteConnection, work: SideWork, step: Step) -> None:
    if work == SideWork.DELETE:
        connection.delete(step.path, step.is_folder)
        return
    if work == SideWork.TOUCH:
        if step.moment is not None:
            connection.set_time(step.path, step.moment)
        return
    if work == SideWork.MOVE:
        if connection.name_taken(step.target):
            raise RemoteActionFailed(TARGET_EXISTS_TEXT)
        _make_remote_folders(connection, step.target)
        connection.rename(step.path, step.target)
        return
    raise RemoteActionFailed(f"{work.value} is not done on the server here.")


def on_this_computer(work: SideWork, step: Step) -> None:
    if work == SideWork.DELETE:
        if step.is_folder and not os.path.islink(step.path):
            shutil.rmtree(step.path)
        else:
            os.remove(step.path)
        return
    if work == SideWork.TOUCH:
        if step.moment is not None:
            os.utime(step.path, (step.moment, step.moment))
        return
    if work == SideWork.MOVE:
        if os.path.lexists(step.target):  # a move never replaces what is there: the person never asked for that
            raise OSError(TARGET_EXISTS_TEXT)
        _make_local_folders(step.target)
        os.rename(step.path, step.target)
        return
    if os.path.islink(step.target):
        raise OSError(TARGET_IS_A_LINK_TEXT)  # writing through it would land in another tree, which the scan never showed
    _make_local_folders(step.target)
    if step.is_folder:
        shutil.copytree(step.path, step.target, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(step.path, step.target)  # a copy replaces the file that is there, as a copy to a server does


def run_side_job(job: Job, side: SideJob, told: Told = lambda _done: None) -> None:
    """Every step in turn. A step that is refused fails only itself (OR-15); a lost connection stops it all. When something could not
    be done the job fails and says how much was done and why, so the person always knows what happened (G-2, OR-23)."""
    done = 0
    problems: list[str] = []
    for step in side.steps:
        if job.stop_asked.is_set():
            raise Stopped
        try:
            if side.on_the_server:
                _on_the_server(job.remote, side.work, step)
            else:
                on_this_computer(side.work, step)
            done += 1
        except ConnectionLost:
            raise
        except (ChronoFtpError, OSError, shutil.Error) as error:
            problems.append(str(getattr(error, "strerror", None) or error) or NOT_A_FOLDER_TEXT)
        told(done)
    if problems:
        why = problems[0]
        raise RemoteActionFailed(NONE_DONE_TEXT.format(count=side.count, why=why) if done == 0 else PART_DONE_TEXT.format(done=done, count=side.count, failed=len(problems), why=why))
