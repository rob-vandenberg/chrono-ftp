"""The options of one transfer (FB-01). The record lives here, in the lowest layer, so the settings can keep its defaults; what the options
do is in chrono_ftp.transfers."""

import re
from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "transferoptions.py 0.0.13"

# --- Version history ----------------------------------------------------
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


def rights_valid(text: str) -> bool:
    return RIGHTS_PATTERN.fullmatch(text) is not None


def file_rights(options: TransferOptions) -> str:
    return format(int(options.rights, 8), "04o")


def folder_rights(options: TransferOptions) -> str:
    mode = int(options.rights, 8)
    if options.add_x_to_folders:
        mode |= (mode & 0o444) >> 2
    return format(mode, "04o")
