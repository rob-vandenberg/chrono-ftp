from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "connection.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: A local starting folder next to the remote one, and whether the last folders are remembered (FH-06)
# v0.0.9: A connection has no colour unless the person gives it one
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
    colour: str = ""  # empty: this connection has no colour of its own (D-007)
    start_folder: str = ""  # on the server; empty: the home folder of the login
    local_start_folder: str = ""  # on this computer; empty: the tab starts where the left side is
    remember_folders: bool = False  # on: the last folders of both sides become the starting folders when the tab or the program closes
