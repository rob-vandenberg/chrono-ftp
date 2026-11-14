"""Reading an address a person pasted, and making one from a connection (FJ-11, FJ-12). No screen is needed for either (AGENTS.md 4.3)."""

from chrono_ftp.core.addresses import AFTER_BRACKET_PROBLEM, MORE_PORTS_PROBLEM, HOME_SAID, NO_USER_WITH_AT_SAID, PASSWORD_SAID, SCHEME_PROBLEM, UNDERSTOOD_HEADING, understood, BAD_CHARACTER, BRACKETS_NEEDED, BRACKET_PROBLEM, DASH_PROBLEM, DOT_PROBLEM, EMPTY_PART, FOLDER_PROBLEM, HOST_PROBLEM, LEADING_DASH, NO_HOST, NO_NUMBER, PROBLEMS, TOO_LONG_NAME, control_in, server_name, shortened, unprintable_in, LONG_NAME_PROBLEM, LONGEST_ADDRESS, NAME_PROBLEM, NUMBER_PROBLEM, PORT_PROBLEM, USER_CHARACTER_PROBLEM, USER_SPACE_PROBLEM, address_text, free_name, login_text, named_character, read_address
from chrono_ftp.core.connection import FTP, SFTP, Connection

# --- Version ------------------------------------------------------------
__version__ = "test_core_addresses.py 0.0.47"

# --- Version history ----------------------------------------------------
# v0.0.47: Review round 10: no user name read while the folder holds an @ is said as well (FJ-11)
# v0.0.47: Review round 9: an address is read the one way addresses are built, and what it says is put in plain words (FJ-11)
# v0.0.47: Review round 6: one reading of an address, and a port given twice (FJ-11)
# v0.0.47: Review round 5: an @ in a folder, a network address without brackets, what follows the brackets, and the character that
#          makes a name no name (FJ-11)
# v0.0.47: Review round 4: the cause a name is no name, told apart for both sides; a bare network address; a space in a name and in a
#          path; and what is echoed back kept short (FJ-08, FJ-11)
# v0.0.47: Review round 3: a sentence per cause, the two sentences about the square brackets, and the login of a network address (FJ-11)
# v0.0.47: Review round 2: every refusal says its own thing, an international name and an underscore are ordinary names, a scope after
#          a server number is checked, only ASCII digits are a port, a folder with a control character is refused, and a password with
#          a slash in it is read as the password (FJ-11)
# v0.0.47: Review round 1: only a plain server name or a server number in brackets is a server, a user name with a space or a control
#          character is refused, and a starting folder that is no path from the root is left out of an address (FJ-11, FJ-12)
# v0.0.47: Initial: addresses read and made, and every address that can't be read (FJ-11, FJ-12)


def test_an_address_with_everything_in_it_is_read_whole() -> None:
    read = read_address("sftp://anna@server.example.com:2222/var/log")
    assert read.problem == ""
    assert read.address is not None
    assert (read.address.connection.protocol, read.address.connection.host, read.address.connection.port) == (SFTP, "server.example.com", 2222)
    assert (read.address.connection.user, read.address.folder) == ("anna", "/var/log")


def test_an_address_without_a_port_takes_the_usual_one_of_its_kind() -> None:
    sftp, ftp = read_address("sftp://server.example.com"), read_address("ftp://server.example.com")
    assert sftp.address is not None and (sftp.address.connection.port, sftp.address.connection.protocol) == (22, SFTP)
    assert ftp.address is not None and (ftp.address.connection.port, ftp.address.connection.protocol) == (21, FTP)


def test_an_address_without_a_folder_opens_where_the_login_starts() -> None:
    read = read_address("sftp://server.example.com")
    assert read.address is not None and read.address.folder == ""


def test_a_password_in_an_address_is_read_apart_from_the_user_name() -> None:
    read = read_address("ftp://anna:very-secret@server.example.com/pub")
    assert read.address is not None
    assert (read.address.connection.user, read.address.password) == ("anna", "very-secret")


def test_a_user_name_with_an_at_sign_in_it_is_read_as_the_user() -> None:
    read = read_address("sftp://anna@example.org@server.example.com/")
    assert read.address is not None
    assert (read.address.connection.user, read.address.connection.host) == ("anna@example.org", "server.example.com")


def test_what_is_written_in_percent_signs_comes_back_as_it_was() -> None:
    read = read_address("sftp://anna.b@server.example.com/var/a%20folder")
    assert read.address is not None
    assert (read.address.connection.user, read.address.folder) == ("anna.b", "/var/a folder")


def test_a_server_number_in_brackets_is_the_server_and_its_port_follows() -> None:
    read = read_address("sftp://[2001:db8::1]:2222/srv")
    assert read.address is not None
    assert (read.address.connection.host, read.address.connection.port, read.address.folder) == ("2001:db8::1", 2222, "/srv")


def test_an_address_that_is_no_address_says_so_and_gives_nothing() -> None:
    for typed in ("", "   ", "server.example.com/var", "https://server.example.com", "sftp://", "sftp:///var/log", "sftp://server:70000/x", "sftp://server:two/x", "sftp://[2001:db8::1/srv"):
        read = read_address(typed)
        assert read.address is None, typed
        assert read.problem and read.problem[-1] in ".:" or read.problem.endswith("/var/log"), typed


def test_an_address_longer_than_any_address_is_not_read() -> None:
    read = read_address("sftp://" + "a" * (LONGEST_ADDRESS + 1))
    assert read.address is None and "longer" in read.problem


def test_the_name_of_a_pasted_address_is_the_server_and_never_one_already_taken() -> None:
    read = read_address("sftp://server.example.com/")
    assert read.address is not None and read.address.connection.name == "server.example.com"
    assert free_name("server.example.com", {"server.example.com"}) == "server.example.com (2)"
    assert free_name("server.example.com", {"server.example.com", "server.example.com (2)"}) == "server.example.com (3)"
    assert free_name("server.example.com", set()) == "server.example.com"


def test_only_a_plain_server_name_or_a_server_number_is_a_server() -> None:
    for typed, what in (("sftp://h?x=1", "the character ?"), ("sftp://h;id", "the character ;"), ("sftp://h me/x", "a space"), ("sftp://h%0a/x", "a line break")):
        read = read_address(typed)
        assert read.address is None, typed
        assert read.problem == NAME_PROBLEM.format(what=what), typed  # the character that makes it no name is named
    assert read_address("sftp://-oProxyCommand=touch%20/tmp/x/a").problem == DASH_PROBLEM  # an option is no server (FJ-11)
    assert read_address("sftp:///var/log").problem == HOST_PROBLEM  # no server at all is its own thing
    assert read_address("sftp://[notanumber]/x").problem == NUMBER_PROBLEM
    assert read_address("sftp://[::1%25-oProxyCommand=x]/").problem == NUMBER_PROBLEM  # the scope after % is a name, nothing else
    assert read_address("sftp://" + "a" * 300 + "/x").problem == LONG_NAME_PROBLEM
    assert read_address("sftp://" + "a" * 70 + ".b/x").problem == LONG_NAME_PROBLEM  # each part has its own length
    assert read_address("sftp://h:٣/x").problem == PORT_PROBLEM  # a digit of another language is no port
    assert read_address("sftp://h/a%00b").problem == FOLDER_PROBLEM.format(what="an invisible character (number 0)")


def test_a_server_name_in_another_language_and_one_with_an_underscore_are_ordinary_names() -> None:
    read = read_address("sftp://münchen.de/x")
    assert read.address is not None and read.address.connection.host == "xn--mnchen-3ya.de"  # the form the network uses
    read = read_address("sftp://my_server.example.com/x")
    assert read.address is not None and read.address.connection.host == "my_server.example.com"
    read = read_address("sftp://[::1%25eth0]:2222/x")
    assert read.address is not None and read.address.connection.host == "::1%eth0"


def test_a_password_with_a_slash_in_it_is_written_with_a_percent_sign() -> None:
    read = read_address("sftp://anna:pa%2Fss@server.example.com/var")
    assert read.address is not None
    assert (read.address.connection.user, read.address.password, read.address.connection.host) == ("anna", "pa/ss", "server.example.com")
    assert read.address.folder == "/var"
    read = read_address("sftp://h/var/mail@old")  # an @ in a folder is a folder, never a user name (FJ-11)
    assert read.address is not None and (read.address.connection.host, read.address.folder) == ("h", "/var/mail@old")


def test_a_plain_server_name_a_number_and_a_server_number_in_brackets_are_servers() -> None:
    for typed, host in (("sftp://shop.example.com/x", "shop.example.com"), ("sftp://a-b.c/x", "a-b.c"), ("sftp://127.0.0.1/x", "127.0.0.1"), ("sftp://[2001:db8::1]/x", "2001:db8::1")):
        read = read_address(typed)
        assert read.address is not None, typed
        assert read.address.connection.host == host


def test_a_user_name_with_a_space_or_a_control_character_is_refused() -> None:
    for typed in ("sftp://us er@h/x", "sftp://anna%20b@h/x"):
        read = read_address(typed)
        assert read.address is None and read.problem == USER_SPACE_PROBLEM, typed
    for typed, what in (("sftp://anna%09b@h/x", "a tab"), ("sftp://anna%0ab@h/x", "a line break"), ("sftp://anna%00b@h/x", "an invisible character (number 0)")):
        read = read_address(typed)
        assert read.address is None, typed
        assert read.problem == USER_CHARACTER_PROBLEM.format(what=what), typed


def test_a_starting_folder_that_is_no_path_from_the_root_is_left_out_of_the_address() -> None:
    connection = Connection(name="Shop", host="shop.example.com")
    assert address_text(connection, "public_html") == "sftp://shop.example.com"  # it means the home folder of the login, which is the truth
    assert address_text(connection, "~/x") == "sftp://shop.example.com"
    assert address_text(connection, "/public_html") == "sftp://shop.example.com/public_html"


def test_the_address_of_a_connection_holds_its_user_and_leaves_out_the_usual_port() -> None:
    connection = Connection(name="Shop", host="shop.example.com", user="anna")
    assert address_text(connection) == "sftp://anna@shop.example.com"
    assert address_text(connection, "/var/www") == "sftp://anna@shop.example.com/var/www"
    assert address_text(Connection(name="S", host="shop.example.com", port=2222)) == "sftp://shop.example.com:2222"
    assert address_text(Connection(name="S", host="shop.example.com", protocol=FTP, port=21)) == "ftp://shop.example.com"
    assert address_text(Connection(name="S", host="shop.example.com", protocol=FTP, port=2121)) == "ftp://shop.example.com:2121"


def test_the_address_of_a_connection_never_holds_a_password() -> None:
    address = address_text(Connection(name="Shop", host="shop.example.com", user="anna"), "/var/www")
    assert ":" not in address.split("//", 1)[1]


def test_an_address_made_and_read_again_says_the_same_thing() -> None:
    connection = Connection(name="Shop", host="shop.example.com", user="anna.b", port=2222)
    read = read_address(address_text(connection, "/var/a folder"))
    assert read.address is not None
    assert (read.address.connection.host, read.address.connection.port, read.address.connection.user) == ("shop.example.com", 2222, "anna.b")
    assert read.address.folder == "/var/a folder"


def test_a_server_number_in_brackets_comes_back_in_brackets() -> None:
    assert address_text(Connection(name="S", host="2001:db8::1", port=2222)) == "sftp://[2001:db8::1]:2222"


# --- review round 3 (FJ-11) -------------------------------------------------------------


def test_every_cause_of_a_refused_server_name_says_its_own_thing() -> None:
    assert read_address("sftp://-h/x").problem == DASH_PROBLEM
    assert read_address("sftp://a..b/x").problem == DOT_PROBLEM
    assert read_address("sftp://a.b./x").problem == DOT_PROBLEM
    assert read_address("sftp://h me/x").problem == NAME_PROBLEM.format(what="a space")
    assert len({DASH_PROBLEM, DOT_PROBLEM, NAME_PROBLEM, LONG_NAME_PROBLEM, NUMBER_PROBLEM, BRACKET_PROBLEM}) == 6


def test_the_sentences_about_the_brackets_say_what_they_are_about() -> None:
    assert "square brackets" in BRACKET_PROBLEM and "square brackets" in NUMBER_PROBLEM
    assert "2001:db8::1" in NUMBER_PROBLEM  # what a person can compare their own address with


def test_a_network_address_goes_between_brackets_in_a_login() -> None:
    assert login_text(Connection(name="S", host="2001:db8::1", user="anna")) == "anna@[2001:db8::1]"
    assert login_text(Connection(name="S", host="2001:db8::1")) == "[2001:db8::1]"
    assert login_text(Connection(name="S", host="shop.example.com", user="anna")) == "anna@shop.example.com"
    assert login_text(Connection(name="S", host="shop.example.com")) == "shop.example.com"


# --- review round 4 (FJ-08, FJ-11) ------------------------------------------------------


def test_a_network_address_in_brackets_is_a_server_in_an_address_and_a_bare_one_is_not() -> None:
    assert server_name("2001:db8::1", False) == ("", BAD_CHARACTER)  # in an address a colon belongs to the port, so this is no name
    assert server_name("2001:db8::1", False, bare_number=True) == ("2001:db8::1", "")  # as this computer's SSH settings write one
    assert server_name("2001:db8::1", True) == ("2001:db8::1", "")
    assert server_name("::1%-oProxyCommand=x", False, bare_number=True) == ("", NO_NUMBER)


def test_the_cause_a_name_is_no_name_is_told_apart_for_both_sides() -> None:
    assert server_name("", False)[1] == NO_HOST
    assert server_name("-h", False)[1] == LEADING_DASH
    assert server_name("a..b", False)[1] == EMPTY_PART
    assert server_name("h me", False)[1] == BAD_CHARACTER
    assert server_name("a" * 300, False)[1] == TOO_LONG_NAME
    assert set(PROBLEMS) == {NO_HOST, LEADING_DASH, EMPTY_PART, BAD_CHARACTER, TOO_LONG_NAME, NO_NUMBER}


def test_a_space_is_no_part_of_a_name_and_may_be_part_of_a_path() -> None:
    assert control_in("a b") and control_in("a\x00b") and not control_in("ab")
    assert not unprintable_in("a b") and unprintable_in("a\x00b")  # a key file or folder may hold a space


def test_what_is_echoed_back_in_a_sentence_is_kept_short() -> None:
    assert shortened("a" * 300).endswith("…") and len(shortened("a" * 300)) == 60
    assert shortened("short") == "short"


# --- review round 5 (FJ-11) -------------------------------------------------------------


def test_an_at_sign_in_a_folder_is_a_folder_and_no_user_name() -> None:
    read = read_address("sftp://host/reports/bob@mail.txt")
    assert read.address is not None
    assert (read.address.connection.host, read.address.connection.user, read.address.folder) == ("host", "", "/reports/bob@mail.txt")
    with_both = read_address("sftp://anna@host/reports/bob@mail.txt")  # a real user name and an @ in the folder together
    assert with_both.address is not None
    assert (with_both.address.connection.user, with_both.address.folder) == ("anna", "/reports/bob@mail.txt")


def test_a_network_address_without_brackets_says_to_put_it_between_brackets() -> None:
    for typed in ("sftp://::1/", "sftp://2001:db8::1/", "sftp://2001:db8::1:2222/x"):
        read = read_address(typed)
        assert read.address is None, typed
        assert read.problem == BRACKETS_NEEDED, typed
    assert "square brackets" in BRACKETS_NEEDED and "sftp://[2001:db8::1]" in BRACKETS_NEEDED


def test_something_that_is_no_port_after_the_brackets_says_so() -> None:
    assert read_address("sftp://[::1]x/").problem == AFTER_BRACKET_PROBLEM
    assert read_address("sftp://[::1]:nothing/").problem == PORT_PROBLEM
    assert read_address("sftp://[::1]/x").address is not None  # nothing after them is right


def test_the_character_that_makes_a_name_or_a_user_name_no_name_is_said() -> None:
    assert named_character("a b") == "a space"
    assert named_character("a\tb") == "a tab"
    assert named_character("a\nb") == "a line break"
    assert named_character("a\x00b") == "an invisible character (number 0)"
    assert named_character("a,b") == "the character ,"


# --- review round 6 (FJ-11) -------------------------------------------------------------


def test_the_user_name_and_the_password_are_looked_for_before_the_first_slash_only() -> None:
    read = read_address("sftp://anna@host/reports/bob@mail.txt")  # the user name ends at the last @ before the first slash
    assert read.address is not None
    assert (read.address.connection.user, read.address.connection.host, read.address.folder) == ("anna", "host", "/reports/bob@mail.txt")
    assert read_address("sftp://user:pa/ss@server/x").problem == PORT_PROBLEM  # "pa" is where the port stands, so it is read as one
    whole = read_address("sftp://u:pa%2Fss@host/x")  # a password with a slash is written %2F, as a password in an address always is
    assert whole.address is not None and whole.address.password == "pa/ss"


def test_a_port_given_more_than_once_says_so() -> None:
    assert read_address("sftp://h:22:33/").problem == MORE_PORTS_PROBLEM
    assert read_address("sftp://2001:db8::1/").problem == BRACKETS_NEEDED  # a network address still says to use brackets
    assert "one port" in MORE_PORTS_PROBLEM and "square brackets" not in MORE_PORTS_PROBLEM


def test_an_address_is_read_the_one_way_addresses_are_built() -> None:
    """The user name and its password stand before the first @ before the first slash, and the port after the colon of the server part.
    So an ordinary address with an @ in its folder opens, port or no port, and nothing is refused for being able to mean two things."""
    for typed, host, port, folder in (
        ("sftp://host:22/reports/bob@mail.txt", "host", 22, "/reports/bob@mail.txt"),
        ("sftp://host:2222/a@b", "host", 2222, "/a@b"),
        ("sftp://host/reports/bob@mail.txt", "host", 22, "/reports/bob@mail.txt"),
        ("sftp://user:12/ss@server/x", "user", 12, "/ss@server/x"),  # what it literally says; the window shows this (`understood`)
    ):
        read = read_address(typed)
        assert read.address is not None, f"{typed}: {read.problem}"
        assert (read.address.connection.host, read.address.connection.port, read.address.folder) == (host, port, folder), typed
    for typed in ("sftp://server:99999/a@b", "sftp://[::1]:abc/a@b", "sftp://h:nothing/x", "sftp://server:abc/var/a@b", "ftp://u:pw/x@y.com:21/"):
        assert read_address(typed).problem == PORT_PROBLEM, typed  # whatever was meant, after the colon stands no port
    whole = read_address("sftp://u:pa%2Fss@host/x")
    assert whole.address is not None and whole.address.password == "pa/ss"  # a password with a slash in it, written as addresses are


def test_what_an_address_says_is_put_in_plain_words() -> None:
    """What the window shows under the field while a person types: the server, the port, the user name and the folder, each named."""
    assert understood("  ") == ""  # nothing typed, nothing to say
    assert understood("sftp://anna@shop.example.com:2222/var/log").splitlines() == [
        UNDERSTOOD_HEADING, "Server: shop.example.com", "Port: 2222", "User name: anna", "Folder: /var/log"]
    read_back = understood("sftp://user:12/ss@server/x").splitlines()  # meant as a password, read as the server it says
    assert read_back[1:3] == ["Server: user", "Port: 12"] and "Folder: /ss@server/x" in read_back[4]
    assert read_back[-1] == NO_USER_WITH_AT_SAID  # no user name read, and an @ in the folder: the person reads that too
    assert NO_USER_WITH_AT_SAID not in understood("sftp://anna@host/reports/bob@mail.txt")  # there a user name was read
    assert NO_USER_WITH_AT_SAID not in understood("sftp://host:22/var/log")  # and here no @ stands in the folder
    assert understood("sftp://shop.example.com").splitlines()[1:] == ["Server: shop.example.com", "Port: 22", "User name: none", HOME_SAID]
    with_password = understood("ftp://anna:very-secret@shop.example.com/pub")
    assert PASSWORD_SAID in with_password and "very-secret" not in with_password  # that one came, never what it is (OR-19)
    assert understood("shop.example.com") == SCHEME_PROBLEM  # the refusal stands in the same place
    assert understood("sftp://h:nothing/x") == PORT_PROBLEM
