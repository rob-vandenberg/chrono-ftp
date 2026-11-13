"""The overwrite question (FB-03): a file that is there already, with the size and date of both, and every way to go on."""

from typing import override

from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.connections.base import safe_name, shown_name
from chrono_ftp.transfers.options import KIND_UNKNOWN, NO_CUT, SIZE_UNKNOWN, TEXT_NO_APPEND, Answer, Choice, Conflict
from chrono_ftp.ui.filelist import date_text, size_text
from chrono_ftp.ui.plaintext import box_text, fitting, plain_label

# --- Version ------------------------------------------------------------
__version__ = "overwritedialog.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Append is greyed for a text file whose line endings are converted, and says why (FD-02)
# v0.0.12: Initial: Yes, No, Yes to all, No to all, Only if newer, Rename, Append, Resume and Stop, with size and date of both files;
#          closing it or Esc is Stop, and Stop says that it stops all this upload or download still has to do; the answers for all
#          say they hold for this upload or this download
#          names are shown as written, never read as HTML
#          long names are fitted to the screen, Append is offered only where a broken-off append can be set back, and says what happens
#          when the connection is lost
#          No is the default and has the focus, so Enter and Space never replace a file; a greyed Append names its real reason; a size the
#          server never gave is unknown, not 0 bytes, also in Resume's hover text; it names the connection it is about; a Rename without a
#          new name says why nothing happened; the name typed is taken exactly as it is

WINDOW_TITLE: str = "File already there"
STOP_TEXT: str = (
    "Stop everything this upload or download still has to do, also the other items you chose with it. What arrived so far stays."
    " Closing this window or pressing Esc does the same."
)
NOT_A_NAME_TEXT: str = "A name can't hold a slash."
NO_NEW_NAME_TEXT: str = "No name was given, so nothing was renamed. Choose again."
SAME_NAME_TEXT: str = "That is the name it has already, so nothing was renamed. Give another name, or choose again."
APPEND_TEXT: str = (
    "Add the new one to the end of the one that is there. If that breaks off, the file gets its old length back; if the connection to"
    " the server is lost meanwhile, the file is left as it is, and you are told what it holds once the connection is back."
)
NO_APPEND_TEXT: str = "This server can't cut a file back, so an append that breaks off could not be undone: Append is not offered here."
NO_SIZE_APPEND_TEXT: str = "The size of the file on the server is unknown, so an append that breaks off could not be undone: Append is not offered here."
NO_KIND_APPEND_TEXT: str = "Whether this is a file or a folder on the server can't be seen, so nothing is appended to it: Append is not offered here."
NO_TEXT_APPEND_TEXT: str = "This file goes as text, with its line endings converted, so it always goes whole: Append is not offered here."
NO_APPEND_TEXTS: dict[str, str] = {NO_CUT: NO_APPEND_TEXT, SIZE_UNKNOWN: NO_SIZE_APPEND_TEXT, KIND_UNKNOWN: NO_KIND_APPEND_TEXT, TEXT_NO_APPEND: NO_TEXT_APPEND_TEXT}
SIZE_UNKNOWN_TEXT: str = "size unknown"
NO_PART_TEXT: str = "There is no unfinished copy of this file that can be continued."
CHOICES: tuple[tuple[Choice, str, str], ...] = (
    (Choice.YES, "Yes", "Replace it with the new one"),
    (Choice.NO, "No", "Leave it as it is and go on with the next file"),
    (Choice.YES_TO_ALL, "Yes to all", "Replace this one and every other file of this upload, or of this download, that is there already"),
    (Choice.NO_TO_ALL, "No to all", "Leave this one and every other file of this upload, or of this download, that is there already"),
    (Choice.ONLY_IF_NEWER, "Only if newer", "Replace this one and the others of this upload, or of this download, only where the new one is newer"),
    (Choice.RENAME, "Rename…", "Give the new one another name, so both are kept"),
    (Choice.APPEND, "Append", APPEND_TEXT),
    (Choice.RESUME, "Resume", "Continue the unfinished copy instead of starting again"),
    (Choice.STOP, "Stop", STOP_TEXT),
)


def _details(size: int, modified: float | None, size_known: bool = True) -> str:
    """A size the server never gave is said to be unknown: 0 bytes would be a guess."""
    how_big = size_text(size) if size_known else SIZE_UNKNOWN_TEXT
    when = date_text(modified)
    return f"{how_big}, {when}" if when else how_big


class OverwriteDialog(QDialog):
    """Closing it (its title bar or Esc) is Stop: nothing is replaced that the person did not choose."""

    def __init__(self, conflict: Conflict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.conflict: Conflict = conflict
        self.answer: Answer = Answer(Choice.STOP)
        self.setWindowTitle(WINDOW_TITLE)
        text = plain_label()
        connection = fitting(conflict.connection, text) if conflict.connection else ""
        if conflict.upload:
            place = f"on the server of {connection}" if connection else "on the server"
        else:
            place = f"on this computer, for the download from {connection}" if connection else "on this computer"
        text.setText(f"{fitting(shown_name(conflict.name), text)} is already in {fitting(shown_name(conflict.folder), text)} {place}.")  # stays on the screen
        text.setToolTip(box_text(f"{shown_name(conflict.name)}\n{shown_name(conflict.folder)}\n{conflict.connection}".rstrip()))
        self.text: QLabel = text
        text.setWordWrap(True)
        grid = QGridLayout()
        grid.addWidget(QLabel("New:"), 0, 0)
        grid.addWidget(plain_label(_details(conflict.source_size, conflict.source_modified, conflict.source_size_known)), 0, 1)
        grid.addWidget(QLabel("There now:"), 1, 0)
        grid.addWidget(plain_label(_details(conflict.target_size, conflict.target_modified, conflict.target_size_known)), 1, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(grid)
        layout.addWidget(QLabel("Replace it?"))
        self.buttons: dict[Choice, QPushButton] = {}
        rows = (CHOICES[:5], CHOICES[5:])
        for row in rows:
            line = QHBoxLayout()
            for choice, label, hover in row:
                button = QPushButton(label)
                button.setToolTip(hover)
                button.clicked.connect(lambda _checked=False, choice=choice: self._chose(choice))
                line.addWidget(button)
                self.buttons[choice] = button
            line.addStretch(1)
            layout.addLayout(line)
        resume = self.buttons[Choice.RESUME]
        if conflict.part_size is None:
            resume.setEnabled(False)
            resume.setToolTip(NO_PART_TEXT)
        else:
            whole = size_text(conflict.source_size) if conflict.source_size_known else "a file of unknown size"
            resume.setToolTip(f"Continue the unfinished copy: {size_text(conflict.part_size)} of {whole} is there already")
        if not conflict.append_possible:
            self.buttons[Choice.APPEND].setEnabled(False)
            self.buttons[Choice.APPEND].setToolTip(NO_APPEND_TEXTS.get(conflict.no_append, NO_APPEND_TEXT))
        safe = self.buttons[Choice.NO]
        safe.setDefault(True)  # Enter leaves the file as it is
        safe.setFocus()  # and so does Space: no key ever replaces a file without a choice

    @override
    def reject(self) -> None:
        """The title bar's close and Esc both end here: the same as the Stop button."""
        self.answer = Answer(Choice.STOP)
        super().reject()

    def _chose(self, choice: Choice) -> None:
        if choice == Choice.RENAME:
            name = self._new_name()
            if name is None:
                return  # the question stays: nothing was chosen yet
            self.answer = Answer(choice, name)
        else:
            self.answer = Answer(choice)
        self.accept()

    def _new_name(self) -> str | None:
        text, given = QInputDialog.getText(self, "Rename", "Name for the new file", QLineEdit.EchoMode.Normal, shown_name(self.conflict.name))
        name = text  # exactly as typed: a name may start or end with a space
        if not given:
            return None  # Cancel: the question is still open, as the person left it
        if not name or name in (self.conflict.name, shown_name(self.conflict.name)):
            QMessageBox.information(self, "Rename", box_text(NO_NEW_NAME_TEXT if not name else SAME_NAME_TEXT))
            return None
        if not safe_name(name):
            QMessageBox.information(self, "Rename", box_text(NOT_A_NAME_TEXT))
            return None
        return name
