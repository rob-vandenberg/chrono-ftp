"""Block FD without a screen: the one mask engine (FD-01), the conversion of line endings (FD-02) and of names (FD-03), the choice of a
preset (FD-04), and the options of block FD kept in the settings, an older or damaged file loading as before."""

import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from chrono_ftp.config import parts
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.connections import config_folder
from chrono_ftp.core.masks import EVERYTHING, MaskError, fits, matches, matches_whole, named, parse, problem
from chrono_ftp.core.presets import STANDARD_NAME, Preset, PresetRules, name_problem, preset_for
from chrono_ftp.core.transferoptions import AUTOMATIC, BINARY, CASE_FIRST_UPPER, CASE_LOWER, CASE_UPPER, TEXT, TEXT_MASK, TransferOptions, plain
from chrono_ftp.transfers import conversion
from chrono_ftp.transfers.conversion import BOM, CRLF, LF, LineEnds, Passing, changes_names, chooses_items, convert_bytes, convert_fetched, convert_file, is_text, renamed, upload_copy
from chrono_ftp.transfers.job import Stopped

# --- Version ------------------------------------------------------------
__version__ = "test_masks.py 0.0.41"

# --- Version history ----------------------------------------------------
# v0.0.41: Initial: block FD without a screen (FD-01 to FD-04); review: ~ in a folder rule is the home folder of its side, and a rule
#          ending in /* fits that folder itself; the copy of an upload is for the person alone; a converted copy left behind is tidied

NOW: float = time.mktime((2026, 6, 15, 12, 0, 0, 0, 0, -1))  # a fixed moment, so a relative date never depends on the clock
DAY: float = 86400.0


def fits_text(mask: str, name: str, is_folder: bool = False, folder: str | None = None, size: int | None = None, modified: float | None = None) -> bool:
    return fits(parse(mask), name, is_folder, folder, size, modified, NOW)


# --- the syntax of a mask (FD-01) -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mask", "name", "is_folder", "passes"),
    [
        ("*.txt", "a.txt", False, True),
        ("*.txt", "A.TXT", False, True),  # case does not matter
        ("*.txt", "a.doc", False, False),
        ("*.txt", "folder", True, True),  # without a folder pattern every folder is included
        ("*.txt; *.doc", "a.doc", False, True),
        ("*.txt, *.doc", "a.doc", False, True),  # a comma separates too
        ("*.txt | old*", "old.txt", False, False),  # after the | is what is left out
        ("*.txt | old*", "new.txt", False, True),
        ("| *.bak", "a.bak", False, False),  # nothing before the |: everything else is included
        ("| *.bak", "a.txt", False, True),
        ("build/", "build", True, True),
        ("build/", "code", True, False),
        ("build/", "a.txt", False, True),  # a folder pattern leaves files alone
        ("| .git/", ".git", True, False),
        ("| .git/", ".github", True, True),
        ("a?c.txt", "abc.txt", False, True),
        ("[ab]*.txt", "c.txt", False, False),
        ("", "anything", False, True),  # an empty mask passes everything
        (" ; ;", "anything", True, True),
    ],
)
def test_a_mask_includes_and_excludes_as_its_parts_say(mask: str, name: str, is_folder: bool, passes: bool) -> None:
    assert fits_text(mask, name, is_folder) is passes


def test_an_included_folder_includes_the_folders_inside_it() -> None:
    mask = parse("build/")
    assert fits(mask, "deep", True, "/home/me/build")
    assert fits(mask, "deeper", True, "/home/me/build/deep")
    assert not fits(mask, "deep", True, "/home/me/src")


def test_a_pattern_with_a_path_matches_the_folder_the_item_is_in() -> None:
    assert fits_text("/var/log/*.log", "a.log", folder="/var/log")
    assert fits_text("/var/log/*.log", "a.log", folder="/var/log/")
    assert not fits_text("/var/log/*.log", "a.log", folder="/var/www")
    assert fits_text("*/cache/*", "x.bin", folder="/home/me/cache")
    assert not fits_text("/var/log/*.log", "a.log")  # where it is, is not known: a pattern with a path does not fit
    assert fits_text("/*.conf", "a.conf", folder="/")


@pytest.mark.parametrize(
    ("mask", "size", "passes"),
    [
        ("*>1M", 1024 * 1024, False),
        ("*>1M", 1024 * 1024 + 1, True),
        ("*>=1M", 1024 * 1024, True),
        ("<100K", 100 * 1024 - 1, True),
        ("<=100K", 100 * 1024, True),
        ("<100K", 100 * 1024, False),
        (">=10<20", 15, True),
        (">=10<20", 20, False),
        ("*.iso>1g", 2 * 1024**3, True),  # a unit in lower case too
        (">100", 101, True),  # a plain number is a size in bytes
    ],
)
def test_a_size_condition_holds_for_files_of_that_size(mask: str, size: int, passes: bool) -> None:
    assert fits_text(mask, "file.iso", size=size) is passes


def test_a_condition_on_what_is_not_known_is_not_met() -> None:
    assert not fits_text("*>1K", "a.txt")
    assert not fits_text("*>2025-01-01", "a.txt")
    assert fits_text("| *>1K", "a.txt")  # nor does it leave out


def test_a_date_condition_holds_for_files_changed_then() -> None:
    day = time.mktime((2025, 1, 1, 0, 0, 0, 0, 0, -1))
    assert fits_text(">2025-01-01", "a", modified=day + 1)
    assert not fits_text(">2025-01-01", "a", modified=day)
    assert fits_text(">=2025-01-01", "a", modified=day)
    assert fits_text("<2025-01-01 12:30", "a", modified=day)
    assert not fits_text("<2025-01-01 12:30", "a", modified=day + 13 * 3600)
    assert fits_text(">2025-01-01T08:00:30", "a", modified=day + 9 * 3600)


def test_a_time_ago_counts_back_from_now() -> None:
    assert fits_text(">7D", "a", modified=NOW - 6 * DAY)
    assert not fits_text(">7D", "a", modified=NOW - 8 * DAY)
    assert fits_text("<7D", "a", modified=NOW - 8 * DAY)  # older than seven days
    assert fits_text(">2H", "a", modified=NOW - 3600)
    assert fits_text(">30N", "a", modified=NOW - 60)
    assert fits_text(">1Y", "a", modified=NOW - 300 * DAY)
    assert not fits_text(">1Y", "a", modified=NOW - 400 * DAY)
    midnight = time.mktime((2026, 6, 15, 0, 0, 0, 0, 0, -1))
    assert fits_text(">=today", "a", modified=midnight)
    assert not fits_text(">=today", "a", modified=midnight - 1)
    assert fits_text(">=yesterday", "a", modified=midnight - DAY)
    assert fits_text(">=1DS", "a", modified=midnight - DAY)  # from the start of that day


@pytest.mark.parametrize(
    ("mask", "fault", "start", "end"),
    [
        ("*.log>1Q", "“>1Q”", 5, 8),
        ("a | b | c", "“|”", 6, 7),
        ("build/>1M", "“>1M”", 6, 9),  # a size is a condition of files
        ("*>1M>2M", "“>2M”", 4, 7),
        ("*>", "“>”", 1, 2),
        ("*>2025-13-01", "“>2025-13-01”", 1, 12),
        ("*>2025-02-30", "“>2025-02-30”", 1, 12),
        (">99999999999999999K", "“>99999999999999999K”", 0, 19),
        ("*" * 1001, "longer than", 1000, 1001),
    ],
)
def test_a_mask_that_cant_be_read_says_calmly_where_its_fault_is(mask: str, fault: str, start: int, end: int) -> None:
    with pytest.raises(MaskError) as raised:
        parse(mask)
    assert fault in str(raised.value)
    assert (raised.value.start, raised.value.end) == (start, end)
    assert problem(mask) == str(raised.value)
    assert not matches(mask, "a.log", False)  # what asks with a bad mask gets no match, never a crash (OR-15)


@pytest.mark.parametrize("mask", ["\0", "[", "]]", "***", "*?*?[!", "\udcff.txt", "a<b", "²>1"])
def test_odd_masks_never_crash(mask: str) -> None:
    try:
        parsed = parse(mask)
    except MaskError:
        return
    fits(parsed, "name\udcff", False, "/", 10, 1.0)


def test_named_needs_a_pattern_of_its_kind() -> None:
    assert named(parse("*.txt"), "a.txt", False)
    assert not named(parse("*.txt"), "folder", True)  # selecting *.txt never selects a folder
    assert named(parse("build/"), "build", True)
    assert not named(parse("| *.bak"), "a.txt", False)  # only an exclusion names nothing
    assert not named(parse("*.txt | old*"), "old.txt", False)


def test_the_simple_masks_of_colours_and_thumbnails_keep_their_meaning() -> None:
    assert matches("*.log; *.tmp", "cache.TMP", False)
    assert not matches("*", "folder", True)
    assert matches(".*/", ".git", True)
    assert matches("[", "[", False)
    assert matches("*.jpg", "a.jpg", False, 10, 1.0)


def test_a_whole_value_fits_a_rule_with_slashes_in_its_stars() -> None:
    assert matches_whole("*.example.com", "www.example.com")
    assert matches_whole("/var/www/*", "/var/www/site/html")
    assert matches_whole("/var/www/", "/var/www")
    assert matches_whole("", "anything")
    assert not matches_whole("*.example.com", "example.org")
    assert matches_whole("A*; b*", "Bob")


# --- what passes a transfer (FD-01) -----------------------------------------------------------------------------------------------------


def test_what_passes_a_transfer() -> None:
    passing = Passing(TransferOptions(file_mask="*.txt", skip_hidden=True))
    assert passing.passes("a.txt", False, "/x", 1, 1.0)
    assert not passing.passes("a.doc", False, "/x", 1, 1.0)
    assert not passing.passes(".a.txt", False, "/x", 1, 1.0)
    assert not passing.passes(".git", True, "/x", None, None)
    assert passing.passes("code", True, "/x", None, None)
    assert Passing(TransferOptions()).everything
    assert not Passing(TransferOptions(skip_empty_folders=True)).everything
    bad = Passing(TransferOptions(file_mask="*>1Q"))
    assert "can't be read" in bad.problem and bad.mask == EVERYTHING


def test_only_a_transfer_that_leaves_out_or_changes_is_looked_over_for_rsync() -> None:
    assert not chooses_items(TransferOptions())
    for options in (TransferOptions(file_mask="*.txt"), TransferOptions(skip_hidden=True), TransferOptions(skip_empty_folders=True), TransferOptions(transfer_mode=AUTOMATIC), TransferOptions(name_case=CASE_LOWER), TransferOptions(replace_characters=True)):
        assert chooses_items(options)


def test_plain_options_send_exactly_what_they_are_given() -> None:
    options = plain(TransferOptions(file_mask="*.txt", skip_hidden=True, skip_empty_folders=True, transfer_mode=TEXT, name_case=CASE_UPPER, replace_characters=True, only_newer=True))
    assert not chooses_items(options) and options.only_newer


# --- text mode (FD-02) ------------------------------------------------------------------------------------------------------------------


def test_which_files_are_text() -> None:
    assert not is_text(TransferOptions(), "a.txt")
    assert is_text(TransferOptions(transfer_mode=TEXT), "a.bin")
    automatic = TransferOptions(transfer_mode=AUTOMATIC)
    assert is_text(automatic, "index.HTML") and is_text(automatic, ".htaccess") and not is_text(automatic, "photo.jpg")
    assert not is_text(TransferOptions(transfer_mode=AUTOMATIC, text_mask="*>1Q"), "a.txt")  # a bad text mask makes nothing text
    assert TransferOptions().transfer_mode == BINARY and "*.txt" in TEXT_MASK


@pytest.mark.parametrize(
    ("data", "line_end", "remove_bom", "result"),
    [
        (b"a\r\nb\r\n", LF, False, b"a\nb\n"),
        (b"a\nb\n", CRLF, False, b"a\r\nb\r\n"),
        (b"a\r\nb\n", CRLF, False, b"a\r\nb\r\n"),  # a line end already right stays one
        (b"a\rb", LF, False, b"a\rb"),  # a lone CR stays as it is
        (b"a\r", LF, False, b"a\r"),
        (BOM + b"a\r\n", LF, True, b"a\n"),
        (BOM + b"a", LF, False, BOM + b"a"),
        (b"a" + BOM, LF, True, b"a" + BOM),  # only at the very start
        (b"\xef\xbb", LF, True, b"\xef\xbb"),  # a start that is no whole mark stays
        (b"", LF, True, b""),
        (b"\xff\xfe\x00\r\n\x00\x80", LF, False, b"\xff\xfe\x00\n\x00\x80"),  # bytes that are no text never break the conversion
    ],
)
def test_line_endings_become_those_of_the_target(data: bytes, line_end: bytes, remove_bom: bool, result: bytes) -> None:
    assert convert_bytes(data, line_end, remove_bom) == result


@pytest.mark.parametrize("size", [1, 2, 3, 7])
def test_a_line_end_split_between_blocks_is_still_one(size: int) -> None:
    data = BOM + b"one\r\ntwo\r\n\r\nthree\rfour\r\n"
    ends = LineEnds(LF, True)
    out = b"".join(ends.block(data[place : place + size]) for place in range(0, len(data), size)) + ends.end()
    assert out == b"one\ntwo\n\nthree\rfour\n"


def test_a_file_is_converted_into_a_file_of_its_own_and_a_stop_ends_it(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"x\r\n" * 1000)
    assert convert_file(str(source), str(tmp_path / "b.txt"), LF, False) == 2000
    assert (tmp_path / "b.txt").read_bytes() == b"x\n" * 1000
    with pytest.raises(FileExistsError):
        convert_file(str(source), str(tmp_path / "b.txt"), LF, False)  # never someone else's file
    stop = threading.Event()
    stop.set()
    with pytest.raises(Stopped):
        convert_file(str(source), str(tmp_path / "c.txt"), LF, False, stop)


def test_an_upload_copy_is_private_and_the_source_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(BOM + b"x\r\n")
    copy, size = upload_copy(source, True)
    assert Path(copy).read_bytes() == b"x\n" and size == 2
    assert Path(copy).parent == conversion.converted_folder()
    assert source.read_bytes() == BOM + b"x\r\n"
    Path(copy).unlink()


def test_a_copy_left_by_a_program_that_ended_goes(tmp_path: Path) -> None:
    folder = conversion.converted_folder()
    folder.mkdir(parents=True, exist_ok=True)
    gone = folder / f"{2**22 + 12345}-left{conversion.TEXT_SUFFIX}"  # no process has a number this high
    gone.write_bytes(b"x")
    mine = folder / f"{os.getpid()}-mine{conversion.TEXT_SUFFIX}"
    mine.write_bytes(b"x")
    source = tmp_path / "a.txt"
    source.write_bytes(b"a")
    copy, _size = upload_copy(source, False)
    assert not gone.exists() and mine.exists()
    mine.unlink()
    Path(copy).unlink()


def test_a_fetched_text_is_converted_in_its_part_and_a_failure_leaves_the_part_as_it_came(tmp_path: Path) -> None:
    here = tmp_path / "here"  # the settings folder of the test is in tmp_path
    here.mkdir()
    part = here / "a.txt.chrono-ftp-part"
    part.write_bytes(b"a\r\nb\r\n")
    assert convert_fetched(str(part), "a.txt", False) == 4
    assert part.read_bytes() == b"a\nb\n"
    assert sorted(item.name for item in here.iterdir()) == ["a.txt.chrono-ftp-part"]
    part.write_bytes(b"a\r\n")
    stop = threading.Event()
    stop.set()
    with pytest.raises(Stopped):
        convert_fetched(str(part), "a.txt", False, stop)
    assert part.read_bytes() == b"a\r\n" and sorted(item.name for item in here.iterdir()) == ["a.txt.chrono-ftp-part"]


# --- names (FD-03) ----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "name", "result"),
    [
        (TransferOptions(), "Read Me.TXT", "Read Me.TXT"),
        (TransferOptions(name_case=CASE_LOWER), "Read Me.TXT", "read me.txt"),
        (TransferOptions(name_case=CASE_UPPER), "Read Me.txt", "READ ME.TXT"),
        (TransferOptions(name_case=CASE_FIRST_UPPER), "rEAD me.TXT", "Read me.txt"),
        (TransferOptions(replace_characters=True), 'a:b*c?"<>|\\.txt', "a%3Ab%2Ac%3F%22%3C%3E%7C%5C.txt"),
        (TransferOptions(replace_characters=True), "line\nbreak", "line%0Abreak"),
        (TransferOptions(replace_characters=True, name_case=CASE_LOWER), "A:B", "a%3ab"),
        (TransferOptions(name_case=CASE_LOWER), "caf\udce9.TXT", "caf\udce9.txt"),  # a byte that is no UTF-8 stays itself
    ],
)
def test_a_name_is_converted_as_the_options_say(options: TransferOptions, name: str, result: str) -> None:
    assert renamed(options, name) == result
    assert changes_names(options) is (options.name_case != TransferOptions().name_case or options.replace_characters)


# --- presets (FD-04) --------------------------------------------------------------------------------------------------------------------


def test_the_first_preset_whose_rules_all_fit_is_chosen() -> None:
    web = Preset("Web", TransferOptions(transfer_mode=AUTOMATIC), PresetRules(host="*.example.com", remote_folder="/var/www/*"))
    backups = Preset("Backups", TransferOptions(resume="on"), PresetRules(user="backup"))
    by_hand = Preset("By hand", TransferOptions())  # without rules: only chosen by hand
    presets = (by_hand, web, backups)
    assert preset_for(presets, "www.example.com", "me", "/var/www/site", "/home/me") is web
    assert preset_for(presets, "www.example.com", "me", "/home/me", "/home/me") is None  # one rule fits, the other not
    assert preset_for(presets, "other.org", "backup", "/", "/") is backups
    assert preset_for((), "a", "b", "/", "/") is None


def test_a_preset_name_is_free_and_not_the_standard() -> None:
    assert name_problem("Web", ["web"])
    assert name_problem(STANDARD_NAME.upper(), [])
    assert name_problem("  ", [])
    assert name_problem("x" * 101, [])
    assert not name_problem("Web", ["Backups"])


# --- the settings (FD-01 to FD-06) -------------------------------------------------------------------------------------------------------


def test_the_options_presets_and_masks_are_kept() -> None:
    options = TransferOptions(file_mask="*.txt | old*", skip_hidden=True, skip_empty_folders=True, transfer_mode=AUTOMATIC, text_mask="*.md", remove_bom=True, name_case=CASE_FIRST_UPPER, replace_characters=True)
    preset = Preset("Web", replace_move(options), PresetRules("*.example.com", "me", "/var/www/*", "~/sites"))
    saved_settings.save(saved_settings.Settings(transfer_options=options, presets=(preset,), mask_history=("*.txt", ">1M")))
    loaded = saved_settings.load()
    assert loaded.transfer_options == options and loaded.presets == (replace(preset, options=options),) and loaded.mask_history == ("*.txt", ">1M")  # never a move
    data = json.loads((config_folder() / saved_settings.FILE_NAME).read_text(encoding="utf-8"))
    assert "delete_source" not in data["presets"][0]["options"]  # a move is never kept, not in a preset either


def replace_move(options: TransferOptions) -> TransferOptions:
    return replace(options, delete_source=True)


def test_an_older_settings_file_loads_with_the_standard_of_block_fd() -> None:
    folder = config_folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / saved_settings.FILE_NAME).write_text(json.dumps({"transfer_options": {"resume": "on"}}), encoding="utf-8")
    loaded = saved_settings.load()
    assert loaded.transfer_options == TransferOptions(resume="on") and loaded.presets == () and loaded.mask_history == ()


def test_damaged_values_of_block_fd_give_their_standard() -> None:
    folder = config_folder()
    folder.mkdir(parents=True, exist_ok=True)
    options = {"file_mask": "*>1Q", "transfer_mode": "ebcdic", "text_mask": 5, "name_case": "wavy", "skip_hidden": "yes", "remove_bom": True}
    presets: list[object] = [{"name": "Good", "options": dict[str, object]()}, {"name": "good"}, {"name": ""}, {"name": STANDARD_NAME}, "broken", {"name": "Rules", "rules": {"host": 3, "user": "me"}}]
    (folder / saved_settings.FILE_NAME).write_text(json.dumps({"transfer_options": options, "presets": presets, "mask_history": ["*.a", 7, "*.a", "*.b"]}), encoding="utf-8")
    loaded = saved_settings.load()
    assert loaded.transfer_options == TransferOptions(remove_bom=True)
    assert [preset.name for preset in loaded.presets] == ["Good", "Rules"]
    assert loaded.presets[1].rules == PresetRules(user="me")
    assert loaded.mask_history == ("*.a", "*.b")


def test_the_mask_history_keeps_the_newest_first_once() -> None:
    history: tuple[str, ...] = ()
    for mask in ("a", "b", "a", " ", "c"):
        history = saved_settings.remember_mask(history, mask)
    assert history == ("c", "a", "b")
    for number in range(30):
        history = saved_settings.remember_mask(history, str(number))
    assert len(history) == saved_settings.MOST_MASKS_KEPT and history[0] == "29"


def test_a_folder_rule_with_a_tilde_is_the_home_folder_of_its_side() -> None:
    sites = Preset("Sites", TransferOptions(), PresetRules(local_folder="~/Sites/*"))
    served = Preset("Served", TransferOptions(), PresetRules(remote_folder="~/www/*"))
    assert preset_for((sites,), "h", "u", "/", "/home/me/Sites/blog", local_home="/home/me") is sites
    assert preset_for((served,), "h", "u", "/home/web/www/blog", "/", remote_home="/home/web") is served
    assert preset_for((served,), "h", "u", "/home/web/www/blog", "/") is None  # the login's home not known yet: no guess
    assert preset_for((Preset("Home", TransferOptions(), PresetRules(local_folder="~")),), "h", "u", "/", "/home/me", local_home="/home/me") is not None


def test_a_folder_rule_ending_in_slash_star_fits_that_folder_itself() -> None:
    assert matches_whole("/var/www/*", "/var/www")
    assert matches_whole("/var/www/*", "/var/www/")
    assert matches_whole("/var/www/*", "/var/www/site")
    assert not matches_whole("/var/www/*", "/var/wwwold")
    assert matches_whole("/*", "/")


def test_the_copy_of_an_upload_is_for_the_person_alone(tmp_path: Path) -> None:
    folder = conversion.converted_folder()
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o775)  # as an earlier version made it
    source = tmp_path / "a.txt"
    source.write_bytes(b"secret\r\n")
    source.chmod(0o600)
    before = os.umask(0o002)
    try:
        copy, _size = upload_copy(source, False)
    finally:
        os.umask(before)
    assert Path(copy).stat().st_mode & 0o777 == 0o600 and folder.stat().st_mode & 0o777 == 0o700
    Path(copy).unlink()


def test_a_converted_copy_left_behind_is_tidied_and_one_without_our_note_never_touched(tmp_path: Path) -> None:
    here = tmp_path / "here"  # the settings folder of the test is in tmp_path
    here.mkdir()
    part = here / "a.txt.chrono-ftp-part"
    left = here / ("a.txt.chrono-ftp-part" + conversion.TEXT_SUFFIX)
    left.write_bytes(b"half")
    parts.claim(parts.download_key(str(left)), parts.PartRecord(conversion.TEXT_COPY_SOURCE, 0, 0.0, 0))  # as one that ended unexpectedly left it
    part.write_bytes(b"a\r\n")
    convert_fetched(str(part), "a.txt", False)
    assert sorted(item.name for item in here.iterdir()) == [part.name] and part.read_bytes() == b"a\n"
    assert parts.lookup(parts.download_key(str(left))) is None
    foreign = here / left.name
    foreign.write_bytes(b"someone's")  # the same name, without our note
    part.write_bytes(b"b\r\n")
    convert_fetched(str(part), "a.txt", False)
    assert foreign.read_bytes() == b"someone's" and part.read_bytes() == b"b\n"
    assert sorted(item.name for item in here.iterdir()) == sorted([part.name, foreign.name])
