import os
from pathlib import Path
from typing import override

from chrono_ftp.connections.base import CommandResult, RemoteEntry
from chrono_ftp.engine import localfiles
from chrono_ftp.engine.compare import Difference, compare_folders, difference_of
from chrono_ftp.core.errors import LocalActionFailed, RemoteActionFailed
from tests.test_engine import Scripted

import pytest

# --- Version ------------------------------------------------------------
__version__ = "test_compare.py 0.0.6"

# --- Version history ----------------------------------------------------
# v0.0.6: Initial


class Listing(Scripted):
    def __init__(self, folders: dict[str, list[RemoteEntry]]) -> None:
        super().__init__(CommandResult(0, "", ""))
        self.folders: dict[str, list[RemoteEntry]] = folders

    @override
    def list_folder(self, path: str) -> list[RemoteEntry]:
        return self.folders[path]


def file(tmp_path: Path, name: str, content: bytes, moment: float) -> None:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (moment, moment))


def test_every_kind_of_difference_is_found_also_inside_a_folder(tmp_path: Path) -> None:
    file(tmp_path, "same.txt", b"12345", 1000)
    file(tmp_path, "mine.txt", b"1", 1000)
    file(tmp_path, "newer.txt", b"123", 2000)
    file(tmp_path, "shared/older.txt", b"12", 1000)
    (tmp_path / "only-folder").mkdir()
    remote = Listing(
        {
            "/srv": [RemoteEntry("same.txt", False, 5, 1001), RemoteEntry("newer.txt", False, 9, 1000), RemoteEntry("theirs.txt", False, 1, 1000), RemoteEntry("shared", True)],
            "/srv/shared": [RemoteEntry("older.txt", False, 7, 2000)],
        }
    )
    found = {(item.folder, item.name): item.difference for item in compare_folders(remote, str(tmp_path), "/srv")}
    assert found == {
        ("", "mine.txt"): Difference.ONLY_LOCAL,
        ("", "newer.txt"): Difference.NEWER_LOCAL,
        ("", "only-folder"): Difference.ONLY_LOCAL,
        ("", "theirs.txt"): Difference.ONLY_REMOTE,
        ("shared", "older.txt"): Difference.NEWER_REMOTE,
    }


def test_the_same_size_with_another_date_is_told_apart_from_a_real_change() -> None:
    assert difference_of(RemoteEntry("a", False, 5, 1000), RemoteEntry("a", False, 5, 5000)) == Difference.DATE
    assert difference_of(RemoteEntry("a", False, 5, 1000), RemoteEntry("a", False, 6, 1001)) == Difference.SIZE
    assert difference_of(RemoteEntry("a", False, 5, None), RemoteEntry("a", False, 5, 1)) is None


def test_links_are_left_out_of_a_compare(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to("/")
    remote = Listing({"/srv": [RemoteEntry("other", True, is_link=True)]})
    assert compare_folders(remote, str(tmp_path), "/srv") == []


def test_a_name_that_is_a_link_on_one_side_is_left_out_also_with_a_file_on_the_other(tmp_path: Path) -> None:
    (tmp_path / "config.php").symlink_to("/some/other/file")
    (tmp_path / "x").write_text("x", encoding="utf-8")
    remote = Listing({"/srv": [RemoteEntry("config.php", False, 5, 1.0), RemoteEntry("x", False, 9, 1.0, is_link=True)]})
    assert compare_folders(remote, str(tmp_path), "/srv") == []


def test_a_file_on_one_side_and_a_folder_on_the_other_is_told(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    remote = Listing({"/srv": [RemoteEntry("x", False, 9, 1.0)]})
    assert [item.difference for item in compare_folders(remote, str(tmp_path), "/srv")] == [Difference.KIND]


def test_a_folder_that_can_not_be_read_is_told_and_the_rest_is_still_compared(tmp_path: Path) -> None:
    (tmp_path / "closed").mkdir()
    (tmp_path / "mine.txt").write_text("x", encoding="utf-8")

    class Closed(Listing):
        @override
        def list_folder(self, path: str) -> list[RemoteEntry]:
            if path == "/srv/closed":
                raise RemoteActionFailed("no")
            return super().list_folder(path)

    found = {item.name: item.difference for item in compare_folders(Closed({"/srv": [RemoteEntry("closed", True)]}), str(tmp_path), "/srv")}
    assert found == {"closed": Difference.UNREADABLE, "mine.txt": Difference.ONLY_LOCAL}


def test_a_local_folder_that_can_not_be_read_is_never_taken_for_empty(tmp_path: Path) -> None:
    closed = tmp_path / "closed"
    closed.mkdir(mode=0o000)
    remote = Listing({"/srv": [RemoteEntry("closed", True)], "/srv/closed": [RemoteEntry("a", False, 1, 1.0)]})
    try:
        assert [item.difference for item in compare_folders(remote, str(tmp_path), "/srv")] == [Difference.UNREADABLE]
    finally:
        closed.chmod(0o755)


def test_our_own_temporary_files_are_no_difference(tmp_path: Path) -> None:
    remote = Listing({"/srv": [RemoteEntry("a.chrono-ftp-part", False, 1, 1.0), RemoteEntry("b.chrono-ftp-old", False, 1, 1.0)]})
    assert compare_folders(remote, str(tmp_path), "/srv") == []


def test_a_local_folder_is_read_with_its_real_names_also_a_name_that_is_not_utf8(tmp_path: Path) -> None:
    os.close(os.open(os.fsencode(tmp_path) + b"/b\xffd.txt", os.O_CREAT | os.O_WRONLY))
    (tmp_path / "link").symlink_to("b-target")
    entries = {entry.name: entry for entry in localfiles.read_folder(str(tmp_path)) or []}
    assert "b\udcffd.txt" in entries
    assert entries["link"].is_link


def test_a_folder_that_can_not_be_opened_reads_as_none(tmp_path: Path) -> None:
    closed = tmp_path / "closed"
    closed.mkdir(mode=0o000)
    try:
        assert localfiles.read_folder(str(closed)) is None
    finally:
        closed.chmod(0o755)


def test_a_local_rename_never_replaces_what_is_there(tmp_path: Path) -> None:
    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "b").write_text("b", encoding="utf-8")
    with pytest.raises(LocalActionFailed):
        localfiles.rename(str(tmp_path), "a", "b")
    assert (tmp_path / "b").read_text(encoding="utf-8") == "b"
    with pytest.raises(LocalActionFailed):
        localfiles.rename(str(tmp_path), "a", "x/y")
    localfiles.rename(str(tmp_path), "a", "c")
    assert (tmp_path / "c").exists()


def test_a_local_folder_is_made_once(tmp_path: Path) -> None:
    localfiles.make_folder(str(tmp_path), "new")
    with pytest.raises(LocalActionFailed):
        localfiles.make_folder(str(tmp_path), "new")
