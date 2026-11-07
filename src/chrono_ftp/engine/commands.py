import re
import shlex

from chrono_ftp.connections.base import Capabilities, RemoteConnection
from chrono_ftp.core.errors import RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "commands.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: what runs on the server: one command, every path through shlex.quote (D-012, D-015, OR-5, OR-19)

TARGET_EXISTS_TEXT: str = "There is already something with this name there."
NAME_PATTERN: re.Pattern[str] = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*")
MODE_PATTERN: re.Pattern[str] = re.compile(r"[0-7]{3,4}|[ugoa]*[-+=][rwxXst]+(,[ugoa]*[-+=][rwxXst]+)*")


def valid_name(name: str) -> bool:
    """An owner or group name. It starts with no hyphen, so it can never be read as an option."""
    return NAME_PATTERN.fullmatch(name) is not None


def valid_mode(mode: str) -> bool:
    return MODE_PATTERN.fullmatch(mode) is not None


def owner_command(paths: list[str], owner: str, group: str, recursive: bool) -> str:
    """One chown for the whole tree. The target is owner, owner:group or :group."""
    target = owner + (f":{group}" if group else "")
    flag = "-R " if recursive else ""
    return f"chown {flag}-- {shlex.quote(target)} {shlex.join(paths)}"


def mode_command(paths: list[str], mode: str, recursive: bool) -> str:
    flag = "-R " if recursive else ""
    return f"chmod {flag}-- {shlex.quote(mode)} {shlex.join(paths)}"


def copy_command(source: str, target: str) -> str:
    return f"cp -a -- {shlex.quote(source)} {shlex.quote(target)}"


def move_command(source: str, target: str) -> str:
    return f"mv -- {shlex.quote(source)} {shlex.quote(target)}"


def without_overwriting(target: str, command: str) -> str:
    """The command runs only when the target is free: nothing is replaced without the person seeing it."""
    return f"if [ -e {shlex.quote(target)} ]; then echo {shlex.quote(TARGET_EXISTS_TEXT)} >&2; exit 1; fi; {command}"


def search_command(folder: str, pattern: str) -> str:
    start = f"./{folder}" if folder.startswith("-") else folder  # find has no --: a folder may never read as an expression
    return f"find {shlex.quote(start)} -iname {shlex.quote(pattern)} 2>/dev/null"


def checksum_command(path: str) -> str:
    return f"sha256sum -- {shlex.quote(path)}"


def run_checked(connection: RemoteConnection, command: str) -> str:
    """Runs one command; a refusal of the server comes back as its own words."""
    result = connection.run(command)
    if result.status != 0:
        raise RemoteActionFailed(result.errors.strip() or "The server refused this action.")
    return result.output


def search(connection: RemoteConnection, folder: str, pattern: str) -> list[str]:
    """find ends with an error when one folder can't be read; what it found still counts, so its status is not checked."""
    return [line for line in connection.run(search_command(folder, pattern)).output.splitlines() if line]


def can_change_owner(capabilities: Capabilities) -> bool:
    return capabilities.has("chown") and capabilities.is_root


def can_change_mode(capabilities: Capabilities) -> bool:
    return capabilities.has("chmod")


def can_copy(capabilities: Capabilities) -> bool:
    return capabilities.has("cp")


def can_search(capabilities: Capabilities) -> bool:
    return capabilities.has("find")
