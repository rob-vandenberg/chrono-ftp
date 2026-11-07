import os
import queue
import re
import select
import shlex
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from chrono_ftp.connections import trust
from chrono_ftp.connections.base import RemoteConnection, RemoteEntry, joined
from chrono_ftp.core.connection import SFTP, Connection
from chrono_ftp.core.errors import ChronoFtpError, RemoteActionFailed

# --- Version ------------------------------------------------------------
__version__ = "queue.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: rsync where it can work, else SFTP or FTP; progress, throttling, stop (D-014)

PROGRESS_PATTERN: re.Pattern[bytes] = re.compile(rb"(\d+)%")
LOGIN_FAILED_STATUSES: frozenset[int] = frozenset({12, 255})  # rsync could not start its ssh: the other way still works
READ_SIZE: int = 4096
PART_SUFFIX: str = ".chrono-ftp-part"  # a transfer lands here first: a stop or failure never touches the file already there
STALL_SECONDS: int = 60
POLL_SECONDS: float = 0.2
FOLDER_IN_THE_WAY_TEXT: str = "There is a folder with this name on the server, so this file was not sent."


class State(Enum):
    WAITING = "Waiting"
    RUNNING = "Running"
    DONE = "Done"
    FAILED = "Failed"
    STOPPED = "Stopped"


class Stopped(Exception):
    pass


@dataclass
class Job:
    remote: RemoteConnection
    connection: Connection
    local: str  # the local file or folder; for a download the folder it goes into
    remote_path: str  # the remote file or folder; for an upload the folder it goes into
    upload: bool
    name: str
    has_password: bool = False
    state: State = State.WAITING
    percent: int = 0
    message: str = ""
    way: str = ""
    stop_asked: threading.Event = field(default_factory=threading.Event)


def rsync_possible(job: Job) -> bool:
    return job.connection.protocol == SFTP and job.remote.capabilities.has("rsync") and shutil.which("rsync") is not None and not job.has_password


def rsync_arguments(job: Job, limit: int) -> list[str]:
    settings = job.connection
    ssh = ["ssh", "-p", str(settings.port), "-o", "BatchMode=yes", "-o", f"UserKnownHostsFile={trust.known_hosts_path()} {Path.home() / '.ssh' / 'known_hosts'}"]
    if settings.key_file:
        ssh += ["-i", settings.key_file]
    login = f"{settings.user}@{settings.host}" if settings.user else settings.host
    arguments = ["rsync", "-a", "--protect-args", "--no-inc-recursive", "--no-perms", "--no-owner", "--no-group", "--info=progress2", f"--timeout={STALL_SECONDS}", "-e", shlex.join(ssh)]
    if limit:
        arguments.append(f"--bwlimit={limit}")
    if job.upload:
        return [*arguments, "--", job.local, f"{login}:{job.remote_path.rstrip('/')}/"]
    return [*arguments, "--", f"{login}:{job.remote_path}", f"{job.local.rstrip('/')}/"]


def _local_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


class TransferQueue:
    """The jobs run one after the other on one thread. changed is called from that thread: the window hands it on to its own."""

    def __init__(self, changed: Callable[[Job], None], program: str = "rsync") -> None:
        self.changed: Callable[[Job], None] = changed
        self.limit: int = 0  # KB per second, 0 for no limit; a running job keeps the limit it started with
        self.program: str = program
        self.jobs: list[Job] = []
        self._waiting: queue.Queue[Job | None] = queue.Queue()
        self._thread: threading.Thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def add(self, job: Job) -> None:
        self.jobs.append(job)
        self.changed(job)
        self._waiting.put(job)

    def stop(self, job: Job) -> None:
        job.stop_asked.set()
        if job.state == State.WAITING:
            job.state = State.STOPPED
            self.changed(job)

    def close(self) -> None:
        for job in self.jobs:
            job.stop_asked.set()
        self._waiting.put(None)

    def _run(self) -> None:
        while (job := self._waiting.get()) is not None:
            if job.stop_asked.is_set():
                continue
            job.state = State.RUNNING
            self.changed(job)
            try:
                self._transfer(job)
                job.state, job.percent = State.DONE, 100
            except Stopped:
                job.state = State.STOPPED
            except (ChronoFtpError, OSError) as error:
                job.state, job.message = State.FAILED, str(error)
            except Exception as error:  # one job may never end the queue of all tabs
                job.state, job.message = State.FAILED, str(error) or "The transfer did not finish."
            self.changed(job)

    def _transfer(self, job: Job) -> None:
        if rsync_possible(job) and not self._target_is_a_link(job) and self._with_rsync(job):
            return
        job.way = job.connection.protocol.upper()
        connection = job.remote.for_a_transfer()
        try:
            self._file_by_file(job, connection)
        finally:
            if connection is not job.remote:
                connection.close()

    def _target_is_a_link(self, job: Job) -> bool:
        """rsync would replace a link by a file: the file by file way keeps it."""
        if not job.upload:
            return Path(job.local, job.name).is_symlink()
        return any(entry.name == job.name and entry.is_link for entry in job.remote.list_folder(job.remote_path))

    def _with_rsync(self, job: Job) -> bool:
        """True when rsync did the job; False when it could not log in, so the other way takes over."""
        job.way = "rsync"
        arguments = rsync_arguments(job, self.limit)
        arguments[0] = self.program
        with tempfile.TemporaryFile() as errors_file:  # a file, so a talkative rsync can never block on a full pipe
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE, stderr=errors_file)
            output = process.stdout
            assert output is not None
            descriptor = output.fileno()
            while True:
                if job.stop_asked.is_set():
                    process.terminate()
                    process.wait()
                    raise Stopped
                if not select.select([descriptor], [], [], POLL_SECONDS)[0]:
                    continue
                block = os.read(descriptor, READ_SIZE)
                if not block:
                    break
                found = PROGRESS_PATTERN.findall(block)
                if found:
                    job.percent = int(found[-1])
                    self.changed(job)
            status = process.wait()
            errors_file.seek(0)
            errors = errors_file.read().decode("utf-8", "replace")
        if status in LOGIN_FAILED_STATUSES:
            return False
        if status != 0:
            raise RemoteActionFailed(errors.strip().splitlines()[-1] if errors.strip() else "The transfer did not finish.")
        return True

    def _progress_of(self, job: Job, total: int) -> Callable[[int], None]:
        done = 0

        def step(amount: int) -> None:
            nonlocal done
            if job.stop_asked.is_set():
                raise Stopped
            done += amount
            percent = 100 * done // total if total else 0
            if percent != job.percent:
                job.percent = percent
                self.changed(job)

        return step

    def _file_by_file(self, job: Job, connection: RemoteConnection) -> None:
        if job.upload:
            source = Path(job.local)
            step = self._progress_of(job, _local_size(source))
            self._upload(connection, source, joined(job.remote_path, source.name), step)
        else:
            files: list[tuple[str, str]] = []
            folders: list[str] = []
            total = self._walk(job, connection, job.remote_path, os.path.join(job.local, job.name), files, folders)
            step = self._progress_of(job, total)
            for folder in folders:
                Path(folder).mkdir(parents=True, exist_ok=True)
            for remote, local in files:
                self._fetch(connection, remote, local, step)

    def _fetch(self, connection: RemoteConnection, remote: str, local: str, step: Callable[[int], None]) -> None:
        target = os.path.realpath(local)  # a link stays a link: what it points at gets the new content
        temporary = target + PART_SUFFIX
        try:
            connection.download(remote, temporary, step, self.limit)
            if Path(target).exists():
                shutil.copymode(target, temporary)  # the new file keeps the old one's permissions
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _send(self, connection: RemoteConnection, source: Path, target: str, step: Callable[[int], None], old: RemoteEntry | None) -> None:
        if old is not None and old.is_folder and not old.is_link:
            raise RemoteActionFailed(FOLDER_IN_THE_WAY_TEXT)
        if old is not None and old.is_link:
            real = connection.resolved(target)
            if real is None or real == target:
                connection.upload(str(source), target, step, self.limit)  # FTP can't tell where a link leads: written through it
                return
            parent, name = real.rsplit("/", 1)
            found = next((entry for entry in connection.list_folder(parent or "/") if entry.name == name), None)
            self._send(connection, source, real, step, found)  # the file it points at is replaced safely; the link stays a link
            return
        temporary = target + PART_SUFFIX
        try:
            connection.upload(str(source), temporary, step, self.limit)
            if old is None:
                connection.rename(temporary, target)
            else:
                connection.swap_in(temporary, target, old)
        except (Stopped, ChronoFtpError):
            try:
                connection.delete(temporary, False)
            except ChronoFtpError:
                pass  # gone already, or the connection is: the part file stays behind under its own name, never under the real one
            raise

    def _upload(self, connection: RemoteConnection, source: Path, target: str, step: Callable[[int], None]) -> None:
        parent = target.rsplit("/", 1)[0] or "/"
        self._upload_into(connection, source, target, step, {entry.name: entry for entry in connection.list_folder(parent)})

    def _upload_into(self, connection: RemoteConnection, source: Path, target: str, step: Callable[[int], None], present: dict[str, RemoteEntry]) -> None:
        """present: what the folder this goes into already holds, read once per folder."""
        old = present.get(source.name)
        if source.is_file():
            self._send(connection, source, target, step, old)
            return
        if source.is_symlink() or not source.is_dir():
            return  # a link to a folder or a broken link: links are not followed into other trees
        inside: dict[str, RemoteEntry] = {}
        if old is None:
            connection.make_folder(target)
        else:
            inside = {entry.name: entry for entry in connection.list_folder(target)}
        for item in sorted(source.iterdir()):
            self._upload_into(connection, item, joined(target, item.name), step, inside)

    def _walk(self, job: Job, connection: RemoteConnection, remote: str, local: str, files: list[tuple[str, str]], folders: list[str]) -> int:
        """A file or a whole folder; the size of what there is to fetch. Links to folders are not followed: a loop would never end."""
        if job.stop_asked.is_set():
            raise Stopped
        parent, name = remote.rsplit("/", 1) if "/" in remote else ("", remote)
        entry = next((item for item in connection.list_folder(parent or "/") if item.name == name), None)
        if entry is not None and entry.is_folder and entry.is_link:
            return 0
        if entry is not None and not entry.is_folder:
            files.append((remote, local))
            return entry.size
        return self._walk_folder(job, connection, remote, local, files, folders)

    def _walk_folder(self, job: Job, connection: RemoteConnection, remote: str, local: str, files: list[tuple[str, str]], folders: list[str]) -> int:
        if job.stop_asked.is_set():
            raise Stopped
        folders.append(local)
        total = 0
        for item in connection.list_folder(remote):
            if item.is_folder and item.is_link:
                continue
            if item.is_folder:
                total += self._walk_folder(job, connection, joined(remote, item.name), os.path.join(local, item.name), files, folders)
            else:
                files.append((joined(remote, item.name), os.path.join(local, item.name)))
                total += item.size
        return total
