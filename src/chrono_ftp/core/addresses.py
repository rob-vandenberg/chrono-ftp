"""An address of a server, as a person pastes one: `sftp://user@server.example.com:2222/var/log`. Reading one and writing one are pure
text work, so both are tested without a screen (AGENTS.md 4.3). A pasted address is input: nothing in it is trusted (OR-15), and a
password in it is used for that one login and never written anywhere (OR-19)."""

import re
from dataclasses import dataclass
from ipaddress import IPv6Address
from urllib.parse import quote, unquote

from chrono_ftp.core.connection import DEFAULT_PORTS, FTP, HIGHEST_PORT, LOWEST_PORT, SFTP, Connection

# --- Version ------------------------------------------------------------
__version__ = "addresses.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 10: where no user name was read and the folder holds an @, the person reads that too, so a password meant
#          and written without %2F stands out where the eye lands (FJ-11)
# v0.0.47: Review round 9: nothing about an address is guessed at any more: it is read the one way addresses are built, and what came
#          out of it is said in plain words for the person to read while they type (`understood`), so an ordinary address with an @ in
#          its folder beside a port opens again and the address of "Copy the Address" always reads back (FJ-11, FJ-12)
# v0.0.47: Review round 8: an address that can be read as a server with a port or as a user name with a password is read as neither:
#          the person is told what is unclear and how to write both, in place of a guess at which it was (FJ-11)
# v0.0.47: Review round 7: a slash in a password says so, in place of the sentence about the port it broke on (FJ-11)
# v0.0.47: Review round 6: the user name and the password are looked for before the first slash of the server part and nowhere else,
#          so no address is ever read as another server; a port given twice says so (FJ-11)
# v0.0.47: Review round 5: an @ in a folder is a folder, not a user name; a network address without brackets, and anything but a port
#          after the brackets, each say what to do; the character that makes a name no name is named (FJ-11)
# v0.0.47: Review round 4: one place says why a server name is no name, as a cause both a pasted address and the SSH settings put in
#          their own words; a bare network address is a server where no port can follow it; a text with a control character in it is
#          answered for both (FJ-08, FJ-11)
# v0.0.47: Review round 3: every cause of a refusal says its own thing, and the two sentences about the square brackets say what they
#          are about in the person's own words (FJ-11)
# v0.0.47: Review round 2: one place reads a server name, with an international name worked out to the form the network uses,
#          underscores accepted, every part and the whole name within its length, the scope of a server number checked, only ASCII
#          digits as a port, a folder without control characters, and a sentence of its own for every cause; the user name and the
#          password end at the last @, so a password with a slash in it is read as one (FJ-11)
# v0.0.47: Review round 1: only a plain server name or a server number in brackets is a server, and a user name with a space or a
#          control character in it is refused, so nothing of an address can ever reach a command as an option (FJ-11); a starting
#          folder that is not absolute is left out of an address, which then means the home folder of the login (FJ-12)
# v0.0.47: Initial: an address read from what a person pasted, and an address made from a connection and a folder or file, never with a
#          password in it (FJ-11, FJ-12)

SCHEMES: dict[str, str] = {"sftp": SFTP, "ftp": FTP}  # the two kinds of connection Chrono FTP has (D-009)
SCHEME_END: str = "://"
LONGEST_ADDRESS: int = 4096  # what a person can paste is input (OR-15); longer than this is no address anyone typed
LONGEST_HOST: int = 253  # the longest server name the network itself allows, and the longest part of one
LONGEST_LABEL: int = 63
EXAMPLE: str = "sftp://user@server.example.com/var/log"
EMPTY_PROBLEM: str = f"Paste an address first, for example {EXAMPLE}"
SCHEME_PROBLEM: str = f"An address starts with sftp:// or ftp://, for example {EXAMPLE}"
NO_HOST: str = "nothing"  # the causes a server name can be no server name; each side says them in its own words
BAD_CHARACTER: str = "character"
LEADING_DASH: str = "dash"
EMPTY_PART: str = "empty part"
TOO_LONG_NAME: str = "too long"
NO_NUMBER: str = "no number"
HOST_PROBLEM: str = "This address names no server, so there is nothing to open."
NAME_PROBLEM: str = "The server in this address holds {what}. A name holds letters, digits, dots, hyphens and underscores."
DASH_PROBLEM: str = "The server in this address starts with a hyphen, which no server name does."
DOT_PROBLEM: str = "The server in this address has an empty part: a name has something between every two dots, and never ends with one."
LONG_NAME_PROBLEM: str = f"The server name in this address is longer than a name can be ({LONGEST_HOST} characters, and {LONGEST_LABEL} per part)."
NUMBER_PROBLEM: str = "What stands between the square brackets in this address is no network address, such as 2001:db8::1."
PORT_PROBLEM: str = f"The port in this address must be a number from {LOWEST_PORT} to {HIGHEST_PORT}."
BRACKET_PROBLEM: str = "An address like this puts the server between square brackets, and this one never closes them."
LONG_PROBLEM: str = "This is far longer than any address, so Chrono FTP did not try to read it."
USER_SPACE_PROBLEM: str = "The user name in this address holds a space, which no user name has."
USER_CHARACTER_PROBLEM: str = "The user name in this address holds {what}, which can't be in a user name."
BRACKETS_NEEDED: str = "A network address goes between square brackets in an address, like sftp://[2001:db8::1]/var/log, because a colon after the server names the port."
AFTER_BRACKET_PROBLEM: str = "After the square brackets in this address comes something that is no port: a colon and the port number, or nothing at all."
MORE_PORTS_PROBLEM: str = "This address names a port more than once. A server has one port, after one colon."
FOLDER_PROBLEM: str = "The folder in this address holds {what}, which can't be in the name of a folder."
UNDERSTOOD_HEADING: str = "This address says:"  # what the program has read from it, in the window while the person types (FJ-11)
SERVER_SAID: str = "Server: {host}"
PORT_SAID: str = "Port: {port}"
USER_SAID: str = "User name: {user}"
NO_USER_SAID: str = "User name: none"
FOLDER_SAID: str = "Folder: {folder}"
HOME_SAID: str = "Folder: wherever this login starts"
PASSWORD_SAID: str = "A password came with it, used for this one login and written nowhere."
NO_USER_WITH_AT_SAID: str = "No user name was read, although the folder holds an @. A password with a slash in it is written %2F."
LONGEST_SAID_FOLDER: int = 100  # a folder read back can be longer than a name, because it is the longest part of an address
PROBLEMS: dict[str, str] = {  # the same causes, as a person pasting an address reads them
    NO_HOST: HOST_PROBLEM,
    BAD_CHARACTER: NAME_PROBLEM,
    LEADING_DASH: DASH_PROBLEM,
    EMPTY_PART: DOT_PROBLEM,
    TOO_LONG_NAME: LONG_NAME_PROBLEM,
    NO_NUMBER: NUMBER_PROBLEM,
}
HOST_NAME: re.Pattern[str] = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._\-]*")  # a server name as the network uses it: letters, digits, dots,
# hyphens and underscores, never a hyphen first, so no part of an address can ever reach a command as an option of its own (FJ-11)
SCOPE_NAME: re.Pattern[str] = re.compile(r"[A-Za-z0-9._\-]+")  # the part after % in a network address: the name of a network card
NAME_CHARACTER: re.Pattern[str] = re.compile(r"[A-Za-z0-9._\-]")  # one character a server name may hold
SAID_CHARACTER: str = "the character {said}"
MOST_COLONS_IN_A_NAME: int = 1  # more colons than this, without brackets, means a network address was typed as it stands


@dataclass(frozen=True)
class Address:
    """What one address says: the connection to make, the password that came with it, and the folder or file it points at."""

    connection: Connection
    password: str = ""  # only ever used for this one login; never saved and never written down (OR-19)
    folder: str = ""  # empty: wherever the login starts


@dataclass(frozen=True)
class AddressRead:
    """The answer to one pasted address: either the address, or one plain sentence saying what is wrong with it."""

    address: Address | None = None
    problem: str = ""


def free_name(wanted: str, taken: set[str]) -> str:
    """The first name not taken yet: "server", then "server (2)". A tab needs a name of its own, and a pasted address gets no name from
    the person."""
    if wanted not in taken:
        return wanted
    number = 2
    while f"{wanted} ({number})" in taken:
        number += 1
    return f"{wanted} ({number})"


def _host_and_port(text: str, protocol: str) -> tuple[str, int, str]:
    """The server and its port from what stands before the first slash; the third value is the problem, empty when there is none."""
    in_brackets = text.startswith("[")
    if in_brackets:
        close = text.find("]")
        if close < 0:
            return "", 0, BRACKET_PROBLEM
        typed_host, rest = text[1:close], text[close + 1 :]
        if rest and not rest.startswith(":"):
            return "", 0, AFTER_BRACKET_PROBLEM  # "[::1]x": what follows the brackets is no port at all
    elif text.count(":") > MOST_COLONS_IN_A_NAME:
        return "", 0, BRACKETS_NEEDED if _is_number(text) else MORE_PORTS_PROBLEM  # a network address, or a port given twice
    else:
        typed_host, _found, rest = text.partition(":")
        rest = f":{rest}" if _found else ""
    typed = unquote(typed_host.strip())  # trimmed before it is read back, so nothing hides behind a percent sign
    host, cause = server_name(typed, in_brackets)
    if cause:
        return "", 0, PROBLEMS[cause].format(what=named_character(typed)) if cause == BAD_CHARACTER else PROBLEMS[cause]
    if not rest:
        return host, DEFAULT_PORTS[protocol], ""
    if not rest.startswith(":"):
        return "", 0, PORT_PROBLEM
    typed = unquote(rest[1:]).strip()
    if not typed.isascii() or not typed.isdigit():  # a digit of another language is no port, however much it looks like one
        return "", 0, PORT_PROBLEM
    port = int(typed)
    if not LOWEST_PORT <= port <= HIGHEST_PORT:
        return "", 0, PORT_PROBLEM
    return host, port, ""


def _is_number(text: str) -> bool:
    """Whether this is a network address as it stands, so the sentence can say to put it between square brackets (FJ-11)."""
    try:
        IPv6Address(text.partition("%")[0])
    except ValueError:
        return False
    return True


def server_name(typed: str, in_brackets: bool, bare_number: bool = False) -> tuple[str, str]:
    """The one place a server name is read: the name as the network uses it, and the cause it is no name, empty when it is one. A name
    with letters of another language is worked out to the form the network uses, so a person can paste the name they know; everything
    that is no server name at all is refused, so nothing of it can ever reach a command as an option (FJ-11). bare_number: a network
    address may stand there without brackets, as this computer's own SSH settings write one (FJ-08); in an address it may not, because
    a colon there belongs to the port."""
    if not typed:
        return "", NO_HOST
    if in_brackets or (bare_number and ":" in typed):
        number, _found, scope = typed.partition("%")  # the name of a network card may follow a network address
        try:
            IPv6Address(number)
        except ValueError:
            return "", NO_NUMBER
        if _found and SCOPE_NAME.fullmatch(scope) is None:
            return "", NO_NUMBER
        return typed, ""
    host = typed
    if not host.isascii():
        try:
            host = ".".join(part.encode("idna").decode("ascii") for part in host.split("."))
        except (UnicodeError, ValueError):
            return "", BAD_CHARACTER  # the letters it holds are no name in any language
    if len(host) > LONGEST_HOST or any(len(part) > LONGEST_LABEL for part in host.split(".")):
        return "", TOO_LONG_NAME
    if any(not part for part in host.split(".")):  # "a..b" and a name that ends in a dot are no names
        return "", EMPTY_PART
    if host.startswith("-"):  # a name that starts like an option could reach a command as one (FJ-11)
        return "", LEADING_DASH
    return (host, "") if HOST_NAME.fullmatch(host) is not None else ("", BAD_CHARACTER)


def named_character(text: str, only_control: bool = False) -> str:
    """The first character of a text that can be in no name, said as a person would say it. For the sentences that refuse a name or a
    path: a person can only put right what they can see (OR-23). only_control: for a path, where a space, a slash and much else are
    characters a file name may hold, so only an invisible one is named."""
    for character in text:
        if character == " ":
            return "a space"
        if character == "\t":
            return "a tab"
        if character in "\r\n":
            return "a line break"
        if ord(character) < 32 or ord(character) == 127:
            return f"an invisible character (number {ord(character)})"
        if character.isspace():
            return "a space"
        if not only_control and NAME_CHARACTER.fullmatch(character) is None:
            return SAID_CHARACTER.format(said=character)
    return "a character that can't be in it"


def control_in(text: str) -> bool:
    """Whether a text holds a space or a character no name or path of a server can hold, whatever it was written as. One answer for a
    pasted address and for a setting of this computer's own SSH settings alike (FJ-08, FJ-11)."""
    return any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in text)


def unprintable_in(text: str) -> bool:
    """Whether a text holds a character no path can hold: a NUL or another control character. A space is a character a folder or a key
    file may hold, so a path is asked this and a name is asked control_in (OR-15)."""
    return any(ord(character) < 32 or ord(character) == 127 for character in text)


def shortened(text: str, room: int = 60) -> str:
    """A value of the person's own file or address, short enough to read back in a sentence; a longer one ends in an ellipsis."""
    return text if len(text) <= room else text[: room - 1] + "…"


def _split(rest: str, protocol: str) -> tuple[str, str, str]:
    """What stands after sftp:// pulled apart: the user name with its password, the server with its port, and the folder. One reading
    only: the folder starts at the first slash, and the user name with its password ends at the last @ before that slash. So an @ in a
    folder is a folder, and an address that doesn't read this way is told what is wrong with it instead of being read as something else
    (FJ-11). A password that holds a slash is written %2F, as a password in an address always is."""
    before_slash, slash, path = rest.partition("/")
    login, found_at, server = before_slash.rpartition("@")
    if not found_at:
        login, server = "", before_slash
    return login, server, slash + path if slash else ""


def read_address(text: str, name: str = "") -> AddressRead:
    """One address as a person pasted it. name: the name the connection gets; empty: the server's own name. Whatever is wrong with it,
    the answer is one sentence a person understands, and nothing else happens (OR-15, OR-23)."""
    typed = text.strip()
    if not typed:
        return AddressRead(problem=EMPTY_PROBLEM)
    if len(typed) > LONGEST_ADDRESS:
        return AddressRead(problem=LONG_PROBLEM)
    scheme, found, rest = typed.partition(SCHEME_END)
    protocol = SCHEMES.get(scheme.casefold()) if found else None
    if protocol is None:
        return AddressRead(problem=SCHEME_PROBLEM)
    login, server, typed_folder = _split(rest, protocol)
    user, _found_colon, password = login.partition(":")
    host, port, problem = _host_and_port(server, protocol)
    if problem:
        return AddressRead(problem=problem)
    typed_user = unquote(user.strip())  # as the server name: trimmed first, so nothing can hide behind a percent sign
    if " " in typed_user:
        return AddressRead(problem=USER_SPACE_PROBLEM)
    if control_in(typed_user):
        return AddressRead(problem=USER_CHARACTER_PROBLEM.format(what=named_character(typed_user)))
    folder = unquote(typed_folder)
    if unprintable_in(folder):
        return AddressRead(problem=FOLDER_PROBLEM.format(what=named_character(folder, True)))  # never to the server (OR-15)
    connection = Connection(name=name or host, host=host, user=typed_user, port=port, protocol=protocol)
    return AddressRead(Address(connection, unquote(password), folder))


def understood(text: str) -> str:
    """What Chrono FTP has made of what a person has typed so far, in plain words, or the one sentence saying it can't be read at all.
    An address is read one way (`_split`), and a person reading what came out of it sees at once when they meant something else: who
    pastes `sftp://user:12/ss@server/x` meaning a password reads "Server: user" and "Port: 12" before pressing Open (FJ-11, G-2).
    Empty while nothing is typed: there is nothing to say yet."""
    typed = text.strip()
    if not typed:
        return ""
    read = read_address(typed)
    address = read.address
    if address is None:
        return read.problem
    connection = address.connection
    said = [
        UNDERSTOOD_HEADING,
        SERVER_SAID.format(host=shortened(connection.host)),
        PORT_SAID.format(port=connection.port),
        USER_SAID.format(user=shortened(connection.user)) if connection.user else NO_USER_SAID,
        FOLDER_SAID.format(folder=shortened(address.folder, LONGEST_SAID_FOLDER)) if address.folder else HOME_SAID,
    ]
    if address.password:
        said.append(PASSWORD_SAID)  # that one came is said, never what it is (OR-19)
    if not connection.user and "@" in address.folder:  # a reading of what was typed, not a guess at what was meant (FJ-11)
        said.append(NO_USER_WITH_AT_SAID)
    return "\n".join(said)


def login_text(connection: Connection) -> str:
    """The server as a command names it: user@host, or host alone. A network address that holds colons goes between square brackets,
    because a path follows it after a colon and rsync would otherwise read the whole thing as something else (FJ-11)."""
    host = f"[{connection.host}]" if ":" in connection.host else connection.host
    return f"{connection.user}@{host}" if connection.user else host


def address_text(connection: Connection, path: str = "") -> str:
    """The address of this connection, and of this folder or file on it. The user name is in it, a password never (OR-19). The port is
    in it only where it is not the usual one, so the shortest address that still says everything comes out. A folder that is not an
    absolute path is left out, because an address can only say a path from the root: the address then means the folder the login
    starts in, which is where that connection opens (FJ-12)."""
    scheme = next(name for name, protocol in SCHEMES.items() if protocol == connection.protocol)
    host = f"[{connection.host}]" if ":" in connection.host else connection.host
    port = "" if connection.port == DEFAULT_PORTS[connection.protocol] else f":{connection.port}"
    user = f"{quote(connection.user, safe='')}@" if connection.user else ""
    place = quote(path, safe="/") if path.startswith("/") else ""
    return f"{scheme}{SCHEME_END}{user}{host}{port}{place}"
