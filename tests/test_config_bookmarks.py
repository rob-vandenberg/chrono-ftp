import json
import stat

import pytest

from chrono_ftp.config import bookmarks
from chrono_ftp.config.bookmarks import LOCAL, REMOTE, SHARED, TYPED_KEPT, Bookmark, Places, Profile, Typed
from chrono_ftp.engine.browsing import matching_folder, typed_local_path, typed_remote_path
from tests.fakeservers import NOT_AS_ROOT

# --- Version ------------------------------------------------------------
__version__ = "test_config_bookmarks.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial: bookmarks, location profiles and typed paths in bookmarks.json; a damaged file never stops the program; the step
#          of synchronized browsing (FH-02, FH-03, FH-05); a typed path is read from the folder shown, on both sides alike; a file read in part is set aside once; a deleted connection
#          forgets its own places; the same damage is set aside once, privately

ODD_PATH: str = "/srv/b\udcffd"  # a name that is not valid UTF-8, as a server can have it


def all_kinds() -> Places:
    return Places(
        (Bookmark("Home", "/home/anna", LOCAL), Bookmark("Site", "/var/www", REMOTE, "Shop", "Ctrl+1"), Bookmark("Odd", ODD_PATH, REMOTE)),
        (Profile("Work", "/home/anna/work", "/var/www", "Shops", "Shop"), Profile("Logs", "/tmp", "/var/log")),
        (Typed(REMOTE, "Shop", "/etc"), Typed(LOCAL, SHARED, "/usr")),
    )


def test_bookmarks_profiles_and_typed_paths_come_back_as_they_were_saved() -> None:
    bookmarks.save(all_kinds())
    loaded = bookmarks.load()
    assert loaded.places == all_kinds()
    assert loaded.set_aside is None


def test_no_file_gives_nothing() -> None:
    assert bookmarks.load().places == Places()


def test_only_the_owner_can_read_the_file() -> None:
    bookmarks.save(all_kinds())
    assert stat.S_IMODE(bookmarks.places_path().stat().st_mode) == 0o600


@pytest.mark.parametrize("damage", ["{not json", "[]", '{"bookmarks": 5}', '{"profiles": {}}', '{"typed": "x"}', "\xff", "null"])
def test_a_damaged_file_is_set_aside_and_nothing_breaks(damage: str) -> None:
    path = bookmarks.places_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(damage.encode("latin-1"))
    loaded = bookmarks.load()
    assert loaded.places == Places()
    assert loaded.set_aside is not None and loaded.set_aside.read_bytes() == damage.encode("latin-1")


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "a", "path": "/a"},
        {"name": "", "path": "/a", "side": "local"},
        {"name": "a", "path": "/a", "side": "left"},
        {"name": "a", "path": 5, "side": "local"},
        {"name": "a", "path": "/a", "side": "remote", "key": 7},
        {"name": "a", "path": "/" + "x" * 5000, "side": "local"},
        7,
        None,
    ],
)
def test_a_bad_bookmark_is_left_out_and_the_good_ones_stay(bad: object) -> None:
    path = bookmarks.places_path()
    path.parent.mkdir(parents=True)
    good = {"name": "Home", "path": "/home/anna", "side": "local"}
    path.write_text(json.dumps({"bookmarks": [good, bad], "profiles": [{"name": "P", "local": "/a", "remote": "/b"}, {"name": "P"}]}), encoding="utf-8")
    loaded = bookmarks.load()
    assert loaded.places.bookmarks == (Bookmark("Home", "/home/anna", LOCAL),)
    assert loaded.places.profiles == (Profile("P", "/a", "/b"),)
    assert loaded.set_aside is not None


def test_a_local_bookmark_belongs_to_no_connection_even_when_the_file_says_so() -> None:
    path = bookmarks.places_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"bookmarks": [{"name": "Home", "path": "/home", "side": "local", "connection": "Shop"}]}), encoding="utf-8")
    assert bookmarks.load().places.bookmarks == (Bookmark("Home", "/home", LOCAL, SHARED),)


@NOT_AS_ROOT
def test_a_file_that_can_not_be_read_gives_nothing_and_no_crash() -> None:
    bookmarks.save(all_kinds())
    bookmarks.places_path().chmod(0)
    try:
        loaded = bookmarks.load()
    finally:
        bookmarks.places_path().chmod(0o600)
    assert loaded.places == Places()
    assert not loaded.writable  # writing it would lose what it holds
    assert bookmarks.load().writable


def test_the_right_side_shows_the_bookmarks_of_its_connection_and_the_shared_ones() -> None:
    places = Places((Bookmark("Mine", "/a", REMOTE, "Shop"), Bookmark("Theirs", "/b", REMOTE, "Other"), Bookmark("All", "/c", REMOTE), Bookmark("Here", "/d", LOCAL)))
    assert [mark.name for mark in bookmarks.bookmarks_of(places, REMOTE, "Shop")] == ["Mine", "All"]
    assert [mark.name for mark in bookmarks.bookmarks_of(places, LOCAL, "Shop")] == ["Here"]
    assert [mark.name for mark in bookmarks.bookmarks_of(places, LOCAL, "")] == ["Here"]


def test_a_folder_is_a_bookmark_only_once() -> None:
    places = bookmarks.with_bookmark(Places(), Bookmark("a", "/a", REMOTE, "Shop"))
    assert bookmarks.with_bookmark(places, Bookmark("again", "/a", REMOTE, "Shop")) == places
    assert len(bookmarks.with_bookmark(places, Bookmark("a", "/a", REMOTE, "Other")).bookmarks) == 2


def test_typed_paths_are_newest_first_once_and_kept_per_side_and_connection() -> None:
    places = Places()
    for number in range(TYPED_KEPT + 5):
        places = bookmarks.with_typed(places, REMOTE, "Shop", f"/{number}")
    places = bookmarks.with_typed(places, REMOTE, "Shop", "/3")
    places = bookmarks.with_typed(places, LOCAL, "Shop", "/home")
    shop = bookmarks.typed_of(places, REMOTE, "Shop")
    assert shop[0] == "/3" and shop.count("/3") == 1 and len(shop) == TYPED_KEPT
    assert bookmarks.typed_of(places, REMOTE, "Other") == []
    assert bookmarks.typed_of(places, LOCAL, "Other") == ["/home"]


def test_a_renamed_connection_keeps_its_own_places() -> None:
    renamed = bookmarks.with_connection_renamed(all_kinds(), "Shop", "Store")
    assert [mark.connection for mark in renamed.bookmarks] == [SHARED, "Store", SHARED]
    assert [profile.connection for profile in renamed.profiles] == ["Store", SHARED]
    assert renamed.typed[0].connection == "Store"


def test_a_new_bookmark_is_named_after_its_last_folder() -> None:
    assert bookmarks.default_name("/var/www/", REMOTE) == "www"
    assert bookmarks.default_name("/", REMOTE) == "/"
    assert bookmarks.default_name("/home/anna", LOCAL) == "anna"


@pytest.mark.parametrize(
    ("previous", "new", "other", "expected"),
    [
        ("/home/anna/site", "/home/anna/site/css", "/var/www", "/var/www/css"),
        ("/home/anna/site/css", "/home/anna/site", "/var/www/css", "/var/www"),
        ("/home/anna/site/css", "/home/anna/site/js", "/var/www/css", "/var/www/js"),
        ("/home/anna/site", "/home/anna/site/a/b", "/var/www", "/var/www/a/b"),
        ("/home/anna", "/", "/var", None),
        ("/home", "/", "/", None),
    ],
)
def test_a_step_of_one_side_is_the_same_step_on_the_other(previous: str, new: str, other: str, expected: str | None) -> None:
    assert matching_folder(previous, new, other, True) == expected
    assert matching_folder(previous, new, other, False) == expected


@pytest.mark.parametrize(
    ("shown", "typed", "expected"),
    [("/srv/www", "css", "/srv/www/css"), ("/srv/www", "..", "/srv"), ("/srv/www", "/etc/", "/etc"), ("/srv/www", "~", "/home/anna"), ("/srv/www", "~/site/./a", "/home/anna/site/a"), ("/", "a//b/../c", "/a/c")],
)
def test_a_path_typed_for_the_server_is_read_from_the_folder_shown(shown: str, typed: str, expected: str) -> None:
    assert typed_remote_path(shown, typed, "/home/anna") == expected


def test_a_path_typed_for_this_computer_is_read_from_the_folder_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/anna")
    assert typed_local_path("/srv/www", "css") == "/srv/www/css"
    assert typed_local_path("/srv/www", " ../log ") == "/srv/log"
    assert typed_local_path("/srv/www", "~/site") == "/home/anna/site"
    assert typed_local_path("/srv/www", "/etc/") == "/etc"


def test_a_file_read_in_part_is_set_aside_once_and_written_back_cleaned() -> None:
    path = bookmarks.places_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"bookmarks": [{"name": "Home", "path": "/home", "side": "local"}, 7]}), encoding="utf-8")
    first = bookmarks.load()
    assert first.set_aside is not None and first.places.bookmarks == (Bookmark("Home", "/home", LOCAL),)
    second = bookmarks.load()
    assert second.set_aside is None and second.places == first.places
    assert len(list(path.parent.glob(f"{path.name}.damaged*"))) == 1


def test_a_deleted_connection_forgets_its_own_places_and_the_shared_ones_stay() -> None:
    kept = bookmarks.without_connection(all_kinds(), "Shop")
    assert [mark.name for mark in kept.bookmarks] == ["Home", "Odd"]
    assert [profile.name for profile in kept.profiles] == ["Logs"]
    assert kept.typed == (Typed(LOCAL, SHARED, "/usr"),)



def test_the_same_damage_is_set_aside_once_and_privately_also_when_it_can_not_be_written_back(monkeypatch: pytest.MonkeyPatch) -> None:
    path = bookmarks.places_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"bookmarks": [{"name": "Home", "path": "/home", "side": "local"}, 7]}), encoding="utf-8")
    path.chmod(0o644)

    def full(_places: Places) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(bookmarks, "save", full)
    first, second = bookmarks.load(), bookmarks.load()
    assert first.set_aside is not None and first.set_aside == second.set_aside
    assert len(list(path.parent.glob(f"{path.name}.damaged*"))) == 1
    assert stat.S_IMODE(first.set_aside.stat().st_mode) == 0o600
