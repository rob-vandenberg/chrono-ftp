"""The SSH settings this computer already has, in `~/.ssh`: the servers in `config` and the host keys in `known_hosts`. Both files are
only ever read, never written and never changed (FJ-10): they belong to the person's own SSH setup. Both are input: a line that can't be
read fails only itself, and a file that can't be read gives nothing (OR-15). Reading them needs no screen (AGENTS.md 4.3)."""

import getpass
import re
from dataclasses import dataclass, replace
from fnmatch import fnmatchcase
from pathlib import Path

from chrono_ftp.core.addresses import BAD_CHARACTER, EMPTY_PART, LEADING_DASH, NO_HOST, NO_NUMBER, TOO_LONG_NAME, control_in, named_character, server_name, shortened, unprintable_in
from chrono_ftp.core.connection import DEFAULT_PORTS, FOLDER_SEPARATOR, HIGHEST_PORT, LOWEST_PORT, SFTP, Connection

# --- Version ------------------------------------------------------------
__version__ = "sshsettings.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 8: a name that is no server name says it in the person's words, short, with no keyword of the file in it (FJ-08)
# v0.0.47: Review round 7: a name after Host that is no server name says that, and no refusal promises a version (FJ-08, FJ-09)
# v0.0.47: Review round 6: a network address with the name of a network card after it is no short-hand; square brackets here say to
#          write the address without them; two percent signs stand for one and ~ alone is the person's own home folder; the three
#          refusals Chrono FTP can't mend are short and send nobody to a terminal (FJ-09, OR-25)
# v0.0.47: Review round 5: a setting above the first Host line counts for every server, as OpenSSH reads it; a key file of another
#          person's home folder and a short-hand of OpenSSH's own are said apart, and every refusal says what it means in the person's
#          own words, naming the character or the short-hand it found (FJ-08, FJ-09)
# v0.0.47: Review round 4: the file is read once for every server in it; a value that is not quoted and holds more than one word, and
#          one with a space or a control character in it, refuse their server; a network address needs no brackets here; every refusal
#          says its cause, shortens what it echoes back, stands once, and says what the person can do today (FJ-08, FJ-09)
# v0.0.47: Review round 2: everything after an unquoted # is a comment, as OpenSSH reads it, so a comment after a setting no longer
#          reaches its value and a comment after a Host line no longer makes servers of its words; the port is read through tokens_of
#          as every other value is; IdentityFile none means no key file (FJ-07, FJ-08)
# v0.0.47: Review round 1: a name with a slash in it becomes a name with a space, so an imported server is never a folder of the list
#          (FJ-08); a setting that holds ~, $HOME, %d, %u or %h is worked out, and a server whose setting still holds something that
#          can't be worked out is refused with that reason, as is one whose port is no port; ProxyJump none and the like mean the
#          thing is off and refuse nothing; the answer says apart whether there is no settings file, no server in it, or a file that
#          could not be read (FJ-09, FJ-14)
# v0.0.47: Initial: the servers of ~/.ssh/config, what of them can't come over yet and why, and where this computer's own host keys are
#          (FJ-08, FJ-09, FJ-10)

FOLDER_NAME: str = ".ssh"
CONFIG_NAME: str = "config"
KNOWN_HOSTS_NAME: str = "known_hosts"
HOST: str = "host"  # every keyword is compared in small letters: OpenSSH reads them without regard to case
MATCH: str = "match"
INCLUDE: str = "include"
HOST_NAME: str = "hostname"
PORT: str = "port"
USER: str = "user"
KEY_FILE: str = "identityfile"
WILDCARDS: str = "*?"
MOST_SERVERS: int = 500  # a file is input (OR-15): more servers than a person could ever have are left out
LONGEST_VALUE: int = 4096
BY_HAND: str = "You can make this connection by hand, with Name, Host, Port, User and Key file."  # what a person can do about a refusal
THROUGH_ANOTHER: str = "Your settings reach this server through another server first, which Chrono FTP can't do."
CERTIFICATE: str = "Your settings log in to this server with a certificate file, which Chrono FTP can't use."
ON_A_CARD: str = "Your settings take this server's key from a smart card, which Chrono FTP can't read."
A_MARK: str = "{what} holds {said}, which your SSH program fills in itself and Chrono FTP doesn't know. " + BY_HAND
ANOTHER_HOME: str = "{what} starts in another person's home folder ({said}), which Chrono FTP can't work out. " + BY_HAND
NO_NAME: str = "{what} is no server name ({said}): {why}. " + BY_HAND
ALIAS_NO_NAME: str = "Your settings call this server {said}, which is no server name: {why}."
BRACKETS_HERE: str = "{what} stands between square brackets ({said}), which belong in an address and not here. Write it as {plain}."
MORE_WORDS: str = "{what} holds more than one word ({said}), so which of them is meant can't be told. " + BY_HAND
BAD_CHARACTERS: str = "{what} holds {which}, which can't be in it. " + BY_HAND
BAD_IN_PATH: str = "{what} holds {which}, which can't be in the name of a file or a folder. " + BY_HAND
TOO_LONG: str = "{what} is far longer than any setting a person writes, so Chrono FTP left it unread. " + BY_HAND
UNREADABLE_SERVER: str = f"Chrono FTP could not read the settings of this server. {BY_HAND}"
BAD_PORT: str = f"Its port is {{said}}, and a port is a number from {LOWEST_PORT} to {HIGHEST_PORT}. {BY_HAND}"
SHORT_HAND: re.Pattern[str] = re.compile(r"%.|\$\{?[A-Za-z_][A-Za-z_0-9]*\}?")  # what a settings file writes for what it fills in itself
PERCENT_KEPT: str = "\x00"  # what two percent signs stand for while the others are worked out; it goes back to one percent at the end
OFF: str = "none"  # the value that says a setting is not in use, as OpenSSH reads it
NO_FILE: str = "no file"  # there is no settings file at all
UNREADABLE: str = "unreadable"  # there is one, and it could not be read
READ: str = "read"  # it was read
IN_A_CASE: str = "Your settings change in certain cases only, and Chrono FTP can't tell when, so nothing from this file is taken over."
FROM_ANOTHER_FILE: str = "Your settings pull in another file, which Chrono FTP doesn't read, so nothing from this file is taken over."
WHAT: dict[str, str] = {HOST_NAME: "The server it reaches", USER: "The user name", KEY_FILE: "The key file", PORT: "Its port"}  # in the person's own words
WHY: dict[str, str] = {  # why a name is no server name, in the person's own words, as core/addresses.py tells the causes apart
    NO_HOST: "it is empty",
    BAD_CHARACTER: "a name holds letters, digits, dots, hyphens and underscores, and this holds {which}",
    LEADING_DASH: "no server name starts with a hyphen",
    EMPTY_PART: "a name has something between every two dots, and never ends with one",
    TOO_LONG_NAME: "it is longer than a name can be",
    NO_NUMBER: "it is no network address either",
}
REFUSING: dict[str, str] = {  # a keyword that changes what a login does, in a way Chrono FTP can't follow yet (FJ-09)
    "proxyjump": THROUGH_ANOTHER,
    "proxycommand": THROUGH_ANOTHER,
    "certificatefile": CERTIFICATE,
    "pkcs11provider": ON_A_CARD,
}


@dataclass(frozen=True)
class FoundServer:
    """One server of the settings file: the connection it would become, and every reason it can't come over whole yet. With a reason it
    is never taken over half: it is offered unticked and says why (FJ-09)."""

    connection: Connection
    refusals: tuple[str, ...] = ()

    @property
    def can_come_over(self) -> bool:
        return not self.refusals


@dataclass(frozen=True)
class FoundSettings:
    """The answer of one reading: the servers found, and whether there was a settings file at all and whether it could be read, so the
    window can say each of the three in its own words (FJ-14)."""

    servers: tuple[FoundServer, ...] = ()
    state: str = READ
    path: Path | None = None


def ssh_folder() -> Path:
    """The person's own SSH folder. Nothing in it is ever written (FJ-10)."""
    return Path.home() / FOLDER_NAME


def config_path(folder: Path | None = None) -> Path:
    return (folder or ssh_folder()) / CONFIG_NAME


def known_hosts_path(folder: Path | None = None) -> Path:
    """The host keys this computer already trusts, read as they are: a server trusted from a terminal is not asked about again (FJ-10)."""
    return (folder or ssh_folder()) / KNOWN_HOSTS_NAME


def tokens_of(value: str) -> list[str]:
    """The words of one setting, as OpenSSH reads them: quotes hold a word together and a backslash keeps the next character."""
    found: list[str] = []
    word = ""
    started = False
    quote = ""
    position = 0
    while position < len(value):
        character = value[position]
        if character == "\\" and position + 1 < len(value):
            word += value[position + 1]
            started = True
            position += 2
            continue
        if not quote and character in " \t":
            if started:
                found.append(word)
            word, started = "", False
        elif not quote and character in "\"'":
            quote, started = character, True
        elif quote == character:
            quote = ""
        else:
            word += character
            started = True
        position += 1
    if started:
        found.append(word)
    return found


def without_comment(line: str) -> str:
    """The line without what a # starts, as OpenSSH reads it: a # inside quotes or after a backslash is part of the value."""
    quote = ""
    place = 0
    while place < len(line):
        character = line[place]
        if character == "\\":
            place += 2  # the character after a backslash is part of the value, whatever it is
            continue
        if not quote and character in "\"'":
            quote = character
        elif quote == character:
            quote = ""
        elif not quote and character == "#":
            return line[:place]
        place += 1
    return line


def directive_of(line: str) -> tuple[str, str] | None:
    """The keyword of one line in small letters and what stands after it; None for a comment, an empty line or a line without a value."""
    text = without_comment(line).strip()
    if not text:
        return None
    cut = len(text)
    for separator in (" ", "\t", "="):
        found = text.find(separator)
        if 0 <= found < cut:
            cut = found
    if cut == len(text):
        return None
    keyword, rest = text[:cut], text[cut:].lstrip(" \t")
    if rest.startswith("="):
        rest = rest[1:].lstrip(" \t")
    return (keyword.casefold(), rest) if keyword and rest else None


def _matches(patterns: list[str], name: str) -> bool:
    """Whether a Host line's patterns cover this server. A pattern with ! before it rules the server out, whatever else matches, as
    OpenSSH reads it."""
    hit = False
    for pattern in patterns:
        if pattern.startswith("!"):
            if fnmatchcase(name, pattern[1:]):
                return False
            continue
        if fnmatchcase(name, pattern):
            hit = True
    return hit


def worked_out(value: str, home: Path, user: str, alias: str) -> str | None:
    """One setting with everything in it worked out: ~ and $HOME and %d are the home folder, %u the person's own name and %h the server
    the settings call by this name, as OpenSSH reads them. None where something is left that can't be worked out, so nothing is ever
    taken over half (FJ-09)."""
    text = value.strip()
    if PERCENT_KEPT in text:
        return None  # no setting a person writes holds this character, and it is the mark two percent signs are kept as
    if text == "~" or text.startswith("~/"):
        text = str(home) + text[1:]  # the person's own home folder, with or without anything after it
    text = text.replace("%%", PERCENT_KEPT)  # two of them stand for one percent sign, as OpenSSH reads it: no short-hand at all
    for token, stands_for in (("${HOME}", str(home)), ("$HOME", str(home)), ("%d", str(home)), ("%u", user), ("%h", alias)):
        text = text.replace(token, stands_for)
    if "%" in text or "$" in text or text.startswith("~"):
        return None  # a short-hand of OpenSSH's own that Chrono FTP does not know: the server comes over as a whole or not at all
    return text.replace(PERCENT_KEPT, "%")


def _port(value: str) -> int | None:
    """The port a setting names; None when it is no port. Only ASCII digits are a port: a digit of another language looks like one to
    isdigit and is no number int can read, which would be a crash on a file (OR-15)."""
    typed = value.strip()
    if not typed.isascii() or not typed.isdigit():
        return None
    number = int(typed)
    return number if LOWEST_PORT <= number <= HIGHEST_PORT else None


@dataclass(frozen=True)
class Directive:
    """One line of the settings as it was read once: its keyword in small letters, its value as written, and its value in words."""

    keyword: str
    value: str
    words: tuple[str, ...]


def directives_in(text: str) -> list[Directive]:
    """Every line of the settings read once, for every server there is: reading the file again for each server made a big file take
    seconds, and a settings file is read while a person waits (G-2)."""
    found: list[Directive] = []
    for line in text.splitlines():
        directive = directive_of(line)
        if directive is not None:
            keyword, value = directive
            found.append(Directive(keyword, value, tuple(tokens_of(value))))
    return found


def _settings_for(alias: str, directives: list[Directive], home: Path, user: str) -> FoundServer:
    """What the file says about this one server: the first value of each keyword wins, as OpenSSH reads it, and only the Host sections
    that cover this server are read. The name of the connection holds no slash, so an imported server is never a folder of the list
    (FJ-08). The server it reaches is worked out first, because %u and %d of the other settings may stand beside %h, which is that
    server, as OpenSSH reads it. Nothing is ever taken over half: a setting that can't be read refuses the whole server (FJ-09)."""
    said: dict[str, str] = {}  # the first value of each keyword that counts for this server, as the file writes it
    refusals: list[str] = []
    used: set[str] = set()
    inside = True  # what stands above the first Host line counts for every server, as OpenSSH reads it (FJ-08)
    for directive in directives:
        keyword, value, words = directive.keyword, directive.value, directive.words
        if keyword == HOST:
            inside = _matches(list(words), alias)
            continue
        if keyword == MATCH:
            inside = False
            continue
        if not inside or keyword in used:
            continue
        if keyword in REFUSING:
            used.add(keyword)
            if words and words[0].casefold() != OFF:
                refusals.append(REFUSING[keyword])  # "none" says this setting is not in use: then there is nothing in the way
            continue
        if keyword not in (PORT, HOST_NAME, USER, KEY_FILE):
            continue
        used.add(keyword)
        if len(value) > LONGEST_VALUE:  # input (OR-15): too long to read, so this server comes over whole or not at all
            refusals.append(TOO_LONG.format(what=WHAT[keyword]))
        elif len(words) > 1:  # "HostName my server": which of the words is meant can't be told, so nothing of this server comes over
            refusals.append(MORE_WORDS.format(what=WHAT[keyword], said=shortened(value.strip())))
        elif words:
            said[keyword] = words[0]
    host, host_refusal = _host_of(alias, said.get(HOST_NAME), home, user)
    connection = Connection(name=alias.replace(FOLDER_SEPARATOR, " "), host=host, port=DEFAULT_PORTS[SFTP], protocol=SFTP)
    refusals.extend(host_refusal)
    if PORT in said:
        port = _port(said[PORT])
        if port is None:
            refusals.append(BAD_PORT.format(said=shortened(said[PORT])))
        else:
            connection = replace(connection, port=port)
    for keyword in (USER, KEY_FILE):
        if keyword not in said or (keyword == KEY_FILE and said[keyword].casefold() == OFF):
            continue  # "none" says there is no key file, as OpenSSH reads it: never a key file of that name
        found = worked_out(said[keyword], home, user, host)  # %h is the server the connection really uses
        if found is None:
            refusals.append(_cant_work_out(WHAT[keyword], said[keyword], keyword == KEY_FILE))
        elif control_in(found) if keyword == USER else unprintable_in(found):  # it would reach the login as it stands (OR-15, OR-19)
            sentence = BAD_CHARACTERS if keyword == USER else BAD_IN_PATH
            refusals.append(sentence.format(what=WHAT[keyword], which=named_character(found, keyword != USER)))
        elif keyword == USER:
            connection = replace(connection, user=found)
        else:
            connection = replace(connection, key_file=found)
    return FoundServer(connection, tuple(dict.fromkeys(refusals)))  # one line per cause, however often the file says it


def _cant_work_out(what: str, value: str, in_a_path: bool = False) -> str:
    """Why a setting can't be worked out, in the person's own words: a character that can be in no name or path, a path in another
    person's home folder, or a short-hand the person's SSH program fills in itself, named as the file writes it (FJ-09)."""
    if control_in(value) if not in_a_path else unprintable_in(value):
        sentence = BAD_IN_PATH if in_a_path else BAD_CHARACTERS
        return sentence.format(what=what, which=named_character(value, in_a_path))
    if value.startswith("~") and value != "~" and not value.startswith("~/"):
        return ANOTHER_HOME.format(what=what, said=shortened(value.partition("/")[0]))
    found = SHORT_HAND.search(value)
    return A_MARK.format(what=what, said=shortened(found.group() if found is not None else value))


def _host_of(alias: str, named: str | None, home: Path, user: str) -> tuple[str, tuple[str, ...]]:
    """The server this one reaches, and the reasons it can't come over: the name after Host unless HostName says another, every token
    worked out, and the name itself checked exactly as a pasted address is checked, so one rule says what a server name is (FJ-09)."""
    if named is not None and server_name(named.strip(), False, bare_number=True)[1] == "":
        return named.strip(), ()  # a network address, scope and all: nothing in it is a short-hand (FJ-09)
    if named is not None and named.strip().startswith("[") and named.strip().endswith("]"):
        return named.strip(), (BRACKETS_HERE.format(what=WHAT[HOST_NAME], said=shortened(named.strip()), plain=shortened(named.strip()[1:-1])),)
    typed = alias if named is None else worked_out(named, home, user, alias)  # in HostName itself, %h is the name after Host
    if typed is None:
        return alias, (_cant_work_out(WHAT[HOST_NAME], named or ""),)
    host, cause = server_name(typed, False, bare_number=True)  # as the settings write one: a network address needs no brackets there
    if not cause:
        return host, ()
    why = WHY[cause].format(which=named_character(typed)) if cause == BAD_CHARACTER else WHY[cause]
    if named is None:  # the person wrote no server at all: the bad word is the name after Host (FJ-08)
        return typed, (ALIAS_NO_NAME.format(said=shortened(typed), why=why),)
    return typed, (NO_NAME.format(what=WHAT[HOST_NAME], said=shortened(typed), why=why),)


def servers_in(text: str, home: Path | None = None, user: str = "") -> tuple[FoundServer, ...]:
    """Every server the settings say something about, in the order the file names them. A name with * or ? in it is no server of its
    own: it is a rule for the others, as OpenSSH reads it. A Match section or an Include line makes every server say that nothing is
    taken over, because what they change can't be judged yet (FJ-09)."""
    directives = directives_in(text)  # the file is read once, for every server in it (G-2)
    whole: list[str] = []
    found: list[str] = []
    for directive in directives:
        if directive.keyword == INCLUDE:
            whole.append(FROM_ANOTHER_FILE)
        elif directive.keyword == MATCH:
            whole.append(IN_A_CASE)
        elif directive.keyword == HOST:
            for pattern in directive.words:
                if pattern and not pattern.startswith("!") and not any(sign in pattern for sign in WILDCARDS) and pattern not in found:
                    found.append(pattern)
    everywhere = tuple(dict.fromkeys(whole))
    home = home or Path.home()
    whoever = user or getpass.getuser()
    servers = [_one_server(name, directives, home, whoever) for name in found[:MOST_SERVERS]]
    return tuple(replace(server, refusals=everywhere + server.refusals) for server in servers)


def _one_server(alias: str, directives: list[Directive], home: Path, user: str) -> FoundServer:
    """One server, and a server whose settings can't be read at all refuses only itself: a settings file is input, and one odd line in
    it may never cost the person every other server of the file (OR-15)."""
    try:
        return _settings_for(alias, directives, home, user)
    except Exception:  # noqa: BLE001 - whatever one server's settings do, the others are still read
        return FoundServer(Connection(name=alias.replace(FOLDER_SEPARATOR, " "), host=alias, port=DEFAULT_PORTS[SFTP], protocol=SFTP), (UNREADABLE_SERVER,))


def read_settings(folder: Path | None = None) -> FoundSettings:
    """The servers of the settings file, and which of the three it was: no settings file at all, one that could not be read, or one that
    was read (FJ-14). The file is only ever read, never written (FJ-10)."""
    path = config_path(folder)
    try:
        text = path.read_bytes().decode("utf-8", "replace")
    except OSError:
        return FoundSettings((), NO_FILE if not path.exists() else UNREADABLE, path)
    return FoundSettings(servers_in(text, (folder or ssh_folder()).parent), READ, path)
