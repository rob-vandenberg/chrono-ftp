import os
from pathlib import Path

from chrono_ftp.connections.base import RemoteEntry, safe_name
from chrono_ftp.core.errors import LocalActionFailed

# --- Version ------------------------------------------------------------
__version__ = "localfiles.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial: reading a local folder and the actions on this computer; os, not Qt, so a name that is not valid UTF-8 is listed too

TARGET_EXISTS_TEXT: str = "There is already something with this name here."
NOT_A_NAME_TEXT: str = "A name can't hold a slash."


def _entry(item: os.DirEntry[str]) -> RemoteEntry:
    is_link = item.is_symlink()
    try:
        is_folder = item.is_dir()
        status = item.stat(follow_symlinks=False)
        target = os.readlink(item.path) if is_link else ""
    except OSError:
        return RemoteEntry(item.name, False, is_link=is_link)  # gone in the meantime, or a broken link
    return RemoteEntry(item.name, is_folder, status.st_size, status.st_mtime, format(status.st_mode & 0o7777, "04o"), is_link=is_link, link_target=target)


def read_folder(folder: str) -> list[RemoteEntry] | None:
    """None: the folder can't be opened."""
    if not os.access(folder, os.R_OK | os.X_OK):
        return None
    try:
        with os.scandir(folder) as items:
            return [_entry(item) for item in items]
    except OSError:
        return None


def make_folder(folder: str, name: str) -> None:
    if not safe_name(name):
        raise LocalActionFailed(NOT_A_NAME_TEXT)
    try:
        Path(folder, name).mkdir()
    except FileExistsError as error:
        raise LocalActionFailed(TARGET_EXISTS_TEXT) from error
    except OSError as error:
        raise LocalActionFailed(error.strerror or "This folder could not be made.") from error


def rename(folder: str, old: str, new: str) -> None:
    if not safe_name(new):
        raise LocalActionFailed(NOT_A_NAME_TEXT)
    target = Path(folder, new)
    if os.path.lexists(target):  # os.rename would replace it without a word
        raise LocalActionFailed(TARGET_EXISTS_TEXT)
    try:
        Path(folder, old).rename(target)
    except OSError as error:
        raise LocalActionFailed(error.strerror or "This name could not be changed.") from error
