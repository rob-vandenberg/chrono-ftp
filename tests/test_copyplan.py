from pathlib import Path

import os
from collections.abc import Callable

import pytest

from chrono_ftp.engine.copyplan import Copy, FILE_IN_THE_WAY_TEXT, FOLDER_IN_THE_WAY_TEXT, GONE_TEXT, INTO_ITSELF_TEXT, free_copy_name, fresh_name, here_kind, here_sources, planned, put_in_place

# --- Version ------------------------------------------------------------
__version__ = "test_copyplan.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 1: a file onto a folder and a folder onto a file are refused, and the plan is the same one both sides use
# v0.0.45: Initial: the plan of a paste: a copy next to its own file, a name that is taken, a folder into itself, a file that is gone (FI-03)


def here_planned(paths: list[str], folder: str) -> list[Copy]:
    """The plan of a paste of these paths into this folder of this computer, as the window makes it."""
    return planned(here_sources(paths), folder, here_kind(folder), os.path.join)


def test_a_file_pasted_where_it_already_is_gets_a_copy_of_its_own(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    plans = here_planned([str(tmp_path / "note.txt")], str(tmp_path))
    assert [plan.name for plan in plans] == ["note (copy).txt"]
    assert not plans[0].replaces and not plans[0].why_not  # nothing is written over (G-2)


def test_the_next_copy_is_numbered(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    (tmp_path / "note (copy).txt").write_text("x", encoding="utf-8")
    taken = here_kind(str(tmp_path))
    assert free_copy_name("note.txt", lambda name: taken(name) is not None) == "note (copy 2).txt"


def test_a_file_pasted_into_another_folder_keeps_its_name_and_says_what_is_in_the_way(tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "note.txt").write_text("x", encoding="utf-8")
    assert not here_planned([str(here / "note.txt")], str(there))[0].replaces
    (there / "note.txt").write_text("y", encoding="utf-8")
    plan = here_planned([str(here / "note.txt")], str(there))[0]
    assert plan.replaces and plan.target == str(there / "note.txt")


def test_a_folder_is_never_copied_into_itself(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    (outer / "inner").mkdir(parents=True)
    assert here_planned([str(outer)], str(outer / "inner"))[0].why_not == INTO_ITSELF_TEXT
    assert here_planned([str(outer)], str(outer))[0].why_not == INTO_ITSELF_TEXT


def test_a_file_that_is_gone_says_so_and_the_others_stay(tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "note.txt").write_text("x", encoding="utf-8")
    plans = here_planned([str(here / "away.txt"), str(here / "note.txt")], str(there))
    assert plans[0].why_not == GONE_TEXT
    assert not plans[1].why_not


def test_a_file_is_never_written_into_a_folder_of_the_same_name(tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "x").write_text("the file", encoding="utf-8")
    (there / "x").mkdir()
    (there / "x" / "x").write_text("must stay", encoding="utf-8")
    plan = here_planned([str(here / "x")], str(there))[0]
    assert plan.why_not == FOLDER_IN_THE_WAY_TEXT and not plan.replaces  # it is never asked about, and never copied (G-2)


def test_a_folder_never_takes_the_place_of_a_file_of_the_same_name(tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "x").mkdir()
    (here / "x" / "inside.txt").write_text("x", encoding="utf-8")
    (there / "x").write_text("the file", encoding="utf-8")
    plan = here_planned([str(here / "x")], str(there))[0]
    assert plan.why_not == FILE_IN_THE_WAY_TEXT and not plan.replaces


def test_a_name_of_the_same_kind_is_asked_about(tmp_path: Path) -> None:
    here, there = tmp_path / "here", tmp_path / "there"
    for folder in (here, there):
        folder.mkdir()
    (here / "x").mkdir()
    (there / "x").mkdir()
    assert here_planned([str(here / "x")], str(there))[0].replaces


def test_two_things_pasted_where_they_are_never_land_on_one_copy_name(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    (tmp_path / "other.txt").write_text("x", encoding="utf-8")
    plans = here_planned([str(tmp_path / "note.txt"), str(tmp_path / "note.txt")], str(tmp_path))
    assert sorted(plan.name for plan in plans) == ["note (copy 2).txt", "note (copy).txt"]


def test_a_copy_that_replaces_something_here_is_put_in_place_only_when_it_is_whole(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("old", encoding="utf-8")
    fresh = fresh_name(str(tmp_path / "old.txt"))
    pathlib_write(fresh, "new")
    assert put_in_place(fresh, str(tmp_path / "old.txt")) == ""
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "new"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["old.txt"]  # nothing of ours is left behind


def test_an_earlier_copy_that_was_cut_short_says_where_the_persons_own_file_is(tmp_path: Path) -> None:
    """Chrono FTP killed between the two steps leaves the person's own file under the aside name: the next paste says so plainly."""
    (tmp_path / "old.txt").write_text("old", encoding="utf-8")
    pathlib_write(str(tmp_path / "old.txt") + ".chrono-ftp-old", "the person's own, moved aside")
    fresh = fresh_name(str(tmp_path / "old.txt"))
    pathlib_write(fresh, "new")
    with pytest.raises(OSError) as refused:
        put_in_place(fresh, str(tmp_path / "old.txt"))
    said = str(refused.value)
    assert "old.txt.chrono-ftp-old" in said and "rename it back" in said and "nothing of it was lost" in said
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "old"  # what was there is as it was (G-2)


def test_each_step_of_putting_a_copy_in_place_says_what_really_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "old.txt").write_text("old", encoding="utf-8")
    fresh = fresh_name(str(tmp_path / "old.txt"))
    pathlib_write(fresh, "new")
    real_rename = os.rename

    def fails_at(step: int) -> Callable[[str, str], None]:
        count = [0]

        def rename(one: str, two: str) -> None:
            count[0] += 1
            if count[0] == step:
                raise OSError("Read-only file system")
            real_rename(one, two)

        return rename

    monkeypatch.setattr(os, "rename", fails_at(1))  # it could not be moved out of the way
    with pytest.raises(OSError) as refused:
        put_in_place(fresh, str(tmp_path / "old.txt"))
    assert "could not be moved out of the way" in str(refused.value)
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "old"
    monkeypatch.setattr(os, "rename", fails_at(2))  # the copy could not take its place; what was there goes back
    with pytest.raises(OSError) as refused:
        put_in_place(fresh, str(tmp_path / "old.txt"))
    assert "could not be put in its place" in str(refused.value) and "back where it was" in str(refused.value)
    assert (tmp_path / "old.txt").read_text(encoding="utf-8") == "old"


def test_a_copy_that_can_not_be_put_back_says_in_words_where_the_data_is(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never a raw error of the system: the person hears that their own data is safe, and under which name (OR-23)."""
    (tmp_path / "old.txt").write_text("old", encoding="utf-8")
    fresh = fresh_name(str(tmp_path / "old.txt"))
    pathlib_write(fresh, "new")
    real_rename = os.rename
    count = [0]

    def rename(one: str, two: str) -> None:
        count[0] += 1
        if count[0] >= 2:  # the copy can't take the place, and what was there can't go back either
            raise OSError("Read-only file system")
        real_rename(one, two)

    monkeypatch.setattr(os, "rename", rename)
    with pytest.raises(OSError) as refused:
        put_in_place(fresh, str(tmp_path / "old.txt"))
    said = str(refused.value)
    assert "Read-only file system" not in said
    assert "old.txt.chrono-ftp-old" in said and "nothing of it is lost" in said
    assert (tmp_path / "old.txt.chrono-ftp-old").read_text(encoding="utf-8") == "old"  # and it is really there


def pathlib_write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")
