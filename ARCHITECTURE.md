# ARCHITECTURE.md

The design as it stands now (version 0.0.37; sections 3 to 5 date from 0.0.5, with section 5 brought up to date in 0.0.37). The rules behind it, with their reasons, are in `DECISIONS.md` (the D-numbers below). 

## 1. Goals that shape the design

1. **The person always knows where they are.** Left is local, right is remote, a colour means a live connection (D-006, D-007).
2. **Work on a server happens on the server** (D-012, D-015).
3. **The window never freezes,** whatever a slow connection does (D-019).
4. **Server work is testable without a screen** (D-022).

## 2. Package layout

All code lives in the package `chrono_ftp` (src layout: `src/chrono_ftp/`).

| Module | Responsibility |
|---|---|
| `core` | Pure data model: Connection, remote file entry, capabilities, error types, colours (`core/colours.py`), the thin door to the credential store, the transfer options (`core/transferoptions.py`). No other I/O. |
| `config` | The connection list (`~/.config/chrono-ftp/connections.json`), the one-time import of a session list, the settings, the notes of part files, aside copies and appends (`config/parts.py`), and the bookmarks, location profiles and typed paths (`config/bookmarks.py`, `~/.config/chrono-ftp/bookmarks.json`). |
| `connections` | The common connection layer (`base.py`), its two implementations (`ssh.py`, `ftp.py`), capability detection, and the servers the person trusted (`trust.py`). |
| `transfers` | The transfer queue (`queue.py`), which job may start (`schedule.py`), and its two ways: `rsync` (`rsyncway.py`) and file by file over SFTP or FTP (`filebyfile.py`), with a large file in parts (`split.py`); a job and its progress (`job.py`); the overwrite question's answers and what a connection can do (`options.py`). |
| `engine` | Between the window and the rest: a thread per connection (`jobs.py`), the commands that run on a server (`commands.py`), the terminal (`terminal.py`), actions on this computer (`localfiles.py`), comparing two folders (`compare.py`), the matching step of synchronized browsing (`browsing.py`). |
| `ui` | PySide6 user interface. Pure window actions live here; server work does not. |

There is no separate command-line program (D-022).

## 3. Layers

A module may import only from lower layers. Modules in one layer are independent siblings and do not import each other. Enforced by import-linter (`.venv/bin/lint-imports`, contract in `pyproject.toml`).

| Layer | Modules |
|---|---|
| L0 | `core` |
| L1 | `config`, `connections` |
| L2 | `transfers` |
| L3 | `engine` |
| L4 | `ui` |

## 4. The connection layer

The panes, tabs and colours do not know which connection type is in use. SSH and FTP are equals, and each server uses whichever it supports (D-009).

1. **SSH** (D-010): browse over SFTP and run shell commands over the same login, with `asyncssh`. Without a shell the actions that need one are greyed out (D-027).
2. **FTP** (D-011): `ftplib`, with TLS where the server offers it, `MLSD` for listings.

Each connection reports what it can do after connecting (D-016): the system, the tools and their options, what the account may do. What is unavailable is greyed out; on an FTP connection the actions that need a shell are not shown (D-027).

## 5. Actions on a server

1. Recursive owner, group and permissions: one `chown -R` or `chmod -R` on the server, after the exact command is shown and confirmed (D-012, D-013).
2. Rename, move, copy, delete and search: `mv`, `cp -a`, `find` and the like on the server (D-015).
3. Without a shell: rename, move, delete and rights through the protocol, which is slower; a copy of a file through the protocol where the server offers it (SFTP copy-data, FTP SITE CPFR and CPTO, D-061); search, the recursive changes, the terminal, the password and the `sha256sum` step of compare are not available (D-027). Comparing by size and date works on every connection (D-020).
4. Every path in a shell command goes through `shlex.quote`.

## 6. The window

1. Left: the local side, neutral: a `QFileSystemModel` tree and the shared file list, read with `os` (`ui/localside.py`, `ui/filelist.py`, D-033). Right: the remote side (`ui/remoteside.py`), filled by answers from the connection's own thread (D-019). Each side has its own buttons (`ui/sidebar.py`, D-034). The look is set in `ui/theme.py`: Fusion with the colours of `core/colours.py`, so nothing comes from the platform (OR-9).
2. Both the folder tree and the file list on the right take the connection's colour; text is black or white by the background's brightness, selected rows too (D-007).
3. Every tab is one connection and keeps its own local and remote folder: a click on another tab changes both sides (D-008).
4. Tabs have the connection's colour, inactive tabs slightly dimmed, a dropped connection grey, through a small custom tab bar above both sides (D-007, D-008).
5. The layout follows the reference program; the buttons use Breeze icons from `ui/resources/icons/`, loaded by `ui/icons.py`: the text colour of the icon follows the theme, its other colours stay, and a disabled icon is one grey shape (D-028).
6. A local folder that can't be opened is shown as closed: an empty list and "no access" on the path line. The side watches its folder and every folder above it, so a removal or rename moves it up to the nearest folder that is left.
7. The main window is built from parts, one module per group of actions (D-041):
   1. `ui/mainwindow.py`: `MainWindow` builds the window and its toolbar, greys the buttons that can't act, and closes;
   2. `ui/windowbase.py`: what the parts share, the shared numbers and texts, and the methods one part calls in another;
   3. `ui/session.py`: one tab, with the files of it open for editing;
   4. `ui/windowlayout.py`: right-click menus, filter, settings and remembered sizes;
   5. `ui/windowtabs.py`: tabs, login and connecting;
   6. `ui/windowfolders.py`: remote folders, refresh, reconnect and the check for dropped connections;
   7. `ui/windowedit.py`: Edit and the saves that go back to the server;
   8. `ui/windowqueue.py`: showing, hiding and pinning the queue, its buttons and right-click menu, and what happens when it is done;
   9. `ui/windowlocal.py`: actions on this computer;
   10. `ui/windowserver.py`: actions on the server;
   11. `ui/windowtransfers.py`: upload, download, drag and drop, the speed limit and stop;
   12. `ui/windownavigation.py`: navigation (block FH): Ctrl+L, Ctrl+O, Ctrl+B and Ctrl+. on the focused side, the bookmark menus and keys, location profiles, synchronized browsing and the last folders written back;
   13. `ui/windowproperties.py`: Properties and server actions (block FM): Properties on both sides, new folders with rights, new files, links, Duplicate and Copy To, my key and my password on the server, the file manager.
8. Answers of the connection threads reach the window through `ui/bridge.py`; once the window closes, the bridge is closed and a late answer goes nowhere (D-042).

## 7. Transfers (block FB, version 0.0.12)

The modules of the transfer options, one line each:

1. `core/transferoptions.py`: the options of one transfer (resume, ask, only newer, dates, rights, count first, move) and the rights rules (D-043).
2. `config/parts.py`: the notes on this computer of part files, aside copies and appends; each note is input and checked when read (D-044).
3. `transfers/job.py`: one job, its state and its progress meter, which leaves out the time a question waits.
4. `transfers/options.py`: the overwrite question's answers and what each means, and which options a connection can carry out (D-043, D-045).
5. `transfers/filebyfile.py`: the file by file way over SFTP or FTP: part files, resume, the overwrite question, the checks right before a replace, moves, appends and their set-back (D-044, D-045).
6. `transfers/rsyncway.py`: the rsync way: the exact list to send, the check of agreed files right before it starts, progress, and the process group that a stop or the end ends whole.
7. `transfers/queue.py`: runs the jobs (several at once since 0.0.13, see section 8), picks the way for each, asks the questions through the window, and tidies leftovers.
8. `ui/transferdialog.py`: the transfer window before each upload, download and drop (D-043).
9. `ui/transferoptionsform.py`: the options as the person sets them, in Transfer settings and in Settings, greyed by what the connection can do.
10. `ui/overwritedialog.py`: the overwrite question (D-045).
11. `ui/remotefolderpicker.py`: Browse on the server, from the transfer window.
12. `ui/plaintext.py`: names and server texts shown as plain text, fitted to the screen.

**How the two ways fit together.** The queue takes rsync where it can work: an SFTP connection with a key login, rsync on both sides, a target folder that can be read, no link in the target (rsync would replace it by a file), and no upload that sets rights. Where the job asks anything of a file (ask before replacing, only newer, a move, an answer for all, a file and a folder meeting, a link or a pipe in the source, a pipe in the target), the file by file way first looks the job over without sending: it asks the questions and records the exact list of files rsync is to send, and does itself what rsync can't (a rename, an append, a continued part). Right before rsync starts, every file agreed to be replaced is looked at again. Where rsync can't log in at all, the file by file way does the whole job, with each answer holding only for the file as the person saw it, and nobody is asked twice. Without rsync, the file by file way does the job on its own.

## 8. The queue (block FC, version 0.0.13)

The modules of the queue, one line each:

1. `transfers/queue.py`: `TransferQueue`: the list of jobs and every control the person has over them (pause, resume, move, run now, retry, stop, delete, the limits, clear finished), the overwrite question one at a time, and the logins lent to a file in parts (D-046, D-048).
2. `transfers/schedule.py`: which waiting job may start: the places a job reads and writes and whether two jobs meet there, the server a job logs in to, and each job's share of the queue's speed limit (D-048).
3. `transfers/split.py`: one large file over several logins: whether a file is split, the ranges confirmed, and the parts that move the rest (D-049).
4. `transfers/job.py`: a job also knows why it still waits and its own speed.
5. `ui/queueview.py`: the queue list with its columns (progress, time left, speed, own speed limit), its buttons, "Start items by themselves", "Transfers at once" and "When done".
6. `ui/afterqueue.py`: what happens when the queue is done: the choices, the countdown window, and suspend and shut down through logind (D-047).

**Threads.** The queue has one thread of its own and one thread per running job. Its own thread only decides: it starts every waiting job that may run (in the order of the list, as many as "Transfers at once" allows, never one that meets a running job, or one earlier in the list that still waits, on the same place, and never one more login than a server takes), removes what a paused job kept once a pause ends without a resume, lets done jobs leave the list, and tells the window when the queue is done. Each job's thread runs that one job and leaves the running jobs only after it told the window how the job ended, so "done" never comes before the last job's end. Every change of the list and of a job's state happens under the queue's lock; the calls to the window go through a gate that closes when `close()` is called, and `close()` waits for every thread within a bounded time, closing a connection a thread hangs on. A job split in parts runs its further parts on threads of their own, each over a login the queue lends it while a place of "Transfers at once" and the server allow.

**Connections.** Over FTP each running job logs in on its own (`for_a_transfer`); over SSH the running jobs share the tab's login, and only the further parts of a split file log in on their own (`another_login`). A login a server refuses while other jobs of it run makes the job wait; the queue remembers how many logins that server took, and tries one more after a job of it ended well.

## 9. Navigation (block FH, version 0.0.34)

The modules of navigation, one line each:

1. `ui/pathbar.py`: the clickable path above each side, with its field to type a path (D-055).
2. `ui/openfolder.py`: the Open folder window of Ctrl+O (D-056).
3. `ui/bookmarksdialog.py`: the window that organizes bookmarks and location profiles (D-056).
4. `config/bookmarks.py`: bookmarks, location profiles and typed paths in `bookmarks.json`; each value is input and checked when read (D-056).
5. `engine/browsing.py`: the folder on the other side that matches a step of one side (D-057).
6. `ui/windownavigation.py`: the part of the main window that ties them together; a step on a server goes through the tab's own thread like every other request (D-019, D-057).
7. `core/connection.py`, `config/connections.py`, `ui/connectionlist.py`: the local starting folder and "Remember the last folders" of a connection (D-058).

## 10. Properties and server actions (block FM, version 0.0.37)

The modules of block FM, one line each:

1. `engine/rights.py`: the twelve boxes of the rights editor, each on, off or "leave as it is"; octal both ways, one file's new rights, and the `chmod` that sets exactly the boxes that are set, with `X` for folders that get read (D-060).
2. `engine/properties.py`: one view of several files, where a field that differs says so; the names of users and groups from the server's own lists, or from the names seen in listings; a typed name checked (D-060).
3. `engine/commands.py`: also the names of copies from a pattern, a copy on the server through the shell or the protocol, links and another target for a link, and why an action can't be done, for its hover text (D-061).
4. `engine/localfiles.py`: also Properties on this computer (owner, group, rights, everything inside without following links), new files, links and the file manager (D-060, D-063).
5. `engine/keyinstall.py`: a public key read and checked, and added to `~/.ssh/authorized_keys` over SFTP alone, with the rights the SSH server accepts (D-062).
6. `connections/passwordchange.py`: the talk with the server's `passwd` and what of its answer is shown, never a password; `SshConnection.change_password` runs it in a terminal of its own (D-062).
7. `connections/base.py`, `ssh.py`, `ftp.py`: what a connection can do without a shell (`server_copy`, `symlinks`, `hardlinks`), links, a copy on the server and the password, each raising its calm reason where it can't (D-061, D-062).
8. `engine/terminal.py`: the terminal opens in the folder on the screen, and stays open in the home folder when that folder can't be entered (D-063).
9. `ui/rightseditor.py`, `ui/propertiesdialog.py`, `ui/actiondialogs.py`: the rights editor, the Properties window, and the windows of a link, Copy To, a new folder with rights and a new password.
10. `ui/windowproperties.py`: the part of the main window that ties them together; every server call goes through the tab's own thread, and long work on this computer through a thread of its own, so the window never freezes (D-019).
