"""Addresses in the window: the small window where a person pastes an address to open (FJ-11), and the one that shows an address that
was just put on the clipboard (FJ-12). An address that can't be read says so in one plain sentence and changes nothing (OR-15, OR-23).
A clicked sftp:// link somewhere else on this computer never opens Chrono FTP: this window is the only way in (D-022, OR-25)."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLineEdit, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.core.addresses import EXAMPLE, Address, read_address, understood
from chrono_ftp.ui.plaintext import plain_label, told, wrapped

# --- Version ------------------------------------------------------------
__version__ = "addressdialog.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 10: the help line asks only for %2F in a password, because an @ in a folder needs nothing (FJ-11)
# v0.0.47: Review round 9: under the field stands what Chrono FTP has read from the address, changing as the person types, so nothing is
#          understood behind their back and they see a password they meant as a server before they press Open (FJ-11)
# v0.0.47: Initial: Open an Address… and the address shown after Copy the Address (FJ-11, FJ-12)

WINDOW_TITLE: str = "Open an Address"
ASK_TEXT: str = f"Paste the address of a server, for example\n{EXAMPLE}\nA slash in a password is written %2F."
FIELD_TIP: str = "The address opens as a tab. It is not saved in your connection list."
OPEN_TEXT: str = "Open"
NOTHING_YET_TEXT: str = "Paste an address first."
COPIED_TITLE: str = "Copy the Address"
COPIED_TEXT: str = "This address is on your clipboard now:"


class OpenAddressDialog(QDialog):
    """Paste an address and open it. What Chrono FTP reads from it stands under the field while the person types — the server, the port,
    the user name and the folder — and in the same place the one sentence saying it can't be read at all, with Open greyed and that
    sentence as its reason. So nothing is ever opened other than what the person saw it would be, and what was typed is never lost
    (G-2, OR-20)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.address: Address | None = None
        self.field = QLineEdit()
        self.field.setToolTip(FIELD_TIP)
        self.field.setClearButtonEnabled(True)
        self.said = plain_label()  # what was read from the address, or why it can't be read
        self.said.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        chosen = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.open_button: QPushButton = chosen if chosen is not None else QPushButton(OPEN_TEXT, self)
        self.open_button.setText(OPEN_TEXT)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(ASK_TEXT))
        layout.addWidget(self.field)
        layout.addWidget(self.said)
        layout.addWidget(self.buttons)
        self.field.textChanged.connect(self._typed)
        self.field.returnPressed.connect(self._read)
        self.buttons.accepted.connect(self._read)
        self.buttons.rejected.connect(self.reject)
        self._typed()
        self.field.setFocus()

    def _typed(self) -> None:
        """What the address says, said under the field as the person types; Open is greyed with its reason while there is nothing to
        open (OR-20)."""
        typed = self.field.text()
        self.said.setText(understood(typed))
        read = read_address(typed) if typed.strip() else None
        ready = read is not None and read.address is not None
        self.open_button.setEnabled(ready)
        self.open_button.setToolTip("" if ready else (read.problem if read is not None else NOTHING_YET_TEXT))

    def _read(self) -> None:
        read = read_address(self.field.text())
        if read.address is None:
            self.said.setText(read.problem)  # one plain sentence; nothing else happens (OR-15)
            return
        self.address = read.address
        self.accept()


def show_address(parent: QWidget, address: str) -> None:
    """The address that was just put on the clipboard, shown whole, so the person sees what they are about to paste (FJ-12)."""
    told(parent, COPIED_TITLE, f"{COPIED_TEXT}\n{wrapped(address, parent)}", address)
