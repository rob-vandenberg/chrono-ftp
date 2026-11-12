"""Facts about the program itself: its version."""

import tomllib
from importlib import metadata
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "appinfo.py 0.0.35"

# --- Version history ----------------------------------------------------
# v0.0.35: Initial: the program version, for the About window (D-059)

PACKAGE_NAME: str = "chrono-ftp"
UNKNOWN_VERSION: str = "unknown"


def program_version() -> str:
    """From pyproject.toml when running from the source tree, the one place the version lives and always current (the record of a source
    install is not renewed when the version changes); otherwise from the installed package."""
    project_file = Path(__file__).resolve().parents[3] / "pyproject.toml"
    try:
        project: object = tomllib.loads(project_file.read_text("utf-8")).get("project", {})
        if isinstance(project, dict):
            name: object = project.get("name")
            version: object = project.get("version")
            if name == PACKAGE_NAME and isinstance(version, str):
                return version
    except (OSError, ValueError):
        pass
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return UNKNOWN_VERSION
