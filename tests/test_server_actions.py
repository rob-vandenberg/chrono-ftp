"""Block FM without a screen: rights and their chmod, Properties of several files, the names of users and groups, links, copies on the
server, my public key on the server and the password on the server. A key and a password are tried on the fake servers only (OR-26)."""

import os
import shlex
import stat
import subprocess
from pathlib import Path
from typing import override

import asyncssh
import pytest

from chrono_ftp.connections import trust
from chrono_ftp.connections import ssh
from chrono_ftp.connections.base import Capabilities, CommandResult, Limit, Progress, RemoteEntry, UnknownServer
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.connections.passwordchange import AskedTooOften, PasswordTalk, shown_answer
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ConnectionLost, LocalActionFailed, NameTaken, RemoteActionFailed
from chrono_ftp.connections.base import OLD_SUFFIX
from chrono_ftp.engine import commands, keyinstall, localfiles, properties, rights
from chrono_ftp.engine.terminal import CANT_ENTER_TEXT, shell_start
from tests.fakeservers import NOT_AS_ROOT, PASSWORD, USER, Passwd, ftp_server, ssh_server
from tests.test_engine import Scripted

# --- Version ------------------------------------------------------------
__version__ = "test_server_actions.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: A copy the person agreed to replace drops only the guard, and still quotes every path (FI-03, OR-19)
# v0.0.37: Initial: block FM, Properties and server actions (D-060 to D-063); review round 1: rights for everything inside through
#          find, never X; a key comment beyond ASCII; a link never lost; a half copy removed; names of directories; a tree listed first;
#          review round 2: a link that leads nowhere is a taken name; free temporary names; a refusal about authorized_keys; passwd
#          that never says it is done; review round 3: a half copy with closed folders, a key install without a temporary folder, a
#          line lost after the new password; review round 4: a common-word password, a link change that can't tidy up; review round
#          5: any name of valid form, a minute per answer of passwd


def connected_ssh(port: int, password: str = PASSWORD) -> SshConnection:
    settings = Connection(name="T", host="127.0.0.1", port=port, user=USER)
    probe = SshConnection(settings, password)
    try:
        probe.connect()
        return probe
    except UnknownServer as unknown:
        probe.close()
        trust.remember_host_key(unknown.to_remember)
    connection = SshConnection(settings, password)
    connection.connect()
    return connection


# --- rights (FM-02, FM-03) ---------------------------------------------------


def test_octal_and_boxes_go_both_ways() -> None:
    assert rights.octal_text(rights.rights_of(0o4755)) == "4755"
    assert rights.from_octal("644") == rights.rights_of(0o644)
    assert rights.from_octal("0644") == rights.rights_of(0o644)
    for bad in ("888", "12", "abc", "75555", "", " 7 5 5"):
        assert rights.from_octal(bad) is None, bad


def test_a_box_where_the_files_differ_says_leave_and_has_no_octal() -> None:
    found = rights.common([0o644, 0o664])
    assert found[4] is None  # group write differs
    assert found[0] is True and found[8] is False
    assert rights.octal_text(found) == ""
    assert rights.common([0o644, None])[0] is None  # a file whose rights are unknown


def test_only_the_boxes_that_are_set_change_a_file() -> None:
    leave_all_but_others_read = tuple(False if place == 6 else None for place in range(12))
    assert rights.applied(0o664, leave_all_but_others_read) == 0o660
    assert rights.applied(0o644, leave_all_but_others_read) == 0o640
    assert rights.applied(None, leave_all_but_others_read) is None  # its own rights are needed and unknown: left as it is
    assert rights.applied(None, rights.rights_of(0o700)) == 0o700


@pytest.mark.parametrize("text", ["0755", "644", "4755", "0"])
def test_rights_from_a_listing_are_read_and_anything_else_is_unknown(text: str) -> None:
    assert rights.mode_number(text) == int(text, 8)


@pytest.mark.parametrize("text", ["", "rwxr-xr-x", "9", "-1", "1234567", "07a5"])
def test_rights_a_listing_gives_in_another_form_are_unknown(text: str) -> None:
    assert rights.mode_number(text) is None


def test_the_chmod_sets_exactly_the_boxes_that_are_set() -> None:
    assert rights.symbolic(rights.rights_of(0o755)) == "u+rwx,g+rx,g-w,o+rx,o-w,u-s,g-s,-t"
    only_group_write = tuple(True if place == 4 else None for place in range(12))
    assert rights.symbolic(only_group_write) == "g+w"
    assert rights.symbolic(tuple(None for _ in range(12))) == ""


def test_folders_get_run_for_each_class_that_gets_read() -> None:
    assert rights.readers(rights.rights_of(0o640)) == "ug"
    assert rights.readers(rights.rights_of(0o200)) == ""  # nobody reads: nothing to open
    assert "X" not in rights.symbolic(rights.rights_of(0o640))
    line = commands.rights_command(["/srv/a b", "-odd"], "g+r", True, "g")
    assert line == "failed=0; find '/srv/a b' ./-odd ! -type d ! -type l -exec chmod -- g+r {} + || failed=1; find '/srv/a b' ./-odd -depth -type d -exec chmod -- g+r,g+x {} + || failed=1; exit $failed"
    assert commands.rights_command(["/srv"], "g+r", False, "g") == "chmod -- g+r /srv"  # folders get Run only with everything inside


def test_every_chmod_the_window_can_make_passes_the_check_of_a_mode() -> None:
    for mode in (0o755, 0o640, 0o7777, 0, 0o1777):
        assert commands.valid_mode(rights.symbolic(rights.rights_of(mode)))
    assert commands.valid_mode(rights.symbolic(tuple(True if place == 4 else None for place in range(12))))


def tree_in(folder: Path) -> Path:
    """A throwaway tree on this computer: a program file that only its owner may run, a plain file, and a folder."""
    tree = folder / "tree"
    (tree / "inner").mkdir(parents=True)
    (tree / "program").write_text("p", encoding="utf-8")
    (tree / "program").chmod(0o700)
    (tree / "inner" / "f.txt").write_text("x", encoding="utf-8")
    (tree / "inner" / "f.txt").chmod(0o755)
    (tree / "inner").chmod(0o700)
    tree.chmod(0o700)
    return tree


@pytest.mark.parametrize("mode,one_box,folders_run", [(0o640, False, True), (0o040, True, True), (0o600, False, False)], ids=["all boxes", "one box", "folders lose run"])
def test_the_server_and_this_computer_give_a_tree_the_same_rights_and_files_never_gain_run(tmp_path: Path, mode: int, one_box: bool, folders_run: bool) -> None:
    """The command of the server, run by a real sh on one throwaway tree, and the walk on this computer on a copy: the same result.
    A file never gains Run it was not given, a Run switched off stays off, and folders that lose Run still have their inside changed."""
    chosen = tuple(True if place == 3 else None for place in range(12)) if one_box else rights.rights_of(mode)
    server_tree, local_tree = tree_in(tmp_path / "server"), tree_in(tmp_path / "here")
    readers = rights.readers(chosen) if folders_run else ""
    try:
        line = commands.rights_command([str(server_tree)], rights.symbolic(chosen), True, readers)
        assert subprocess.run(["/bin/sh", "-c", line], check=False).returncode == 0
        assert localfiles.change_properties([str(local_tree)], "", "", chosen, True, readers) == []
        on_server, here = modes_of_all(server_tree), modes_of_all(local_tree)
        assert on_server == here
        expected = {
            "all boxes": {".": 0o750, "inner": 0o750, "inner/f.txt": 0o640, "program": 0o640},
            "one box": {".": 0o750, "inner": 0o750, "inner/f.txt": 0o755, "program": 0o740},  # the program does not get 750
            "folders lose run": {".": 0o600, "inner": 0o600, "inner/f.txt": 0o600, "program": 0o600},
        }
        assert on_server == expected["one box" if one_box else "all boxes" if folders_run else "folders lose run"]
    finally:
        for tree in (server_tree, local_tree):
            subprocess.run(["chmod", "-R", "u+rwx", str(tree)], check=False)  # so the throwaway trees can go again


def modes_of_all(tree: Path) -> dict[str, int]:
    """The modes of a tree that may not be readable any more: each folder's own mode is read first, then its owner gets Read and Run
    back for a moment to read what is in it. Read once per tree."""
    found = {".": stat.S_IMODE(os.lstat(tree).st_mode)}
    subprocess.run(["chmod", "u+rx", str(tree)], check=True)
    found["inner"] = stat.S_IMODE(os.lstat(tree / "inner").st_mode)
    found["program"] = stat.S_IMODE(os.lstat(tree / "program").st_mode)
    subprocess.run(["chmod", "u+rx", str(tree / "inner")], check=True)
    found["inner/f.txt"] = stat.S_IMODE(os.lstat(tree / "inner" / "f.txt").st_mode)
    return found


def test_rights_are_shown_as_ls_shows_them() -> None:
    assert rights.letters(0o755) == "rwxr-xr-x"
    assert rights.letters(0o4644) == "rwSr--r--"
    assert rights.letters(0o1777) == "rwxrwxrwt"


# --- Properties of several files (FM-01, FM-04) ------------------------------


def test_one_view_of_several_files_says_where_they_differ() -> None:
    entries = [RemoteEntry("a", False, 10, 100.0, "0644", "anna", "web"), RemoteEntry("b", False, 5, 200.0, "0664", "anna", "staff"), RemoteEntry("c", True, 0, 100.0, "0755", "anna", "web")]
    found = properties.summary(entries)
    assert (found.files, found.folders, found.name) == (2, 1, None)
    assert found.size == 15 and found.modified is None and found.modified_differs
    assert found.owner == "anna" and found.group is None
    assert found.rights[0] is True and found.rights[2] is None
    assert found.kind == "Files and folders"


def test_one_file_is_shown_as_it_is() -> None:
    found = properties.summary([RemoteEntry("a", False, 10, 100.0, "0644", "anna", "web", is_link=True, link_target="/x")])
    assert found.name == "a" and found.kind == "Link" and found.link_target == "/x" and found.rights_known


def test_the_names_of_the_server_are_read_and_a_bad_line_is_left_out() -> None:
    output = "root:x:0:0::/root:/bin/sh\nanna@corp.example:x:1000:1000::/home/anna:/bin/sh\n-rf:x:5:5\nbad\x01name:x\n\nchrono-ftp-groups\nroot:x:0:\ndomain users:x:33:anna\nCORP\\web:x:34:\n"
    assert properties.parse_names(output) == (["anna@corp.example", "root"], ["CORP\\web", "domain users", "root"])
    assert properties.parse_names("garbage") == ([], [])


def test_a_typed_name_is_checked() -> None:
    assert properties.name_allowed("web")
    assert properties.name_allowed("not-in-any-list")  # the lists only suggest: the server refuses an unknown name itself
    assert properties.name_allowed("33")  # an id
    assert not properties.name_allowed("-R")


def test_passwd_gets_a_minute_per_answer_for_a_slow_password_check() -> None:
    """Review round 5 decision: slow PAM or LDAP checks of passwd are waited for, 60 seconds per answer."""
    assert ssh.PASSWORD_ANSWER_SECONDS == 60.0


def test_without_a_shell_the_names_seen_in_the_listing_are_offered() -> None:
    entries = [RemoteEntry("a", False, owner="anna", group="web"), RemoteEntry("b", False, owner="bob", group="web"), RemoteEntry("c", False, owner="-x")]
    assert properties.names_seen(entries) == (["anna", "bob"], ["web"])


def test_the_names_command_lists_users_and_groups_on_the_server() -> None:
    server = Scripted(CommandResult(0, "anna:x:1000\nchrono-ftp-groups\nweb:x:33\n", ""))
    assert properties.server_names(server) == (["anna"], ["web"])


def test_the_group_can_be_changed_without_root_and_the_owner_only_as_root() -> None:
    plain = Capabilities(shell=True, tools=frozenset({"chown"}))
    assert commands.can_change_group(plain) and not commands.can_change_owner(plain)
    assert not commands.can_change_group(Capabilities(tools=frozenset({"chown"})))


# --- Properties on this computer (FM-01 to FM-03) -----------------------------


@NOT_AS_ROOT
def test_several_files_here_keep_the_boxes_left_as_they_are(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "b").write_text("b", encoding="utf-8")
    (tmp_path / "a").chmod(0o664)
    (tmp_path / "b").chmod(0o644)
    others_read_off = tuple(False if place == 6 else None for place in range(12))
    assert localfiles.change_properties([str(tmp_path / "a"), str(tmp_path / "b")], "", "", others_read_off, False) == []
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o660
    assert stat.S_IMODE((tmp_path / "b").stat().st_mode) == 0o640


def test_everything_inside_a_folder_here_with_x_for_folders_and_no_link_followed(tmp_path: Path) -> None:
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    (tree / "inner").mkdir(parents=True)
    outside.write_text("o", encoding="utf-8")
    outside.chmod(0o644)
    (tree / "inner" / "f").write_text("f", encoding="utf-8")
    (tree / "inner" / "link").symlink_to(outside)
    assert localfiles.change_properties([str(tree)], "", "", rights.rights_of(0o640), True, "ug") == []
    assert stat.S_IMODE((tree / "inner" / "f").stat().st_mode) == 0o640
    assert stat.S_IMODE((tree / "inner").stat().st_mode) == 0o750
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644  # the link was not followed


def test_a_refusal_here_is_told_and_the_rest_goes_on(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    refused = localfiles.change_properties([str(tmp_path / "gone"), str(tmp_path / "a")], "", "", rights.rights_of(0o600), False)
    assert len(refused) == 1 and "gone" in refused[0]
    assert stat.S_IMODE((tmp_path / "a").stat().st_mode) == 0o600


@NOT_AS_ROOT
def test_taking_read_away_here_still_changes_everything_inside(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "inner").mkdir(parents=True)
    (tree / "inner" / "f").write_text("f", encoding="utf-8")
    try:
        assert localfiles.change_properties([str(tree)], "", "", rights.rights_of(0o300), True) == []
        tree.chmod(0o700)
        (tree / "inner").chmod(0o700)
        assert stat.S_IMODE((tree / "inner" / "f").stat().st_mode) == 0o300
    finally:
        subprocess.run(["chmod", "-R", "u+rwx", str(tree)], check=False)


@NOT_AS_ROOT
def test_a_folder_here_that_can_not_be_read_is_reported_never_skipped_in_silence(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "closed").mkdir(parents=True)
    (tree / "closed" / "f").write_text("f", encoding="utf-8")
    (tree / "closed").chmod(0)
    try:
        group_reads = tuple(True if place == 3 else None for place in range(12))
        refused = localfiles.change_properties([str(tree)], "", "", group_reads, True)
        assert len(refused) == 1 and "closed" in refused[0] and "could not be read" in refused[0]
    finally:
        (tree / "closed").chmod(0o700)


def test_an_unknown_user_here_is_refused_calmly(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    with pytest.raises(LocalActionFailed, match="no user"):
        localfiles.change_properties([str(tmp_path / "a")], "no-such-user-of-chrono-ftp", "", None, False)


def test_the_details_here_name_owner_group_and_link(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "l").symlink_to("a")
    found = {entry.name: entry for entry in localfiles.details(str(tmp_path), ["a", "l", "gone"])}
    assert set(found) == {"a", "l"}
    assert found["a"].owner and found["a"].group and found["a"].permissions
    assert found["l"].is_link and found["l"].link_target == "a" and found["l"].permissions == ""


def test_a_link_change_here_that_fails_and_can_not_tidy_says_what_remains(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "l").symlink_to("old")

    def refused(*_args: object) -> None:
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(localfiles.os, "replace", refused)
    monkeypatch.setattr(localfiles.os, "unlink", refused)
    with pytest.raises(LocalActionFailed, match="remains"):
        localfiles.change_link(str(tmp_path), "l", "new")
    assert os.readlink(tmp_path / "l") == "old"


def test_a_new_file_and_links_here(tmp_path: Path) -> None:
    path = localfiles.make_file(str(tmp_path), "new.txt")
    assert Path(path).read_bytes() == b""
    with pytest.raises(LocalActionFailed):
        localfiles.make_file(str(tmp_path), "new.txt")  # never over what is there
    localfiles.make_link(str(tmp_path), "soft", "new.txt", False)
    localfiles.make_link(str(tmp_path), "hard", "new.txt", True)
    assert os.readlink(tmp_path / "soft") == "new.txt"
    assert (tmp_path / "hard").stat().st_ino == (tmp_path / "new.txt").stat().st_ino
    localfiles.change_link(str(tmp_path), "soft", "elsewhere")
    assert os.readlink(tmp_path / "soft") == "elsewhere"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["hard", "new.txt", "soft"]  # nothing of the change left behind
    with pytest.raises(LocalActionFailed):
        localfiles.make_link(str(tmp_path), "a/b", "x", False)


# --- copies and links on the server (FM-05, FM-06) ----------------------------


def test_the_names_of_copies_come_from_a_simple_pattern() -> None:
    assert commands.copy_names("*.bak", ["a.txt", "b"]) == ["a.txt.bak", "b.bak"]
    assert commands.copy_names("old-*", ["a.txt"]) == ["old-a.txt"]
    assert commands.copy_names("other.txt", ["a.txt"]) == ["other.txt"]
    with pytest.raises(RemoteActionFailed, match=r"\*"):
        commands.copy_names("other.txt", ["a.txt", "b"])
    with pytest.raises(RemoteActionFailed, match="slash"):
        commands.copy_names("x/*", ["a"])


def test_why_a_copy_on_the_server_can_not_be_made() -> None:
    folder = [RemoteEntry("f", True)]
    files = [RemoteEntry("a", False)]
    assert commands.why_no_copy(Capabilities(shell=True, tools=frozenset({"cp"})), folder, False) == ""
    assert commands.why_no_copy(Capabilities(server_copy=True), files, False) == ""
    assert commands.why_no_copy(Capabilities(server_copy=True), folder, False) == commands.FOLDERS_NEED_SHELL_TEXT
    assert commands.why_no_copy(Capabilities(), files, True) == commands.NO_COPY_ON_FTP_TEXT
    assert commands.why_no_link(Capabilities(symlinks=True), True) == commands.NO_LINKS_ON_FTP_TEXT
    assert commands.why_no_link(Capabilities(symlinks=True), False) == ""


class Recording(Scripted):
    """Notes every command and every protocol call, and answers each command with its result."""

    def __init__(self, capabilities: Capabilities) -> None:
        super().__init__(CommandResult(0, "", ""))
        self.capabilities = capabilities
        self.calls: list[str] = []

    @override
    def run(self, command: str) -> CommandResult:
        self.calls.append(command)
        return self.result

    @override
    def copy_file(self, source: str, target: str) -> None:
        self.calls.append(f"copy {source} {target}")

    @override
    def make_link(self, target: str, path: str, hard: bool = False) -> None:
        self.calls.append(f"link {target} {path}")

    @override
    def delete(self, path: str, is_folder: bool) -> None:
        self.calls.append(f"delete {path}")

    @override
    def rename(self, old: str, new: str) -> None:
        self.calls.append(f"rename {old} {new}")


def test_with_a_shell_a_copy_is_one_cp_that_never_replaces_and_quotes_every_path() -> None:
    server = Recording(Capabilities(shell=True, tools=frozenset({"cp"})))
    commands.copy_on_server(server, "/srv/a b", "/srv/$(reboot)")
    assert len(server.calls) == 1
    assert server.calls[0].startswith("if [ -e '/srv/$(reboot)' ]")
    assert "{ cp -a -- '/srv/a b' '/srv/$(reboot)' || { { [ -L '/srv/$(reboot)' ] || chmod -R u+rwx -- '/srv/$(reboot)' 2>/dev/null; rm -rf -- '/srv/$(reboot)' || echo" in server.calls[0]


def test_a_copy_the_person_agreed_to_replace_is_made_under_our_own_name_and_quotes_every_path() -> None:
    """A paste the person said yes to for that one name takes its place through a copy of ours (FI-03, G-2); every path goes through
    shlex.quote (OR-19)."""
    server = Recording(Capabilities(shell=True, tools=frozenset({"cp"})))
    commands.copy_on_server(server, "/srv/a b", "/srv/$(reboot)", replace=True)
    assert len(server.calls) == 1
    command = server.calls[0]
    assert "cp -a -- '/srv/a b' '/srv/$(reboot).chrono-ftp-new'" in command  # never onto the name the person sees
    assert "rm -rf -- '/srv/$(reboot)'" not in command  # what was there is never removed by a failure (G-2)
    assert "mv -- '/srv/$(reboot)' '/srv/$(reboot).chrono-ftp-old'" in command
    assert "mv -- '/srv/$(reboot).chrono-ftp-new' '/srv/$(reboot)'" in command


@NOT_AS_ROOT
def test_a_replacing_copy_that_fails_leaves_what_was_there_whole(tmp_path: Path) -> None:
    """The command of the server, run by a real sh on a throwaway folder (OR-26): a folder with a file cp can't read, copied onto a
    folder the person already had."""
    source, there = tmp_path / "from" / "d", tmp_path / "onto" / "d"
    source.mkdir(parents=True)
    there.mkdir(parents=True)
    (source / "readable").write_text("r", encoding="utf-8")
    (source / "secret").write_text("s", encoding="utf-8")
    (source / "secret").chmod(0)
    (there / "precious").write_text("p", encoding="utf-8")
    result = subprocess.run(["sh", "-c", commands.replace_command(str(source), str(there))], capture_output=True, text=True)
    assert result.returncode != 0
    assert (there / "precious").read_text(encoding="utf-8") == "p"  # the person is never worse off than before Start (G-2)
    assert sorted(path.name for path in there.parent.iterdir()) == ["d"]  # and our own copy went


@NOT_AS_ROOT
def test_a_replacing_copy_of_a_folder_really_takes_its_place(tmp_path: Path) -> None:
    """cp -a onto an existing folder would put the copy inside it: the copy takes the name itself instead."""
    source, there = tmp_path / "from" / "d", tmp_path / "onto" / "d"
    (source / "sub").mkdir(parents=True)
    there.mkdir(parents=True)
    (source / "readable").write_text("r", encoding="utf-8")
    (there / "was_here").write_text("w", encoding="utf-8")
    result = subprocess.run(["sh", "-c", commands.replace_command(str(source), str(there))], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert sorted(path.name for path in there.iterdir()) == ["readable", "sub"]  # replaced, never nested as d/d
    assert sorted(path.name for path in there.parent.iterdir()) == ["d"]


@NOT_AS_ROOT
def test_a_replacing_copy_refuses_when_the_name_it_needs_is_taken(tmp_path: Path) -> None:
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    (tmp_path / "old.txt").write_text("old", encoding="utf-8")
    (tmp_path / ("old.txt" + commands.NEW_SUFFIX)).write_text("someone else", encoding="utf-8")
    result = subprocess.run(["sh", "-c", commands.replace_command(str(tmp_path / "new.txt"), str(tmp_path / "old.txt"))], capture_output=True, text=True)
    assert result.returncode != 0 and "is taken" in result.stderr
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "old"


@NOT_AS_ROOT
def test_a_copy_cut_short_earlier_says_where_the_persons_own_file_is_and_what_to_do(tmp_path: Path) -> None:
    """Chrono FTP killed between the two steps leaves the person's own file under the aside name; the next paste says so (OR-23)."""
    (tmp_path / "new.txt").write_text("new", encoding="utf-8")
    (tmp_path / ("old.txt" + OLD_SUFFIX)).write_text("the person's own, moved aside", encoding="utf-8")
    result = subprocess.run(["sh", "-c", commands.replace_command(str(tmp_path / "new.txt"), str(tmp_path / "old.txt"))], capture_output=True, text=True)
    assert result.returncode != 0
    assert "old.txt.chrono-ftp-old" in result.stderr and "rename it back to old.txt" in result.stderr and "Nothing of it was lost" in result.stderr
    assert (tmp_path / ("old.txt" + OLD_SUFFIX)).read_text(encoding="utf-8") == "the person's own, moved aside"


@NOT_AS_ROOT
def test_every_step_of_a_replacing_copy_says_what_really_failed(tmp_path: Path) -> None:
    """One sentence per step, never one sentence for several different failures: each says what happened and that what the person had
    is untouched (OR-23)."""
    command = commands.replace_command(str(tmp_path / "a"), str(tmp_path / "b"))
    assert "could not be moved out of the way" in command
    assert "could not be put in the place of" in command
    assert "could not be put back either" in command  # the rename back failed too: it says where the data is
    assert command.count("is here under the name") == 1


@NOT_AS_ROOT
def test_a_cp_that_fails_halfway_removes_its_half_copy_and_never_what_was_there(tmp_path: Path) -> None:
    """The command of the server, run by a real sh on a throwaway folder: a folder with a file cp can't read."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "readable").write_text("r", encoding="utf-8")
    (source / "secret").write_text("s", encoding="utf-8")
    (source / "secret").chmod(0)
    (tmp_path / "there").write_text("keep", encoding="utf-8")
    server = Recording(Capabilities(shell=True, tools=frozenset({"cp"})))
    commands.copy_on_server(server, str(source), str(tmp_path / "copy"))
    commands.copy_on_server(server, str(source), str(tmp_path / "there"))
    results = [subprocess.run(["/bin/sh", "-c", line], capture_output=True, text=True, check=False) for line in server.calls]
    assert results[0].returncode != 0 and not (tmp_path / "copy").exists()
    assert results[1].returncode != 0 and (tmp_path / "there").read_text(encoding="utf-8") == "keep"


@NOT_AS_ROOT
def test_a_half_copy_with_closed_folders_is_removed_and_one_that_stays_is_said(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "closed").mkdir(parents=True)
    (source / "closed" / "inside").write_text("i", encoding="utf-8")
    (source / "closed").chmod(0o500)  # cp -a gives the copy the same: rm alone can't empty it
    (source / "secret").write_text("s", encoding="utf-8")
    (source / "secret").chmod(0)
    server = Recording(Capabilities(shell=True, tools=frozenset({"cp"})))
    commands.copy_on_server(server, str(source), str(tmp_path / "copy"))
    try:
        result = subprocess.run(["/bin/sh", "-c", server.calls[0]], capture_output=True, text=True, check=False)
        assert result.returncode != 0 and not os.path.lexists(tmp_path / "copy")
        assert "remains" not in result.stderr
    finally:
        subprocess.run(["chmod", "-R", "u+rwx", str(tmp_path)], check=False)
    assert "An unfinished copy remains at" in commands.half_copy_removal("/srv/x")


def test_a_link_that_leads_nowhere_is_a_taken_name_for_every_copy_and_link(tmp_path: Path) -> None:
    """Review round 2: [ -e ] and stat see no name where a link leads nowhere; cp then failed and its clean-up removed the link."""
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "dangling").symlink_to("nowhere")
    server = Recording(Capabilities(shell=True, tools=frozenset({"cp"})))
    commands.copy_on_server(server, str(tmp_path / "a.txt"), str(tmp_path / "dangling"))
    assert subprocess.run(["/bin/sh", "-c", server.calls[0]], capture_output=True, check=False).returncode != 0
    assert os.readlink(tmp_path / "dangling") == "nowhere"
    with ssh_server(tmp_path, shell=False) as fake:
        connection = connected_ssh(fake.port)
        try:
            with pytest.raises(NameTaken):
                connection.copy_file("/a.txt", "/dangling")
            with pytest.raises(NameTaken):
                commands.make_link(connection, "a.txt", "/dangling", False)
        finally:
            connection.close()
    assert os.readlink(tmp_path / "dangling") == "nowhere"


def test_without_a_shell_the_server_copies_through_the_protocol() -> None:
    server = Recording(Capabilities(server_copy=True))
    commands.copy_on_server(server, "/a", "/b")
    assert server.calls == ["copy /a /b"]
    with pytest.raises(RemoteActionFailed):
        commands.copy_on_server(server, "/a", "/a")


def test_a_link_led_elsewhere_is_never_missing_on_the_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands.secrets, "token_hex", same_name)
    server = Recording(Capabilities(symlinks=True))
    commands.change_link(server, "/srv/l", "/new")
    assert server.calls == ["link /new /srv/l.aaaaaaaa.chrono-ftp-link", "rename /srv/l /srv/l.aaaaaaaa.chrono-ftp-old-link", "rename /srv/l.aaaaaaaa.chrono-ftp-link /srv/l", "delete /srv/l.aaaaaaaa.chrono-ftp-old-link"]


class RefusesRename(Recording):
    """A server that refuses to rename anything onto one name, as a refusal or a dropped line would."""

    def __init__(self, capabilities: Capabilities, refused: str) -> None:
        super().__init__(capabilities)
        self.refused: str = refused

    @override
    def rename(self, old: str, new: str) -> None:
        super().rename(old, new)
        if new == self.refused and not old.endswith(commands.LINK_OLD_SUFFIX):  # putting the old link back works
            raise RemoteActionFailed("Permission denied")

    @override
    def replace_atomically(self, source: str, target: str) -> None:
        self.calls.append(f"replace {source} {target}")
        raise RemoteActionFailed("Permission denied")


def test_a_refused_step_puts_the_old_link_back_and_removes_the_new_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(commands.secrets, "token_hex", same_name)
    server = RefusesRename(Capabilities(symlinks=True), "/srv/l")
    with pytest.raises(RemoteActionFailed):
        commands.change_link(server, "/srv/l", "/new")
    assert server.calls[-2:] == ["rename /srv/l.aaaaaaaa.chrono-ftp-old-link /srv/l", "delete /srv/l.aaaaaaaa.chrono-ftp-link"]
    assert "delete /srv/l" not in server.calls  # the person's link was never removed, only moved aside and back
    atomic = RefusesRename(Capabilities(symlinks=True, atomic_rename=True), "/srv/l")
    with pytest.raises(RemoteActionFailed):
        commands.change_link(atomic, "/srv/l", "/new")
    assert atomic.calls == ["link /new /srv/l.aaaaaaaa.chrono-ftp-link", "replace /srv/l.aaaaaaaa.chrono-ftp-link /srv/l", "delete /srv/l.aaaaaaaa.chrono-ftp-link"]


class TakenNames(Recording):
    """A folder where some names belong to someone else: a link made there is refused, as the server refuses it."""

    def __init__(self, capabilities: Capabilities, taken: set[str]) -> None:
        super().__init__(capabilities)
        self.taken: set[str] = taken

    @override
    def make_link(self, target: str, path: str, hard: bool = False) -> None:
        if path in self.taken:
            raise NameTaken("taken")
        super().make_link(target, path, hard)

    @override
    def name_taken(self, path: str) -> bool:
        return path in self.taken


def same_name(_size: int | None = None) -> str:
    return "aaaaaaaa"


def test_the_temporary_names_of_a_link_led_elsewhere_are_free_and_only_ours_are_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = TakenNames(Capabilities(symlinks=True), {"/srv/l.chrono-ftp-link", "/srv/l.chrono-ftp-old-link"})
    commands.change_link(server, "/srv/l", "/new")
    assert not any(path in call for call in server.calls for path in server.taken)  # someone's files: never touched
    assert server.calls[-1].startswith("delete /srv/l.") and server.calls[-1].endswith(commands.LINK_OLD_SUFFIX)
    (tmp_path / "l").symlink_to("old")
    for someones in ("l.chrono-ftp-link", "l.aaaaaaaa.chrono-ftp-link"):
        (tmp_path / someones).write_text("someone's", encoding="utf-8")
    names = iter(["aaaaaaaa", "bbbbbbbb"])

    def next_name(_size: int | None = None) -> str:
        return next(names)

    monkeypatch.setattr(localfiles.secrets, "token_hex", next_name)
    localfiles.change_link(str(tmp_path), "l", "new")
    assert os.readlink(tmp_path / "l") == "new"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["l", "l.aaaaaaaa.chrono-ftp-link", "l.chrono-ftp-link"]


def test_with_a_shell_a_link_is_replaced_in_one_step_by_ln_and_mv(tmp_path: Path) -> None:
    server = Recording(Capabilities(shell=True, tools=frozenset({"ln", "mv"}), symlinks=True))
    link = tmp_path / "it's a link"
    link.symlink_to("old")
    commands.change_link(server, str(link), "new target")
    assert len(server.calls) == 1
    assert subprocess.run(["/bin/sh", "-c", server.calls[0]], check=False).returncode == 0
    assert os.readlink(link) == "new target" and sorted(path.name for path in tmp_path.iterdir()) == ["it's a link"]
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "inside").write_text("x", encoding="utf-8")
    refused = commands.link_replace_command("t", str(tmp_path / "fresh"), str(tmp_path / "folder"))  # mv -T can't replace a full folder
    assert subprocess.run(["/bin/sh", "-c", refused], capture_output=True, check=False).returncode != 0
    assert not os.path.lexists(tmp_path / "fresh")  # the new link went again


def test_over_sftp_links_are_made_and_a_taken_name_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    with ssh_server(tmp_path, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            assert connection.capabilities.symlinks
            commands.make_link(connection, "a.txt", "/soft", False)
            assert os.readlink(tmp_path / "soft") == "a.txt"
            with pytest.raises(NameTaken):
                commands.make_link(connection, "other", "/soft", False)
            if connection.capabilities.hardlinks:
                commands.make_link(connection, "/a.txt", "/hard", True)
                assert (tmp_path / "hard").stat().st_ino == (tmp_path / "a.txt").stat().st_ino
            commands.change_link(connection, "/soft", "elsewhere")
            assert os.readlink(tmp_path / "soft") == "elsewhere"
        finally:
            connection.close()


def test_over_sftp_without_a_shell_the_server_copies_a_file_itself(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("content", encoding="utf-8")
    (tmp_path / "a.txt").chmod(0o640)
    with ssh_server(tmp_path, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            assert connection.capabilities.server_copy and not connection.capabilities.shell
            commands.copy_on_server(connection, "/a.txt", "/b.txt")
            assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "content"
            assert stat.S_IMODE((tmp_path / "b.txt").stat().st_mode) == 0o640
            with pytest.raises(NameTaken):
                commands.copy_on_server(connection, "/a.txt", "/b.txt")
        finally:
            connection.close()


def test_over_ftp_the_server_copies_a_file_itself_where_it_offers_it(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("content", encoding="utf-8")
    settings = Connection(name="F", host="127.0.0.1", user=USER, protocol=FTP)
    with ftp_server(tmp_path, copies=True) as port:
        connection = FtpConnection(Connection(name="F", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        try:
            assert connection.capabilities.server_copy
            commands.copy_on_server(connection, "/a.txt", "/b.txt")
            assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "content"
            with pytest.raises(NameTaken):
                commands.copy_on_server(connection, "/a.txt", "/b.txt")
        finally:
            connection.close()
    with ftp_server(tmp_path) as port:
        connection = FtpConnection(Connection(name=settings.name, host=settings.host, port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        try:
            assert not connection.capabilities.server_copy  # not offered: detected, never assumed (D-016)
            with pytest.raises(RemoteActionFailed):
                connection.copy_file("/a.txt", "/c.txt")
        finally:
            connection.close()


def test_a_server_copy_that_fails_halfway_removes_its_half_copy_over_sftp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.txt").write_text("content", encoding="utf-8")

    async def full_disk(self: asyncssh.SFTPClient, source: asyncssh.SFTPClientFile, target: asyncssh.SFTPClientFile) -> None:
        await target.write(b"c")
        raise asyncssh.SFTPNoSpaceOnFilesystem("No space left on device")

    with ssh_server(tmp_path, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            monkeypatch.setattr(asyncssh.SFTPClient, "remote_copy", full_disk)
            with pytest.raises(RemoteActionFailed, match="no room"):
                connection.copy_file("/a.txt", "/b.txt")
            assert not (tmp_path / "b.txt").exists()
            (tmp_path / "c.txt").write_text("there before", encoding="utf-8")
            with pytest.raises(NameTaken):
                connection.copy_file("/a.txt", "/c.txt")
            assert (tmp_path / "c.txt").read_text(encoding="utf-8") == "there before"  # never what was there
        finally:
            connection.close()


def test_a_server_copy_that_fails_halfway_removes_its_half_copy_over_ftp(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("content", encoding="utf-8")
    with ftp_server(tmp_path, copies="halfway") as port:
        connection = FtpConnection(Connection(name="F", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        try:
            with pytest.raises(RemoteActionFailed):
                connection.copy_file("/a.txt", "/b.txt")
        finally:
            connection.close()
    assert not (tmp_path / "b.txt").exists()


# --- my public key on the server (FM-09), on the fake server only (OR-26) ----


def public_key(folder: Path, comment: str = "anna@laptop") -> Path:
    key = asyncssh.generate_private_key("ssh-ed25519", comment=comment)
    path = folder / "id_test.pub"
    path.write_bytes(key.export_public_key("openssh"))
    (folder / "id_test").write_bytes(key.export_private_key("openssh"))
    return path


def test_a_public_key_is_read_and_a_private_key_or_another_file_is_refused(tmp_path: Path) -> None:
    key = keyinstall.read_public_key(str(public_key(tmp_path)))
    assert key.line.startswith("ssh-ed25519 ") and key.comment == "anna@laptop"
    with pytest.raises(LocalActionFailed, match="private key"):
        keyinstall.read_public_key(str(tmp_path / "id_test"))
    (tmp_path / "other.pub").write_text("hello\n", encoding="utf-8")
    with pytest.raises(LocalActionFailed, match="no public key"):
        keyinstall.read_public_key(str(tmp_path / "other.pub"))
    with pytest.raises(LocalActionFailed, match="could not be read"):
        keyinstall.read_public_key(str(tmp_path / "gone.pub"))
    (tmp_path / "big.pub").write_bytes(b"x" * (keyinstall.MOST_KEY_BYTES + 1))
    with pytest.raises(LocalActionFailed, match="too large"):
        keyinstall.read_public_key(str(tmp_path / "big.pub"))


def test_a_key_whose_comment_has_letters_beyond_ascii_is_installed_exactly(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    key = keyinstall.read_public_key(str(public_key(tmp_path, "rob@mäc")))
    assert key.comment == "rob@mäc" and key.line.endswith(" rob@mäc")
    with ssh_server(root, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            keyinstall.install_key(connection, key)
        finally:
            connection.close()
    assert (root / ".ssh" / "authorized_keys").read_text(encoding="utf-8") == key.line + "\n"


def test_the_connections_own_key_is_suggested_first(tmp_path: Path) -> None:
    public_key(tmp_path)
    assert keyinstall.suggested_key_file(Connection(name="T", host="h", key_file=str(tmp_path / "id_test"))) == str(tmp_path / "id_test.pub")


def test_a_key_is_found_also_with_options_before_it() -> None:
    key = keyinstall.PublicKey("ssh-ed25519 AAAAkey me", "AAAAkey", "me")
    assert keyinstall.holds_key(b'no-pty,no-port-forwarding ssh-ed25519 AAAAkey other\n', key)
    assert not keyinstall.holds_key(b"# ssh-ed25519 AAAAkey\nssh-ed25519 AAAAother\n", key)


def test_installing_a_key_makes_the_folder_and_file_with_only_my_rights(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    key = keyinstall.read_public_key(str(public_key(tmp_path)))
    with ssh_server(root, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            done = keyinstall.install_key(connection, key)
            assert done == [keyinstall.MADE_FOLDER_TEXT, keyinstall.MADE_FILE_TEXT]
            assert stat.S_IMODE((root / ".ssh").stat().st_mode) == 0o700
            assert stat.S_IMODE((root / ".ssh" / "authorized_keys").stat().st_mode) == 0o600
            assert (root / ".ssh" / "authorized_keys").read_text(encoding="utf-8") == key.line + "\n"
            assert keyinstall.install_key(connection, key) == [keyinstall.ALREADY_TEXT]  # never twice
            assert (root / ".ssh" / "authorized_keys").read_text(encoding="utf-8") == key.line + "\n"
        finally:
            connection.close()


def test_a_key_is_added_after_the_keys_that_are_there_and_loose_rights_are_put_right(tmp_path: Path) -> None:
    root = tmp_path / "server"
    (root / ".ssh").mkdir(parents=True)
    (root / ".ssh").chmod(0o775)
    keys = root / ".ssh" / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAAother someone", encoding="utf-8")  # no line end after the last key
    keys.chmod(0o664)
    key = keyinstall.read_public_key(str(public_key(tmp_path)))
    with ssh_server(root, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            done = keyinstall.install_key(connection, key)
        finally:
            connection.close()
    assert done == [keyinstall.FOLDER_PUT_RIGHT_TEXT, keyinstall.ADDED_TEXT, keyinstall.FILE_PUT_RIGHT_TEXT]
    assert keys.read_text(encoding="utf-8") == f"ssh-ed25519 AAAAother someone\n{key.line}\n"
    assert stat.S_IMODE(keys.stat().st_mode) == 0o600
    assert stat.S_IMODE((root / ".ssh").stat().st_mode) == 0o755


@NOT_AS_ROOT
def test_a_server_that_refuses_to_tell_about_authorized_keys_says_why_and_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    (root / ".ssh").mkdir(parents=True)
    key = keyinstall.read_public_key(str(public_key(tmp_path)))
    (root / ".ssh").chmod(0o600)  # no Run: what is in it can't be looked at
    sent: list[str] = []
    try:
        with ssh_server(root, shell=False) as server:
            connection = connected_ssh(server.port)
            real_upload = connection.upload

            def noted(local: str, remote: str, progress: Progress, limit: Limit = 0, offset: int = 0, append: bool = False, exclusive: bool = False) -> None:
                sent.append(remote)
                real_upload(local, remote, progress, limit, offset, append, exclusive)

            monkeypatch.setattr(connection, "upload", noted)
            try:
                with pytest.raises(RemoteActionFailed) as refused:
                    keyinstall.install_key(connection, key)
            finally:
                connection.close()
    finally:
        (root / ".ssh").chmod(0o700)
    assert sent == [] and "ermission" in str(refused.value)  # the server's own reason, not taken for a missing file


def test_a_temporary_folder_this_computer_can_not_make_stops_the_install_calmly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "server"
    root.mkdir()
    key = keyinstall.read_public_key(str(public_key(tmp_path)))

    def full(*_args: object, **_options: object) -> object:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(keyinstall.tempfile, "TemporaryDirectory", full)
    with ssh_server(root, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            with pytest.raises(LocalActionFailed, match="No space left"):
                keyinstall.install_key(connection, key)
        finally:
            connection.close()


def test_a_file_called_ssh_stops_the_install_calmly(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / ".ssh").write_text("x", encoding="utf-8")
    key = keyinstall.read_public_key(str(public_key(tmp_path)))
    with ssh_server(root, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            with pytest.raises(RemoteActionFailed, match="file called .ssh"):
                keyinstall.install_key(connection, key)
        finally:
            connection.close()


# --- the password on the server (FM-10), on the fake server only (OR-26) -----


def test_the_questions_of_passwd_get_the_right_answers() -> None:
    talk = PasswordTalk("old-one", "new-one")
    assert talk.reply("Changing password for anna.\nCurrent password: ") == "old-one"
    assert talk.reply("\nNew password: ") == "new-one"
    assert talk.reply("\nRetype new password: ") == "new-one"
    with pytest.raises(AskedTooOften):
        talk.reply("\nNew password: ")  # a refused password asked again: the talk ends


@pytest.mark.parametrize("questions", [["(current) UNIX password: ", "Enter new UNIX password: ", "Retype new UNIX password: "], ["Old Password:", "New Password:", "Retype New Password:"], ["New password: ", "Retype new password: "]])
def test_passwd_of_other_systems_and_of_root_is_answered(questions: list[str]) -> None:
    talk = PasswordTalk("o", "n")
    assert [talk.reply(question) for question in questions] == (["o", "n", "n"] if len(questions) == 3 else ["n", "n"])


def test_nothing_is_typed_before_a_question() -> None:
    assert PasswordTalk("o", "n").reply("Changing password for anna.\n") is None


def test_a_password_that_is_a_common_word_never_garbles_the_servers_message() -> None:
    """Review round 4: only what the server echoes of the typed input is taken out of its words."""
    said = shown_answer("New password: dictionary\ndictionary\nBAD PASSWORD: it is based on a dictionary word\n", ("old", "dictionary"))
    assert said == "BAD PASSWORD: it is based on a dictionary word"


def test_the_answer_of_the_server_holds_no_password_and_no_question() -> None:
    said = shown_answer("Current password: old-one\r\nNew password: \nBAD PASSWORD: new-one is too short\npasswd: unchanged\n", ("old-one", "new-one"))
    assert "old-one" not in said and "new-one" not in said
    assert said == "BAD PASSWORD:  is too short\npasswd: unchanged"


def test_the_password_is_changed_by_passwd_on_the_server(tmp_path: Path) -> None:
    passwd = Passwd(PASSWORD)
    with ssh_server(tmp_path, commands=passwd) as server:
        connection = connected_ssh(server.port)
        try:
            assert connection.capabilities.has("passwd")
            answer = connection.change_password(PASSWORD, "a-new-secret")
        finally:
            connection.close()
    assert answer.changed and passwd.password == "a-new-secret"
    assert "a-new-secret" not in answer.text and PASSWORD not in answer.text
    assert passwd.commands[-1] == "env LC_ALL=C passwd"


@pytest.mark.parametrize("echo", [False, True])
def test_a_refused_new_password_ends_calmly_with_the_servers_words_and_no_password(tmp_path: Path, echo: bool) -> None:
    passwd = Passwd(PASSWORD, refuse=True, echo=echo)
    with ssh_server(tmp_path, commands=passwd) as server:
        connection = connected_ssh(server.port)
        try:
            answer = connection.change_password(PASSWORD, "short")
        finally:
            connection.close()
    assert not answer.changed and passwd.password == PASSWORD
    assert "shorter than 8" in answer.text
    assert "short\n" not in answer.text + "\n" and PASSWORD not in answer.text


def test_a_wrong_current_password_changes_nothing(tmp_path: Path) -> None:
    passwd = Passwd(PASSWORD)
    with ssh_server(tmp_path, commands=passwd) as server:
        connection = connected_ssh(server.port)
        try:
            answer = connection.change_password("not-the-password", "a-new-secret")
        finally:
            connection.close()
    assert not answer.changed and passwd.password == PASSWORD
    assert "unchanged" in answer.text


def test_a_passwd_that_never_says_it_is_done_gives_a_calm_true_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ssh, "PASSWORD_ANSWER_SECONDS", 1.0)
    passwd = Passwd(PASSWORD, silent_end=True)
    with ssh_server(tmp_path, commands=passwd) as server:
        connection = connected_ssh(server.port)
        try:
            answer = connection.change_password(PASSWORD, "a-new-secret")
        finally:
            connection.close()
    assert not answer.changed and answer.text == ssh.PASSWORD_UNKNOWN_TEXT  # never "no shell": the stored password stays


def test_a_line_lost_after_the_new_password_says_the_server_may_have_taken_it(tmp_path: Path) -> None:
    passwd = Passwd(PASSWORD, drop_at_end=True)
    with ssh_server(tmp_path, commands=passwd) as server:
        connection = connected_ssh(server.port)
        try:
            with pytest.raises(ConnectionLost) as lost:
                connection.change_password(PASSWORD, "a-new-secret")
        finally:
            connection.close()
    assert "may have taken the new one" in str(lost.value) and "a-new-secret" not in str(lost.value)


def test_without_passwd_the_password_is_not_changed(tmp_path: Path) -> None:
    with ssh_server(tmp_path, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            with pytest.raises(RemoteActionFailed):
                connection.change_password(PASSWORD, "x")
        finally:
            connection.close()


# --- the terminal in the folder on the screen (FM-12) -------------------------


def test_the_terminal_starts_in_the_folder_and_a_folder_it_can_not_enter_leaves_it_open(tmp_path: Path) -> None:
    assert shell_start("") == 'exec "$SHELL" -l'
    line = shell_start(str(tmp_path / "it's here"))
    assert shlex.split(line)[:2] == ["cd", str(tmp_path / "it's here")]
    result = subprocess.run(["/bin/sh", "-c", shell_start(str(tmp_path / "gone"))], env={**os.environ, "SHELL": "/bin/true"}, capture_output=True, text=True, check=False)
    assert result.returncode == 0 and CANT_ENTER_TEXT in result.stdout
