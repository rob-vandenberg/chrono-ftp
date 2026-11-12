"""The bookmarks window (FH-02, FH-03): the bookmarks of this computer, those of the server and the location profiles, each to rename,
delete, drag into another order; a bookmark can get a key of its own."""

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from chrono_ftp.config.bookmarks import LOCAL, REMOTE, SHARED, Bookmark, Places, Profile, shows_for
from chrono_ftp.connections.base import shown_name
from chrono_ftp.engine.browsing import typed_local_path, typed_remote_path
from chrono_ftp.ui.plaintext import box_text

# --- Version ------------------------------------------------------------
__version__ = "bookmarksdialog.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial: bookmarks of both sides and location profiles: rename, delete, order, a key per bookmark, shared or not (FH-02, FH-03);
#          a bookmark key needs Ctrl or Alt, or is a free F-key, never a standard key binding of Qt nor Ctrl with a key lists use; an & in
#          a connection name stays an &; a folder typed here is read as in the path field, a remote one never stays relative; without
#          the login's home folder only a full server path is taken, anything else is told calmly

WINDOW_TITLE: str = "Bookmarks and location profiles"
LOCAL_TAB: str = "This computer"
REMOTE_TAB: str = "Server"
PROFILES_TAB: str = "Location profiles"
SHARED_TEXT: str = "Show on every connection"
SHARED_TIP: str = "On: every connection shows it. Off: only {name} shows it."
DELETE_TEXT: str = "Delete"
KEY_TIP: str = "A key that opens this bookmark while its side has the keyboard focus: with Ctrl or Alt, or a free F-key. Clear removes it."
CLEAR_KEY_TEXT: str = "Clear"
KEY_TAKEN_TEXT: str = "{key} already opens {what}. Please choose another key."
KEY_RULE_TEXT: str = "{key} can't open a bookmark, as lists and fields need it. A bookmark key holds Ctrl or Alt (Shift may be added), or is one of F1 to F12 that Chrono FTP doesn't use itself."
MODIFIER_NAMES: tuple[str, ...] = ("Ctrl", "Alt", "Shift", "Meta")
FUNCTION_KEYS: frozenset[str] = frozenset(f"F{number}" for number in range(1, 13))
LIST_KEYS: tuple[str, ...] = ("Left", "Right", "Up", "Down", "Home", "End", "PgUp", "PgDown", "Backspace", "Del", "Space", "Tab")  # with Ctrl, lists and fields use them
FULL_PATH_TEXT: str = "The home folder of this server is not known here, so {typed} can't be read. Please type the full path, starting with /."
ORDER_TIP: str = "Drag an entry to change the order."
FOR_ALL_MARK: str = "  (every connection)"
WIDTH: int = 640
HEIGHT: int = 420


def _modifiers_and_key(key: str) -> tuple[set[str], str]:
    """"Ctrl+Shift+1" as ({"Ctrl", "Shift"}, "1"); the key itself may be a plus: "Ctrl++"."""
    modifiers: set[str] = set()
    rest = key
    found = True
    while found:
        found = False
        for name in MODIFIER_NAMES:
            if rest.startswith(name + "+") and len(rest) > len(name) + 1:
                modifiers.add(name)
                rest = rest[len(name) + 1 :]
                found = True
    return modifiers, rest


def _canonical(key: str) -> str:
    return QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)


def keys_lists_need() -> frozenset[str]:
    """Every standard key binding of Qt (copy, paste, undo, find, moving by words, Back, …), and the keys of LIST_KEYS with Ctrl or
    Ctrl+Shift: lists and fields act on them, so a bookmark never takes one."""
    found = {_canonical(prefix + key) for key in LIST_KEYS for prefix in ("Ctrl+", "Ctrl+Shift+")}
    for standard in QKeySequence.StandardKey:
        if standard != QKeySequence.StandardKey.UnknownKey:
            found.update(binding.toString(QKeySequence.SequenceFormat.PortableText) for binding in QKeySequence.keyBindings(standard))
    return frozenset(found)


def key_usable(key: str) -> bool:
    """A key a bookmark may have: with Ctrl or Alt (Shift may be added), or a plain F1 to F12; never a key lists and fields need. Whether the
    program uses it already is asked apart (the keys of the program differ per window)."""
    if not key or "," in key.replace("+,", ""):
        return False
    modifiers, rest = _modifiers_and_key(key)
    if "Meta" in modifiers or not rest:
        return False
    usable = bool(modifiers & {"Ctrl", "Alt"}) or (not modifiers and rest in FUNCTION_KEYS)
    return usable and _canonical(key) not in keys_lists_need()


class _Page(QWidget):
    """One tab: a list that can be reordered by dragging, and the fields of the selected entry."""

    def __init__(self) -> None:
        super().__init__()
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setToolTip(ORDER_TIP)
        self.form = QFormLayout()
        self.delete_button = QPushButton(DELETE_TEXT)
        self.delete_button.setAutoDefault(False)
        right = QVBoxLayout()
        right.addLayout(self.form)
        right.addStretch(1)
        right.addWidget(self.delete_button, 0, Qt.AlignmentFlag.AlignRight)
        layout = QHBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addLayout(right, 1)

    def numbers(self) -> list[int]:
        """The entries of the list in their order now, as numbers into the window's own list."""
        found: list[int] = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            number = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if isinstance(number, int):
                found.append(number)
        return found

    def chosen(self) -> int:
        item = self.list.currentItem()
        number = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return number if isinstance(number, int) else -1


class BookmarksDialog(QDialog):
    """Works on a copy: OK hands back the changed places (places()), Cancel changes nothing."""

    def __init__(self, places: Places, connection: str, taken_keys: dict[str, str], start_tab: str = LOCAL, parent: QWidget | None = None, local_base: str = "/", remote_base: str = "/", remote_home: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(WIDTH, HEIGHT)
        self.connection: str = connection  # the connection of the tab shown; empty without a tab
        self.local_base: str = local_base  # a relative folder typed here starts at these, as in the path field (D-055)
        self.remote_base: str = remote_base or remote_home or "/"
        self.remote_home: str = remote_home
        self.taken_keys: dict[str, str] = taken_keys  # the keys of the program itself -> what they do
        self.marks: list[Bookmark | None] = list(places.bookmarks)  # None: deleted
        self.profile_list: list[Profile | None] = list(places.profiles)
        self.typed = places.typed
        self.filling: bool = False
        self.tabs = QTabWidget()
        self.local_page = _Page()
        self.remote_page = _Page()
        self.profile_page = _Page()
        self.tabs.addTab(self.local_page, LOCAL_TAB)
        self.tabs.addTab(self.remote_page, f"{REMOTE_TAB}: {connection.replace('&', '&&')}" if connection else REMOTE_TAB)
        self.tabs.addTab(self.profile_page, PROFILES_TAB)
        self.mark_fields: dict[str, tuple[QLineEdit, QLineEdit, QKeySequenceEdit]] = {}
        for side, page in ((LOCAL, self.local_page), (REMOTE, self.remote_page)):
            name, path, key = QLineEdit(), QLineEdit(), QKeySequenceEdit()
            key.setMaximumSequenceLength(1)
            key.setClearButtonEnabled(True)
            key.setToolTip(KEY_TIP)
            page.form.addRow("Name", name)
            page.form.addRow("Folder", path)
            page.form.addRow("Key", key)
            self.mark_fields[side] = (name, path, key)
            name.textEdited.connect(lambda _text="", side=side: self._mark_edited(side))
            path.textEdited.connect(lambda _text="", side=side: self._mark_edited(side))
            key.editingFinished.connect(lambda side=side: self._key_edited(side))
            page.list.currentRowChanged.connect(lambda _row=0, side=side: self._show_mark(side))
            page.delete_button.clicked.connect(lambda _checked=False, side=side: self._delete_mark(side))
        self.mark_shared = QCheckBox(SHARED_TEXT)
        self.mark_shared.setToolTip(SHARED_TIP.format(name=connection or "this connection"))
        self.mark_shared.setEnabled(bool(connection))  # without a tab every new one is shared anyway
        self.remote_page.form.addRow("", self.mark_shared)
        self.mark_shared.clicked.connect(lambda _checked=False: self._mark_edited(REMOTE))
        self.profile_name, self.profile_folder, self.profile_local, self.profile_remote = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.profile_folder.setPlaceholderText("none")
        self.profile_shared = QCheckBox(SHARED_TEXT)
        self.profile_shared.setToolTip(SHARED_TIP.format(name=connection or "this connection"))
        self.profile_shared.setEnabled(bool(connection))
        for label, field in (("Name", self.profile_name), ("In folder", self.profile_folder), ("Local folder", self.profile_local), ("Remote folder", self.profile_remote)):
            self.profile_page.form.addRow(label, field)
            field.textEdited.connect(lambda _text="": self._profile_edited())
        self.profile_page.form.addRow("", self.profile_shared)
        self.profile_shared.clicked.connect(lambda _checked=False: self._profile_edited())
        self.mark_fields[REMOTE][1].editingFinished.connect(self._remote_mark_left)
        self.profile_remote.editingFinished.connect(self._remote_profile_left)
        self.profile_page.list.currentRowChanged.connect(lambda _row=0: self._show_profile())
        self.profile_page.delete_button.clicked.connect(self._delete_profile)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)
        self._fill()
        self.tabs.setCurrentIndex({LOCAL: 0, REMOTE: 1}.get(start_tab, 2))

    # --- the lists ---------------------------------------------------------

    def page_of(self, side: str) -> _Page:
        return self.local_page if side == LOCAL else self.remote_page

    def _mark_text(self, mark: Bookmark) -> str:
        shared = mark.side == REMOTE and mark.connection == SHARED and bool(self.connection)
        key = f"    {mark.key}" if mark.key else ""
        return f"{mark.name}{FOR_ALL_MARK if shared else ''}{key}"

    def _profile_text(self, profile: Profile) -> str:
        shared = profile.connection == SHARED and bool(self.connection)
        return f"{profile.folder + ' / ' if profile.folder else ''}{profile.name}{FOR_ALL_MARK if shared else ''}"

    def _fill(self) -> None:
        self.filling = True
        for side in (LOCAL, REMOTE):
            page = self.page_of(side)
            page.list.clear()
            for number, mark in enumerate(self.marks):
                if mark is not None and mark.side == side and shows_for(mark.connection, side, self.connection):
                    item = QListWidgetItem(self._mark_text(mark))
                    item.setData(Qt.ItemDataRole.UserRole, number)
                    item.setToolTip(shown_name(mark.path))
                    page.list.addItem(item)
        self.profile_page.list.clear()
        for number, profile in enumerate(self.profile_list):
            if profile is not None and profile.connection in (SHARED, self.connection):
                item = QListWidgetItem(self._profile_text(profile))
                item.setData(Qt.ItemDataRole.UserRole, number)
                item.setToolTip(f"{shown_name(profile.local)}\n{shown_name(profile.remote)}")
                self.profile_page.list.addItem(item)
        self.filling = False
        for page in (self.local_page, self.remote_page, self.profile_page):
            if page.list.count():
                page.list.setCurrentRow(0)
        self._show_mark(LOCAL)
        self._show_mark(REMOTE)
        self._show_profile()

    def _retext(self, page: _Page, number: int, text: str) -> None:
        for row in range(page.list.count()):
            item = page.list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == number:
                item.setText(text)

    # --- bookmarks ---------------------------------------------------------

    def _show_mark(self, side: str) -> None:
        if self.filling:
            return
        page = self.page_of(side)
        number = page.chosen()
        mark = self.marks[number] if 0 <= number < len(self.marks) else None
        name, path, key = self.mark_fields[side]
        for widget in (name, path, key, page.delete_button):
            widget.setEnabled(mark is not None)
        name.setText(mark.name if mark is not None else "")
        path.setText(shown_name(mark.path) if mark is not None else "")
        key.setKeySequence(QKeySequence(mark.key if mark is not None else ""))
        if side == REMOTE:
            self.mark_shared.setChecked(mark is not None and mark.connection == SHARED)
            self.mark_shared.setEnabled(mark is not None and bool(self.connection))

    def _mark_edited(self, side: str) -> None:
        page = self.page_of(side)
        number = page.chosen()
        mark = self.marks[number] if 0 <= number < len(self.marks) else None
        if mark is None:
            return
        name, path, _key = self.mark_fields[side]
        new_path = self.typed_folder(side, path.text(), mark.path)
        connection = mark.connection
        if side == REMOTE and self.connection:
            connection = SHARED if self.mark_shared.isChecked() else self.connection
        changed = replace(mark, name=name.text().strip() or mark.name, path=new_path or mark.path, connection=connection)
        self.marks[number] = changed
        self._retext(page, number, self._mark_text(changed))

    def typed_folder(self, side: str, text: str, before: str) -> str:
        """A folder typed here, read as in the path field: ~ and a relative path are taken from the folder shown (on the right the home
        folder of the login when none is shown); a remote folder is never kept relative. Unchanged or empty: the folder it was."""
        typed = text.strip()
        if not typed or typed == shown_name(before) or not self.remote_folder_readable(side, typed):
            return before  # a server folder that can't be read yet stays as it was; leaving the field says why
        return typed_local_path(self.local_base, typed) if side == LOCAL else typed_remote_path(self.remote_base, typed, self.remote_home)

    def remote_folder_readable(self, side: str, typed: str) -> bool:
        """Without the home folder of the login (no tab of this connection), a server folder is only a full path starting with /."""
        return side == LOCAL or bool(self.remote_home) or typed.startswith("/")

    def _check_remote_field(self, field: QLineEdit, before: str) -> None:
        """Leaving a server folder field that holds what can't be read: a calm message, and the field shows the folder kept."""
        typed = field.text().strip()
        if typed and not self.remote_folder_readable(REMOTE, typed):
            field.setText(shown_name(before))
            QMessageBox.information(self, WINDOW_TITLE, box_text(FULL_PATH_TEXT.format(typed=typed)))

    def _remote_mark_left(self) -> None:
        number = self.remote_page.chosen()
        mark = self.marks[number] if 0 <= number < len(self.marks) else None
        if mark is not None:
            self._check_remote_field(self.mark_fields[REMOTE][1], mark.path)

    def _remote_profile_left(self) -> None:
        number = self.profile_page.chosen()
        profile = self.profile_list[number] if 0 <= number < len(self.profile_list) else None
        if profile is not None:
            self._check_remote_field(self.profile_remote, profile.remote)

    def key_owner(self, key: str, side: str, number: int) -> str:
        """What this key does already on this side; empty when it is free."""
        if key in self.taken_keys:
            return self.taken_keys[key]
        for other_number, other in enumerate(self.marks):
            if other is not None and other_number != number and other.key == key and other.side == side and shows_for(other.connection, side, self.connection):
                return f"the bookmark {other.name}"
        return ""

    def _key_edited(self, side: str) -> None:
        page = self.page_of(side)
        number = page.chosen()
        mark = self.marks[number] if 0 <= number < len(self.marks) else None
        if mark is None:
            return
        field = self.mark_fields[side][2]
        key = field.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        owner = self.key_owner(key, side, number) if key else ""
        if owner or (key and not key_usable(key)):  # what the key does already says the most
            QMessageBox.information(self, WINDOW_TITLE, box_text(KEY_TAKEN_TEXT.format(key=key, what=owner) if owner else KEY_RULE_TEXT.format(key=key)))
            field.setKeySequence(QKeySequence(mark.key))
            return
        changed = replace(mark, key=key)
        self.marks[number] = changed
        self._retext(page, number, self._mark_text(changed))

    def _delete_mark(self, side: str) -> None:
        page = self.page_of(side)
        number = page.chosen()
        if 0 <= number < len(self.marks):
            self.marks[number] = None
            page.list.takeItem(page.list.currentRow())
            self._show_mark(side)

    # --- location profiles ---------------------------------------------------

    def _show_profile(self) -> None:
        if self.filling:
            return
        number = self.profile_page.chosen()
        profile = self.profile_list[number] if 0 <= number < len(self.profile_list) else None
        for widget in (self.profile_name, self.profile_folder, self.profile_local, self.profile_remote, self.profile_page.delete_button):
            widget.setEnabled(profile is not None)
        self.profile_name.setText(profile.name if profile is not None else "")
        self.profile_folder.setText(profile.folder if profile is not None else "")
        self.profile_local.setText(shown_name(profile.local) if profile is not None else "")
        self.profile_remote.setText(shown_name(profile.remote) if profile is not None else "")
        self.profile_shared.setChecked(profile is not None and profile.connection == SHARED)
        self.profile_shared.setEnabled(profile is not None and bool(self.connection))

    def _profile_edited(self) -> None:
        number = self.profile_page.chosen()
        profile = self.profile_list[number] if 0 <= number < len(self.profile_list) else None
        if profile is None:
            return

        connection = profile.connection
        if self.connection:
            connection = SHARED if self.profile_shared.isChecked() else self.connection
        changed = Profile(
            self.profile_name.text().strip() or profile.name,
            self.typed_folder(LOCAL, self.profile_local.text(), profile.local),
            self.typed_folder(REMOTE, self.profile_remote.text(), profile.remote),
            self.profile_folder.text().strip(),
            connection,
        )
        self.profile_list[number] = changed
        self._retext(self.profile_page, number, self._profile_text(changed))

    def _delete_profile(self) -> None:
        number = self.profile_page.chosen()
        if 0 <= number < len(self.profile_list):
            self.profile_list[number] = None
            self.profile_page.list.takeItem(self.profile_page.list.currentRow())
            self._show_profile()

    # --- the result ------------------------------------------------------------

    @staticmethod
    def _in_new_order[T](entries: list[T | None], pages: list[list[int]]) -> tuple[T, ...]:
        """Each list's entries in its new order, in the places its entries had; the entries no list shows keep their place."""
        result = list(entries)
        for order in pages:
            places = sorted(order)
            for place, number in zip(places, order, strict=True):
                result[place] = entries[number]
        return tuple(entry for entry in result if entry is not None)

    def places(self) -> Places:
        marks = self._in_new_order(self.marks, [self.local_page.numbers(), self.remote_page.numbers()])
        profiles = self._in_new_order(self.profile_list, [self.profile_page.numbers()])
        return Places(marks, profiles, self.typed)
