import json
import stat

import pytest

from chrono_ftp.config import connections
from chrono_ftp.core.connection import FTP, Connection

# --- Version ------------------------------------------------------------
__version__ = "test_config_connections.py 0.0.9"

# --- Version history ----------------------------------------------------
# v0.0.9: The optional colour
# v0.0.8: A user name the server accepted
# v0.0.5: Initial


def test_a_list_comes_back_as_it_was_saved() -> None:
    saved = (Connection(name="Shop", host="shop.example.com", user="anna", port=2022, key_file="/keys/id", colour="#aa3355", start_folder="/srv"), Connection(name="Old", host="old.example.com", port=21, protocol=FTP))
    connections.save(saved)
    assert connections.load().connections == saved
    assert connections.load().set_aside is None


def test_no_file_gives_an_empty_list() -> None:
    assert connections.load().connections == ()


def test_only_the_owner_can_read_the_file() -> None:
    connections.save([Connection(name="Shop", host="shop.example.com")])
    assert stat.S_IMODE(connections.list_path().stat().st_mode) == 0o600


def test_a_failed_save_leaves_the_old_list_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    connections.save([Connection(name="Shop", host="shop.example.com")])

    def broken(*args: object) -> None:
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr("os.replace", broken)
        with pytest.raises(OSError):
            connections.save([])
    assert len(connections.load().connections) == 1


@pytest.mark.parametrize("damage", ["{not json", "[]", '{"connections": 5}', "\xff"])
def test_a_damaged_file_is_set_aside_and_the_list_starts_empty(damage: str) -> None:
    path = connections.list_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(damage.encode("latin-1"))
    loaded = connections.load()
    assert loaded.connections == ()
    assert loaded.set_aside is not None and loaded.set_aside.read_bytes() == damage.encode("latin-1")


@pytest.mark.parametrize("bad", [{"host": "a"}, {"name": "a"}, {"name": "a", "host": "b", "port": 70000}, {"name": "a", "host": "b", "port": True}, {"name": "a", "host": "b", "protocol": "gopher"}, {"name": "a", "host": "b", "user": 5}, 7])
def test_a_bad_entry_is_left_out_and_the_good_ones_stay(bad: object) -> None:
    path = connections.list_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"connections": [bad, {"name": "Good", "host": "good.example.com"}]}), encoding="utf-8")
    loaded = connections.load()
    assert [connection.name for connection in loaded.connections] == ["Good"]
    assert loaded.set_aside is not None


def test_a_second_damaged_file_does_not_overwrite_the_first_copy() -> None:
    path = connections.list_path()
    path.parent.mkdir(parents=True)
    path.write_text("one", encoding="utf-8")
    first = connections.load().set_aside
    path.write_text("two", encoding="utf-8")
    second = connections.load().set_aside
    assert first is not None and second is not None and first != second
    assert first.read_text(encoding="utf-8") == "one"


def test_an_accepted_user_goes_to_its_own_connection_only() -> None:
    connections.save([Connection(name="A", host="a"), Connection(name="B", host="b", user="bob")])
    connections.remember_user("A", "anna")
    assert [item.user for item in connections.load().connections] == ["anna", "bob"]


def test_a_connection_removed_in_the_meantime_stays_removed() -> None:
    connections.save([Connection(name="A", host="a")])
    connections.remember_user("gone", "anna")
    assert [item.name for item in connections.load().connections] == ["A"]


def test_a_file_that_can_not_be_read_is_never_replaced_by_a_kept_user() -> None:
    connections.save([Connection(name="A", host="a"), Connection(name="B", host="b")])
    path = connections.list_path()
    before = path.read_bytes()
    path.chmod(0o000)
    try:
        connections.remember_user("A", "anna")
    finally:
        path.chmod(0o600)
    assert path.read_bytes() == before


def test_a_list_with_a_damaged_entry_is_left_as_it_is_by_a_kept_user() -> None:
    connections.save([Connection(name="A", host="a")])
    path = connections.list_path()
    damaged = path.read_text(encoding="utf-8").replace('"host": "a"', '"host": 5')
    path.write_text(damaged, encoding="utf-8")
    connections.remember_user("A", "anna")
    assert path.read_text(encoding="utf-8") == damaged


def test_a_connection_without_a_colour_stays_without_one_and_a_colour_stays_a_colour() -> None:
    connections.save([Connection(name="A", host="a"), Connection(name="B", host="b", colour="#aa3355")])
    assert [item.colour for item in connections.load().connections] == ["", "#aa3355"]
