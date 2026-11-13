"""Name masks, such as `*.log; *.tmp`: the one place that says whether a name fits a mask (FQ-01, FQ-02). Block FD brings the full mask
engine; it replaces this function, and everything that matches a name asks here."""

import fnmatch

# --- Version ------------------------------------------------------------
__version__ = "masks.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Initial: simple name patterns, separated by ; or ,; a pattern that ends in / is for folders; case does not matter (D-069)

SEPARATORS: tuple[str, ...] = (";", ",")
FOLDER_MARK: str = "/"
LONGEST_MASK: int = 1000  # a mask from a file is input (OR-15)


def parts_of(mask: str) -> list[str]:
    """The patterns of a mask, each without the spaces around it; empty ones left out."""
    text = mask
    for separator in SEPARATORS[1:]:
        text = text.replace(separator, SEPARATORS[0])
    return [part.strip() for part in text.split(SEPARATORS[0]) if part.strip()]


def matches(mask: str, name: str, is_folder: bool) -> bool:
    """Whether a name fits one of the patterns of the mask. A pattern ending in / fits folders only (`build/`, `.*/`); any other pattern
    fits files only, so `*` colours files and leaves folders as they are. Upper and lower case are the same."""
    folded = name.casefold()
    for part in parts_of(mask[:LONGEST_MASK]):
        for_folders = part.endswith(FOLDER_MARK)
        pattern = part.rstrip(FOLDER_MARK) if for_folders else part
        if for_folders == is_folder and pattern and fnmatch.fnmatchcase(folded, pattern.casefold()):
            return True
    return False
