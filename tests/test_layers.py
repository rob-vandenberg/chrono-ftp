import ast
import subprocess
import sys
import tomllib
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "test_layers.py 0.0.1"

# --- Version history ----------------------------------------------------
# v0.0.1: Initial skeleton

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def test_layer_contracts_hold() -> None:
    # owner rule OR-17: the layers, only ui imports PySide6
    result = subprocess.run(
        [str(Path(sys.executable).parent / "lint-imports"), "--config", "pyproject.toml"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_module_of_the_program_is_in_a_layer() -> None:
    # owner rule OR-17: a module outside the contract would escape the layer check
    settings = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    layers = settings["tool"]["importlinter"]["contracts"][0]["layers"]
    listed = {name.strip().removeprefix("chrono_ftp.") for line in layers for name in line.split("|")}
    package = PROJECT_ROOT / "src" / "chrono_ftp"
    found = {path.stem for path in package.iterdir() if path.name not in {"__init__.py", "__pycache__", "py.typed"}}
    assert found == listed


def test_every_layer_below_ui_may_not_import_pyside6() -> None:
    # owner rule OR-17: only ui imports PySide6, so a new package is in both contracts
    settings = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    layers, forbidden = settings["tool"]["importlinter"]["contracts"]
    listed = {name.strip() for line in layers["layers"] for name in line.split("|")}
    assert set(forbidden["source_modules"]) == listed - {"chrono_ftp.ui"}


def test_the_root_package_imports_nothing() -> None:
    # owner rule OR-17: every layer loads this file, so an import here would escape the layer check
    tree = ast.parse((PROJECT_ROOT / "src" / "chrono_ftp" / "__init__.py").read_text(encoding="utf-8"))
    assert [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))] == []
