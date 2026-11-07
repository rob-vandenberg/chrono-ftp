import os
import shlex
import subprocess
from collections.abc import Generator
from pathlib import Path

import pytest

from chrono_ftp.connections import trust
from chrono_ftp.connections.base import RemoteConnection, UnknownServer
from chrono_ftp.connections.ftp import FtpConnection, _TlsFtp, entry_from
from chrono_ftp.connections.ssh import SshConnection, sendable
from chrono_ftp.core.connection import FTP, Connection
from chrono_ftp.core.errors import ConnectionFailed, RemoteActionFailed
from tests.fakeservers import PASSWORD, USER, SshServer, ftp_server, self_signed_certificate, ssh_server

# --- Version ------------------------------------------------------------
__version__ = "test_connections.py 0.0.10"

# --- Version history ----------------------------------------------------
# v0.0.10: A server without the listing option
# v0.0.5: Initial: both connections against real servers in the test process


def trusted_ssh(server: SshServer, password: str = PASSWORD) -> SshConnection:
    settings = Connection(name="T", host="127.0.0.1", port=server.port, user=USER)
    first = SshConnection(settings, password)
    with pytest.raises(UnknownServer) as unknown:
        first.connect()
    first.close()
    trust.remember_host_key(unknown.value.to_remember)
    connection = SshConnection(settings, password)
    connection.connect()
    return connection


@pytest.fixture(params=["ssh", "sftp-only", "ftp"])
def remote(request: pytest.FixtureRequest, tmp_path: Path) -> Generator[tuple[RemoteConnection, Path]]:
    root = tmp_path / "server"
    root.mkdir()
    if request.param == "ftp":
        with ftp_server(root) as port:
            connection: RemoteConnection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
            connection.connect()
            yield connection, root
            connection.close()
    else:
        with ssh_server(root, shell=request.param == "ssh") as server:
            connection = trusted_ssh(server)
            yield connection, root
            connection.close()


def names(connection: RemoteConnection, path: str) -> set[str]:
    return {entry.name for entry in connection.list_folder(path)}


def test_a_listing_shows_files_and_folders(remote: tuple[RemoteConnection, Path]) -> None:
    connection, root = remote
    (root / "file.txt").write_text("12345", encoding="utf-8")
    (root / "folder").mkdir()
    entries = {entry.name: entry for entry in connection.list_folder("/")}
    assert set(entries) == {"file.txt", "folder"}
    assert entries["file.txt"].size == 5 and not entries["file.txt"].is_folder
    assert entries["folder"].is_folder
    assert entries["file.txt"].modified is not None


def test_rename_make_folder_and_delete(remote: tuple[RemoteConnection, Path]) -> None:
    connection, root = remote
    (root / "a.txt").write_text("x", encoding="utf-8")
    connection.rename("/a.txt", "/b.txt")
    assert names(connection, "/") == {"b.txt"}
    connection.make_folder("/new")
    assert (root / "new").is_dir()
    connection.delete("/b.txt", False)
    assert names(connection, "/") == {"new"}


def test_a_refused_action_is_an_error_not_a_crash(remote: tuple[RemoteConnection, Path]) -> None:
    connection, _root = remote
    with pytest.raises(RemoteActionFailed):
        connection.rename("/gone.txt", "/other.txt")
    with pytest.raises(RemoteActionFailed):
        connection.list_folder("/gone")


def test_a_file_goes_up_and_comes_back_the_same(remote: tuple[RemoteConnection, Path], tmp_path: Path) -> None:
    connection, root = remote
    source = tmp_path / "source.bin"
    source.write_bytes(bytes(range(256)) * 1000)
    done: list[int] = []
    connection.upload(str(source), "/up.bin", done.append)
    assert (root / "up.bin").read_bytes() == source.read_bytes()
    assert sum(done) == source.stat().st_size
    target = tmp_path / "back.bin"
    connection.download("/up.bin", str(target), done.append)
    assert target.read_bytes() == source.read_bytes()


def test_a_limit_slows_the_transfer_down(remote: tuple[RemoteConnection, Path], tmp_path: Path) -> None:
    connection, _root = remote
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * 256 * 1024)
    pauses: list[int] = []
    connection.upload(str(source), "/up.bin", pauses.append, limit=4096)
    assert sum(pauses) == 256 * 1024


def test_a_stopped_transfer_raises_through(remote: tuple[RemoteConnection, Path], tmp_path: Path) -> None:
    connection, _root = remote
    source = tmp_path / "source.bin"
    source.write_bytes(b"x" * 256 * 1024)

    class Stopped(Exception):
        pass

    def stop(_done: int) -> None:
        raise Stopped

    with pytest.raises(Stopped):
        connection.upload(str(source), "/up.bin", stop)


def test_a_folder_tree_is_deleted(remote: tuple[RemoteConnection, Path]) -> None:
    connection, root = remote
    (root / "tree" / "deep").mkdir(parents=True)
    (root / "tree" / "deep" / "f.txt").write_text("x", encoding="utf-8")
    if connection.capabilities.shell:
        pytest.skip("with a shell the server's own path is used: see test_a_shell_runs_real_commands")
    connection.delete("/tree", True)
    assert not (root / "tree").exists()


def test_a_shell_is_detected_with_its_tools(tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        connection = trusted_ssh(server)
        assert connection.capabilities.shell
        assert connection.capabilities.has("find") and connection.capabilities.has("chmod")
        assert connection.capabilities.system == "Linux"
        connection.close()


def test_a_shell_runs_real_commands(tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        connection = trusted_ssh(server)
        result = connection.run("echo hello; exit 3")
        assert (result.status, result.output.strip()) == (3, "hello")
        connection.close()


def test_sftp_without_a_shell_reports_no_shell(tmp_path: Path) -> None:
    with ssh_server(tmp_path, shell=False) as server:
        connection = trusted_ssh(server)
        assert not connection.capabilities.shell
        assert not connection.capabilities.has("find")
        connection.close()


def test_ftp_reports_no_shell(tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        assert not connection.capabilities.shell
        connection.close()


def test_an_unknown_host_key_is_never_accepted_on_its_own(tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        connection = SshConnection(Connection(name="T", host="127.0.0.1", port=server.port, user=USER), PASSWORD)
        with pytest.raises(UnknownServer) as unknown:
            connection.connect()
        connection.close()
        assert unknown.value.fingerprint.startswith("SHA256:")
        assert not trust.known_hosts_path().exists()


def test_a_changed_host_key_is_refused(tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trusted_ssh(server).close()
        port = server.port
    with ssh_server(tmp_path) as other:
        lines = trust.known_hosts_path().read_text(encoding="utf-8").replace(str(port), str(other.port))
        trust.known_hosts_path().write_text(lines, encoding="utf-8")
        connection = SshConnection(Connection(name="T", host="127.0.0.1", port=other.port, user=USER), PASSWORD)
        with pytest.raises(ConnectionFailed, match="changed"):
            connection.connect()
        connection.close()


def test_a_wrong_password_is_told_calmly(tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        trusted_ssh(server).close()
        connection = SshConnection(Connection(name="T", host="127.0.0.1", port=server.port, user=USER), "wrong")
        with pytest.raises(ConnectionFailed):
            connection.connect()
        connection.close()


def test_a_wrong_ftp_password_is_told_calmly(tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), "wrong")
        with pytest.raises(ConnectionFailed):
            connection.connect()


def test_a_closed_port_is_told_calmly() -> None:
    connection = SshConnection(Connection(name="T", host="127.0.0.1", port=1, user=USER), PASSWORD)
    with pytest.raises(ConnectionFailed):
        connection.connect()
    connection.close()


def test_a_bad_mlsd_line_gives_an_entry_without_a_size() -> None:
    entry = entry_from("a", {"type": "file", "size": "many", "modify": "garbage"})
    assert entry is not None and entry.size == 0 and entry.modified is None


def test_the_ftp_folder_entries_are_left_out() -> None:
    assert entry_from(".", {"type": "cdir"}) is None
    assert entry_from("..", {"type": "pdir"}) is None


def ftp_settings(port: int) -> Connection:
    return Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP)


def test_plain_ftp_asks_first_and_sends_no_password_before_that(tmp_path: Path) -> None:
    with ftp_server(tmp_path, trusted=False) as port:
        connection = FtpConnection(ftp_settings(port), PASSWORD)
        with pytest.raises(UnknownServer, match="readable"):
            connection.connect()


def test_tls_certificate_is_asked_then_trusted_and_the_data_is_encrypted(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    (root / "a.txt").write_text("x", encoding="utf-8")
    with ftp_server(root, certificate=self_signed_certificate(tmp_path)) as port:
        first = FtpConnection(ftp_settings(port), PASSWORD)
        with pytest.raises(UnknownServer) as unknown:
            first.connect()
        trust.remember_certificate(unknown.value.to_remember)
        connection = FtpConnection(ftp_settings(port), PASSWORD)
        connection.connect()
        assert connection.encrypted
        assert names(connection, "/") == {"a.txt"}
        other = connection.for_a_transfer()
        assert isinstance(other, FtpConnection) and other.encrypted
        other.close()
        connection.close()


def test_a_changed_certificate_is_refused(tmp_path: Path) -> None:
    with ftp_server(tmp_path, certificate=self_signed_certificate(tmp_path, "one.pem")) as port:
        first = FtpConnection(ftp_settings(port), PASSWORD)
        with pytest.raises(UnknownServer) as unknown:
            first.connect()
    trust.remember_certificate(unknown.value.to_remember.replace(unknown.value.fingerprint, "0" * 64))
    with ftp_server(tmp_path, certificate=self_signed_certificate(tmp_path, "two.pem")) as other_port:
        lines = trust.certificates_path().read_text(encoding="utf-8").replace(f":{port} ", f":{other_port} ")
        trust.certificates_path().write_text(lines, encoding="utf-8")
        connection = FtpConnection(ftp_settings(other_port), PASSWORD)
        with pytest.raises(ConnectionFailed, match="changed"):
            connection.connect()


def test_a_server_that_stopped_offering_tls_is_refused(tmp_path: Path) -> None:
    with ftp_server(tmp_path, trusted=False) as port:
        trust.remember_certificate(f"127.0.0.1:{port} {'0' * 64}")
        connection = FtpConnection(ftp_settings(port), PASSWORD)
        with pytest.raises(ConnectionFailed, match="no longer"):
            connection.connect()


def test_a_transfer_login_never_has_less_protection(tmp_path: Path) -> None:
    with ftp_server(tmp_path) as port:
        connection = FtpConnection(ftp_settings(port), PASSWORD)
        connection.connect()
        connection.encrypted = True  # as if browsing were encrypted and a man in the middle now hides TLS
        with pytest.raises(ConnectionFailed):
            connection.for_a_transfer()
        connection.close()


@pytest.mark.parametrize("name", ["../../escaped.txt", "/etc/passwd", "a/b", "..", ".", "a\\b", ""])
def test_a_hostile_name_from_a_server_is_left_out(name: str) -> None:
    assert entry_from(name, {"type": "file", "size": "1"}) is None


@pytest.mark.parametrize("size", ["\u00b2", "9" * 5000, "-1", "1e5"])
def test_a_strange_size_gives_size_zero(size: str) -> None:
    entry = entry_from("a", {"type": "file", "size": size})
    assert entry is not None and entry.size == 0


def test_a_login_banner_and_a_missing_tool_keep_the_shell(tmp_path: Path) -> None:
    from chrono_ftp.connections import ssh

    with ssh_server(tmp_path) as server:
        connection = trusted_ssh(server)
        result = connection.run("echo Welcome to this server; " + ssh.PROBE.replace("sha256sum", "no-such-tool-here"))
        assert result.status == 0
        connection.close()


def test_a_waiting_call_on_a_closed_connection_ends_at_once(tmp_path: Path) -> None:
    with ssh_server(tmp_path) as server:
        connection = trusted_ssh(server)
        connection.close()
        connection.close()
        with pytest.raises(RemoteActionFailed):
            connection.list_folder("/")
        with pytest.raises(RemoteActionFailed):
            connection.upload(str(tmp_path), "/x", lambda _done: None)


def test_every_data_connection_must_show_the_trusted_certificate(tmp_path: Path) -> None:
    root = tmp_path / "server"
    root.mkdir()
    with ftp_server(root, certificate=self_signed_certificate(tmp_path)) as port:
        first = FtpConnection(ftp_settings(port), PASSWORD)
        with pytest.raises(UnknownServer) as unknown:
            first.connect()
        trust.remember_certificate(unknown.value.to_remember)
        connection = FtpConnection(ftp_settings(port), PASSWORD)
        connection.connect()
        assert names(connection, "/") == set()
        session = connection._ftp
        assert isinstance(session, _TlsFtp) and session.trusted_certificate is not None
        session.trusted_certificate = b"another"
        with pytest.raises(RemoteActionFailed):
            connection.list_folder("/")
        connection.close()


def test_a_command_with_a_name_that_is_not_utf8_reaches_the_real_file(tmp_path: Path) -> None:
    Path(tmp_path, "caf\udce9").write_text("x", encoding="utf-8")
    line = sendable(f"rm -- {shlex.quote(str(tmp_path / 'caf\udce9'))}")
    assert line.isascii()
    subprocess.run(["sh", "-c", line], check=True)
    assert os.listdir(tmp_path) == []


def test_a_plain_command_is_sent_as_it_is() -> None:
    assert sendable("ls -l") == "ls -l"


def test_a_server_without_the_listing_option_still_lists_its_folders(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("text", encoding="utf-8")
    (tmp_path / "inner").mkdir()
    with ftp_server(tmp_path, refuses_listing_options=True) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        found = {entry.name: entry for entry in connection.list_folder("/")}
        connection.close()
    assert found["a.txt"].size == 4 and not found["a.txt"].is_folder
    assert found["inner"].is_folder


def test_a_server_with_the_listing_option_still_gives_the_rights(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("text", encoding="utf-8")
    (tmp_path / "a.txt").chmod(0o640)
    with ftp_server(tmp_path) as port:
        connection = FtpConnection(Connection(name="T", host="127.0.0.1", port=port, user=USER, protocol=FTP), PASSWORD)
        connection.connect()
        found = {entry.name: entry for entry in connection.list_folder("/")}
        connection.close()
    assert found["a.txt"].permissions.endswith("640")  # the test server has no owner to give, so only the rights are checked
