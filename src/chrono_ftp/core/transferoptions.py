"""The options of one transfer (FB-01). The record lives here, in the lowest layer, so the settings can keep its defaults; what the options
do is in chrono_ftp.transfers."""

import re
from dataclasses import dataclass, replace

# --- Version ------------------------------------------------------------
__version__ = "transferoptions.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Masks (block FD): a file mask, skip hidden files and skip empty folders (FD-01); binary, text or automatic by a text mask,
#          with removing the BOM (FD-02); the case of names and replacing characters other systems can't hold (FD-03); the options of a
#          transfer that sends exactly the files it is given (plain); the standard changes nothing
# v0.0.13: Split large files over several connections (FC-05), off unless the person chooses it
# v0.0.12: Initial: resume, the overwrite question, only newer, dates, rights on upload, count first, delete the source (FB-01)

RESUME_SMART: str = "smart"
RESUME_ON: str = "on"
RESUME_OFF: str = "off"
RESUME_MODES: tuple[str, ...] = (RESUME_SMART, RESUME_ON, RESUME_OFF)
SMART_RESUME_BYTES: int = 100 * 1024  # smart resume: only files this large, as the reference program does
DEFAULT_RIGHTS: str = "644"
RIGHTS_TEXT_PATTERN: str = "[0-7]{3,4}"  # octal, as chmod takes it: 644 or 0644
RIGHTS_PATTERN: re.Pattern[str] = re.compile(RIGHTS_TEXT_PATTERN)
BINARY: str = "binary"  # every file goes byte for byte (FD-02)
TEXT: str = "text"  # every file is text: its line endings become those of the side it goes to
AUTOMATIC: str = "automatic"  # the files that fit the text mask are text, the others go byte for byte
TRANSFER_MODES: tuple[str, ...] = (BINARY, TEXT, AUTOMATIC)
TEXT_MASK: str = ("*.txt; *.text; *.htm; *.html; *.xhtml; *.css; *.js; *.php; *.cgi; *.pl; *.py; *.sh; *.c; *.cpp; *.h; *.java; *.xml; "
                  "*.json; *.yml; *.yaml; *.ini; *.cfg; *.conf; *.csv; *.md; *.sql; .htaccess")
CASE_UNCHANGED: str = "unchanged"  # the case of names as they are (FD-03)
CASE_LOWER: str = "lower"
CASE_UPPER: str = "upper"
CASE_FIRST_UPPER: str = "first upper"  # the first letter upper, the rest lower
NAME_CASES: tuple[str, ...] = (CASE_UNCHANGED, CASE_LOWER, CASE_UPPER, CASE_FIRST_UPPER)


@dataclass(frozen=True)
class TransferOptions:
    resume: str = RESUME_SMART
    ask_before_replacing: bool = True
    only_newer: bool = False  # a file is skipped when its target is not older
    keep_timestamps: bool = True
    set_rights: bool = False  # only on upload
    rights: str = DEFAULT_RIGHTS
    add_x_to_folders: bool = True  # a folder gets x where it has r, so it can be opened
    ignore_rights_errors: bool = False
    count_first: bool = True
    delete_source: bool = False  # a move: each source file goes once it arrived; never a saved default
    split_large: bool = False  # a large file goes over several connections at once (FC-05): many servers limit logins, so off
    file_mask: str = ""  # only what fits goes (FD-01); empty: everything
    skip_hidden: bool = False  # names that start with a dot stay where they are
    skip_empty_folders: bool = False  # a folder that holds no file that goes is not made
    transfer_mode: str = BINARY  # FD-02
    text_mask: str = TEXT_MASK  # the text files of the automatic mode
    remove_bom: bool = False  # a text file loses the UTF-8 mark at its start
    name_case: str = CASE_UNCHANGED  # FD-03
    replace_characters: bool = False  # characters other systems can't hold in a name become %XX


def plain(options: TransferOptions) -> TransferOptions:
    """The options of a transfer that sends exactly the files it is given, byte for byte under their own names: a save from Edit, the
    rows ticked in Compare. No mask may leave one out, and nothing is converted."""
    return replace(options, file_mask="", skip_hidden=False, skip_empty_folders=False, transfer_mode=BINARY, name_case=CASE_UNCHANGED, replace_characters=False)


def rights_valid(text: str) -> bool:
    return RIGHTS_PATTERN.fullmatch(text) is not None


def file_rights(options: TransferOptions) -> str:
    return format(int(options.rights, 8), "04o")


def folder_rights(options: TransferOptions) -> str:
    mode = int(options.rights, 8)
    if options.add_x_to_folders:
        mode |= (mode & 0o444) >> 2
    return format(mode, "04o")
