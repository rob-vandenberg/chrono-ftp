"""The window of the SSH settings this computer already has: every server found, each with a tick, and every server that can't come
over whole saying in plain words why, unticked (FJ-08, FJ-09). Nothing in `~/.ssh` is ever written: the settings are only read (FJ-10)."""

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.core.connection import Connection
from chrono_ftp.core.sshsettings import FoundServer
from chrono_ftp.ui.plaintext import counted, plain_label

# --- Version ------------------------------------------------------------
__version__ = "sshimportdialog.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 7: the reason Add is greyed says "added", the word a person uses, and promises no version (FJ-13)
# v0.0.47: Review round 5: the reason no server can come over reads right for one server too (FJ-13, OR-23)
# v0.0.47: Review round 4: the columns are Name and Server, the words a person uses for them (FJ-08)
# v0.0.47: Review round 1: where no server can come over, Add says that instead of asking for a tick that can't be given (FJ-13);
#          the names follow the program's own style (FJ-17)
# v0.0.47: Initial: the servers of this computer's own SSH settings, with their ticks and their refusals (FJ-08, FJ-09)

WINDOW_TITLE: str = "The SSH Settings on This Computer"
ASK_TEXT: str = "These servers stand in your SSH settings. Tick the ones to add to your connection list.\nYour settings themselves are only read, never changed."
ADD_TEXT: str = "Add"
NOTHING_TICKED_TEXT: str = "Tick at least one server first."
NONE_CAN_TEXTS: tuple[str, str] = (
    "This server can't be added. Under it stands what is in the way.",
    "None of these servers can be added. Under each one stands what is in the way.",
)
TAKEN_TEXT: str = "Your list already holds a connection of this name, so this one is added under a name of its own."
WINDOW_WIDTH: int = 620
WINDOW_HEIGHT: int = 420
COLUMNS: tuple[str, ...] = ("Name", "Server", "User", "Key file")  # the name your settings call it by, and the server it reaches
SERVER_WORDS: tuple[str, str] = ("server", "servers")
FOUND_TEXT: str = "{count} found."
PORT_TEXT: str = "{host} port {port}"


class SshImportDialog(QDialog):
    """Every server found, ticked where it can come over whole; one that can't is unticked and says why, in its own line under it, and
    ticking it is not possible: nothing is ever taken over half (FJ-09)."""

    def __init__(self, found: Sequence[FoundServer], taken: set[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.found: tuple[FoundServer, ...] = tuple(found)
        self.list = QTreeWidget()
        self.list.setColumnCount(len(COLUMNS))
        self.list.setHeaderLabels(list(COLUMNS))
        self.list.setRootIsDecorated(False)
        self.items: list[QTreeWidgetItem] = []
        for server in self.found:
            connection = server.connection
            where = PORT_TEXT.format(host=connection.host, port=connection.port)
            item = QTreeWidgetItem([connection.name, where, connection.user, connection.key_file])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if server.can_come_over:
                item.setCheckState(0, Qt.CheckState.Checked)
                if connection.name in taken:
                    item.setToolTip(0, TAKEN_TEXT)  # it comes over under a name of its own; the list never loses what it has
            else:
                item.setCheckState(0, Qt.CheckState.Unchecked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addTopLevelItem(item)
            self.items.append(item)
            for refusal in server.refusals:  # in plain words, under the server it is about (FJ-09)
                line = QTreeWidgetItem([refusal])
                line.setFirstColumnSpanned(True)
                line.setFlags(Qt.ItemFlag.NoItemFlags)
                item.addChild(line)
            item.setExpanded(True)
        for column in range(len(COLUMNS)):
            self.list.resizeColumnToContents(column)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        chosen = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.add_button: QPushButton = chosen if chosen is not None else QPushButton(ADD_TEXT, self)
        self.add_button.setText(ADD_TEXT)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(ASK_TEXT))
        layout.addWidget(plain_label(FOUND_TEXT.format(count=counted(len(self.found), *SERVER_WORDS))))
        layout.addWidget(self.list)
        layout.addWidget(self.buttons)
        self.list.itemChanged.connect(self._ticks_changed)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._ticks_changed()

    def _ticks_changed(self, *_ignored: object) -> None:
        """Add is greyed with its reason while nothing is ticked, and with the true reason where no server can be ticked at all
        (OR-20, FJ-13)."""
        ticked = bool(self.chosen())
        self.add_button.setEnabled(ticked)
        if ticked:
            self.add_button.setToolTip("")
        else:
            none_can = NONE_CAN_TEXTS[0] if len(self.found) == 1 else NONE_CAN_TEXTS[1]
            self.add_button.setToolTip(NOTHING_TICKED_TEXT if any(server.can_come_over for server in self.found) else none_can)

    def chosen(self) -> list[Connection]:
        """The servers that are ticked now, in the order the window shows them."""
        return [server.connection for server, item in zip(self.found, self.items) if item.checkState(0) == Qt.CheckState.Checked]
