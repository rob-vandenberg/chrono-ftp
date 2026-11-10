"""Browse for a folder on the server, for the target of an upload (FB-09). The folders load in the connection's own thread (D-019)."""

from collections.abc import Callable
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.connections.base import RemoteEntry, joined, parent_of, shown_name
from chrono_ftp.ui.plaintext import box_text, fitting, fitting_words, plain_label

# --- Version ------------------------------------------------------------
__version__ = "remotefolderpicker.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: the folders of the server, opened with a double click; the one shown is chosen, and Choose is greyed while no
#          folder is shown (loading, or the connection lost); a folder that isn't there starts the picker at the nearest one above
#          names are shown as written, never read as HTML
#          a long folder is fitted to the screen; a relative start means /
#          a closed picker takes no more answers: a listing that arrives late never reaches it; when it starts above a folder that can't be
#          read it says which and why; its hover text is always what it shows, in full; a long word in a server's reason is shortened;
#          a greyed Choose says why; only a folder that is not there makes it climb, any other failure is shown, with the way up; Choose
#          takes the highlighted folder, else the one shown, and says which, and that nothing is sent yet; Enter in the list opens the
#          highlighted folder, as a key acts on the part that has the focus

WINDOW_TITLE: str = "Folder on the server"
UP_TEXT: str = ".."
LOADING_TEXT: str = "Reading the folder…"
CHOOSE_TEXT: str = "Take {folder} as the target; nothing is sent yet"
WAIT_TEXT: str = "Wait until the folder is shown"
LIST_TEXT: str = "Double-click a folder, or press Enter, to open it; Choose takes the folder that is highlighted"
NOTHING_TO_CHOOSE_TEXT: str = "No folder is shown to choose: {reason}"
INSTEAD_TEXT: str = "Shown instead of {folder}, which can't be read: {reason}"
Failed = Callable[[str, bool], None]  # why a folder could not be shown, and whether that is because it is not there
Load = Callable[[str, Callable[[list[RemoteEntry]], None], Failed], None]  # a folder, then what it holds or why it failed


class FolderList(QListWidget):
    """Enter on a highlighted folder opens it, and goes no further: a key acts on the part that has the focus (OR-21), never also on the
    window's default button."""

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        item = self.currentItem()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and item is not None:
            self.itemActivated.emit(item)
            event.accept()
            return
        super().keyPressEvent(event)


class RemoteFolderPicker(QDialog):
    def __init__(self, start: str, load: Load, parent: QWidget | None = None, lost_text: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.load: Load = load
        self.lost_text: str = lost_text  # what load says when the connection is gone: then there is nothing above to try either
        self.folder: str = start if start.startswith("/") else "/"  # a relative path means nothing on a server
        self.wanted: str = self.folder
        self.loaded: bool = False  # self.folder is on the screen, read from the server
        self.gone: bool = False  # closed: a listing that arrives later goes nowhere
        self.instead: tuple[str, str] | None = None  # the folder asked for that could not be read, and why: a folder above is shown
        self.names: list[str | None] = []  # per row the real name (Qt can't hold one that is not valid UTF-8); None for the way up
        self.place = plain_label()
        self.place.setWordWrap(True)
        self.folders = FolderList()
        self.folders.setToolTip(LIST_TEXT)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        choose = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert choose is not None
        self.choose = choose
        self.choose.setText("Choose")
        layout = QVBoxLayout(self)
        layout.addWidget(self.place)
        layout.addWidget(self.folders, 1)
        layout.addWidget(self.buttons)
        self.folders.itemActivated.connect(self._open)  # a double-click, or Enter on the highlighted folder
        self.folders.itemSelectionChanged.connect(self._highlight_changed)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.open_folder(self.folder)

    def _highlight_changed(self) -> None:
        """Choose says the folder it takes now."""
        if self.choose.isEnabled():
            self._can_choose(True)

    def _can_choose(self, can: bool, why: str = "") -> None:
        """Choose takes only a folder that is on the screen: never one that is still loading, never one that could not be read. Greyed, its
        hover text says why: still loading, or what went wrong."""
        self.choose.setEnabled(can)
        self.choose.setToolTip(box_text(CHOOSE_TEXT.format(folder=shown_name(self.chosen()))) if can else box_text(NOTHING_TO_CHOOSE_TEXT.format(reason=why)) if why else WAIT_TEXT)

    def _say(self, folder: str, notes: list[str], instead: str = "") -> None:
        """The place line: the folder's name fitted to the screen, then what is to be said of it; the hover text says the same with every
        name in full, so it never tells another place. Nothing to hover over while it loads."""
        fitted = [fitting(folder, self.place)] if folder else []
        whole = [folder] if folder else []
        if instead:
            fitted.append(INSTEAD_TEXT.format(folder=fitting(instead, self.place), reason=fitting_words(notes[0], self.place)))
            whole.append(INSTEAD_TEXT.format(folder=instead, reason=notes[0]))
            notes = notes[1:]
        self.place.setText("\n".join([*fitted, *(fitting_words(note, self.place) for note in notes)]))  # a server's reason may hold a long path
        self.place.setToolTip(box_text("\n".join([*whole, *notes])) if whole or instead or notes != [LOADING_TEXT] else "")

    def open_folder(self, folder: str) -> None:
        self.wanted = folder
        self._say("", [LOADING_TEXT])
        self._can_choose(False)

        def shown(entries: list[RemoteEntry]) -> None:
            if self.gone or folder != self.wanted:
                return  # the person has moved on
            self.folder = folder
            self.loaded = True
            self._can_choose(True)
            if self.instead is not None:
                self._say(shown_name(folder), [shown_name(self.instead[1])], shown_name(self.instead[0]))
            else:
                self._say(shown_name(folder), [])
            self.folders.clear()
            self.names = [None] if folder != "/" else []
            self.names += sorted((entry.name for entry in entries if entry.is_folder), key=str.lower)
            for name in self.names:
                self.folders.addItem(UP_TEXT if name is None else shown_name(name))

        def failed(text: str, missing: bool) -> None:
            if self.gone:
                return
            if folder == self.wanted and not self.loaded and folder != "/" and missing:
                if self.instead is None:
                    self.instead = (folder, text)  # said once the folder above is shown
                self.open_folder(parent_of(folder))  # a folder that isn't there: start at the nearest one above it that is
                return
            if folder != self.wanted:
                return
            self.wanted = self.folder
            if self.loaded:
                self._say(shown_name(self.folder), [shown_name(text)])
                self._can_choose(True)  # the folder shown before stays the one Choose takes
                return
            lost = text == self.lost_text  # no connection: no folder to name, and no way up either
            self.folder = folder  # nothing shown yet: this folder, with only the way up, and nothing to choose
            self._say("" if lost else shown_name(folder), [shown_name(text)])
            self.folders.clear()
            self.names = [None] if folder != "/" and not lost else []
            for _name in self.names:
                self.folders.addItem(UP_TEXT)
            self._can_choose(False, text)

        self.load(folder, shown, failed)

    def _open(self, item: QListWidgetItem) -> None:
        row = self.folders.row(item)
        self.instead = None  # the person moves on: what was said about the start is said no more
        if 0 <= row < len(self.names):
            name = self.names[row]
            self.open_folder(parent_of(self.folder) if name is None else joined(self.folder, name))

    @override
    def done(self, result: int) -> None:
        """Closed, by Choose, Cancel or the window's close button: from here on it takes no more answers, also once it is deleted."""
        self.gone = True
        super().done(result)

    def chosen(self) -> str:
        """The folder highlighted in the list, as most people guess Choose takes; else the folder shown above."""
        row = self.folders.currentRow()
        item = self.folders.currentItem()
        if self.loaded and item is not None and item.isSelected() and 0 <= row < len(self.names):
            name = self.names[row]
            if name is not None:
                return joined(self.folder, name)
        return self.folder
