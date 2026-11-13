import re
import secrets
import shlex

from chrono_ftp.connections.base import OLD_SUFFIX, NO_COPY_TEXT, NO_HARD_LINKS_TEXT, NO_LINKS_TEXT, Capabilities, RemoteConnection, RemoteEntry, joined, own_name, parent_of, safe_name
from chrono_ftp.core.errors import ConnectionLost, NameTaken, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "commands.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 3: every step of a replacing copy says in plain words what failed and that what the person had is untouched,
#          and a copy cut short earlier says where their own file is and what to do with it
# v0.0.45: Review round 2: a copy on the server that replaces the one name the person agreed to is made under a name of our own and
#          put in place only once it is whole; a failure removes only our own copy, never what was already there (FI-03, G-2)
# v0.0.37: The group can be changed without being root; a copy on the server without a shell, of files, through the protocol where the
#          server offers it; the names of copies from a simple pattern; a link's target changed without the link ever missing; why
#          an action can't be done, for its hover text (FM-04, FM-05, FM-06); a link led elsewhere is replaced in one step where the
#          server can, and is never lost; a failed cp removes its half copy; owner and group names of directories are accepted (no
#          hyphen first, no colon, no control character); a link that leads nowhere is a taken name too, and the temporary names of a
#          link led elsewhere are made unique, so only what this call made is ever removed; everything inside changes through find, files first and folders deepest
#          first, in one command line, with Run for the folders of the classes that read, never X; a half copy that can't be
#          written gets its owner's rights back before it is removed, and one that remains is said
# v0.0.5: Initial: what runs on the server: one command, every path through shlex.quote (D-012, D-015, OR-5, OR-19)

TARGET_EXISTS_TEXT: str = "There is already something with this name there."
NO_STAR_TEXT: str = "With several files the name needs a *, which stands for each file's own name."
NOT_A_COPY_NAME_TEXT: str = "This pattern gives {name}, which can't be a name: a name can't hold a slash."
SAME_PLACE_TEXT: str = "A copy can't take the place of its original: please give another folder or name."
FOLDERS_NEED_SHELL_TEXT: str = "Without a shell this server copies single files only, not folders."
NO_COPY_ON_FTP_TEXT: str = "This FTP server can't copy on itself (it offers no SITE CPFR and CPTO). You can download and upload instead."
NO_LINKS_ON_FTP_TEXT: str = "FTP can't make links. An SFTP connection to this server can."
HALF_COPY_REMAINS_TEXT: str = "An unfinished copy remains at {path}; you can delete it."
NEW_SUFFIX: str = ".chrono-ftp-new"  # a copy that is to replace something carries this name until it is whole (FI-03, G-2)
BUSY_NEW_TEXT: str = "The name {path}, which Chrono FTP needs while it makes this copy, is taken. Remove it and try again."
BUSY_OLD_TEXT: str = "An earlier copy of {name} did not finish. Your own {name} is still here, under the name {path}: rename it back to {name}, or remove it, and try again. Nothing of it was lost."
NOT_MOVED_ASIDE_TEXT: str = "{path} could not be moved out of the way, so nothing was copied over it: what you had is untouched."
NOT_IN_PLACE_TEXT: str = "The copy could not be put in the place of {path}, so nothing was copied over it: what you had is back where it was, untouched."
BOTH_FAILED_TEXT: str = "The copy could not be put in the place of {path}, and what you had could not be put back either. Nothing of it is lost: it is here under the name {aside}."
OLD_STAYS_TEXT: str = "The copy is in place. What was there could not be removed and stays next to it as {path}."
LINK_NEW_SUFFIX: str = ".chrono-ftp-link"
LINK_OLD_SUFFIX: str = ".chrono-ftp-old-link"
OLD_LINK_LEFT_TEXT: str = "The link leads to its new target now. Its old version, {name}, could not be removed: {reason}"
MODE_PATTERN: re.Pattern[str] = re.compile(r"[0-7]{3,4}|[ugoa]*[-+=][rwxXst]+(,[ugoa]*[-+=][rwxXst]+)*")


def valid_name(name: str) -> bool:
    """An owner or group name, also of a directory (user@corp.example, DOMAIN\\user, domain users): not empty, no hyphen first, so it
    is never read as an option, no colon, which chown reads as owner:group, and no control character. It always goes through
    shlex.quote."""
    return bool(name) and not name.startswith("-") and ":" not in name and not any(ord(character) < 32 or ord(character) == 127 for character in name)


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


def _find_starts(paths: list[str]) -> str:
    return " ".join(shlex.quote(f"./{path}" if path.startswith("-") else path) for path in paths)  # find has no --: never an expression


def rights_command(paths: list[str], mode: str, recursive: bool, readers: str) -> str:
    """The one command line of a change of rights, shown and confirmed exactly (OR-5, OR-6). Alone: chmod. With everything inside:
    first every file (never a link, which chmod would follow), then every folder deepest first, so a folder loses Read or Run only
    after what is in it was changed, as on this computer; chmod -R would change a folder before it reads it. Folders get Run for the
    classes that read (readers) in that same step; a file never gains Run it was not given. A refusal does not stop the rest, and the
    line fails when anything was refused."""
    if not recursive:
        return mode_command(paths, mode, False) if mode else ""
    folder_mode = ",".join(part for part in (mode, f"{readers}+x" if readers else "") if part)
    if not folder_mode:
        return ""
    starts = _find_starts(paths)
    steps = [f"find {starts} ! -type d ! -type l -exec chmod -- {shlex.quote(mode)} {{}} +"] if mode else []
    steps.append(f"find {starts} -depth -type d -exec chmod -- {shlex.quote(folder_mode)} {{}} +")
    return "failed=0; " + " ".join(f"{step} || failed=1;" for step in steps) + " exit $failed"


def copy_command(source: str, target: str) -> str:
    return f"cp -a -- {shlex.quote(source)} {shlex.quote(target)}"


def move_command(source: str, target: str) -> str:
    return f"mv -- {shlex.quote(source)} {shlex.quote(target)}"


def without_overwriting(target: str, command: str) -> str:
    """The command runs only when the target is free: nothing is replaced without the person seeing it."""
    quoted = shlex.quote(target)  # -e alone is false for a link that leads nowhere: such a link has the name all the same
    return f"if [ -e {quoted} ] || [ -L {quoted} ]; then echo {shlex.quote(TARGET_EXISTS_TEXT)} >&2; exit 1; fi; {command}"


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


def can_change_group(capabilities: Capabilities) -> bool:
    """chown :group works without root for a group the login is in; the server says no to any other, in its own words."""
    return capabilities.has("chown")


def can_change_mode(capabilities: Capabilities) -> bool:
    return capabilities.has("chmod")


def can_copy(capabilities: Capabilities) -> bool:
    return capabilities.has("cp")


def why_no_copy(capabilities: Capabilities, chosen: list[RemoteEntry], on_ftp: bool) -> str:
    """Why these can't be copied on the server; empty when they can (FM-06). With a shell cp copies anything; without, the protocol
    copies single files where the server offers it (D-016: detected, never assumed)."""
    if can_copy(capabilities):
        return ""
    if not capabilities.server_copy:
        return NO_COPY_ON_FTP_TEXT if on_ftp else NO_COPY_TEXT
    if any(entry.is_folder for entry in chosen):
        return FOLDERS_NEED_SHELL_TEXT
    return ""


def why_no_link(capabilities: Capabilities, on_ftp: bool) -> str:
    if on_ftp:
        return NO_LINKS_ON_FTP_TEXT
    return "" if capabilities.symlinks else NO_LINKS_TEXT


def copy_names(pattern: str, names: list[str]) -> list[str]:
    """The name of each copy: * stands for the file's own name, so *.bak gives a.txt.bak and old-* gives old-a.txt; a pattern without *
    is the name itself, for one file only. A name that can't be one is refused with its reason."""
    if "*" not in pattern and len(names) != 1:
        raise RemoteActionFailed(NO_STAR_TEXT)
    made = [pattern.replace("*", name) for name in names]
    for name in made:
        if not safe_name(name):
            raise RemoteActionFailed(NOT_A_COPY_NAME_TEXT.format(name=name))
    return made


def replace_command(source: str, target: str) -> str:
    """A copy that takes the place of what is there, without ever removing that before the new copy is whole (G-2): the copy is made
    under a name of Chrono FTP's own, the old one is moved aside, the copy takes the name, and only then is the old one removed. A
    failure anywhere removes our own copy only, so the person is never worse off than before they pressed Start. Every path goes
    through shlex.quote (OR-19)."""
    fresh, aside = target + NEW_SUFFIX, target + OLD_SUFFIX
    quoted, new, old = shlex.quote(target), shlex.quote(fresh), shlex.quote(aside)
    name = target.rsplit("/", 1)[-1] or target
    busy_new = f"if [ -e {new} ] || [ -L {new} ]; then echo {shlex.quote(BUSY_NEW_TEXT.format(path=fresh))} >&2; exit 1; fi"
    busy_old = f"if [ -e {old} ] || [ -L {old} ]; then echo {shlex.quote(BUSY_OLD_TEXT.format(name=name, path=aside))} >&2; exit 1; fi"  # an earlier copy was cut short: the person is told where their own is
    made = f"{copy_command(source, fresh)} || {{ {half_copy_removal(fresh)}; exit 1; }}"  # only our own copy is ever removed
    set_aside = f"mv -- {quoted} {old} || {{ {half_copy_removal(fresh)}; echo {shlex.quote(NOT_MOVED_ASIDE_TEXT.format(path=target))} >&2; exit 1; }}"
    put_back = f"if mv -- {old} {quoted} 2>/dev/null; then echo {shlex.quote(NOT_IN_PLACE_TEXT.format(path=target))}; else echo {shlex.quote(BOTH_FAILED_TEXT.format(path=target, aside=aside))}; fi >&2"
    in_place = f"mv -- {new} {quoted} || {{ {put_back}; {half_copy_removal(fresh)}; exit 1; }}"
    tidy = f"rm -rf -- {old} 2>/dev/null || {{ chmod -R u+rwx -- {old} 2>/dev/null; rm -rf -- {old} 2>/dev/null || echo {shlex.quote(OLD_STAYS_TEXT.format(path=aside))}; }}"
    return f"{busy_new}; {busy_old}; {made}; {set_aside}; {in_place}; {tidy}"


def copy_on_server(connection: RemoteConnection, source: str, target: str, replace: bool = False) -> str:
    """One copy made by the server itself: cp where a shell has it, else the protocol (a file only). Never onto a name that is taken,
    unless replace: the person was asked about that one name and said yes, and the copy then takes its place through replace_command,
    which never removes what was there before the new copy is whole (FI-03, G-2). Returns what the person should know about what was
    left behind; empty when nothing was."""
    if source == target:
        raise RemoteActionFailed(SAME_PLACE_TEXT)
    if can_copy(connection.capabilities):
        if replace:
            return run_checked(connection, replace_command(source, target)).strip()
        run_checked(connection, without_overwriting(target, f"{{ {copy_command(source, target)} || {{ {half_copy_removal(target)}; exit 1; }}; }}"))
    else:
        connection.copy_file(source, target)
    return ""


def half_copy_removal(target: str) -> str:
    """The name was free when cp started: what has it after a failed cp is that copy, unfinished, and only that is removed. A copied
    folder that can't be written (cp -a keeps its rights) is first given back its owner's rights, never through a link; what still
    can't be removed is said calmly."""
    quoted = shlex.quote(target)
    remains = shlex.quote(HALF_COPY_REMAINS_TEXT.format(path=target))
    return f"{{ [ -L {quoted} ] || chmod -R u+rwx -- {quoted} 2>/dev/null; rm -rf -- {quoted} || echo {remains} >&2; }}"


def make_link(connection: RemoteConnection, target: str, path: str, hard: bool) -> None:
    if hard and not connection.capabilities.hardlinks:
        raise RemoteActionFailed(NO_HARD_LINKS_TEXT)
    connection.make_link(target, path, hard)


def change_link(connection: RemoteConnection, path: str, target: str) -> None:
    """A link leads somewhere else, and the person's link is never lost: the new link is made under a name of its own and replaces the
    old one in one step where the server can (SFTP's posix-rename, or a shell's ln and mv -T); elsewhere the old link is moved aside,
    the new one renamed in, and the old one removed, and any failure puts the old link back. A leftover new link is removed."""
    folder, name = parent_of(path), path.rsplit("/", 1)[-1]
    fresh = joined(folder, own_name(name, f".{secrets.token_hex(4)}{LINK_NEW_SUFFIX}"))  # a name of its own, never someone's file
    capabilities = connection.capabilities
    if not capabilities.atomic_rename and capabilities.has("ln") and capabilities.has("mv"):
        try:
            run_checked(connection, link_replace_command(target, fresh, path))
            return
        except ConnectionLost:
            raise
        except RemoteActionFailed:
            pass  # a mv without -T (not GNU): the command removed its new link; the steps below do it with the protocol
    connection.make_link(target, fresh)  # refused where anything has the name, a link that leads nowhere too: then nothing of ours exists
    try:
        if capabilities.atomic_rename:
            connection.replace_atomically(fresh, path)
            return
        aside = joined(folder, own_name(name, f".{secrets.token_hex(4)}{LINK_OLD_SUFFIX}"))
        if connection.name_taken(aside):
            raise NameTaken(TARGET_EXISTS_TEXT)
        connection.rename(path, aside)
        try:
            connection.rename(fresh, path)
        except RemoteActionFailed:
            connection.rename(aside, path)  # the old link back first
            raise
    except RemoteActionFailed:
        _quietly_delete(connection, fresh)
        raise
    try:
        connection.delete(aside, False)
    except ConnectionLost:
        raise
    except RemoteActionFailed as error:
        raise RemoteActionFailed(OLD_LINK_LEFT_TEXT.format(name=aside.rsplit("/", 1)[-1], reason=error)) from error


def _quietly_delete(connection: RemoteConnection, path: str) -> None:
    """A link of our own that was not used goes; when it can't (the connection is gone), the first failure is what the person hears."""
    try:
        connection.delete(path, False)
    except RemoteActionFailed:
        pass


def link_replace_command(target: str, fresh: str, path: str) -> str:
    """ln -s to a name of our own, then mv -T, which replaces the link in one step; a failure removes the new link and fails."""
    quoted = shlex.quote(fresh)
    return f"ln -s -- {shlex.quote(target)} {quoted} && {{ mv -Tf -- {quoted} {shlex.quote(path)} || {{ rm -f -- {quoted}; exit 1; }}; }}"


def can_search(capabilities: Capabilities) -> bool:
    return capabilities.has("find")
