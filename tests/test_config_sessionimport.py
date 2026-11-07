from pathlib import Path

from chrono_ftp.config import sessionimport
from chrono_ftp.core import colours
from chrono_ftp.core.connection import FTP, SFTP

# --- Version ------------------------------------------------------------
__version__ = "test_config_sessionimport.py 0.0.9"

# --- Version history ----------------------------------------------------
# v0.0.9: The optional colour
# v0.0.5: Initial, with made-up data

SAMPLE: str = r"""[Configuration]
Version=6

[Sessions\My%20Shop]
HostName=shop.example.com
UserName=anna
PortNumber=2022
FSProtocol=2
PublicKeyFile=/keys/id%20one
RemoteDirectory=/srv/www
Color=255

[Sessions\Old%20Ftp]
HostName=ftp.example.com
FSProtocol=5

[Sessions\No%20Host]
UserName=anna

[Sessions\Bad%20Port]
HostName=bad.example.com
PortNumber=99999
Color=lots
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "sessions.ini"
    path.write_text(text, encoding="utf-8")
    return path


def test_sessions_become_connections(tmp_path: Path) -> None:
    found = {item.name: item for item in sessionimport.read_sessions(write(tmp_path, SAMPLE))}
    assert set(found) == {"My Shop", "Old Ftp", "Bad Port"}
    shop = found["My Shop"]
    assert (shop.host, shop.user, shop.port, shop.protocol) == ("shop.example.com", "anna", 2022, SFTP)
    assert (shop.key_file, shop.start_folder, shop.colour) == ("/keys/id one", "/srv/www", "#ff0000")


def test_an_ftp_session_gets_the_ftp_port(tmp_path: Path) -> None:
    old = sessionimport.read_sessions(write(tmp_path, SAMPLE))[1]
    assert (old.protocol, old.port) == (FTP, 21)


def test_bad_numbers_give_the_standard_values(tmp_path: Path) -> None:
    bad = [item for item in sessionimport.read_sessions(write(tmp_path, SAMPLE)) if item.name == "Bad Port"][0]
    assert bad.port == 22 and bad.colour == ""


def test_a_file_that_is_no_session_list_gives_nothing(tmp_path: Path) -> None:
    assert sessionimport.read_sessions(write(tmp_path, "just some text")) == []
    assert sessionimport.read_sessions(tmp_path / "gone.ini") == []
