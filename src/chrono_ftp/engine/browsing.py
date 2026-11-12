"""Synchronized browsing (FH-05): the step one side made, made the same on the other side."""

import os
import posixpath
from pathlib import PurePath, PurePosixPath

# --- Version ------------------------------------------------------------
__version__ = "browsing.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial: the folder on the other side that matches a step of one side; a typed path read the same way for Ctrl+L and Ctrl+O; a
#          local starting folder read from the home folder


def matching_folder(previous: str, new: str, other: str, moved_is_local: bool) -> str | None:
    """One side went from previous to new; the other side is in other. The same step there: as many folders up as the step went up
    from previous to the part previous and new share, then down into the folders new adds. None when the other side can't go up that
    far, or when the two paths share nothing (then the steps can no longer be matched)."""
    moved: type[PurePath] = PurePath if moved_is_local else PurePosixPath
    there: type[PurePath] = PurePosixPath if moved_is_local else PurePath
    before, after = moved(previous).parts, moved(new).parts
    shared = 0
    while shared < min(len(before), len(after)) and before[shared] == after[shared]:
        shared += 1
    if shared == 0:
        return None
    target = there(other)
    for _step in range(len(before) - shared):
        if target.parent == target:
            return None
        target = target.parent
    for part in after[shared:]:
        target = target / part
    return str(target)


HOME_MARK: str = "~"


def typed_local_path(shown: str, typed: str) -> str:
    """A path typed for this computer (Ctrl+L, Ctrl+O): ~ is the home folder, a relative path starts at the folder shown, . and .. are
    taken out."""
    return os.path.normpath(os.path.join(shown, os.path.expanduser(typed.strip())))


def typed_remote_path(shown: str, typed: str, home: str) -> str:
    """A path typed for the server (Ctrl+L, Ctrl+O): ~ is the home folder of the login, a relative path starts at the folder shown, . and
    .. are taken out, as the Parent folder button does."""
    text = typed.strip()
    if home and (text == HOME_MARK or text.startswith(HOME_MARK + "/")):
        text = home + text[len(HOME_MARK) :]
    elif not text.startswith("/"):
        text = posixpath.join(shown or "/", text)
    found = posixpath.normpath(text)
    return "/" + found.lstrip("/") if found.startswith("/") else found


def local_start_folder(text: str) -> str:
    """A local starting folder as kept with a connection: ~ expanded, a relative one read from the home folder, absolute and normalised;
    empty stays empty (the left side then stays where it is)."""
    return typed_local_path(os.path.expanduser(HOME_MARK), text) if text.strip() else ""
