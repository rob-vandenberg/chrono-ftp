from typing import Protocol

import keyring
import keyring.errors

from chrono_ftp.core.errors import CredentialError

# --- Version ------------------------------------------------------------
__version__ = "credentials.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: the thin door to the credential store (OR-19)

SERVICE_NAME: str = "chrono-ftp"
UNREACHABLE_TEXT: str = "The password store is not available right now, so the password was not stored."


class CredentialStore(Protocol):
    def read(self, name: str) -> str | None: ...

    def write(self, name: str, password: str) -> None: ...

    def remove(self, name: str) -> None: ...


class KeyringStore:
    """The operating system's store, through keyring (KWallet on KDE)."""

    def read(self, name: str) -> str | None:
        try:
            return keyring.get_password(SERVICE_NAME, name)
        except keyring.errors.KeyringError as error:
            raise CredentialError(UNREACHABLE_TEXT) from error

    def write(self, name: str, password: str) -> None:
        try:
            keyring.set_password(SERVICE_NAME, name, password)
        except keyring.errors.KeyringError as error:
            raise CredentialError(UNREACHABLE_TEXT) from error

    def remove(self, name: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, name)
        except keyring.errors.PasswordDeleteError:
            pass  # nothing was stored
        except keyring.errors.KeyringError as error:
            raise CredentialError(UNREACHABLE_TEXT) from error


store: CredentialStore = KeyringStore()  # the tests put a stand-in here
