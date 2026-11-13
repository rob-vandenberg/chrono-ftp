"""What a paste will do (FI-03), on this computer and on a server alike: one plan per item, made before anything is copied, so the
window can say what will happen and nothing is ever written over without the person seeing it (G-2)."""

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, replace

from chrono_ftp.connections.base import OLD_SUFFIX

# --- Version ------------------------------------------------------------
__version__ = "copyplan.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 3: each step of a replacing copy says what really failed, and a copy that can't be put back says where the
#          person's own data is, in words, never as a raw error of the system (OR-23)
# v0.0.45: Review round 2: a copy that replaces something is made under a name of our own and put in place only once it is whole,
#          so a copy that breaks off never costs the person what was there (G-2)
# v0.0.45: Initial: the plan of a paste, the same for this computer and for a server: a copy next to its own file, a name that is
#          taken, a file onto a folder or a folder onto a file, a folder into itself, and a file that is gone (FI-03, D-099)

NEW_SUFFIX: str = ".chrono-ftp-new"  # a copy that is to replace something carries this name until it is whole (FI-03, G-2)
BUSY_NEW_TEXT: str = "the name {path}, which Chrono FTP needs while it makes this copy, is taken"
BUSY_OLD_TEXT: str = "an earlier copy of {name} did not finish, so your own {name} is here under the name {path}: rename it back to {name}, or remove it, and try again; nothing of it was lost"
NOT_MOVED_ASIDE_TEXT: str = "it could not be moved out of the way, so nothing was copied over it: what you had is untouched"
NOT_IN_PLACE_TEXT: str = "the copy could not be put in its place, so nothing was copied over it: what you had is back where it was, untouched"
BOTH_FAILED_TEXT: str = "the copy could not be put in its place, and what you had could not be put back either; nothing of it is lost: it is here under the name {path}"
OLD_STAYS_TEXT: str = "The copy is in place. What was there could not be removed and stays next to it as {path}."
COPY_NAME: str = "{stem} (copy){suffix}"  # a copy next to the file it was made from, as a person would name it
NUMBERED_NAME: str = "{stem} (copy {number}){suffix}"
MOST_TRIES: int = 1000  # after this many taken names the person is told instead of the program looking forever
INTO_ITSELF_TEXT: str = "a folder can't be copied into itself"
GONE_TEXT: str = "it is no longer there"
NO_FREE_NAME_TEXT: str = "there are already a thousand copies of this name here"
FOLDER_IN_THE_WAY_TEXT: str = "a folder with this name is there, and a file can never be written into a folder"
FILE_IN_THE_WAY_TEXT: str = "a file with this name is there, and a folder can never take the place of a file"

Kind = bool | None  # what is at a name in the folder it goes into: None nothing, True a folder, False a file


@dataclass(frozen=True)
class Source:
    """One thing the person copied: where it is, what it is called, and whether it is a folder."""

    path: str
    name: str
    is_folder: bool = False
    folder: str = ""  # the folder it is in; a paste into that same folder makes a copy next to it
    there: bool = True  # False: it is gone since it was copied


@dataclass(frozen=True)
class Copy:
    """One item of a paste: where it comes from, where it goes, and what stands in the way."""

    source: str
    target: str
    name: str
    is_folder: bool = False
    replaces: bool = False  # something of the same kind is there already: the person is asked before it is written over
    beside: bool = False  # it goes next to its own file, under a copy name, as it was pasted where it already is
    why_not: str = ""  # it can't be done at all; empty when it can


def free_copy_name(name: str, taken: Callable[[str], bool]) -> str:
    """The name a copy gets next to the file it was made from: "note (copy).txt", then "note (copy 2).txt"."""
    stem, suffix = os.path.splitext(name)
    for number in range(1, MOST_TRIES + 1):
        wanted = COPY_NAME.format(stem=stem, suffix=suffix) if number == 1 else NUMBERED_NAME.format(stem=stem, suffix=suffix, number=number)
        if not taken(wanted):
            return wanted
    return ""


def inside(folder: str, top: str, separator: str = os.sep) -> bool:
    """folder is top itself or lies in it: a folder may never be copied into itself."""
    one, two = folder.rstrip(separator) or separator, top.rstrip(separator) or separator
    return one == two or one.startswith(two.rstrip(separator) + separator)


def planned(sources: list[Source], folder: str, kind_of: Callable[[str], Kind], join: Callable[[str, str], str], separator: str = os.sep) -> list[Copy]:
    """What a paste of these things into this folder will do. kind_of says what is at a name in that folder already. A thing pasted
    into the folder it is already in gets a copy of its own next to it, so nothing is ever lost (G-2); a file onto a folder, a folder
    onto a file and a folder into itself are refused with their reason (OR-20)."""
    plans: list[Copy] = []
    taken: set[str] = set()  # the copy names this paste takes itself: two of them never land on one name

    def is_taken(name: str) -> bool:
        return name in taken or kind_of(name) is not None

    for source in sources:
        plan = Copy(source.path, join(folder, source.name), source.name, source.is_folder)
        if not source.there:
            plans.append(replace(plan, why_not=GONE_TEXT))
            continue
        if source.is_folder and inside(folder, source.path, separator):
            plans.append(replace(plan, why_not=INTO_ITSELF_TEXT))
            continue
        if source.folder.rstrip(separator) == folder.rstrip(separator):
            free = free_copy_name(source.name, is_taken)  # pasted where it already is: a copy of its own, never over itself
            if not free:
                plans.append(replace(plan, why_not=NO_FREE_NAME_TEXT))
                continue
            taken.add(free)
            plans.append(replace(plan, target=join(folder, free), name=free, beside=True))
            continue
        kind = kind_of(source.name)
        if kind is None:
            plans.append(plan)
        elif kind != source.is_folder:  # a file would be written into a folder, or a folder over a file: both destroy what is there
            plans.append(replace(plan, why_not=FOLDER_IN_THE_WAY_TEXT if kind else FILE_IN_THE_WAY_TEXT))
        else:
            plans.append(replace(plan, replaces=True))
    return plans


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def fresh_name(target: str) -> str:
    """The name Chrono FTP's own copy carries while it is being made; it takes the real name only once it is whole (G-2)."""
    return target + NEW_SUFFIX


def put_in_place(fresh: str, target: str) -> str:
    """Our whole copy takes the place of what is there: the old one is moved aside, the copy takes the name, and only then is the old
    one removed. Nothing that was there is ever removed before the copy is whole and in place (G-2). Returns what the person should
    know about what was left behind; empty when nothing was. Whatever fails, our own copy goes and what was there stays."""
    aside = target + OLD_SUFFIX
    name = os.path.basename(target) or target
    if os.path.lexists(aside):  # an earlier copy was cut short: the person is told where their own is and what to do
        raise OSError(BUSY_OLD_TEXT.format(name=name, path=os.path.basename(aside)))
    try:
        os.rename(target, aside)
    except OSError as error:
        raise OSError(NOT_MOVED_ASIDE_TEXT) from error
    try:
        os.rename(fresh, target)
    except OSError as error:
        try:
            os.rename(aside, target)  # back exactly as it was
        except OSError:
            raise OSError(BOTH_FAILED_TEXT.format(path=os.path.basename(aside))) from error  # never a raw error of the system (OR-23)
        raise OSError(NOT_IN_PLACE_TEXT) from error
    try:
        _remove(aside)
    except OSError:
        return OLD_STAYS_TEXT.format(path=os.path.basename(aside))
    return ""


def here_kind(folder: str) -> Callable[[str], Kind]:
    """What is at a name in a folder of this computer."""

    def kind_of(name: str) -> Kind:
        path = os.path.join(folder, name)
        if not os.path.lexists(path):
            return None
        return os.path.isdir(path) and not os.path.islink(path)

    return kind_of


def here_sources(paths: list[str]) -> list[Source]:
    """The things a paste on this computer acts on, as they are now."""
    found: list[Source] = []
    for path in paths:
        name = os.path.basename(path.rstrip(os.sep)) or path
        found.append(Source(path, name, os.path.isdir(path) and not os.path.islink(path), os.path.dirname(path), os.path.lexists(path)))
    return found
