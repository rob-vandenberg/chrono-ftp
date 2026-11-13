"""Block FF without a screen: the directions and the modes, the criteria with their case choice, the timestamp precision a listing
reports, the checklist rows with their counts, a row reversed, moved and renamed files, the deletes and the scope, the masks, and the
deletes, moves and dates that Start sends to the queue. Only the fake servers are used (OR-26)."""

import os
from pathlib import Path

import pytest

from typing import cast

from chrono_ftp.connections.base import RemoteConnection, RemoteEntry
from chrono_ftp.core.connection import Connection
from chrono_ftp.core.errors import LocalActionFailed
from chrono_ftp.core.synchronizing import Criterion, Direction, Mode, Options, counted_before, remember_count, scan_key
from chrono_ftp.engine import synchronize as sync
from chrono_ftp.engine.synchronize import Action, Pair, Row, Total, can_turn, delete_warning, why_not_these_two, local_reader, plan, precision_of, precision_of_pairs, remote_reader, scan, totals, turned, why_no_turn
from chrono_ftp.transfers.job import Job, State, Stopped
from chrono_ftp.engine.sizes import Count
from chrono_ftp.transfers.sidework import SideJob, SideWork, Step, on_this_computer, run_side_job
from tests.fakeservers import PASSWORD, USER, ftp_server, ssh_server
from tests.test_hashes import ftp_of
from tests.test_server_actions import connected_ssh

# --- Version ------------------------------------------------------------
__version__ = "test_synchronize.py 0.0.44"

# --- Version history ----------------------------------------------------
# v0.0.44: The words of a row and of the delete question, gone over one by one (D-097)
# v0.0.43: Initial: block FF, synchronize (FF-01 to FF-13); review round 1: a copy on this computer replaces the file that is
#          there and refuses a link in its place, the delete question never says less than it does, a delete reversed copies from the
#          side the file is still on (the earlier test asserted the wrong side), a move can't be reversed, and a scan can be
#          stopped; review round 2: two folders one inside the other are refused, and a move counts the size of the side its file is on

_ = (PASSWORD, USER)  # the fake servers' own login, used through the helpers below


def _connection() -> Connection:
    return Connection(name="T", host="127.0.0.1", user=USER)


def _no_connection() -> RemoteConnection:
    """A stand-in for a connection that side work on this computer never touches."""
    return cast(RemoteConnection, object())


def write_at(path: Path, text: str, moment: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (moment, moment))


def two_folders(tmp_path: Path) -> tuple[Path, Path]:
    here, there = tmp_path / "here", tmp_path / "there"
    here.mkdir()
    there.mkdir()
    return here, there


def planned(here: Path, there: Path, options: Options, selected: tuple[str, ...] = ()) -> list[Row]:
    """The plan of two folders on this computer: the engine reads both sides through a reader, so no server is needed (FF-14)."""
    pairs = scan(str(here), str(there), local_reader, local_reader, options, lambda: True, selected, False)
    assert pairs is not None
    return plan(pairs, options, 1, {}, False)


def by_name(rows: list[Row]) -> dict[str, Action]:
    return {row.path: row.action for row in rows}


# --- FF-01 the directions and the modes ------------------------------------------------


def test_the_direction_decides_which_way_a_row_goes(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "mine.txt", "mine", 1_600_000_000)
    write_at(there / "theirs.txt", "theirs", 1_600_000_000)
    assert by_name(planned(here, there, Options(direction=Direction.TO_THERE))) == {"mine.txt": Action.COPY_THERE}
    assert by_name(planned(here, there, Options(direction=Direction.TO_HERE))) == {"theirs.txt": Action.COPY_HERE}
    both = by_name(planned(here, there, Options(direction=Direction.BOTH)))
    assert both == {"mine.txt": Action.COPY_THERE, "theirs.txt": Action.COPY_HERE}


def test_mirror_replaces_a_newer_file_on_the_target_and_synchronize_does_not(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "a.txt", "old here", 1_600_000_000)
    write_at(there / "a.txt", "newer there", 1_700_000_000)
    assert planned(here, there, Options(direction=Direction.TO_THERE)) == []  # Synchronize never undoes a newer file
    mirrored = planned(here, there, Options(direction=Direction.TO_THERE, mode=Mode.MIRROR))
    assert by_name(mirrored) == {"a.txt": Action.COPY_THERE} and mirrored[0].ticked


def test_mirror_deletes_what_the_source_no_longer_has(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(there / "gone.txt", "gone", 1_600_000_000)
    rows = planned(here, there, Options(direction=Direction.TO_THERE, mode=Mode.MIRROR))
    assert by_name(rows) == {"gone.txt": Action.DELETE_THERE} and not rows[0].ticked  # the box is off: it starts unticked (FF-08)
    on = planned(here, there, Options(direction=Direction.TO_THERE, mode=Mode.MIRROR, delete_other_side=True))
    assert on[0].ticked


def test_timestamps_only_sets_a_date_and_copies_nothing(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "same.txt", "12345", 1_700_000_000)
    write_at(there / "same.txt", "12345", 1_600_000_000)
    write_at(here / "other.txt", "longer text", 1_700_000_000)
    write_at(there / "other.txt", "short", 1_600_000_000)
    rows = planned(here, there, Options(direction=Direction.TO_THERE, mode=Mode.TIMESTAMPS))
    assert by_name(rows) == {"same.txt": Action.TOUCH_THERE}  # only files of the same size; nothing is copied, nothing deleted


# --- FF-02 the criteria and the case choice ---------------------------------------------


def test_each_criterion_decides_what_counts_as_a_difference(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "a.txt", "12345", 1_700_000_000)
    write_at(there / "a.txt", "12345", 1_600_000_000)  # the same size, another date
    write_at(here / "b.txt", "longer here", 1_600_000_000)
    write_at(there / "b.txt", "short", 1_600_000_000)  # another size, the same date
    by_date = by_name(planned(here, there, Options(direction=Direction.BOTH)))
    assert by_date == {"a.txt": Action.COPY_THERE, "b.txt": Action.COPY_THERE}
    by_size = by_name(planned(here, there, Options(direction=Direction.BOTH, criterion=Criterion.SIZE)))
    assert by_size == {"b.txt": Action.COPY_THERE}  # the dates say nothing here
    assert planned(here, there, Options(direction=Direction.BOTH, criterion=Criterion.NAME)) == []


def test_the_case_choice_makes_two_names_one_file_or_two(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "README", "same", 1_600_000_000)
    write_at(there / "readme", "same", 1_600_000_000)
    apart = by_name(planned(here, there, Options(direction=Direction.BOTH, case_matters=True)))
    assert apart == {"README": Action.COPY_THERE, "readme": Action.COPY_HERE}
    assert planned(here, there, Options(direction=Direction.BOTH, case_matters=False)) == []


# --- FF-03 the timestamp precision -------------------------------------------------------


def test_the_precision_is_the_step_the_listing_reports() -> None:
    assert precision_of([1_599_999_960.0, 1_600_000_020.0]) == 60  # whole minutes: an FTP listing without seconds
    assert precision_of([1_600_000_002.0, 1_600_000_004.0]) == 2
    assert precision_of([1_600_000_001.0, 1_600_000_002.0]) == 1
    assert precision_of([]) == 1


def test_a_date_that_differs_only_within_the_reported_step_is_no_difference() -> None:
    here = RemoteEntry("a.txt", False, 5, 1_600_000_050.0)
    there = RemoteEntry("a.txt", False, 5, 1_600_000_020.0)  # a listing in whole minutes
    pairs = [Pair("", "a.txt", here, there)]
    assert precision_of_pairs(pairs) == 60
    assert plan(pairs, Options(direction=Direction.BOTH), 60) == []
    assert len(plan(pairs, Options(direction=Direction.BOTH), 1)) == 1  # at one second it is a difference


# --- FF-04 the checklist ------------------------------------------------------------------


def test_the_counts_and_sizes_per_action_are_of_the_ticked_rows(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "one.txt", "12345", 1_600_000_000)
    write_at(here / "two.txt", "1234567890", 1_600_000_000)
    rows = planned(here, there, Options(direction=Direction.TO_THERE))
    found = totals(rows, ticked_only=True)
    assert found[Action.COPY_THERE].count == 2 and found[Action.COPY_THERE].size == 15
    rows[0] = Row(rows[0].folder, rows[0].name, rows[0].action, rows[0].here, rows[0].there, False, rows[0].reason)
    assert totals(rows, ticked_only=True)[Action.COPY_THERE].count == 1


# --- FF-05 one row reversed ----------------------------------------------------------------


def test_a_row_turns_around_and_a_delete_becomes_the_copy_that_keeps_the_file() -> None:
    entry = RemoteEntry("a.txt", False, 5, 1_600_000_000.0)
    upload = Row("", "a.txt", Action.COPY_THERE, entry, None, True, "")
    assert turned(upload).action == Action.COPY_HERE and turned(turned(upload)).action == Action.COPY_THERE
    # The file of a delete row is only on the side it would be deleted from, so the copy that keeps it comes from there (review round 1).
    only_there = Row("", "a.txt", Action.DELETE_THERE, None, entry, False, "")
    saved = turned(only_there)
    assert saved.action == Action.COPY_HERE and saved.ticked and saved.there is not None and saved.here is None
    only_here = Row("", "b.txt", Action.DELETE_HERE, entry, None, False, "")
    kept = turned(only_here)
    assert kept.action == Action.COPY_THERE and kept.ticked and kept.here is not None and kept.there is None


def test_a_move_row_can_not_be_reversed_and_says_why() -> None:
    entry = RemoteEntry("new.txt", False, 5, 1_600_000_000.0)
    move = Row("", "new.txt", Action.MOVE_THERE, entry, None, True, "", "", "old.txt")
    assert not can_turn(move) and why_no_turn(move)  # greyed with its reason, never a button that does nothing (OR-20)
    assert turned(move) == move  # and the row stays exactly as it was
    assert can_turn(Row("", "a.txt", Action.COPY_THERE, entry, None, True, ""))


def test_the_delete_question_never_says_less_than_it_does() -> None:
    entry = RemoteEntry("a.txt", False, 5, 1_600_000_000.0)
    folder = RemoteEntry("tree", True)
    one_file = [Row("", "a.txt", Action.DELETE_THERE, None, entry, True, "")]
    assert delete_warning(one_file) == "1 file will be deleted on the server. This can't be brought back. Go on?"
    assert delete_warning([]) == ""
    tree = Row("", "tree", Action.DELETE_THERE, None, folder, True, "")
    unknown = delete_warning([*one_file, tree])
    assert "1 file and 1 folder" in unknown and "deleted with everything in it" in unknown
    assert "was not counted" in unknown  # never a number it does not know (G-2)
    counted = delete_warning([*one_file, Row("", "tree", Action.DELETE_THERE, None, folder, True, "", size=Count(400, 7, 2))])
    assert "7 files more here" in counted and "was not counted" not in counted
    at_least = delete_warning([Row("", "tree", Action.DELETE_THERE, None, folder, True, "", size=Count(400, 7, 2, complete=False))])
    assert "at least 7 files" in at_least
    masked = delete_warning([tree], a_mask_is_on=True)
    assert "what the mask left out of this list goes with it" in masked
    assert "the other folder" in delete_warning([tree], on_a_server=False)  # two folders here are no server (OR-1)


def test_a_copy_on_this_computer_replaces_the_file_that_is_there(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "a.txt", "the new text", 1_700_000_000)
    write_at(there / "a.txt", "the old text", 1_600_000_000)
    on_this_computer(SideWork.COPY, Step(str(here / "a.txt"), str(there / "a.txt")))
    assert (there / "a.txt").read_text(encoding="utf-8") == "the new text"  # a copy replaces, as a copy to a server does
    (there / "deep").mkdir()
    (there / "deep" / "inside.txt").write_text("old", encoding="utf-8")
    (here / "deep").mkdir()
    write_at(here / "deep" / "inside.txt", "new", 1_700_000_000)
    on_this_computer(SideWork.COPY, Step(str(here / "deep"), str(there / "deep"), is_folder=True))
    assert (there / "deep" / "inside.txt").read_text(encoding="utf-8") == "new"


def test_a_copy_on_this_computer_refuses_a_link_in_its_place(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "a.txt", "mine", 1_600_000_000)
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_text("not ours", encoding="utf-8")
    (there / "a.txt").symlink_to(elsewhere)
    with pytest.raises(OSError):
        on_this_computer(SideWork.COPY, Step(str(here / "a.txt"), str(there / "a.txt")))
    assert elsewhere.read_text(encoding="utf-8") == "not ours"  # nothing was written into another tree


def test_two_folders_one_inside_the_other_are_refused(tmp_path: Path) -> None:
    outer = tmp_path / "photos"
    inner = outer / "backup"
    inner.mkdir(parents=True)
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert why_not_these_two(str(outer), str(inner))  # a copy would write the folder into itself (review round 2)
    assert why_not_these_two(str(inner), str(outer))  # in both directions
    assert why_not_these_two(str(outer), str(outer))
    link = tmp_path / "link-to-photos"
    link.symlink_to(outer)
    assert why_not_these_two(str(link), str(inner))  # also when a link hides it
    assert why_not_these_two(str(outer), str(other)) == ""  # two folders that lie apart are sound


def test_a_move_counts_the_size_of_the_side_its_file_is_on() -> None:
    here = RemoteEntry("new.txt", False, 1000, 1_600_000_000.0)
    there = RemoteEntry("old.txt", False, 1000, 1_600_000_000.0)
    to_there = Row("", "new.txt", Action.MOVE_THERE, here, None, True, "", "", "old.txt")
    to_here = Row("", "old.txt", Action.MOVE_HERE, None, there, True, "", "", "new.txt")
    assert totals([to_there])[Action.MOVE_THERE] == Total(1, 1000)  # never 0 bytes for a file of 1000 (G-2)
    assert totals([to_here])[Action.MOVE_HERE] == Total(1, 1000)


# --- FF-07 moved and renamed files ------------------------------------------------------


def test_a_file_under_another_name_becomes_one_move_row(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "new-name.txt", "the same bytes", 1_600_000_000)
    write_at(there / "old-name.txt", "the same bytes", 1_600_000_000)
    options = Options(direction=Direction.TO_THERE, mode=Mode.MIRROR, delete_other_side=True)
    rows = planned(here, there, options)
    assert [(row.action, row.from_name, row.name, row.ticked) for row in rows] == [(Action.MOVE_THERE, "old-name.txt", "new-name.txt", True)]


def test_a_move_whose_dates_differ_is_shown_but_starts_unticked(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "new-name.txt", "the same bytes", 1_700_000_000)
    write_at(there / "old-name.txt", "the same bytes", 1_600_000_000)
    rows = planned(here, there, Options(direction=Direction.TO_THERE, mode=Mode.MIRROR, delete_other_side=True))
    assert [(row.action, row.ticked) for row in rows] == [(Action.MOVE_THERE, False)]


def test_two_files_of_the_same_size_are_never_paired_as_a_move(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "a-new.txt", "same size!!", 1_600_000_000)
    write_at(here / "b-new.txt", "same size!!", 1_600_000_000)
    write_at(there / "a-old.txt", "same size!!", 1_600_000_000)
    rows = planned(here, there, Options(direction=Direction.TO_THERE, mode=Mode.MIRROR, delete_other_side=True))
    assert Action.MOVE_THERE not in {row.action for row in rows}  # which became which is not known: both go the plain way


# --- FF-08 the scope ---------------------------------------------------------------------


def test_existing_files_only_leaves_out_what_is_on_one_side(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "new.txt", "new", 1_600_000_000)
    write_at(here / "both.txt", "later here", 1_700_000_000)
    write_at(there / "both.txt", "there", 1_600_000_000)
    rows = planned(here, there, Options(direction=Direction.TO_THERE, existing_only=True))
    assert by_name(rows) == {"both.txt": Action.COPY_THERE}


def test_no_subfolders_stays_in_the_two_folders(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "deep" / "inside.txt", "deep", 1_600_000_000)
    (there / "deep").mkdir()
    assert planned(here, there, Options(direction=Direction.TO_THERE, no_subfolders=True)) == []
    assert by_name(planned(here, there, Options(direction=Direction.TO_THERE))) == {"deep/inside.txt": Action.COPY_THERE}


def test_the_selected_files_only_looks_at_what_was_picked(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "one.txt", "one", 1_600_000_000)
    write_at(here / "two.txt", "two", 1_600_000_000)
    rows = planned(here, there, Options(direction=Direction.TO_THERE, selected_only=True), ("two.txt",))
    assert by_name(rows) == {"two.txt": Action.COPY_THERE}


# --- FF-09 the masks ----------------------------------------------------------------------


def test_a_mask_decides_what_the_scan_looks_at(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "keep.txt", "keep", 1_600_000_000)
    write_at(here / "leave.bak", "leave", 1_600_000_000)
    rows = planned(here, there, Options(direction=Direction.TO_THERE, mask="*.txt"))
    assert by_name(rows) == {"keep.txt": Action.COPY_THERE}
    both = planned(here, there, Options(direction=Direction.TO_THERE, mask="| *.bak"))
    assert by_name(both) == {"keep.txt": Action.COPY_THERE}


# --- what can't be read -------------------------------------------------------------------


def test_a_folder_that_can_not_be_read_is_told_and_never_taken_for_empty(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    (here / "closed").mkdir(mode=0o000)
    (there / "closed").mkdir()
    write_at(there / "closed-file.txt", "x", 1_600_000_000)
    try:
        rows = planned(here, there, Options(direction=Direction.BOTH))
        closed = [row for row in rows if row.name == "closed"]
        assert [row.action for row in closed] == [Action.NOTHING] and closed[0].reason == sync.UNREADABLE_TEXT
    finally:
        (here / "closed").chmod(0o755)


def test_a_top_folder_that_can_not_be_read_says_so(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    here.chmod(0o000)
    try:
        with pytest.raises(LocalActionFailed):
            planned(here, there, Options())
    finally:
        here.chmod(0o755)


def test_a_link_on_either_side_is_left_out(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    (here / "link").symlink_to("/")
    write_at(there / "link", "a real file", 1_600_000_000)
    assert planned(here, there, Options(direction=Direction.BOTH)) == []


# --- FF-13 the scan statistics ---------------------------------------------------------------


def test_the_file_count_of_a_scan_is_kept_per_folder_pair() -> None:
    key = scan_key("/home/someone", "/srv/www")
    counts = remember_count((), key, 120)
    assert counted_before(counts, key) == 120 and counted_before(counts, scan_key("/a", "/b")) == 0
    assert counted_before(remember_count(counts, key, 130), key) == 130
    assert remember_count(counts, key, -1) == counts  # a number that means nothing changes nothing (OR-15)


def test_the_scan_says_how_many_names_it_looked_at(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    for name in ("a.txt", "b.txt", "c.txt"):
        write_at(here / name, name, 1_600_000_000)
    seen: list[int] = []
    pairs = scan(str(here), str(there), local_reader, local_reader, Options(), lambda: True, (), False, seen.append)
    assert pairs is not None and seen == [1, 2, 3]


def test_a_scan_that_is_stopped_gives_nothing(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "a.txt", "a", 1_600_000_000)
    assert scan(str(here), str(there), local_reader, local_reader, Options(), lambda: False, (), False) is None


# --- FF-10 the deletes, moves and dates as queue items -----------------------------------------


def test_side_work_on_this_computer_deletes_moves_and_sets_dates(tmp_path: Path) -> None:
    folder = tmp_path / "work"
    folder.mkdir()
    write_at(folder / "gone.txt", "gone", 1_600_000_000)
    write_at(folder / "old-name.txt", "move me", 1_600_000_000)
    write_at(folder / "dated.txt", "date me", 1_600_000_000)
    job = Job(_no_connection(), _connection(), str(folder), "/", True, "side")
    run_side_job(job, SideJob(SideWork.DELETE, False, (Step(str(folder / "gone.txt")),)))
    run_side_job(job, SideJob(SideWork.MOVE, False, (Step(str(folder / "old-name.txt"), str(folder / "deep" / "new-name.txt")),)))
    run_side_job(job, SideJob(SideWork.TOUCH, False, (Step(str(folder / "dated.txt"), moment=1_700_000_000.0),)))
    assert not (folder / "gone.txt").exists()
    assert (folder / "deep" / "new-name.txt").read_text(encoding="utf-8") == "move me"  # the folder it goes into is made first
    assert (folder / "dated.txt").stat().st_mtime == 1_700_000_000


def test_one_step_that_fails_never_stops_the_rest_and_the_job_says_so(tmp_path: Path) -> None:
    folder = tmp_path / "work"
    folder.mkdir()
    write_at(folder / "there.txt", "there", 1_600_000_000)
    job = Job(_no_connection(), _connection(), str(folder), "/", True, "side")
    steps = (Step(str(folder / "missing.txt")), Step(str(folder / "there.txt")))
    with pytest.raises(Exception) as refused:
        run_side_job(job, SideJob(SideWork.DELETE, False, steps))
    assert not (folder / "there.txt").exists()  # the one that could be done was done
    assert "1 of 2" in str(refused.value)


def test_a_move_never_replaces_what_is_already_there(tmp_path: Path) -> None:
    folder = tmp_path / "work"
    folder.mkdir()
    write_at(folder / "a.txt", "a", 1_600_000_000)
    write_at(folder / "b.txt", "b", 1_600_000_000)
    job = Job(_no_connection(), _connection(), str(folder), "/", True, "side")
    with pytest.raises(Exception):
        run_side_job(job, SideJob(SideWork.MOVE, False, (Step(str(folder / "a.txt"), str(folder / "b.txt")),)))
    assert (folder / "b.txt").read_text(encoding="utf-8") == "b" and (folder / "a.txt").exists()


def test_side_work_stops_when_the_job_is_stopped(tmp_path: Path) -> None:
    folder = tmp_path / "work"
    folder.mkdir()
    for name in ("a.txt", "b.txt"):
        write_at(folder / name, name, 1_600_000_000)
    job = Job(_no_connection(), _connection(), str(folder), "/", True, "side")
    job.stop_asked.set()
    with pytest.raises(Stopped):
        run_side_job(job, SideJob(SideWork.DELETE, False, (Step(str(folder / "a.txt")),)))
    assert (folder / "a.txt").exists() and job.state == State.WAITING


# --- against the fake servers ------------------------------------------------------------------


def test_the_scan_against_a_fake_sftp_server_finds_both_sides(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "mine.txt", "mine", 1_600_000_000)
    write_at(there / "theirs.txt", "theirs", 1_600_000_000)
    write_at(here / "deep" / "inside.txt", "deep", 1_600_000_000)
    with ssh_server(there) as server:
        connection = connected_ssh(server.port)
        try:
            options = Options(direction=Direction.BOTH)
            pairs = scan(str(here), "/", local_reader, remote_reader(connection), options)
            assert pairs is not None
            assert by_name(plan(pairs, options, precision_of_pairs(pairs))) == {"mine.txt": Action.COPY_THERE, "theirs.txt": Action.COPY_HERE, "deep": Action.COPY_THERE}
        finally:
            connection.close()


def test_the_scan_against_a_fake_ftp_server_finds_both_sides(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    write_at(here / "mine.txt", "mine", 1_600_000_000)
    write_at(there / "theirs.txt", "theirs", 1_600_000_000)
    with ftp_server(there) as port:
        connection = ftp_of(port)
        try:
            options = Options(direction=Direction.BOTH)
            pairs = scan(str(here), "/", local_reader, remote_reader(connection), options)
            assert pairs is not None
            assert by_name(plan(pairs, options, precision_of_pairs(pairs))) == {"mine.txt": Action.COPY_THERE, "theirs.txt": Action.COPY_HERE}
        finally:
            connection.close()


def test_deletes_and_moves_run_on_a_fake_sftp_server(tmp_path: Path) -> None:
    here, there = two_folders(tmp_path)
    _ = here
    write_at(there / "gone.txt", "gone", 1_600_000_000)
    write_at(there / "old-name.txt", "move me", 1_600_000_000)
    with ssh_server(there) as server:
        connection = connected_ssh(server.port)
        try:
            job = Job(connection, _connection(), str(here), "/", True, "side")
            run_side_job(job, SideJob(SideWork.DELETE, True, (Step("/gone.txt"),)))
            run_side_job(job, SideJob(SideWork.MOVE, True, (Step("/old-name.txt", "/deep/new-name.txt"),)))
        finally:
            connection.close()
    assert not (there / "gone.txt").exists()
    assert (there / "deep" / "new-name.txt").read_text(encoding="utf-8") == "move me"


