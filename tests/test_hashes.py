"""Block FE without a screen: checksums through the shell, SFTP's check-file and FTP's HASH and older commands, the choice of the way,
checksums of files on this computer read in pieces, the size of folders on both sides, stopping, and the checksum step of compare.
Only the fake servers are used; a shell command is answered by the test itself, never run on this computer (OR-26)."""

import asyncio
import gc
import hashlib
import os
import shlex
import subprocess
import threading
import time
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import cast

import asyncssh
import pytest

from chrono_ftp.connections.base import FIND_PRINTF, Capabilities, RemoteEntry, checksum_from
from chrono_ftp.connections.ftp import FtpConnection, checksum_of_reply, hash_algorithms
from chrono_ftp.connections import ssh
from chrono_ftp.connections.ssh import OTHER_CHECKSUM_TEXT, SshConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import NoMoreLogins, RemoteActionFailed
from chrono_ftp.engine import checksums, serverinfo, sizes
from chrono_ftp.engine.checksums import Checksum
from chrono_ftp.engine.compare import Difference, compare_contents, compare_folders
from chrono_ftp.engine.sizes import Count
from tests.fakeservers import NOT_AS_ROOT, PASSWORD, SLOW_HASHING, USER, ftp_server, ssh_server
from tests.test_server_actions import connected_ssh

# --- Version ------------------------------------------------------------
__version__ = "test_hashes.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Initial: block FE, hashes and sizes without a shell (FE-01 to FE-03); review round 1: an FTP checksum on a login of its
#          own and its stop, the tab's own login where the server takes no further one, a check-file request stopped, a file the server
#          may not read, answers split on line ends only; review round 2: the whole process group stopped, a channel closed after its
#          connection, a name with a space sent quoted; review round 3: a group is ended only where its shell printed itself as the
#          leader, a plain number above 1

CONTENT: bytes = b"the same bytes on both sides\n"


def sha(data: bytes, algorithm: str = "sha256") -> str:
    return hashlib.new(algorithm, data).hexdigest()


def always() -> bool:
    return True


def ftp_of(port: int) -> FtpConnection:
    connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
    connection.connect()
    return connection


PID_PREFIX: str = ssh.GROUP_LINE
STAND_IN_PID: int = 424242  # the shell number the stand-in tells; nothing is ever signalled here


class Shell:
    """A stand-in for the server's shell: the probe names the tools a test gives, every other command is answered by answer and noted.
    Nothing runs on this computer."""

    def __init__(self, tools: tuple[str, ...], answer: Callable[[str], str]) -> None:
        self.tools: tuple[str, ...] = tools
        self.answer: Callable[[str], str] = answer
        self.heard: list[str] = []

    async def __call__(self, process: asyncssh.SSHServerProcess[str]) -> None:
        command = process.command or ""
        if command.startswith("echo chrono-ftp-probe"):
            process.stdout.write("chrono-ftp-probe\nLinux\n1000\n" + "".join(f"tool:{tool}\n" for tool in self.tools))
            process.exit(0)
            return
        if command.startswith(PID_PREFIX):  # a stoppable command: its shell tells its number first, as a real one does
            command = command.removeprefix(PID_PREFIX)
            process.stdout.write(f"{ssh.PID_MARK}{STAND_IN_PID} {STAND_IN_PID}\n")
        self.heard.append(command)
        if command == "wait":
            await asyncio.sleep(60)  # a command that runs long: only a stop ends it
        process.stdout.write(self.answer(command))
        process.exit(0)


def paths_of(command: str) -> list[str]:
    """The quoted paths after "for f in", as the shell would read them."""
    words = shlex.split(command.split("; do ", 1)[0].removeprefix("for f in "))
    return words


def answered_in(root: Path, algorithm: str) -> Callable[[str], str]:
    """The answer of shell_command for files inside root, as the server's shell gives it."""

    def answer(command: str) -> str:
        lines: list[str] = []
        for path in paths_of(command):
            real = root / path.lstrip("/")
            if not real.exists():
                lines.append(f"no {checksums.GONE_TEXT}")
            elif not real.is_file():
                lines.append(f"no {checksums.NOT_A_FILE_TEXT}")
            else:
                lines.append(f"ok {sha(real.read_bytes(), algorithm)}")
        return "\n".join(lines) + "\n"

    return answer


# --- the choice of the way (FE-01, D-016) ------------------------------------------


def test_the_shell_comes_first_then_the_protocol_and_otherwise_the_reason() -> None:
    both = Capabilities(shell=True, tools=frozenset({"sha256sum"}), checksums=frozenset({"sha256", "md5"}))
    assert checksums.way_of(both, "sha256") == checksums.SHELL
    assert checksums.way_of(both, "md5") == checksums.PROTOCOL
    assert checksums.way_of(both, "sha512") == ""
    assert checksums.algorithms_of(both) == ("md5", "sha256")
    assert checksums.preferred_algorithm(both) == "sha256"
    assert checksums.preferred_algorithm(Capabilities(checksums=frozenset({"md5"}))) == "md5"
    assert checksums.why_no_checksum(both, False) == ""
    assert checksums.why_no_checksum(Capabilities(), True) == checksums.NO_WAY_ON_FTP_TEXT
    assert checksums.why_no_checksum(Capabilities(), False) == checksums.NO_WAY_WITHOUT_SHELL_TEXT
    assert checksums.why_no_checksum(Capabilities(shell=True, tools=frozenset({"find"})), False) == checksums.NO_WAY_TEXT
    assert checksums.way_of(Capabilities(shell=False, tools=frozenset({"sha256sum"})), "sha256") == ""  # a tool counts only with a shell


def test_server_information_names_the_checksums_by_shell_or_protocol_also_over_ftp() -> None:
    both = Capabilities(shell=True, tools=frozenset({"sha256sum"}), checksums=frozenset({"md5"}))
    assert dict(serverinfo.ability_lines(both, False))["Checksums on the server"] == "md5, sha256"
    assert dict(serverinfo.ability_lines(Capabilities(checksums=frozenset({"sha1"})), True))["Checksums on the server"] == "sha1"
    assert dict(serverinfo.ability_lines(Capabilities(), True))["Checksums on the server"] == "no"


def test_a_checksum_from_a_server_is_checked_as_input() -> None:
    good = sha(b"x")
    assert checksum_from(good.upper(), "sha256") == good
    for bad in (good[:-1], good + "0", "z" * 64, "", " "):
        assert checksum_from(bad, "sha256") is None, bad
    assert checksum_from(good, "nothing") is None


# --- FTP: HASH and the older commands (FE-01) ----------------------------------------


def test_the_hash_line_of_feat_and_the_replies_are_read() -> None:
    assert hash_algorithms(["MDTM", "HASH SHA-256*;SHA-1;MD5;CRC32", "SIZE"]) == {"sha256": "SHA-256", "sha1": "SHA-1", "md5": "MD5"}
    assert hash_algorithms(["HASH"]) == {}
    digest = sha(b"x")
    assert checksum_of_reply(f"213 SHA-256 0-1 {digest} name with spaces", "sha256", True) == digest
    assert checksum_of_reply(f"213 SHA-256 {digest}", "sha256", True) == digest  # an older draft, without the range
    assert checksum_of_reply(f"213-Computing\n213 SHA-256 0-1 {digest} a", "sha256", True) == digest
    assert checksum_of_reply(f'251 "a b" {sha(b"x", "md5")}', "md5", False) == sha(b"x", "md5")
    with pytest.raises(RemoteActionFailed):
        checksum_of_reply(f"213 MD5 0-1 {sha(b'x', 'md5')} a", "sha256", True)  # another algorithm is never taken for this one
    for odd in ("213 SHA-256 0-1 nothing a", "", "250", "213 SHA-256 0-1 " + "f" * 63):
        with pytest.raises(RemoteActionFailed):
            checksum_of_reply(odd, "sha256", True)


def test_ftp_hash_computes_on_the_server_with_the_algorithm_chosen(tmp_path: Path) -> None:
    (tmp_path / "a b.txt").write_bytes(CONTENT)
    heard: list[str] = []
    with ftp_server(tmp_path, hashes="SHA-256*;SHA-1;MD5", hash_heard=heard.append) as port:
        connection = ftp_of(port)
        try:
            assert connection.capabilities.checksums == frozenset({"sha256", "sha1", "md5"})
            assert connection.checksum("/a b.txt", "sha1") == sha(CONTENT, "sha1")
            assert connection.checksum("/a b.txt", "sha256") == sha(CONTENT)
            assert heard == ['"/a b.txt"', '"/a b.txt"']  # quoted where it holds a space, as the reference program sends it
            with pytest.raises(RemoteActionFailed):
                connection.checksum("/gone.txt", "sha256")  # refused for that file only
            assert connection.list_folder("/")  # the connection goes on
        finally:
            connection.close()


def test_ftp_without_hash_uses_an_older_command_and_refuses_an_answer_that_is_no_checksum(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(CONTENT)
    with ftp_server(tmp_path, checksum_commands=("XSHA256", "XMD5")) as port:
        connection = ftp_of(port)
        try:
            assert connection.capabilities.checksums == frozenset({"sha256", "md5"})
            assert connection.checksum("/a.txt", "md5") == sha(CONTENT, "md5")
        finally:
            connection.close()
    with ftp_server(tmp_path, hashes="SHA-256*", hash_reply="213 SHA-256 0-3 not-a-checksum a.txt") as port:
        connection = ftp_of(port)
        try:
            with pytest.raises(RemoteActionFailed):
                connection.checksum("/a.txt", "sha256")
        finally:
            connection.close()


def test_ftp_that_offers_nothing_computes_nothing_and_says_why(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(CONTENT)
    with ftp_server(tmp_path) as port:
        connection = ftp_of(port)
        try:
            assert connection.capabilities.checksums == frozenset()
            assert checksums.why_no_checksum(connection.capabilities, True) == checksums.NO_WAY_ON_FTP_TEXT
            with pytest.raises(RemoteActionFailed):
                checksums.remote_checksums(connection, ["/a.txt"], "sha256", always, lambda _found: None)
        finally:
            connection.close()


# --- SFTP: check-file (FE-01) -------------------------------------------------------


def test_sftp_check_file_computes_on_the_server_without_a_shell(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(CONTENT)
    with ssh_server(tmp_path, shell=False, check_file=True) as server:
        connection = connected_ssh(server.port)
        try:
            assert not connection.capabilities.shell
            assert "sha512" in connection.capabilities.checksums
            assert checksums.way_of(connection.capabilities, "sha256") == checksums.PROTOCOL
            found: list[Checksum] = []
            assert checksums.remote_checksums(connection, ["/a.txt", "/gone.txt"], "sha512", always, found.append)
            assert found[0] == Checksum("/a.txt", sha(CONTENT, "sha512"))
            assert found[1].value == "" and found[1].problem  # refused for that file only
            assert "check-file" in connection.server_info().offers
        finally:
            connection.close()


def test_sftp_check_file_that_answers_with_another_algorithm_is_refused(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(CONTENT)
    with ssh_server(tmp_path, shell=False, check_file="other") as server:
        connection = connected_ssh(server.port)
        try:
            with pytest.raises(RemoteActionFailed) as refused:
                connection.checksum("/a.txt", "sha256")
            assert str(refused.value) == OTHER_CHECKSUM_TEXT.format(used="md5", wanted="sha256")
        finally:
            connection.close()


def test_sftp_without_check_file_computes_nothing_without_a_shell(tmp_path: Path) -> None:
    with ssh_server(tmp_path, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            assert connection.capabilities.checksums == frozenset()
            assert checksums.why_no_checksum(connection.capabilities, False) == checksums.NO_WAY_WITHOUT_SHELL_TEXT
        finally:
            connection.close()


# --- the shell: one command for many files (FE-01, OR-5) ------------------------------


def test_with_a_shell_many_files_are_one_command_and_odd_names_stay_whole(tmp_path: Path) -> None:
    names = ["plain.txt", "a b.txt", "it's.txt", "-dash.txt", "new\nline.txt"]
    for number, name in enumerate(names):
        (tmp_path / name).write_bytes(CONTENT * (number + 1))
    (tmp_path / "folder").mkdir()
    shell = Shell(("sha256sum",), answered_in(tmp_path, "sha256"))
    with ssh_server(tmp_path, commands=shell) as server:
        connection = connected_ssh(server.port)
        try:
            assert checksums.way_of(connection.capabilities, "sha256") == checksums.SHELL
            found: list[Checksum] = []
            paths = [f"/{name}" for name in names] + ["/folder"]
            assert checksums.remote_checksums(connection, paths, "sha256", always, found.append)
            assert len(shell.heard) == 1  # one command for all of them
            assert paths_of(shell.heard[0]) == paths  # every path whole, through shlex.quote
            assert [item.value for item in found[:-1]] == [sha(CONTENT * (number + 1)) for number in range(len(names))]
            assert found[-1].problem == checksums.NOT_A_FILE_TEXT
        finally:
            connection.close()


def test_a_shell_answer_that_is_no_checksum_fails_only_its_own_file() -> None:
    good = sha(b"x")
    found = checksums.checksums_from_output(f"ok {good}\nok nonsense\nno Permission denied\n", ["a", "b", "c"], "sha256")
    assert found == [Checksum("a", good), Checksum("b", problem=checksums.ODD_ANSWER_TEXT), Checksum("c", problem="Permission denied")]
    assert all(item.problem == checksums.ODD_ANSWER_TEXT for item in checksums.checksums_from_output(f"ok {good}\n", ["a", "b"], "sha256"))


def test_many_files_are_cut_into_batches_that_fit_a_command_line() -> None:
    paths = [f"/folder/file-{number:05}.txt" for number in range(250)]
    groups = checksums.batches(paths)
    assert [path for group in groups for path in group] == paths
    assert all(len(group) <= checksums.BATCH_FILES for group in groups) and len(groups) == 3


# --- this computer: in pieces (FE-01) ---------------------------------------------------


def test_a_file_here_is_read_in_pieces_and_a_stop_ends_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "a.bin"
    path.write_bytes(CONTENT * 100)
    monkeypatch.setattr(checksums, "LOCAL_PIECE_BYTES", 64)
    asked: list[bool] = []

    def counted() -> bool:
        asked.append(True)
        return True

    assert checksums.local_checksum(str(path), "md5", counted) == sha(CONTENT * 100, "md5")
    assert len(asked) == -(-len(CONTENT * 100) // 64)  # once per piece: never the whole file at once
    assert checksums.local_checksum(str(path), "md5", lambda: False) is None


def test_files_here_that_can_not_be_hashed_fail_only_themselves(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(CONTENT)
    cast(Callable[[Path], None], getattr(os, "mkfifo"))(tmp_path / "pipe")  # it would keep the reading waiting (POSIX only)
    (tmp_path / "inside").mkdir()
    (tmp_path / "inside" / "b.txt").write_bytes(b"b")
    (tmp_path / "loop").symlink_to(tmp_path / "inside")
    listed = checksums.local_files(str(tmp_path), ["a.txt", "pipe", "inside", "loop", "gone"], always)
    assert listed is not None
    files, problems = listed
    assert files == ["a.txt", "pipe", os.path.join("inside", "b.txt"), "loop"]
    assert [problem.path for problem in problems] == ["gone"]
    found: list[Checksum] = []
    assert checksums.local_checksums(str(tmp_path), files, "sha256", always, found.append)
    assert [item.value for item in found] == [sha(CONTENT), "", sha(b"b"), ""]
    assert found[1].problem == checksums.NOT_A_FILE_TEXT and found[3].problem == checksums.NOT_A_FILE_TEXT  # a link to a folder is not followed


@NOT_AS_ROOT
def test_an_unreadable_file_here_says_why(tmp_path: Path) -> None:
    (tmp_path / "secret").write_bytes(b"x")
    (tmp_path / "secret").chmod(0)
    found: list[Checksum] = []
    checksums.local_checksums(str(tmp_path), ["secret"], "sha256", always, found.append)
    assert found[0].value == "" and "could not be read" in found[0].problem


def test_the_files_inside_folders_on_the_server_are_found_without_following_links(tmp_path: Path) -> None:
    (tmp_path / "top").mkdir()
    (tmp_path / "top" / "deep").mkdir()
    (tmp_path / "top" / "deep" / "c.txt").write_bytes(b"c")
    (tmp_path / "top" / "a.txt").write_bytes(b"a")
    (tmp_path / "top" / "link").symlink_to(tmp_path / "top" / "deep")
    with ssh_server(tmp_path, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            entries = [entry for entry in connection.list_folder("/") if entry.name == "top"]
            listed = checksums.remote_files(connection, "/", entries, always)
            assert listed is not None
            assert listed[0] == ["top/a.txt", "top/deep/c.txt"]
            assert [problem.path for problem in listed[1]] == ["top/link"]
            assert checksums.remote_files(connection, "/", entries, lambda: False) is None
        finally:
            connection.close()


# --- sizes (FE-02, FE-03) --------------------------------------------------------------------


def tree(root: Path) -> None:
    (root / "photos" / "2025").mkdir(parents=True)
    (root / "photos" / "a.jpg").write_bytes(b"1" * 100)
    (root / "photos" / "2025" / "b.jpg").write_bytes(b"2" * 50)
    (root / "photos" / "away").symlink_to(root / "elsewhere")
    (root / "elsewhere").mkdir()
    (root / "elsewhere" / "big").write_bytes(b"3" * 1000)


def test_a_folder_here_is_counted_without_following_links(tmp_path: Path) -> None:
    tree(tmp_path)
    found = sizes.local_count(str(tmp_path / "photos"), always)
    link_size = os.lstat(tmp_path / "photos" / "away").st_size
    assert found == Count(150 + link_size, 3, 2, True)  # the link is a file of its own size; photos and 2025 are the folders
    assert sizes.local_count(str(tmp_path / "photos"), lambda: False) is None


@NOT_AS_ROOT
def test_a_folder_here_that_can_not_be_read_in_full_is_at_least(tmp_path: Path) -> None:
    tree(tmp_path)
    (tmp_path / "photos" / "2025").chmod(0)
    try:
        found = sizes.local_count(str(tmp_path / "photos"), always)
    finally:
        (tmp_path / "photos" / "2025").chmod(0o755)
    assert found is not None and not found.complete and found.size >= 100


def test_a_folder_on_the_server_without_a_shell_is_counted_by_its_listings_and_a_stop_ends_it(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    tree(root)
    with ssh_server(root, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            link_size = next(entry.size for entry in connection.list_folder("/photos") if entry.name == "away")  # as the listing tells it
            entries = connection.list_folder("/")
            counted: dict[str, Count] = {}
            assert sizes.remote_counts(connection, "/", entries, always, counted.__setitem__)
            photos = counted["photos"]
            assert photos == Count(150 + link_size, 3, 2, True)  # the link to a folder is a file, never followed
            assert counted["elsewhere"] == Count(1000, 1, 1, True)
            assert not sizes.remote_counts(connection, "/", entries, lambda: False, counted.__setitem__)
        finally:
            connection.close()


def test_with_a_shell_the_folders_are_counted_by_one_command_and_its_answer_is_checked(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").mkdir()
    (root / "-b c").mkdir()
    (root / "file.txt").write_bytes(b"12345")
    answers = {"/-b c": "3 2 1500 0", "/a": "1 1 7 1"}

    def answer(command: str) -> str:
        """One line per folder, in the order of the command: as the server's find and awk would give them."""
        starts = [shlex.split(part.split(" '(' ", 1)[0])[0] for part in command.split("{ find ")[1:]]
        return "".join(answers[start] + "\n" for start in starts)

    shell = Shell(("find", FIND_PRINTF, "awk"), answer)
    with ssh_server(root, commands=shell) as server:
        connection = connected_ssh(server.port)
        try:
            assert sizes.can_count_on_server(connection.capabilities)
            entries = connection.list_folder("/")
            counted: dict[str, Count] = {}
            assert sizes.remote_counts(connection, "/", entries, always, counted.__setitem__)
            assert len(shell.heard) == 1  # one command for every folder (OR-5)
            assert "'./-b c'" not in shell.heard[0] and "'/-b c'" in shell.heard[0]  # an absolute path never reads as an option
            assert counted == {"file.txt": Count(5, 1, 0, True), "-b c": Count(1500, 3, 2, True), "a": Count(7, 1, 1, False)}
        finally:
            connection.close()
    assert sizes.counts_from_output("3 2 1500 0\nnonsense\n", 2) == [Count(1500, 3, 2, True), None]
    assert sizes.counts_from_output("3 2 1500 0\n", 2) == [None, None]  # lines that don't match the folders asked
    assert sizes.counts_from_output("1 1 99999999999999999999 0\n", 1) == [None]  # a number no size can be (OR-15)
    assert "find ./-x " in sizes.count_command(["-x"])  # find has no --: a path never reads as an option


def test_a_long_command_on_the_server_ends_as_soon_as_it_is_stopped(tmp_path: Path) -> None:
    shell = Shell(("find",), lambda _command: "")
    with ssh_server(tmp_path, commands=shell) as server:
        connection = connected_ssh(server.port)
        try:
            started = time.monotonic()
            stop_at = started + 0.5
            assert connection.run_stoppable("wait", lambda: time.monotonic() < stop_at) is None
            assert time.monotonic() - started < 5
            assert shell.heard[-1] == f"kill -TERM -{STAND_IN_PID} 2>/dev/null"  # the whole process group, in a form every sh takes
            result = connection.run_stoppable("quick", always)
            assert result is not None and result.status == 0  # the connection goes on
        finally:
            connection.close()


# --- the checksum step of compare (FE-01, D-027) ------------------------------------------------


def test_compare_contents_works_without_a_shell_where_the_server_computes_checksums(tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    for side, other in ((here, b"AAAA"), (there, b"BBBB")):
        (side / "same.txt").write_bytes(CONTENT)
        (side / "other.txt").write_bytes(other)
    os.utime(here / "same.txt", (1_600_000_000, 1_600_000_000))
    os.utime(here / "other.txt", (1_700_000_000, 1_700_000_000))  # newer here
    os.utime(there / "other.txt", (1_600_000_000, 1_600_000_000))
    with ssh_server(there, shell=False, check_file=True) as server:
        connection = connected_ssh(server.port)
        try:
            found = compare_folders(connection, str(here), "/")
            assert {row.name: row.difference for row in found} == {"same.txt": Difference.DATE, "other.txt": Difference.DATE}
            result = compare_contents(connection, str(here), "/", found, "sha256", always)
            assert result is not None
            assert result.same == 1 and result.unknown == 0
            assert [(row.name, row.difference) for row in result.found] == [("other.txt", Difference.CONTENT_NEWER_LOCAL)]
            assert compare_contents(connection, str(here), "/", found, "sha256", lambda: False) is None
        finally:
            connection.close()


def test_compare_contents_over_ftp_hash(tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    (here / "same.txt").write_bytes(CONTENT)
    (there / "same.txt").write_bytes(CONTENT)
    os.utime(here / "same.txt", (1_600_000_000, 1_600_000_000))
    with ftp_server(there, hashes="SHA-256*;MD5") as port:
        connection = ftp_of(port)
        try:
            found = compare_folders(connection, str(here), "/")
            result = compare_contents(connection, str(here), "/", found, checksums.preferred_algorithm(connection.capabilities), always)
            assert result is not None and result.found == [] and result.same == 1
        finally:
            connection.close()


def test_a_file_or_a_link_counts_as_its_listing_says() -> None:
    assert sizes.of_entry(RemoteEntry("a", False, 10)) == Count(10, 1, 0, True)
    assert sizes.of_entry(RemoteEntry("b", False, 0, size_known=False)) == Count(0, 1, 0, False)  # a size the server did not tell
    assert not sizes.walked(RemoteEntry("l", True, 5, is_link=True)) and sizes.walked(RemoteEntry("f", True))


# --- review round 1 --------------------------------------------------------------------------------


def stops_after(seconds: float) -> Callable[[], bool]:
    end = time.monotonic() + seconds
    return lambda: time.monotonic() < end


def test_an_ftp_checksum_runs_on_a_login_of_its_own_so_the_tab_never_waits_and_a_stop_ends_it(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(CONTENT)
    hashing = threading.Event()
    with ftp_server(tmp_path, hashes="SHA-256*", hash_seconds=30, command_heard=lambda command: hashing.set() if command == "HASH" else None) as port:
        connection = ftp_of(port)
        ended: list[bool] = []
        stop = threading.Event()
        try:
            worker = threading.Thread(target=lambda: ended.append(checksums.remote_checksums(connection, ["/big.bin"], "sha256", lambda: not stop.is_set(), lambda _found: None)))
            worker.start()
            assert hashing.wait(10)  # the server is computing it now (it answers after half a minute)
            started = time.monotonic()
            assert connection.list_folder("/")  # the tab's own requests go on meanwhile
            assert time.monotonic() - started < 3
            assert worker.is_alive()
            stop.set()
            worker.join(5)
            assert not worker.is_alive() and ended == [False]
        finally:
            stop.set()
            connection.close()


def test_without_a_further_login_the_tab_says_it_waits_and_a_stop_still_ends_the_checksum(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(CONTENT)
    with ftp_server(tmp_path, hashes="SHA-256*", hash_seconds=30, most_logins=1) as port:
        connection = ftp_of(port)
        try:
            told: list[str] = []
            started = time.monotonic()
            assert not checksums.remote_checksums(connection, ["/big.bin"], "sha256", stops_after(0.5), lambda _found: None, told.append)
            assert time.monotonic() - started < 5
            assert told == [checksums.TAB_WAITS_TEXT]
            limit = time.monotonic() + 10
            while True:  # the tab logs in again with its next request, once the server let the old login go
                try:
                    assert connection.list_folder("/")
                    break
                except NoMoreLogins:
                    assert time.monotonic() < limit
                    time.sleep(0.1)
        finally:
            connection.close()


def test_a_check_file_request_runs_on_a_channel_of_its_own_and_stops_without_waiting_for_the_server(tmp_path: Path) -> None:
    (tmp_path / "big.bin").write_bytes(CONTENT)
    SLOW_HASHING.clear()
    with ssh_server(tmp_path, shell=False, check_file="slow") as server:
        connection = connected_ssh(server.port)
        stop = threading.Event()
        ended: list[bool] = []
        try:
            worker = threading.Thread(target=lambda: ended.append(checksums.remote_checksums(connection, ["/big.bin"], "sha256", lambda: not stop.is_set(), lambda _found: None)))
            worker.start()
            assert SLOW_HASHING.wait(10)  # the server is hashing now (it answers after a minute)
            started = time.monotonic()
            assert connection.list_folder("/")  # the tab's own request never waits behind the hash
            assert time.monotonic() - started < 3
            stop.set()
            worker.join(5)
            assert not worker.is_alive() and ended == [False]
            assert connection.list_folder("/")  # the connection goes on
        finally:
            stop.set()
            connection.close()


@NOT_AS_ROOT
def test_a_file_the_server_may_not_read_says_so_calmly_without_its_path(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_bytes(b"x")
    (tmp_path / "secret.txt").chmod(0)
    (tmp_path / "open.txt").write_bytes(b"y")
    paths = [str(tmp_path / "secret.txt"), str(tmp_path / "open.txt")]
    answer = subprocess.run(["/bin/sh", "-c", checksums.shell_command(paths, "sha256")], capture_output=True, text=True, check=False, timeout=10)  # temporary files only
    found = checksums.checksums_from_output(answer.stdout, paths, "sha256")
    assert found[0].problem == checksums.NOT_READABLE_TEXT and str(tmp_path) not in answer.stdout
    assert found[1].value == sha(b"y")


def test_a_channel_closed_after_its_connection_leaves_no_coroutine_behind(tmp_path: Path) -> None:
    with ssh_server(tmp_path, shell=False, check_file=True) as server:
        connection = connected_ssh(server.port)
        channel = connection.login_of_its_own()
        connection.close()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            channel.close()
            gc.collect()
        assert [str(item.message) for item in caught if issubclass(item.category, RuntimeWarning)] == []


def test_a_carriage_return_in_an_answer_never_shifts_the_files() -> None:
    good, other = sha(b"x"), sha(b"y")
    found = checksums.checksums_from_output(f"ok {good}\nno odd\rreason\u2028here\nok {other}\n", ["a", "b", "c"], "sha256")
    assert [item.value for item in found] == [good, "", other]
    assert sizes.counts_from_output("1 1 5 0\r\n", 1) == [Count(5, 1, 1, True)]


@pytest.mark.parametrize(
    ("line", "command"),
    [
        ("chrono-ftp-pid:4242 4242", "kill -TERM -4242 2>/dev/null"),
        ("chrono-ftp-pid:1 1", ""),  # -1 would reach every process of the login
        ("chrono-ftp-pid:0 0", ""),  # 0 would be the group of the killing shell
        ("chrono-ftp-pid:4242 17", ""),  # the shell is not the leader of its group: another group is never ended
        ("chrono-ftp-pid:4242 ", ""),  # the group could not be told
        ("chrono-ftp-pid:", ""),
        ("chrono-ftp-pid:04242 04242", ""),  # no plain decimal
        ("chrono-ftp-pid:-5 -5", ""),
        ("chrono-ftp-pid:42a 42a", ""),
        ("chrono-ftp-pid:\u0664\u0662 \u0664\u0662", ""),  # digits of another script
        ("chrono-ftp-pid:99999999999 99999999999", ""),
        ("some output", ""),  # a shell that told nothing
        ("", ""),
    ],
)
def test_a_group_is_ended_only_where_its_shell_is_its_leader(line: str, command: str) -> None:
    assert ssh.group_kill_command(line) == command

