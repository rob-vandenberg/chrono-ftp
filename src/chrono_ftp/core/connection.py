from collections.abc import Iterable
from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "connection.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: not_saved: this connection is in no list, so nothing of it is written and its log stands apart (FJ-11)
# v0.0.47: A note of its own, in the person's own words, and the longest one that is kept (FJ-05); the folder helpers of the list, and
#          the name as it is saved, with the spaces around each part trimmed (FJ-01)
# v0.0.45: A tab name of its own, kept with the connection, and the longest one a person can give (FI-01)
# v0.0.34: A local starting folder next to the remote one, and whether the last folders are remembered (FH-06)
# v0.0.9: A connection has no colour unless the person gives it one
# v0.0.5: Initial: a saved connection (D-017)

SFTP: str = "sftp"
FTP: str = "ftp"
PROTOCOLS: tuple[str, ...] = (SFTP, FTP)
DEFAULT_PORTS: dict[str, int] = {SFTP: 22, FTP: 21}
LONGEST_TAB_NAME: int = 100  # the name a tab can be given; a longer one would not fit a tab and is refused when it is typed (FI-01)
LONGEST_NOTE: int = 4000  # the note a person writes with a connection; longer than this is cut when it is typed (FJ-05)
FOLDER_SEPARATOR: str = "/"  # a connection whose name holds this stands in a folder of the list: "Work/Mail server" (FJ-01)
LOWEST_PORT: int = 1
HIGHEST_PORT: int = 65535


@dataclass(frozen=True)
class Connection:
    name: str
    host: str
    user: str = ""
    port: int = DEFAULT_PORTS[SFTP]
    protocol: str = SFTP
    key_file: str = ""
    colour: str = ""  # empty: this connection has no colour of its own (D-007)
    start_folder: str = ""  # on the server; empty: the home folder of the login
    local_start_folder: str = ""  # on this computer; empty: the tab starts where the left side is
    remember_folders: bool = False  # on: the last folders of both sides become the starting folders when the tab or the program closes
    tab_name: str = ""  # what the tab of this connection is called; empty: the name of the connection itself (FI-01)
    note: str = ""  # whatever the person wrote about this connection; it shows when the mouse rests on it in the list (FJ-05)
    not_saved: bool = False  # this connection is in no list: a tab from a pasted address. Nothing of it is ever written (FJ-11, OR-19)


def folder_of(name: str) -> str:
    """The folder a connection of this name stands in, empty when it stands in none: a folder is part of the name (FJ-01)."""
    return name.rpartition(FOLDER_SEPARATOR)[0]


def own_name(name: str) -> str:
    """What the connection is called within its folder: "Work/Mail server" is called "Mail server" there (FJ-01)."""
    return name.rpartition(FOLDER_SEPARATOR)[2]


def in_folder(name: str, folder: str) -> bool:
    """Whether a connection of this name stands in that folder, or in one of its folders. Every connection stands in the empty folder."""
    return not folder or name.startswith(folder + FOLDER_SEPARATOR)


def moved_into(name: str, folder: str) -> str:
    """The name this connection takes in that folder: its own name after the folder, and its own name alone outside every folder (FJ-02)."""
    return f"{folder}{FOLDER_SEPARATOR}{own_name(name)}" if folder else own_name(name)


def folders_in(names: Iterable[str]) -> list[str]:
    """Every folder these names make, a folder of a folder included, each once, in the order the names bring them up (FJ-01)."""
    found: list[str] = []
    for name in names:
        parts = name.split(FOLDER_SEPARATOR)[:-1]
        for depth in range(1, len(parts) + 1):
            folder = FOLDER_SEPARATOR.join(parts[:depth])
            if folder and folder not in found:
                found.append(folder)
    return found


def tidy_name(name: str) -> str:
    """The name as it is saved: the spaces around each part trimmed, so no folder of the list is called " Work " and "Work / Mail"
    names the folder Work (FJ-01)."""
    return FOLDER_SEPARATOR.join(part.strip() for part in name.split(FOLDER_SEPARATOR))
