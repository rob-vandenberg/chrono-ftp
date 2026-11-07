import json
import os
from dataclasses import asdict, dataclass
from typing import cast

from chrono_ftp.config.connections import config_folder

# --- Version ------------------------------------------------------------
__version__ = "settings.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Hidden files; the window's sizes are remembered
# v0.0.5: Initial: the settings of the program; a bad value gives the standard one (OR-15)

FILE_NAME: str = "settings.json"
WINDOW_FILE_NAME: str = "window.json"
TREE_LEFT: str = "left"
TREE_ABOVE: str = "above"
TREE_HIDDEN: str = "hidden"
TREE_PLACES: tuple[str, ...] = (TREE_LEFT, TREE_ABOVE, TREE_HIDDEN)


@dataclass(frozen=True)
class Settings:
    tree_place: str = TREE_LEFT
    show_hidden: bool = True


def _read(name: str) -> dict[str, object]:
    try:
        data: object = json.loads((config_folder() / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return cast(dict[str, object], data) if isinstance(data, dict) else {}


def _write(name: str, text: str) -> None:
    path = config_folder() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def load() -> Settings:
    data = _read(FILE_NAME)
    place = data.get("tree_place")
    hidden = data.get("show_hidden")
    return Settings(place if isinstance(place, str) and place in TREE_PLACES else TREE_LEFT, hidden if isinstance(hidden, bool) else True)


def save(settings: Settings) -> None:
    _write(FILE_NAME, json.dumps(asdict(settings), indent=2))


def load_window() -> dict[str, str]:
    """The sizes of the window, its parts and its columns, each as Qt hands them out."""
    return {name: value for name, value in _read(WINDOW_FILE_NAME).items() if isinstance(value, str)}


def save_window(state: dict[str, str]) -> None:
    _write(WINDOW_FILE_NAME, json.dumps(state, indent=2))
