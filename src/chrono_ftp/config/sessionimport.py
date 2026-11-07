import re
from configparser import ConfigParser, Error
from pathlib import Path
from typing import override
from urllib.parse import unquote

from chrono_ftp.core import colours
from chrono_ftp.core.connection import DEFAULT_PORTS, FTP, HIGHEST_PORT, LOWEST_PORT, SFTP, Connection

# --- Version ------------------------------------------------------------
__version__ = "sessionimport.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: the one-time import of an exported session list (D-021); the layout of the file is from memory, not checked yet against a real export

IMPORT_BUTTON_TEXT: str = "Import from WinSCP"
FILE_FILTER: str = "WinSCP.ini (*.ini)"
FTP_PROTOCOL_NUMBER: int = 5
NUMBER_PATTERN: re.Pattern[str] = re.compile(r"[0-9]{1,10}")
SECTION_PREFIX: re.Pattern[str] = re.compile(r"^Sessions\\", re.IGNORECASE)


class _CaseKeepingParser(ConfigParser):
    @override
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def _number(text: str) -> int | None:
    return int(text) if NUMBER_PATTERN.fullmatch(text.strip()) else None


def colour_from(text: str) -> str:
    """The file keeps a colour as one number with blue first; a number that is no colour gives the starting colour."""
    number = _number(text)
    if number is None or number > 0xFFFFFF:
        return colours.FIRST_CONNECTION
    blue, green, red = (number >> 16) & 255, (number >> 8) & 255, number & 255
    return f"#{red:02x}{green:02x}{blue:02x}"


def read_sessions(path: Path) -> list[Connection]:
    """Every session with a host. A file that can't be read gives an empty list; a bad session is left out (OR-15)."""
    parser = _CaseKeepingParser(interpolation=None, strict=False)
    try:
        parser.read_string(path.read_bytes().decode("utf-8", "replace"))
    except (Error, OSError):
        return []
    found: list[Connection] = []
    for section in parser.sections():
        if not SECTION_PREFIX.match(section):
            continue
        values = parser[section]
        host = values.get("HostName", "").strip()
        if not host:
            continue
        name = unquote(SECTION_PREFIX.sub("", section)).replace("/", " ")
        protocol = FTP if _number(values.get("FSProtocol", "")) == FTP_PROTOCOL_NUMBER else SFTP
        port = _number(values.get("PortNumber", "")) or DEFAULT_PORTS[protocol]
        if not LOWEST_PORT <= port <= HIGHEST_PORT:
            port = DEFAULT_PORTS[protocol]
        colour = colour_from(values["Color"]) if "Color" in values else colours.FIRST_CONNECTION
        found.append(Connection(name=name, host=host, user=values.get("UserName", "").strip(), port=port, protocol=protocol, key_file=unquote(values.get("PublicKeyFile", "")).strip(), colour=colour, start_folder=unquote(values.get("RemoteDirectory", "")).strip()))
    return found
