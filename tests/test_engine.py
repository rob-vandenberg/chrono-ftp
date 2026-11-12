import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import override

import pytest

from chrono_ftp.connections.base import Capabilities, CommandResult, Limit, Progress, RemoteConnection, RemoteEntry
from chrono_ftp.core.errors import RemoteActionFailed
from chrono_ftp.engine import commands
from chrono_ftp.engine.jobs import Worker

# --- Version ------------------------------------------------------------
__version__ = "test_engine.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Owner and group names of directories are accepted; only a hyphen first, a colon and control characters are refused
# v0.0.13: Scripted takes a speed limit that may be asked again for every block
# v0.0.12: Scripted takes the offset, append and exclusive of a transfer, and knows the size of what was uploaded
# v0.0.5: Initial


def test_any_owner_name_stays_one_quoted_argument() -> None:
    import shlex

    assert shlex.split(commands.owner_command(["/srv"], "domain users", "$(id)", False)) == ["chown", "--", "domain users:$(id)", "/srv"]


def test_a_recursive_change_is_one_command_with_every_path_quoted() -> None:
    assert commands.owner_command(["/srv/my site"], "web", "www-data", True) == "chown -R -- web:www-data '/srv/my site'"
    assert commands.mode_command(["/srv/a", "/srv/b c"], "755", True) == "chmod -R -- 755 /srv/a '/srv/b c'"


def test_only_the_group_can_be_changed() -> None:
    assert commands.owner_command(["/srv"], "", "www-data", False) == "chown -- :www-data /srv"


@pytest.mark.parametrize("path", ["a; rm -rf /", "$(reboot)", "`reboot`", "-rf", "it's", "new\nline", "*"])
def test_a_hostile_file_name_stays_one_argument(path: str, tmp_path: Path) -> None:
    import shlex

    words = shlex.split(commands.copy_command(path, "/target"))
    assert words == ["cp", "-a", "--", path, "/target"]
    assert shlex.split(commands.search_command(path, path))[3] == path


@pytest.mark.parametrize("name,good", [("www-data", True), ("web.user", True), ("_apt", True), ("user@corp.example", True), ("DOMAIN\\user", True), ("domain users", True), ("$(id)", True), ("-R", False), ("", False), ("a:b", False), ("a\nb", False), ("a\0b", False), ("a\x7fb", False)])
def test_owner_names_are_checked(name: str, good: bool) -> None:
    assert commands.valid_name(name) is good


@pytest.mark.parametrize("mode,good", [("755", True), ("0644", True), ("u+x", True), ("go-rwx,u=rw", True), ("a+X", True), ("888", False), ("rm", False), ("; ls", False), ("", False), ("-R", False)])
def test_modes_are_checked(mode: str, good: bool) -> None:
    assert commands.valid_mode(mode) is good


class Scripted(RemoteConnection):
    """A server that answers every command with result; an upload only notes the size that arrived, so a transfer can check it."""

    def __init__(self, result: CommandResult) -> None:
        self.result: CommandResult = result
        self.sizes: dict[str, int] = {}

    def connect(self) -> None: ...
    def close(self) -> None: ...
    def check(self) -> None: ...
    def home(self) -> str:
        return "/"

    def list_folder(self, path: str) -> list[RemoteEntry]:
        return []

    def rename(self, old: str, new: str) -> None: ...
    def make_folder(self, path: str) -> None: ...
    def delete(self, path: str, is_folder: bool) -> None: ...
    def download(self, remote: str, local: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None: ...
    def upload(self, local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
        self.sizes[remote] = os.path.getsize(local)

    @override
    def file_size(self, path: str) -> int:
        return self.sizes.get(path, 0)

    @override
    def run(self, command: str) -> CommandResult:
        return self.result


def test_a_refused_command_comes_back_in_the_servers_words() -> None:
    with pytest.raises(RemoteActionFailed, match="Operation not permitted"):
        commands.run_checked(Scripted(CommandResult(1, "", "Operation not permitted\n")), "chown")


def test_search_keeps_what_it_found_when_one_folder_could_not_be_read() -> None:
    assert commands.search(Scripted(CommandResult(1, "/a/x.txt\n/b/x.txt\n", "")), "/", "x*") == ["/a/x.txt", "/b/x.txt"]


def test_changing_the_owner_needs_root_and_chown() -> None:
    assert commands.can_change_owner(Capabilities(shell=True, tools=frozenset({"chown"}), is_root=True))
    assert not commands.can_change_owner(Capabilities(shell=True, tools=frozenset({"chown"}), is_root=False))
    assert not commands.can_change_owner(Capabilities(shell=False, tools=frozenset({"chown"}), is_root=True))


def run_on_worker(work: Callable[[], int]) -> list[object]:
    answers: list[object] = []
    finished = threading.Event()

    def deliver(call: Callable[[], None]) -> None:
        call()
        finished.set()

    worker = Worker(deliver)
    worker.submit(work, answers.append, answers.append)
    assert finished.wait(5)
    worker.stop()
    return answers


def test_a_job_answers_with_its_result() -> None:
    assert run_on_worker(lambda: 5) == [5]


def test_a_failed_job_answers_with_its_error() -> None:
    def fail() -> int:
        raise RemoteActionFailed("no")

    answers = run_on_worker(fail)
    assert len(answers) == 1 and isinstance(answers[0], RemoteActionFailed)


def test_nothing_is_replaced_without_asking(tmp_path: Path) -> None:
    import subprocess

    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "b").write_text("b", encoding="utf-8")
    target = str(tmp_path / "b")
    line = commands.without_overwriting(target, commands.move_command(str(tmp_path / "a"), target))
    result = subprocess.run(["/bin/sh", "-c", line], capture_output=True, text=True, check=False)
    assert result.returncode == 1 and commands.TARGET_EXISTS_TEXT in result.stderr
    assert (tmp_path / "b").read_text(encoding="utf-8") == "b"


def test_a_folder_that_starts_with_a_hyphen_is_never_an_expression_of_find() -> None:
    import shlex

    assert shlex.split(commands.search_command("-delete", "*"))[1] == "./-delete"
