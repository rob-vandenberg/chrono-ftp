"""Block FP without a screen: the preferences as kept (FP-01), the session log (FP-02), the server information and free space (FP-03),
the clean-up of stored data (FP-04), the licence (FP-05) and the update check against a fake answer, never the real GitHub (FP-06)."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from chrono_ftp.config import connections as saved_connections
from chrono_ftp.config import settings as saved_settings
from chrono_ftp.config.connections import config_folder
from chrono_ftp.connections import sessionlog, trust
from chrono_ftp.connections.base import Capabilities, FreeSpace
from chrono_ftp.connections.ftp import FtpConnection, certificate_lines
from chrono_ftp.connections.ssh import SshConnection, space_from_df
from chrono_ftp.connections.base import UnknownServer
from chrono_ftp.core import appinfo
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import CredentialError
from chrono_ftp.core.logrules import DEBUG, DETAILED, NORMAL, OFF, LogRules
from chrono_ftp.engine import cleanup, serverinfo, updatecheck
from tests.conftest import MemoryStore
from tests.fakeservers import PASSWORD, USER, Passwd, ftp_server, self_signed_certificate, ssh_server
from tests.test_server_actions import connected_ssh

# --- Version ------------------------------------------------------------
__version__ = "test_program.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: block FP: review round 1: a key over several lines, a password store that can't be reached; preferences kept and checked, the session log never holds a password or key (also not FTP's PASS line)
#          and is rotated, server information and free space, clean-up of exactly what was ticked, the licence, the version comparison
#          and the update check against fake answers

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def log_text() -> str:
    folder = sessionlog.log_folder()
    return "".join(path.read_text("utf-8") for path in sorted(folder.iterdir())) if folder.is_dir() else ""


# --- preferences (FP-01) ------------------------------------------------------


def test_an_older_settings_file_loads_with_the_new_choices_at_their_standard() -> None:
    config_folder().mkdir(parents=True)
    (config_folder() / saved_settings.FILE_NAME).write_text(json.dumps({"tree_place": "above", "show_hidden": False, "transfers_at_once": 4}), "utf-8")
    loaded = saved_settings.load()
    assert (loaded.tree_place, loaded.show_hidden, loaded.transfers_at_once) == ("above", False, 4)  # today's choices keep their values
    assert loaded.confirm_close_running and loaded.confirm_delete and loaded.confirm_drop
    assert loaded.double_click == saved_settings.OPEN and loaded.refresh_seconds == 0 and not loaded.columns_per_tab
    assert loaded.log_level == OFF  # the session log is off by default


def test_the_preferences_are_kept_and_bad_values_give_their_standard() -> None:
    chosen = saved_settings.Settings(confirm_delete=False, double_click=saved_settings.TRANSFER, refresh_seconds=30, columns_per_tab=True, log_level=DETAILED, log_size_megabytes=2, logs_kept=5)
    saved_settings.save(chosen)
    assert saved_settings.load() == chosen
    damaged = {"confirm_delete": "no", "double_click": "explode", "refresh_seconds": 2, "log_level": 9, "log_size_megabytes": 0, "logs_kept": -1, "confirm_drop": 1}
    (config_folder() / saved_settings.FILE_NAME).write_text(json.dumps(damaged), "utf-8")
    loaded = saved_settings.load()
    assert loaded.confirm_delete and loaded.confirm_drop
    assert loaded.double_click == saved_settings.OPEN
    assert loaded.refresh_seconds == 0  # below the fewest seconds: off, never a refresh that floods the server
    assert (loaded.log_level, loaded.log_size_megabytes, loaded.logs_kept) == (OFF, 5, 3)


# --- the session log (FP-02) ----------------------------------------------------


def test_the_log_takes_out_passwords_keys_and_the_pass_line() -> None:
    secrets = ("hunter2",)
    assert sessionlog.cleaned("PASS hunter2", secrets) == "PASS ***"
    assert sessionlog.cleaned("pass anything-at-all", ()) == "pass ***"  # FTP's PASS, whatever the password
    assert "hunter2" not in sessionlog.cleaned("run echo hunter2 | something", secrets)
    assert sessionlog.cleaned("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----", ()) == sessionlog.KEY_TEXT
    assert "abc" not in sessionlog.cleaned("password=abc", ())
    assert sessionlog.cleaned("readdir /home/anna", secrets) == "readdir /home/anna"


def test_a_private_key_over_several_lines_of_output_is_never_written() -> None:
    sessionlog.set_rules(LogRules(DETAILED))
    log = sessionlog.log_for("key")
    log.answered_lines(["before", "-----BEGIN OPENSSH PRIVATE KEY-----", "b3BlbnNzaC1rZXktdjEAAAA", "-----END OPENSSH PRIVATE KEY-----", "after"])
    text = log_text()
    assert "b3BlbnNzaC1rZXktdjEAAAA" not in text and sessionlog.KEY_TEXT in text and "before" in text and "after" in text


def test_the_log_is_off_by_default_and_writes_nothing(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root, shell=False) as server:
        connection = connected_ssh(server.port)
        connection.list_folder("/")
        connection.close()
    assert not sessionlog.log_folder().exists()


def test_the_sftp_log_holds_requests_and_answers_and_never_the_password(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("x", "utf-8")
    sessionlog.set_rules(LogRules(DEBUG))
    with ssh_server(root, commands=Passwd(PASSWORD)) as server:  # the test answers every command: nothing runs on this computer
        connection = connected_ssh(server.port)
        connection.list_folder("/")
        connection.run("echo done")
        connection.close()
    text = log_text()
    assert "> readdir /" in text and "a.txt" in text and "> run echo done" in text and "exit status 127" in text and "Not here." in text
    assert "Cipher" in text  # the debug level tells the encryption agreed
    assert PASSWORD not in text
    assert oct(os.stat(sessionlog.log_folder() / sessionlog.file_name("T")).st_mode & 0o777) == "0o600"  # it holds server paths: private


def test_the_ftp_log_never_holds_the_pass_line_with_its_password(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    sessionlog.set_rules(LogRules(DEBUG))
    with ftp_server(root) as port:
        connection = FtpConnection(Connection(name="F", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        connection.list_folder("/")
        connection.close()
    text = log_text()
    assert f"> USER {USER}" in text and "> PASS ***" in text and "< 230" in text and "> MLSD /" in text
    assert PASSWORD not in text


def test_the_new_and_old_passwords_of_passwd_are_never_written(tmp_path: Path) -> None:
    passwd = Passwd(PASSWORD)
    sessionlog.set_rules(LogRules(DEBUG))
    with ssh_server(tmp_path, commands=passwd) as server:
        connection = connected_ssh(server.port)
        try:
            assert connection.change_password(PASSWORD, "a-new-secret").changed
        finally:
            connection.close()
    text = log_text()
    assert "passwd" in text and "the password was changed" in text
    assert "a-new-secret" not in text and PASSWORD not in text


def test_a_log_is_rotated_by_size_and_keeps_only_so_many(tmp_path: Path) -> None:
    sessionlog.set_rules(LogRules(NORMAL, 1, 2))
    log = sessionlog.log_for("rotated")
    line = "x" * 1000
    for _ in range(4000):  # about four megabytes: rotated three times
        log.note(line)
    path = log.path
    assert path is not None
    assert path.exists() and sessionlog.rotated(path, 1).exists() and sessionlog.rotated(path, 2).exists()
    assert not sessionlog.rotated(path, 3).exists()
    for kept in (path, sessionlog.rotated(path, 1), sessionlog.rotated(path, 2)):
        assert kept.stat().st_size <= sessionlog.MEGABYTE
    sessionlog.set_rules(LogRules(NORMAL, 1, 0))
    for _ in range(1100):
        log.note(line)
    assert not sessionlog.rotated(path, 1).exists()  # with none kept, the log starts afresh


def test_a_log_that_can_not_be_written_says_so_and_never_fails(tmp_path: Path) -> None:
    sessionlog.set_rules(LogRules(NORMAL))
    sessionlog.state_folder().parent.mkdir(parents=True, exist_ok=True)
    sessionlog.state_folder().write_text("not a folder", "utf-8")  # the log folder can't be made
    log = sessionlog.log_for("blocked")
    seen: list[str] = []
    log.watch(seen.extend)
    log.note("hello")
    log.unwatch(seen.extend)
    assert log.problem.startswith("The session log could not be written")
    assert seen and seen[0].endswith(". hello")  # the Log window still sees it


def test_a_log_name_is_safe_for_any_connection_name() -> None:
    assert "/" not in sessionlog.file_name("../../etc/passwd")
    assert sessionlog.file_name("a/b") != sessionlog.file_name("a_b")


# --- server information and free space (FP-03) ------------------------------------


def test_df_tells_the_free_space_and_odd_output_gives_unknown() -> None:
    output = "Filesystem     1024-blocks      Used Available Capacity Mounted on\n/dev/my disk    1000000    400000    600000      40% /home/a b\n"
    assert space_from_df(output) == FreeSpace(600000 * 1024, 1000000 * 1024, "df")
    for odd in ("", "Filesystem\n", "a\nb c d\n", "x\n/dev 99999999999999999999 1 2 3% /\n"):
        assert space_from_df(odd) is None


def test_sizes_and_space_read_as_a_person_says_them() -> None:
    assert serverinfo.size_text(512) == "512 bytes"
    assert serverinfo.size_text(1536) == "1.5 KB"
    assert serverinfo.space_text(None) == serverinfo.NO_SPACE_TEXT
    assert serverinfo.space_text(FreeSpace(None, None, "df")) == serverinfo.NO_SPACE_TEXT
    assert serverinfo.space_text(FreeSpace(2 * 1024**3, 4 * 1024**3, "df")) == "2.0 GB free of 4.0 GB (by df)"


def test_the_abilities_of_ftp_leave_out_what_needs_a_shell() -> None:
    labels = [label for label, _value in serverinfo.ability_lines(Capabilities(resume=True), True)]
    assert "A shell for commands" not in labels and "Find files on the server" not in labels
    assert dict(serverinfo.ability_lines(Capabilities(resume=True), True))["Continue a transfer that broke off"] == serverinfo.YES
    without_shell = dict(serverinfo.ability_lines(Capabilities(), False))
    assert without_shell["A shell for commands"] == serverinfo.NO and without_shell["System"] == serverinfo.UNKNOWN


def report_lines(report: serverinfo.Report) -> dict[str, str]:
    return {f"{title}/{label}": value for title, lines in report.sections for label, value in lines}


def test_server_information_of_sftp_tells_encryption_host_key_and_free_space(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root, commands=Passwd(PASSWORD)) as server:  # a shell without df: the free space comes from statvfs@openssh.com
        connection = connected_ssh(server.port)
        try:
            lines = report_lines(serverinfo.report(connection, "/", False, "anna@127.0.0.1"))
        finally:
            connection.close()
    assert lines["Connection/Protocol"].startswith("SFTP version ")
    assert lines["Connection/Server software"].startswith("SSH-2.0-")
    assert lines["Encryption/Cipher"] and lines["Encryption/Key exchange"]
    assert lines["Host key/Fingerprint (SHA-256)"].startswith("SHA256:")
    assert lines["What this connection can do/A shell for commands"] == serverinfo.YES
    assert " free" in lines["Space/Free space"] and "statvfs@openssh.com" in lines["Space/Free space"]
    assert "What the server offers/statvfs@openssh.com" in lines


def test_server_information_of_sftp_without_a_shell_says_what_it_can_not_know(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ssh_server(root, shell=False) as server:
        connection = connected_ssh(server.port)
        try:
            assert isinstance(connection, SshConnection)
            lines = report_lines(serverinfo.report(connection, "/", False, "anna@127.0.0.1"))
        finally:
            connection.close()
    assert lines["What this connection can do/A shell for commands"] == serverinfo.NO
    assert lines["What this connection can do/Change owner and group"] == serverinfo.NO


def test_server_information_of_ftp_over_tls_shows_its_certificate_and_unknown_space(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    settings = Connection(name="F", host="127.0.0.1", user=USER, protocol=FTP)
    with ftp_server(root, certificate=self_signed_certificate(tmp_path)) as port:
        first = FtpConnection(Connection(name="F", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        with pytest.raises(UnknownServer) as unknown:
            first.connect()
        trust.remember_certificate(unknown.value.to_remember)
        connection = FtpConnection(Connection(name=settings.name, host=settings.host, port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        try:
            lines = report_lines(serverinfo.report(connection, "/", True, "anna@127.0.0.1"))
        finally:
            connection.close()
    assert lines["Connection/Protocol"] == "FTP over TLS"
    assert lines["Encryption/TLS version"].startswith("TLS")
    assert "Certificate/Fingerprint (SHA-256)" in lines and "Certificate/Valid until" in lines
    assert lines["Space/Free space"] == serverinfo.NO_SPACE_TEXT  # this server offers no AVBL


def test_plain_ftp_is_said_to_be_not_encrypted(tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connection = FtpConnection(Connection(name="F", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        try:
            lines = report_lines(serverinfo.report(connection, "/", True, "x"))
        finally:
            connection.close()
    assert lines["Encryption/Encryption"] == serverinfo.NOT_ENCRYPTED_TEXT


def test_a_certificate_that_can_not_be_read_gives_its_fingerprint_alone() -> None:
    lines = certificate_lines(b"not a certificate")
    assert [label for label, _value in lines] == ["Fingerprint (SHA-256)"]


# --- clean up stored data (FP-04) --------------------------------------------------


@pytest.fixture
def stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_store: MemoryStore) -> Path:
    """Every kind of stored data, in the folders of this test alone; the temporary folder of the edits is the test's too."""
    temporary = tmp_path / "tmp"
    temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))
    saved_connections.save([Connection(name="one", host="h"), Connection(name="two", host="h")])
    memory_store.write("one", "pw1")
    memory_store.write("two", "pw2")
    memory_store.write("not-a-saved-connection", "pw3")
    trust.remember_host_key("[h]:22 ssh-ed25519 AAAA")
    saved_settings.save(saved_settings.Settings())
    saved_settings.save_window({"window": "x"})
    (config_folder() / "bookmarks.json").write_text("{}", "utf-8")
    (config_folder() / "parts.json").write_text("{}", "utf-8")
    (config_folder() / "notes.lock").write_text("", "utf-8")  # a lock of the running program: never cleaned up
    (temporary / f"{cleanup.EDIT_FOLDER_PREFIX}old").mkdir()
    (temporary / f"{cleanup.EDIT_FOLDER_PREFIX}old" / "a.txt").write_text("edited", "utf-8")
    (temporary / f"{cleanup.EDIT_FOLDER_PREFIX}open").mkdir()
    (temporary / "someone-else").mkdir()
    sessionlog.set_rules(LogRules(NORMAL))
    sessionlog.log_for("one").note("hello")
    sessionlog.set_rules(LogRules())
    return temporary


def kinds_by_key(edits_in_use: list[str], queued: bool = False) -> dict[str, cleanup.StoredKind]:
    return {kind.key: kind for kind in cleanup.stored_kinds(edits_in_use, queued)}


def test_every_kind_of_stored_data_is_listed_with_its_size(stored: Path) -> None:
    kinds = kinds_by_key([str(stored / f"{cleanup.EDIT_FOLDER_PREFIX}open")])
    assert list(kinds) == [cleanup.CONNECTIONS, cleanup.TRUST, cleanup.BOOKMARKS, cleanup.SETTINGS, cleanup.NOTES, cleanup.LOGS, cleanup.SCHEMES]
    assert kinds[cleanup.CONNECTIONS].passwords == ("one", "two")
    assert all(kinds[key].size > 0 for key in (cleanup.CONNECTIONS, cleanup.TRUST, cleanup.SETTINGS, cleanup.NOTES, cleanup.LOGS))
    notes = {path.name for path in kinds[cleanup.NOTES].paths}
    assert notes == {"parts.json", f"{cleanup.EDIT_FOLDER_PREFIX}old"}  # the open edit, the lock and others' folders are not ours to clean


def test_clean_up_removes_exactly_what_was_ticked(stored: Path, memory_store: MemoryStore) -> None:
    kinds = kinds_by_key([str(stored / f"{cleanup.EDIT_FOLDER_PREFIX}open")])
    assert cleanup.clean([kinds[cleanup.CONNECTIONS], kinds[cleanup.NOTES]]) == []
    assert not (config_folder() / "connections.json").exists()
    assert memory_store.passwords == {"not-a-saved-connection": "pw3"}  # only the passwords of the saved connections
    assert not (config_folder() / "parts.json").exists() and not (stored / f"{cleanup.EDIT_FOLDER_PREFIX}old").exists()
    assert (stored / f"{cleanup.EDIT_FOLDER_PREFIX}open").exists() and (stored / "someone-else").exists()
    assert (config_folder() / "notes.lock").exists()
    for kept in (trust.known_hosts_path(), config_folder() / "settings.json", config_folder() / "window.json", config_folder() / "bookmarks.json"):
        assert kept.exists(), kept
    assert sessionlog.log_folder().exists()


def test_a_password_store_that_can_not_be_reached_keeps_the_saved_connections(stored: Path, memory_store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    def locked(name: str) -> None:
        raise CredentialError("locked")

    monkeypatch.setattr(memory_store, "remove", locked)
    problems = cleanup.clean([kinds_by_key([])[cleanup.CONNECTIONS]])
    assert problems and "one, two" in problems[0]
    assert (config_folder() / "connections.json").exists()  # so a later Clean Up still finds the names of those passwords


def test_notes_in_use_by_transfers_are_never_removed(stored: Path) -> None:
    notes = kinds_by_key([], queued=True)[cleanup.NOTES]
    assert notes.busy
    assert cleanup.clean([notes]) == []
    assert (config_folder() / "parts.json").exists()


def test_what_can_not_be_removed_is_said_and_the_rest_goes(stored: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kinds = kinds_by_key([])
    os.chmod(config_folder(), 0o500)
    try:
        if os.access(config_folder(), os.W_OK):
            pytest.skip("as root every file can be removed")
        problems = cleanup.clean([kinds[cleanup.SETTINGS], kinds[cleanup.LOGS]])
    finally:
        os.chmod(config_folder(), 0o700)
    assert problems and "settings.json could not be removed" in problems[0]
    assert not sessionlog.log_folder().exists()


# --- the licence (FP-05) -------------------------------------------------------------


def test_the_licence_is_the_agpl_text_shipped_with_the_program() -> None:
    text = appinfo.licence_text()
    assert text is not None
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text and "Version 3, 19 November 2007" in text


# --- the update check (FP-06) ----------------------------------------------------------


def test_versions_compare_as_numbers() -> None:
    assert updatecheck.version_numbers("0.0.10") == (0, 0, 10)
    assert updatecheck.version_numbers("v1.2.3") == (1, 2, 3)
    assert updatecheck.version_numbers("1.2") is None and updatecheck.version_numbers("release") is None
    assert updatecheck.newest_tag(["0.0.9", "0.0.10", "nightly", "0.0.2"]) == "0.0.10"  # 10 after 9, never as text


def answers(pages: dict[str, tuple[bytes, str]], asked: list[str]) -> updatecheck.Fetch:
    def fetch(address: str) -> tuple[bytes, str]:
        asked.append(address)
        return pages[address]

    return fetch


REPOSITORY: str = "https://github.com/someone/chrono-ftp"
FIRST: str = "https://api.github.com/repos/someone/chrono-ftp/tags?per_page=100"


def test_a_newer_version_on_another_page_is_found_with_its_page() -> None:
    second = "https://api.github.com/repositories/1/tags?per_page=100&page=2"
    asked: list[str] = []
    pages = {FIRST: (json.dumps([{"name": "0.0.37"}, {"name": "0.0.9"}]).encode(), f'<{second}>; rel="next"'), second: (json.dumps([{"name": "0.0.40"}]).encode(), "")}
    answer = updatecheck.check(REPOSITORY, "0.0.38", answers(pages, asked))
    assert answer == updatecheck.UpdateAnswer(True, "0.0.40", "https://github.com/someone/chrono-ftp/releases/tag/0.0.40")
    assert asked == [FIRST, second]


def test_the_same_or_an_older_version_is_no_update() -> None:
    pages = {FIRST: (json.dumps([{"name": "0.0.38"}, {"name": "0.0.37"}]).encode(), "")}
    assert not updatecheck.check(REPOSITORY, "0.0.38", answers(pages, [])).newer


@pytest.mark.parametrize("body", [b"not json", b'{"message": "rate limited"}', b"[]", json.dumps([{"name": "nightly"}]).encode()])
def test_an_answer_that_is_no_list_of_versions_fails_calmly(body: bytes) -> None:
    with pytest.raises(updatecheck.UpdateCheckFailed):
        updatecheck.check(REPOSITORY, "0.0.38", answers({FIRST: (body, "")}, []))


def test_a_repository_that_is_not_known_is_said_calmly() -> None:
    with pytest.raises(updatecheck.UpdateCheckFailed, match="does not know where its repository is"):
        updatecheck.check("", "0.0.38", answers({}, []))


def test_the_request_holds_nothing_about_the_person() -> None:
    assert updatecheck.HEADERS == {"User-Agent": "chrono-ftp-update-check", "Accept": "application/vnd.github+json"}
    assert appinfo.repository_url() == "https://github.com/rob-vandenberg/chrono-ftp"


def test_a_page_link_elsewhere_than_the_api_is_never_followed() -> None:
    asked: list[str] = []
    pages = {FIRST: (json.dumps([{"name": "0.0.1"}]).encode(), '<https://example.com/x>; rel="next"')}
    updatecheck.check(REPOSITORY, "0.0.38", answers(pages, asked))
    assert asked == [FIRST]

