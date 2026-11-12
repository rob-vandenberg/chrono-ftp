"""The server information of one tab (FP-03): what the connection tells about itself, what it can do as plain yes and no, and the free
space in the folder shown. Read on the tab's own thread, so the window never waits (D-019)."""

from dataclasses import dataclass

from chrono_ftp.connections.base import Capabilities, FreeSpace, RemoteConnection
from chrono_ftp.core.errors import ConnectionLost, RemoteActionFailed
from chrono_ftp.engine import commands

# --- Version ------------------------------------------------------------
__version__ = "serverinfo.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: protocol, software, encryption, the host key or certificate, the detected abilities as yes or no, and the free
#          space, "unknown" where it can't be known (FP-03, D-066)

UNKNOWN: str = "unknown"
YES: str = "yes"
NO: str = "no"
NOT_ENCRYPTED_TEXT: str = "not encrypted: names, files and the password travel readable"
NO_SPACE_TEXT: str = "unknown: this server does not tell"
UNITS: tuple[str, ...] = ("bytes", "KB", "MB", "GB", "TB", "PB")
Lines = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Report:
    """The sections of the window, each a title and its lines of label and value."""

    sections: tuple[tuple[str, Lines], ...]


def size_text(size: int | None) -> str:
    if size is None:
        return UNKNOWN
    value = float(size)
    for unit in UNITS:
        if value < 1024 or unit == UNITS[-1]:
            return f"{size} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return UNKNOWN


def space_text(space: FreeSpace | None) -> str:
    if space is None or space.available is None:
        return NO_SPACE_TEXT
    total = f" of {size_text(space.total)}" if space.total else ""
    return f"{size_text(space.available)} free{total} (by {space.how})"


def ability_lines(capabilities: Capabilities, ftp: bool) -> Lines:
    """What this connection can do (D-016), in the words of the window. The abilities that need a shell are left out on FTP, as their
    actions are not shown there (D-027)."""

    def word(on: bool) -> str:
        return YES if on else NO

    shell = capabilities.shell
    lines: list[tuple[str, str]] = []
    if not ftp:
        lines.append(("A shell for commands", word(shell)))
        lines.append(("System", capabilities.system or UNKNOWN if shell else UNKNOWN))
        lines.append(("Logged in as root", word(capabilities.is_root) if shell else UNKNOWN))
    lines += [
        ("Continue a transfer that broke off", word(capabilities.resume)),
        ("Keep the dates of files", word(capabilities.timestamps)),
        ("Set permissions", word(capabilities.rights)),
        ("Copy a file on the server", word(capabilities.server_copy or capabilities.has("cp"))),
        ("Symbolic links", word(capabilities.symlinks or capabilities.has("ln"))),
        ("Hard links", word(capabilities.hardlinks or capabilities.has("ln"))),
        ("Replace a name in one step", word(capabilities.atomic_rename)),
        ("Confirm a file is on the server's disk", word(capabilities.durable)),
        ("Cut a file back", word(capabilities.cut)),
    ]
    if not ftp:
        lines += [
            ("Change owner and group", word(capabilities.has("chown"))),
            ("Change everything inside a folder in one command", word(capabilities.has("chmod") and capabilities.has("find"))),
            ("Find files on the server", word(commands.can_search(capabilities))),
            ("Fast transfers with rsync", word(capabilities.has("rsync"))),
            ("Checksums (sha256sum)", word(capabilities.has("sha256sum"))),
            ("Change the password", word(capabilities.has("passwd"))),
        ]
    return tuple(lines)


def report(remote: RemoteConnection, folder: str, ftp: bool, address: str) -> Report:
    """Everything the window shows; only a lost connection is raised. A refusal to tell leaves its value unknown."""
    try:
        info = remote.server_info()
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        info = None
    try:
        space = remote.free_space(folder) if folder else None
    except ConnectionLost:
        raise
    except RemoteActionFailed:
        space = None
    connection: Lines = (
        ("Server", address),
        ("Protocol", info.protocol if info is not None and info.protocol else UNKNOWN),
        ("Server software", info.software if info is not None and info.software else UNKNOWN),
    )
    if not remote.encrypted:
        encryption: Lines = (("Encryption", NOT_ENCRYPTED_TEXT),)
    else:
        encryption = info.encryption if info is not None and info.encryption else (("Encryption", UNKNOWN),)
    identity = info.identity if info is not None and info.identity else (("Identity", UNKNOWN),)
    sections: list[tuple[str, Lines]] = [
        ("Connection", connection),
        ("Encryption", encryption),
        ("Certificate" if ftp else "Host key", identity),
        ("What this connection can do", ability_lines(remote.capabilities, ftp)),
        ("Space", (("Folder", folder or UNKNOWN), ("Free space", space_text(space)))),
    ]
    if info is not None and info.offers:
        sections.append(("What the server offers", tuple((name, "") for name in info.offers)))
    return Report(tuple(sections))
