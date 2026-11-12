"""The bookmarks of both sides, the location profiles and the paths typed in the Open folder window, in one file:
`~/.config/chrono-ftp/bookmarks.json`. The file is input: a damaged one never stops the program, it is kept aside (OR-15)."""

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePath, PurePosixPath
from typing import cast

from chrono_ftp.config.connections import config_folder, set_aside_copy, write_whole

# --- Version ------------------------------------------------------------
__version__ = "bookmarks.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial: bookmarks of this computer and of the server, location profiles and typed paths (FH-02, FH-03); a file read in part
#          is set aside once and written back cleaned; a deleted connection's own places go with it

FILE_NAME: str = "bookmarks.json"
LOCAL: str = "local"
REMOTE: str = "remote"
SIDES: tuple[str, ...] = (LOCAL, REMOTE)
SHARED: str = ""  # the connection of a remote bookmark or profile that every connection shows
MOST_ENTRIES: int = 10_000  # a number from a file is input (OR-15): more entries than this are left out
MOST_TEXT: int = 4096  # the longest name, path or key a file may hold
TYPED_KEPT: int = 20  # the paths typed in the Open folder window that are kept per side and connection


@dataclass(frozen=True)
class Bookmark:
    """A folder of one side. A remote one belongs to one connection, or, with SHARED, to all; a local one is always shared."""

    name: str
    path: str
    side: str
    connection: str = SHARED
    key: str = ""  # a key that opens it, as Qt writes keys ("Ctrl+1"); empty: none


@dataclass(frozen=True)
class Profile:
    """A named pair of a local and a remote folder (a location profile), optionally in a folder of profiles."""

    name: str
    local: str
    remote: str
    folder: str = ""
    connection: str = SHARED


@dataclass(frozen=True)
class Typed:
    """A path typed in the Open folder window."""

    side: str
    connection: str
    path: str


@dataclass(frozen=True)
class Places:
    bookmarks: tuple[Bookmark, ...] = ()
    profiles: tuple[Profile, ...] = ()
    typed: tuple[Typed, ...] = ()  # newest first


@dataclass(frozen=True)
class LoadedPlaces:
    places: Places
    set_aside: Path | None  # the copy of a file that could not be read in full
    writable: bool = True  # False: the file is there but can't be read, nor kept aside; writing it would lose what it holds


def places_path() -> Path:
    return config_folder() / FILE_NAME


def _text(value: object, required: bool) -> str | None:
    if not isinstance(value, str) or len(value) > MOST_TEXT or (required and not value.strip()):
        return None
    return value


def _fields(entry: object, required: tuple[str, ...], optional: tuple[str, ...]) -> dict[str, str] | None:
    if not isinstance(entry, dict):
        return None
    values = cast(dict[str, object], entry)
    found: dict[str, str] = {}
    for name in required + optional:
        text = _text(values.get(name, "" if name in optional else None), name in required)
        if text is None:
            return None
        found[name] = text
    return found


def _bookmark_from(entry: object) -> Bookmark | None:
    found = _fields(entry, ("name", "path", "side"), ("connection", "key"))
    if found is None or found["side"] not in SIDES:
        return None
    connection = found["connection"] if found["side"] == REMOTE else SHARED
    return Bookmark(found["name"], found["path"], found["side"], connection, found["key"])


def _profile_from(entry: object) -> Profile | None:
    found = _fields(entry, ("name", "local", "remote"), ("folder", "connection"))
    if found is None:
        return None
    return Profile(found["name"], found["local"], found["remote"], found["folder"].strip(), found["connection"])


def _typed_from(entry: object) -> Typed | None:
    found = _fields(entry, ("side", "path"), ("connection",))
    if found is None or found["side"] not in SIDES:
        return None
    return Typed(found["side"], found["connection"] if found["side"] == REMOTE else SHARED, found["path"])


def _list(data: dict[str, object], name: str) -> list[object] | None:
    value = data.get(name, [])
    return cast(list[object], value) if isinstance(value, list) else None


def load() -> LoadedPlaces:
    path = places_path()
    if not path.exists():
        return LoadedPlaces(Places(), None)
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return _cleaned(path, Places())
    if not isinstance(data, dict):
        return _cleaned(path, Places())
    values = cast(dict[str, object], data)
    marks, profiles, typed = _list(values, "bookmarks"), _list(values, "profiles"), _list(values, "typed")
    if marks is None or profiles is None or typed is None:
        return _cleaned(path, Places())
    found_marks = [_bookmark_from(entry) for entry in marks[:MOST_ENTRIES]]
    found_profiles = [_profile_from(entry) for entry in profiles[:MOST_ENTRIES]]
    found_typed = [_typed_from(entry) for entry in typed[:MOST_ENTRIES]]
    places = Places(
        tuple(mark for mark in found_marks if mark is not None),
        tuple(profile for profile in found_profiles if profile is not None),
        tuple(entry for entry in found_typed if entry is not None),
    )
    whole = len(places.bookmarks) == len(marks) and len(places.profiles) == len(profiles) and len(places.typed) == len(typed)
    return LoadedPlaces(places, None) if whole else _cleaned(path, places)


def _cleaned(path: Path, usable: Places) -> LoadedPlaces:
    """A file that could not be read in full: a copy goes aside, and what could be used is written back at once, so the next start
    reads a whole file and sets nothing aside again. When no copy can be made, the file is left exactly as it is, never overwritten."""
    copy = set_aside_copy(path)
    if copy is None:
        return LoadedPlaces(usable, None, False)
    try:
        save(usable)
    except OSError:
        pass  # the copy is kept; the next start tries again
    return LoadedPlaces(usable, copy)


def save(places: Places) -> None:
    """Written to a new file and moved into place in one step, so a crash leaves the old file whole. A name that is not valid UTF-8 is
    written escaped, so it comes back as it was."""
    data = {
        "bookmarks": [asdict(mark) for mark in places.bookmarks],
        "profiles": [asdict(profile) for profile in places.profiles],
        "typed": [asdict(entry) for entry in places.typed],
    }
    write_whole(places_path(), data)


def shows_for(connection: str, side: str, wanted_connection: str) -> bool:
    """Whether a bookmark or profile of this connection shows on a tab of the wanted connection."""
    return side == LOCAL or connection in (SHARED, wanted_connection)


def bookmarks_of(places: Places, side: str, connection: str) -> list[Bookmark]:
    """The bookmarks of one side, in their order: on the right those of this connection and the shared ones."""
    return [mark for mark in places.bookmarks if mark.side == side and shows_for(mark.connection, side, connection)]


def profiles_of(places: Places, connection: str) -> list[Profile]:
    return [profile for profile in places.profiles if profile.connection in (SHARED, connection)]


def typed_of(places: Places, side: str, connection: str) -> list[str]:
    owner = connection if side == REMOTE else SHARED
    return [entry.path for entry in places.typed if entry.side == side and entry.connection == owner]


def default_name(path: str, side: str) -> str:
    """The last folder of a path; the path itself for the root."""
    name = (PurePosixPath(path) if side == REMOTE else PurePath(path)).name
    return name or path


def has_bookmark(places: Places, side: str, connection: str, path: str) -> bool:
    return any(mark.path == path for mark in bookmarks_of(places, side, connection))


def with_bookmark(places: Places, bookmark: Bookmark) -> Places:
    """The bookmark added at the end; a folder that is already a bookmark of that side is not added twice."""
    if has_bookmark(places, bookmark.side, bookmark.connection, bookmark.path):
        return places
    return replace(places, bookmarks=(*places.bookmarks, bookmark))


def with_profile(places: Places, profile: Profile) -> Places:
    return replace(places, profiles=(*places.profiles, profile))


def with_typed(places: Places, side: str, connection: str, path: str) -> Places:
    """The path first in the history of its side and connection, once; the oldest beyond TYPED_KEPT go."""
    owner = connection if side == REMOTE else SHARED
    entry = Typed(side, owner, path)
    mine = [old for old in places.typed if old.side == side and old.connection == owner and old.path != path]
    others = [old for old in places.typed if not (old.side == side and old.connection == owner)]
    return replace(places, typed=(entry, *mine[: TYPED_KEPT - 1], *others))


def without_connection(places: Places, name: str) -> Places:
    """A deleted connection's own bookmarks, profiles and typed paths go with it; the shared ones and those of this computer stay."""
    if not name:
        return places
    return Places(
        tuple(mark for mark in places.bookmarks if not (mark.side == REMOTE and mark.connection == name)),
        tuple(profile for profile in places.profiles if profile.connection != name),
        tuple(entry for entry in places.typed if not (entry.side == REMOTE and entry.connection == name)),
    )


def with_connection_renamed(places: Places, old: str, new: str) -> Places:
    """A renamed connection keeps its own bookmarks, profiles and typed paths."""
    if not old or old == new:
        return places
    return Places(
        tuple(replace(mark, connection=new) if mark.side == REMOTE and mark.connection == old else mark for mark in places.bookmarks),
        tuple(replace(profile, connection=new) if profile.connection == old else profile for profile in places.profiles),
        tuple(replace(entry, connection=new) if entry.side == REMOTE and entry.connection == old else entry for entry in places.typed),
    )
