from dataclasses import dataclass

from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "connection.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: a saved connection (D-017)

SFTP: str = "sftp"
FTP: str = "ftp"
PROTOCOLS: tuple[str, ...] = (SFTP, FTP)
DEFAULT_PORTS: dict[str, int] = {SFTP: 22, FTP: 21}
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
    colour: str = colours.FIRST_CONNECTION
    start_folder: str = ""
