# ARCHITECTURE.md

The design as it stands now (version 0.0.47; sections 3 to 5 date from 0.0.5, with section 5 brought up to date in 0.0.37). The rules behind it, with their reasons, are in `DECISIONS.md` (the D-numbers below). 

## 1. Goals that shape the design

1. **The person always knows where they are.** Left is local, right is remote, a colour means a live connection (D-006, D-007).
2. **Work on a server happens on the server** (D-012, D-015).
3. **The window never freezes,** whatever a slow connection does (D-019).
4. **Server work is testable without a screen** (D-022).

## 2. Package layout

All code lives in the package `chrono_ftp` (src layout: `src/chrono_ftp/`).

| Module | Responsibility |
|---|---|
| `core` | Pure data model: Connection, remote file entry, capabilities, error types, colours (`core/colours.py`), the thin door to the credential store, the transfer options (`core/transferoptions.py`), addresses (`core/addresses.py`) and the SSH settings of this computer, which are only ever read (`core/sshsettings.py`). No other I/O. |
| `config` | The connection list (`~/.config/chrono-ftp/connections.json`), the one-time import of a session list, the settings, the notes of part files, aside copies and appends (`config/parts.py`), the bookmarks, location profiles and typed paths (`config/bookmarks.py`, `~/.config/chrono-ftp/bookmarks.json`), and the workspaces (`config/workspaces.py`, `~/.config/chrono-ftp/workspaces.json`). |
| `connections` | The common connection layer (`base.py`), its two implementations (`ssh.py`, `ftp.py`), capability detection, and the servers the person trusted (`trust.py`). |
| `transfers` | The transfer queue (`queue.py`), which job may start (`schedule.py`), and its two ways: `rsync` (`rsyncway.py`) and file by file over SFTP or FTP (`filebyfile.py`), with a large file in parts (`split.py`); a job and its progress (`job.py`); the overwrite question's answers and what a connection can do (`options.py`). |
| `engine` | Between the window and the rest: a thread per connection (`jobs.py`), the commands that run on a server (`commands.py`), the terminal (`terminal.py`), actions on this computer (`localfiles.py`), comparing two folders (`compare.py`), the matching step of synchronized browsing (`browsing.py`), checksums and folder sizes on both sides (`checksums.py`, `sizes.py`). |
| `ui` | PySide6 user interface. Pure window actions live here; server work does not. |

There is no separate command-line program (D-022).

Outside the package, `packaging/` builds the installers: Nuitka compiles the program with its own Python and Qt inside
an Ubuntu 22.04 container, nFPM packs that folder into a `.deb` and an `.rpm`, and `packaging/chrono_ftp_main.py` is the
one file Nuitka compiles, which only calls `chrono_ftp.ui.main()`. It holds no program logic (D-104,
`packaging/PACKAGING.md`).

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
3. Without a shell: rename, move, delete and rights through the protocol, which is slower; a copy of a file through the protocol where the server offers it (SFTP copy-data, FTP SITE CPFR and CPTO, D-061); search, the recursive changes, the terminal and the password are not available; the checksum step of compare uses SFTP's check-file or FTP's HASH where the server offers them (D-027, D-088). Comparing by size and date works on every connection (D-020).
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
   14. `ui/windowprogram.py`: the program-wide part (block FP).
   15. `ui/windowlooks.py`: the looks (block FQ): file colours, the thumbnail view of each side and the toolbar menu.
   16. `ui/windowselection.py`: selection and keys (block FG): the selection keys and menus, the two-pane mode, the key set, This File Only, the clipboard, the title bar, the right-button drag, pausing the refresh and clearing the listings.
   17. `ui/windowsizes.py`: hashes and sizes (block FE): the folder sizes of Shift+Space and what the Size and Checksum tabs of Properties ask.
   18. `ui/windowworkspaces.py`: workspaces (block FI): the menu beside the tabs, and saving, opening, renaming and deleting a workspace.
   19. `ui/windowpaste.py`: copy and paste files between tabs (block FI): Ctrl+C and Ctrl+V, and the copies that go from one server to another through this computer.
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

## 11. Program-wide (block FP, version 0.0.38)

The modules of block FP, one line each:

1. `ui/settingsdialog.py`: Settings with pages on the left: General, Panels, Transfers, Queue, Confirmations, Log; `config/settings.py` keeps the new choices, each checked when read (D-064).
2. `core/logrules.py`: the levels, size and kept logs of the session log, shared by the settings and the log (D-065).
3. `connections/sessionlog.py`: the log of one connection in the state folder, cleaned of every password and key before it is written or shown, rotated by size and count; `ssh.py` and `ftp.py` write their requests and answers to it (D-065).
4. `connections/base.py`, `ssh.py`, `ftp.py`: `server_info` and `free_space` of a connection (D-066).
5. `engine/serverinfo.py`: the report of the Server Information window, read on the tab's own thread (D-066).
6. `engine/cleanup.py`: what Chrono FTP keeps, each kind with its size, and the removal of exactly the kinds ticked (D-067).
7. `engine/updatecheck.py`: the newest version tag on GitHub against the running version; `core/appinfo.py` also gives the repository and the licence text (D-068).
8. `ui/logwindow.py`, `ui/serverinfodialog.py`, `ui/cleanupdialog.py`, `ui/aboutdialog.py`: the Session Log, Server Information, Clean Up and About windows; the update check runs on a thread of its own (D-019).
9. `ui/windowprogram.py`: the part of the main window that ties them together, with the double-click on a file, the automatic refresh and the columns per tab (D-064).

## 12. Looks (block FQ, version 0.0.39)

The modules of block FQ, one line each:

1. `core/masks.py`: whether a name fits a mask such as `*.log; *.tmp` (D-069); since block FD the one mask engine (section 14).
2. `core/filecolours.py`: a rule of file colours and the first rule that fits a name; `config/settings.py` keeps the rules, the thumbnail limit and mask, and the toolbar choices, each checked when read (D-069 to D-071).
3. `engine/thumbnails.py`: which files in view get a thumbnail, the key a picture is kept under, and a file of this computer read into memory, stopped when told (D-070).
4. `connections/base.py`, `ssh.py`, `ftp.py`: `read_file`, a small file read into memory block by block, stopped when told; on FTP the data connection is ended and its answer read (D-070).
5. `engine/jobs.py`: a worker tells whether it is idle and whether a call waits behind the one that runs, so a thumbnail gives way at once (D-070).
6. `ui/filelist.py`: the rows in a rule's colours, the selection over them, and the thumbnail view with the rows in view (D-069, D-070).
7. `ui/thumbnails.py`: the thumbnails of one side, fetched one at a time through a source the window gives, kept in memory (D-070).
8. `ui/toolbars.py`: every toolbar, its icon size and buttons, and the right-click menu (D-071).
9. `ui/colourrules.py`: the Colours page of Settings (D-069).
10. `ui/windowlooks.py`: the part of the main window that ties them together, per tab for the thumbnails (D-070).

## 13. Selection and keys (block FG, version 0.0.40)

The modules of block FG, one line each:

1. `engine/listing.py`: the logic of a file list without a screen: type-ahead matching, the extension of a name, size formats, natural order, the selection totals, clipboard lines, the folders above a path and the folders of the history drop-down (D-072 to D-078).
2. `config/settings.py`: the two-pane selection mode, the key set, the size format, natural sort, the path in the title bar and moving by a right-button drag, each checked when read (D-073 to D-079).
3. `ui/filelist.py`: type-ahead, the selection actions, the two-pane mode and what an action acts on (`acting_entries`), sort keys, the column header menu, the hidden and filtered counts, the right-button drag and the right-click menu that waits for the button (D-072 to D-076, D-079).
4. `ui/sidebar.py`: the history drop-down of Back and Forward, several steps at once, and the status line of a side with its counts (D-076, D-077).
5. `ui/pathbar.py`: the drop-down of the folders above the path (D-077).
6. `ui/windowselection.py`: the part of the main window that ties them together (D-073 to D-080).

## 14. Masks (block FD, version 0.0.41)

The modules of block FD, one line each:

1. `core/masks.py`: the one mask engine: include and exclude, folder patterns, patterns with a path, size and date conditions; `fits` for transfers and the filter, `named` for selecting, colours and thumbnails, `MaskError` with the place of a fault (D-081).
2. `core/transferoptions.py`: the file mask, skip hidden files, skip empty folders, the transfer mode with its text mask and BOM, and the names; `plain` for Edit's saves and Compare's rows (D-082, D-083, D-087).
3. `core/presets.py`: a preset, its rules and the first preset that fits a transfer (D-084); `config/settings.py` keeps the presets and the history of masks.
4. `transfers/conversion.py`: what passes a transfer, which files are text, the line endings block by block into a copy of our own, and the names in the target (D-082, D-083, D-087).
5. `transfers/filebyfile.py`: leaves out what does not pass, sends a text through its converted copy and checks its size against that, writes the converted names; beside rsync it sends what rsync can't and makes their folders first; `transfers/queue.py` looks a job over for rsync wherever it leaves out or changes anything.
6. `ui/maskfield.py`: the mask field with its history and its fault; `ui/maskdialog.py`: Select by Mask, Unselect by Mask and the Filter window (D-085, D-086).
7. `ui/transferoptionsform.py`: the Files and Text and names tabs of the transfer settings; `ui/presetseditor.py`: the Presets page of Settings; `ui/transferdialog.py`: the preset of a transfer (D-084).
8. `ui/pathbar.py`: the mark of the active filter next to the path; `ui/filelist.py`: the filter and selecting by mask through the engine; `ui/windowselection.py` and `ui/windowlayout.py`: Num+, Num−, the filter window and the history of masks.

## 15. Hashes and sizes (block FE, version 0.0.42)

The modules of block FE, one line each:

1. `core/hashes.py`: the checksum algorithms and the length of each checksum, shared by the connections, the settings and the program logic.
2. `connections/base.py`: what a protocol hashes without a shell (`Capabilities.checksums`), the checksum of one file through the protocol (`checksum`), an answer checked as input (`checksum_from`), and a command that ends when told to stop (`run_stoppable`).
3. `connections/ssh.py`: check-file of SFTP, where the hello names it, on an SFTP channel of its own over the same login (`SftpChannel`) (the extension names of the hello are kept on asyncssh's handler); the `*sum` tools, `awk` and a find that prints sizes detected with the other tools; `run_stoppable` ends its command on the server (D-088).
4. `connections/ftp.py`: HASH with the algorithms of FEAT, chosen with OPTS HASH, and the older XSHA256 and the like, on a login of its own; a stop shuts the control connection down (D-088).
5. `engine/checksums.py`: the one checksum function, the choice of the way, one shell command for many files, files on this computer read in pieces, and the files inside folders (D-088).
6. `engine/sizes.py`: the size, files and folders of a selection: one find with awk on the server for all folders where the shell can, else the listings; on this computer by walking (D-089).
7. `engine/compare.py`: `compare_contents`, the checksum step of compare (D-020, D-088); `engine/serverinfo.py` names the checksums a server computes.
8. `ui/propertiesdialog.py`: the Size and Checksum tabs and the `Calculator` they ask (D-089); `ui/comparedialog.py`: Compare Contents (D-088).
9. `ui/filelist.py`: the folder sizes in the Size column, "at least" where something could not be read, and Shift+Space never typed nor marking (D-090).
10. `ui/windowsizes.py`: the part of the main window that ties them together: a worker per side for the folder sizes, each tied to its tab, and a thread of its own for each calculation of Properties and Compare (`run_alone`), on the server over the tab's own connection, so the person's own requests never wait behind them (D-019, D-090); `ui/windowserver.py` runs Compare Contents through it.

## 16. Synchronize (block FF, version 0.0.43)

The modules of block FF, one line each:

1. `core/synchronizing.py`: the choices of the Synchronize window as pure data: the direction, the mode, the criterion, the case choice, the deletes, the scope and the masks; the key of a folder pair and the file count of its last scan (D-091 to D-093).
2. `engine/synchronize.py`: the whole plan without a screen: the scan of both sides through a reader (so a server, a fake server and a second folder on this computer are all the same to it), the date precision the listing reports, what counts as a difference per criterion, the rows with their actions and ticks, the moves, a row reversed, and the count and size per action (D-038, D-091 to D-094, D-096).
3. `transfers/sidework.py`: a queue item that is no transfer: the deletes, the moves on one side, the dates to set and the copies between two folders on this computer; one step that is refused never stops the rest (D-095).
4. `transfers/queue.py`, `transfers/job.py`: a job may carry side work instead of a transfer; the queue runs it like any other item, with its progress counting the steps done (D-095).
5. `config/settings.py`: every choice of the window and the file count per folder pair, each checked when read (D-091 to D-093, OR-15).
6. `ui/synchronizeoptions.py`: the choices as the person sets them, with what this connection can't do greyed and its reason as hover text (OR-7, OR-20).
7. `ui/comparedialog.py`: the Synchronize window: the choices on top, the checklist below with the size and date of both sides, sorting by any column, Select by action, Reverse, the counts per action, the progress bar of a scan, and Compare Contents of block FE (D-038, D-088).
8. `ui/filelist.py`: `select_names`, which marks exactly what Mark Differences found (FF-12).
9. `ui/windowsynchronize.py`: the part of the main window that ties them together: the scan on the tab's own thread, the counting of a one-sided folder and the checksum step on threads of their own, Apply through the queue, the question before a delete, Mark Differences, and the same window for two folders on this computer (D-019, D-094 to D-096).


## 17. Tabs and workspaces (block FI, version 0.0.45)

The modules of block FI, one line each:

1. `core/connection.py`, `config/connections.py`: a connection carries the name its tab was given, kept with it (D-098).
2. `config/settings.py`: the order the tabs were dragged into, by connection name, so a tab opens again in its place (D-098).
3. `config/workspaces.py`: the workspaces in `workspaces.json`: a name, its tabs with the folders of both sides, and the one that opens at start; every value is input and checked when read (D-100, OR-15).
4. `core/colours.py`, `ui/tabbar.py`: a tab dragged to another place, and files dropped on a tab, with the border that marks the tab that would take them (D-098, OR-9).
5. `ui/session.py`: what Ctrl+C copied, and a copy on its way from one server to another through this computer (D-099).
6. `ui/windowtabs.py`: the tab part: the order kept, Rename Tab, Duplicate Tab, Reconnect, Disconnect and Close Tab, the tab keys, the list of all tabs, the folder a tab names, and what a drop on a tab does (D-098).
7. `ui/windowworkspaces.py`: the workspaces part, and the menu an empty part of the tab row opens (D-100).
8. `ui/windowpaste.py`: Ctrl+C and Ctrl+V on the list with the focus, the server's own copy within one server, the way through this computer between two servers, the copy of this computer's files into a folder here, and a pasted path (D-099, OR-21).
9. `engine/copyplan.py`: what a paste will do, on this computer and on a server alike, made before anything is copied: the free name of a copy next to its own file, what is already there, a file onto a folder or a folder onto a file, and what else can't be done with its reason (D-099, G-2); the copying itself is `transfers/sidework.py`'s here and `engine/commands.py`'s there.
10. `ui/startabort.py`: the one Start/Abort button of [[D-097]] and the words a run on this computer says, shared by the Synchronize window and the paste; `ui/copydialog.py`: the window of a paste, used for both sides.
11. `transfers/options.py`, `ui/overwritedialog.py`: the same overwrite question for a copy that stays on this computer, which says so and greys Append and Resume with that reason (FI-03).


## 18. The connection list (block FJ, version 0.0.47)

The modules of block FJ, one line each:

1. `core/connection.py`: the note of a connection, the folder its name names (`folder_of`, `own_name`, `in_folder`, `moved_into`, `folders_in`) and the name as it is saved (`tidy_name`), so a folder of the list is pure name work (D-101).
2. `core/addresses.py`: an address read from what a person pasted and an address made from a connection, both pure text work, with the check that only a plain server name or a server number in brackets is a server, and never a password in an address (D-103, OR-19).
3. `core/sshsettings.py`: the SSH settings of this computer, `~/.ssh/config` and `~/.ssh/known_hosts`, read and never written: the servers found, what of each can't come over yet and why, and the tokens of a setting worked out (D-102, FJ-10).
4. `config/connections.py`: the note, kept with the connection; the file is unchanged otherwise, because a folder is part of the name (D-101).
5. `config/settings.py`: the folders of the list that stand open, and the connection a new one starts with (D-101).
6. `config/workspaces.py`: `connection_renamed`, so a connection saved under a new name, or moved into a folder, keeps its place in every workspace (D-101).
7. `ui/connectiontree.py`: the list as a tree: the folders, the dragging of a connection into one or out of one (which the window turns into the one rename), the folders that stand open, and the typing that jumps to a connection (D-101).
8. `ui/connectionlist.py`: the connections window: the tree, the note, the settings a new connection starts with, the one place a connection is written (`_keep`, which carries the password first), and the two ways in of Import (D-101, D-102).
9. `ui/sshimportdialog.py`: the window of the servers found, each with its tick, and every server that can't come over whole saying why, unticked (D-102, OR-20).
10. `ui/addressdialog.py`: the window where an address is pasted, with what the program has read from it under the field while the person types, and the one that shows an address just put on the clipboard (D-103).
11. `ui/session.py`: `may_keep`, the one question every writer asks before anything is written under a tab's name; a tab opened from an address answers no (D-103, OR-19).
12. `ui/windowtabs.py`: a tab per connection of a folder, the tab of a pasted address, and the rename carried into the settings and the workspaces (D-101, D-103).
13. `ui/windowselection.py`: Copy the Address of the file or folder under the cursor on the server (D-103).
