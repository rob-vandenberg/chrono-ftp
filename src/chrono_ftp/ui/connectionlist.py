
from collections.abc import Callable, Sequence
from pathlib import Path

from dataclasses import dataclass, replace

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from chrono_ftp.config import bookmarks as saved_places
from chrono_ftp.config import connections as saved
from chrono_ftp.config import sessionimport
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config import workspaces as saved_workspaces
from chrono_ftp.core import colours, credentials, sshsettings
from chrono_ftp.core.addresses import Address, address_text, free_name, read_address
from chrono_ftp.core.connection import DEFAULT_PORTS, FOLDER_SEPARATOR, FTP, HIGHEST_PORT, LONGEST_NOTE, LOWEST_PORT, SFTP, Connection, folder_of, moved_into, own_name, tidy_name
from chrono_ftp.core.errors import CredentialError
from chrono_ftp.engine.browsing import local_start_folder
from chrono_ftp.ui.addressdialog import OpenAddressDialog, show_address
from chrono_ftp.ui.connectiontree import ConnectionTree
from chrono_ftp.ui.menus import as_drop_down, open_menu
from chrono_ftp.ui.passwordeye import PasswordEye
from chrono_ftp.ui.plaintext import box_text, counted, plain_label, told, was
from chrono_ftp.ui.sshimportdialog import SshImportDialog

# --- Version ------------------------------------------------------------
__version__ = "connectionlist.py 0.0.49"

# --- Version history ----------------------------------------------------
# v0.0.49: The colour blocks drawn again after a change of look (FQ-04)
# v0.0.47: Block FJ: the list is a tree with folders, a connection dragged into a folder or out of one keeps its password, bookmarks and
#          location profiles, and the open folders are kept for next time (FJ-01 to FJ-03); Open Every Connection in This Folder
#          (FJ-04); a note of its own (FJ-05); the settings a new connection starts with (FJ-06); typing jumps to a connection, and
#          what was typed is shown (FJ-07); Import offers the SSH settings of this computer (FJ-08 to FJ-10); Open an Address… and
#          Copy the Address (FJ-11, FJ-12)
# v0.0.47: Review round 9: the greyed Export button says what is true today, and promises no later version (OR-23)
# v0.0.47: Review round 8: deleting a connection leaves nothing of its name behind, and a list that can't be written lets nothing be
#          deleted or copied and says so, in place of a crash (FJ-06, OR-15)
# v0.0.47: Review round 7: a new connection says it was not saved, the cause of a failure stands beside what is left behind, the
#          password store says which call failed, and the leftover copy is said after what happened, never before it (FJ-02)
# v0.0.47: Review round 5: every message about a move or a rename that did not go through says what is true and follows what the
#          person did, a drag or a typed name; a store that can't be put back says what stands on the disk; the sentences about the
#          password store, the bookmarks and a copy left behind say what the person can do (FJ-02)
# v0.0.47: Review round 4: everything that follows a new name is written before the connection list, and put back where one of them
#          can't be written, so a failure leaves nothing renamed; a password the person typed that can't be kept saves nothing; every
#          sentence about a move that did not happen names the file and says what to do (FJ-02)
# v0.0.47: Review round 3: a file that can't be read, one without a connection and one whose connections the list already holds each
#          say their own thing; an import that can't be written adds nothing and says so; a move asks every store whether it can be
#          written before anything is renamed (FJ-02, FJ-08, FJ-14)
# v0.0.47: Review round 2: the list is saved before the old password is removed, and a list that can't be saved moves nothing and
#          leaves the password where the connection is (FJ-02)
# v0.0.47: Review round 1: a move that can't take the password along moves nothing and says so, and a name already taken in that folder
#          says that in its own words (FJ-02); a name with an empty part is refused and the spaces around each part are trimmed
#          (FJ-01); the settings of this computer say apart whether there is no file, no server in it or a file that could not be read
#          (FJ-14); the names follow the program's own style (FJ-17)
# v0.0.45: The name its tab was given is no field of this window: it stays as it is when a connection is saved here, and a tab renamed
#          meanwhile reaches the list (FI-01)
# v0.0.38: The list can be read again, after Clean Up Stored Data (FP-04)
# v0.0.34: A local starting folder next to the remote one, and "Remember the last folders" (FH-06); a renamed connection keeps its
#          bookmarks and location profiles; a saved connection is told to the window, so its open tabs follow; a deleted one too,
#          so its own bookmarks go; a local starting folder is kept absolute, a relative one read from the home folder
# v0.0.32: Connect is the default button: Enter in the list or a field connects the selected connection (D-050)
# v0.0.31: start_on: the window starts on the connection that was last live, in view; the top one when it is gone (D-053)
# v0.0.29: The right-click menu opens through open_menu, which lets go of the keyboard (D-052)
# v0.0.27: select_connection, for Edit in the right-click menu of a tab
# v0.0.25: Connect with unsaved changes asks "Save changes?" first: Save, Don't Save or Cancel
# v0.0.21: An eye in the password field while it holds typed text
# v0.0.20: The labels of the fields keep the style's margin from the splitter again
# v0.0.19: Import… and Export… next to each other on the left
# v0.0.18: The buttons under the list and under the fields stand on one line, as the two sides have the same top and bottom
# v0.0.17: Laid out as the reference program: Import… and Export… under the list; Connect, Duplicate and Delete in the right-click
#          menu of a connection; New, Save and Connect under the fields; Duplicate copies a connection with its password
# v0.0.16: A splitter between the list and the fields of the selected connection
# v0.0.12: Names in message boxes are shown as written, never read as HTML
# v0.0.9: A tick switches the colour of a connection on; off for a new one
# v0.0.8: A user name kept after a login shows here
# v0.0.5: Initial: the saved connections, each with its colour; the fields of the selected one (D-017)

WINDOW_TITLE: str = "Connections"
COLOUR_BLOCK_SIZE: int = 16
LIST_WIDTH: int = 240
WINDOW_WIDTH: int = 720
WINDOW_HEIGHT: int = 460
SET_ASIDE_TEXT: str = "Part of your connection list could not be read. A copy is kept here, so nothing is lost:"
SAME_NAME_TEXT: str = "There is already a connection with this name. Please choose another name."
DELETE_TEXT: str = "Delete the connection {name}?"
IMPORT_TEXT: str = "Import…"
EXPORT_TEXT: str = "Export…"
EXPORT_NOT_TEXT: str = "Chrono FTP does not write out a connection list."
COPY_NAME: str = "{name} (copy)"
SAVE_CHANGES_TEXT: str = "Save changes to {name}?"
NEXT_COPY_NAME: str = "{name} (copy {number})"
REMEMBER_TEXT: str = "Remember the last folders"
REMEMBER_TIP: str = "On: when its tab or Chrono FTP closes, the folders open on both sides become the starting folders of this connection."
LOCAL_START_TIP: str = "The folder on this computer that the left side opens with this connection. Empty: the left side stays where it is."
REMOTE_START_TIP: str = "The folder on the server that opens first. Empty: the home folder of the login."
PROTOCOL_LABELS: dict[str, str] = {SFTP: "SFTP", FTP: "FTP"}
NOTE_LABEL: str = "Note"
NOTE_TIP: str = "Anything you want to remember about this connection. It shows when you rest the mouse on it in the list."
NAME_TIP: str = "A name with a slash in it puts the connection in a folder of the list: Work/Mail server stands in the folder Work."
FOLDER_OPEN_TEXT: str = "Open Every Connection in This Folder"
FOLDER_OPEN_TIP: str = "Opens a tab for {count} in {folder}, in the order they stand here."
FOLDER_EMPTY_TIP: str = "There is no connection in this folder."
COPY_ADDRESS_TEXT: str = "Copy the Address"
USE_FOR_NEW_TEXT: str = "Use This Connection's Settings for a New One"
EMPTY_FOR_NEW_TEXT: str = "Use Empty Settings for a New One"
EMPTY_FOR_NEW_TIP: str = "A new connection already starts with empty settings."
USE_FOR_NEW_TOLD: str = "New now starts with the settings of {name}, except its name."
EMPTY_FOR_NEW_TOLD: str = "New starts with empty settings again."
OPEN_ADDRESS_TEXT: str = "Open an Address…"
OPEN_ADDRESS_TIP: str = "Opens an address you paste, such as sftp://user@server.example.com/var/log, as a tab. It is not saved in this list."
IMPORT_SSH_TEXT: str = "Read the SSH Settings on This Computer…"
NO_SSH_FILE_TEXT: str = "This computer has no SSH settings ({path}), so there is nothing to take over."
NO_SSH_SERVER_TEXT: str = "Your SSH settings name no server, so there is nothing to take over."
SSH_UNREADABLE_TEXT: str = "Your SSH settings could not be read ({path}), so nothing was taken over. Check that you may read that file."
ADDED_TEXT: str = "{count} {was} added to your list."
FILE_NOT_READ_TEXT: str = "That file could not be read ({reason}), so nothing was taken over. Check that you picked the file you meant and that you may read it."
NONE_IN_FILE_TEXT: str = "That file holds no connection, so there was nothing to take over."
ALL_THERE_TEXT: str = "Your list already holds every connection of that file, so nothing was added."
TYPING_TEXT: str = "Find: {typed}"
TYPING_NEXT_TEXT: str = "Find: {typed}   (Tab: the next one)"
CONNECTION_WORDS: tuple[str, str] = ("connection", "connections")
MOVE_TAKEN_TEXT: str = "The folder {folder} already holds a connection called {name}, so nothing was moved."
TOP_TAKEN_TEXT: str = "Outside every folder there is already a connection called {name}, so nothing was moved."
# What a person reads when saving or moving a connection went wrong. Three sentences, each built from the facts there are: what the
# connection is called now, what could not be saved, and what is left where. No pairs of near-identical texts, and nothing in them that
# can come out untrue when a name is empty or the same on both sides (the owner, review round 6).
NOTHING_CHANGED: str = "Nothing changed: {name} is as it was. {why}"
NEW_NOT_SAVED: str = "{name} was not saved. {why}"  # a connection that is new: there is nothing it could be "as it was"
LEFT_BEHIND: str = "{name} is as it was, but the name {other} now holds {what}. {why}"  # one verb, right for one part and for three
PASSWORD_LEFT_AFTER_DELETE: str = "{name} is deleted. Its password is still in your password store, which did not answer; nothing uses it."
COPY_LEFT: str = "{name} kept its password. A copy of it is left under the name {other} in your password store, where nothing uses it."
NOT_SAVED_WHY: str = "{path} could not be saved: {reason}."  # the one fact behind most of it: a file and what the system said
STORE_UNREAD_WHY: str = "Your password store could not be read."  # the cause there is: the call that failed, never a guess at why
STORE_UNWRITTEN_WHY: str = "Your password store could not be written."
LIST_ITSELF: str = "your connection list"  # what is as it was when an import could not be written: no one connection is involved
PLACES_UNREADABLE_WHY: str = "Your bookmarks could not be read in full, and they would have to follow the new name."
AND: str = " and "  # between the last two of the parts that are left behind
COMMA: str = ", "
BOOKMARKS_LEFT: str = "its bookmarks"  # the parts, as they read in a list of one, two or three
SETTINGS_LEFT: str = "its place in the row of tabs"
WORKSPACES_LEFT: str = "its workspaces"


@dataclass(frozen=True)
class StoreProblem:
    """What went wrong while the things kept by a connection's name followed it: why, as a whole sentence of facts, and the parts that
    are left under the other name because putting them back failed too."""

    why: str = ""
    left: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoresFollowed:
    """The answer of the window: whether everything that is kept by the name followed it, and the way to put it all back exactly as it
    stood, for when the connection list itself can't be saved afterwards."""

    problem: StoreProblem = StoreProblem()
    put_back: Callable[[], StoreProblem] = lambda: StoreProblem()


def and_joined(parts: Sequence[str]) -> str:
    """The parts of a sentence as a person writes them: "a", "a and b", "a, b and c"."""
    if len(parts) < 2:
        return parts[0] if parts else ""
    return COMMA.join(parts[:-1]) + AND + parts[-1]


def both_said(first: str, second: str) -> str:
    """Two short sentences of facts after one another, the first cause first; one alone where there is only one. A person who reads
    that something is left behind must also read why the whole thing stopped (the owner, review round 7)."""
    return f"{first} {second}".strip() if first != second else first
EMPTY_PART_TEXT: str = "A name can't have an empty part. Write it as Work/Mail server, with something in every part."


def colour_block(colour: str) -> QIcon:
    pixmap = QPixmap(COLOUR_BLOCK_SIZE, COLOUR_BLOCK_SIZE)
    pixmap.fill(QColor(colour))
    return QIcon(pixmap)


class ConnectionList(QDialog):
    connect_requested = Signal(object)  # a Connection
    deleted = Signal(str)  # a connection was deleted: its name
    saved = Signal(str, object)  # a connection was saved over an existing one: its name before, and the Connection as saved
    folder_connect_requested = Signal(object)  # Open Every Connection in This Folder: the Connections, in the order of the folder (FJ-04)
    address_requested = Signal(object, str)  # an address the person pasted: the Connection and the password that came with it (FJ-11)
    folders_kept = Signal(object)  # the folders that stand open now, for next time: a tuple of their names (FJ-03)
    new_from_kept = Signal(str)  # the connection a new one starts with; "" for empty settings (FJ-06)

    def __init__(self, parent: QWidget | None = None, open_folders: tuple[str, ...] = (), new_from: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.colour: str = colours.FIRST_CONNECTION
        self.stored_password: bool = False
        self.connections: list[Connection] = []
        self.open_folders: tuple[str, ...] = open_folders  # the folders that stood open last time (FJ-03)
        self.rename_stores: Callable[[str, str], StoresFollowed] | None = None  # set by the window: what else follows a new name (FJ-02)
        self.new_from: str = new_from  # the connection a new one starts with (FJ-06)
        self.tree = ConnectionTree()
        self.typing = plain_label()  # what was typed to find a connection, so the person sees why the selection moved (FJ-07)
        self.name = QLineEdit()
        self.name.setToolTip(NAME_TIP)
        self.note = QPlainTextEdit()
        self.note.setToolTip(NOTE_TIP)
        self.note.setTabChangesFocus(True)  # Tab leaves the box, as it does in every other field here
        self.protocol = QComboBox()
        for protocol, label in PROTOCOL_LABELS.items():
            self.protocol.addItem(label, protocol)
        self.host = QLineEdit()
        self.port = QSpinBox()
        self.port.setRange(LOWEST_PORT, HIGHEST_PORT)
        self.port.setValue(DEFAULT_PORTS[SFTP])
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_eye = PasswordEye(self.password)
        self.key_file = QLineEdit()
        self.browse_button = QPushButton("Browse")
        self.start_folder = QLineEdit()
        self.start_folder.setToolTip(REMOTE_START_TIP)
        self.local_start_folder = QLineEdit()
        self.local_start_folder.setToolTip(LOCAL_START_TIP)
        self.local_browse_button = QPushButton("Browse")
        self.remember_folders = QCheckBox(REMEMBER_TEXT)
        self.remember_folders.setToolTip(REMEMBER_TIP)
        self.colour_button = QPushButton("Choose colour")
        self.use_colour = QCheckBox("Use a colour")
        self.new_button = QPushButton("New")
        self.save_button = QPushButton("Save")
        self.connect_button = QPushButton("Connect")
        self.import_button = QPushButton(IMPORT_TEXT)
        self.import_menu = QMenu(self.import_button)  # two ways in: a session list, or the SSH settings of this computer (FJ-08)
        self.session_import_action = QAction(sessionimport.IMPORT_BUTTON_TEXT, self)
        self.ssh_import_action = QAction(IMPORT_SSH_TEXT, self)
        for action in (self.session_import_action, self.ssh_import_action):
            self.import_menu.addAction(action)
        as_drop_down(self.import_menu)
        self.import_button.setMenu(self.import_menu)
        self.export_button = QPushButton(EXPORT_TEXT)
        self.export_button.setEnabled(False)  # greyed, never hidden (OR-20)
        self.address_button = QPushButton(OPEN_ADDRESS_TEXT)
        self.address_button.setToolTip(OPEN_ADDRESS_TIP)
        for button in (self.browse_button, self.local_browse_button, self.colour_button, self.new_button, self.save_button, self.import_button, self.export_button, self.address_button):
            button.setAutoDefault(False)  # Enter never goes to another button, also not to the one with the focus
        self.connect_button.setDefault(True)  # Enter connects the selected connection (D-050)
        self.export_button.setToolTip(EXPORT_NOT_TEXT)
        self.connect_action = QAction("Connect", self)
        self.duplicate_action = QAction("Duplicate", self)
        self.delete_action = QAction("Delete", self)
        self.copy_address_action = QAction(COPY_ADDRESS_TEXT, self)
        self.use_for_new_action = QAction(USE_FOR_NEW_TEXT, self)
        self.empty_for_new_action = QAction(EMPTY_FOR_NEW_TEXT, self)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_file)
        key_row.addWidget(self.browse_button)
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Protocol", self.protocol)
        form.addRow("Host", self.host)
        form.addRow("Port", self.port)
        form.addRow("User", self.user)
        form.addRow("Password", self.password)
        form.addRow("Key file", key_row)
        form.addRow("Remote starting folder", self.start_folder)
        local_row = QHBoxLayout()
        local_row.addWidget(self.local_start_folder)
        local_row.addWidget(self.local_browse_button)
        form.addRow("Local starting folder", local_row)
        form.addRow("", self.remember_folders)
        form.addRow(NOTE_LABEL, self.note)
        colour_row = QHBoxLayout()
        colour_row.addWidget(self.use_colour)
        colour_row.addWidget(self.colour_button)
        colour_row.addStretch(1)
        form.addRow("Colour", colour_row)
        buttons = QHBoxLayout()
        for button in (self.address_button, self.new_button, self.save_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.connect_button)
        right = QVBoxLayout()
        margin = self.style().pixelMetric(QStyle.PixelMetric.PM_LayoutLeftMargin)
        right.setContentsMargins(margin, 0, 0, 0)  # room from the splitter; no top or bottom, as the list side: both start and end at one height
        right.addLayout(form)
        right.addStretch(1)
        right.addLayout(buttons)
        fields = QWidget()
        fields.setLayout(right)
        self.parts = QSplitter(Qt.Orientation.Horizontal)
        self.parts.setChildrenCollapsible(False)
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(self.import_button)
        list_buttons.addWidget(self.export_button)
        list_buttons.addStretch(1)
        list_side = QVBoxLayout()
        list_side.setContentsMargins(0, 0, 0, 0)
        list_side.addWidget(self.tree)
        list_side.addWidget(self.typing)
        list_side.addLayout(list_buttons)
        list_part = QWidget()
        list_part.setLayout(list_side)
        self.parts.addWidget(list_part)
        self.parts.addWidget(fields)
        self.parts.setStretchFactor(1, 1)  # a wider window widens the fields, the list keeps its width
        self.parts.setSizes([LIST_WIDTH, WINDOW_WIDTH - LIST_WIDTH])
        layout = QHBoxLayout(self)
        layout.addWidget(self.parts)
        self.tree.currentItemChanged.connect(self._row_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_menu)
        self.tree.moved.connect(self.move_into_folder)
        self.tree.folders_changed.connect(self._folders_changed)
        self.tree.typing_changed.connect(self._typing_changed)
        self.connect_action.triggered.connect(self._connect)
        self.duplicate_action.triggered.connect(self.duplicate)
        self.delete_action.triggered.connect(self.delete)
        self.copy_address_action.triggered.connect(self.copy_the_address)
        self.use_for_new_action.triggered.connect(self.use_for_a_new_one)
        self.empty_for_new_action.triggered.connect(self.use_empty_for_a_new_one)
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self.name.textChanged.connect(self._update_buttons)
        self.host.textChanged.connect(self._update_buttons)
        self.browse_button.clicked.connect(self._browse)
        self.local_browse_button.clicked.connect(self._browse_local)
        self.colour_button.clicked.connect(self._choose_colour)
        self.use_colour.toggled.connect(self.colour_button.setEnabled)
        self.new_button.clicked.connect(self.clear_fields)
        self.save_button.clicked.connect(self.save)
        self.connect_button.clicked.connect(self._connect)
        self.session_import_action.triggered.connect(self.import_sessions)
        self.ssh_import_action.triggered.connect(self.import_ssh_settings)
        self.address_button.clicked.connect(self.open_an_address)
        self._load()
        self.clear_fields()

    def _load(self) -> None:
        loaded = saved.load()
        self.connections = list(loaded.connections)
        self._fill_list()
        if loaded.set_aside is not None:
            QMessageBox.information(self, WINDOW_TITLE, box_text(f"{SET_ASIDE_TEXT}\n{loaded.set_aside}"))

    def reload(self) -> None:
        """The list read again, as after Clean Up Stored Data removed it (FP-04)."""
        self._load()
        self.clear_fields()

    def user_accepted(self, name: str, user: str) -> None:
        """A login kept this user name; the fields follow, so Save can't put back the old one."""
        self.connections = [replace(connection, user=user) if connection.name == name else connection for connection in self.connections]
        if self.name.text().strip() == name:
            self.user.setText(user)

    def _fill_list(self) -> None:
        """The tree afresh: every connection in the folder its name names, the folders that stood open open again (FJ-01, FJ-03)."""
        self.tree.fill(self.connections, self.open_folders, colour_block)

    def draw_the_colour_blocks(self) -> None:
        """The blocks beside the connections drawn again after a change of look, without rebuilding the tree (FQ-04)."""
        colours_by_name = {connection.name: connection.colour for connection in self.connections}
        self.tree.draw_the_colour_blocks(lambda name: colours_by_name.get(name, ""), colour_block)

    def _folders_changed(self) -> None:
        """A folder was opened or closed: which ones stand open is kept for next time (FJ-03)."""
        self.open_folders = self.tree.open_folders()
        self.folders_kept.emit(self.open_folders)

    def _typing_changed(self, typed: str) -> None:
        """What was typed to find a connection, under the tree, so the person sees why the selection moved (FJ-07)."""
        if not typed:
            self.typing.setText("")
            return
        self.typing.setText((TYPING_NEXT_TEXT if self.tree.has_next_match() else TYPING_TEXT).format(typed=typed))

    def selected(self) -> Connection | None:
        name = self.tree.selected_name()
        return next((connection for connection in self.connections if connection.name == name), None) if name else None

    def _row_changed(self) -> None:
        connection = self.selected()
        if connection is None:
            self._update_buttons()  # a folder is selected: Connect and the rest grey out, never a button that can't act (OR-20)
            return
        self.name.setText(connection.name)
        self.note.setPlainText(connection.note)
        self.protocol.setCurrentIndex(self.protocol.findData(connection.protocol))
        self.host.setText(connection.host)
        self.port.setValue(connection.port)
        self.user.setText(connection.user)
        self.key_file.setText(connection.key_file)
        self.start_folder.setText(connection.start_folder)
        self.local_start_folder.setText(connection.local_start_folder)
        self.remember_folders.setChecked(connection.remember_folders)
        self.password.clear()
        self.password.setPlaceholderText("")
        try:
            if credentials.store.read(connection.name):
                self.password.setPlaceholderText("Stored")
        except CredentialError:
            pass  # the field stays empty; nothing is lost
        self._set_colour(connection.colour)
        self._update_buttons()

    def _protocol_changed(self) -> None:
        protocol = str(self.protocol.currentData())
        if self.port.value() in DEFAULT_PORTS.values():
            self.port.setValue(DEFAULT_PORTS[protocol])

    def _set_colour(self, colour: str) -> None:
        """Empty: no colour. The button then shows the first colour, so ticking the box offers one straight away."""
        self.use_colour.setChecked(bool(colour))
        self.colour_button.setEnabled(bool(colour))
        self.colour = colour or colours.FIRST_CONNECTION
        self.colour_button.setIcon(colour_block(self.colour))

    def _update_buttons(self) -> None:
        self.save_button.setEnabled(bool(self.name.text().strip() and self.host.text().strip()))
        chosen = self.selected() is not None
        for action in (self.connect_action, self.duplicate_action, self.delete_action, self.copy_address_action, self.use_for_new_action):
            action.setEnabled(chosen)
        self.empty_for_new_action.setEnabled(bool(self.new_from))
        self.empty_for_new_action.setToolTip("" if self.new_from else EMPTY_FOR_NEW_TIP)  # greyed with its reason, never hidden (OR-20)
        self.connect_button.setEnabled(chosen)

    def folder_menu_for(self, folder: str) -> QMenu:
        """The right-click menu of a folder: a tab for every connection in it, greyed with its reason where it holds none (FJ-04, OR-20)."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        names = self.tree.names_in(folder)
        action = QAction(FOLDER_OPEN_TEXT, menu)
        action.setEnabled(bool(names))
        action.setToolTip(FOLDER_OPEN_TIP.format(count=counted(len(names), *CONNECTION_WORDS), folder=own_name(folder)) if names else FOLDER_EMPTY_TIP)
        action.triggered.connect(lambda: self.open_the_folder(folder))
        menu.addAction(action)
        return menu

    def menu_for(self, place: QPoint) -> QMenu | None:
        """The right-click menu of the connection or folder at this place in the tree; nothing outside both."""
        item = self.tree.itemAt(place)
        if item is None:
            return None
        self.tree.setCurrentItem(item)
        folder = self.tree.folder_at(place)
        if folder is not None:
            return self.folder_menu_for(folder)
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        menu.addAction(self.connect_action)
        menu.addSeparator()
        menu.addAction(self.duplicate_action)
        menu.addAction(self.delete_action)
        menu.addSeparator()
        menu.addAction(self.copy_address_action)
        menu.addAction(self.use_for_new_action)
        menu.addAction(self.empty_for_new_action)
        return menu

    def _show_menu(self, place: QPoint) -> None:
        menu = self.menu_for(place)
        if menu is not None:
            open_menu(menu, self.tree.viewport().mapToGlobal(place))
            menu.deleteLater()

    def _double_clicked(self, *_ignored: object) -> None:
        """A double-click connects a connection; on a folder it opens or closes it, which the tree does itself."""
        if self.selected() is not None:
            self._connect()

    def clear_fields(self) -> None:
        """New: empty fields, or the settings of the connection the person chose for a new one, its name left empty (FJ-06)."""
        self.tree.clear_choice()
        self.tree.stop_typing()
        start = next((connection for connection in self.connections if connection.name == self.new_from), None)
        for field in (self.name, self.host, self.user, self.password, self.key_file, self.start_folder, self.local_start_folder):
            field.clear()
        self.note.clear()
        self.remember_folders.setChecked(False)  # off for a new connection
        self.password.setPlaceholderText("")
        self.protocol.setCurrentIndex(self.protocol.findData(SFTP))
        self.port.setValue(DEFAULT_PORTS[SFTP])
        self._set_colour("")
        if start is not None:
            self.protocol.setCurrentIndex(self.protocol.findData(start.protocol))
            self.host.setText(start.host)
            self.port.setValue(start.port)
            self.user.setText(start.user)
            self.key_file.setText(start.key_file)
            self.start_folder.setText(start.start_folder)
            self.local_start_folder.setText(start.local_start_folder)
            self.remember_folders.setChecked(start.remember_folders)
            self.note.setPlainText(start.note)
            self._set_colour(start.colour)
        self._update_buttons()

    def _browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Key file")
        if path:
            self.key_file.setText(path)

    def _browse_local(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Local starting folder", self.local_start_folder.text().strip())
        if path:
            self.local_start_folder.setText(path)

    def _choose_colour(self) -> None:
        colour = QColorDialog.getColor(QColor(self.colour), self, "Colour of this connection")
        if colour.isValid():
            self._set_colour(colour.name())

    def from_fields(self) -> Connection:
        edited = self.selected()
        return Connection(
            tab_name=edited.tab_name if edited is not None else "",  # not a field of this window: the name its tab was given stays (FI-01)
            name=tidy_name(self.name.text()),  # the spaces around each part are trimmed, so no folder of the list is called " Work "
            host=self.host.text().strip(),
            user=self.user.text().strip(),
            port=self.port.value(),
            protocol=str(self.protocol.currentData()),
            key_file=self.key_file.text().strip(),
            colour=self.colour if self.use_colour.isChecked() else "",
            start_folder=self.start_folder.text().strip(),
            local_start_folder=local_start_folder(self.local_start_folder.text()),  # kept absolute: a relative one starts at the home folder
            remember_folders=self.remember_folders.isChecked(),
            note=self.note.toPlainText()[:LONGEST_NOTE],  # a note longer than a person writes is kept to its length (FJ-05)
        )

    def _keep(self, edited: Connection | None, connection: Connection, password: str = "") -> bool:
        """The one place a connection is written: a new name takes its password along first, then everything that is kept by that name
        follows it, and the connection list itself is written last of all (FJ-02). False when nothing was written: the connection then
        stays exactly as it was, password and all, and the person is told in one sentence what is true."""
        before = edited.name if edited is not None and edited.name != connection.name else ""  # the name it had, when it gets a new one
        if before:
            why = self._stores_why()  # everything that has to follow the new name is asked first: whole or not at all
            if why:
                told(self, WINDOW_TITLE, NOTHING_CHANGED.format(name=before, why=why))
                return False
            if not self._password_copied(before, connection.name):
                return False
        try:
            if password:
                credentials.store.write(connection.name, password)
        except CredentialError:
            name = before or connection.name  # a password the person typed that is not kept would be asked for again: nothing is saved
            told(self, WINDOW_TITLE, self._said_about_one(edited, name, StoreProblem(STORE_UNWRITTEN_WHY)))
            self._copy_dropped(before, connection.name)  # what happened first, the copy left behind after it
            return False
        followed = self.rename_stores(before, connection.name) if before and self.rename_stores is not None else StoresFollowed()
        if followed.problem.why:
            told(self, WINDOW_TITLE, self.said_about(before, connection.name, followed.problem))
            self._copy_dropped(before, connection.name)  # what happened first, the copy left behind after it
            return False
        kept = [other for other in self.connections if other is not edited] + [connection]
        try:
            saved.save(kept)  # the list last of all: a failure before this leaves the connection under the name it had
        except OSError as error:
            why = NOT_SAVED_WHY.format(path=saved.list_path(), reason=error.strerror or str(error))
            if not before:
                told(self, WINDOW_TITLE, self._said_about_one(edited, connection.name, StoreProblem(why)))
                return False
            problem = followed.put_back()  # exactly what stood there before, never a rename the other way
            whole = replace(problem, why=both_said(why, problem.why))  # why it stopped, and then what could not be put back
            told(self, WINDOW_TITLE, self.said_about(before, connection.name, whole))
            self._copy_dropped(before, connection.name)
            return False
        if before:
            self._copy_dropped(connection.name, before)
        self.connections = kept
        if before and self.new_from == before:
            self.new_from = connection.name  # New goes on starting with this one, under its new name (FJ-06)
            self.new_from_kept.emit(self.new_from)
        if edited is not None:
            self.saved.emit(edited.name, connection)  # its open tabs, bookmarks and location profiles follow
        self._fill_list()
        self._select(connection.name)
        return True

    def said_about(self, name: str, other: str, problem: StoreProblem) -> str:
        """The one sentence a person reads about a save or a move that did not go through, built from the facts: what the connection is
        called now, what is left under the other name, and what could not be saved (the owner, review round 6)."""
        if problem.left:
            return LEFT_BEHIND.format(name=name, what=and_joined(problem.left), other=other, why=problem.why)
        return NOTHING_CHANGED.format(name=name, why=problem.why)

    def _said_about_one(self, edited: Connection | None, name: str, problem: StoreProblem) -> str:
        """The same for a connection that is not being renamed: one that was there is as it was, and a new one was not saved at all,
        because there is nothing it could be as it was (the owner, review round 7)."""
        stem = NOTHING_CHANGED if edited is not None else NEW_NOT_SAVED
        return stem.format(name=name, why=problem.why)

    def _stores_why(self) -> str:
        """Empty when every file that has to follow a connection's new name can be written: the connection list, the settings, the
        workspaces and the bookmarks. Else what is wrong, as one short sentence of facts (FJ-02, G-2)."""
        for name in (saved.FILE_NAME, saved_settings.FILE_NAME, saved_workspaces.FILE_NAME, saved_places.FILE_NAME):
            path = saved.config_folder() / name
            reason = saved.can_write(path)
            if reason:
                return NOT_SAVED_WHY.format(path=path, reason=reason)
        return "" if saved_places.load().writable else PLACES_UNREADABLE_WHY

    def _password_copied(self, old: str, new: str) -> bool:
        """The stored password of the old name copied to the new one, before anything else is written. False where it could not be: then
        nothing is written at all, because a connection without its password is not the connection the person had (OR-24)."""
        why = STORE_UNREAD_WHY  # reading and writing are asked apart, so the sentence says the cause there is
        try:
            stored = credentials.store.read(old)
            why = STORE_UNWRITTEN_WHY
            if stored:
                credentials.store.write(new, stored)
        except CredentialError:
            told(self, WINDOW_TITLE, NOTHING_CHANGED.format(name=old, why=why))
            return False
        return True

    def _copy_dropped(self, kept_name: str, gone_name: str) -> None:
        """The copy of the password under the name that is not in use: after a save that went through, the copy under the old name;
        after one that did not, the copy made for the new name. The facts are the same either way, so a copy that can't be removed is
        said in the same sentence, with both names in full, so the person can find it in their password store (FJ-02, OR-24)."""
        if not kept_name or not gone_name or gone_name == kept_name:
            return  # nothing to say where there is no second name: a sentence with an empty name in it would be untrue (OR-23)
        try:
            credentials.store.remove(gone_name)
        except CredentialError:
            told(self, WINDOW_TITLE, COPY_LEFT.format(name=kept_name, other=gone_name))

    def save(self) -> None:
        edited = self.selected()
        connection = self.from_fields()
        if any(not part for part in connection.name.split(FOLDER_SEPARATOR)):
            told(self, WINDOW_TITLE, EMPTY_PART_TEXT)  # "Work/", "A//B" and " / " leave a blank row or a blank folder in the list (FJ-01)
            return
        if any(other.name == connection.name and other is not edited for other in self.connections):
            told(self, WINDOW_TITLE, SAME_NAME_TEXT)
            return
        self._keep(edited, connection, self.password.text())

    def move_into_folder(self, name: str, folder: str) -> None:
        """A connection dragged onto a folder, or out of every folder: that is a new name, and the rename takes its password, bookmarks
        and location profiles along (FJ-02). A name already taken in that folder is told and nothing moves."""
        connection = next((other for other in self.connections if other.name == name), None)
        if connection is None:
            return
        wanted = moved_into(name, folder)
        if wanted == name:
            return
        if any(other.name == wanted for other in self.connections):
            said = MOVE_TAKEN_TEXT.format(folder=own_name(folder), name=own_name(wanted)) if folder else TOP_TAKEN_TEXT.format(name=own_name(wanted))
            told(self, WINDOW_TITLE, said)  # the person chose no name here, so Save's words about choosing another would make no sense
            return
        if not self._keep(connection, replace(connection, name=wanted)):
            return
        if folder and folder not in self.open_folders:
            self.open_folders = (*self.open_folders, folder)  # it lands in sight: the folder it went into stands open (FJ-03)
            self.folders_kept.emit(self.open_folders)
            self._fill_list()
            self._select(wanted)

    def tab_renamed(self, name: str, tab_name: str) -> None:
        """The person gave the tab of this connection a name: the list follows, so Save here does not put the old one back (FI-01)."""
        self.connections = [replace(other, tab_name=tab_name) if other.name == name else other for other in self.connections]

    def folders_remembered(self, connection: Connection) -> None:
        """The last folders of a closed tab became its starting folders; the list and the fields follow, so Save can't put back the old ones."""
        before = next((other for other in self.connections if other.name == connection.name), None)
        if before is None:
            return
        self.connections = [connection if other is before else other for other in self.connections]
        if self.selected() is connection:
            self.start_folder.setText(connection.start_folder)
            self.local_start_folder.setText(connection.local_start_folder)

    def select_connection(self, name: str) -> None:
        """Selects this connection, its fields shown and ready to change."""
        self._select(name)
        self.name.setFocus()

    def start_on(self, name: str) -> None:
        """Selects this connection and scrolls it into view; a name that is not in the list leaves the window as it was."""
        item = self.tree.item_of(name)
        if item is None:
            return
        self._select(name)
        self.tree.scrollToItem(item)

    def _select(self, name: str) -> None:
        """Selects this connection, with the folders above it open, so it is always in sight (FJ-01)."""
        self.tree.select_name(name)

    def delete(self) -> None:
        connection = self.selected()
        if connection is None:
            return
        answer = QMessageBox.question(self, WINDOW_TITLE, box_text(DELETE_TEXT.format(name=connection.name)))
        if answer != QMessageBox.StandardButton.Yes:
            return
        kept = [other for other in self.connections if other is not connection]
        try:
            saved.save(kept)  # the list first: where it can't be written, nothing is deleted at all (OR-15, G-2)
        except OSError as error:
            why = NOT_SAVED_WHY.format(path=saved.list_path(), reason=error.strerror or str(error))
            told(self, WINDOW_TITLE, NOTHING_CHANGED.format(name=connection.name, why=why))
            return
        self.connections = kept
        if self.new_from == connection.name:
            self.new_from = ""  # New starts empty again: a connection of this name later is no connection the person chose (FJ-06)
            self.new_from_kept.emit("")
        self.deleted.emit(connection.name)  # its own bookmarks, location profiles and place in the row of tabs go with it
        try:
            credentials.store.remove(connection.name)
        except CredentialError:
            told(self, WINDOW_TITLE, PASSWORD_LEFT_AFTER_DELETE.format(name=connection.name))
        self._fill_list()
        self.clear_fields()

    def copy_name(self, name: str) -> str:
        """The first free name of a copy: "name (copy)", then "name (copy 2)" and so on."""
        taken = {connection.name for connection in self.connections}
        candidate = COPY_NAME.format(name=name)
        number = 2
        while candidate in taken:
            candidate = NEXT_COPY_NAME.format(name=name, number=number)
            number += 1
        return candidate

    def duplicate(self) -> None:
        """A saved copy of the selected connection, its password too (OR-24), selected with its name ready to be typed over."""
        original = self.selected()
        if original is None:
            return
        copy = replace(original, name=self.copy_name(original.name))
        try:
            stored = credentials.store.read(original.name)
            if stored:
                credentials.store.write(copy.name, stored)
        except CredentialError as error:
            QMessageBox.information(self, WINDOW_TITLE, box_text(str(error)))
        kept = [*self.connections, copy]
        try:
            saved.save(kept)  # the list first: where it can't be written, nothing is copied at all (OR-15, G-2)
        except OSError as error:
            why = NOT_SAVED_WHY.format(path=saved.list_path(), reason=error.strerror or str(error))
            self._copy_dropped(original.name, copy.name)  # the password copied for a connection that is not there
            told(self, WINDOW_TITLE, NOTHING_CHANGED.format(name=original.name, why=why))
            return
        self.connections = kept
        self._fill_list()
        self._select(copy.name)
        self.name.setFocus()
        self.name.selectAll()

    def import_sessions(self) -> None:
        """A session list the person picked. A file that can't be read, a file without a single connection and a file whose connections
        the list already holds are three different things, and each says its own (OR-23)."""
        path, _filter = QFileDialog.getOpenFileName(self, sessionimport.IMPORT_BUTTON_TEXT, "", sessionimport.FILE_FILTER)
        if not path:
            return
        try:
            Path(path).read_bytes()  # only to tell a file that can't be read from one that holds no connection
        except OSError as error:
            told(self, WINDOW_TITLE, FILE_NOT_READ_TEXT.format(reason=error.strerror or str(error)))
            return
        found = sessionimport.read_sessions(Path(path))
        if not found:
            told(self, WINDOW_TITLE, NONE_IN_FILE_TEXT)
            return
        taken = {connection.name for connection in self.connections}
        new = [connection for connection in found if connection.name not in taken]
        if not new:
            told(self, WINDOW_TITLE, ALL_THERE_TEXT)
            return
        self._add_imported(new)

    def _add_imported(self, new: list[Connection]) -> None:
        """Connections that came from somewhere else. The list in memory follows only once the file is written: where it can't be
        written, nothing is added and the person is told in plain words (G-2, OR-15)."""
        kept = self.connections + new
        try:
            saved.save(kept)
        except OSError as error:
            why = NOT_SAVED_WHY.format(path=saved.list_path(), reason=error.strerror or str(error))
            told(self, WINDOW_TITLE, NOTHING_CHANGED.format(name=LIST_ITSELF, why=why))
            return
        self.connections = kept
        self._fill_list()
        told(self, WINDOW_TITLE, ADDED_TEXT.format(count=counted(len(new), *CONNECTION_WORDS), was=was(len(new))))

    def import_ssh_settings(self) -> None:
        """The servers of this computer's own SSH settings, each with a tick, and each that can't come over whole saying why. Nothing in
        ~/.ssh is ever written (FJ-08, FJ-09, FJ-10)."""
        found = sshsettings.read_settings()
        if found.state == sshsettings.NO_FILE:
            told(self, WINDOW_TITLE, NO_SSH_FILE_TEXT.format(path=found.path))
            return
        if found.state == sshsettings.UNREADABLE:
            told(self, WINDOW_TITLE, SSH_UNREADABLE_TEXT.format(path=found.path))
            return
        if not found.servers:
            told(self, WINDOW_TITLE, NO_SSH_SERVER_TEXT)
            return
        taken = {connection.name for connection in self.connections}
        dialog = SshImportDialog(found.servers, taken, self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted.value
        chosen = dialog.chosen()
        dialog.deleteLater()
        if not accepted or not chosen:
            return
        new: list[Connection] = []
        for connection in chosen:
            name = free_name(connection.name, taken | {other.name for other in new})
            new.append(replace(connection, name=name))
        self._add_imported(new)

    def open_the_folder(self, folder: str) -> None:
        """Open Every Connection in This Folder: one tab per connection, in the order the folder shows them (FJ-04)."""
        names = self.tree.names_in(folder)
        by_name = {connection.name: connection for connection in self.connections}
        wanted = [by_name[name] for name in names if name in by_name]
        if not wanted:
            return
        self.folder_connect_requested.emit(wanted)
        self.hide()

    def copy_the_address(self) -> None:
        """Copy the Address of the selected connection: on the clipboard, and shown, so the person sees what they are about to paste.
        A password is never in it (OR-19, FJ-12)."""
        connection = self.selected()
        if connection is None:
            return
        address = address_text(connection, connection.start_folder)
        QGuiApplication.clipboard().setText(address)
        show_address(self, address)

    def use_for_a_new_one(self) -> None:
        """New starts with the settings of this connection from now on, its name left empty (FJ-06)."""
        connection = self.selected()
        if connection is None:
            return
        self.new_from = connection.name
        self.new_from_kept.emit(self.new_from)
        self._update_buttons()
        told(self, WINDOW_TITLE, USE_FOR_NEW_TOLD.format(name=connection.name))

    def use_empty_for_a_new_one(self) -> None:
        """New starts with empty fields again (FJ-06)."""
        self.new_from = ""
        self.new_from_kept.emit("")
        self._update_buttons()
        told(self, WINDOW_TITLE, EMPTY_FOR_NEW_TOLD)

    def open_an_address(self) -> None:
        """An address the person pastes opens as a tab and is never saved in this list; a password in it is used for that one login and
        written nowhere (FJ-11, OR-19)."""
        dialog = OpenAddressDialog(self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted.value
        address: Address | None = dialog.address
        dialog.deleteLater()
        if not accepted or address is None:
            return
        taken = {connection.name for connection in self.connections}
        connection = replace(address.connection, name=free_name(address.connection.name, taken), start_folder=address.folder, not_saved=True)
        self.address_requested.emit(connection, address.password)
        self.hide()

    def has_changes(self) -> bool:
        """The fields differ from the selected connection as saved, or a password was typed."""
        connection = self.selected()
        return connection is not None and (self.from_fields() != connection or bool(self.password.text()))

    def _connect(self, *_ignored: object) -> None:
        connection = self.selected()
        if connection is not None and self.has_changes():
            answer = QMessageBox.question(
                self,
                WINDOW_TITLE,
                box_text(SAVE_CHANGES_TEXT.format(name=connection.name)),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Save:
                self.save()
                if self.has_changes():
                    return  # not saved (a name already taken was told): the window stays, the edits too
                connection = self.selected()
            elif answer == QMessageBox.StandardButton.Discard:
                self._row_changed()  # the fields show the saved connection again
            else:
                return
        if connection is not None:
            self.connect_requested.emit(connection)
            self.hide()


