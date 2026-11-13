"""File colours by mask (FQ-01): a list of rules, each a mask with a text colour and a background the person chose; the first rule that
fits a name colours its row. The colours are the person's own: they are kept in the settings and never themed (OR-9)."""

import re
from dataclasses import dataclass

from chrono_ftp.core.masks import matches

# --- Version ------------------------------------------------------------
__version__ = "filecolours.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Initial: a rule of a mask, a text colour and a background, either may be empty; the first rule that fits wins (D-069)

COLOUR: re.Pattern[str] = re.compile(r"#[0-9a-fA-F]{6}")
MOST_RULES: int = 100  # a list from a file is input (OR-15)


@dataclass(frozen=True)
class ColourRule:
    mask: str
    text: str = ""  # "#rrggbb", or empty: the row keeps the text colour of its side
    background: str = ""  # "#rrggbb", or empty: the row keeps the background of its side


def colour_valid(value: object) -> bool:
    """"#rrggbb", or empty for no colour."""
    return isinstance(value, str) and (value == "" or COLOUR.fullmatch(value) is not None)


def rule_for(rules: tuple[ColourRule, ...], name: str, is_folder: bool) -> ColourRule | None:
    """The first rule whose mask fits the name and that colours something; None leaves the row as its side shows it."""
    return next((rule for rule in rules if (rule.text or rule.background) and matches(rule.mask, name, is_folder)), None)
