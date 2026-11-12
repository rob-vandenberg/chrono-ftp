import os
import shlex
import shutil
from pathlib import Path

from chrono_ftp.core import colours
from chrono_ftp.core.connection import Connection

# --- Version ------------------------------------------------------------
__version__ = "terminal.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: The folder of the colour schemes is known outside, so Clean Up can list them (FP-04)
# v0.0.37: The terminal opens in the folder on the screen; a folder it can't enter leaves it open in the home folder with a calm line,
#          instead of closing at once; without a folder yet it opens in the home folder (FM-12)
# v0.0.9: A connection without a colour opens with the normal terminal colours
# v0.0.6: The terminal has the colour of its connection (D-007)
# v0.0.5: Initial: a terminal on the server, in the folder shown (D-018)

TERMINAL_PROGRAM: str = "konsole"
CANT_ENTER_TEXT: str = "Chrono FTP: this folder could not be entered, so the terminal starts in your home folder:"


def terminal_available() -> bool:
    return shutil.which(TERMINAL_PROGRAM) is not None


def scheme_folder() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "konsole"


def _channels(colour: str) -> str:
    text = colour.lstrip("#")
    return ",".join(str(int(text[start : start + 2], 16)) for start in (0, 2, 4))


def scheme_for(colour: str) -> str:
    """The terminal's colour scheme of this connection colour: its name. The terminal only takes colours from a scheme file."""
    name = f"chrono-ftp-{colour.lstrip('#').lower()}"
    text = colours.readable_text(colour)
    lines = ["[General]", f"Description=Chrono FTP {colour}", "Opacity=1"]
    for part, value in (("Background", colour), ("BackgroundIntense", colour), ("Foreground", text), ("ForegroundIntense", text)):
        lines += [f"[{part}]", f"Color={_channels(value)}"]
    folder = scheme_folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.colorscheme").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return name


def terminal_arguments(connection: Connection, folder: str) -> list[str]:
    login = f"{connection.user}@{connection.host}" if connection.user else connection.host
    ssh = ["ssh", "-t", "-p", str(connection.port)]
    if connection.key_file:
        ssh += ["-i", connection.key_file]
    colours_of_terminal = ["-p", f"ColorScheme={scheme_for(connection.colour)}"] if connection.colour else []
    return [TERMINAL_PROGRAM, *colours_of_terminal, "-e", *ssh, "--", login, shell_start(folder)]


def shell_start(folder: str) -> str:
    """What the server runs first: into the folder, then the person's own shell. A folder that can't be entered (gone meanwhile, no
    rights) is said in one line, and the shell opens in the home folder all the same (FM-12)."""
    if not folder:
        return 'exec "$SHELL" -l'
    quoted = shlex.quote(folder)
    return f"cd {quoted} || echo {shlex.quote(CANT_ENTER_TEXT)} {quoted}; exec \"$SHELL\" -l"
