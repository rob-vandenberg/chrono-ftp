import json
import os
from dataclasses import asdict, dataclass
from typing import cast

from chrono_ftp.config.connections import config_folder

# --- Version ------------------------------------------------------------
__version__ = "settings.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: the settings of the program; a bad value gives the standard one (OR-15)

FILE_NAME: str = "settings.json"
TREE_LEFT: str = "left"
TREE_ABOVE: str = "above"
TREE_HIDDEN: str = "hidden"
TREE_PLACES: tuple[str, ...] = (TREE_LEFT, TREE_ABOVE, TREE_HIDDEN)


@dataclass(frozen=True)
class Settings:
    tree_place: str = TREE_LEFT


def load() -> Settings:
    path = config_folder() / FILE_NAME
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return Settings()
    place = cast(dict[str, object], data).get("tree_place") if isinstance(data, dict) else None
    return Settings(place) if isinstance(place, str) and place in TREE_PLACES else Settings()


def save(settings: Settings) -> None:
    path = config_folder() / FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        file.write(json.dumps(asdict(settings), indent=2))
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)
