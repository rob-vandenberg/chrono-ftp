import os
import secrets
import shutil
import stat
import sys
from pathlib import Path

from chrono_ftp.connections.base import RemoteEntry, safe_name
from chrono_ftp.core.errors import LocalActionFailed
from chrono_ftp.engine import rights as rights_model
from chrono_ftp.engine.rights import Rights

if sys.platform != "win32":
    import grp
    import pwd

# --- Version ------------------------------------------------------------
__version__ = "localfiles.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Properties on this computer: owner and group by name, which of them may be changed, the names to offer; rights of several
#          files, also of everything inside, with x for folders where they have r; owner and group changed; a symbolic or hard link
#          made or led elsewhere; a new empty file; a folder made with rights; the file manager on a folder (FM-01 to FM-05, FM-07,
#          FM-08, FM-11); a tree is listed whole before it changes, deepest first, and what could not be read is reported; folders get
#          Run for the classes whose Read box is on; a link led elsewhere uses a free name of its own, and removes only what it made;
#          folders get Run for the classes given, apart from the boxes that change; a temporary link that can't be removed is said
# v0.0.6: Initial: reading a local folder and the actions on this computer; os, not Qt, so a name that is not valid UTF-8 is listed too

TARGET_EXISTS_TEXT: str = "There is already something with this name here."
NOT_A_NAME_TEXT: str = "A name can't hold a slash."
NO_SUCH_USER_TEXT: str = "There is no user {name} on this computer."
NO_SUCH_GROUP_TEXT: str = "There is no group {name} on this computer."
NOT_CHANGED_TEXT: str = "{name}: {reason}"
NOT_READ_TEXT: str = "{name}: could not be read, so what is in it was left as it was ({reason})"
FILE_MANAGER: str = "xdg-open"
LINK_NEW_SUFFIX: str = ".chrono-ftp-link"
FREE_NAME_TRIES: int = 20
TEMPORARY_LINK_LEFT_TEXT: str = "A new link made on the way remains at {path}; you can delete it."


def _entry(item: os.DirEntry[str]) -> RemoteEntry:
    is_link = item.is_symlink()
    try:
        is_folder = item.is_dir()
        status = item.stat(follow_symlinks=False)
        target = os.readlink(item.path) if is_link else ""
    except OSError:
        return RemoteEntry(item.name, False, is_link=is_link)  # gone in the meantime, or a broken link
    return RemoteEntry(item.name, is_folder, status.st_size, status.st_mtime, format(status.st_mode & 0o7777, "04o"), is_link=is_link, link_target=target)


if sys.platform != "win32":

    def _owner_name(uid: int) -> str:
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return str(uid)  # an id without a name, as ls shows it

    def _group_name(gid: int) -> str:
        try:
            return grp.getgrgid(gid).gr_name
        except KeyError:
            return str(gid)

    def may_change_owner() -> bool:
        """Only root gives a file to another user."""
        return os.geteuid() == 0

    def my_groups() -> list[str]:
        """The groups this computer's user may give a file of their own to; every group for root."""
        if may_change_owner():
            return sorted({group.gr_name for group in grp.getgrall()})
        return sorted({_group_name(gid) for gid in {os.getegid(), *os.getgroups()}})

    def all_users() -> list[str]:
        return sorted({user.pw_name for user in pwd.getpwall()})

    def _my_name() -> str:
        return _owner_name(os.geteuid())

    def _id_of(name: str, users: bool) -> int:
        if name.isdecimal():
            return int(name)
        try:
            return pwd.getpwnam(name).pw_uid if users else grp.getgrnam(name).gr_gid
        except KeyError as error:
            raise LocalActionFailed((NO_SUCH_USER_TEXT if users else NO_SUCH_GROUP_TEXT).format(name=name)) from error

    def _give(path: str, uid: int, gid: int) -> None:
        os.chown(path, uid, gid, follow_symlinks=False)

else:  # Windows has no owners and groups of this kind: Properties shows none and offers no change

    def _owner_name(uid: int) -> str:
        return ""

    def _group_name(gid: int) -> str:
        return ""

    def may_change_owner() -> bool:
        return False

    def my_groups() -> list[str]:
        return []

    def all_users() -> list[str]:
        return []

    def _my_name() -> str:
        return ""

    def _id_of(name: str, users: bool) -> int:
        raise LocalActionFailed((NO_SUCH_USER_TEXT if users else NO_SUCH_GROUP_TEXT).format(name=name))

    def _give(path: str, uid: int, gid: int) -> None:
        raise LocalActionFailed(NO_SUCH_USER_TEXT.format(name=uid))


def details(folder: str, names: list[str]) -> list[RemoteEntry]:
    """The selected entries for Properties, with owner and group by name and the target of a link; one that is gone is left out."""
    found: list[RemoteEntry] = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            status = os.lstat(path)
            is_link = stat.S_ISLNK(status.st_mode)
            target = os.readlink(path) if is_link else ""
            is_folder = os.path.isdir(path)
        except OSError:
            continue
        special = not is_link and not stat.S_ISREG(status.st_mode) and not stat.S_ISDIR(status.st_mode)
        mode = "" if is_link else format(stat.S_IMODE(status.st_mode), "04o")  # a link has no rights of its own
        found.append(RemoteEntry(name, is_folder, status.st_size, status.st_mtime, mode, _owner_name(status.st_uid), _group_name(status.st_gid), is_link, target, special=special))
    return found


def may_change_group(entries: list[RemoteEntry]) -> bool:
    """A file's group can be changed by its owner, to a group of theirs, and by root."""
    return may_change_owner() or (bool(_my_name()) and all(entry.owner == _my_name() for entry in entries))


def _listed(path: str, inside: bool, refused: list[str]) -> list[tuple[str, bool]]:
    """The path, and with inside everything below it, each with whether it is a folder; links are never followed. The whole tree is
    listed before anything changes, so taking Read away can never hide what is below; a folder that can't be read is reported."""

    def unreadable(error: OSError) -> None:
        refused.append(NOT_READ_TEXT.format(name=error.filename or path, reason=error.strerror or error))

    found = [(path, os.path.isdir(path) and not os.path.islink(path))]
    if not inside or os.path.islink(path) or not os.path.isdir(path):
        return found
    for top, folders, files in os.walk(path, onerror=unreadable, followlinks=False):
        for name in folders:
            place = os.path.join(top, name)
            found.append((place, not os.path.islink(place)))
        found.extend((os.path.join(top, name), False) for name in files)
    return found


def change_properties(paths: list[str], owner: str, group: str, rights: Rights | None, inside: bool, folder_readers: str = "") -> list[str]:
    """Owner, group and rights of each path, and with inside of everything below it, as the person set them in Properties. A link
    keeps its own rights, which mean nothing, and is never followed. Everything is listed first and changed deepest first, so a folder
    loses Read or Run only after what is in it was changed. Returns everything that could not be read or changed, with the reason:
    one refusal never stops the rest, and nothing is skipped in silence."""
    uid = _id_of(owner, True) if owner else -1
    gid = _id_of(group, False) if group else -1
    refused: list[str] = []
    for top in paths:
        for path, is_folder in reversed(_listed(top, inside, refused)):  # what is inside a folder comes after it in the list
            try:
                if uid != -1 or gid != -1:
                    _give(path, uid, gid)
                if (rights is not None or (folder_readers and is_folder)) and not os.path.islink(path):
                    before = stat.S_IMODE(os.lstat(path).st_mode)
                    mode = before if rights is None else rights_model.applied(before, rights)
                    if mode is not None:
                        mode = rights_model.with_x_for_folders(mode, folder_readers) if is_folder else mode
                        if mode != before:
                            os.chmod(path, mode)
            except OSError as error:
                refused.append(NOT_CHANGED_TEXT.format(name=path, reason=error.strerror or error))
    return refused


def make_file(folder: str, name: str) -> str:
    """A new empty file, only where nothing has the name; its path."""
    if not safe_name(name):
        raise LocalActionFailed(NOT_A_NAME_TEXT)
    path = os.path.join(folder, name)
    try:
        with Path(path).open("x"):
            pass
    except FileExistsError as error:
        raise LocalActionFailed(TARGET_EXISTS_TEXT) from error
    except OSError as error:
        raise LocalActionFailed(error.strerror or "This file could not be made.") from error
    return path


def make_link(folder: str, name: str, target: str, hard: bool) -> None:
    if not safe_name(name):
        raise LocalActionFailed(NOT_A_NAME_TEXT)
    path = os.path.join(folder, name)
    try:
        if hard:
            os.link(os.path.join(folder, target), path)  # a hard link needs the real file; a relative target is read from this folder
        else:
            os.symlink(target, path)
    except FileExistsError as error:
        raise LocalActionFailed(TARGET_EXISTS_TEXT) from error
    except OSError as error:
        raise LocalActionFailed(error.strerror or "This link could not be made.") from error


def change_link(folder: str, name: str, target: str) -> None:
    """The link leads elsewhere: the new one is made beside it and takes its name in one step, so the link is never missing."""
    path = os.path.join(folder, name)
    made = ""  # the new link, once this call made it: only that is ever removed
    try:
        for _ in range(FREE_NAME_TRIES):
            fresh = f"{path}.{secrets.token_hex(4)}{LINK_NEW_SUFFIX}"
            try:
                os.symlink(target, fresh)
            except FileExistsError:
                continue  # someone's file has this name: another one
            made = fresh
            break
        if not made:
            raise LocalActionFailed("No free name was found beside the link.")
        os.replace(made, path)
    except OSError as error:
        text = error.strerror or "This link could not be changed."
        if made:
            try:
                os.unlink(made)
            except OSError:
                text = f"{text} {TEMPORARY_LINK_LEFT_TEXT.format(path=made)}"
        raise LocalActionFailed(text) from error


def set_folder_rights(path: str, rights: Rights) -> None:
    mode = rights_model.applied(None, rights)
    if mode is None:
        return
    try:
        os.chmod(path, mode)
    except OSError as error:
        raise LocalActionFailed(error.strerror or "The rights of this folder could not be set.") from error


def file_manager_available() -> bool:
    return shutil.which(FILE_MANAGER) is not None


def file_manager_arguments(folder: str) -> list[str]:
    """The desktop's own file manager on this folder (FM-11)."""
    return [FILE_MANAGER, folder]


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
