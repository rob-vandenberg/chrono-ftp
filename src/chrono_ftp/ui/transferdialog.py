"""The transfer window (FB-09): before an upload, a download or a drop, the target folder, the options in one line, Transfer settings,
the tick that makes it a move (FB-08), and the tick that stops this window from coming."""

import os
from collections.abc import Callable
from dataclasses import replace

from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.connections.base import normalized, shown_name
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.ui.transferoptionsform import TransferOptionsDialog, summary_text
from chrono_ftp.ui.plaintext import box_text, fitting, plain_label

# --- Version ------------------------------------------------------------
__version__ = "transferdialog.py 0.0.36"

# --- Version history ----------------------------------------------------
# v0.0.36: The question about a missing local folder can be given its title: the main window's title holds the version (D-059)
# v0.0.12: Initial: the target folder with Browse, the options in one line, Transfer settings, move, and never show again (FB-09, FB-08);
#          ~ is the home folder, a relative local path is refused, Browse starts at the real folder; a server folder is taken in its one
#          form; names are shown as plain text
#          a long name is fitted to the screen
#          a download target that is a file says so; the move's hover text says only what is checked where the server can't put a file
#          on its disk on request; a download folder that isn't on this computer yet is offered to be made right here; the problem line
#          goes as soon as the folder changes; the folder agreed to is made only when the transfer starts; the move's hover text says
#          exactly what is checked; a corrected folder is taken as typed, an untouched one is the folder shown; a download folder
#          that can't be written is said so up front

WIDTH: int = 560
UPLOAD_TITLE: str = "Upload"
DOWNLOAD_TITLE: str = "Download"
MOVE_UP_TEXT: str = "Each file is removed from this computer once it arrived safely on the server. Folders that end up empty go too."
MOVE_UP_UNFLUSHED_TEXT: str = "Each file is removed from this computer once it arrived on the server at its size and it did not change here meanwhile; this server can't confirm it is on its disk. Folders that end up empty go too."
MOVE_DOWN_TEXT: str = "Each file is removed from the server once it arrived safely on this computer. Folders that end up empty go too."
NEVER_AGAIN_TEXT: str = "Uploads and downloads then start at once, with the settings of Settings, where you can bring this window back."
NO_LOCAL_FOLDER_TEXT: str = "This folder is not on this computer. Please choose one that is."
NO_LOCAL_FOLDER_YET_TEXT: str = "This folder isn't on this computer yet:\n{folder}\n\nMake it?"
NOT_MADE_HERE_TEXT: str = "The folder {folder} could not be made on this computer ({reason}), so nothing was fetched."
NO_REMOTE_FOLDER_TEXT: str = "A folder on the server starts with a slash, such as /home."
LOCAL_FILE_TEXT: str = "This is a file on this computer, not a folder. Please choose a folder."
NOT_WRITABLE_HERE_TEXT: str = "Files can't be saved in this folder on this computer. Please choose another folder."
EMPTY_TEXT: str = "Please give the folder to transfer to."
NOT_WHOLE_PATH_TEXT: str = "Please give the whole path of the folder, starting with / (or ~ for your home folder)."


class TransferDialog(QDialog):
    """browse: picks a target folder starting at the one given, or None when the person cancels."""

    def __init__(self, upload: bool, names: list[str], target: str, options: TransferOptions, reasons: dict[str, str], browse: Callable[[str], str | None], parent: QWidget | None = None, durable: bool = True) -> None:
        super().__init__(parent)
        self.upload: bool = upload
        self.options: TransferOptions = options
        self.reasons: dict[str, str] = reasons
        self.browse: Callable[[str], str | None] = browse
        title = UPLOAD_TITLE if upload else DOWNLOAD_TITLE
        self.setWindowTitle(title)
        self.setMinimumWidth(WIDTH)
        what = shown_name(names[0]) if len(names) == 1 else f"{len(names)} items"
        place = "the server" if upload else "this computer"
        self.what = plain_label()
        self.what.setText(f"{title} {fitting(what, self.what)} to this folder on {place}:")  # a long name never pushes the window off the screen
        self.what.setToolTip(box_text(what))
        self.original_target: str = target  # a name that is not valid UTF-8 can't be shown as it is: unchanged, it keeps its real bytes
        self.target = QLineEdit(shown_name(target))
        self.browse_button = QPushButton("Browse…")
        self.browse_button.setToolTip("Choose the folder on the server" if upload else "Choose the folder on this computer")
        self.summary = plain_label(summary_text(options, upload))
        self.summary.setWordWrap(True)
        self.settings_button = QPushButton("Transfer settings…")
        self.settings_button.setToolTip("The settings for this transfer only; Settings keeps the ones every transfer starts with")
        self.move_tick = QCheckBox("Delete the source once it arrived")
        self.move_tick.setToolTip((MOVE_UP_TEXT if durable else MOVE_UP_UNFLUSHED_TEXT) if upload else MOVE_DOWN_TEXT)  # only what is checked
        self.never_again = QCheckBox("Don't show this window again")
        self.never_again.setToolTip(NEVER_AGAIN_TEXT)
        self.to_make: str = ""  # a download folder here the person agreed to have made: made only when the transfer really starts
        self.problem = plain_label()
        self.problem.setWordWrap(True)
        self.problem.hide()
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(title)  # the button names the one action it does (OR-20)
        target_row = QHBoxLayout()
        target_row.addWidget(self.target, 1)
        target_row.addWidget(self.browse_button)
        settings_row = QHBoxLayout()
        settings_row.addWidget(self.summary, 1)
        settings_row.addWidget(self.settings_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.what)
        layout.addLayout(target_row)
        layout.addLayout(settings_row)
        layout.addWidget(self.move_tick)
        layout.addWidget(self.problem)
        layout.addWidget(self.never_again)
        layout.addWidget(self.buttons)
        self.browse_button.clicked.connect(self._browse)
        self.target.textChanged.connect(self.problem.hide)  # typed or browsed: what was said of the folder before no longer holds
        self.settings_button.clicked.connect(self._settings)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)

    def _browse(self) -> None:
        chosen = self.browse(self.folder())  # the real folder, also when its name is not valid UTF-8
        if chosen:
            self.original_target = chosen
            self.target.setText(shown_name(chosen))

    def _settings(self) -> None:
        dialog = TransferOptionsDialog(self.options, self.reasons, self)
        if dialog.exec() == QDialog.DialogCode.Accepted.value:
            self.options = dialog.form.chosen()
            self.summary.setText(summary_text(self.options, self.upload))
        dialog.deleteLater()

    def _problem_with(self, folder: str) -> str:
        if not folder:
            return EMPTY_TEXT
        if self.upload:
            return "" if folder.startswith("/") else NO_REMOTE_FOLDER_TEXT
        if not os.path.isabs(folder):
            return NOT_WHOLE_PATH_TEXT
        if os.path.exists(folder) and not os.path.isdir(folder):
            return LOCAL_FILE_TEXT
        if not os.path.isdir(folder):
            return NO_LOCAL_FOLDER_TEXT
        return "" if os.access(folder, os.W_OK | os.X_OK) else NOT_WRITABLE_HERE_TEXT  # said here, before anything starts

    def _accept(self) -> None:
        folder = self.folder()
        problem = self._problem_with(folder)
        self.to_make = ""
        if problem == NO_LOCAL_FOLDER_TEXT and agreed_to_make(self, folder):  # offered right here, as a folder that isn't on the server yet
            problem, self.to_make = "", folder
        if problem:
            self.problem.setText(problem)
            self.problem.show()
            self.target.setFocus()
            return
        self.accept()

    def folder(self) -> str:
        """The folder as typed or chosen; on this computer ~ stands for the home folder, as everywhere else. Left exactly as shown, it is
        the folder it stands for, with its real bytes (a name that is not valid UTF-8 is shown changed); any change is taken as typed."""
        text = self.target.text()
        folder = self.original_target if text == shown_name(self.original_target) else text.strip()
        if self.upload:
            return normalized(folder) if folder.startswith("/") else folder  # /a/, /a// and /a/. are /a, as the list on the right says
        return os.path.expanduser(folder)

    def chosen_options(self) -> TransferOptions:
        return replace(self.options, delete_source=self.move_tick.isChecked())


def agreed_to_make(parent: QWidget, folder: str, title: str = "") -> bool:
    """A folder on this computer that isn't there yet is offered to be made. It is made only when the transfer really starts (made_now),
    so a transfer that never starts leaves no empty folder behind."""
    shown = fitting(shown_name(folder), parent)
    return QMessageBox.question(parent, title or parent.windowTitle(), box_text(NO_LOCAL_FOLDER_YET_TEXT.format(folder=shown))) == QMessageBox.StandardButton.Yes


def made_now(parent: QWidget, folder: str) -> str:
    """The folder the person agreed to have made, made as the transfer starts: what stands in the way, as the text to show; empty once it
    is there."""
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as error:
        return NOT_MADE_HERE_TEXT.format(folder=fitting(shown_name(folder), parent), reason=error.strerror or str(error))
    return ""
