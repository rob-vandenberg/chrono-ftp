import shlex
import shutil

from chrono_ftp.core.connection import Connection

# --- Version ------------------------------------------------------------
__version__ = "terminal.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: a terminal on the server, in the folder shown (D-018)

TERMINAL_PROGRAM: str = "konsole"


def terminal_available() -> bool:
    return shutil.which(TERMINAL_PROGRAM) is not None


def terminal_arguments(connection: Connection, folder: str) -> list[str]:
    login = f"{connection.user}@{connection.host}" if connection.user else connection.host
    ssh = ["ssh", "-t", "-p", str(connection.port)]
    if connection.key_file:
        ssh += ["-i", connection.key_file]
    return [TERMINAL_PROGRAM, "-e", *ssh, "--", login, f"cd {shlex.quote(folder)} && exec \"$SHELL\" -l"]
