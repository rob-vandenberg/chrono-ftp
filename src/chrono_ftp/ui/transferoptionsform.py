"""The transfer options as the person sets them (FB-01): in the Transfer settings window of one transfer, and as the defaults in Settings.
An option the connection can't do is greyed out and says why (OR-7, OR-20); a rights value that is no octal number can't be typed (FB-06)."""

from dataclasses import replace

from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QTabWidget, QVBoxLayout, QWidget

from chrono_ftp.core.transferoptions import AUTOMATIC, BINARY, CASE_FIRST_UPPER, CASE_LOWER, CASE_UNCHANGED, CASE_UPPER, RESUME_OFF, RESUME_ON, RESUME_SMART, RIGHTS_TEXT_PATTERN, TEXT, TransferOptions, rights_valid
from chrono_ftp.transfers.options import KEEP_TIMESTAMPS, RESUME, SET_RIGHTS, SPLIT, TEXT_WHOLE_TEXT
from chrono_ftp.transfers.split import MOST_PARTS, SPLIT_BYTES
from chrono_ftp.ui.maskfield import MaskField

# --- Version ------------------------------------------------------------
__version__ = "transferoptionsform.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Masks (block FD): a Files tab with the file mask, skip hidden files and skip empty folders (FD-01); a Text and names tab with
#          binary, text or automatic by a text mask, removing the BOM (FD-02), the case of names and replacing characters (FD-03); in
#          the text mode resume and splitting are greyed with the reason; a mask with a fault keeps OK greyed and says where, the text
#          mask only while it is in use (Automatic)
# v0.0.13: Split large files over several connections (FC-05), greyed where the connection can't, with why; the summary says it holds
#          for the file by file way only
# v0.0.12: Initial: resume, the overwrite question, only newer, dates, rights on upload and count first, greyed by what the connection can do;
#          the rights texts say that a folder that is there already keeps its own

SETTINGS_TITLE: str = "Transfer settings"
RESUME_LABELS: dict[str, str] = {RESUME_SMART: "Smart: files of 100 KB or more", RESUME_ON: "Always", RESUME_OFF: "Never"}
RESUME_TEXT: str = "A stopped or broken off transfer keeps what arrived; the next transfer of the same file continues from there."
ASK_TEXT: str = "Each file that is there already is shown with its size and date, and you choose what happens."
NEWER_TEXT: str = "A file is left as it is when the one already there is not older."
DATES_TEXT: str = "Each file and folder gets the date and time of its original, instead of the moment it arrived."
RIGHTS_TEXT: str = "Uploaded files, and folders the upload makes, get these rights, written as an octal number such as 644. A folder that is there already keeps its own."
RIGHTS_FIRST_TEXT: str = "Tick “Set the rights of uploaded files” first."
ADD_X_TEXT: str = "A folder the upload makes also gets x wherever it has r, so it can be opened."
IGNORE_TEXT: str = "When the server refuses the rights, the file still counts as sent."
RIGHTS_INCOMPLETE_TEXT: str = "Rights are three or four octal digits, such as 644."
COUNT_TEXT: str = "The size of everything is counted before the transfer starts, so the queue can show the percentage and the time left."
SPLIT_LABEL: str = "Split large files over several connections"
SPLIT_SUMMARY: str = "Split large files (file by file only)"  # rsync sends each file in one piece
MASK_TEXT: str = "Only the files and folders that fit this mask go; empty: everything. Hover over the field for the syntax."
SKIP_HIDDEN_TEXT: str = "Files and folders whose names start with a dot stay where they are."
SKIP_EMPTY_TEXT: str = "A folder that holds no file that goes is not made on the other side."
MODE_LABELS: dict[str, str] = {BINARY: "Binary: every file byte for byte", TEXT: "Text: every file is text", AUTOMATIC: "Automatic: the text files below are text"}
MODE_TEXT: str = ("A text file arrives with the line endings of the side it goes to (one LF per line, as Linux and macOS write them); "
                  "every other byte stays as it is. A text file always goes whole: it is never continued, split or appended. Binary changes nothing.")
TEXT_MASK_TEXT: str = "The files that go as text in the automatic mode."
TEXT_MASK_FIRST_TEXT: str = "Choose Automatic first."
BOM_TEXT: str = "A text file loses the mark some editors put at its very start (the UTF-8 BOM)."
BOM_FIRST_TEXT: str = "Choose Text or Automatic first."
CASE_LABELS: dict[str, str] = {CASE_UNCHANGED: "Unchanged", CASE_LOWER: "lower case", CASE_UPPER: "UPPER CASE", CASE_FIRST_UPPER: "First letter upper case"}
CASE_TEXT: str = "The names files and folders get on the other side. A name that is there already is asked about, as always."
REPLACE_TEXT: str = "The characters \\ : * ? \" < > | and control characters become %XX, such as %3A for a colon, so the name fits on systems that can't hold them (Windows, a memory stick)."
MASK_INCOMPLETE_TEXT: str = "Please correct the mask first: its fault is said under it."
SPLIT_TEXT: str = (f"A file of {SPLIT_BYTES // (1024 * 1024)} MB or more goes over up to {MOST_PARTS} connections at once, as far as “Transfers at once” "
                   "and the server allow; it stays one item in the queue. Many servers limit their connections, so this is off unless you "
                   "choose it. Where rsync sends the files, each file goes in one piece.")


def summary_text(options: TransferOptions, upload: bool) -> str:
    """The options in one line, as the transfer window shows them."""
    parts = [{RESUME_SMART: "Resume large files", RESUME_ON: "Resume every file", RESUME_OFF: "No resume"}[options.resume]]
    parts.append("Ask before replacing" if options.ask_before_replacing else "Replace without asking")
    if options.only_newer:
        parts.append("Only new and updated")
    parts.append("Keep dates" if options.keep_timestamps else "New dates")
    if options.file_mask.strip():
        parts.append(f"Mask {options.file_mask.strip()}")
    if options.skip_hidden:
        parts.append("No hidden files")
    if options.skip_empty_folders:
        parts.append("No empty folders")
    if options.transfer_mode != BINARY:
        parts.append("Text files converted" if options.transfer_mode == TEXT else "Text by mask")
    if options.name_case != CASE_UNCHANGED:
        parts.append(f"Names {CASE_LABELS[options.name_case]}")
    if options.replace_characters:
        parts.append("Odd characters replaced")
    if upload and options.set_rights:
        parts.append(f"Rights {options.rights}")
    if options.count_first:
        parts.append("Count first")
    if options.split_large:
        parts.append(SPLIT_SUMMARY)
    return ", ".join(parts)


class TransferOptionsForm(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None, history: tuple[str, ...] = ()) -> None:
        """history: the masks typed before, offered in the mask fields."""
        super().__init__(parent)
        self.kept: TransferOptions = TransferOptions()  # what an option that is greyed out or not valid keeps
        self.reasons: dict[str, str] = {}
        self.resume = QComboBox()
        for mode, label in RESUME_LABELS.items():
            self.resume.addItem(label, mode)
        self.resume.setToolTip(RESUME_TEXT)
        self.ask = QCheckBox("Ask before replacing a file")
        self.ask.setToolTip(ASK_TEXT)
        self.only_newer = QCheckBox("Only new and updated files")
        self.only_newer.setToolTip(NEWER_TEXT)
        self.keep_timestamps = QCheckBox("Keep the date and time of each file")
        self.keep_timestamps.setToolTip(DATES_TEXT)
        self.set_rights = QCheckBox("Set the rights of uploaded files")
        self.set_rights.setToolTip(RIGHTS_TEXT)
        self.rights = QLineEdit()
        self.rights.setValidator(QRegularExpressionValidator(QRegularExpression(RIGHTS_TEXT_PATTERN.replace("{3,4}", "{0,4}")), self.rights))
        self.rights.setMaxLength(4)
        self.rights.setPlaceholderText("644")
        self.add_x = QCheckBox("Add x to folders")
        self.add_x.setToolTip(ADD_X_TEXT)
        self.ignore_errors = QCheckBox("Ignore permission errors")
        self.ignore_errors.setToolTip(IGNORE_TEXT)
        self.count_first = QCheckBox("Count the total size first")
        self.count_first.setToolTip(COUNT_TEXT)
        self.split_large = QCheckBox(SPLIT_LABEL)
        self.split_large.setToolTip(SPLIT_TEXT)
        rights_row = QHBoxLayout()
        rights_row.addWidget(self.rights)
        rights_row.addWidget(self.add_x)
        rights_row.addWidget(self.ignore_errors)
        rights_row.addStretch(1)
        self.file_mask = MaskField(history, "*.txt | *.bak")
        self.file_mask.setToolTip(MASK_TEXT)
        self.skip_hidden = QCheckBox("Skip hidden files")
        self.skip_hidden.setToolTip(SKIP_HIDDEN_TEXT)
        self.skip_empty_folders = QCheckBox("Skip empty folders")
        self.skip_empty_folders.setToolTip(SKIP_EMPTY_TEXT)
        self.mode = QComboBox()
        for mode, label in MODE_LABELS.items():
            self.mode.addItem(label, mode)
        self.mode.setToolTip(MODE_TEXT)
        self.text_mask = MaskField(history)
        self.remove_bom = QCheckBox("Remove the BOM of text files")
        self.name_case = QComboBox()
        for case, label in CASE_LABELS.items():
            self.name_case.addItem(label, case)
        self.name_case.setToolTip(CASE_TEXT)
        self.replace_characters = QCheckBox("Replace characters other systems can't hold")
        self.replace_characters.setToolTip(REPLACE_TEXT)
        self.resume_label = QLabel("Continue an unfinished transfer")
        self.tabs = QTabWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.tabs)
        transfer_page = QWidget()
        form = QFormLayout(transfer_page)
        form.addRow(self.resume_label, self.resume)
        form.addRow("", self.ask)
        form.addRow("", self.only_newer)
        form.addRow("", self.keep_timestamps)
        form.addRow("", self.set_rights)
        form.addRow("Rights", rights_row)
        form.addRow("", self.count_first)
        form.addRow("", self.split_large)
        files_page = QWidget()
        files = QFormLayout(files_page)
        files.addRow("File mask", self.file_mask)
        files.addRow("", self.skip_hidden)
        files.addRow("", self.skip_empty_folders)
        text_page = QWidget()
        text = QFormLayout(text_page)
        text.addRow("Transfer mode", self.mode)
        text.addRow("Text files", self.text_mask)
        text.addRow("", self.remove_bom)
        text.addRow("Names", self.name_case)
        text.addRow("", self.replace_characters)
        self.tabs.addTab(transfer_page, "Transfer")
        self.tabs.addTab(files_page, "Files")
        self.tabs.addTab(text_page, "Text and names")
        for box in (self.ask, self.only_newer, self.keep_timestamps, self.set_rights, self.add_x, self.ignore_errors, self.count_first, self.split_large, self.skip_hidden, self.skip_empty_folders, self.remove_bom, self.replace_characters):
            box.toggled.connect(self._changed)
        self.resume.currentIndexChanged.connect(self._changed)
        self.mode.currentIndexChanged.connect(self._changed)
        self.name_case.currentIndexChanged.connect(self._changed)
        self.rights.textChanged.connect(self._changed)
        self.file_mask.changed.connect(self._changed)
        self.text_mask.changed.connect(self._changed)

    def set_options(self, options: TransferOptions, reasons: dict[str, str] | None = None) -> None:
        """reasons: the options this connection can't do, and why; they are shown off and greyed out."""
        self.kept = options
        self.reasons = reasons or {}
        self.resume.setCurrentIndex(self.resume.findData(options.resume))
        self.ask.setChecked(options.ask_before_replacing)
        self.only_newer.setChecked(options.only_newer)
        self.keep_timestamps.setChecked(options.keep_timestamps)
        self.set_rights.setChecked(options.set_rights)
        self.rights.setText(options.rights)
        self.add_x.setChecked(options.add_x_to_folders)
        self.ignore_errors.setChecked(options.ignore_rights_errors)
        self.count_first.setChecked(options.count_first)
        self.split_large.setChecked(options.split_large)
        self.file_mask.set_text(options.file_mask)
        self.skip_hidden.setChecked(options.skip_hidden)
        self.skip_empty_folders.setChecked(options.skip_empty_folders)
        self.mode.setCurrentIndex(self.mode.findData(options.transfer_mode))
        self.text_mask.set_text(options.text_mask)
        self.remove_bom.setChecked(options.remove_bom)
        self.name_case.setCurrentIndex(self.name_case.findData(options.name_case))
        self.replace_characters.setChecked(options.replace_characters)
        for name, widgets, text in ((RESUME, (self.resume_label, self.resume), RESUME_TEXT), (KEEP_TIMESTAMPS, (self.keep_timestamps,), DATES_TEXT), (SET_RIGHTS, (self.set_rights,), RIGHTS_TEXT), (SPLIT, (self.split_large,), SPLIT_TEXT)):
            for widget in widgets:
                widget.setEnabled(name not in self.reasons)
                widget.setToolTip(self.reasons.get(name, text))
        if RESUME in self.reasons:
            self.resume.setCurrentIndex(self.resume.findData(RESUME_OFF))
        if KEEP_TIMESTAMPS in self.reasons:
            self.keep_timestamps.setChecked(False)
        if SET_RIGHTS in self.reasons:
            self.set_rights.setChecked(False)
        if SPLIT in self.reasons:
            self.split_large.setChecked(False)
        self._changed()

    def _changed(self) -> None:
        rights_usable = self.set_rights.isChecked() and SET_RIGHTS not in self.reasons
        for widget in (self.rights, self.add_x, self.ignore_errors):
            widget.setEnabled(rights_usable)
        why_not = self.reasons.get(SET_RIGHTS, RIGHTS_FIRST_TEXT)
        self.rights.setToolTip(RIGHTS_TEXT if rights_usable else why_not)
        self.add_x.setToolTip(ADD_X_TEXT if rights_usable else why_not)
        self.ignore_errors.setToolTip(IGNORE_TEXT if rights_usable else why_not)
        mode = self.mode.currentData()
        whole = mode == TEXT  # every file is text: none is continued or split (FD-02)
        for name, widgets, text in ((RESUME, (self.resume_label, self.resume), RESUME_TEXT), (SPLIT, (self.split_large,), SPLIT_TEXT)):
            for widget in widgets:
                widget.setEnabled(name not in self.reasons and not whole)
                widget.setToolTip(self.reasons.get(name, TEXT_WHOLE_TEXT if whole else text))
        self.text_mask.setEnabled(mode == AUTOMATIC)
        self.text_mask.setToolTip(TEXT_MASK_TEXT if mode == AUTOMATIC else TEXT_MASK_FIRST_TEXT)
        self.remove_bom.setEnabled(mode in (TEXT, AUTOMATIC))
        self.remove_bom.setToolTip(BOM_TEXT if mode in (TEXT, AUTOMATIC) else BOM_FIRST_TEXT)
        self.changed.emit()

    def acceptable(self) -> bool:
        """A rights value that is not complete yet (64), or a mask with a fault, keeps OK greyed; nothing wrong ever reaches a server."""
        text_mask_used = self.mode.currentData() == AUTOMATIC  # a field that is greyed is not in use, and never holds OK back
        return (not self.set_rights.isChecked() or rights_valid(self.rights.text())) and self.file_mask.acceptable() and (not text_mask_used or self.text_mask.acceptable())

    def problem_text(self) -> str:
        """Why OK is greyed; empty while it is not."""
        if self.set_rights.isChecked() and not rights_valid(self.rights.text()):
            return RIGHTS_INCOMPLETE_TEXT
        return MASK_INCOMPLETE_TEXT if not self.acceptable() else ""

    def chosen(self) -> TransferOptions:
        rights = self.rights.text() if rights_valid(self.rights.text()) else self.kept.rights
        shown = self.resume.currentData()
        resume = RESUME_OFF if RESUME in self.reasons else shown if isinstance(shown, str) else self.kept.resume
        return replace(
            self.kept,
            resume=resume,
            ask_before_replacing=self.ask.isChecked(),
            only_newer=self.only_newer.isChecked(),
            keep_timestamps=self.keep_timestamps.isChecked() and KEEP_TIMESTAMPS not in self.reasons,
            set_rights=self.set_rights.isChecked() and SET_RIGHTS not in self.reasons and rights_valid(self.rights.text()),
            rights=rights,
            add_x_to_folders=self.add_x.isChecked(),
            ignore_rights_errors=self.ignore_errors.isChecked(),
            count_first=self.count_first.isChecked(),
            split_large=self.split_large.isChecked() and SPLIT not in self.reasons,
            file_mask=self.file_mask.text() if self.file_mask.acceptable() else self.kept.file_mask,
            skip_hidden=self.skip_hidden.isChecked(),
            skip_empty_folders=self.skip_empty_folders.isChecked(),
            transfer_mode=mode if isinstance(mode := self.mode.currentData(), str) else self.kept.transfer_mode,
            text_mask=self.text_mask.text() if self.text_mask.acceptable() else self.kept.text_mask,
            remove_bom=self.remove_bom.isChecked(),
            name_case=case if isinstance(case := self.name_case.currentData(), str) else self.kept.name_case,
            replace_characters=self.replace_characters.isChecked(),
        )


class TransferOptionsDialog(QDialog):
    """Transfer settings for the one transfer the transfer window is about to start."""

    def __init__(self, options: TransferOptions, reasons: dict[str, str], parent: QWidget | None = None, history: tuple[str, ...] = ()) -> None:
        super().__init__(parent)
        self.setWindowTitle(SETTINGS_TITLE)
        self.form = TransferOptionsForm(history=history)
        self.form.set_options(options, reasons)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.form.changed.connect(self._check)
        layout = QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.addWidget(self.buttons)
        self._check()

    def _check(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(self.form.acceptable())
            ok.setToolTip(self.form.problem_text())
