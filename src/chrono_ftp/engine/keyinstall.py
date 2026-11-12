"""Install my public key on the server (FM-09): over SFTP alone, without a shell, the key is added to ~/.ssh/authorized_keys once, and
the folder and the file are kept so that the SSH server accepts them. Nothing of the key is ever logged."""

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import asyncssh

from chrono_ftp.connections.base import RemoteConnection, joined
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.errors import LocalActionFailed, RemoteActionFailed
from chrono_ftp.engine.rights import mode_number

# --- Version ------------------------------------------------------------
__version__ = "keyinstall.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Initial: a public key read and checked, and added to authorized_keys only when it is not there yet; .ssh made with 700 and
#          authorized_keys with 600, and rights that others could write put right (D-062); a comment with letters beyond ASCII is kept;
#          a server that refuses to tell about .ssh or authorized_keys says so in its own words, never taken for missing; a temporary
#          copy this computer can't make is told calmly

SSH_FOLDER: str = ".ssh"
KEYS_FILE: str = "authorized_keys"
FOLDER_RIGHTS: int = 0o700
FILE_RIGHTS: int = 0o600
OTHERS_WRITE: int = 0o022  # the SSH server refuses a key file or folder that the group or others can write to
MOST_KEY_BYTES: int = 64 * 1024  # a public key is a line; a bigger file is something else
STANDARD_KEYS: tuple[str, ...] = ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub")
PRIVATE_TEXT: str = "This is a private key. Please choose the public key: the file of the same name that ends in .pub."
NOT_A_KEY_TEXT: str = "This file holds no public key that Chrono FTP can read."
TOO_BIG_TEXT: str = "This file is too large to be a public key."
NOT_READ_TEXT: str = "The key file could not be read: {reason}."
SSH_IS_A_FILE_TEXT: str = "There is a file called .ssh in your home folder on the server, where a folder is needed, so the key was not added."
KEYS_IS_A_FOLDER_TEXT: str = "There is a folder called authorized_keys in .ssh on the server, where a file is needed, so the key was not added."
MADE_FOLDER_TEXT: str = "Made the folder .ssh in your home folder, which only you can open."
FOLDER_PUT_RIGHT_TEXT: str = "Others could write to your .ssh folder, which the SSH server does not accept; now only you can."
MADE_FILE_TEXT: str = "Made the file .ssh/authorized_keys with your key; only you can read and change it."
ADDED_TEXT: str = "Added your key to .ssh/authorized_keys."
ALREADY_TEXT: str = "Your key is in .ssh/authorized_keys already, so nothing was added."
TEMPORARY_TEXT: str = "This computer could not make the temporary copy it needs ({reason}), so the key was not added."
FILE_PUT_RIGHT_TEXT: str = "Others could write to .ssh/authorized_keys, which the SSH server does not accept; now only you can."


@dataclass(frozen=True)
class PublicKey:
    line: str  # as authorized_keys holds it: kind, key and comment
    blob: str  # the key itself, which tells it apart from any other
    comment: str


def read_public_key(path: str) -> PublicKey:
    """A public key file is input: a private key, another file or a damaged one is refused with its reason."""
    try:
        with Path(path).open("rb") as file:
            data = file.read(MOST_KEY_BYTES + 1)
    except OSError as error:
        raise LocalActionFailed(NOT_READ_TEXT.format(reason=error.strerror or error)) from error
    if len(data) > MOST_KEY_BYTES:
        raise LocalActionFailed(TOO_BIG_TEXT)
    if b"PRIVATE KEY" in data:
        raise LocalActionFailed(PRIVATE_TEXT)
    try:
        key = asyncssh.import_public_key(data)
        line = key.export_public_key("openssh").decode("utf-8").strip()  # a comment may hold any letter, such as rob@mäc
    except (asyncssh.KeyImportError, ValueError, UnicodeDecodeError) as error:
        raise LocalActionFailed(NOT_A_KEY_TEXT) from error
    fields = line.split()
    if len(fields) < 2 or "\n" in line:
        raise LocalActionFailed(NOT_A_KEY_TEXT)
    comment = key.get_comment() or ""
    return PublicKey(line, fields[1], comment)


def suggested_key_file(connection: Connection) -> str:
    """The public key of the connection's own key file first, else a standard key of this computer's user; empty when there is none."""
    candidates = [connection.key_file + ".pub"] if connection.key_file else []
    candidates += [str(Path.home() / SSH_FOLDER / name) for name in STANDARD_KEYS]
    return next((name for name in candidates if os.path.isfile(name)), "")


def holds_key(content: bytes, key: PublicKey) -> bool:
    """Whether a line of authorized_keys holds this key, also with options before it or another comment after it."""
    return any(key.blob in line.split() for line in content.decode("utf-8", "replace").splitlines() if not line.lstrip().startswith("#"))


def _none(_amount: int) -> None:
    pass


def _only_mine(connection: RemoteConnection, path: str, permissions: str, wanted: int) -> bool:
    """Rights others could write to are put right; True when something changed."""
    mode = mode_number(permissions)
    if mode is None or not mode & OTHERS_WRITE:
        return False
    connection.set_rights(path, format(wanted if wanted == FILE_RIGHTS else mode & ~OTHERS_WRITE, "04o"))
    return True


def install_key(connection: RemoteConnection, key: PublicKey) -> list[str]:
    """Adds the key to the server's ~/.ssh/authorized_keys through the protocol alone, and says what it did, step by step."""
    done: list[str] = []
    folder = joined(connection.home(), SSH_FOLDER)
    found = connection.details_or_refusal(folder)
    if found is None:
        connection.make_folder(folder)
        connection.set_rights(folder, format(FOLDER_RIGHTS, "04o"))
        done.append(MADE_FOLDER_TEXT)
    elif not found.is_folder:
        raise RemoteActionFailed(SSH_IS_A_FILE_TEXT)
    elif _only_mine(connection, folder, found.permissions, FOLDER_RIGHTS):
        done.append(FOLDER_PUT_RIGHT_TEXT)
    keys_file = joined(folder, KEYS_FILE)
    existing = connection.details_or_refusal(keys_file)
    try:
        with tempfile.TemporaryDirectory(prefix="chrono-ftp-key-", ignore_cleanup_errors=True) as temporary:
            here = os.path.join(temporary, KEYS_FILE)
            if existing is None:
                Path(here).write_text(key.line + "\n", encoding="utf-8")
                connection.upload(here, keys_file, _none, exclusive=True)
                connection.set_rights(keys_file, format(FILE_RIGHTS, "04o"))
                done.append(MADE_FILE_TEXT)
                return done
            if existing.is_folder:
                raise RemoteActionFailed(KEYS_IS_A_FOLDER_TEXT)
            connection.download(keys_file, here, _none)
            content = Path(here).read_bytes()
            if holds_key(content, key):
                done.append(ALREADY_TEXT)
            else:
                addition = os.path.join(temporary, "addition")
                Path(addition).write_text(("\n" if content and not content.endswith(b"\n") else "") + key.line + "\n", encoding="utf-8")
                connection.upload(addition, keys_file, _none, append=True)  # one append: the keys that are there stay as they are
                done.append(ADDED_TEXT)
    except OSError as error:  # this computer's temporary folder (a full or closed disk); the server's steps raise their own errors
        raise LocalActionFailed(TEMPORARY_TEXT.format(reason=error.strerror or error)) from error
    if _only_mine(connection, keys_file, existing.permissions, FILE_RIGHTS):
        done.append(FILE_PUT_RIGHT_TEXT)
    return done
