"""Block FQ without a screen: masks and the first rule of file colours (FQ-01), which thumbnails are fetched and the reading of a file
into memory that stops when told (FQ-02), and the choices of the looks kept in the settings, a damaged value giving its standard (FQ-03)."""

import json
import threading
from collections.abc import Generator
from pathlib import Path

import pytest

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.connections import config_folder
from chrono_ftp.config.settings import LARGE, MEDIUM, THUMBNAIL_MASK, ToolbarChoices
from chrono_ftp.connections.base import RemoteConnection, RemoteEntry
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import LocalReadFailed, RemoteActionFailed
from chrono_ftp.core.filecolours import ColourRule, rule_for
from chrono_ftp.core.masks import matches
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.engine.thumbnails import MEGABYTE, Key, fits, key_of, read_local, to_fetch
from tests.fakeservers import PASSWORD, USER, ftp_server, ssh_server
from tests.test_connections import trusted_ssh

# --- Version ------------------------------------------------------------
__version__ = "test_looks.py 0.0.39"

# --- Version history ----------------------------------------------------
# v0.0.39: Initial: block FQ without a screen (FQ-01 to FQ-03)


def a_file(name: str, size: int = 1000, special: bool = False, size_known: bool = True) -> RemoteEntry:
    return RemoteEntry(name, False, size, 1.0, size_known=size_known, special=special)


# --- masks and file colours (FQ-01) -----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mask", "name", "is_folder", "fits_it"),
    [
        ("*.log; *.tmp", "server.log", False, True),
        ("*.log; *.tmp", "cache.TMP", False, True),  # case does not matter
        ("*.log,*.tmp", "a.tmp", False, True),  # a comma separates too
        ("*.log; *.tmp", "server.log.gz", False, False),
        ("*.log", "logs.log", True, False),  # a plain mask is for files
        ("build/", "build", True, True),  # one that ends in / is for folders
        ("build/", "build", False, False),
        ("*", "anything", False, True),
        ("*", "folder", True, False),
        ("", "a.log", False, False),
        (" ; ;", "a.log", False, False),
        ("[", "[", False, True),  # an odd mask fails only itself, never the program (OR-15)
    ],
)
def test_a_mask_fits_names_as_its_patterns_say(mask: str, name: str, is_folder: bool, fits_it: bool) -> None:
    assert matches(mask, name, is_folder) is fits_it


def test_the_first_rule_that_fits_colours_the_row() -> None:
    rules = (ColourRule("*.log", "#ff0000"), ColourRule("*.log; *.txt", "#00ff00", "#000000"), ColourRule("*.txt", "#0000ff"))
    assert rule_for(rules, "a.log", False) == rules[0]
    assert rule_for(rules, "a.txt", False) == rules[1]  # the earlier of the two that fit
    assert rule_for(rules, "a.bin", False) is None


def test_a_rule_without_a_colour_is_passed_over() -> None:
    rules = (ColourRule("*.log"), ColourRule("*", "", "#202020"))
    assert rule_for(rules, "a.log", False) == rules[1]


# --- which thumbnails are fetched (FQ-02) ---------------------------------------------------------------------------------------


def test_only_image_files_within_the_size_limit_get_a_thumbnail() -> None:
    limit = 5 * MEGABYTE
    assert fits(a_file("a.jpg", 100), limit, THUMBNAIL_MASK)
    assert fits(a_file("A.PNG", limit), limit, THUMBNAIL_MASK)
    assert not fits(a_file("big.jpg", limit + 1), limit, THUMBNAIL_MASK)  # over the limit
    assert not fits(a_file("notes.txt", 100), limit, THUMBNAIL_MASK)  # outside the mask
    assert not fits(a_file("empty.png", 0), limit, THUMBNAIL_MASK)
    assert not fits(RemoteEntry("pictures.jpg", True), limit, THUMBNAIL_MASK)  # a folder
    assert not fits(a_file("pipe.png", 10, special=True), limit, THUMBNAIL_MASK)  # reading a pipe could wait forever
    assert not fits(a_file("unknown.png", 0, size_known=False), limit, THUMBNAIL_MASK)  # a size that is not known could be anything


def test_the_thumbnails_to_fetch_are_those_in_view_in_their_order_without_those_known() -> None:
    in_view = [a_file("c.png"), RemoteEntry("folder", True), a_file("a.jpg"), a_file("b.txt"), a_file("d.gif", 10 * MEGABYTE)]
    known = {key_of("login", "/pics", in_view[2])}
    keys = to_fetch("login", "/pics", in_view, 5 * MEGABYTE, THUMBNAIL_MASK, known)
    assert [key.name for key in keys] == ["c.png"]
    assert keys[0] == Key("login", "/pics", "c.png", 1000, 1.0)


def test_a_changed_file_is_fetched_again() -> None:
    before = a_file("a.jpg", 100)
    known = {key_of("", "/pics", before)}
    changed = RemoteEntry("a.jpg", False, 120, 2.0)
    assert [key.name for key in to_fetch("", "/pics", [changed], MEGABYTE, THUMBNAIL_MASK, known)] == ["a.jpg"]


def test_a_file_here_is_read_into_memory_and_stops_when_told(tmp_path: Path) -> None:
    picture = tmp_path / "a.png"
    picture.write_bytes(b"x" * 200_000)
    assert read_local(str(picture), MEGABYTE, lambda: True) == b"x" * 200_000
    assert read_local(str(picture), MEGABYTE, lambda: False) is None
    with pytest.raises(LocalReadFailed):
        read_local(str(picture), 100_000, lambda: True)  # larger than the limit by now
    with pytest.raises(LocalReadFailed):
        read_local(str(tmp_path / "gone.png"), MEGABYTE, lambda: True)


def test_a_worker_tells_whether_it_is_idle_and_whether_work_waits() -> None:
    answers: list[object] = []
    worker = Worker(lambda call: call())
    started, release = threading.Event(), threading.Event()
    seen: list[bool] = []

    def first() -> None:
        started.set()
        release.wait(5)
        seen.append(worker.others_waiting())

    assert worker.idle()
    worker.submit(first, answers.append, answers.append)
    assert started.wait(5)
    assert not worker.idle()
    worker.submit(lambda: None, answers.append, answers.append)
    release.set()
    done = threading.Event()
    worker.submit(done.set, answers.append, answers.append)
    assert done.wait(5)
    assert seen == [True]  # the first one saw the second wait behind it
    worker.stop()


@pytest.fixture(params=["sftp", "ftp"])
def server(request: pytest.FixtureRequest, tmp_path: Path) -> Generator[tuple[RemoteConnection, Path]]:
    root = tmp_path / "server"
    root.mkdir()
    if request.param == "ftp":
        with ftp_server(root) as port:
            connection: RemoteConnection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
            connection.connect()
            yield connection, root
            connection.close()
    else:
        with ssh_server(root, shell=False) as fake:
            connection = trusted_ssh(fake)
            yield connection, root
            connection.close()


def test_a_server_file_is_read_into_memory_and_the_connection_goes_on(server: tuple[RemoteConnection, Path], tmp_path: Path) -> None:
    connection, root = server
    data = bytes(range(256)) * 1000
    (root / "a.png").write_bytes(data)
    before = set(tmp_path.rglob("*"))
    assert connection.read_file("/a.png", MEGABYTE, lambda: True) == data
    assert set(tmp_path.rglob("*")) == before  # nothing was written on this computer
    assert [entry.name for entry in connection.list_folder("/")] == ["a.png"]  # the next request is answered rightly


def test_a_server_file_that_is_too_large_or_must_give_way_is_not_read_whole(server: tuple[RemoteConnection, Path]) -> None:
    connection, root = server
    (root / "a.png").write_bytes(b"y" * 300_000)
    with pytest.raises(RemoteActionFailed):
        connection.read_file("/a.png", 100_000, lambda: True)
    asked: list[int] = []

    def until_second_block() -> bool:
        asked.append(1)
        return len(asked) < 2

    assert connection.read_file("/a.png", MEGABYTE, until_second_block) is None
    assert connection.read_file("/a.png", MEGABYTE, lambda: True) == b"y" * 300_000  # the connection goes on after a stop
    with pytest.raises(RemoteActionFailed):
        connection.read_file("/gone.png", MEGABYTE, lambda: True)


# --- the choices kept (FQ-01 to FQ-03) --------------------------------------------------------------------------------------------


def write_settings(data: object) -> None:
    config_folder().mkdir(parents=True, exist_ok=True)
    (config_folder() / saved_settings.FILE_NAME).write_text(json.dumps(data), encoding="utf-8")


def test_the_looks_are_kept_and_read_back() -> None:
    chosen = saved_settings.Settings(
        file_colours=(ColourRule("*.log", "#ff8800"), ColourRule("build/", "", "#202830")),
        thumbnail_megabytes=12,
        thumbnail_mask="*.png",
        toolbars=ToolbarChoices(("queue", "top"), LARGE, ("local_files/upload",)),
    )
    saved_settings.save(chosen)
    assert saved_settings.load() == chosen


def test_an_older_settings_file_gives_the_standard_looks() -> None:
    write_settings({"show_hidden": False})
    loaded = saved_settings.load()
    assert not loaded.show_hidden
    assert loaded.file_colours == ()
    assert (loaded.thumbnail_megabytes, loaded.thumbnail_mask) == (saved_settings.THUMBNAIL_MEGABYTES, THUMBNAIL_MASK)
    assert loaded.toolbars == ToolbarChoices()


def test_damaged_looks_give_their_standard_and_a_damaged_rule_goes_alone() -> None:
    damaged = {
        "file_colours": [{"mask": "*.log", "text": "#ff0000"}, {"mask": "*.x", "text": "red"}, {"mask": "", "text": "#00ff00"}, "rule", {"mask": "*.y", "background": "#0000ff"}],
        "thumbnail_megabytes": 100_000,
        "thumbnail_mask": ["*.png"],
        "toolbars": {"hidden": "top", "icon_size": "huge", "text_under": ["top/queue", 7, "x" * 1000]},
    }
    write_settings(damaged)
    loaded = saved_settings.load()
    assert loaded.file_colours == (ColourRule("*.log", "#ff0000"), ColourRule("*.y", "", "#0000ff"))
    assert loaded.thumbnail_megabytes == saved_settings.THUMBNAIL_MEGABYTES
    assert loaded.thumbnail_mask == THUMBNAIL_MASK
    assert loaded.toolbars == ToolbarChoices((), MEDIUM, ("top/queue",))
    write_settings({"toolbars": [1], "file_colours": {"mask": "*"}})
    assert saved_settings.load().toolbars == ToolbarChoices()
