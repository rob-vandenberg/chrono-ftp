"""What a transfer changes on the way (block FD): which items a mask lets through (FD-01), the line endings of text files (FD-02) and the
names in the target (FD-03). Nothing here touches a server; a file is converted only into a file of our own, never under a real name."""

import errno
import os
import threading
import uuid
from pathlib import Path

from chrono_ftp.config import parts
from chrono_ftp.connections.base import own_name
from chrono_ftp.connections.sessionlog import state_folder
from chrono_ftp.core.errors import LocalActionFailed
from chrono_ftp.core.masks import EVERYTHING, Mask, MaskError, fits, named, parse
from chrono_ftp.core.transferoptions import AUTOMATIC, BINARY, CASE_FIRST_UPPER, CASE_LOWER, CASE_UPPER, TEXT, TransferOptions
from chrono_ftp.transfers.job import Stopped

# --- Version ------------------------------------------------------------
__version__ = "conversion.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: what passes the file mask, hidden files and empty folders (FD-01); text files and their line endings, block by
#          block, with the BOM removed where asked, into a copy of our own (FD-02); the case of names and the characters other systems
#          can't hold (FD-03) (D-081 to D-083); review: the copy of an upload is for the person alone (0600, in a 0700 folder); the
#          converted copy of a fetched text is noted as ours before it is made, so one left by an unexpected end is tidied the next time

CR: bytes = b"\r"
LF: bytes = b"\n"
CRLF: bytes = b"\r\n"
BOM: bytes = b"\xef\xbb\xbf"  # the mark some editors put at the start of a UTF-8 text
HERE_LINE_END: bytes = LF  # the line ending of this computer
SERVER_LINE_END: bytes = LF  # the line ending a text gets on a server (D-082)
BLOCK_BYTES: int = 1024 * 1024
TEXT_SUFFIX: str = ".chrono-ftp-text"  # a converted copy of our own, next to its part file or in our state folder
CONVERTED_FOLDER: str = "converted"
TEXT_COPY_SOURCE: str = "converted text"  # the note of a converted copy next to its part: it is never continued, only tidied
MOST_COPY_NAMES: int = 100
FOREIGN_CHARACTERS: str = '\\:*?"<>|'  # what other systems (Windows, a memory stick) can't hold in a name
NO_COPY_TEXT: str = "Chrono FTP could not convert the line endings of {name} on this computer ({reason}), so it was not {done}."
BAD_MASK_TEXT: str = "The file mask of this transfer can't be read, so nothing was sent: {why}"


class Passing:
    """What goes of a transfer: the file mask, hidden files and empty folders. A mask that can't be read lets nothing through: the
    window never starts such a transfer, and one from an older settings file fails calmly (OR-15)."""

    def __init__(self, options: TransferOptions) -> None:
        self.skip_hidden: bool = options.skip_hidden
        self.skip_empty_folders: bool = options.skip_empty_folders
        self.problem: str = ""  # why the mask can't be read: the transfer then sends nothing and says this
        try:
            self.mask: Mask = parse(options.file_mask) if options.file_mask.strip() else EVERYTHING
        except MaskError as error:
            self.mask = EVERYTHING
            self.problem = BAD_MASK_TEXT.format(why=str(error))

    @property
    def everything(self) -> bool:
        return self.mask.empty and not self.skip_hidden and not self.skip_empty_folders

    def passes(self, name: str, is_folder: bool, folder: str, size: int | None, modified: float | None) -> bool:
        """folder: the path of the folder the item is in, on its own side."""
        if self.skip_hidden and name.startswith("."):
            return False
        return self.mask.empty or fits(self.mask, name, is_folder, folder, None if is_folder else size, None if is_folder else modified)


def is_text(options: TransferOptions, name: str) -> bool:
    """Whether a file goes as text: always in the text mode, by the text mask in the automatic mode, never in binary."""
    if options.transfer_mode == TEXT:
        return True
    if options.transfer_mode != AUTOMATIC:
        return False
    try:
        mask = parse(options.text_mask)
    except MaskError:
        return False  # a text mask that can't be read makes nothing text: byte for byte never damages a file
    return named(mask, name, False)


def renamed(options: TransferOptions, name: str) -> str:
    """The name an item gets in the target (FD-03)."""
    if options.replace_characters:
        name = "".join(f"%{ord(character):02X}" if character in FOREIGN_CHARACTERS or ord(character) < 32 else character for character in name)
    if options.name_case == CASE_LOWER:
        return name.lower()
    if options.name_case == CASE_UPPER:
        return name.upper()
    if options.name_case == CASE_FIRST_UPPER:
        return name[:1].upper() + name[1:].lower()
    return name


def changes_names(options: TransferOptions) -> bool:
    return options.replace_characters or options.name_case in (CASE_LOWER, CASE_UPPER, CASE_FIRST_UPPER)


def chooses_items(options: TransferOptions) -> bool:
    """Whether a transfer leaves items out or changes them on the way: then the rsync way is looked over first, so rsync gets the exact
    list of what passes, and what rsync can't do (a text, a new name) goes file by file."""
    return bool(options.file_mask.strip()) or options.skip_hidden or options.skip_empty_folders or options.transfer_mode != BINARY or changes_names(options)


class LineEnds:
    """Converts a text block by block: CRLF and LF both end a line, and each becomes line_end; a lone CR stays as it is, and so does
    every other byte, so a file that is not text in the expected way is never broken further. With remove_bom the UTF-8 mark at the very
    start goes. A CR at the end of a block waits for the next one, so a CRLF split between two blocks is still one line end."""

    def __init__(self, line_end: bytes, remove_bom: bool) -> None:
        self.line_end: bytes = line_end
        self.remove_bom: bool = remove_bom
        self.at_start: bool = True
        self.held: bytes = b""

    def block(self, data: bytes) -> bytes:
        data = self.held + data
        self.held = b""
        if self.at_start:
            if len(data) < len(BOM) and BOM.startswith(data):
                self.held = data  # perhaps the start of the mark: decided once more has come
                return b""
            self.at_start = False
            if self.remove_bom and data.startswith(BOM):
                data = data[len(BOM) :]
        if data.endswith(CR):
            self.held, data = CR, data[:-1]
        return self._converted(data)

    def end(self) -> bytes:
        data, self.held = self.held, b""
        return data  # a lone CR at the end, or a short start that was no whole mark: as it was

    def _converted(self, data: bytes) -> bytes:
        data = data.replace(CRLF, LF)
        return data.replace(LF, self.line_end) if self.line_end != LF else data


def convert_bytes(data: bytes, line_end: bytes, remove_bom: bool) -> bytes:
    """A whole text at once: what convert_file writes, for the tests and for small files."""
    ends = LineEnds(line_end, remove_bom)
    return ends.block(data) + ends.end()


PRIVATE_FILE: int = 0o600  # a converted copy in the state folder: for the person alone, as the session log
PRIVATE_FOLDER: int = 0o700


def _private(path: str, flags: int) -> int:
    return os.open(path, flags, PRIVATE_FILE)


def convert_file(source: str, target: str, line_end: bytes, remove_bom: bool, stop: threading.Event | None = None, private: bool = False) -> int:
    """source converted into target, which is made only where nothing is (never someone else's file); private: readable by the person
    alone from the moment it is made. The bytes written. OSError where a file can't be read or written here; Stopped at a stop. The
    caller removes target on any failure."""
    ends = LineEnds(line_end, remove_bom)
    written = 0
    with open(source, "rb") as reading, open(target, "xb", opener=_private if private else None) as writing:
        while True:
            if stop is not None and stop.is_set():
                raise Stopped
            block = reading.read(BLOCK_BYTES)
            if not block:
                break
            out = ends.block(block)
            writing.write(out)
            written += len(out)
        out = ends.end()
        writing.write(out)
        written += len(out)
    return written


def converted_folder() -> Path:
    return state_folder() / CONVERTED_FOLDER


def _tidy_converted(folder: Path) -> None:
    """Copies an earlier Chrono FTP left when it ended unexpectedly: each name starts with the number of the process that made it, and
    one of a process that no longer runs goes."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        number = name.split("-", 1)[0]
        if not number.isdigit() or int(number) == os.getpid():
            continue
        try:
            os.kill(int(number), 0)
        except ProcessLookupError:
            Path(folder, name).unlink(missing_ok=True)
        except (OSError, OverflowError):
            continue


def upload_copy(source: Path, remove_bom: bool, stop: threading.Event | None = None) -> tuple[str, int]:
    """A text to upload, converted to the server's line endings into a private copy in Chrono FTP's state folder: the source itself is
    never changed. The copy's path and size; the caller removes it once it was sent, or failed."""
    folder = converted_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True, mode=PRIVATE_FOLDER)
        if folder.stat().st_mode & 0o077:
            folder.chmod(PRIVATE_FOLDER)  # made by an earlier version with the usual rights: for the person alone from now on
    except OSError as error:
        raise LocalActionFailed(NO_COPY_TEXT.format(name=source.name, reason=error.strerror or str(error), done="sent")) from error
    _tidy_converted(folder)
    copy = folder / f"{os.getpid()}-{uuid.uuid4().hex}{TEXT_SUFFIX}"
    try:
        return str(copy), convert_file(str(source), str(copy), SERVER_LINE_END, remove_bom, stop, True)
    except BaseException as error:
        copy.unlink(missing_ok=True)
        if isinstance(error, OSError):
            raise LocalActionFailed(NO_COPY_TEXT.format(name=source.name, reason=error.strerror or str(error), done="sent")) from error
        raise


def _free_copy(part: str) -> tuple[str, str]:
    """The name of a fetched text's converted copy next to its part, and its note's key. A copy of our own an earlier Chrono FTP left when
    it ended unexpectedly (it has our note, and nobody writes it now) goes first; a file without our note, or one another Chrono FTP writes
    now, is never touched: the next name is taken (D-044)."""
    folder, part_name = os.path.split(part)
    for number in range(1, MOST_COPY_NAMES):
        copy = os.path.join(folder, own_name(part_name, TEXT_SUFFIX if number == 1 else f"~{number}{TEXT_SUFFIX}"))
        key = parts.download_key(copy)
        note = parts.lookup(key)
        if note is not None and parts.in_use(note):
            continue
        if os.path.lexists(copy):
            if note is None or not os.path.isfile(copy) or os.path.islink(copy):
                continue  # not ours
            os.remove(copy)  # ours, left behind: tidied now
            parts.forget(key)
        if parts.claim(key, parts.PartRecord(TEXT_COPY_SOURCE, 0, 0.0, 0)) is False:
            continue  # another Chrono FTP took it this moment
        return copy, key
    raise OSError(errno.EEXIST, "every name for the converted copy is taken")


def convert_fetched(part: str, name: str, remove_bom: bool, stop: threading.Event | None = None) -> int:
    """A fetched text, whole in its part file, converted to this computer's line endings: into a file of our own next to it, noted as ours
    before it is made (so one an unexpected end leaves behind is tidied the next time), which then takes the part's place. The part's real
    name is never touched; on any failure the part stays as it arrived. The size it has now."""
    try:
        copy, key = _free_copy(part)
    except OSError as error:
        raise LocalActionFailed(NO_COPY_TEXT.format(name=name, reason=error.strerror or str(error), done="fetched")) from error
    try:
        size = convert_file(part, copy, HERE_LINE_END, remove_bom, stop)
        os.replace(copy, part)
    except BaseException as error:
        Path(copy).unlink(missing_ok=True)
        if isinstance(error, OSError):
            raise LocalActionFailed(NO_COPY_TEXT.format(name=name, reason=error.strerror or str(error), done="fetched")) from error
        raise
    finally:
        parts.forget(key)
    return size
