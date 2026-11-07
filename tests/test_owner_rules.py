"""OR-33: a test that checks the project itself names the owner rule it enforces, and every number it compares against appears in that
rule. So no test can hold a rule the owner never made, nor a limit the owner never set."""

import ast
import re
from pathlib import Path

import pytest

# --- Version ------------------------------------------------------------
__version__ = "test_owner_rules.py 0.0.1"

# --- Version history ----------------------------------------------------
# v0.0.1: Initial: the check of Chrono Mail, with the numbers of this project

ROOT: Path = Path(__file__).resolve().parent.parent
RULE: re.Pattern[str] = re.compile(r"\bOR-(\d+)\b")
TOOLS: re.Pattern[str] = re.compile(r'"src"|"pyproject\.toml"|"(git|lint-imports|pyrefly)"|ls-files')  # what a check of the project itself reads or runs
SMALLEST_NUMBER: int = 10  # below it a number is a count of things in the test itself (0, 1, 2 …), not a limit


def owner_rules(root: Path = ROOT) -> dict[str, str] | None:
    """OR-nn -> its text, from OWNER-RULES.md; None in a copy of the project without it (the file is private, not in git)."""
    path = root / "OWNER-RULES.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return {match.group(1): line for line in text.splitlines() for match in [re.match(r"(?:- )?\*\*OR-(\d+)\b", line)] if match}


def _numbers_in(text: str) -> set[float]:
    return {float(found.replace(",", "")) for found in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


class Module:
    """One test module: its functions, and the names that lead into the project (paths into src, the tools that check it)."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tree = ast.parse(source)
        self.functions = {node.name: node for node in ast.walk(self.tree) if isinstance(node, ast.FunctionDef)}  # also the tests inside a class
        self.constants: dict[str, float] = {}
        self.project: set[str] = set()
        for node in self.tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = [name.id for name in (node.targets if isinstance(node, ast.Assign) else [node.target]) if isinstance(name, ast.Name)]
            text = ast.get_source_segment(source, node) or ""
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)) and not isinstance(value.value, bool):
                self.constants.update(dict.fromkeys(targets, float(value.value)))
            if TOOLS.search(text) or (self.project and re.search(r"\b(" + "|".join(sorted(self.project)) + r")\b", text)):
                self.project.update(targets)

    def text(self, node: ast.AST) -> str:
        return ast.get_source_segment(self.source, node) or ""

    def reach(self, name: str) -> list[ast.FunctionDef]:
        """The test and every function of this module it calls, directly or through another."""
        found: dict[str, ast.FunctionDef] = {}
        waiting = [name]
        while waiting:
            current = waiting.pop()
            if current in found or current not in self.functions:
                continue
            found[current] = self.functions[current]
            waiting.extend(node.id for node in ast.walk(self.functions[current]) if isinstance(node, ast.Name))
        return list(found.values())

    def checks_the_project(self, name: str) -> bool:
        return any(TOOLS.search(self.text(node)) or {item.id for item in ast.walk(node) if isinstance(item, ast.Name)} & self.project for node in self.reach(name))

    def compared_numbers(self, name: str) -> set[float]:
        """Every number the test (and what it calls) compares against: a written number, or a module constant holding one."""
        found: set[float] = set()
        for function in self.reach(name):
            for compare in (node for node in ast.walk(function) if isinstance(node, ast.Compare)):
                for side in [compare.left, *compare.comparators]:
                    if isinstance(side, ast.Constant) and isinstance(side.value, (int, float)) and not isinstance(side.value, bool):
                        found.add(float(side.value))
                    elif isinstance(side, ast.Name) and side.id in self.constants:
                        found.add(self.constants[side.id])
        return {number for number in found if abs(number) >= SMALLEST_NUMBER}


def faults(path: str, source: str, rules: dict[str, str]) -> list[str]:
    """What is wrong with the project checks of one test module (empty: nothing)."""
    module = Module(source)
    found: list[str] = []
    for name in module.functions:
        if not name.startswith("test_") or not module.checks_the_project(name):
            continue
        cited = {number for function in module.reach(name)[:1] for number in RULE.findall(module.text(function))}
        if not cited:
            found.append(f"{path}::{name} checks the project but names no owner rule (OR-nn)")
            continue
        unknown = cited - rules.keys()
        if unknown:
            found.append(f"{path}::{name} names a rule that does not exist: {', '.join('OR-' + number for number in sorted(unknown))}")
            continue
        allowed = set().union(*(_numbers_in(rules[number]) for number in cited))
        invented = sorted(module.compared_numbers(name) - allowed)
        if invented:
            found.append(f"{path}::{name} compares against {invented}, which no cited rule sets")
    return found


def test_every_check_of_the_project_names_its_owner_rule_and_uses_only_the_rules_numbers() -> None:
    """OR-33: the check that keeps every other check honest."""
    rules = owner_rules()
    if rules is None:
        pytest.skip("OWNER-RULES.md is private and not in this copy of the project")
    found: list[str] = []
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        found.extend(faults(path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8-sig"), rules))
    assert found == []


def test_the_check_catches_a_limit_the_owner_never_set() -> None:
    """OR-33: a guard with an invented limit, and the same guard citing a rule: both refused; a guard within OR-8 passes."""
    rules = {"8": "- **OR-8** A code file may have up to 2,000 lines", "32": "- **OR-32** decisions"}
    invented = 'UI = ROOT / "src"\n\ndef test_small() -> None:\n    assert lines(UI / "mainwindow.py") < 1100\n'
    cited = 'UI = ROOT / "src"\n\ndef test_small() -> None:\n    """OR-32"""\n    assert lines(UI / "mainwindow.py") < 1100\n'
    kept = 'UI = ROOT / "src"\n\ndef test_small() -> None:\n    """OR-8"""\n    assert lines(UI / "mainwindow.py") <= 2000\n'
    assert faults("a.py", invented, rules) == ["a.py::test_small checks the project but names no owner rule (OR-nn)"]
    assert faults("a.py", cited, rules) == ["a.py::test_small compares against [1100.0], which no cited rule sets"]
    assert faults("a.py", kept, rules) == []


def test_a_copy_of_the_project_without_the_rules_file_gives_no_rules(tmp_path: Path) -> None:
    """The rules file is private (not in git): a fresh clone has none, which is no fault of the project."""
    assert owner_rules(tmp_path) is None
    (tmp_path / "OWNER-RULES.md").write_text("- **OR-8** A code file may have up to 2,000 lines\n", encoding="utf-8")
    assert owner_rules(tmp_path) == {"8": "- **OR-8** A code file may have up to 2,000 lines"}


def test_a_rule_written_as_a_line_of_its_own_is_read(tmp_path: Path) -> None:
    """OWNER-RULES.md writes each rule as a line starting with its bold number, without a hyphen in front."""
    (tmp_path / "OWNER-RULES.md").write_text("**OR-8 A code file may have up to 2,000 lines**.\n\n**OR-80** no\n", encoding="utf-8")
    rules = owner_rules(tmp_path)
    assert rules is not None and set(rules) == {"8", "80"} and rules["8"].startswith("**OR-8 ")
