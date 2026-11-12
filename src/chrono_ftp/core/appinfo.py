"""Facts about the program itself: its version, its repository and its licence."""

import tomllib
from importlib import metadata
from pathlib import Path
from typing import cast

# --- Version ------------------------------------------------------------
__version__ = "appinfo.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: The repository's address, for the update check (FP-06), and the licence text as shipped, never retyped (FP-05); both from
#          the source tree, else from the installed package
# v0.0.35: Initial: the program version, for the About window (D-059)

PACKAGE_NAME: str = "chrono-ftp"
UNKNOWN_VERSION: str = "unknown"
REPOSITORY_LABEL: str = "Repository"
LICENCE_FILES: tuple[str, ...] = ("licenses/LICENSE", "LICENSE")  # where a wheel keeps it (PEP 639), and where older tools did


def _source_tree() -> Path:
    return Path(__file__).resolve().parents[3]


def _project() -> dict[str, object] | None:
    """The [project] table of pyproject.toml when this runs from the source tree of Chrono FTP; None otherwise."""
    try:
        project: object = tomllib.loads((_source_tree() / "pyproject.toml").read_text("utf-8")).get("project", {})
    except (OSError, ValueError):
        return None
    if isinstance(project, dict):
        table = cast(dict[str, object], project)
        if table.get("name") == PACKAGE_NAME:
            return table
    return None


def program_version() -> str:
    """From pyproject.toml when running from the source tree, the one place the version lives and always current (the record of a source
    install is not renewed when the version changes); otherwise from the installed package."""
    project = _project()
    version = project.get("version") if project is not None else None
    if isinstance(version, str):
        return version
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return UNKNOWN_VERSION


def repository_url() -> str:
    """The address of the repository, as [project.urls] names it; empty when it can't be found."""
    project = _project()
    urls = project.get("urls") if project is not None else None
    if isinstance(urls, dict):
        found: object = cast(dict[str, object], urls).get(REPOSITORY_LABEL)
        if isinstance(found, str):
            return found
    try:
        lines = metadata.metadata(PACKAGE_NAME).get_all("Project-URL") or []
    except metadata.PackageNotFoundError:
        return ""
    for line in lines:
        label, _comma, address = str(line).partition(",")
        if label.strip() == REPOSITORY_LABEL:
            return address.strip()
    return ""


def licence_text() -> str | None:
    """The licence as shipped with the program: the LICENSE file of the source tree, else of the installed package; None when it is
    not there."""
    if _project() is not None:
        try:
            return (_source_tree() / "LICENSE").read_text("utf-8")
        except (OSError, UnicodeDecodeError):
            pass
    try:
        distribution = metadata.distribution(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return None
    for name in LICENCE_FILES:
        try:
            text = distribution.read_text(name)
        except (OSError, UnicodeDecodeError):
            text = None
        if text:
            return text
    return None
