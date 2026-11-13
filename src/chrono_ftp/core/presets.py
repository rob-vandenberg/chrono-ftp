"""Transfer presets (FD-04): named sets of transfer options, each with rules on the host, the user, the remote folder and the local folder.
The transfer window takes the first set whose rules all fit the transfer; without one, the standard set of Settings."""

from dataclasses import dataclass, field

from chrono_ftp.core.masks import mask_parts, matches_whole
from chrono_ftp.core.transferoptions import TransferOptions

# --- Version ------------------------------------------------------------
__version__ = "presets.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: a preset of a name, its options and its rules; the first preset whose rules all fit is chosen, a preset without
#          rules only by hand (D-084); ~ in a folder rule is the home folder of its side, and a rule ending in /* fits that folder too

STANDARD_NAME: str = "Standard"  # the options of Settings, Transfers: always there, never renamed or deleted
MOST_PRESETS: int = 100  # a list from a file is input (OR-15)
LONGEST_NAME: int = 100


@dataclass(frozen=True)
class PresetRules:
    """Each rule a mask of the whole value, such as `*.example.com` or `/var/www/*`; an empty rule fits everything."""

    host: str = ""
    user: str = ""
    remote_folder: str = ""
    local_folder: str = ""

    @property
    def empty(self) -> bool:
        return not (self.host.strip() or self.user.strip() or self.remote_folder.strip() or self.local_folder.strip())


@dataclass(frozen=True)
class Preset:
    name: str
    options: TransferOptions = field(default_factory=TransferOptions)
    rules: PresetRules = field(default_factory=PresetRules)


HOME_MARK: str = "~"


def with_home(mask: str, home: str) -> str:
    """A folder rule with ~ at the start of a pattern standing for the home folder, where that is known; else as it is."""
    if not home:
        return mask
    parts = [home.rstrip("/") + part[1:] if part == HOME_MARK or part.startswith(HOME_MARK + "/") else part for part in mask_parts(mask)]
    return ";".join(part or "/" for part in parts)


def rules_fit(rules: PresetRules, host: str, user: str, remote_folder: str, local_folder: str, remote_home: str = "", local_home: str = "") -> bool:
    """A preset without rules is never chosen by itself: only one with at least one rule, all of which fit. ~ in a folder rule is the
    home folder of its side: this computer's, and the login's on the server where it is known."""
    if rules.empty:
        return False
    checks = ((rules.host, host), (rules.user, user), (with_home(rules.remote_folder, remote_home), remote_folder), (with_home(rules.local_folder, local_home), local_folder))
    return all(matches_whole(mask, value) for mask, value in checks)


def preset_for(presets: tuple[Preset, ...], host: str, user: str, remote_folder: str, local_folder: str, remote_home: str = "", local_home: str = "") -> Preset | None:
    """The first preset whose rules fit this transfer; None: the standard set."""
    return next((preset for preset in presets if rules_fit(preset.rules, host, user, remote_folder, local_folder, remote_home, local_home)), None)


def name_problem(name: str, taken: list[str]) -> str:
    """Why a name can't be a preset's name; empty when it can."""
    cleaned = name.strip()
    if not cleaned:
        return "Please give the preset a name."
    if len(cleaned) > LONGEST_NAME:
        return f"A name has {LONGEST_NAME} characters at most."
    if cleaned.casefold() == STANDARD_NAME.casefold() or cleaned.casefold() in (item.casefold() for item in taken):
        return "There is a preset with this name already."
    return ""
