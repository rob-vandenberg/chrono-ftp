"""Opt-in tests of block FM against a real SFTP server (D-026): links, copies on the server, a chmod of a whole tree with x for its
folders, and the names of users and groups. Run with `pytest -m live`; the server, key and trust are set as in
tests/test_live_transfers.py. Every test works in a throwaway folder of its own in the account's home, which goes again (OR-26). Installing
a key and changing a password are never tried on a real account: only on the fake servers (tests/test_server_actions.py)."""

import os
import tempfile

import pytest

from chrono_ftp.connections.base import RemoteConnection, joined
from chrono_ftp.core.errors import NameTaken
from chrono_ftp.engine import commands, properties, rights
from tests.test_live_transfers import live

# --- Version ------------------------------------------------------------
__version__ = "test_live_serveractions.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Initial: links, copies on the server with and without the shell, a chmod with Run for folders by find, the names of the
#          server (FM-03 to FM-06)

__all__ = ["live"]  # the fixture, used by name

pytestmark = pytest.mark.live


def nothing(_amount: int) -> None:
    pass


def put(server: RemoteConnection, path: str, content: bytes) -> None:
    with tempfile.TemporaryDirectory() as here:
        local = os.path.join(here, "file")
        with open(local, "wb") as file:
            file.write(content)
        server.upload(local, path, nothing)


def test_links_are_made_and_led_elsewhere_on_a_real_server(live: tuple[RemoteConnection, str]) -> None:
    server, folder = live
    put(server, joined(folder, "a.txt"), b"a")
    commands.make_link(server, "a.txt", joined(folder, "soft"), False)
    found = server.details(joined(folder, "soft"))
    assert found is not None and found.size == 1
    with pytest.raises(NameTaken):
        commands.make_link(server, "b.txt", joined(folder, "soft"), False)
    commands.change_link(server, joined(folder, "soft"), "b.txt")
    listed = {entry.name: entry for entry in server.list_folder(folder)}
    assert listed["soft"].is_link and listed["soft"].link_target == "b.txt"
    assert set(listed) == {"a.txt", "soft"}  # nothing of the change left behind
    if server.capabilities.hardlinks:
        commands.make_link(server, joined(folder, "a.txt"), joined(folder, "hard"), True)
        hard = server.details(joined(folder, "hard"))
        assert hard is not None and hard.size == 1 and not hard.is_link


def test_a_copy_on_a_real_server_with_its_shell_and_through_the_protocol(live: tuple[RemoteConnection, str]) -> None:
    server, folder = live
    put(server, joined(folder, "a.txt"), b"content")
    if commands.can_copy(server.capabilities):
        commands.copy_on_server(server, joined(folder, "a.txt"), joined(folder, "by-cp.txt"))
        copied = server.details(joined(folder, "by-cp.txt"))
        assert copied is not None and copied.size == len(b"content")
    if server.capabilities.server_copy:
        server.copy_file(joined(folder, "a.txt"), joined(folder, "by-protocol.txt"))
        copied = server.details(joined(folder, "by-protocol.txt"))
        assert copied is not None and copied.size == len(b"content")
        with pytest.raises(NameTaken):
            server.copy_file(joined(folder, "a.txt"), joined(folder, "by-protocol.txt"))


def test_a_chmod_of_a_whole_tree_gives_its_folders_x_on_a_real_server(live: tuple[RemoteConnection, str]) -> None:
    server, folder = live
    if not commands.can_change_mode(server.capabilities):
        pytest.skip("this server has no shell with chmod")
    tree = joined(folder, "tree")
    server.make_folder(tree)
    server.make_folder(joined(tree, "inner"))
    put(server, joined(joined(tree, "inner"), "f.txt"), b"f")
    chosen = rights.rights_of(0o640)
    line = commands.rights_command([tree], rights.symbolic(chosen), True, rights.readers(chosen))
    commands.run_checked(server, line)
    inner = server.details(joined(tree, "inner"))
    file = server.details(joined(joined(tree, "inner"), "f.txt"))
    assert inner is not None and rights.mode_number(inner.permissions) == 0o750
    assert file is not None and rights.mode_number(file.permissions) == 0o640
    commands.run_checked(server, commands.mode_command([tree], "u+rwx", True))  # so the throwaway folder can go again


def test_the_names_of_users_and_groups_come_from_a_real_server(live: tuple[RemoteConnection, str]) -> None:
    server, _folder = live
    if not server.capabilities.shell:
        pytest.skip("this server has no shell")
    users, groups = properties.server_names(server)
    assert users and groups
    assert "root" in users
