"""The Properties window (FM-01 to FM-04), on both sides and for several files at once: what they are, and their owner, group and
rights, each changeable where the connection or this computer allows it. A field where the files differ says so."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent, QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.engine import properties, rights
from chrono_ftp.engine.checksums import Checksum
from chrono_ftp.engine.listing import size_text as short_size
from chrono_ftp.engine.properties import Summary
from chrono_ftp.engine.rights import Rights
from chrono_ftp.engine.sizes import Count
from chrono_ftp.ui.plaintext import plain_label
from chrono_ftp.ui.rightseditor import RightsEditor

# --- Version ------------------------------------------------------------
__version__ = "propertiesdialog.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: The Size tab: total size, files and folders, calculated away from the window and stoppable; the Checksum tab: the algorithm,
#          last chosen first, Calculate, the names with their checksums and Copy; both sides and several files (FE-02); review round 1:
#          the tab says calmly when the server takes no further login and the tab's connection waits for the checksums
# v0.0.37: Initial: in place of the window of owner and permissions: both sides, several files, owner and group from lists, the rights
#          editor, everything inside with x for folders where they have r; OK only when something changed (D-060); folders get Run
#          for the classes whose Read box is on; OK waits while the octal field holds no complete number; only the touched boxes
#          change, also for everything inside; Enter in a name or the octal field moves on instead of applying the window; a date out of
#          range is unknown; any name of valid form goes, the lists only suggest; a name the screen can't show is never sent; without a
#          shell and with unknown rights only a complete rights value is set, and the window says so; "give folders Run" is off at first
#          and follows only the Read boxes switched on in this change; "inside" alone changes nothing

WINDOW_TITLE: str = "Properties"
DIFFERS_TEXT: str = "(differs)"
UNKNOWN_TEXT: str = "(unknown)"
INSIDE_TEXT: str = "Also everything inside the selected folders"
FOLDERS_X_TEXT: str = "Give folders Run (open) where they get Read"
FOLDERS_X_TIP: str = "Folders inside get Run for owner, group or others where you switch Read on here, so they can still be opened; files are left without it."
WHOLE_RIGHTS_NOTE: str = "The server did not tell the current rights, so they can only be set as a whole: every box, or a full octal number."
WHOLE_RIGHTS_TEXT: str = "The server did not tell the rights of every selected file, and without a shell a box can't be changed alone: please set every box, or type a full octal number such as 644."
NOT_A_NAME_TEXT: str = "{name} can't be a {what} name."
LINK_RIGHTS_TEXT: str = "A link has no rights of its own: change those of the file it leads to."
GENERAL_TAB_TEXT: str = "General"
SIZE_TAB_TEXT: str = "Size"
CHECKSUM_TAB_TEXT: str = "Checksum"
CALCULATE_TEXT: str = "Calculate"
STOP_TEXT: str = "Stop"
COPY_TEXT: str = "Copy"
NOT_YET_TEXT: str = "Not calculated yet"
CALCULATING_TEXT: str = "Calculating…"
STOPPED_TEXT: str = "Stopped: these are the numbers so far."
AT_LEAST_TEXT: str = "Some folders could not be read in full, so these are the least the selection holds."
SIZE_NOTE_TEXT: str = "The sizes of the files together; a link counts as a file and is never followed."
CHECKSUMS_DONE_TEXT: str = "{done} of {count} files. Copy gives the lines as the *sum tools write them."
CHECKSUMS_SOME_FAILED_TEXT: str = "{done} of {count} files; {failed} could not be done, each says why."
CHECKSUMS_STOPPED_TEXT: str = "Stopped after {done} files."
CHECKSUMS_RUNNING_TEXT: str = "Calculating… {done} files so far."
CHECKSUM_COLUMNS: tuple[str, str] = ("Name", "Checksum")
Stop = Callable[[], None]  # stops a calculation that runs; harmless once it ended


@dataclass(frozen=True)
class Calculator:
    """What the Size and Checksum tabs ask of the window (FE-02). Each start runs away from the window and returns how to stop it; every
    answer comes on the window's thread: each item as it is done, then done (False when stopped) or failed with a calm text."""

    count: Callable[[Callable[[str, Count], None], Callable[[bool], None], Callable[[str], None]], Stop]
    checksums: Callable[[str, Callable[[Checksum], None], Callable[[bool], None], Callable[[str], None], Callable[[str], None]], Stop]  # the last: a note on the way
    algorithms: tuple[str, ...]  # what this side computes, in the order shown
    algorithm: str  # chosen first: the last one the person chose, where this side offers it
    checksum_why: str  # why no checksum can be calculated here, for the hover text (OR-20); empty when it can
    chosen: Callable[[str], None]  # the algorithm the person calculated with, remembered for next time


@dataclass(frozen=True)
class Abilities:
    """What may be changed, and why not where it can't: each reason becomes the hover text of what is greyed (OR-20)."""

    owner: bool = False
    owner_why: str = ""
    group: bool = False
    group_why: str = ""
    rights: bool = False
    rights_why: str = ""
    inside: bool = False  # everything inside the folders, in one go
    inside_shown: bool = True  # False on FTP, where it needs a shell and is not shown (D-027)
    inside_why: str = ""
    owners: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    names_known: bool = False  # the lists are the system's own; still only suggestions: any name of valid form may be typed
    whole_rights: bool = False  # no shell and a file's rights unknown: only a complete rights value can be set


@dataclass(frozen=True)
class Change:
    owner: str  # empty: as it is
    group: str
    rights: Rights | None  # None: as they are
    inside: bool
    folder_readers: str  # with everything inside: the folders get Run for these classes (as ug); empty: none


def _when(moment: float | None) -> str:
    if moment is None:
        return UNKNOWN_TEXT
    try:
        return datetime.fromtimestamp(moment).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return UNKNOWN_TEXT  # a date from the server is input (OR-15)


def size_text(size: int | None) -> str:
    if size is None:
        return UNKNOWN_TEXT
    return f"{size:,} bytes".replace(",", " ")


def count_text(size: int) -> str:
    """The total size as bytes and, from a kilobyte on, short as well: 1 234 567 bytes (1.2 MB)."""
    return size_text(size) + (f" ({short_size(size)})" if size >= 1024 else "")


def checksum_lines(rows: list[tuple[str, str]]) -> str:
    """Lines as the *sum tools write them, "<checksum>  <name>", so the tools can check them again."""
    return "".join(f"{value}  {name}\n" for name, value in rows)


class SizeTab(QWidget):
    """Total size, files and folders of the selection, calculated on request and stoppable (FE-02)."""

    def __init__(self, calculator: Calculator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.calculator: Calculator = calculator
        self.total: Count = Count()
        self.stop: Stop | None = None  # the calculation that runs now
        self.size_value = QLabel(NOT_YET_TEXT)
        self.files = QLabel("")
        self.folders = QLabel("")
        for label in (self.size_value, self.files, self.folders):
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.note = QLabel(SIZE_NOTE_TEXT)
        self.note.setWordWrap(True)
        self.calculate = QPushButton(CALCULATE_TEXT)
        self.calculate.setAutoDefault(False)  # Enter in the window applies it, never a calculation
        form = QFormLayout()
        form.addRow("Size", self.size_value)
        form.addRow("Files", self.files)
        form.addRow("Folders", self.folders)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.calculate)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.note)
        layout.addStretch(1)
        layout.addLayout(row)
        self.calculate.clicked.connect(self._pressed)

    def _pressed(self) -> None:
        if self.stop is not None:
            self.stop()
            return
        self.total = Count()
        self.size_value.setText(CALCULATING_TEXT)
        self.files.setText("")
        self.folders.setText("")
        self.note.setText(SIZE_NOTE_TEXT)
        self.calculate.setText(STOP_TEXT)
        self.stop = self.calculator.count(self._counted, self._done, self._failed)

    def _show(self) -> None:
        self.size_value.setText(count_text(self.total.size))
        self.files.setText(f"{self.total.files:,}".replace(",", " "))
        self.folders.setText(f"{self.total.folders:,}".replace(",", " "))

    def _counted(self, _name: str, found: Count) -> None:
        self.total = self.total.plus(found)
        self._show()

    def _ended(self) -> None:
        self.stop = None
        self.calculate.setText(CALCULATE_TEXT)

    def _done(self, finished: bool) -> None:
        self._ended()
        self._show()
        self.note.setText(STOPPED_TEXT if not finished else SIZE_NOTE_TEXT if self.total.complete else AT_LEAST_TEXT)

    def _failed(self, text: str) -> None:
        self._ended()
        self.size_value.setText(UNKNOWN_TEXT)
        self.note.setText(text)

    def end(self) -> None:
        if self.stop is not None:
            self.stop()


class ChecksumTab(QWidget):
    """The checksums of the selected files and of the files inside the selected folders, by the algorithm the person chooses (FE-02)."""

    def __init__(self, calculator: Calculator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.calculator: Calculator = calculator
        self.stop: Stop | None = None
        self.note: str = ""  # said on the way, while the checksums run
        self.done_count: int = 0
        self.failed_count: int = 0
        self.algorithm = QComboBox()
        self.algorithm.addItems(list(calculator.algorithms))
        if calculator.algorithm in calculator.algorithms:
            self.algorithm.setCurrentText(calculator.algorithm)
        self.calculate = QPushButton(CALCULATE_TEXT)
        self.calculate.setAutoDefault(False)
        usable = bool(calculator.algorithms) and not calculator.checksum_why
        for widget in (self.algorithm, self.calculate):
            widget.setEnabled(usable)  # greyed with the reason, never hidden (OR-20)
            if not usable:
                widget.setToolTip(calculator.checksum_why)
        self.rows = QTreeWidget()
        self.rows.setColumnCount(2)
        self.rows.setHeaderLabels(list(CHECKSUM_COLUMNS))
        self.rows.setRootIsDecorated(False)
        self.rows.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.rows.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.rows.installEventFilter(self)
        self.status = QLabel(calculator.checksum_why)
        self.status.setWordWrap(True)
        self.copy = QPushButton(COPY_TEXT)
        self.copy.setAutoDefault(False)
        self.copy.setEnabled(False)
        top = QHBoxLayout()
        top.addWidget(QLabel("Algorithm"))
        top.addWidget(self.algorithm, 1)
        top.addWidget(self.calculate)
        bottom = QHBoxLayout()
        bottom.addWidget(self.status, 1)
        bottom.addWidget(self.copy)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.rows, 1)
        layout.addLayout(bottom)
        self.calculate.clicked.connect(self._pressed)
        self.copy.clicked.connect(self.copy_rows)
        self.algorithm.currentTextChanged.connect(self._algorithm_changed)

    def _algorithm_changed(self, _text: str) -> None:
        if self.stop is None:
            self.rows.clear()  # checksums of another algorithm are never shown under this one
            self.copy.setEnabled(False)
            self.status.setText("")

    def _pressed(self) -> None:
        if self.stop is not None:
            self.stop()
            return
        algorithm = self.algorithm.currentText()
        self.rows.clear()
        self.done_count = self.failed_count = 0
        self.note = ""
        self.copy.setEnabled(False)
        self.status.setText(CALCULATING_TEXT)
        self.calculate.setText(STOP_TEXT)
        self.algorithm.setEnabled(False)
        self.calculator.chosen(algorithm)
        self.stop = self.calculator.checksums(algorithm, self._arrived, self._done, self._failed, self._told)

    def _told(self, text: str) -> None:
        self.note = text
        self.status.setText(text)

    def _arrived(self, found: Checksum) -> None:
        item = QTreeWidgetItem([shown_name(found.path), found.value or found.problem])
        item.setData(0, Qt.ItemDataRole.UserRole, found.value)
        if not found.value:
            item.setToolTip(1, found.problem)
            self.failed_count += 1
        else:
            self.done_count += 1
            self.copy.setEnabled(True)
        self.rows.addTopLevelItem(item)
        self.status.setText((self.note + " " if self.note else "") + CHECKSUMS_RUNNING_TEXT.format(done=self.done_count + self.failed_count))

    def _ended(self) -> None:
        self.stop = None
        self.calculate.setText(CALCULATE_TEXT)
        self.algorithm.setEnabled(True)
        self.rows.resizeColumnToContents(0)

    def _done(self, finished: bool) -> None:
        self._ended()
        count = self.done_count + self.failed_count
        if not finished:
            self.status.setText(CHECKSUMS_STOPPED_TEXT.format(done=count))
        elif self.failed_count:
            self.status.setText(CHECKSUMS_SOME_FAILED_TEXT.format(done=self.done_count, count=count, failed=self.failed_count))
        else:
            self.status.setText(CHECKSUMS_DONE_TEXT.format(done=self.done_count, count=count))

    def _failed(self, text: str) -> None:
        self._ended()
        self.status.setText(text)

    def copy_rows(self) -> None:
        """The selected rows, or every row when none is selected, that have a checksum."""
        items = self.rows.selectedItems() or [item for number in range(self.rows.topLevelItemCount()) if (item := self.rows.topLevelItem(number)) is not None]
        rows = [(item.text(0), str(item.data(0, Qt.ItemDataRole.UserRole) or "")) for item in items]
        text = checksum_lines([(name, value) for name, value in rows if value])
        if text:
            QGuiApplication.clipboard().setText(text)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Ctrl+C in the list copies, as Copy does."""
        if watched is self.rows and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent) and event.matches(QKeySequence.StandardKey.Copy):
            self.copy_rows()
            return True
        return super().eventFilter(watched, event)

    def end(self) -> None:
        if self.stop is not None:
            self.stop()


class PropertiesDialog(QDialog):
    def __init__(self, summary: Summary, location: str, abilities: Abilities, parent: QWidget | None = None, calculator: Calculator | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(WINDOW_TITLE)
        self.summary: Summary = summary
        self.abilities: Abilities = abilities
        form = QFormLayout()
        name = shown_name(summary.name) if summary.name is not None else f"{summary.files} files and {summary.folders} folders" if summary.folders and summary.files else f"{summary.count} {'folders' if summary.folders else 'files'}"
        form.addRow("Name", plain_label(name))
        form.addRow("Location", plain_label(shown_name(location)))
        form.addRow("Kind", plain_label(summary.kind))
        if summary.files:
            form.addRow("Size", plain_label(size_text(summary.size) + (" (the files, without what is in the folders)" if summary.folders else "")))
        form.addRow("Modified", plain_label(DIFFERS_TEXT if summary.modified_differs else _when(summary.modified)))
        if summary.links:
            form.addRow("Link target", plain_label(DIFFERS_TEXT if summary.link_target is None else shown_name(summary.link_target)))
        self.owner = self._names_field(summary.owner, abilities.owners, abilities.owner, abilities.owner_why)
        self.group = self._names_field(summary.group, abilities.groups, abilities.group, abilities.group_why)
        form.addRow("Owner", self.owner)
        form.addRow("Group", self.group)
        self.rights = RightsEditor(summary.rights)
        self.rights.setEnabled(abilities.rights)
        if not abilities.rights:
            self.rights.setToolTip(abilities.rights_why)
        form.addRow("Rights", self.rights)
        self.whole_note = QLabel(WHOLE_RIGHTS_NOTE)  # said before OK, not after it
        self.whole_note.setWordWrap(True)
        self.whole_note.setVisible(abilities.whole_rights and abilities.rights)
        form.addRow("", self.whole_note)
        self.inside = QCheckBox(INSIDE_TEXT)
        self.folders_x = QCheckBox(FOLDERS_X_TEXT)
        self.folders_x.setToolTip(FOLDERS_X_TIP)
        self.folders_x.setChecked(False)  # off: a private folder never gains Run for others by a tick on "inside"
        if summary.folders:
            self.inside.setEnabled(abilities.inside)
            if not abilities.inside:
                self.inside.setToolTip(abilities.inside_why)
            form.addRow("", self.inside)
            form.addRow("", self.folders_x)
        self.inside.setVisible(bool(summary.folders) and abilities.inside_shown)
        self.folders_x.setVisible(bool(summary.folders) and abilities.inside_shown)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.tabs = QTabWidget()
        general = QWidget()
        general.setLayout(form)
        self.tabs.addTab(general, GENERAL_TAB_TEXT)
        self.size_tab: SizeTab | None = None
        self.checksum_tab: ChecksumTab | None = None
        if calculator is not None:
            self.size_tab = SizeTab(calculator)
            self.checksum_tab = ChecksumTab(calculator)
            self.tabs.addTab(self.size_tab, SIZE_TAB_TEXT)
            self.tabs.addTab(self.checksum_tab, CHECKSUM_TAB_TEXT)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        self.owner.currentTextChanged.connect(self._update)
        self.group.currentTextChanged.connect(self._update)
        self.rights.changed.connect(self._update)
        self.inside.toggled.connect(self._update)
        self.folders_x.toggled.connect(self._update)
        for field in (self.owner, self.owner.lineEdit(), self.group, self.group.lineEdit(), self.rights.octal):
            if field is not None:
                field.installEventFilter(self)
        self._update()

    def _names_field(self, now: str | None, names: tuple[str, ...], usable: bool, why: str) -> QComboBox:
        field = QComboBox()
        field.setEditable(True)
        field.addItems([shown_name(name) for name in names])
        field.setCurrentText("" if now is None else shown_name(now))
        line = field.lineEdit()
        if line is not None:
            line.setPlaceholderText(DIFFERS_TEXT if now is None else "")
        field.setEnabled(usable)
        if not usable:
            field.setToolTip(why)
        return field

    def _typed(self, field: QComboBox, now: str | None) -> str:
        """The name typed or chosen, when it differs from what the files have; empty when it is as it was. The field shows a name as
        the screen can (shown_name), so it is compared with that: a name that is not valid UTF-8 and left as it is keeps its real
        bytes, as nothing is sent for it."""
        text = field.currentText().strip()
        return "" if not text or text == shown_name(now or "") or not field.isEnabled() else text

    def _name_problem(self, name: str, known: tuple[str, ...], what: str) -> str:
        """The lists are suggestions: any name of valid form goes, and the server or this computer refuses an unknown one with its own
        reason. A name with a character the screen could not show is never sent."""
        if not name:
            return ""
        if not properties.name_allowed(name) or "\ufffd" in name:
            return NOT_A_NAME_TEXT.format(name=name, what=what)
        return ""

    def _folder_readers(self) -> str:
        """The classes whose Read box the person switched on in this change: with "give folders Run", the folders inside get Run for
        these, and for no other class."""
        inside = self.inside.isChecked() and self.inside.isEnabled()
        touched = self._touched()
        return rights.readers(touched) if inside and self.folders_x.isChecked() and touched is not None else ""

    def _touched(self) -> Rights | None:
        """Only the boxes the person changed; every other box says leave, for one file as for many, and for everything inside."""
        if not self.rights.isEnabled():
            return None
        touched = tuple(now if now != before else None for now, before in zip(self.rights.value(), self.rights.start, strict=True))
        return touched if any(value is not None for value in touched) else None

    def _update(self) -> None:
        owner = self._typed(self.owner, self.summary.owner)
        group = self._typed(self.group, self.summary.group)
        problem = self._name_problem(owner, self.abilities.owners, "user") or self._name_problem(group, self.abilities.groups, "group")
        touched = self._touched()
        if not problem and touched is not None and self.abilities.whole_rights and not rights.complete(self.rights.value()):
            problem = WHOLE_RIGHTS_TEXT
        self.problem.setText(problem)
        self.problem.setVisible(bool(problem))
        self.folders_x.setEnabled(self.inside.isChecked() and self.rights.isEnabled())
        changed = bool(owner or group or self._touched() is not None or self._folder_readers())
        button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if button is not None:
            button.setEnabled(changed and not problem and (not self.rights.isEnabled() or self.rights.is_valid()))

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Enter in a name field or the octal field confirms that field and moves on; only Enter elsewhere, or OK, applies the window."""
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent) and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.focusNextChild()
            return True
        return super().eventFilter(watched, event)

    @override
    def done(self, result: int) -> None:
        """A calculation that still runs stops with the window."""
        for tab in (self.size_tab, self.checksum_tab):
            if tab is not None:
                tab.end()
        super().done(result)

    def change(self) -> Change:
        """What the person set: only the boxes they touched change, for one file as for several and for everything inside; every
        other box stays as each file has it. Owner or group alone leave the rights as they are."""
        inside = self.inside.isChecked() and self.inside.isEnabled()
        return Change(
            owner=self._typed(self.owner, self.summary.owner),
            group=self._typed(self.group, self.summary.group),
            rights=self._touched(),
            inside=inside,
            folder_readers=self._folder_readers(),
        )
