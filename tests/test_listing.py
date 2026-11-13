import json

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.connections import config_folder
from chrono_ftp.config.settings import BYTES, CLASSIC_KEYS, FILE_MANAGER_KEYS, KILOBYTES, SHORT, TITLE_FULL, TITLE_SHORT
from chrono_ftp.connections.base import RemoteEntry
from chrono_ftp.engine.listing import HISTORY_SHOWN, clipboard_names, extension_of, history_shown, matches_typed, natural_key, parent_folders, plain_key, quoted, same_extension, size_text, summary_text, type_ahead

# --- Version ------------------------------------------------------------
__version__ = "test_listing.py 0.0.40"

# --- Version history ----------------------------------------------------
# v0.0.40: Review: the totals say at least where a size is unknown; names with superscript and circled digits, and digits of other scripts
# v0.0.40: Initial: the logic of block FG without a screen: type-ahead, extensions, sizes, natural sort, totals, clipboard lines, the
#          folders above a path, the history drop-down, and the settings of block FG


# --- type-ahead (FG-01) ----------------------------------------------------------


def test_a_typed_name_finds_the_first_name_that_starts_with_it_from_the_cursor_on() -> None:
    names: list[str | None] = [None, "alpha", "beta", "bravo", "charlie"]
    assert type_ahead(names, "b", 0) == 2
    assert type_ahead(names, "br", 2) == 3
    assert type_ahead(names, "a", 3) == 1  # round the end
    assert type_ahead(names, "x", 0) == -1
    assert type_ahead(names, "", 0) == -1
    assert type_ahead([], "a", 0) == -1


def test_tab_goes_to_the_next_name_and_shift_tab_to_the_one_before() -> None:
    names: list[str | None] = ["bee", "apple", "bat", "boat"]
    assert type_ahead(names, "b", 0, skip_start=True) == 2
    assert type_ahead(names, "b", 3, skip_start=True) == 0  # round the end
    assert type_ahead(names, "b", 2, skip_start=True, backwards=True) == 0
    assert type_ahead(names, "bee", 0, skip_start=True) == 0  # the only one: it stays


def test_small_letters_find_any_case_and_a_capital_finds_only_its_case() -> None:
    assert matches_typed("README.md", "read")
    assert matches_typed("readme.md", "Read") is False
    assert matches_typed("Readme.md", "Read")


def test_the_parent_entry_is_never_found() -> None:
    assert type_ahead([None, "x"], ".", 0) == -1


# --- extensions (FG-02) ---------------------------------------------------------


def test_the_extension_is_what_follows_the_last_dot_of_a_name_that_does_not_start_with_it() -> None:
    assert extension_of("a.b.TXT") == ".txt"
    assert extension_of("README") == ""
    assert extension_of(".bashrc") == ""
    assert extension_of(".config.json") == ".json"


def test_the_same_extension_takes_files_only_and_a_name_without_one_takes_those_without() -> None:
    entries = [RemoteEntry("a.txt", False), RemoteEntry("b.TXT", False), RemoteEntry("c.txt", True), RemoteEntry("README", False), RemoteEntry("d.log", False)]
    assert [entry.name for entry in same_extension(entries, ".txt")] == ["a.txt", "b.TXT"]
    assert [entry.name for entry in same_extension(entries, "")] == ["README"]


# --- sizes (FG-06) ------------------------------------------------------------------


def test_a_size_shows_in_the_chosen_format() -> None:
    assert size_text(1_234_567, BYTES) == "1,234,567"
    assert size_text(1_234_567, KILOBYTES) == "1,206 KB"
    assert size_text(1, KILOBYTES) == "1 KB"  # rounded up: never 0 KB for a file with something in it
    assert size_text(0, KILOBYTES) == "0 KB"
    assert size_text(1_234_567, SHORT) == "1.2 MB"
    assert size_text(512, SHORT) == "512 bytes"


def test_a_negative_size_from_a_server_shows_as_nothing() -> None:
    assert size_text(-5, BYTES) == "0"  # OR-15


# --- natural sort (FG-08) -------------------------------------------------------------


def test_natural_order_puts_file2_before_file10_and_plain_order_does_not() -> None:
    names = ["file10", "File2", "file1", "file", "file2b"]
    assert sorted(names, key=natural_key) == ["file", "file1", "File2", "file2b", "file10"]
    assert sorted(names, key=plain_key) == ["file", "file1", "file10", "File2", "file2b"]


def test_a_superscript_or_circled_digit_after_a_number_sorts_without_a_crash() -> None:
    names = ["x1²", "2²", "①", "x10", "x2", "file٣", "file٢٠", "a³b", "۱۲"]
    ordered = sorted(names, key=natural_key)  # OR-15: never a crash on a name
    assert ordered.index("x2") < ordered.index("x10") and ordered.index("file٣") < ordered.index("file٢٠")  # other scripts' digits by value


def test_a_name_that_is_not_valid_utf8_sorts_without_a_crash() -> None:
    assert natural_key("caf\udce9 2") < natural_key("caf\udce9 10")


# --- selection totals (FG-13) --------------------------------------------------------


def test_the_totals_say_the_selected_size_of_all_and_the_count_of_all() -> None:
    entries = [RemoteEntry("a", False, 1024), RemoteEntry("b", False, 2048), RemoteEntry("c", True, 99)]
    assert summary_text(entries[:1], entries, BYTES) == "1,024 of 3,072, 1 of 3"  # a folder's size is no size of its files
    assert summary_text([], entries, KILOBYTES) == "0 KB of 3 KB, 0 of 3"


# --- the clipboard (FG-11) -----------------------------------------------------------


def test_the_totals_say_at_least_where_the_server_gave_no_size() -> None:
    entries = [RemoteEntry("a", False, 1024), RemoteEntry("b", False, 0, size_known=False), RemoteEntry("c", True, 0, size_known=False)]
    assert summary_text(entries[:1], entries, BYTES) == "1,024 of at least 1,024, 1 of 3"
    assert summary_text(entries[1:2], entries, BYTES) == "at least 0 of at least 1,024, 1 of 3"
    assert summary_text(entries[2:], entries[2:], BYTES) == "0 of 0, 1 of 1"  # a folder has no size of its own to miss


def test_a_name_with_a_space_is_quoted_and_full_paths_go_with_their_folder() -> None:
    assert quoted("a b") == '"a b"' and quoted("ab") == "ab"
    assert clipboard_names(["one.txt", "my file.txt"], "/srv/www", False, False) == 'one.txt\n"my file.txt"'
    assert clipboard_names(["one.txt", "my file.txt"], "/srv/www", True, False) == '/srv/www/one.txt\n"/srv/www/my file.txt"'
    assert clipboard_names(["x"], "/home/me docs", True, True) == '"/home/me docs/x"'


# --- the folders above a path (FG-16) and the history (FG-10) ------------------------------


def test_the_folders_above_a_path_come_nearest_first() -> None:
    assert parent_folders("/srv/www/site", False) == ["/srv/www/site", "/srv/www", "/srv", "/"]
    assert parent_folders("/", False) == ["/"]
    assert parent_folders("/home/me", True) == ["/home/me", "/home", "/"]
    assert parent_folders("", False) == []


def test_the_history_drop_down_lists_the_nearest_folders_first_and_at_most_ten() -> None:
    steps = [f"/{number}" for number in range(15)]
    shown = history_shown(steps)
    assert len(shown) == HISTORY_SHOWN == 10
    assert shown[0] == "/14" and shown[-1] == "/5"


# --- the settings of block FG -----------------------------------------------------------


def test_the_settings_of_block_fg_have_their_standards_and_come_back_as_kept() -> None:
    standard = saved_settings.load()
    assert (standard.two_pane_selection, standard.key_set, standard.size_format, standard.natural_sort, standard.title_path, standard.drag_move) == (False, CLASSIC_KEYS, SHORT, True, TITLE_SHORT, True)
    chosen = saved_settings.Settings(two_pane_selection=True, key_set=FILE_MANAGER_KEYS, size_format=BYTES, natural_sort=False, title_path=TITLE_FULL, drag_move=False)
    saved_settings.save(chosen)
    assert saved_settings.load() == chosen


def test_a_bad_value_of_block_fg_in_the_settings_file_gives_its_standard() -> None:
    folder = config_folder()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / saved_settings.FILE_NAME).write_text(json.dumps({"two_pane_selection": "yes", "key_set": "emacs", "size_format": 7, "natural_sort": None, "title_path": "", "drag_move": 1}), encoding="utf-8")
    loaded = saved_settings.load()
    assert (loaded.two_pane_selection, loaded.key_set, loaded.size_format, loaded.natural_sort, loaded.title_path, loaded.drag_move) == (False, CLASSIC_KEYS, SHORT, True, TITLE_SHORT, True)
