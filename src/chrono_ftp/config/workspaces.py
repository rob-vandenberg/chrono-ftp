"""Workspaces (FI-02): a named set of open tabs, each with the folders it was in, kept in workspaces.json."""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import cast

from chrono_ftp.config.connections import config_folder, write_whole

# --- Version ------------------------------------------------------------
__version__ = "workspaces.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Initial: a workspace is a name and the tabs it holds, each with its connection and the folders of both sides; one workspace
#          can open by itself at start; a damaged file gives no workspaces and a damaged entry is left out (OR-15, FI-02)

FILE_NAME: str = "workspaces.json"
MOST_WORKSPACES: int = 100  # a file is input (OR-15)
MOST_TABS: int = 100
LONGEST_NAME: int = 100
LONGEST_PATH: int = 4096


@dataclass(frozen=True)
class WorkspaceTab:
    """One tab of a workspace: the name of its connection, and the folder each side was in."""

    connection: str
    local_folder: str = ""
    remote_folder: str = ""


@dataclass(frozen=True)
class Workspace:
    name: str
    tabs: tuple[WorkspaceTab, ...] = ()


@dataclass(frozen=True)
class Workspaces:
    items: tuple[Workspace, ...] = ()
    at_start: str = ""  # the name of the workspace that opens by itself when Chrono FTP starts; empty: none


def _tab_from(entry: object) -> WorkspaceTab | None:
    if not isinstance(entry, dict):
        return None
    values = cast(dict[str, object], entry)
    name = values.get("connection")
    if not isinstance(name, str) or not name or len(name) > LONGEST_NAME:
        return None
    folders: dict[str, str] = {}
    for field in ("local_folder", "remote_folder"):
        value = values.get(field, "")
        if not isinstance(value, str) or len(value) > LONGEST_PATH:
            return None
        folders[field] = value
    return WorkspaceTab(name, folders["local_folder"], folders["remote_folder"])


def _workspace_from(entry: object) -> Workspace | None:
    if not isinstance(entry, dict):
        return None
    values = cast(dict[str, object], entry)
    name = values.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > LONGEST_NAME:
        return None
    found = cast(list[object], values.get("tabs")) if isinstance(values.get("tabs"), list) else []
    tabs = [_tab_from(item) for item in found[:MOST_TABS]]
    return Workspace(name.strip(), tuple(tab for tab in tabs if tab is not None))


def load() -> Workspaces:
    """The workspaces as the file has them. A file that can't be read gives none; a damaged workspace is left out (OR-15)."""
    try:
        data: object = json.loads((config_folder() / FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return Workspaces()
    if not isinstance(data, dict):
        return Workspaces()
    values = cast(dict[str, object], data)
    entries = cast(list[object], values.get("items")) if isinstance(values.get("items"), list) else []
    found: list[Workspace] = []
    taken: set[str] = set()
    for entry in entries[:MOST_WORKSPACES]:
        workspace = _workspace_from(entry)
        if workspace is not None and workspace.name.casefold() not in taken:
            taken.add(workspace.name.casefold())
            found.append(workspace)
    at_start = values.get("at_start")
    chosen = at_start if isinstance(at_start, str) and any(item.name == at_start for item in found) else ""
    return Workspaces(tuple(found), chosen)


def save(workspaces: Workspaces) -> None:
    write_whole(config_folder() / FILE_NAME, asdict(workspaces), private=False)


def named(workspaces: Workspaces, name: str) -> Workspace | None:
    return next((item for item in workspaces.items if item.name.casefold() == name.casefold()), None)


def with_workspace(workspaces: Workspaces, workspace: Workspace) -> Workspaces:
    """The list with this workspace in it: in the place of one of the same name, else at the end."""
    if named(workspaces, workspace.name) is None:
        return Workspaces((*workspaces.items, workspace), workspaces.at_start)
    items = tuple(workspace if item.name.casefold() == workspace.name.casefold() else item for item in workspaces.items)
    at_start = workspace.name if workspaces.at_start.casefold() == workspace.name.casefold() else workspaces.at_start
    return Workspaces(items, at_start)


def without(workspaces: Workspaces, name: str) -> Workspaces:
    items = tuple(item for item in workspaces.items if item.name.casefold() != name.casefold())
    at_start = "" if workspaces.at_start.casefold() == name.casefold() else workspaces.at_start
    return Workspaces(items, at_start)


def renamed(workspaces: Workspaces, old: str, new: str) -> Workspaces:
    """The workspace called old under the name new, in its place; the one that opens at start follows its new name."""
    items = tuple(Workspace(new, item.tabs) if item.name.casefold() == old.casefold() else item for item in workspaces.items)
    at_start = new if workspaces.at_start.casefold() == old.casefold() else workspaces.at_start
    return Workspaces(items, at_start)


def with_start(workspaces: Workspaces, name: str) -> Workspaces:
    return Workspaces(workspaces.items, name if named(workspaces, name) is not None else "")


def names(workspaces: Workspaces) -> Sequence[str]:
    return [item.name for item in workspaces.items]
