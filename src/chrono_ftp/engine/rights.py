"""The rights of files (FM-02, FM-03): the twelve boxes of the rights editor, each on, off or "leave as it is", and what they become: an
octal number, the rights of one file, or a chmod that changes exactly the boxes that are set."""

# --- Version ------------------------------------------------------------
__version__ = "rights.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Initial: rights as boxes with "leave as it is", octal both ways, one file's new rights, and the chmod for the server (D-060);
#          never X, which also gave Run to files; folders get Run for the classes whose Read box is on, on both sides alike; the classes
#          are given apart from the boxes that change, as only the touched boxes change

# The boxes in the order of the editor: read, write and run for the owner, the group and others, then set user, set group and sticky.
BITS: tuple[int, ...] = (0o400, 0o200, 0o100, 0o040, 0o020, 0o010, 0o004, 0o002, 0o001, 0o4000, 0o2000, 0o1000)
CLASSES: tuple[str, ...] = ("u", "g", "o")
LETTERS: str = "rwx"
SPECIALS: tuple[tuple[str, str], ...] = (("u", "s"), ("g", "s"), ("", "t"))  # sticky without a class: understood alike by every chmod
ALL_RIGHTS: int = 0o7777
NEW_FOLDER_RIGHTS: int = 0o755  # what the editor starts with for a new folder

Rights = tuple[bool | None, ...]  # one per BITS: True on, False off, None leave as it is (the files differ there)


def rights_of(mode: int) -> Rights:
    return tuple(bool(mode & bit) for bit in BITS)


def mode_number(text: str) -> int | None:
    """The rights a listing gave as octal digits ("0755", "644"); None when it gave none, or something else. The text is input."""
    if not 1 <= len(text) <= 6 or any(character not in "01234567" for character in text):
        return None
    return int(text, 8) & ALL_RIGHTS


def common(modes: list[int | None]) -> Rights:
    """The boxes for several files: on or off where every file agrees, "leave as it is" where they differ or a file's rights are unknown."""
    found: list[bool | None] = []
    for bit in BITS:
        values = {None if mode is None else bool(mode & bit) for mode in modes}
        found.append(values.pop() if len(values) == 1 else None)
    return tuple(found)


def complete(rights: Rights) -> bool:
    return all(value is not None for value in rights)


def octal_text(rights: Rights) -> str:
    """Four octal digits, as 0755; empty while a box is "leave as it is"."""
    if not complete(rights):
        return ""
    return format(sum(bit for bit, value in zip(BITS, rights, strict=True) if value), "04o")


def from_octal(text: str) -> Rights | None:
    """Typed octal: three or four digits; None for anything else."""
    clean = text.strip()
    if len(clean) not in (3, 4) or any(character not in "01234567" for character in clean):
        return None
    return rights_of(int(clean, 8))


def applied(mode: int | None, rights: Rights) -> int | None:
    """One file's new rights: the boxes that are set, and its own rights where a box says leave. None when its own rights are unknown
    and a box needs them: that file is left as it is."""
    if mode is None and not complete(rights):
        return None
    result = mode or 0
    for bit, value in zip(BITS, rights, strict=True):
        if value is True:
            result |= bit
        elif value is False:
            result &= ~bit
    return result & ALL_RIGHTS


def symbolic(rights: Rights) -> str:
    """The chmod mode that sets exactly the boxes that are set and leaves the others, as u+rw,u-x,g+r,…. Never X: it would also give
    Run to a file that has it for any class, and undo a Run the person switched off."""
    clauses: list[str] = []
    for place, who in enumerate(CLASSES):
        trio = rights[place * 3 : place * 3 + 3]
        given = "".join(letter for letter, value in zip(LETTERS, trio, strict=True) if value is True)
        taken = "".join(letter for letter, value in zip(LETTERS, trio, strict=True) if value is False)
        if given:
            clauses.append(f"{who}+{given}")
        if taken:
            clauses.append(f"{who}-{taken}")
    for (who, letter), value in zip(SPECIALS, rights[9:], strict=True):
        if value is not None:
            clauses.append(f"{who}{'+' if value else '-'}{letter}")
    return ",".join(clauses)


def readers(rights: Rights) -> str:
    """The classes whose Read box is on (as ug): with "give folders Run", each folder gets Run for exactly these (FM-03)."""
    return "".join(who for place, who in enumerate(CLASSES) if rights[place * 3] is True)


def with_x_for_folders(mode: int, classes: str) -> int:
    """A folder's rights after "give folders Run where they get Read" (FM-03) for these classes (as ug), as the find of the server does."""
    for place, who in enumerate(CLASSES):
        if who in classes:
            mode |= BITS[place * 3 + 2]
    return mode


def letters(mode: int) -> str:
    """rwxr-xr-x, with s, S, t and T where the special bits are, as ls shows them."""
    shown = [letter if mode & bit else "-" for letter, bit in zip(LETTERS * 3, BITS[:9], strict=True)]
    for (place, special), bit in zip(((2, "s"), (5, "s"), (8, "t")), BITS[9:], strict=True):
        if mode & bit:
            shown[place] = special if shown[place] == "x" else special.upper()
    return "".join(shown)
