"""What the Properties window shows of one or several files (FM-01), and the owner and group names it offers (FM-04)."""

from dataclasses import dataclass

from chrono_ftp.connections.base import RemoteConnection, RemoteEntry
from chrono_ftp.engine import commands, rights

# --- Version ------------------------------------------------------------
__version__ = "properties.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Initial: one view of several files, where a field that differs says so; the names of users and groups, from the server's
#          own lists where a shell exists, else from the names seen in listings (D-060); the server's lists within 10 seconds;
#          the lists only suggest, any name of valid form goes

GROUPS_MARK: str = "chrono-ftp-groups"
NAMES_COMMAND: str = f"{{ getent passwd || cat /etc/passwd; }} 2>/dev/null; echo {GROUPS_MARK}; {{ getent group || cat /etc/group; }} 2>/dev/null; exit 0"
NAMES_SECONDS: float = 10.0  # a directory service slower than this: the names seen in the listing are offered instead
MOST_NAMES: int = 10_000  # a directory with more users than this is offered in part; a typed name is still checked by the form of a name


@dataclass(frozen=True)
class Summary:
    """Several files as one view. A field is None where the files differ, and empty where nothing is known."""

    files: int
    folders: int
    links: int
    name: str | None  # None for several
    kind: str
    size: int | None  # the files together, without what is in the folders; None when a size is unknown
    modified: float | None
    modified_differs: bool
    link_target: str | None
    owner: str | None
    group: str | None
    rights: rights.Rights
    rights_known: bool  # every file's rights are known

    @property
    def count(self) -> int:
        return self.files + self.folders


def kind_of(entry: RemoteEntry) -> str:
    if entry.is_link:
        return "Link to a folder" if entry.is_folder else "Link"
    if entry.special:
        return "Special file (a pipe, socket or device)"
    return "Folder" if entry.is_folder else "File"


def _one[T](values: list[T]) -> T | None:
    return values[0] if values and all(value == values[0] for value in values) else None


def summary(entries: list[RemoteEntry]) -> Summary:
    folders = sum(1 for entry in entries if entry.is_folder)
    links = sum(1 for entry in entries if entry.is_link)
    files = len(entries) - folders
    kinds = {kind_of(entry) for entry in entries}
    kind = kinds.pop() if len(kinds) == 1 else "Files and folders" if files and folders else "Files"
    file_entries = [entry for entry in entries if not entry.is_folder]
    size = sum(entry.size for entry in file_entries) if all(entry.size_known for entry in file_entries) else None
    dates = [entry.modified for entry in entries]
    modes = [rights.mode_number(entry.permissions) for entry in entries]
    return Summary(
        files=files,
        folders=folders,
        links=links,
        name=entries[0].name if len(entries) == 1 else None,
        kind=kind,
        size=size,
        modified=_one(dates),
        modified_differs=len(set(dates)) > 1,
        link_target=_one([entry.link_target for entry in entries if entry.is_link]) if links else "",
        owner=_one([entry.owner for entry in entries]),
        group=_one([entry.group for entry in entries]),
        rights=rights.common(modes),
        rights_known=all(mode is not None for mode in modes),
    )


def _checked(names: list[str]) -> list[str]:
    return sorted({name for name in names if commands.valid_name(name)})[:MOST_NAMES]


def parse_names(output: str) -> tuple[list[str], list[str]]:
    """The users and groups of the server from its passwd and group lists: the first field of each line. The output is input: a line
    that holds no valid name is left out."""
    lines = output.splitlines()
    if GROUPS_MARK not in lines:
        return [], []  # not the answer of NAMES_COMMAND: nothing is assumed
    middle = lines.index(GROUPS_MARK)
    return _checked([line.split(":", 1)[0] for line in lines[:middle]]), _checked([line.split(":", 1)[0] for line in lines[middle + 1 :]])


def server_names(connection: RemoteConnection) -> tuple[list[str], list[str]]:
    """The users and groups the server knows; empty lists when it could not tell."""
    return parse_names(connection.run_briefly(NAMES_COMMAND, NAMES_SECONDS).output)


def names_seen(entries: list[RemoteEntry]) -> tuple[list[str], list[str]]:
    """Without a shell: the owners and groups seen in a listing are the names offered."""
    return _checked([entry.owner for entry in entries]), _checked([entry.group for entry in entries])


def name_allowed(name: str) -> bool:
    """A typed owner or group: any name of valid form. The lists only suggest; the server or this computer refuses a name it does not
    know with its own reason (a directory may not list every name)."""
    return commands.valid_name(name)
