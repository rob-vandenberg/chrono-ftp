"""The SSH settings of this computer, read from a folder of the test and never from the person's own ~/.ssh (FJ-08 to FJ-10). The files
are input: a line that can't be read fails only itself (OR-15). Nothing is ever written into them."""

import time
from pathlib import Path

import pytest

from chrono_ftp.core import sshsettings
from chrono_ftp.core.sshsettings import FoundServer
from chrono_ftp.core.sshsettings import FROM_ANOTHER_FILE, IN_A_CASE, NO_FILE, READ, UNREADABLE, config_path, directive_of, known_hosts_path, read_settings, servers_in, ssh_folder, tokens_of, without_comment, worked_out

# --- Version ------------------------------------------------------------
__version__ = "test_core_sshsettings.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Initial: the servers found, what they take over, what they refuse, and that the settings are only read (FJ-08 to FJ-10)

SERVERS_MEASURED: int = 500  # a settings file far bigger than a person's own, read while they wait
COMMENTS_MEASURED: int = 20000
SECONDS_ALLOWED: float = 1.0  # it took 2.1 s before the file was read once; it takes about 0.14 s now
SETTINGS: str = """
# the servers of this computer
Host shop
    HostName shop.example.com
    User anna
    Port 2222
    IdentityFile ~/.ssh/id_shop

Host mail backup
    HostName mail.example.com

Host *
    User fallback
    ServerAliveInterval 30
"""


def servers_of(folder: Path) -> tuple[FoundServer, ...]:
    """The servers of the settings in this folder, with a user name of the test's own, so no test depends on who runs it."""
    return read_settings(folder).servers


def folder_with(tmp_path: Path, text: str, name: str = "config") -> Path:
    folder = tmp_path / ".ssh"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")
    return folder


def test_every_server_of_the_settings_is_found_in_the_order_of_the_file(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, SETTINGS))
    assert [server.connection.name for server in found] == ["shop", "mail", "backup"]


def test_the_name_host_port_user_and_key_file_come_over(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, SETTINGS))
    shop = found[0].connection
    assert (shop.name, shop.host, shop.port, shop.user) == ("shop", "shop.example.com", 2222, "anna")
    assert shop.key_file == str(tmp_path / ".ssh" / "id_shop")  # ~ is the home folder, as it is in a terminal
    assert found[0].can_come_over


def test_a_server_without_settings_of_its_own_reaches_the_name_it_is_called_by(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host plain\n"))
    assert (found[0].connection.host, found[0].connection.port) == ("plain", 22)


def test_a_rule_for_every_server_fills_in_what_a_server_does_not_say_itself(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, SETTINGS))
    names = {server.connection.name: server.connection for server in found}
    assert names["mail"].user == "fallback"  # Host * says so, and mail says nothing itself
    assert names["shop"].user == "anna"  # what the server says itself wins, as OpenSSH reads it


def test_a_name_with_a_star_or_a_question_mark_is_no_server_of_its_own(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host *\n  User anna\nHost web?\n  User bob\nHost real\n"))
    assert [server.connection.name for server in found] == ["real"]


def test_a_server_that_a_rule_rules_out_keeps_its_own_settings(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host shop\nHost * !shop\n  User anna\n"))
    assert found[0].connection.user == ""


def test_a_server_reached_through_another_one_is_not_ticked_and_says_why(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host inside\n  HostName inside.example.com\n  ProxyJump jump.example.com\n"))
    assert not found[0].can_come_over
    assert found[0].refusals[0] == "Your settings reach this server through another server first, which Chrono FTP can't do."


def test_a_server_with_a_signed_certificate_or_a_key_on_a_card_is_not_ticked(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host one\n  CertificateFile ~/.ssh/id-cert.pub\nHost two\n  PKCS11Provider /usr/lib/x.so\n"))
    assert [server.can_come_over for server in found] == [False, False]
    assert "certificate" in found[0].refusals[0] and "card" in found[1].refusals[0]


def test_settings_that_change_in_a_certain_case_take_over_nothing(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host shop\n  HostName shop.example.com\nMatch host shop\n  User anna\n"))
    assert not found[0].can_come_over and found[0].refusals[0] == IN_A_CASE
    assert found[0].connection.user == ""  # what stands in the Match section is never taken over half


def test_settings_that_read_another_file_take_over_nothing(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Include config.d/*\nHost shop\n  HostName shop.example.com\n"))
    assert not found[0].can_come_over and found[0].refusals[0] == FROM_ANOTHER_FILE


def test_a_bad_line_that_is_no_setting_of_ours_fails_only_itself(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host shop\n  Thisisnothing\n  = \n  User anna\n"))
    assert found[0].can_come_over and found[0].connection.user == "anna"  # the bad line fails only itself (OR-15)


def test_no_settings_file_a_file_that_cannot_be_read_and_one_that_was_read_are_said_apart(tmp_path: Path) -> None:
    missing = read_settings(tmp_path / "nothing")
    assert missing.servers == () and missing.state == NO_FILE and missing.path is not None
    folder = folder_with(tmp_path, "Host shop\n")
    assert read_settings(folder).state == READ
    config_path(folder).unlink()
    config_path(folder).mkdir()  # a folder where the file should be: it is there and can't be read
    unreadable = read_settings(folder)
    assert unreadable.servers == () and unreadable.state == UNREADABLE and unreadable.path == config_path(folder)


def test_settings_that_are_no_text_at_all_fail_only_themselves(tmp_path: Path) -> None:
    folder = tmp_path / ".ssh"
    folder.mkdir()
    (folder / "config").write_bytes(b"Host shop\n  HostName \xff\xfe.example.com\n")
    found = servers_of(folder)
    assert [server.connection.name for server in found] == ["shop"]


def test_a_setting_written_with_an_equals_sign_or_in_quotes_is_read(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, 'Host shop\n  Port=2222\n  IdentityFile "my key"\n'))
    assert found[0].connection.port == 2222 and found[0].connection.key_file == "my key"


def test_the_words_of_one_setting_are_read_as_openssh_reads_them() -> None:
    assert tokens_of('one "two three" four') == ["one", "two three", "four"]
    assert tokens_of("a\\ b c") == ["a b", "c"]
    assert tokens_of("   ") == []


def test_a_comment_or_an_empty_line_says_nothing() -> None:
    assert directive_of("# Host shop") is None
    assert directive_of("") is None
    assert directive_of("   ") is None
    assert directive_of("Host") is None  # a keyword without a value
    assert directive_of("  Host   shop  ") == ("host", "shop")


def test_nothing_is_ever_written_into_the_ssh_settings(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, SETTINGS)
    (folder / "known_hosts").write_text("shop.example.com ssh-ed25519 AAAA\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in sorted(folder.iterdir())}
    servers_of(folder)
    known_hosts_path(folder)
    after = {path.name: path.read_bytes() for path in sorted(folder.iterdir())}
    assert after == before


def test_the_settings_of_this_computer_sit_in_the_home_folder() -> None:
    assert ssh_folder() == Path.home() / ".ssh"
    assert config_path() == Path.home() / ".ssh" / "config"
    assert known_hosts_path() == Path.home() / ".ssh" / "known_hosts"


def test_servers_are_read_from_text_without_any_file() -> None:
    found = servers_in("Host shop\n  HostName shop.example.com\n", Path("/home/someone"))
    assert [server.connection.host for server in found] == ["shop.example.com"]


# --- review round 1 (FJ-08, FJ-09, FJ-14) -----------------------------------------------


def test_a_name_with_a_slash_becomes_a_name_with_a_space(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host work/mail\n  HostName mail.example.com\n"))
    assert [server.connection.name for server in found] == ["work mail"]  # never a folder of the list (FJ-01, FJ-08)
    assert found[0].can_come_over


def test_what_a_setting_writes_as_a_token_is_worked_out(tmp_path: Path) -> None:
    folder = folder_with(tmp_path, "Host shop\n  HostName %h.example.com\n  IdentityFile %d/.ssh/id_shop\n")
    found = servers_of(folder)
    assert found[0].can_come_over
    assert found[0].connection.host == "shop.example.com"
    assert found[0].connection.key_file == str(tmp_path / ".ssh" / "id_shop")


def test_the_home_folder_is_worked_out_however_a_setting_writes_it(tmp_path: Path) -> None:
    home = tmp_path
    assert worked_out("~/x", home, "anna", "shop") == str(home / "x")
    assert worked_out("${HOME}/x", home, "anna", "shop") == str(home / "x")
    assert worked_out("$HOME/x", home, "anna", "shop") == str(home / "x")
    assert worked_out("%d/x", home, "anna", "shop") == str(home / "x")
    assert worked_out("/srv/%u/key", home, "anna", "shop") == "/srv/anna/key"


def test_a_setting_with_something_that_cannot_be_worked_out_refuses_its_server(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host shop\n  HostName %x.example.com\n"))
    assert not found[0].can_come_over
    assert "your SSH program fills in itself" in found[0].refusals[0] and "The server it reaches" in found[0].refusals[0]
    assert found[0].connection.host == "shop"  # nothing of it is taken over half
    assert worked_out("~anna/x", tmp_path, "anna", "shop") is None
    assert worked_out("%%d", tmp_path, "anna", "shop") == "%d"  # two percent signs stand for one, as OpenSSH reads them
    assert worked_out("%z", tmp_path, "anna", "shop") is None


def test_a_proxy_that_is_switched_off_refuses_nothing(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host one\n  ProxyJump none\nHost two\n  ProxyCommand None\n"))
    assert [server.can_come_over for server in found] == [True, True]
    assert all(server.refusals == () for server in found)


def test_a_port_that_is_no_port_refuses_its_server(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host one\n  Port 0\nHost two\n  Port 99999\nHost three\n  Port seventy\n"))
    assert [server.can_come_over for server in found] == [False, False, False]
    assert "1 to 65535" in found[0].refusals[0] and "Its port is 0" in found[0].refusals[0]
    assert [server.connection.port for server in found] == [22, 22, 22]  # unticked, so nothing of them comes over


# --- review round 2 (FJ-07, FJ-08) ------------------------------------------------------


def test_a_comment_after_a_setting_is_no_part_of_it(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host shop # my shop\n  Port 22 # the usual one\n  HostName h.com # c\n  User anna # me\n"))
    assert [server.connection.name for server in found] == ["shop"]  # the words of the comment are no servers of their own
    assert found[0].can_come_over
    assert (found[0].connection.port, found[0].connection.host, found[0].connection.user) == (22, "h.com", "anna")


def test_a_hash_inside_quotes_belongs_to_the_value(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, 'Host shop\n  IdentityFile "/home/anna/my#key"\n'))
    assert found[0].connection.key_file == "/home/anna/my#key"
    assert without_comment('IdentityFile "my#key" # c') == 'IdentityFile "my#key" '


def test_no_key_file_means_no_key_file_and_never_one_called_none(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host shop\n  IdentityFile none\n"))
    assert found[0].can_come_over and found[0].connection.key_file == ""


# --- review round 3 (FJ-01, FJ-08, FJ-09) ----------------------------------------------


def test_a_port_that_only_looks_like_a_number_refuses_its_server_and_nothing_else(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host shop\n  HostName shop.example.com\nHost odd\n  Port ²\n"))
    assert [server.connection.name for server in found] == ["shop", "odd"]  # the whole file is still read (OR-15)
    assert found[0].can_come_over and found[0].connection.host == "shop.example.com"
    assert not found[1].can_come_over and "1 to 65535" in found[1].refusals[0]


def test_the_settings_of_one_server_that_cannot_be_read_cost_no_other_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def refusing(alias: str, directives: list[sshsettings.Directive], home: Path, user: str) -> FoundServer:
        if alias == "odd":
            raise ValueError("whatever one server's settings do")
        return real(alias, directives, home, user)

    real = sshsettings._settings_for
    monkeypatch.setattr(sshsettings, "_settings_for", refusing)
    found = servers_of(folder_with(tmp_path, "Host shop\n  HostName shop.example.com\nHost odd\n"))
    assert [server.connection.name for server in found] == ["shop", "odd"]
    assert found[0].can_come_over
    assert not found[1].can_come_over and "could not read the settings" in found[1].refusals[0]


def test_the_server_a_setting_names_is_the_one_the_tokens_stand_for(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host a\n  HostName real.example.com\n  IdentityFile ~/.ssh/%h\n  User %h-admin\n"))
    assert found[0].can_come_over
    assert found[0].connection.key_file == str(tmp_path / ".ssh" / "real.example.com")  # %h is the server it really uses
    assert found[0].connection.user == "real.example.com-admin"


def test_a_setting_longer_than_any_setting_refuses_its_server(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host big\n  HostName " + "a" * 5000 + "\n"))
    assert not found[0].can_come_over and "far longer" in found[0].refusals[0]
    assert found[0].connection.host == "big"  # never offered as fine with the name after Host as its server


def test_a_server_name_that_is_no_server_name_refuses_its_server(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host one\n  HostName -oProxyCommand=x\nHost -weird\nHost ok\n  HostName shop.example.com\n"))
    assert [server.can_come_over for server in found] == [False, False, True]
    assert "no server name" in found[0].refusals[0] and "-oProxyCommand=x" in found[0].refusals[0]
    assert "no server name" in found[1].refusals[0]


def test_a_backslash_keeps_the_hash_that_follows_it(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host shop\n  IdentityFile /home/anna/a\\#b\n"))
    assert found[0].connection.key_file == "/home/anna/a#b"
    assert without_comment("IdentityFile /a\\#b # c") == "IdentityFile /a\\#b "


def test_every_refusal_is_said_in_words_a_person_uses(tmp_path: Path) -> None:
    text = "Host one\n  ProxyJump j.example.com\nHost two\n  CertificateFile /a\nHost three\n  PKCS11Provider /a\nHost four\n  IdentityFile ~/.ssh/%r\nMatch host *\n  User x\n"
    said = " ".join(refusal for server in servers_of(folder_with(tmp_path, text)) for refusal in server.refusals)
    for keyword in ("ProxyJump", "CertificateFile", "PKCS11Provider", "IdentityFile", "Include", "Match"):
        assert keyword not in said, keyword
    assert "another server" in said and "certificate" in said and "smart card" in said and "key file" in said
    assert "by hand" in said  # every refusal says what the person can do


# --- review round 4 (FJ-08, FJ-09) ------------------------------------------------------


def test_a_value_of_more_words_that_is_not_quoted_refuses_its_server(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, 'Host a\n  HostName my server\nHost b\n  User bob x\nHost c\n  IdentityFile "my key"\n'))
    assert [server.can_come_over for server in found] == [False, False, True]
    assert "more than one word" in found[0].refusals[0] and "my server" in found[0].refusals[0]
    assert found[0].connection.host == "a"  # never the first word of it
    assert "more than one word" in found[1].refusals[0] and found[1].connection.user == ""
    assert found[2].connection.key_file == "my key"  # what is quoted is one value, spaces and all


def test_a_network_address_needs_no_brackets_in_the_settings(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host v\n  HostName 2001:db8::1\nHost w\n  HostName ::1\nHost s\n  HostName fe80::1%eth0\n"))
    assert [server.can_come_over for server in found] == [True, True, True]
    assert [server.connection.host for server in found] == ["2001:db8::1", "::1", "fe80::1%eth0"]  # the name of a network card is no mark


def test_a_network_address_between_brackets_in_the_settings_says_to_drop_them(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host b\n  HostName [::1]\n"))
    assert not found[0].can_come_over
    assert found[0].refusals[0] == "The server it reaches stands between square brackets ([::1]), which belong in an address and not here. Write it as ::1."


def test_a_value_with_a_control_character_refuses_its_server(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host n\n  User a\x00b\nHost k\n  IdentityFile \x00x\n"))
    assert [server.can_come_over for server in found] == [False, False]
    assert found[0].connection.user == "" and found[1].connection.key_file == ""
    assert "an invisible character (number 0), which can't be in it" in found[0].refusals[0]
    assert "an invisible character (number 0), which can't be in the name of a file" in found[1].refusals[0]


def test_the_same_refusal_stands_once(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host j\n  ProxyJump a.example.com\n  ProxyCommand nc %h %p\n"))
    assert len(found[0].refusals) == 1  # one line per cause, however often the file says it


def test_the_refusal_of_a_bad_server_name_says_the_cause_and_keeps_what_it_echoes_short(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host d\n  HostName -x\nHost e\n  HostName a.b.\nHost f\n  HostName " + "a" * 300 + "\n"))
    assert "starts with a hyphen" in found[0].refusals[0]
    assert "between every two dots" in found[1].refusals[0] and "a.b.)" in found[1].refusals[0]
    assert "longer than a name can be" in found[2].refusals[0] and len(found[2].refusals[0]) < 250


def test_the_three_refusals_that_no_connection_by_hand_can_mend_say_what_is_true(tmp_path: Path) -> None:
    text = "Host one\n  ProxyJump j.example.com\nHost two\n  CertificateFile /a\nHost three\n  PKCS11Provider /a\n"
    found = servers_of(folder_with(tmp_path, text))
    for server in found:
        assert "by hand" not in server.refusals[0], server.refusals[0]  # a connection by hand has no field for any of these
        assert "terminal" not in server.refusals[0] and "version" not in server.refusals[0]  # no terminal (OR-25), no promise
        assert len(server.refusals[0].split()) <= 20, server.refusals[0]
    assert "through another server first" in found[0].refusals[0] and "certificate file" in found[1].refusals[0] and "smart card" in found[2].refusals[0]


def test_the_port_refusal_is_a_sentence_like_the_others(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host p\n  Port 0\n"))
    assert found[0].refusals[0].endswith(".") and "by hand" in found[0].refusals[0]
    assert "Its port is 0" in found[0].refusals[0] and "written here" not in found[0].refusals[0]


def test_a_short_hand_of_the_ssh_program_is_said_to_be_one_and_named(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host m\n  IdentityFile ~/.ssh/id_%r\nHost o\n  User ${WHO}\nHost h\n  IdentityFile ~bob/.ssh/id\n"))
    assert "The key file holds %r, which your SSH program fills in itself" in found[0].refusals[0]  # the short-hand itself, not the path
    assert "The user name holds ${WHO}" in found[1].refusals[0]
    assert "another person's home folder (~bob)" in found[2].refusals[0]  # a path of someone else's home is its own thing
    assert all("OpenSSH" not in server.refusals[0] for server in found)  # not a word the person uses


def test_the_settings_are_read_once_however_many_servers_they_hold(tmp_path: Path) -> None:
    """Reading the file again for every server made a big file take seconds, and a person waits for it (G-2)."""
    text = "".join(f"Host server{number}\n  HostName server{number}.example.com\n  User anna\n" for number in range(SERVERS_MEASURED))
    text += "\n".join(f"# comment {number}" for number in range(COMMENTS_MEASURED))
    start = time.monotonic()
    found = servers_in(text, tmp_path, "anna")
    taken = time.monotonic() - start
    assert len(found) == SERVERS_MEASURED and all(server.can_come_over for server in found)
    assert taken < SECONDS_ALLOWED, f"{taken:.2f} s"


# --- review round 5 (FJ-08, FJ-09) ------------------------------------------------------


def test_a_setting_above_the_first_host_line_counts_for_every_server(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "User anna\n  IdentityFile ~/.ssh/id\nHost a\n  HostName a.example.com\nHost b\n  User bob\n"))
    assert [server.can_come_over for server in found] == [True, True]
    assert [server.connection.user for server in found] == ["anna", "anna"]  # as OpenSSH reads it: the first value wins
    assert all(server.connection.key_file == str(tmp_path / ".ssh" / "id") for server in found)


def test_a_refusing_setting_above_the_first_host_line_refuses_every_server(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "ProxyJump bastion\nHost a\n  HostName a.example.com\nHost b\n"))
    assert [server.can_come_over for server in found] == [False, False]
    assert all("through another server first" in server.refusals[0] for server in found)


def test_the_port_refusal_says_the_port_can_be_given_by_hand(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host p\n  Port 0\n"))
    assert "Port" in found[0].refusals[0].rpartition("by hand")[2]  # the row that is the very thing refused


def test_the_refusal_names_the_character_that_makes_a_name_no_name(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, 'Host q\n  HostName "my server"\nHost r\n  HostName a,b\n'))
    assert [server.can_come_over for server in found] == [False, False]
    assert "and this holds a space" in found[0].refusals[0]  # the quoted name: the space is named
    assert "and this holds the character ," in found[1].refusals[0]


def test_no_refusal_uses_a_word_a_person_would_not_use(tmp_path: Path) -> None:
    text = "Host one\n  ProxyJump j.example.com\nHost two\n  CertificateFile /a\nHost three\n  PKCS11Provider /a\nHost four\n  IdentityFile ~/id_%r\nHost five\n  IdentityFile /a\x00b\n"
    said = " ".join(refusal for server in servers_of(folder_with(tmp_path, text)) for refusal in server.refusals)
    for word in ("a token", "jump host", "OpenSSH", "a path", "signs your key", "ProxyJump", "CertificateFile", "PKCS11Provider", "terminal"):
        assert word not in said, word
    assert "smart card" in said and "certificate file" in said


def test_the_persons_own_home_folder_is_a_key_file_and_two_percent_signs_are_one(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, "Host t\n  IdentityFile ~\nHost p\n  IdentityFile ~/a%%b\n"))
    assert [server.can_come_over for server in found] == [True, True]
    assert found[0].connection.key_file == str(tmp_path)  # ~ alone is the person's own home folder, not another person's
    assert found[1].connection.key_file == str(tmp_path / "a%b")  # two percent signs stand for one


# --- review round 7 (FJ-08, FJ-09) ------------------------------------------------------


def test_a_name_after_host_that_is_no_server_name_says_that(tmp_path: Path) -> None:
    found = servers_of(folder_with(tmp_path, 'Host "b c"\nHost x\n  HostName -y\n'))
    assert [server.can_come_over for server in found] == [False, False]
    assert found[0].refusals[0] == "Your settings call this server b c, which is no server name: a name holds letters, digits, dots, hyphens and underscores, and this holds a space."
    assert len(found[0].refusals[0].split()) <= 26 and "Host" not in found[0].refusals[0]  # short, and no keyword of the file in it
    assert found[1].refusals[0].startswith("The server it reaches is no server name (-y):")  # there the person did write one


def test_no_refusal_promises_a_version(tmp_path: Path) -> None:
    text = ("Include a\nMatch host x\n  User y\nHost one\n  ProxyJump j\nHost two\n  CertificateFile /a\nHost three\n  PKCS11Provider /a\n"
            "Host four\n  IdentityFile ~/id_%r\n")
    said = " ".join(refusal for server in servers_of(folder_with(tmp_path, text)) for refusal in server.refusals)
    for promise in (" yet", "later version", "a version", "come over"):
        assert promise not in said, promise
