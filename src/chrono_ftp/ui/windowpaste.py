"""Copy and paste files between tabs (FI-03): Ctrl+C on the files chosen, another tab, Ctrl+V. Between two tabs on the same server the
server makes the copy itself where it can; otherwise the copy goes through this computer. A pasted path opens that folder."""

import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from functools import partial
from dataclasses import replace
from typing import override

from PySide6.QtGui import QGuiApplication

from chrono_ftp.config.bookmarks import LOCAL, REMOTE
from chrono_ftp.connections.base import RemoteConnection, joined, shown_name
from chrono_ftp.core.connection import FTP
from chrono_ftp.core.errors import ConnectionLost, RemoteActionFailed
from chrono_ftp.engine import commands
from chrono_ftp.engine.copyplan import BUSY_NEW_TEXT, Copy, Source, free_copy_name, fresh_name, here_kind, here_sources, planned, put_in_place
from chrono_ftp.engine.listing import clipboard_names
from chrono_ftp.transfers.options import Answer, Choice, Conflict
from chrono_ftp.transfers.queue import ENDED, Job, State
from chrono_ftp.transfers.sidework import SideWork, Step, on_this_computer
from chrono_ftp.ui.copydialog import CopyDialog, problem_lines
from chrono_ftp.ui.plaintext import was
from chrono_ftp.ui.overwritedialog import OverwriteDialog
from chrono_ftp.ui.session import CopiedFiles, Relay, Session, same_server
from chrono_ftp.ui.windowbase import WindowBase

# --- Version ------------------------------------------------------------
__version__ = "windowpaste.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Review round 4: every sentence with a number in the copy window reads as a person writes it, for one and for none (OR-23)
# v0.0.45: Review round 3: the words of a copy that replaces something come from engine/copyplan.py, one sentence per step
# v0.0.45: Review round 2: a copy that replaces something, here or on a server, is made under a name of Chrono FTP's own and put in
#          place only once it is whole (G-2); a run on a server that loses its connection ends its window and says what was copied
# v0.0.45: Review round 1: one plan and one window for a paste here and on one server, with Start and Abort on both; a file is
#          never written into a folder of its name; a pasted path must make sense for its side, and a greyed Ctrl+V says why
# v0.0.45: Initial: Ctrl+C and Ctrl+V move files from one tab to another; within one server the server copies them itself where it can,
#          otherwise they go through this computer; files of this computer pasted here are copied in a window with the one Start/Abort
#          button, asking before anything is written over; a pasted path opens that folder (FI-03, D-097, D-099)

COPY_TEXT: str = "Copy These Files"
PASTE_TEXT: str = "Paste the Files Here"
COPY_KEY: str = "Ctrl+C"
PASTE_KEY: str = "Ctrl+V"
NOTHING_SELECTED_TEXT: str = "Nothing is selected."
NOT_LIVE_TEXT: str = "The connection is not live. Reconnect first."
NO_FOLDER_TEXT: str = "No folder is shown on this side."
SOURCE_GONE_TEXT: str = "The tab these files came from is closed, so they can't be pasted."
SOURCE_LOST_TEXT: str = "{name} is not connected, so its files can't be pasted. Reconnect and try again."
COPIED_TEXTS: tuple[str, str] = ("Copied 1 item. Go to another tab and press Ctrl+V to paste it there.", "Copied {count} items. Go to another tab and press Ctrl+V to paste them there.")
STOPPED_TEXT: str = "Stopped: nothing was copied."
NOTHING_AGREED_TEXT: str = "Nothing was copied: everything that is there was left as it is."
APPEARED_TEXT: str = "something with this name appeared there meanwhile, so it was left as it is"
DONE_TEXTS: tuple[str, str] = ("Finished: {name} was copied.", "Finished: all {count} were copied.")
SOME_TEXT: str = "Finished: {done} of {count} {was} copied.{problems}"
NONE_TEXT: str = "Finished: nothing was copied.{problems}"
ABORTED_TEXT: str = "Aborted: {done} of {count} {was_done} copied, and {left} {was_left} not. What was done stays done."
NOTHING_COPIED_YET_TEXT: str = "Aborted: nothing was copied; what was to come was not touched."
PROBLEM_TEXTS: tuple[str, str] = (" This could not be: {problems}.", " These could not be: {problems}.")
NO_PATH_HERE_TEXT: str = "The clipboard holds no files of Chrono FTP and no folder of this computer."
NO_PATH_THERE_TEXT: str = "The clipboard holds no files of Chrono FTP and no path on the server, which starts with a slash."
CANT_REPLACE_TEXT: str = "this server can't put a copy over a name that is taken"
LOST_TEXT: str = "the connection was lost, or the server refused; what was copied before that stays"
NO_FOLDER_HERE_TEXT: str = "{path} is not a folder on this computer."
RELAY_FOLDER_PREFIX: str = "chrono-ftp-copy-"  # the temporary folder of a copy that goes from one server to another through this computer
RELAY_TEXTS: tuple[str, str] = ("{name} goes to {connection} through this computer: it is fetched first and sent after.", "{count} items go to {connection} through this computer: they are fetched first and sent after.")
RELAY_FAILED_TEXT: str = "{name} was not fetched, so nothing of it was sent to {connection}."
RELAY_TAB_GONE_TEXT: str = "{name} was fetched, but its tab is no longer connected, so it was not sent."
LONGEST_PASTED_PATH: int = 4096  # the clipboard is input (OR-15)


def _remove_our_copy(path: str) -> None:
    """Chrono FTP's own unfinished copy, and only that: what was already there is never touched (G-2)."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except OSError:
        pass  # it was never made, or can't go: it carries our own name and the person keeps what they had


class PastePart(WindowBase):
    """Copy and paste files between tabs (FI-03)."""

    def _build_paste(self) -> None:
        self.copied = None
        self.relayed = {}
        for side_name in (LOCAL, REMOTE):
            view = self._side(side_name).file_list
            self._list_action(f"{side_name}_copy_files", COPY_TEXT, COPY_KEY, view, self._copy_handler(side_name))
            self._list_action(f"{side_name}_paste_files", PASTE_TEXT, PASTE_KEY, view, self._paste_handler(side_name))
            view.paste_refused.connect(partial(self._paste_key_refused, side_name))

    def _paste_key_refused(self, side_name: str) -> None:
        """Ctrl+V while pasting is greyed: nothing happens, and the status bar says why, where the person will see it (OR-20)."""
        action = self.list_actions[f"{side_name}_paste_files"]
        if not action.isEnabled() and action.toolTip():
            self.status.showMessage(action.toolTip())

    def _copy_handler(self, side_name: str) -> Callable[[], None]:
        return lambda: self.copy_files(side_name)

    def _paste_handler(self, side_name: str) -> Callable[[], None]:
        return lambda: self.paste_files(side_name)

    # --- Ctrl+C ------------------------------------------------------------------------

    def copy_files(self, side_name: str) -> None:
        """The files chosen are noted, and their full paths go on the clipboard, so another program gets them too (FI-03)."""
        side = self._side(side_name)
        chosen = side.selected_entries()
        if not side.folder or not chosen:
            return
        session = None if side_name == LOCAL else self.current()
        if side_name == REMOTE and (session is None or not session.alive):
            return
        text = clipboard_names([entry.name for entry in chosen], side.folder, True, side_name == LOCAL)
        QGuiApplication.clipboard().setText(text)
        self.copied = CopiedFiles(session, side.folder, tuple(chosen), text)
        self.status.showMessage(COPIED_TEXTS[0] if len(chosen) == 1 else COPIED_TEXTS[1].format(count=len(chosen)))
        self.update_actions()

    # --- Ctrl+V ------------------------------------------------------------------------

    def _ours(self) -> CopiedFiles | None:
        """What Ctrl+C copied, while the clipboard still holds exactly that; otherwise nothing: the person copied something else since."""
        copied = self.copied
        if copied is None or QGuiApplication.clipboard().text() != copied.text:
            return None
        return copied

    def _pasted_path(self, side_name: str) -> str:
        """A path on the clipboard that this side can really go to: a folder of this computer on the left, a path on the server on
        the right. Anything else is no path, so the action is greyed instead of promising what it can't do (OR-20)."""
        text = QGuiApplication.clipboard().text().strip()
        if not text or "\n" in text or len(text) > LONGEST_PASTED_PATH:
            return ""
        if side_name == LOCAL:
            return text if os.path.isdir(text) else ""
        return text if text.startswith("/") else ""

    def why_no_paste(self, side_name: str) -> str:
        """Why nothing can be pasted on this side now; empty when something can (OR-20)."""
        side = self._side(side_name)
        session = self.current()
        if side_name == REMOTE and (session is None or not session.alive or not side.folder):
            return NOT_LIVE_TEXT if session is None or not session.alive else NO_FOLDER_TEXT
        copied = self._ours()
        if copied is None:
            return "" if self._pasted_path(side_name) else (NO_PATH_HERE_TEXT if side_name == LOCAL else NO_PATH_THERE_TEXT)
        if copied.session is None:
            return ""  # files of this computer: an upload on the right, a copy on the left (FI-03)
        if copied.session not in self.sessions:
            return SOURCE_GONE_TEXT
        if not copied.session.alive:
            return SOURCE_LOST_TEXT.format(name=shown_name(copied.session.connection.name))
        return ""

    def paste_files(self, side_name: str) -> None:
        """Ctrl+V on the list with the focus (OR-21): what Ctrl+C copied comes here; a path on the clipboard opens that folder."""
        why = self.why_no_paste(side_name)
        if why:
            self.status.showMessage(why)
            return
        copied = self._ours()
        if copied is None:
            self._go_to_pasted_path(side_name, self._pasted_path(side_name))
            return
        if side_name == LOCAL:
            self._paste_on_this_computer(copied)
            return
        self._paste_on_the_server(copied)

    def _go_to_pasted_path(self, side_name: str, path: str) -> None:
        session = self.current()
        if side_name == LOCAL:
            if not self.local_side.go_to(path):
                self.status.showMessage(NO_FOLDER_HERE_TEXT.format(path=shown_name(path)))
            return
        if session is not None and session.alive and path.startswith("/"):
            self._go_remote(session, path)

    def _paste_on_this_computer(self, copied: CopiedFiles) -> None:
        """On the left side: files of a server are downloaded into the folder shown, as Download does; files of this computer are
        copied here, in a window with the one Start/Abort button (D-097, D-099)."""
        source = copied.session
        if source is None:
            self._copy_here(copied)
            return
        self._offer(source, [joined(copied.folder, name) for name in copied.names], self.local_side.folder, False)

    def _paste_on_the_server(self, copied: CopiedFiles) -> None:
        """On the right side: files of this computer are uploaded, as a drop on that side is; files of a server are copied by the
        server itself where it can, otherwise they go through this computer (D-099)."""
        session = self.current()
        if session is None or not session.alive:
            return
        target = self.remote_side.folder
        source = copied.session
        if source is None:  # from this computer: an upload, as a drop on the right side is
            self._offer(session, [os.path.join(copied.folder, name) for name in copied.names], target, True)
            return
        entries = list(copied.entries)
        if same_server(source, session) and not commands.why_no_copy(session.capabilities, entries, session.connection.protocol == FTP):
            self._copy_there(session, copied, target)  # the server makes every copy itself, one by one, with Start and Abort (D-097)
            return
        self._relay(source, copied, session, target)

    # --- a copy into a folder, here or on one server (FI-03, D-097) ------------------------

    def _copy_window(self, folder: str, plans: list[Copy], run: "Callable[[CopyDialog, list[Copy], threading.Event], None]") -> None:
        """One window for both sides: it says what will happen, nothing happens until Start, and the same button then reads Abort;
        closing it stops the work too (D-097)."""
        dialog = CopyDialog(folder, plans, self)
        stop = threading.Event()
        dialog.start_requested.connect(lambda: run(dialog, plans, stop))
        dialog.work_stopped.connect(stop.set)
        dialog.finished.connect(lambda _result=0: stop.set())  # nothing is left running behind a closed window
        dialog.show()

    def _copy_here(self, copied: CopiedFiles) -> None:
        folder = self.local_side.folder
        plans = planned(here_sources([os.path.join(copied.folder, name) for name in copied.names]), folder, here_kind(folder), os.path.join)
        self._copy_window(folder, plans, lambda dialog, _plans, stop: self._start_copy_here(dialog, folder, plans, stop))

    def _agreed(self, plans: list[Copy], folder: str, there: "Callable[[str], object]", join: "Callable[[str, str], str]", details: "Callable[[str], tuple[int, float | None]]", upload: bool, connection: str) -> list[Copy] | None:
        """What will really be copied, after the question about everything that is there already (G-2). None: the person pressed Stop."""
        batch: Choice | None = None
        agreed: list[Copy] = []
        for plan in plans:
            if plan.why_not:
                continue
            while plan.replaces:
                choice = batch
                if choice is None:
                    answer = self._ask_replace(plan, folder, details, upload, connection)
                    choice = answer.choice
                    if choice in (Choice.YES_TO_ALL, Choice.NO_TO_ALL, Choice.ONLY_IF_NEWER):
                        batch = choice
                    if choice == Choice.RENAME:
                        plan = replace(plan, target=join(folder, answer.new_name), name=answer.new_name, replaces=there(answer.new_name) is not None)
                        continue  # the new name may be taken too: then it asks again
                if choice == Choice.STOP:
                    return None
                if choice in (Choice.YES, Choice.YES_TO_ALL) or (choice == Choice.ONLY_IF_NEWER and self._newer(plan, details)):
                    break  # it is replaced, as the person chose
                plan = replace(plan, why_not="left as it is")  # No: this one stays out of the work
                break
            if not plan.why_not:
                agreed.append(plan)
        return agreed

    def _ask_replace(self, plan: Copy, folder: str, details: "Callable[[str], tuple[int, float | None]]", upload: bool, connection: str) -> Answer:
        here, there = details(plan.source), details(plan.target)
        conflict = Conflict(plan.name, folder, upload, here[0], here[1], there[0], there[1], None, connection=connection, copying=True)
        dialog = OverwriteDialog(conflict, self)
        dialog.exec()
        answer = dialog.answer
        dialog.deleteLater()  # its parent is the window: without this every question would stay behind
        return answer

    def _copy_one_here(self, plan: Copy) -> str:
        """One copy on this computer. Where it replaces something, it is made under a name of Chrono FTP's own and put in place only
        once it is whole, so a copy that breaks off never costs the person what was there (G-2)."""
        if not plan.replaces:
            on_this_computer(SideWork.COPY, Step(path=plan.source, target=plan.target, is_folder=plan.is_folder))
            return ""
        fresh = fresh_name(plan.target)
        if os.path.lexists(fresh):
            raise OSError(BUSY_NEW_TEXT.format(path=os.path.basename(fresh)))
        try:
            on_this_computer(SideWork.COPY, Step(path=plan.source, target=fresh, is_folder=plan.is_folder))
        except (OSError, ValueError, shutil.Error):
            _remove_our_copy(fresh)  # only our own copy went: what was there is untouched
            raise
        try:
            return put_in_place(fresh, plan.target)
        except OSError:
            _remove_our_copy(fresh)
            raise

    def _details_of(self, path: str) -> tuple[int, float | None]:
        try:
            found = os.stat(path)
        except OSError:
            return 0, None
        return found.st_size, found.st_mtime

    def _newer(self, plan: Copy, details: "Callable[[str], tuple[int, float | None]]") -> bool:
        source, target = details(plan.source)[1], details(plan.target)[1]
        return source is not None and target is not None and source > target

    def _ending(self, dialog: CopyDialog, count: int, done: int, problems: list[tuple[str, str]], stopped: bool, one: str = "", notes: list[str] | None = None) -> None:
        """What was done and what was not, naming every item that could not be copied and why, and what was left behind (G-2, OR-23)."""
        left = count - done - len(problems)
        said = (PROBLEM_TEXTS[0] if len(problems) == 1 else PROBLEM_TEXTS[1]).format(problems=problem_lines(problems)) if problems else ""
        if stopped and left > 0:  # an Abort after the last step stopped nothing: it finished (G-2)
            gone = left + len(problems)
            text = (NOTHING_COPIED_YET_TEXT if not done else ABORTED_TEXT.format(done=done, count=count, was_done=was(done), left=gone, was_left=was(gone))) + said
        elif problems and not done:
            text = NONE_TEXT.format(problems=said)
        elif problems:
            text = SOME_TEXT.format(done=done, count=count, was=was(done), problems=said)
        else:
            text = DONE_TEXTS[0].format(name=shown_name(one)) if count == 1 else DONE_TEXTS[1].format(count=count)
        for note in notes or []:
            text += " " + note
        dialog.work_ended(text)
        self.status.showMessage(text)

    def _start_copy_here(self, dialog: CopyDialog, folder: str, plans: list[Copy], stop: threading.Event) -> None:
        agreed = self._agreed(plans, folder, here_kind(folder), os.path.join, self._details_of, False, "")
        if agreed is None:
            dialog.work_ended(STOPPED_TEXT)
            return
        if not agreed:
            dialog.work_ended(NOTHING_AGREED_TEXT)
            return
        count = len(agreed)
        done: list[int] = [0]
        problems: list[tuple[str, str]] = []
        notes: list[str] = []
        dialog.work_started(count)

        def work() -> object:
            for number, plan in enumerate(agreed, 1):
                if stop.is_set():
                    return None  # after the file it was busy with: what was done stays done (D-097)
                self.bridge.deliver(lambda shown=number, what=plan.name: dialog.work_doing(shown, count, what))  # named while it is copied
                try:
                    if not plan.replaces and os.path.lexists(plan.target):
                        raise OSError(APPEARED_TEXT)  # it appeared since the question: never written over in silence (G-2)
                    note = self._copy_one_here(plan)
                    done[0] += 1
                    if note:
                        notes.append(note)
                except (OSError, ValueError, shutil.Error) as error:
                    problems.append((plan.name, str(getattr(error, "strerror", None) or error)))
            return None

        def ended(_result: object) -> None:
            self._ending(dialog, count, done[0], problems, stop.is_set(), agreed[0].name, notes)
            self.local_side.refresh()

        self.run_alone(work, ended, lambda _error: ended(None), stop)

    def _copy_there(self, session: Session, copied: CopiedFiles, folder: str) -> None:
        """A paste within one server: the server makes every copy itself, one by one, so the window can show how far it is and Abort
        can end it after the one it is busy with (D-097)."""
        there = {entry.name: entry for entry in session.entries}

        def kind_of(name: str) -> bool | None:
            entry = there.get(name)
            return None if entry is None else entry.is_folder

        sources = [Source(joined(copied.folder, entry.name), entry.name, entry.is_folder, copied.folder) for entry in copied.entries]
        plans = planned(sources, folder, kind_of, joined, "/")
        self._copy_window(folder, plans, lambda dialog, _plans, stop: self._start_copy_there(dialog, session, folder, plans, stop))

    def _server_details(self, session: Session, folder: str) -> "Callable[[str], tuple[int, float | None]]":
        known = {joined(folder, entry.name): entry for entry in session.entries}
        known.update({joined(session.remote_folder, entry.name): entry for entry in session.entries})

        def details(path: str) -> tuple[int, float | None]:
            entry = known.get(path)
            return (0, None) if entry is None else (entry.size, entry.modified)

        return details

    def _start_copy_there(self, dialog: CopyDialog, session: Session, folder: str, plans: list[Copy], stop: threading.Event) -> None:
        details = self._server_details(session, folder)
        there = {entry.name: entry for entry in session.entries}
        agreed = self._agreed(plans, folder, lambda name: there.get(name), joined, details, True, session.connection.name)
        if agreed is None:
            dialog.work_ended(STOPPED_TEXT)
            return
        if not agreed:
            dialog.work_ended(NOTHING_AGREED_TEXT)
            return
        if session not in self.sessions or not session.alive:
            self._lost_meanwhile(session)
            dialog.work_ended(STOPPED_TEXT)
            return
        count = len(agreed)
        done: list[int] = [0]
        problems: list[tuple[str, str]] = []
        notes: list[str] = []
        dialog.work_started(count)
        shell = commands.can_copy(session.capabilities)

        def work(remote: RemoteConnection) -> object:
            for number, plan in enumerate(agreed, 1):
                if stop.is_set():
                    return None
                self.bridge.deliver(lambda shown=number, what=plan.name: dialog.work_doing(shown, count, what))
                try:
                    if plan.replaces and not shell:
                        raise RemoteActionFailed(CANT_REPLACE_TEXT)  # the protocol can't be told to put one over another (OR-7)
                    note = commands.copy_on_server(remote, plan.source, plan.target, plan.replaces)  # every path quoted (OR-19)
                    done[0] += 1
                    if note:
                        notes.append(note)
                except ConnectionLost:
                    raise
                except RemoteActionFailed as error:
                    problems.append((plan.name, str(error).strip()))
            return None

        def ended(_result: object) -> None:
            self._ending(dialog, count, done[0], problems, stop.is_set(), agreed[0].name, notes)
            self._reload(session)

        def failed() -> None:
            """The connection dropped, or the server refused the whole run: the window says what was copied before that and stops
            waiting, so no Abort button is left that does nothing (D-097)."""
            self._ending(dialog, count, done[0], [*problems, (agreed[min(done[0], count - 1)].name, LOST_TEXT)], True, agreed[0].name, notes)

        self._ask(session, work, ended, refresh_after_failure=True, on_failure=failed)

    # --- a copy from one server to another, through this computer (FI-03) ----------------

    def _relay(self, source: Session, copied: CopiedFiles, target: Session, folder: str) -> None:
        """Each item is fetched to a folder of its own here and sent on once it has arrived; the queue shows both steps and stops them."""
        started = 0
        for name in copied.names:
            try:
                temporary = tempfile.mkdtemp(prefix=RELAY_FOLDER_PREFIX)
            except OSError as error:
                self.status.showMessage(str(error))
                return
            job = self._add_job(source, temporary, joined(copied.folder, name), False, name, by_itself=True)
            if job is None:
                shutil.rmtree(temporary, ignore_errors=True)
                continue
            self.relayed[id(job)] = Relay(target, folder, name, os.path.join(temporary, name), temporary)
            started += 1
        if started:
            text = RELAY_TEXTS[0].format(name=shown_name(copied.names[0]), connection=shown_name(target.connection.name)) if started == 1 else RELAY_TEXTS[1].format(count=started, connection=shown_name(target.connection.name))
            self.status.showMessage(text)
            self._show_queue()

    @override
    def _relay_job_ended(self, job: Job) -> None:
        """The download of a relayed copy is done: the upload starts. The upload is done: the copy here goes, whatever happened."""
        relay = self.relayed.get(id(job))
        if relay is None or job.state not in ENDED:
            return
        del self.relayed[id(job)]
        if relay.uploading:
            shutil.rmtree(relay.temporary, ignore_errors=True)
            return
        if job.state != State.DONE:
            shutil.rmtree(relay.temporary, ignore_errors=True)
            self.status.showMessage(RELAY_FAILED_TEXT.format(name=shown_name(relay.name), connection=shown_name(relay.target.connection.name)))
            return
        if relay.target not in self.sessions or not relay.target.alive:
            shutil.rmtree(relay.temporary, ignore_errors=True)
            self.status.showMessage(RELAY_TAB_GONE_TEXT.format(name=shown_name(relay.name)))
            return
        upload = self._add_job(relay.target, relay.copy, relay.folder, True, relay.name, by_itself=True)
        if upload is None:
            shutil.rmtree(relay.temporary, ignore_errors=True)
            return
        self.relayed[id(upload)] = replace(relay, uploading=True)

    @override
    def _forget_relays(self) -> None:
        """Closing: every copy that was on its way from one server to another goes; the files themselves are still on their server."""
        for relay in self.relayed.values():
            shutil.rmtree(relay.temporary, ignore_errors=True)
        self.relayed.clear()

    # --- greyed or not (OR-20) ------------------------------------------------------------

    @override
    def _update_paste_actions(self) -> None:
        session = self.current()
        for side_name in (LOCAL, REMOTE):
            side = self._side(side_name)
            live = side_name == LOCAL or (session is not None and session.alive and bool(side.folder))
            can_copy = live and bool(side.selected_entries())
            copy = self.list_actions[f"{side_name}_copy_files"]
            copy.setEnabled(can_copy)
            copy.setToolTip("" if can_copy else NOT_LIVE_TEXT if not live else NOTHING_SELECTED_TEXT)
            why = self.why_no_paste(side_name)
            paste = self.list_actions[f"{side_name}_paste_files"]
            paste.setEnabled(not why)
            paste.setToolTip(why)
