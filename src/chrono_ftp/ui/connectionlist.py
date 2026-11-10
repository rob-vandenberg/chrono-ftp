
from pathlib import Path

from dataclasses import replace

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from chrono_ftp.config import connections as saved
from chrono_ftp.config import sessionimport
from chrono_ftp.core import colours, credentials
from chrono_ftp.core.connection import DEFAULT_PORTS, FTP, HIGHEST_PORT, LOWEST_PORT, SFTP, Connection
from chrono_ftp.core.errors import CredentialError
from chrono_ftp.ui.menus import open_menu
from chrono_ftp.ui.passwordeye import PasswordEye
from chrono_ftp.ui.plaintext import box_text

# --- Version ------------------------------------------------------------
__version__ = "connectionlist.py 0.0.32"

# --- Version history ----------------------------------------------------
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
EXPORT_LATER_TEXT: str = "Export comes in a later version."
COPY_NAME: str = "{name} (copy)"
SAVE_CHANGES_TEXT: str = "Save changes to {name}?"
NEXT_COPY_NAME: str = "{name} (copy {number})"
PROTOCOL_LABELS: dict[str, str] = {SFTP: "SFTP", FTP: "FTP"}


def colour_block(colour: str) -> QIcon:
    pixmap = QPixmap(COLOUR_BLOCK_SIZE, COLOUR_BLOCK_SIZE)
    pixmap.fill(QColor(colour))
    return QIcon(pixmap)


class ConnectionList(QDialog):
    connect_requested = Signal(object)  # a Connection

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.colour: str = colours.FIRST_CONNECTION
        self.stored_password: bool = False
        self.connections: list[Connection] = []
        self.list = QListWidget()
        self.name = QLineEdit()
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
        self.colour_button = QPushButton("Choose colour")
        self.use_colour = QCheckBox("Use a colour")
        self.new_button = QPushButton("New")
        self.save_button = QPushButton("Save")
        self.connect_button = QPushButton("Connect")
        self.import_button = QPushButton(IMPORT_TEXT)
        self.export_button = QPushButton(EXPORT_TEXT)
        self.export_button.setEnabled(False)  # greyed, never hidden (OR-20)
        for button in (self.browse_button, self.colour_button, self.new_button, self.save_button, self.import_button, self.export_button):
            button.setAutoDefault(False)  # Enter never goes to another button, also not to the one with the focus
        self.connect_button.setDefault(True)  # Enter connects the selected connection (D-050)
        self.export_button.setToolTip(EXPORT_LATER_TEXT)
        self.connect_action = QAction("Connect", self)
        self.duplicate_action = QAction("Duplicate", self)
        self.delete_action = QAction("Delete", self)
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
        form.addRow("Starting folder", self.start_folder)
        colour_row = QHBoxLayout()
        colour_row.addWidget(self.use_colour)
        colour_row.addWidget(self.colour_button)
        colour_row.addStretch(1)
        form.addRow("Colour", colour_row)
        buttons = QHBoxLayout()
        for button in (self.new_button, self.save_button):
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
        list_side.addWidget(self.list)
        list_side.addLayout(list_buttons)
        list_part = QWidget()
        list_part.setLayout(list_side)
        self.parts.addWidget(list_part)
        self.parts.addWidget(fields)
        self.parts.setStretchFactor(1, 1)  # a wider window widens the fields, the list keeps its width
        self.parts.setSizes([LIST_WIDTH, WINDOW_WIDTH - LIST_WIDTH])
        layout = QHBoxLayout(self)
        layout.addWidget(self.parts)
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.itemDoubleClicked.connect(self._connect)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_menu)
        self.connect_action.triggered.connect(self._connect)
        self.duplicate_action.triggered.connect(self.duplicate)
        self.delete_action.triggered.connect(self.delete)
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self.name.textChanged.connect(self._update_buttons)
        self.host.textChanged.connect(self._update_buttons)
        self.browse_button.clicked.connect(self._browse)
        self.colour_button.clicked.connect(self._choose_colour)
        self.use_colour.toggled.connect(self.colour_button.setEnabled)
        self.new_button.clicked.connect(self.clear_fields)
        self.save_button.clicked.connect(self.save)
        self.connect_button.clicked.connect(self._connect)
        self.import_button.clicked.connect(self.import_sessions)
        self._load()
        self.clear_fields()

    def _load(self) -> None:
        loaded = saved.load()
        self.connections = list(loaded.connections)
        self._fill_list()
        if loaded.set_aside is not None:
            QMessageBox.information(self, WINDOW_TITLE, box_text(f"{SET_ASIDE_TEXT}\n{loaded.set_aside}"))

    def user_accepted(self, name: str, user: str) -> None:
        """A login kept this user name; the fields follow, so Save can't put back the old one."""
        self.connections = [replace(connection, user=user) if connection.name == name else connection for connection in self.connections]
        if self.name.text().strip() == name:
            self.user.setText(user)

    def _fill_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for connection in sorted(self.connections, key=lambda item: item.name.casefold()):
            item = QListWidgetItem(colour_block(connection.colour or colours.NO_COLOUR_TAB), connection.name)
            item.setData(Qt.ItemDataRole.UserRole, connection.name)
            self.list.addItem(item)
        self.list.blockSignals(False)

    def selected(self) -> Connection | None:
        item = self.list.currentItem()
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return next((connection for connection in self.connections if connection.name == name), None)

    def _row_changed(self) -> None:
        connection = self.selected()
        if connection is None:
            return
        self.name.setText(connection.name)
        self.protocol.setCurrentIndex(self.protocol.findData(connection.protocol))
        self.host.setText(connection.host)
        self.port.setValue(connection.port)
        self.user.setText(connection.user)
        self.key_file.setText(connection.key_file)
        self.start_folder.setText(connection.start_folder)
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
        for action in (self.connect_action, self.duplicate_action, self.delete_action):
            action.setEnabled(chosen)
        self.connect_button.setEnabled(chosen)

    def menu_for(self, place: QPoint) -> QMenu | None:
        """The right-click menu of the connection at this place in the list; nothing outside a connection."""
        item = self.list.itemAt(place)
        if item is None:
            return None
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(self.connect_action)
        menu.addSeparator()
        menu.addAction(self.duplicate_action)
        menu.addAction(self.delete_action)
        return menu

    def _show_menu(self, place: QPoint) -> None:
        menu = self.menu_for(place)
        if menu is not None:
            open_menu(menu, self.list.viewport().mapToGlobal(place))
            menu.deleteLater()

    def clear_fields(self) -> None:
        self.list.setCurrentRow(-1)
        self.list.clearSelection()
        for field in (self.name, self.host, self.user, self.password, self.key_file, self.start_folder):
            field.clear()
        self.password.setPlaceholderText("")
        self.protocol.setCurrentIndex(self.protocol.findData(SFTP))
        self.port.setValue(DEFAULT_PORTS[SFTP])
        self._set_colour("")
        self._update_buttons()

    def _browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "Key file")
        if path:
            self.key_file.setText(path)

    def _choose_colour(self) -> None:
        colour = QColorDialog.getColor(QColor(self.colour), self, "Colour of this connection")
        if colour.isValid():
            self._set_colour(colour.name())

    def from_fields(self) -> Connection:
        return Connection(
            name=self.name.text().strip(),
            host=self.host.text().strip(),
            user=self.user.text().strip(),
            port=self.port.value(),
            protocol=str(self.protocol.currentData()),
            key_file=self.key_file.text().strip(),
            colour=self.colour if self.use_colour.isChecked() else "",
            start_folder=self.start_folder.text().strip(),
        )

    def save(self) -> None:
        edited = self.selected()
        connection = self.from_fields()
        if any(other.name == connection.name and other is not edited for other in self.connections):
            QMessageBox.information(self, WINDOW_TITLE, box_text(SAME_NAME_TEXT))
            return
        try:
            if edited is not None and edited.name != connection.name:
                stored = credentials.store.read(edited.name)
                if stored:
                    credentials.store.write(connection.name, stored)
                    credentials.store.remove(edited.name)
            if self.password.text():
                credentials.store.write(connection.name, self.password.text())
        except CredentialError as error:
            QMessageBox.information(self, WINDOW_TITLE, box_text(str(error)))
        self.connections = [other for other in self.connections if other is not edited] + [connection]
        saved.save(self.connections)
        self._fill_list()
        self._select(connection.name)

    def select_connection(self, name: str) -> None:
        """Selects this connection, its fields shown and ready to change."""
        self._select(name)
        self.name.setFocus()

    def start_on(self, name: str) -> None:
        """Selects this connection and scrolls it into view; a name that is not in the list leaves the window as it was."""
        self._select(name)
        item = self.list.currentItem()
        if item is not None:
            self.list.scrollToItem(item)

    def _select(self, name: str) -> None:
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == name:
                self.list.setCurrentRow(row)

    def delete(self) -> None:
        connection = self.selected()
        if connection is None:
            return
        answer = QMessageBox.question(self, WINDOW_TITLE, box_text(DELETE_TEXT.format(name=connection.name)))
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.connections = [other for other in self.connections if other is not connection]
        saved.save(self.connections)
        try:
            credentials.store.remove(connection.name)
        except CredentialError as error:
            QMessageBox.information(self, WINDOW_TITLE, box_text(str(error)))
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
        self.connections.append(copy)
        saved.save(self.connections)
        self._fill_list()
        self._select(copy.name)
        self.name.setFocus()
        self.name.selectAll()

    def import_sessions(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, sessionimport.IMPORT_BUTTON_TEXT, "", sessionimport.FILE_FILTER)
        if not path:
            return
        taken = {connection.name for connection in self.connections}
        new = [connection for connection in sessionimport.read_sessions(Path(path)) if connection.name not in taken]
        self.connections.extend(new)
        saved.save(self.connections)
        self._fill_list()
        QMessageBox.information(self, WINDOW_TITLE, box_text(f"{len(new)} connections were added."))

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


