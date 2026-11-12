"""The Properties window (FM-01 to FM-04), on both sides and for several files at once: what they are, and their owner, group and
rights, each changeable where the connection or this computer allows it. A field where the files differ says so."""

from dataclasses import dataclass
from datetime import datetime
from typing import override

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.engine import properties, rights
from chrono_ftp.engine.properties import Summary
from chrono_ftp.engine.rights import Rights
from chrono_ftp.ui.plaintext import plain_label
from chrono_ftp.ui.rightseditor import RightsEditor

# --- Version ------------------------------------------------------------
__version__ = "propertiesdialog.py 0.0.37"

# --- Version history ----------------------------------------------------
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


class PropertiesDialog(QDialog):
    def __init__(self, summary: Summary, location: str, abilities: Abilities, parent: QWidget | None = None) -> None:
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
        layout = QVBoxLayout(self)
        layout.addLayout(form)
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
