from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "connection.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: A tab name of its own, kept with the connection, and the longest one a person can give (FI-01)
# v0.0.34: A local starting folder next to the remote one, and whether the last folders are remembered (FH-06)
# v0.0.9: A connection has no colour unless the person gives it one
# v0.0.5: Initial: a saved connection (D-017)

SFTP: str = "sftp"
FTP: str = "ftp"
PROTOCOLS: tuple[str, ...] = (SFTP, FTP)
DEFAULT_PORTS: dict[str, int] = {SFTP: 22, FTP: 21}
LONGEST_TAB_NAME: int = 100  # the name a tab can be given; a longer one would not fit a tab and is refused when it is typed (FI-01)
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
