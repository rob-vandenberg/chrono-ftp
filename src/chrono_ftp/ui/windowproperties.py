"""Properties and the actions of block FM in the main window: Properties on both sides and for several files, links, new empty
files, Duplicate and Copy to on the server, my public key on the server, the password on the server, and the file manager."""

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import override

from PySide6.QtWidgets import QFileDialog, QMessageBox

from chrono_ftp.connections.base import RemoteConnection, RemoteEntry, joined, normalized, safe_name, shown_name
from chrono_ftp.connections.passwordchange import PasswordAnswer
from chrono_ftp.core import credentials
from chrono_ftp.core.connection import FTP
from chrono_ftp.core.errors import ConnectionLost, CredentialError, LocalActionFailed, RemoteActionFailed
from chrono_ftp.engine import commands, localfiles, properties, rights
from chrono_ftp.engine.browsing import typed_remote_path
from chrono_ftp.engine.jobs import Worker
from chrono_ftp.engine.keyinstall import PublicKey, install_key, read_public_key, suggested_key_file
from chrono_ftp.ui.actiondialogs import CopyToDialog, LinkDialog, NewFolderDialog, PasswordDialog
from chrono_ftp.ui.plaintext import box_text, fitting
from chrono_ftp.ui.propertiesdialog import Abilities, Change, PropertiesDialog
from chrono_ftp.ui.session import Session, no_progress
from chrono_ftp.ui.windowbase import NOT_A_NAME_TEXT, WINDOW_TITLE, WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowproperties.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: The Properties window has the Size and Checksum tabs, on both sides (FE-02)
# v0.0.37: Initial: block FM, Properties and server actions (D-060 to D-063); review round 1: rights for everything inside through
#          find, never X; a key comment beyond ASCII; a link never lost; a half copy removed; names of directories; a tree listed first;
#          review round 2: Copy To goes on after a refused item and says what was and was not copied, and reads ~ and relative folders;
#          review round 3: every action tells when the connection was lost meanwhile and nothing was changed; a temporary file this
#          computer can't make, or a file manager that can't start, is told calmly; F9 says it reads the names, and a second F9 waits;
#          review round 4: rights without a shell go on after a refusal and say what kept its rights; only touched boxes change, also
#          for everything inside; names that come too slowly fall back to the listing with a note; texts that need widgets are made on
#          the window's thread; this computer's users and groups are read on a thread of its own; review round 5: without a shell and
#          with unknown rights the window asks for a complete rights value; review round 6: as root no current password is asked

ON_FTP_OWNER_TEXT: str = "FTP can't change the owner or group of a file."
NO_SHELL_OWNER_TEXT: str = "Changing the owner or group needs a shell on the server."
NOT_ROOT_TEXT: str = "Only root can give a file to another user."
NOT_MINE_TEXT: str = "Only the owner of a file, or root, can change its group."
NO_RIGHTS_TEXT: str = "This server can't change rights."
INSIDE_NEEDS_SHELL_TEXT: str = "Changing everything inside needs a shell on the server: then it is one command there."
LINK_SELECTED_TEXT: str = "A link is selected: a change would go through it to what it leads to. Select the file or folder itself."
RIGHTS_LEFT_TEXT: str = "These kept their rights, the others were changed:\n{lines}"
RIGHTS_REFUSED_LINE: str = "{name}: {reason}"
RIGHTS_UNKNOWN_TEXT: str = "the server did not tell its rights"
NAMES_FALLBACK_TEXT: str = "The server did not tell its users and groups ({reason}); the names seen in this folder are offered."
LOCAL_REFUSED_TEXT: str = "These could not be changed:\n{lines}"
LOCAL_INSIDE_TEXT: str = "This changes {what} of everything inside {names} on this computer. Go ahead?"
RUN_ON_SERVER_TEXT: str = "This will run on the server:\n\n{lines}\n\nGo ahead?"
COPY_NAME_TEXT: str = "Name of the copy, in the same folder"
NEW_FILE_TITLE: str = "New file"
NEW_FILE_LABEL: str = "Name of the new file"
KEY_TITLE: str = "Install My Public Key"
KEY_FILTER: str = "Public keys (*.pub);;All files (*)"
KEY_DONE_TEXT: str = "Your public key {comment}is on {login} now.\n\n{steps}\n\nThe connection can log in with the matching private key: choose it as the key file of the connection."
KEY_ON_FTP_TEXT: str = "A key logs in over SSH. This is an FTP connection: an SFTP connection to this server can install it."
PASSWORD_ON_FTP_TEXT: str = "The password is changed over SSH with a shell."
PASSWORD_NO_SHELL_TEXT: str = "Changing the password needs a shell and passwd on the server."
PASSWORD_DONE_TEXT: str = "The password of {login} was changed. The new one is kept in your password store."
PASSWORD_DONE_NOT_KEPT_TEXT: str = "The password of {login} was changed. Your password store could not be reached, so please type the new one at the next login."
PASSWORD_REFUSED_TEXT: str = "The server did not change the password; the old one still works. It said:\n\n{said}"
NOT_LIVE_TEXT: str = "The connection is not live. Reconnect first."
NO_FILE_MANAGER_TEXT: str = "There is no xdg-open on this computer, which opens a folder in the file manager."
COPY_REFUSED_LINE: str = "{name}: {reason}"
COPY_NONE_TEXT: str = "nothing"
COPY_SUMMARY_TEXT: str = "Copied to {folder}: {copied}.\n\nNot copied, the others went on:\n{refused}"
LOST_NOTHING_CHANGED_TEXT: str = "The connection was lost meanwhile, so nothing was changed. Reconnect and try again."
TAB_CLOSED_NOTHING_CHANGED_TEXT: str = "Its tab was closed meanwhile, so nothing was changed."
READING_NAMES_TEXT: str = "Reading the users and groups of the server…"
TEMPORARY_TEXT: str = "This computer could not make a temporary file for it ({reason}), so nothing was made on the server."
FILE_MANAGER_FAILED_TEXT: str = "The file manager could not be started: {reason}."
MOST_LISTED: int = 12  # refusals shown in one message box; the rest are counted


def copy_name(name: str, is_folder: bool) -> str:
    """The suggested name of a duplicate: report-copy.txt, a folder photos-copy."""
    stem, dot, suffix = name.rpartition(".")
    if is_folder or not dot or not stem:
        return f"{name}-copy"
    return f"{stem}-copy.{suffix}"


class PropertiesPart(WindowBase):
    """Block FM in the main window: Properties on both sides, links, new files, copies on the server, the key, the password, the file manager."""

    # --- the worker for this computer ----------------------------------------

    def _on_this_computer_in_turn(self, work: Callable[[], object], done: Callable[[object], None]) -> None:
        """Work on this computer that may take long (everything inside a big folder) runs on a thread of its own: the window never freezes."""
        if self.local_worker is None:
            self.local_worker = Worker(self.bridge.deliver)

        def failed(error: Exception) -> None:
            QMessageBox.information(self, WINDOW_TITLE, box_text(str(error)))
            self.local_side.refresh()

        self.local_worker.submit(work, done, failed)

    # --- a server call that tells when the connection was lost meanwhile --------

    def _live(self, session: Session) -> bool:
        """Whether the tab can still reach its server; when not, the person is told calmly that nothing was changed, as an OK that does
        nothing would leave them guessing."""
        if session in self.sessions and session.alive and session.remote is not None:
            return True
        text = f"{session.connection.name}: {LOST_NOTHING_CHANGED_TEXT if session in self.sessions else TAB_CLOSED_NOTHING_CHANGED_TEXT}"
        self.status.showMessage(text)
        QMessageBox.information(self, WINDOW_TITLE, box_text(text))
        return False

    def _ask_live(self, session: Session, work: Callable[[RemoteConnection], object], done: Callable[[object], None], refresh_after_failure: bool = False) -> None:
        if self._live(session):
            self._ask(session, work, done, refresh_after_failure=refresh_after_failure)

    # --- Properties on the server (FM-01 to FM-04) ----------------------------

    def remote_properties(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2] or self.properties_waiting:
            return  # a second F9 while the names are read: the first window comes
        session, folder, chosen = screen
        if not self._live(session):
            return
        capabilities = session.capabilities
        if commands.can_change_group(capabilities) and not any(entry.is_link for entry in chosen):
            remote = session.remote
            if remote is None:
                return
            self.properties_waiting = True
            self.status.showMessage(READING_NAMES_TEXT)

            def from_server(names: tuple[list[str], list[str]]) -> None:
                self.properties_waiting = False
                self.status.clearMessage()  # a slow directory service ends in from_listing instead
                self._remote_properties(session, folder, chosen, names, True)

            def from_listing(error: Exception) -> None:
                self.properties_waiting = False
                self.status.clearMessage()
                if isinstance(error, ConnectionLost):
                    self._failed(session, error)  # the tab goes grey and the person is told
                    return
                self.status.showMessage(NAMES_FALLBACK_TEXT.format(reason=shown_name(str(error))))
                self._remote_properties(session, folder, chosen, properties.names_seen(list(session.entries)), False)

            session.worker.submit(lambda: properties.server_names(remote), from_server, from_listing)
            return
        self._remote_properties(session, folder, chosen, properties.names_seen(list(session.entries)), False)

    def _remote_abilities(self, session: Session, chosen: list[RemoteEntry], names: tuple[list[str], list[str]], known: bool) -> Abilities:
        capabilities = session.capabilities
        on_ftp = session.connection.protocol == FTP
        link = any(entry.is_link for entry in chosen)
        owner = commands.can_change_owner(capabilities) and not link
        group = commands.can_change_group(capabilities) and not link
        owner_why = LINK_SELECTED_TEXT if link else ON_FTP_OWNER_TEXT if on_ftp else NO_SHELL_OWNER_TEXT if not capabilities.shell else NOT_ROOT_TEXT
        group_why = LINK_SELECTED_TEXT if link else ON_FTP_OWNER_TEXT if on_ftp else NO_SHELL_OWNER_TEXT
        can_rights = (commands.can_change_mode(capabilities) or capabilities.rights) and not link
        return Abilities(
            owner=owner,
            owner_why=owner_why,
            group=group,
            group_why=group_why,
            rights=can_rights,
            rights_why=LINK_SELECTED_TEXT if link else NO_RIGHTS_TEXT,
            inside=capabilities.has("chmod") and not link,
            inside_shown=not on_ftp,
            inside_why=LINK_SELECTED_TEXT if link else INSIDE_NEEDS_SHELL_TEXT,
            owners=tuple(names[0]),
            groups=tuple(names[1]),
            names_known=known and bool(names[0]) and bool(names[1]),
            whole_rights=can_rights and not commands.can_change_mode(capabilities) and not properties.summary(chosen).rights_known,
        )

    def _remote_properties(self, session: Session, folder: str, chosen: list[RemoteEntry], names: tuple[list[str], list[str]], known: bool) -> None:
        if not self._live(session):
            return
        abilities = self._remote_abilities(session, chosen, names, known)
        location = f"{session.connection.user}@{session.connection.host}:{folder}" if session.connection.user else f"{session.connection.host}:{folder}"
        dialog = PropertiesDialog(properties.summary(chosen), location, abilities, self, self.remote_calculator(session, folder, chosen))
        if dialog.exec() != PropertiesDialog.DialogCode.Accepted.value:
            return
        self._change_on_server(session, folder, chosen, dialog.change())

    def _change_on_server(self, session: Session, folder: str, chosen: list[RemoteEntry], change: Change) -> None:
        capabilities = session.capabilities
        paths = [joined(folder, entry.name) for entry in chosen]
        lines: list[str] = []
        if change.owner or change.group:
            lines.append(commands.owner_command(paths, change.owner, change.group, change.inside))
        through_shell = commands.can_change_mode(capabilities)
        if (change.rights is not None or change.folder_readers) and through_shell:
            mode = rights.symbolic(change.rights) if change.rights is not None else ""
            line = commands.rights_command(paths, mode, change.inside, change.folder_readers)
            if line:
                lines.append(line)
        if lines:
            text = RUN_ON_SERVER_TEXT.format(lines="\n".join(shown_name(line) for line in lines))  # OR-6: the exact command, first
            if QMessageBox.question(self, WINDOW_TITLE, box_text(text)) != QMessageBox.StandardButton.Yes:
                return
        by_protocol = change.rights is not None and not through_shell

        def work(remote: RemoteConnection) -> list[tuple[str, str]]:
            """Each file in turn; a refusal is noted and the rest goes on, only a lost connection stops it. No widget here: this is the
            tab's own thread."""
            for line in lines:
                commands.run_checked(remote, line)
            refused: list[tuple[str, str]] = []
            if by_protocol and change.rights is not None:
                for entry, path in zip(chosen, paths, strict=True):
                    mode = rights.applied(rights.mode_number(entry.permissions), change.rights)
                    if mode is None:
                        refused.append((entry.name, RIGHTS_UNKNOWN_TEXT))
                        continue
                    try:
                        remote.set_rights(path, format(mode, "04o"))
                    except ConnectionLost:
                        raise
                    except RemoteActionFailed as error:
                        refused.append((entry.name, str(error).strip()))
            return refused

        def done(refused: object) -> None:
            self._reload(session)
            if isinstance(refused, list) and refused:
                lines_shown = [RIGHTS_REFUSED_LINE.format(name=fitting(shown_name(str(name)), self), reason=shown_name(str(reason))) for name, reason in refused[:MOST_LISTED]]
                more = len(refused) - len(lines_shown)
                QMessageBox.information(self, WINDOW_TITLE, box_text(RIGHTS_LEFT_TEXT.format(lines="\n".join(lines_shown)) + (f"\n… and {more} more" if more else "")))

        if not lines and not by_protocol:
            return
        self.status.showMessage("Changing on the server")
        self._ask_live(session, work, done, refresh_after_failure=True)

    # --- Properties on this computer (FM-01) ----------------------------------

    def local_properties(self) -> None:
        """What the files are and the names of this computer's users and groups are read on a thread of its own: a directory service
        can be slow, and the window never freezes. A second F9 meanwhile waits for the first window."""
        if self.local_properties_waiting:
            return
        folder = self.local_side.folder
        names = [entry.name for entry in self.local_side.selected_entries()]
        if not names:
            return
        self.local_properties_waiting = True

        def read() -> tuple[list[RemoteEntry], list[str], list[str], bool, bool]:
            entries = localfiles.details(folder, names)
            return entries, localfiles.all_users(), localfiles.my_groups(), localfiles.may_change_owner(), localfiles.may_change_group(entries)

        def shown(found: object) -> None:
            self.local_properties_waiting = False
            if isinstance(found, tuple):
                self._local_properties(folder, *found)

        def not_read(error: Exception) -> None:
            self.local_properties_waiting = False
            QMessageBox.information(self, WINDOW_TITLE, box_text(str(error)))

        if self.local_worker is None:
            self.local_worker = Worker(self.bridge.deliver)
        self.local_worker.submit(read, shown, not_read)

    def _local_properties(self, folder: str, entries: list[RemoteEntry], users: list[str], groups: list[str], may_owner: bool, may_group: bool) -> None:
        if not entries:
            return
        link = any(entry.is_link for entry in entries)
        abilities = Abilities(
            owner=may_owner and not link,
            owner_why=LINK_SELECTED_TEXT if link else NOT_ROOT_TEXT,
            group=may_group and not link,
            group_why=LINK_SELECTED_TEXT if link else NOT_MINE_TEXT,
            rights=not link,
            rights_why=LINK_SELECTED_TEXT,
            inside=not link,
            inside_why=LINK_SELECTED_TEXT,
            owners=tuple(users),
            groups=tuple(groups),
            names_known=True,
        )
        dialog = PropertiesDialog(properties.summary(entries), folder, abilities, self, self.local_calculator(folder, entries))
        if dialog.exec() != PropertiesDialog.DialogCode.Accepted.value:
            return
        change = dialog.change()
        if change.inside:
            what = " and ".join(part for part in ("the owner" if change.owner else "", "the group" if change.group else "", "the rights" if change.rights is not None or change.folder_readers else "") if part) or "the rights"
            folders = ", ".join(fitting(shown_name(entry.name), self) for entry in entries if entry.is_folder)
            if QMessageBox.question(self, WINDOW_TITLE, box_text(LOCAL_INSIDE_TEXT.format(what=what, names=folders))) != QMessageBox.StandardButton.Yes:
                return
        paths = [os.path.join(folder, entry.name) for entry in entries]

        def work() -> list[str]:
            return localfiles.change_properties(paths, change.owner, change.group, change.rights, change.inside, change.folder_readers)

        def done(refused: object) -> None:
            self.local_side.refresh()
            if isinstance(refused, list) and refused:
                listed = [shown_name(str(line)) for line in refused[:MOST_LISTED]]
                more = len(refused) - len(listed)
                text = LOCAL_REFUSED_TEXT.format(lines="\n".join(listed)) + (f"\n… and {more} more" if more else "")
                QMessageBox.information(self, WINDOW_TITLE, box_text(text))

        self._on_this_computer_in_turn(work, done)

    # --- a new folder with rights (FM-08) -------------------------------------

    def new_remote_folder(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        capabilities = session.capabilities
        dialog = NewFolderDialog(capabilities.rights or commands.can_change_mode(capabilities), NO_RIGHTS_TEXT, self)
        if dialog.exec() != NewFolderDialog.DialogCode.Accepted.value:
            return
        name, chosen_rights = dialog.chosen()
        if not safe_name(name):
            QMessageBox.information(self, "New folder", box_text(NOT_A_NAME_TEXT))
            return
        path = joined(folder, name)

        def work(remote: RemoteConnection) -> None:
            remote.make_folder(path)
            mode = None if chosen_rights is None else rights.applied(None, chosen_rights)
            if mode is not None:
                remote.set_rights(path, format(mode, "04o"))

        self._ask_live(session, work, self._then_refresh(session), refresh_after_failure=True)

    def new_local_folder(self) -> None:
        folder = self.local_side.folder
        dialog = NewFolderDialog(True, "", self)
        if dialog.exec() != NewFolderDialog.DialogCode.Accepted.value:
            return
        name, chosen_rights = dialog.chosen()
        try:
            localfiles.make_folder(folder, name)
            if chosen_rights is not None:
                localfiles.set_folder_rights(os.path.join(folder, name), chosen_rights)
        except LocalActionFailed as error:
            QMessageBox.information(self, WINDOW_TITLE, box_text(str(error)))
        self.local_side.refresh()

    # --- a new empty file, opened in the editor (FM-07) -----------------------

    def new_remote_file(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, _chosen = screen
        name = self._ask_text(NEW_FILE_TITLE, NEW_FILE_LABEL)
        if not name:
            return
        if not safe_name(name):
            QMessageBox.information(self, NEW_FILE_TITLE, box_text(NOT_A_NAME_TEXT))
            return
        if name in {entry.name for entry in session.entries}:
            QMessageBox.information(self, NEW_FILE_TITLE, box_text(commands.TARGET_EXISTS_TEXT))
            return
        path = joined(folder, name)

        def work(remote: RemoteConnection) -> None:
            try:
                empty = tempfile.mkdtemp(prefix="chrono-ftp-new-")
            except OSError as error:
                raise LocalActionFailed(TEMPORARY_TEXT.format(reason=error.strerror or error)) from error
            try:
                Path(empty, "empty").touch()
                remote.upload(os.path.join(empty, "empty"), path, no_progress, exclusive=True)  # never onto a name that is taken
            except OSError as error:  # this computer's temporary folder: a full or closed disk
                raise LocalActionFailed(TEMPORARY_TEXT.format(reason=error.strerror or error)) from error
            finally:
                shutil.rmtree(empty, ignore_errors=True)

        def made(_nothing: object) -> None:
            self._reload(session)
            self._edit_entry(session, folder, name)  # the edit of F4: every save goes back to the server

        self._ask_live(session, work, made, refresh_after_failure=True)

    def new_local_file(self) -> None:
        name = self._ask_text(NEW_FILE_TITLE, NEW_FILE_LABEL)
        if not name:
            return
        try:
            path = localfiles.make_file(self.local_side.folder, name)
        except LocalActionFailed as error:
            QMessageBox.information(self, NEW_FILE_TITLE, box_text(str(error)))
            return
        self.local_side.refresh()
        self._open_local_file(path)

    # --- links (FM-05) --------------------------------------------------------

    def remote_link(self) -> None:
        screen = self._on_screen()
        if screen is None:
            return
        session, folder, chosen = screen
        capabilities = session.capabilities
        if commands.why_no_link(capabilities, session.connection.protocol == FTP):
            return
        editing = len(chosen) == 1 and chosen[0].is_link
        start_target = chosen[0].link_target if editing else chosen[0].name if len(chosen) == 1 else ""
        dialog = LinkDialog(chosen[0].name if editing else "", start_target, editing, capabilities.hardlinks, commands.NO_HARD_LINKS_TEXT, self)
        if dialog.exec() != LinkDialog.DialogCode.Accepted.value:
            return
        name, target, hard = dialog.chosen()
        path = joined(folder, chosen[0].name if editing else name)
        if hard and not target.startswith("/"):
            target = joined(folder, target)  # a hard link names the real file: from the folder shown, not from the login's home

        def work(remote: RemoteConnection) -> None:
            if editing:
                commands.change_link(remote, path, target)
            else:
                commands.make_link(remote, target, path, hard)

        self._ask_live(session, work, self._then_refresh(session), refresh_after_failure=True)

    def local_link(self) -> None:
        folder = self.local_side.folder
        chosen = self.local_side.selected_entries()
        editing = len(chosen) == 1 and chosen[0].is_link
        start_target = chosen[0].link_target if editing else chosen[0].name if len(chosen) == 1 else ""
        dialog = LinkDialog(chosen[0].name if editing else "", start_target, editing, True, "", self)
        if dialog.exec() != LinkDialog.DialogCode.Accepted.value:
            return
        name, target, hard = dialog.chosen()
        try:
            if editing:
                localfiles.change_link(folder, chosen[0].name, target)
            else:
                localfiles.make_link(folder, name, target, hard)
        except LocalActionFailed as error:
            QMessageBox.information(self, WINDOW_TITLE, box_text(str(error)))
        self.local_side.refresh()

    # --- Duplicate and Copy to on the server (FM-06) --------------------------

    def duplicate(self) -> None:
        screen = self._on_screen()
        if screen is None or len(screen[2]) != 1:
            return
        session, folder, chosen = screen
        if commands.why_no_copy(session.capabilities, chosen, session.connection.protocol == FTP):
            return
        name = self._ask_text("Duplicate", COPY_NAME_TEXT, shown_name(copy_name(chosen[0].name, chosen[0].is_folder)))
        if not name:
            return
        if not safe_name(name):
            QMessageBox.information(self, "Duplicate", box_text(NOT_A_NAME_TEXT))
            return
        source, target = joined(folder, chosen[0].name), joined(folder, name)
        self._ask_live(session, lambda remote: commands.copy_on_server(remote, source, target), self._then_refresh(session), refresh_after_failure=True)

    def copy_to(self) -> None:
        screen = self._on_screen()
        if screen is None or not screen[2]:
            return
        session, folder, chosen = screen
        if commands.why_no_copy(session.capabilities, chosen, session.connection.protocol == FTP):
            return
        dialog = CopyToDialog(len(chosen), folder, self)
        if dialog.exec() != CopyToDialog.DialogCode.Accepted.value:
            return
        typed_folder, pattern = dialog.chosen()
        target_folder = typed_remote_path(folder, typed_folder, session.home_folder)  # as the path bar reads it: ~ and relative paths
        try:
            names = commands.copy_names(pattern, [entry.name for entry in chosen])
        except RemoteActionFailed as error:
            QMessageBox.information(self, "Copy to", box_text(str(error)))
            return
        pairs = [(joined(folder, entry.name), joined(target_folder, name)) for entry, name in zip(chosen, names, strict=True)]

        def work(remote: RemoteConnection) -> tuple[list[str], list[str]]:
            """Every item is tried, whatever happened to the one before it; only a lost connection stops the rest."""
            copied: list[str] = []
            refused: list[str] = []
            for source, target in pairs:
                try:
                    commands.copy_on_server(remote, source, target)
                    copied.append(source.rsplit("/", 1)[-1])
                except ConnectionLost:
                    raise
                except RemoteActionFailed as error:
                    refused.append(f"{source.rsplit('/', 1)[-1]}\0{str(error).strip()}")  # made into text on the window's thread
            return copied, refused

        def done(result: object) -> None:
            self._reload(session)
            if not isinstance(result, tuple) or not result[1]:
                return
            copied, refused = result
            names = ", ".join(fitting(shown_name(str(name)), self) for name in copied) or COPY_NONE_TEXT
            lines = [COPY_REFUSED_LINE.format(name=fitting(shown_name(name), self), reason=shown_name(reason)) for name, _, reason in (str(item).partition("\0") for item in refused)]
            text = COPY_SUMMARY_TEXT.format(folder=fitting(shown_name(target_folder), self), copied=names, refused="\n".join(lines))
            QMessageBox.information(self, "Copy to", box_text(text))

        self._ask_live(session, work, done, refresh_after_failure=True)

    # --- my public key and the password on the server (FM-09, FM-10) ----------

    @override
    def install_my_key(self, session: Session | None = None) -> None:
        session = session or self.current()
        if session is None or not session.alive or session.connection.protocol == FTP:
            return
        start = suggested_key_file(session.connection) or str(Path.home() / ".ssh")
        path, _filter = QFileDialog.getOpenFileName(self, KEY_TITLE, start, KEY_FILTER)
        if not path:
            return
        try:
            key = read_public_key(path)
        except LocalActionFailed as error:
            QMessageBox.information(self, KEY_TITLE, box_text(str(error)))
            return
        self._install(session, key)

    def _install(self, session: Session, key: PublicKey) -> None:
        login = f"{session.connection.user}@{session.connection.host}" if session.connection.user else session.connection.host

        def done(steps: object) -> None:
            lines = "\n".join(str(step) for step in steps) if isinstance(steps, list) else ""
            comment = f"({shown_name(key.comment)}) " if key.comment else ""
            QMessageBox.information(self, KEY_TITLE, box_text(KEY_DONE_TEXT.format(comment=comment, login=shown_name(login), steps=lines)))

        self._ask_live(session, lambda remote: install_key(remote, key), done)

    @override
    def change_password(self, session: Session | None = None) -> None:
        session = session or self.current()
        if session is None or not session.alive or session.connection.protocol == FTP or not session.capabilities.has("passwd"):
            return
        login = f"{session.connection.user}@{session.connection.host}" if session.connection.user else session.connection.host
        dialog = PasswordDialog(login, session.capabilities.is_root, self)
        if dialog.exec() != PasswordDialog.DialogCode.Accepted.value:
            return
        old, new = dialog.chosen()

        def done(answer: object) -> None:
            if not isinstance(answer, PasswordAnswer):
                return
            if not answer.changed:
                QMessageBox.information(self, WINDOW_TITLE, box_text(PASSWORD_REFUSED_TEXT.format(said=shown_name(answer.text))))
                return
            text = PASSWORD_DONE_TEXT
            try:
                if session.password is not None or credentials.store.read(session.connection.name) is not None:
                    credentials.store.write(session.connection.name, new)  # a stored password is never asked for again (OR-24)
            except CredentialError:
                text = PASSWORD_DONE_NOT_KEPT_TEXT
            if session.password is not None:
                session.password = new  # the next login of this tab uses the new one
            QMessageBox.information(self, WINDOW_TITLE, box_text(text.format(login=shown_name(login))))

        self._ask_live(session, lambda remote: remote.change_password(old, new), done)

    @override
    def password_reason(self, session: Session) -> str:
        """Why the password can't be changed on this tab's connection; empty when it can."""
        if session.connection.protocol == FTP:
            return PASSWORD_ON_FTP_TEXT
        if not session.alive:
            return NOT_LIVE_TEXT
        return "" if session.capabilities.has("passwd") else PASSWORD_NO_SHELL_TEXT

    @override
    def key_reason(self, session: Session) -> str:
        if session.connection.protocol == FTP:
            return KEY_ON_FTP_TEXT
        return "" if session.alive else NOT_LIVE_TEXT

    # --- the file manager on the left side (FM-11) ----------------------------

    def open_in_file_manager(self) -> None:
        if not localfiles.file_manager_available():
            return
        try:
            subprocess.Popen(localfiles.file_manager_arguments(self.local_side.folder), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as error:  # gone since it was looked for, or it can't be started
            QMessageBox.information(self, WINDOW_TITLE, box_text(FILE_MANAGER_FAILED_TEXT.format(reason=error.strerror or error)))
